#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.live_boundary_island_micro_asr_lab/v1"
DEFAULT_MODEL = "~/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_./+-]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a diagnostic micro-ASR lab for live boundary-island candidates. "
            "The lab writes evidence only and never changes live drafts or batch transcripts."
        )
    )
    parser.add_argument("--report", type=Path, default=Path("sessions/_reports/live-pipeline/live_corpus_gates_report.json"))
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/live-pipeline"))
    parser.add_argument("--model", type=Path, default=Path(DEFAULT_MODEL))
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default="whisper-cli")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument(
        "--source-scope",
        choices=("live", "batch-reference", "both"),
        default="both",
        help="live uses live chunk wavs; batch-reference uses full-session preprocessed mic as a diagnostic reference.",
    )
    parser.add_argument("--force", action="store_true", help="Rerun whisper-cli even when cached micro-ASR JSON exists.")
    return parser.parse_args()


def load_transcribe_bridge() -> Any:
    path = Path(__file__).resolve().parent / "transcribe-simple-whispercpp.py"
    spec = importlib.util.spec_from_file_location("murmurmark_transcribe_bridge_for_live_boundary_lab", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import transcribe bridge: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def clean_text(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def normalize(text: str) -> str:
    return " ".join(token.lower().replace("ё", "е") for token in TOKEN_RE.findall(str(text or "")))


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def bag_recall(source: list[str], target: list[str]) -> float:
    if not source:
        return 0.0
    remaining: dict[str, int] = {}
    for token in target:
        remaining[token] = remaining.get(token, 0) + 1
    matched = 0
    for token in source:
        count = remaining.get(token, 0)
        if count <= 0:
            continue
        matched += 1
        remaining[token] = count - 1
    return matched / len(source)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def run(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return completed.returncode


def load_chunks(session: Path) -> list[dict[str, Any]]:
    chunks_path = session / "derived/live/chunks.jsonl"
    rows = read_jsonl(chunks_path)
    chunks: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        chunks.append(row)
    chunks.sort(key=lambda item: safe_float(item.get("start_sec")))
    return chunks


def live_chunk_sources(session: Path, start_sec: float, end_sec: float) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for chunk in load_chunks(session):
        mic = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
        wav_rel = mic.get("wav")
        if not wav_rel:
            continue
        clip_start = safe_float(mic.get("clip_start_sec"), safe_float(chunk.get("clip_start_sec")))
        clip_end = safe_float(mic.get("clip_end_sec"), safe_float(chunk.get("clip_end_sec")))
        if clip_end <= start_sec or clip_start >= end_sec:
            continue
        path = session / str(wav_rel)
        if not path.exists():
            continue
        sources.append(
            {
                "source_scope": "live",
                "source_label": f"live_chunk_{safe_int(chunk.get('index')):06d}_mic",
                "source": path,
                "source_start_sec": clip_start,
                "source_end_sec": clip_end,
                "chunk_index": chunk.get("index"),
                "chunk_start_sec": chunk.get("start_sec"),
                "chunk_end_sec": chunk.get("end_sec"),
                "remote_text": live_remote_text_for_interval(session, chunk, start_sec, end_sec),
            }
        )
    return sources


def live_remote_text_for_interval(session: Path, chunk: dict[str, Any], start_sec: float, end_sec: float) -> str:
    remote = chunk.get("remote") if isinstance(chunk.get("remote"), dict) else {}
    json_path = remote.get("asr", {}).get("json") if isinstance(remote.get("asr"), dict) else None
    if not json_path:
        return clean_text(str(remote.get("text") or ""))
    path = Path(str(json_path))
    if not path.is_absolute():
        path = session / path
    if not path.exists():
        return clean_text(str(remote.get("text") or ""))
    bridge = load_transcribe_bridge()
    clip_start_ms = int(round(safe_float(remote.get("clip_start_sec"), safe_float(chunk.get("clip_start_sec"))) * 1000))
    text, _rows = bridge.read_micro_reasr_text(
        path,
        clip_start_ms,
        int(round(start_sec * 1000)),
        int(round(end_sec * 1000)),
    )
    return text or clean_text(str(remote.get("text") or ""))


def batch_reference_sources(session: Path, remote_text: str) -> list[dict[str, Any]]:
    audio_dir = session / "derived/preprocess/audio"
    preferred = [
        ("batch_clean_local_fir", audio_dir / "mic_clean_local_fir.wav"),
        ("batch_raw_for_asr", audio_dir / "mic_raw_for_asr.wav"),
        ("batch_role_masked_for_asr", audio_dir / "mic_role_masked_for_asr.wav"),
    ]
    rows: list[dict[str, Any]] = []
    for label, path in preferred:
        if path.exists():
            rows.append(
                {
                    "source_scope": "batch_reference",
                    "source_label": label,
                    "source": path,
                    "source_start_sec": 0.0,
                    "source_end_sec": None,
                    "remote_text": remote_text,
                }
            )
    return rows


def select_candidates(report: dict[str, Any], max_candidates: int) -> list[dict[str, Any]]:
    design = report.get("live_online_speaker_boundary_evidence_design_lab")
    design_examples = design.get("examples") if isinstance(design, dict) and isinstance(design.get("examples"), list) else []
    split = report.get("live_local_island_split_lab")
    split_examples = split.get("examples") if isinstance(split, dict) and isinstance(split.get("examples"), list) else []
    split_by_key = {
        (str(row.get("session") or ""), str(row.get("batch_id") or "")): row
        for row in split_examples
        if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    for item in design_examples:
        if not isinstance(item, dict) or item.get("design_unit") != "boundary_island_micro_asr":
            continue
        key = (str(item.get("session") or ""), str(item.get("batch_id") or ""))
        split_row = split_by_key.get(key)
        islands = split_row.get("local_island_examples") if isinstance(split_row, dict) else None
        if not isinstance(islands, list) or not islands:
            continue
        rows.append({**item, "local_island_examples": islands, "split_row": split_row})
    rows.sort(
        key=lambda row: (
            safe_int(row.get("priority"), 99),
            -safe_float(row.get("duration_sec")),
            str(row.get("session") or ""),
            safe_float(row.get("start")),
        )
    )
    return rows[:max_candidates]


def output_stem(*, candidate_index: int, island_index: int, source_label: str, window_label: str) -> str:
    safe_label = re.sub(r"[^0-9A-Za-z_.-]+", "_", source_label)
    safe_window = re.sub(r"[^0-9A-Za-z_.-]+", "_", window_label)
    return f"candidate_{candidate_index:03d}_island_{island_index:02d}_{safe_label}_{safe_window}"


def run_micro_asr_attempt(
    *,
    bridge: Any,
    source: dict[str, Any],
    micro_dir: Path,
    whisper_cli: str,
    model: Path,
    language: str,
    threads: int,
    candidate_index: int,
    island_index: int,
    start_sec: float,
    end_sec: float,
    window_label: str,
    before_sec: float,
    after_sec: float,
    leading_silence_ms: int,
    remote_text: str,
    force: bool,
) -> tuple[str, dict[str, Any]]:
    source_path = Path(source["source"])
    source_start_sec = safe_float(source.get("source_start_sec"))
    source_end = source.get("source_end_sec")
    source_end_sec = safe_float(source_end) if source_end is not None else None
    recognition_start_sec = max(source_start_sec, start_sec - before_sec)
    recognition_end_sec = end_sec + after_sec
    if source_end_sec is not None:
        recognition_end_sec = min(source_end_sec, recognition_end_sec)
    if recognition_end_sec <= recognition_start_sec:
        return "", {
            "status": "failed",
            "reason": "empty_window_after_source_bounds",
            "source_label": source.get("source_label"),
            "window_label": window_label,
        }

    target_start_ms = int(round(start_sec * 1000))
    target_end_ms = int(round(end_sec * 1000))
    selection_start_ms = target_start_ms
    selection_end_ms = target_end_ms
    source_relative_start_sec = max(0.0, recognition_start_sec - source_start_sec)
    duration_sec = max(0.001, recognition_end_sec - recognition_start_sec)
    stem = output_stem(
        candidate_index=candidate_index,
        island_index=island_index,
        source_label=str(source.get("source_label") or "source"),
        window_label=window_label,
    )
    wav_path = micro_dir / f"{stem}.wav"
    output_base = micro_dir / stem
    json_path = output_base.with_suffix(".json")

    if force or not json_path.exists():
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        ffmpeg_command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{source_relative_start_sec:.3f}",
            "-t",
            f"{duration_sec:.3f}",
            "-i",
            str(source_path),
            *(
                ["-af", f"adelay={leading_silence_ms}:all=1"]
                if leading_silence_ms > 0
                else []
            ),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(wav_path),
        ]
        ffmpeg_returncode = run(ffmpeg_command, output_base.with_suffix(".ffmpeg.log"))
        if ffmpeg_returncode != 0:
            return "", {
                "status": "failed",
                "reason": "ffmpeg_failed",
                "returncode": ffmpeg_returncode,
                "source": str(source_path),
                "source_label": source.get("source_label"),
                "window_label": window_label,
                "ffmpeg_log": str(output_base.with_suffix(".ffmpeg.log")),
            }
        command = [
            whisper_cli,
            "--model",
            str(model),
            "--language",
            language,
            "--threads",
            str(threads),
            "--max-context",
            "0",
            "--temperature",
            "0",
            "--temperature-inc",
            "0",
            "--no-fallback",
            "--output-json",
            "--output-json-full",
            "--output-txt",
            "--output-file",
            str(output_base),
            "--no-prints",
            "--log-score",
            "--suppress-nst",
            "--suppress-regex",
            r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
            "--file",
            str(wav_path),
        ]
        returncode = run(command, output_base.with_suffix(".run.log"))
        if returncode != 0:
            return "", {
                "status": "failed",
                "reason": "whisper_cli_failed",
                "returncode": returncode,
                "source": str(source_path),
                "source_label": source.get("source_label"),
                "window_label": window_label,
                "run_log": str(output_base.with_suffix(".run.log")),
            }
    if not json_path.exists():
        return "", {
            "status": "failed",
            "reason": "micro_json_not_found",
            "source": str(source_path),
            "source_label": source.get("source_label"),
            "window_label": window_label,
        }

    global_offset_ms = int(round(recognition_start_sec * 1000)) - leading_silence_ms
    text, rows = bridge.read_micro_reasr_text(json_path, global_offset_ms, selection_start_ms, selection_end_ms)
    score = bridge.score_micro_reasr_text(
        text,
        rows,
        target_start_ms,
        target_end_ms,
        remote_context_text=remote_text,
    )
    return text, {
        "status": "ok" if text else "empty",
        "source_scope": source.get("source_scope"),
        "source_label": source.get("source_label"),
        "source": str(source_path),
        "chunk_index": source.get("chunk_index"),
        "remote_text": remote_text,
        "window_label": window_label,
        "recognition_start_sec": round(recognition_start_sec, 3),
        "recognition_end_sec": round(recognition_end_sec, 3),
        "selection_start_sec": round(start_sec, 3),
        "selection_end_sec": round(end_sec, 3),
        "leading_silence_ms": leading_silence_ms,
        "text": text,
        "score": round(float(score), 6),
        "remote_similarity": round(float(bridge.text_similarity(text, remote_text)), 6) if remote_text else 0.0,
        "json": str(json_path),
        "wav": str(wav_path),
        "rows": rows,
    }


def classify_best_attempt(
    *,
    best: dict[str, Any] | None,
    batch_text: str,
    island_text: str,
    remote_text: str,
    bridge: Any,
) -> dict[str, Any]:
    if not best or best.get("status") != "ok":
        return {
            "label": "no_micro_asr_candidate",
            "publication_ready": False,
            "reason": "no successful non-empty micro-ASR attempt",
        }
    text = str(best.get("text") or "")
    batch_tokens = tokens(batch_text)
    micro_recall = bag_recall(batch_tokens, tokens(text))
    island_recall = bag_recall(batch_tokens, tokens(island_text))
    remote_similarity = float(best.get("remote_similarity") or 0.0)
    remote_recall = bag_recall(tokens(remote_text), tokens(text)) if remote_text else 0.0
    score = safe_float(best.get("score"))
    if remote_similarity > 0.42 or remote_recall > 0.50:
        label = "blocked_remote_similarity"
        reason = "micro-ASR text is too similar to overlapping remote text"
    elif score < 0.68:
        label = "blocked_low_micro_score"
        reason = "micro-ASR score is below diagnostic threshold"
    elif micro_recall <= island_recall + 0.05:
        label = "blocked_no_alignment_gain"
        reason = "micro-ASR does not materially improve token recall over existing island text"
    elif micro_recall < 0.30:
        label = "blocked_low_batch_alignment"
        reason = "micro-ASR still has weak alignment with the missing batch Me row"
    else:
        label = "micro_asr_alignment_candidate"
        reason = "micro-ASR improves local island alignment while staying remote-dissimilar"
    return {
        "label": label,
        "publication_ready": False,
        "reason": reason,
        "batch_token_recall": round(micro_recall, 6),
        "existing_island_batch_token_recall": round(island_recall, 6),
        "remote_text_recall_in_micro": round(remote_recall, 6),
        "remote_similarity": round(remote_similarity, 6),
        "score": round(score, 6),
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Live Boundary-Island Micro-ASR Lab",
        "",
        "Diagnostic only. This lab never changes live drafts, batch transcripts or promotion gates.",
        "",
        f"- status: `{report.get('status')}`",
        f"- candidates: {summary.get('candidate_count', 0)}",
        f"- islands: {summary.get('island_count', 0)}",
        f"- attempts: {summary.get('attempt_count', 0)}",
        f"- alignment candidates: {summary.get('alignment_candidate_count', 0)} / {summary.get('alignment_candidate_seconds', 0.0)} sec",
        f"- publication-ready seconds now: {summary.get('publication_ready_seconds', 0.0)}",
        "",
        "| Session | Batch id | Island | Best label | Source | Score | Batch recall | Remote sim | Text |",
        "| --- | --- | ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in report.get("items") or []:
        if not isinstance(row, dict):
            continue
        best = row.get("best_attempt") if isinstance(row.get("best_attempt"), dict) else {}
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        text = clean_text(str(best.get("text") or "")).replace("|", "\\|")
        lines.append(
            f"| `{row.get('session')}` | `{row.get('batch_id')}` | {row.get('island_index')} "
            f"| `{decision.get('label')}` | `{best.get('source_label')}` / `{best.get('window_label')}` "
            f"| {safe_float(best.get('score')):.3f} "
            f"| {safe_float(decision.get('batch_token_recall')):.3f} "
            f"| {safe_float(best.get('remote_similarity')):.3f} "
            f"| {text[:120]} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    report_path = args.report
    if not report_path.exists():
        print(f"report not found: {report_path}", file=sys.stderr)
        return 2
    model = args.model.expanduser()
    if not model.exists():
        print(f"model not found: {model}", file=sys.stderr)
        return 2
    whisper_cli = shutil.which(args.whisper_cli) or args.whisper_cli
    if shutil.which(args.whisper_cli) is None and not Path(args.whisper_cli).exists():
        print(f"whisper-cli not found: {args.whisper_cli}", file=sys.stderr)
        return 2

    bridge = load_transcribe_bridge()
    corpus_report = read_json(report_path)
    candidates = select_candidates(corpus_report, args.max_candidates)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    attempts_jsonl = args.out_dir / "live_boundary_island_micro_asr_lab_attempts.jsonl"
    report_json = args.out_dir / "live_boundary_island_micro_asr_lab.json"
    report_md = args.out_dir / "live_boundary_island_micro_asr_lab.md"

    windows = [
        ("tight", 0.3, 0.3),
        ("normal", 0.9, 0.7),
        ("wide", 1.6, 1.0),
    ]
    all_attempts: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    for candidate_index, candidate in enumerate(candidates, start=1):
        session_name = str(candidate.get("session") or "")
        session = args.sessions_root / session_name
        batch_text = str(candidate.get("text") or "")
        islands = candidate.get("local_island_examples") if isinstance(candidate.get("local_island_examples"), list) else []
        for island_index, island in enumerate(islands, start=1):
            if not isinstance(island, dict):
                continue
            start_sec = safe_float(island.get("start"))
            end_sec = safe_float(island.get("end"))
            if end_sec <= start_sec:
                continue
            island_text = str(island.get("text") or "")
            sources: list[dict[str, Any]] = []
            live_sources = live_chunk_sources(session, start_sec, end_sec)
            candidate_remote_text = clean_text(
                " ".join(str(row.get("remote_text") or "") for row in live_sources if isinstance(row, dict))
            )
            if args.source_scope in {"live", "both"}:
                sources.extend(live_sources)
            if args.source_scope in {"batch-reference", "both"}:
                sources.extend(batch_reference_sources(session, candidate_remote_text))
            island_attempts: list[dict[str, Any]] = []
            for source in sources:
                remote_text = str(source.get("remote_text") or "")
                for window_label, before_sec, after_sec in windows:
                    text, meta = run_micro_asr_attempt(
                        bridge=bridge,
                        source=source,
                        micro_dir=session / "derived/live/boundary-island-micro-asr-lab/micro_asr",
                        whisper_cli=whisper_cli,
                        model=model,
                        language=args.language,
                        threads=args.threads,
                        candidate_index=candidate_index,
                        island_index=island_index,
                        start_sec=start_sec,
                        end_sec=end_sec,
                        window_label=window_label,
                        before_sec=before_sec,
                        after_sec=after_sec,
                        leading_silence_ms=400,
                        remote_text=remote_text,
                        force=args.force,
                    )
                    meta.update(
                        {
                            "candidate_index": candidate_index,
                            "island_index": island_index,
                            "session": session_name,
                            "batch_id": candidate.get("batch_id"),
                            "batch_text": batch_text,
                            "existing_island_text": island_text,
                        }
                    )
                    if text:
                        meta["batch_token_recall"] = round(bag_recall(tokens(batch_text), tokens(text)), 6)
                        meta["existing_island_batch_token_recall"] = round(
                            bag_recall(tokens(batch_text), tokens(island_text)),
                            6,
                        )
                    island_attempts.append(meta)
                    all_attempts.append(meta)
            successful = [row for row in island_attempts if row.get("status") == "ok" and row.get("text")]
            successful.sort(
                key=lambda row: (
                    safe_float(row.get("batch_token_recall")),
                    -safe_float(row.get("remote_similarity")),
                    safe_float(row.get("score")),
                ),
                reverse=True,
            )
            live_successful = [row for row in successful if row.get("source_scope") == "live"]
            reference_successful = [row for row in successful if row.get("source_scope") == "batch_reference"]
            best_live = live_successful[0] if live_successful else None
            best_reference = reference_successful[0] if reference_successful else None
            best = best_live or best_reference or (successful[0] if successful else None)
            remote_text = str(best.get("remote_text") or "") if isinstance(best, dict) else ""
            decision = classify_best_attempt(
                best=best,
                batch_text=batch_text,
                island_text=island_text,
                remote_text=remote_text,
                bridge=bridge,
            )
            items.append(
                {
                    "schema": "murmurmark.live_boundary_island_micro_asr_item/v1",
                    "candidate_index": candidate_index,
                    "island_index": island_index,
                    "session": session_name,
                    "batch_id": candidate.get("batch_id"),
                    "start": start_sec,
                    "end": end_sec,
                    "duration_sec": round(end_sec - start_sec, 3),
                    "batch_text": batch_text,
                    "existing_island_text": island_text,
                    "best_attempt": best,
                    "best_live_attempt": best_live,
                    "best_reference_attempt": best_reference,
                    "decision": decision,
                    "attempt_count": len(island_attempts),
                    "successful_attempt_count": len(successful),
                }
            )

    alignment_candidates = [
        row for row in items
        if isinstance(row.get("decision"), dict)
        and row["decision"].get("label") == "micro_asr_alignment_candidate"
    ]
    summary = {
        "candidate_count": len(candidates),
        "island_count": len(items),
        "attempt_count": len(all_attempts),
        "successful_attempt_count": sum(1 for row in all_attempts if row.get("status") == "ok"),
        "alignment_candidate_count": len(alignment_candidates),
        "alignment_candidate_seconds": round(sum(safe_float(row.get("duration_sec")) for row in alignment_candidates), 3),
        "publication_ready_seconds": 0.0,
        "promotion_allowed": False,
        "source_scope": args.source_scope,
    }
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "report-live-boundary-island-micro-asr-lab", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok" if items else "no_boundary_island_candidates",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: micro-decodes local islands from remaining live gaps. "
            "It can identify a future shadow-profile candidate, but cannot publish live text by itself."
        ),
        "inputs": {
            "live_corpus_gates_report": str(report_path),
            "sessions_root": str(args.sessions_root),
            "model": str(model),
            "whisper_cli": whisper_cli,
        },
        "summary": summary,
        "items": items,
        "outputs": {
            "report_json": str(report_json),
            "attempts_jsonl": str(attempts_jsonl),
            "report_markdown": str(report_md),
        },
    }
    write_json(report_json, payload)
    write_jsonl(attempts_jsonl, all_attempts)
    report_md.write_text(markdown_report(payload), encoding="utf-8")
    print(f"status: {payload['status']}")
    print(f"report: {report_json}")
    print(f"attempts: {attempts_jsonl}")
    print(f"alignment_candidate_count: {summary['alignment_candidate_count']}")
    print(f"alignment_candidate_seconds: {summary['alignment_candidate_seconds']}")
    print("promotion_allowed: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
