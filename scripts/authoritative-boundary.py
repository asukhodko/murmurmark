#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any


SCRIPT_VERSION = "0.1.1"
PROFILE = "authoritative_boundary_v1"
REPORT_DIR_NAME = "authoritative-boundary-v1"
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_./+-]+")
PROTECTED_MARKERS = {
    "блокер",
    "вопрос",
    "давай",
    "давайте",
    "договорились",
    "надо",
    "нужно",
    "проблема",
    "решили",
    "риск",
    "согласовали",
}
STOP_WORDS = {
    "а",
    "в",
    "во",
    "вот",
    "да",
    "для",
    "же",
    "и",
    "или",
    "как",
    "мы",
    "на",
    "не",
    "но",
    "ну",
    "по",
    "с",
    "то",
    "у",
    "что",
    "это",
    "я",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze, apply and evaluate evidence-backed authoritative transcript boundary closure."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--sessions-root", type=Path, default=Path("sessions"))
        command.add_argument("--out-dir", type=Path)

    freeze = subparsers.add_parser("freeze", help="Freeze the current operational corpus and review queue.")
    common(freeze)
    freeze.add_argument("--force", action="store_true")

    apply = subparsers.add_parser("apply", help="Apply boundary dispositions to one frozen session.")
    apply.add_argument("session", type=Path)
    common(apply)
    apply.add_argument("--mode", choices=("conservative", "dry-run"), default="conservative")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate all generated session profiles against the freeze.")
    common(evaluate)
    evaluate.add_argument("--apply", action="store_true", help="Apply every queued session before evaluation.")
    evaluate.add_argument("--synthesize", action="store_true", help="Build profile-specific notes before promotion.")

    run = subparsers.add_parser("run", help="Freeze if needed, apply the corpus, synthesize and evaluate.")
    common(run)
    run.add_argument("--force-freeze", action="store_true")
    run.add_argument("--skip-synthesis", action="store_true")
    return parser.parse_args()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def report_dir(sessions_root: Path, explicit: Path | None) -> Path:
    return explicit.expanduser().resolve() if explicit else sessions_root / "_reports" / REPORT_DIR_NAME


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def tokens(text: Any) -> list[str]:
    return [token.lower().replace("ё", "е") for token in TOKEN_RE.findall(str(text or ""))]


def content_tokens(text: Any) -> list[str]:
    return [token for token in tokens(text) if token not in STOP_WORDS and len(token) > 2]


def has_protected_marker(text: Any) -> bool:
    return bool(set(tokens(text)) & PROTECTED_MARKERS)


def role_name(row: dict[str, Any]) -> str:
    source = str(row.get("source_track") or "").lower()
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    if source == "mic" or role == "me":
        return "Me"
    if source == "remote" or role in {"remote", "colleagues"}:
        return "Colleagues"
    return str(row.get("speaker_label") or row.get("role") or "Unknown")


def interval(row: dict[str, Any]) -> tuple[float, float]:
    nested = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = safe_float(nested.get("start", row.get("start", row.get("start_sec"))))
    end = safe_float(nested.get("end", row.get("end", row.get("end_sec"))))
    return start, end


def interval_overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_start, left_end = interval(left)
    right_start, right_end = interval(right)
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


def item_duration(row: dict[str, Any]) -> float:
    start, end = interval(row)
    nested = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    return max(0.0, safe_float(nested.get("duration_sec")) or end - start)


def queue_source(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "").strip()
    if source:
        return source
    lane = str(row.get("review_lane") or "")
    if lane in {"classify_audio", "check_unique_me_content", "fast_confirm_drop"}:
        return "audio_review"
    if lane in {"check_local_recall", "recover_missing_me"}:
        return "local_recall"
    if lane == "check_transcript_order":
        return "transcript_order"
    source_id = str(row.get("source_audit_id") or "")
    if source_id.startswith("arp_"):
        return "audio_review"
    if source_id.startswith("order_"):
        return "transcript_order"
    if source_id.startswith("local_"):
        return "local_recall"
    return "unknown"


def queue_id(row: dict[str, Any]) -> str:
    identity = {
        "session_id": row.get("session_id"),
        "source": queue_source(row),
        "source_audit_id": row.get("source_audit_id"),
        "utterance_ids": sorted(str(value) for value in row.get("utterance_ids") or []),
        "interval": row.get("interval"),
        "label": row.get("label"),
        "review_lane": row.get("review_lane"),
    }
    return f"boundary_{sha256_bytes(canonical_bytes(identity))[:16]}"


def profile_paths(session: Path, profile: str) -> dict[str, Path]:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    synthesis = session / "derived/synthesis-simple/extractive"
    profile_suffix = suffix(profile)
    return {
        "dialogue": resolved / f"clean_dialogue{profile_suffix}.json",
        "quality": resolved / f"quality_report{profile_suffix}.json",
        "overlaps": resolved / f"overlaps{profile_suffix}.json",
        "transcript": resolved / f"transcript{profile_suffix}.md",
        "transcript_json": resolved / f"transcript.simple{profile_suffix}.json",
        "notes": synthesis / f"notes{profile_suffix}.md",
        "evidence_notes": synthesis / f"evidence_notes{profile_suffix}.json",
        "quality_verdict": synthesis / f"quality_verdict{profile_suffix}.json",
    }


def utterance_fingerprint(dialogue: dict[str, Any]) -> str:
    rows = []
    for row in dialogue.get("utterances") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "id": row.get("id"),
                "role": role_name(row),
                "start": row.get("start"),
                "end": row.get("end"),
                "text": row.get("text"),
            }
        )
    return sha256_bytes(canonical_bytes(rows))


def token_fingerprint(dialogue: dict[str, Any]) -> dict[str, Any]:
    inventory: list[str] = []
    for row in dialogue.get("utterances") or []:
        if isinstance(row, dict):
            inventory.extend(content_tokens(row.get("text")))
    return {"count": len(inventory), "sha256": sha256_bytes(canonical_bytes(inventory))}


def artifact_fingerprints(session: Path, profile: str) -> dict[str, Any]:
    paths = profile_paths(session, profile)
    result: dict[str, Any] = {}
    for name, path in paths.items():
        result[name] = {
            "path": str(path),
            "exists": path.exists(),
            "sha256": sha256_file(path),
        }
    return result


def load_operational_module() -> ModuleType:
    path = Path(__file__).with_name("report-operational-readiness.py")
    spec = importlib.util.spec_from_file_location("murmurmark_operational_readiness", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load operational readiness module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_full_review_queue(session_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    operational = load_operational_module()
    pseudo_sessions = [
        {
            "session_id": row.get("session_id"),
            "session": row.get("session"),
            "label": row.get("session_id"),
            "selected_profile": row.get("selected_profile"),
            # Force collection of the complete finite pool. Existing profile and
            # review decisions still filter already-resolved evidence inside the builder.
            "use_gate": "review_first",
            "export_blockers": ["frozen_boundary_scope"],
            "transcript_review_burden_sec": 1.0,
        }
        for row in session_rows
        if row.get("selected_profile") not in {None, "", "missing"}
    ]
    mandatory, low_materiality = operational.build_review_queue_details(pseudo_sessions, 100_000)
    actions = operational.review_action_summary(mandatory)
    return mandatory, {
        "action_count": safe_int(actions.get("actions")),
        "grouped_rows": safe_int(actions.get("grouped_rows")),
        "low_materiality_items": len(low_materiality),
        "low_materiality_seconds": round(sum(item_duration(row) for row in low_materiality), 3),
    }


def freeze_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest_path = out_dir / "baseline_manifest.json"
    queue_path = out_dir / "boundary_review_queue.jsonl"
    existing_manifest = read_json(manifest_path)
    if existing_manifest and not args.force:
        print(f"baseline_manifest: {manifest_path}")
        print("status: already_frozen")
        return 0

    quality_report_path = sessions_root / "_reports/session-quality/session_quality_report.json"
    readiness_path = sessions_root / "_reports/operational-readiness/operational_readiness_report.json"
    hard_failures: list[str] = []
    if existing_manifest and args.force:
        # A forced queue expansion must not silently move the baseline after profile
        # promotion. Preserve the original session/profile/artifact freeze.
        sessions = [row for row in existing_manifest.get("sessions") or [] if isinstance(row, dict)]
        source_reports = existing_manifest.get("source_reports") or {}
    else:
        quality_report = read_json(quality_report_path)
        readiness = read_json(readiness_path)
        if not quality_report or not readiness:
            print("status: missing_corpus_reports", file=sys.stderr)
            return 2
        quality_by_id = {
            str(row.get("session_id")): row
            for row in quality_report.get("sessions") or []
            if isinstance(row, dict) and row.get("session_id")
        }
        scope_rows = [row for row in readiness.get("session_review_burden") or [] if isinstance(row, dict)]
        sessions = []
        for scope_row in sorted(scope_rows, key=lambda row: str(row.get("session_id") or "")):
            session_id = str(scope_row.get("session_id") or "")
            session = sessions_root / session_id
            quality_row = quality_by_id.get(session_id, {})
            profile = str(scope_row.get("selected_profile") or quality_row.get("selected_profile") or "missing")
            paths = profile_paths(session, profile) if profile != "missing" else {}
            dialogue = read_json(paths["dialogue"]) if paths else None
            if profile != "missing" and not dialogue:
                hard_failures.append(f"{session_id}:missing_frozen_dialogue:{profile}")
            sessions.append(
                {
                    "session_id": session_id,
                    "session": str(session),
                    "selected_profile": profile,
                    "artifacts": artifact_fingerprints(session, profile) if profile != "missing" else {},
                    "utterance_fingerprint": utterance_fingerprint(dialogue) if dialogue else None,
                    "token_inventory": token_fingerprint(dialogue) if dialogue else None,
                    "metrics": {
                        "transcript_order_review_seconds": safe_float(scope_row.get("transcript_order_review_seconds")),
                        "local_recall_review_seconds": safe_float(scope_row.get("local_recall_meaningful_review_seconds")),
                        "remote_duplicate_in_me_seconds": safe_float(quality_row.get("remote_duplicate_in_me_seconds")),
                        "transcript_review_burden_sec": safe_float(scope_row.get("transcript_review_burden_sec")),
                        "notes_review_burden_sec": safe_float(scope_row.get("notes_review_burden_sec")),
                        "local_only_island_recall": quality_row.get("local_only_island_recall"),
                    },
                }
            )
        source_reports = {
            "session_quality": {"path": str(quality_report_path), "sha256": sha256_file(quality_report_path)},
            "operational_readiness": {"path": str(readiness_path), "sha256": sha256_file(readiness_path)},
        }

    queue_rows, queue_meta = build_full_review_queue(sessions)
    frozen_queue: list[dict[str, Any]] = []
    for row in queue_rows:
        frozen = copy.deepcopy(row)
        frozen["source"] = queue_source(frozen)
        frozen["boundary_queue_id"] = queue_id(row)
        frozen["schema"] = "murmurmark.authoritative_boundary_queue_item/v1"
        frozen_queue.append(frozen)
    frozen_queue.sort(key=lambda row: (str(row.get("session_id") or ""), str(row.get("boundary_queue_id") or "")))

    manifest = {
        "schema": "murmurmark.authoritative_boundary_baseline/v1",
        "generator": {"name": "authoritative-boundary", "version": SCRIPT_VERSION},
        "source_reports": source_reports,
        "scope": {
            "session_count": len(sessions),
            "session_ids": [row["session_id"] for row in sessions],
        },
        "queue": {
            "item_count": len(frozen_queue),
            "action_count": queue_meta["action_count"] or len(frozen_queue),
            "grouped_rows": queue_meta["grouped_rows"],
            "seconds": round(sum(item_duration(row) for row in frozen_queue), 3),
            "sha256": sha256_bytes(canonical_bytes(frozen_queue)),
            "by_lane": dict(sorted(Counter(str(row.get("review_lane") or "unknown") for row in frozen_queue).items())),
            "low_materiality_excluded_items": queue_meta["low_materiality_items"],
            "low_materiality_excluded_seconds": queue_meta["low_materiality_seconds"],
        },
        "sessions": sessions,
        "gates": {"passed": not hard_failures, "hard_failures": hard_failures},
    }
    write_json(manifest_path, manifest)
    write_jsonl(queue_path, frozen_queue)
    print(f"baseline_manifest: {manifest_path}")
    print(f"boundary_review_queue: {queue_path}")
    print(f"sessions: {len(sessions)}")
    print(f"queue_items: {len(frozen_queue)}")
    return 0 if not hard_failures else 2


def load_order_module() -> ModuleType:
    path = Path(__file__).with_name("apply-transcript-order-repair.py")
    spec = importlib.util.spec_from_file_location("murmurmark_order_repair", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load order repair module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row_utterance_ids(row: dict[str, Any]) -> set[str]:
    values = row.get("utterance_ids") if isinstance(row.get("utterance_ids"), list) else []
    ids = {str(value) for value in values if value is not None and str(value)}
    utterances = row.get("utterances")
    if isinstance(utterances, list):
        ids.update(str(value.get("id")) for value in utterances if isinstance(value, dict) and value.get("id"))
    elif isinstance(utterances, dict):
        ids.update(str(value.get("id")) for value in utterances.values() if isinstance(value, dict) and value.get("id"))
    return ids


def evidence_match(queue_row: dict[str, Any], evidence_row: dict[str, Any]) -> bool:
    queue_ids = row_utterance_ids(queue_row)
    evidence_ids = row_utterance_ids(evidence_row)
    if queue_ids and evidence_ids and queue_ids & evidence_ids:
        return True
    overlap = interval_overlap(queue_row, evidence_row)
    shortest = min(max(item_duration(queue_row), 0.001), max(item_duration(evidence_row), 0.001))
    return overlap / shortest >= 0.60


def evidence_ref(path: Path, row: dict[str, Any], label: str, confidence: float) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "id": row.get("id") or row.get("item_id"),
        "label": label,
        "confidence": round(confidence, 6),
    }


def classification(row: dict[str, Any]) -> tuple[str, float]:
    value = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    return str(value.get("label") or value.get("verdict") or ""), safe_float(value.get("confidence"))


def gather_evidence(session: Path, queue_row: dict[str, Any]) -> dict[str, Any]:
    paths = {
        "audio_review": session / "derived/audit/audio-review-pack/audio_review_audit.jsonl",
        "stronger_judge": session / "derived/audit/audio-review-pack/faster_whisper_judge.jsonl",
        "group_overlap": session / "derived/audit/group-overlaps/group_overlap_audit.jsonl",
        "local_recall": session / "derived/audit/local-recall/local_recall_items.jsonl",
        "order": session / "derived/audit/order/transcript_order_items.jsonl",
    }
    result: dict[str, Any] = {"refs": [], "missing_sources": []}
    for name, path in paths.items():
        if not path.exists():
            result["missing_sources"].append(str(path))
            result[name] = []
            continue
        matches = [row for row in read_jsonl(path) if evidence_match(queue_row, row)]
        result[name] = matches
        for row in matches:
            label, confidence = classification(row)
            if not label:
                label = str(row.get("label") or "")
                confidence = safe_float(row.get("confidence"))
            result["refs"].append(evidence_ref(path, row, label, confidence))
    return result


def safe_keep(evidence: dict[str, Any]) -> tuple[bool, str]:
    stronger = [(*classification(row), row) for row in evidence.get("stronger_judge") or []]
    audio = [(*classification(row), row) for row in evidence.get("audio_review") or []]
    group = [(*classification(row), row) for row in evidence.get("group_overlap") or []]
    conflict_labels = {"confirm_remote_duplicate", "confirm_asr_noise", "remote_duplicate", "asr_noise", "probable_duplicate", "probable_remote_leak", "probable_asr_noise"}
    if any(label in conflict_labels and confidence >= 0.80 for label, confidence, _row in stronger + audio + group):
        return False, "conflicting_duplicate_or_noise_evidence"
    if any(label == "confirm_timing_or_doubletalk" and confidence >= 0.88 for label, confidence, _row in stronger):
        return True, "stronger_judge_confirmed_timing_or_doubletalk"
    benign_group = any(
        label in {"probable_timing_overlap", "probable_double_talk"} and confidence >= 0.70
        for label, confidence, _row in group
    )
    reliable_audio = any(label == "likely_reliable" and confidence >= 0.70 for label, confidence, _row in audio)
    confirm_me = any(label == "confirm_me" and confidence >= 0.90 for label, confidence, _row in stronger)
    if benign_group and (reliable_audio or confirm_me):
        return True, "independent_audio_and_overlap_evidence_confirm_benign_overlap"
    return False, "insufficient_independent_evidence"


def strict_drop(queue_row: dict[str, Any], evidence: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> tuple[bool, str]:
    if queue_source(queue_row) != "audio_review":
        return False, "not_audio_review_duplicate_or_noise"
    me_ids = [str(value) for value in queue_row.get("me_utterance_ids") or []]
    if len(me_ids) != 1 or me_ids[0] not in by_id:
        return False, "whole_me_utterance_not_identified"
    me = by_id[me_ids[0]]
    if has_protected_marker(me.get("text")):
        return False, "protected_action_decision_or_risk_marker"
    audio_matches = [(*classification(row), row) for row in evidence.get("audio_review") or []]
    judge_matches = [(*classification(row), row) for row in evidence.get("stronger_judge") or []]
    audio_ok = any(
        label in {"remote_duplicate", "asr_noise"}
        and confidence >= 0.90
        and str((row.get("classification") or {}).get("verdict") or "") == "probable_transcript_error"
        and safe_float((row.get("scores") or {}).get("local_support")) <= 20.0
        for label, confidence, row in audio_matches
    )
    judge_ok = any(label in {"confirm_remote_duplicate", "confirm_asr_noise"} and confidence >= 0.95 for label, confidence, _row in judge_matches)
    if not (audio_ok and judge_ok):
        return False, "duplicate_or_noise_not_confirmed_by_two_independent_judges"
    remote_text = " ".join(
        str(item.get("text") or "")
        for item in queue_row.get("text") or []
        if isinstance(item, dict) and str(item.get("source_track") or item.get("role") or "").lower() in {"remote", "colleagues"}
    )
    unique = set(content_tokens(me.get("text"))) - set(content_tokens(remote_text))
    if unique:
        return False, "unique_me_content_present"
    return True, "two_judges_confirm_whole_utterance_duplicate_or_noise"


def annotate(row: dict[str, Any], queue_row: dict[str, Any], disposition: str, reason: str) -> dict[str, Any]:
    result = copy.deepcopy(row)
    quality = copy.deepcopy(result.get("quality")) if isinstance(result.get("quality"), dict) else {}
    boundary = quality.get("authoritative_boundary") if isinstance(quality.get("authoritative_boundary"), dict) else {}
    decisions = boundary.get("decisions") if isinstance(boundary.get("decisions"), list) else []
    decisions.append(
        {
            "queue_id": queue_row.get("boundary_queue_id"),
            "source_audit_id": queue_row.get("source_audit_id"),
            "disposition": disposition,
            "reason": reason,
        }
    )
    boundary.update({"profile": PROFILE, "decisions": decisions})
    quality["authoritative_boundary"] = boundary
    if disposition == "needs_review":
        quality["needs_review"] = True
    result["quality"] = quality
    return result


def output_fingerprint(paths: dict[str, Path]) -> str:
    values = {name: sha256_file(path) for name, path in paths.items() if name in {"dialogue", "quality", "overlaps", "transcript", "transcript_json"}}
    return sha256_bytes(canonical_bytes(values))


def session_boundary_dir(session: Path) -> Path:
    return session / "derived/transcript-simple/whisper-cpp/authoritative-boundary-v1"


def write_failure_report(session: Path, input_profile: str, hard_failures: list[str], queue_rows: list[dict[str, Any]]) -> None:
    boundary_dir = session_boundary_dir(session)
    report = {
        "schema": "murmurmark.authoritative_boundary_report/v1",
        "generator": {"name": "authoritative-boundary", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": input_profile,
        "output_profile": PROFILE,
        "status": "failed_open",
        "summary": {"queue_items": len(queue_rows), "closed_items": 0, "remaining_items": len(queue_rows)},
        "gates": {"passed": False, "hard_failures": hard_failures, "warnings": ["all_items_remain_needs_review"]},
    }
    write_json(boundary_dir / "boundary_repair_report.json", report)


def apply_session(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest = read_json(out_dir / "baseline_manifest.json")
    frozen_queue = read_jsonl(out_dir / "boundary_review_queue.jsonl")
    if not manifest or not frozen_queue:
        print("status: missing_frozen_baseline", file=sys.stderr)
        return 2
    session = args.session.expanduser().resolve()
    session_record = next((row for row in manifest.get("sessions") or [] if isinstance(row, dict) and str(row.get("session_id")) == session.name), None)
    queue_rows = [row for row in frozen_queue if str(row.get("session_id")) == session.name]
    if not session_record:
        print(f"status: session_not_in_frozen_scope: {session.name}", file=sys.stderr)
        return 2
    if not queue_rows:
        print(f"status: no_boundary_queue_items: {session.name}")
        return 0

    input_profile = str(session_record.get("selected_profile") or "missing")
    input_paths = profile_paths(session, input_profile)
    frozen_dialogue_sha = (((session_record.get("artifacts") or {}).get("dialogue") or {}).get("sha256"))
    current_dialogue_sha = sha256_file(input_paths["dialogue"])
    hard_failures: list[str] = []
    if not current_dialogue_sha:
        hard_failures.append("missing_input_dialogue")
    elif current_dialogue_sha != frozen_dialogue_sha:
        hard_failures.append("frozen_input_dialogue_hash_mismatch")
    dialogue = read_json(input_paths["dialogue"])
    quality = read_json(input_paths["quality"])
    if not dialogue or not quality:
        hard_failures.append("missing_or_invalid_input_profile")
    if hard_failures:
        write_failure_report(session, input_profile, hard_failures, queue_rows)
        print(f"boundary_repair_report: {session_boundary_dir(session) / 'boundary_repair_report.json'}")
        print("status: failed_open")
        return 2

    order_module = load_order_module()
    utterances = [row for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    by_id = {str(row.get("id")): row for row in utterances if row.get("id")}
    input_overlaps_payload = read_json(input_paths["overlaps"]) or {}
    input_overlaps = [row for row in input_overlaps_payload.get("overlaps") or [] if isinstance(row, dict)]
    raw_segments = order_module.load_raw_segments(input_paths["dialogue"].parent, input_profile)
    raw_by_id = {str(row.get("id")): row for row in raw_segments if row.get("id")}
    candidates = order_module.load_candidates(input_paths["dialogue"].parent, input_profile)
    order_items = {
        str(row.get("item_id")): row
        for row in read_jsonl(session / "derived/audit/order/transcript_order_items.jsonl")
        if row.get("item_id")
    }
    me_usage = Counter(
        str(value)
        for row in queue_rows
        if queue_source(row) == "transcript_order"
        for value in row.get("me_utterance_ids") or []
    )

    replacements: dict[str, list[dict[str, Any]]] = {}
    dropped_ids: set[str] = set()
    annotations: dict[str, list[tuple[dict[str, Any], str, str]]] = {}
    dispositions: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for queue_row in queue_rows:
        queue_row = copy.deepcopy(queue_row)
        source = queue_source(queue_row)
        queue_row["source"] = source
        evidence = gather_evidence(session, queue_row)
        disposition = "needs_review"
        reason = "insufficient_existing_evidence"
        patch_meta: dict[str, Any] = {}
        target_ids = [str(value) for value in queue_row.get("utterance_ids") or []]

        if source == "transcript_order":
            item = order_items.get(str(queue_row.get("source_audit_id") or ""))
            me_ids = [str(value) for value in queue_row.get("me_utterance_ids") or []]
            remote_ids = [str(value) for value in queue_row.get("remote_utterance_ids") or []]
            if item and len(me_ids) == 1 and len(remote_ids) == 1 and me_usage[me_ids[0]] == 1 and me_ids[0] in by_id and remote_ids[0] in by_id:
                repair_args = argparse.Namespace(
                    remote_guard_sec=0.2,
                    output_profile=PROFILE,
                )
                status, rows, meta = order_module.attempt_repair(
                    me=by_id[me_ids[0]],
                    remote=by_id[remote_ids[0]],
                    item=item,
                    candidates=candidates,
                    raw_by_id=raw_by_id,
                    raw_segments=raw_segments,
                    args=repair_args,
                )
                dropped_text = str(meta.get("dropped_overlap_text") or "")
                remote_text = str(by_id[remote_ids[0]].get("text") or "")
                unique_dropped = sorted(set(content_tokens(dropped_text)) - set(content_tokens(remote_text)))
                replacement_text = " ".join(str(row.get("text") or "") for row in rows)
                unique_original_removed = sorted(
                    set(content_tokens(by_id[me_ids[0]].get("text")))
                    - set(content_tokens(replacement_text))
                    - set(content_tokens(remote_text))
                )
                if status == "applied" and not unique_dropped and not unique_original_removed:
                    disposition = "split_at_proven_boundary"
                    reason = "source_asr_segments_prove_pre_and_post_local_islands"
                    patch_meta = {
                        **meta,
                        "unique_dropped_content_tokens": [],
                        "unique_original_content_tokens_removed": [],
                    }
                    replacements[me_ids[0]] = [
                        annotate(row, queue_row, disposition, reason)
                        for row in rows
                    ]
                else:
                    keep, keep_reason = safe_keep(evidence)
                    if keep:
                        disposition = "keep"
                        reason = keep_reason
                    else:
                        reason = str(meta.get("reason") or keep_reason)
                        patch_meta = {
                            **meta,
                            "unique_dropped_content_tokens": unique_dropped,
                            "unique_original_content_tokens_removed": unique_original_removed,
                        }
            else:
                keep, keep_reason = safe_keep(evidence)
                if keep:
                    disposition = "keep"
                    reason = keep_reason
                elif not item:
                    reason = "missing_exact_order_audit_item"
                elif any(me_usage[value] > 1 for value in me_ids):
                    reason = "multiple_queue_items_share_me_utterance"
        elif source == "audio_review":
            drop, drop_reason = strict_drop(queue_row, evidence, by_id)
            if drop:
                disposition = "drop_duplicate_or_noise"
                reason = drop_reason
                dropped_ids.update(str(value) for value in queue_row.get("me_utterance_ids") or [])
            else:
                keep, keep_reason = safe_keep(evidence)
                if keep:
                    disposition = "keep"
                    reason = keep_reason
                else:
                    reason = drop_reason if drop_reason != "duplicate_or_noise_not_confirmed_by_two_independent_judges" else keep_reason
        elif source in {"local_recall", "local_recall_repair"}:
            reason = "existing_evidence_does_not_prove_safe_text_insertion"
        else:
            reason = "unsupported_queue_source_fails_open"

        closed = disposition != "needs_review"
        disposition_row = {
            "schema": "murmurmark.authoritative_boundary_disposition/v1",
            "queue_id": queue_row.get("boundary_queue_id"),
            "session_id": session.name,
            "source": queue_row.get("source"),
            "source_audit_id": queue_row.get("source_audit_id"),
            "disposition": disposition,
            "closed": closed,
            "reason": reason,
            "interval": queue_row.get("interval"),
            "utterance_ids": target_ids,
            "evidence": evidence.get("refs") or [],
            "missing_evidence_sources": evidence.get("missing_sources") or [],
            "meta": patch_meta,
        }
        dispositions.append(disposition_row)
        if closed:
            applied.append(disposition_row)
        else:
            rejected.append(disposition_row)
        for target_id in target_ids:
            if target_id in by_id and target_id not in replacements and target_id not in dropped_ids:
                annotations.setdefault(target_id, []).append((queue_row, disposition, reason))

    output_utterances: list[dict[str, Any]] = []
    target_ids = {str(value) for row in queue_rows for value in row.get("utterance_ids") or []}
    for row in utterances:
        row_id = str(row.get("id") or "")
        if row_id in dropped_ids:
            continue
        if row_id in replacements:
            output_utterances.extend(replacements[row_id])
            continue
        output = copy.deepcopy(row)
        for queue_row, disposition, reason in annotations.get(row_id, []):
            output = annotate(output, queue_row, disposition, reason)
        output_utterances.append(output)
    # Python's stable sort preserves the original order for equal timestamps. Using
    # end/id as additional keys would create unrelated changes outside the repair scope.
    output_utterances.sort(key=lambda row: safe_float(row.get("start")))
    output_overlaps = order_module.build_overlaps(output_utterances)

    input_remote = [
        (row.get("id"), row.get("start"), row.get("end"), row.get("text"))
        for row in utterances
        if role_name(row) == "Colleagues"
    ]
    output_remote = [
        (row.get("id"), row.get("start"), row.get("end"), row.get("text"))
        for row in output_utterances
        if role_name(row) == "Colleagues"
    ]
    generated_target_ids = {
        str(row.get("id"))
        for rows in replacements.values()
        for row in rows
        if row.get("id")
    }
    target_scope_ids = target_ids | generated_target_ids
    input_outside = [
        (row.get("id"), row.get("start"), row.get("end"), row.get("text"), role_name(row))
        for row in utterances
        if str(row.get("id") or "") not in target_scope_ids
    ]
    output_outside = [
        (row.get("id"), row.get("start"), row.get("end"), row.get("text"), role_name(row))
        for row in output_utterances
        if str(row.get("id") or "") not in target_scope_ids
    ]
    input_duplicate_seconds = round(
        sum(
            safe_float(row.get("duration_sec"))
            for row in order_module.build_overlaps(utterances)
            if safe_float(row.get("text_similarity")) >= 0.65
        ),
        3,
    )
    duplicate_overlaps = [row for row in output_overlaps if safe_float(row.get("text_similarity")) >= 0.65]
    output_duplicate_seconds = round(sum(safe_float(row.get("duration_sec")) for row in duplicate_overlaps), 3)
    summary = {
        "schema": "murmurmark.authoritative_boundary_summary/v1",
        "queue_items": len(dispositions),
        "closed_items": sum(1 for row in dispositions if row["closed"]),
        "remaining_items": sum(1 for row in dispositions if not row["closed"]),
        "closed_seconds": round(sum(item_duration(row) for row, disposition in zip(queue_rows, dispositions) if disposition["closed"]), 3),
        "remaining_seconds": round(sum(item_duration(row) for row, disposition in zip(queue_rows, dispositions) if not disposition["closed"]), 3),
        "by_disposition": dict(sorted(Counter(str(row["disposition"]) for row in dispositions).items())),
        "remaining_order_items": sum(1 for row in dispositions if not row["closed"] and row["source"] == "transcript_order"),
        "remaining_order_seconds": round(sum(item_duration(source) for source, row in zip(queue_rows, dispositions) if not row["closed"] and row["source"] == "transcript_order"), 3),
        "remaining_local_recall_items": sum(1 for row in dispositions if not row["closed"] and row["source"] in {"local_recall", "local_recall_repair"}),
        "remaining_local_recall_seconds": round(sum(item_duration(source) for source, row in zip(queue_rows, dispositions) if not row["closed"] and row["source"] in {"local_recall", "local_recall_repair"}), 3),
        "remaining_duplicate_or_noise_items": sum(1 for row in dispositions if not row["closed"] and row["source"] == "audio_review"),
        "remaining_duplicate_or_noise_seconds": round(sum(item_duration(source) for source, row in zip(queue_rows, dispositions) if not row["closed"] and row["source"] == "audio_review"), 3),
        "remote_duplicate_in_me_seconds_before": round(input_duplicate_seconds, 3),
        "remote_duplicate_in_me_seconds_after": output_duplicate_seconds,
        "local_content_token_recall": 1.0
        if not any(
            row.get("meta", {}).get("unique_original_content_tokens_removed")
            for row in dispositions
            if row.get("disposition") in {"split_at_proven_boundary", "drop_duplicate_or_noise"}
            if isinstance(row.get("meta"), dict)
        )
        else 0.0,
    }
    output_quality = order_module.quality_report(quality, output_utterances, output_overlaps, summary)
    output_quality["authoritative_boundary"] = summary
    source_cleanup_report = (
        session
        / "derived/transcript-simple/whisper-cpp/audit-cleanup"
        / f"audit_cleanup_report.{input_profile}.json"
    )
    source_cleanup = read_json(source_cleanup_report)
    source_gates = source_cleanup.get("gates") if isinstance(source_cleanup, dict) else None
    local_recall_explanation = (
        source_gates.get("local_recall_explanation")
        if isinstance(source_gates, dict)
        else None
    )
    if local_recall_explanation:
        output_quality["local_recall_low_score_explained"] = True
        output_quality["local_recall_explanation"] = local_recall_explanation
        output_quality["local_recall_explanation_source"] = str(source_cleanup_report)
    output_dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": dialogue.get("session", session.name),
        "utterances": output_utterances,
    }
    simple_payload = {
        "schema": "murmurmark.transcript_simple/v1",
        "session": dialogue.get("session", session.name),
        "backend": "whisper.cpp",
        "utterances": [
            {**copy.deepcopy(row), "raw_text": row.get("text"), "corrected_text": row.get("text"), "corrections": []}
            for row in output_utterances
        ],
    }
    hard_failures = []
    if len(dispositions) != len(queue_rows):
        hard_failures.append("not_all_queue_items_have_dispositions")
    if input_remote != output_remote:
        hard_failures.append("remote_utterances_changed")
    if input_outside != output_outside:
        hard_failures.append("utterances_outside_target_scope_changed")
    if output_duplicate_seconds > input_duplicate_seconds + 0.001:
        hard_failures.append("remote_duplicate_in_me_seconds_regressed")
    if summary["local_content_token_recall"] < 1.0:
        hard_failures.append("local_content_token_recall_regressed")
    if any(row["closed"] and row["reason"].startswith("insufficient") for row in dispositions):
        hard_failures.append("uncertainty_closed_automatically")
    warnings = ["residual_boundary_review_required"] if summary["remaining_items"] else []
    gates = {"passed": not hard_failures, "hard_failures": hard_failures, "warnings": warnings}

    boundary_dir = session_boundary_dir(session)
    output_paths = profile_paths(session, PROFILE)
    if args.mode == "conservative" and gates["passed"]:
        write_json(output_paths["dialogue"], output_dialogue)
        write_json(output_paths["quality"], output_quality)
        write_json(
            output_paths["overlaps"],
            {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": output_overlaps},
        )
        write_json(output_paths["transcript_json"], simple_payload)
        transcript_report = read_json(input_paths["dialogue"].parent / "transcribe_simple_report.json") or {}
        order_module.write_markdown(
            output_paths["transcript"],
            output_utterances,
            transcript_report.get("model"),
            transcript_report.get("language"),
        )
    report = {
        "schema": "murmurmark.authoritative_boundary_report/v1",
        "generator": {"name": "authoritative-boundary", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": input_profile,
        "output_profile": PROFILE,
        "status": "ok" if gates["passed"] else "failed_open",
        "mode": args.mode,
        "inputs": {
            "clean_dialogue": str(input_paths["dialogue"]),
            "frozen_clean_dialogue_sha256": frozen_dialogue_sha,
            "actual_clean_dialogue_sha256": current_dialogue_sha,
            "queue_sha256": manifest.get("queue", {}).get("sha256"),
        },
        "summary": summary,
        "gates": gates,
    }
    write_json(boundary_dir / "boundary_repair_report.json", report)
    write_jsonl(boundary_dir / "boundary_repair_applied.jsonl", applied)
    write_jsonl(boundary_dir / "boundary_repair_rejected.jsonl", rejected)
    write_jsonl(boundary_dir / "boundary_review_queue.jsonl", [row for row in dispositions if not row["closed"]])
    write_json(
        boundary_dir / "boundary_repair_diff.json",
        {
            "schema": "murmurmark.authoritative_boundary_diff/v1",
            "session_id": session.name,
            "input_profile": input_profile,
            "output_profile": PROFILE,
            "input_utterance_fingerprint": utterance_fingerprint(dialogue),
            "output_utterance_fingerprint": utterance_fingerprint(output_dialogue),
            "outside_target_unchanged": input_outside == output_outside,
            "remote_unchanged": input_remote == output_remote,
            "dispositions": dispositions,
        },
    )
    if args.mode == "conservative" and gates["passed"]:
        report["output_fingerprint"] = output_fingerprint(output_paths)
        write_json(boundary_dir / "boundary_repair_report.json", report)
    print(f"boundary_repair_report: {boundary_dir / 'boundary_repair_report.json'}")
    print(f"output_profile: {PROFILE}")
    print(f"closed_items: {summary['closed_items']}")
    print(f"remaining_items: {summary['remaining_items']}")
    return 0 if gates["passed"] else 2


def evidence_ids_exist(session: Path) -> tuple[bool, list[str]]:
    paths = profile_paths(session, PROFILE)
    dialogue = read_json(paths["dialogue"]) or {}
    evidence = read_json(paths["evidence_notes"])
    if not evidence:
        return False, ["missing_profile_evidence_notes"]
    known_ids = {str(row.get("id")) for row in dialogue.get("utterances") or [] if isinstance(row, dict) and row.get("id")}
    missing: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            if key in {"utterance_ids", "evidence_utterance_ids"}:
                for item in value:
                    if isinstance(item, str) and item.startswith("utt_") and item not in known_ids:
                        missing.add(item)
            else:
                for item in value:
                    visit(item, key)
        elif key in {"utterance_id", "evidence_utterance_id"} and isinstance(value, str):
            if value.startswith("utt_") and value not in known_ids:
                missing.add(value)

    visit(evidence)
    return not missing, sorted(missing)


def selected_note_count(payload: dict[str, Any] | None) -> int:
    selected = payload.get("selected") if isinstance(payload, dict) and isinstance(payload.get("selected"), dict) else {}
    return sum(len(value) for value in selected.values() if isinstance(value, list))


def evaluate_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest = read_json(out_dir / "baseline_manifest.json")
    queue_rows = read_jsonl(out_dir / "boundary_review_queue.jsonl")
    if not manifest or not queue_rows:
        print("status: missing_frozen_baseline", file=sys.stderr)
        return 2
    queued_ids = sorted({str(row.get("session_id")) for row in queue_rows})
    if getattr(args, "apply", False):
        for session_id in queued_ids:
            child = argparse.Namespace(
                session=sessions_root / session_id,
                sessions_root=sessions_root,
                out_dir=out_dir,
                mode="conservative",
            )
            apply_session(child)
    if getattr(args, "synthesize", False):
        synthesis_script = Path(__file__).with_name("synthesize-simple-extractive.py")
        for session_id in queued_ids:
            report = read_json(session_boundary_dir(sessions_root / session_id) / "boundary_repair_report.json") or {}
            if (report.get("gates") or {}).get("passed") is not True:
                continue
            subprocess.run(
                [sys.executable, str(synthesis_script), str(sessions_root / session_id), "--transcript-profile", PROFILE],
                check=True,
            )

    session_reports: list[dict[str, Any]] = []
    hard_failures: list[str] = []
    manifest_gates = manifest.get("gates") if isinstance(manifest.get("gates"), dict) else {}
    if manifest_gates.get("passed") is not True:
        hard_failures.append("frozen_baseline_gates_failed")
    closed = 0
    remaining = 0
    closed_seconds = 0.0
    remaining_seconds = 0.0
    promoted_sessions: list[str] = []
    baseline_by_id = {
        str(row.get("session_id")): row
        for row in manifest.get("sessions") or []
        if isinstance(row, dict) and row.get("session_id")
    }
    verdict_rank = {"failed": 0, "risky": 1, "usable_with_review": 2, "good": 3}
    for session_id in queued_ids:
        session = sessions_root / session_id
        baseline = baseline_by_id.get(session_id, {})
        frozen_session_rows = [row for row in queue_rows if str(row.get("session_id")) == session_id]
        report = read_json(session_boundary_dir(session) / "boundary_repair_report.json")
        if not report:
            hard_failures.append(f"{session_id}:missing_boundary_report")
            continue
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
        notes_ok, missing_evidence_ids = (
            evidence_ids_exist(session)
            if profile_paths(session, PROFILE)["evidence_notes"].exists()
            else (False, ["missing_profile_evidence_notes"])
        )
        if gates.get("passed") is not True:
            hard_failures.append(f"{session_id}:session_gates_failed")
        if not notes_ok:
            hard_failures.append(f"{session_id}:notes_reference_missing_utterances")
        baseline_artifact_regressions: list[str] = []
        for name, artifact in (baseline.get("artifacts") or {}).items():
            if not isinstance(artifact, dict) or not artifact.get("sha256"):
                continue
            path = Path(str(artifact.get("path") or ""))
            if sha256_file(path) != artifact.get("sha256"):
                baseline_artifact_regressions.append(str(name))
        if baseline_artifact_regressions:
            hard_failures.append(f"{session_id}:frozen_baseline_artifacts_changed")
        baseline_profile = str(baseline.get("selected_profile") or "")
        baseline_verdict_payload = read_json(profile_paths(session, baseline_profile)["quality_verdict"])
        output_verdict_payload = read_json(profile_paths(session, PROFILE)["quality_verdict"])
        baseline_evidence_notes = read_json(profile_paths(session, baseline_profile)["evidence_notes"])
        output_evidence_notes = read_json(profile_paths(session, PROFILE)["evidence_notes"])
        baseline_verdict = str((baseline_verdict_payload or {}).get("verdict") or "")
        output_verdict = str((output_verdict_payload or {}).get("verdict") or "")
        if not baseline_verdict_payload:
            hard_failures.append(f"{session_id}:missing_frozen_quality_verdict")
        if not output_verdict_payload:
            hard_failures.append(f"{session_id}:missing_profile_quality_verdict")
        if baseline_verdict in verdict_rank and output_verdict in verdict_rank and verdict_rank[output_verdict] < verdict_rank[baseline_verdict]:
            hard_failures.append(f"{session_id}:quality_verdict_regressed:{baseline_verdict}->{output_verdict}")
        baseline_selected_notes = selected_note_count(baseline_evidence_notes)
        output_selected_notes = selected_note_count(output_evidence_notes)
        selected_notes_regressed = output_selected_notes < baseline_selected_notes
        if selected_notes_regressed:
            hard_failures.append(
                f"{session_id}:selected_notes_regressed:{baseline_selected_notes}->{output_selected_notes}"
            )
        output_fingerprint_matches = report.get("output_fingerprint") == output_fingerprint(profile_paths(session, PROFILE))
        if not output_fingerprint_matches:
            hard_failures.append(f"{session_id}:output_fingerprint_mismatch")
        output_quality = read_json(profile_paths(session, PROFILE)["quality"]) or {}
        baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
        baseline_local_recall = baseline_metrics.get("local_only_island_recall")
        output_local_recall = output_quality.get("local_only_island_recall")
        local_recall_regressed = (
            baseline_local_recall is not None
            and output_local_recall is not None
            and safe_float(output_local_recall) + 0.000001 < safe_float(baseline_local_recall)
        )
        if local_recall_regressed:
            hard_failures.append(f"{session_id}:local_only_island_recall_regressed")
        queue_before = {
            "items": len(frozen_session_rows),
            "seconds": round(sum(item_duration(row) for row in frozen_session_rows), 3),
            "order_items": sum(1 for row in frozen_session_rows if queue_source(row) == "transcript_order"),
            "local_recall_items": sum(
                1 for row in frozen_session_rows if queue_source(row) in {"local_recall", "local_recall_repair"}
            ),
            "duplicate_or_noise_items": sum(1 for row in frozen_session_rows if queue_source(row) == "audio_review"),
        }
        queue_after = {
            "items": safe_int(summary.get("remaining_items")),
            "seconds": round(safe_float(summary.get("remaining_seconds")), 3),
            "order_items": safe_int(summary.get("remaining_order_items")),
            "local_recall_items": safe_int(summary.get("remaining_local_recall_items")),
            "duplicate_or_noise_items": safe_int(summary.get("remaining_duplicate_or_noise_items")),
        }
        queue_regressions = [
            name
            for name in queue_before
            if safe_float(queue_after.get(name)) > safe_float(queue_before.get(name)) + 0.001
        ]
        if queue_regressions:
            hard_failures.append(f"{session_id}:review_queue_regressed:{','.join(queue_regressions)}")
        local_content_token_recall = safe_float(summary.get("local_content_token_recall"))
        if local_content_token_recall < 1.0:
            hard_failures.append(f"{session_id}:local_content_token_recall_regressed")
        closed += safe_int(summary.get("closed_items"))
        remaining += safe_int(summary.get("remaining_items"))
        closed_seconds += safe_float(summary.get("closed_seconds"))
        remaining_seconds += safe_float(summary.get("remaining_seconds"))
        if gates.get("passed") is True and notes_ok:
            promoted_sessions.append(session_id)
        session_reports.append(
            {
                "session_id": session_id,
                "input_profile": report.get("input_profile"),
                "output_profile": PROFILE,
                "gates_passed": gates.get("passed") is True,
                "summary": summary,
                "output_fingerprint": report.get("output_fingerprint"),
                "notes_evidence_ids_exist": notes_ok,
                "missing_notes_evidence_ids": missing_evidence_ids,
                "frozen_baseline_artifact_regressions": baseline_artifact_regressions,
                "baseline_verdict": baseline_verdict or None,
                "output_verdict": output_verdict or None,
                "selected_notes_before": baseline_selected_notes,
                "selected_notes_after": output_selected_notes,
                "selected_notes_regressed": selected_notes_regressed,
                "output_fingerprint_matches": output_fingerprint_matches,
                "local_only_island_recall_before": baseline_local_recall,
                "local_only_island_recall_after": output_local_recall,
                "local_only_island_recall_regressed": local_recall_regressed,
                "local_content_token_recall": local_content_token_recall,
                "review_queue_before": queue_before,
                "review_queue_after": queue_after,
                "review_queue_regressions": queue_regressions,
            }
        )

    baseline_items = safe_int((manifest.get("queue") or {}).get("item_count"))
    baseline_actions = safe_int((manifest.get("queue") or {}).get("action_count"))
    baseline_seconds = safe_float((manifest.get("queue") or {}).get("seconds"))
    closure_ratio = closed / max(1, baseline_items)
    if closed + remaining != baseline_items:
        hard_failures.append("not_all_frozen_queue_items_accounted_for")
    if closure_ratio < 0.20:
        hard_failures.append("safe_closure_ratio_below_20_percent")
    if remaining >= baseline_actions:
        hard_failures.append("mandatory_review_actions_did_not_decrease")
    if remaining_seconds >= baseline_seconds:
        hard_failures.append("mandatory_review_seconds_did_not_decrease")
    decision = "PROMOTE_AUTHORITATIVE_BOUNDARY_V1" if not hard_failures else "DO_NOT_PROMOTE"
    report = {
        "schema": "murmurmark.authoritative_boundary_corpus_report/v1",
        "generator": {"name": "authoritative-boundary", "version": SCRIPT_VERSION},
        "profile": PROFILE,
        "decision": decision,
        "baseline_manifest_sha256": sha256_file(out_dir / "baseline_manifest.json"),
        "queue_sha256": (manifest.get("queue") or {}).get("sha256"),
        "summary": {
            "session_count": len(session_reports),
            "frozen_queue_items": baseline_items,
            "closed_items": closed,
            "remaining_items": remaining,
            "safe_closure_ratio": round(closure_ratio, 6),
            "mandatory_review_actions_before": baseline_actions,
            "mandatory_review_actions_after": remaining,
            "mandatory_review_seconds_before": round(baseline_seconds, 3),
            "mandatory_review_seconds_after": round(remaining_seconds, 3),
            "closed_seconds": round(closed_seconds, 3),
        },
        "promoted_sessions": promoted_sessions if not hard_failures else [],
        "sessions": session_reports,
        "gates": {"passed": not hard_failures, "hard_failures": hard_failures, "warnings": []},
    }
    write_json(out_dir / "boundary_corpus_report.json", report)
    lines = [
        "# Authoritative Boundary v1",
        "",
        f"Decision: `{decision}`",
        "",
        f"- Frozen queue: `{baseline_items}` items / `{baseline_actions}` actions / `{baseline_seconds:.3f}` sec",
        f"- Safely closed: `{closed}` items / `{closed_seconds:.3f}` sec (`{closure_ratio:.1%}`)",
        f"- Remaining: `{remaining}` items / `{remaining_seconds:.3f}` sec",
        f"- Per-session gates passed: `{len(promoted_sessions)}/{len(session_reports)}`",
    ]
    if hard_failures:
        lines.extend(["", "## Blockers", "", *[f"- `{value}`" for value in hard_failures]])
    (out_dir / "boundary_corpus_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"boundary_corpus_report: {out_dir / 'boundary_corpus_report.json'}")
    print(f"decision: {decision}")
    print(f"closed_items: {closed}/{baseline_items}")
    print(f"remaining_items: {remaining}")
    return 0 if not hard_failures else 2


def main() -> int:
    args = parse_args()
    if args.command == "freeze":
        return freeze_corpus(args)
    if args.command == "apply":
        return apply_session(args)
    if args.command == "evaluate":
        return evaluate_corpus(args)
    if args.command == "run":
        freeze_args = argparse.Namespace(
            sessions_root=args.sessions_root,
            out_dir=args.out_dir,
            force=args.force_freeze,
        )
        frozen = freeze_corpus(freeze_args)
        if frozen != 0:
            return frozen
        evaluate_args = argparse.Namespace(
            sessions_root=args.sessions_root,
            out_dir=args.out_dir,
            apply=True,
            synthesize=not args.skip_synthesis,
        )
        return evaluate_corpus(evaluate_args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
