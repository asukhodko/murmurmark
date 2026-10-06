"""Fingerprint-bound reading projection; never changes speaker eligibility or gates."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import review_audio_evidence as review
import transcript_publication as publication
import transcript_interval_evidence
import micro_asr_evidence
from murmurmark_deadline import check_deadline

SCHEMA = "murmurmark.transcript_read_view/v1"
DIRECTORY = Path("derived/transcript-rich/read-view-v1")
PLAN = Path("derived/readiness/review-plan")
STRICT = Path("derived/transcript-rich/speaker-resolved-default-v1/selection.json")
PROVISIONAL = Path("derived/transcript-rich/speaker-resolved-default-v1/provisional/selection.json")
DISPLAY_REASONS = {"speaker_turn_text_mismatch", "speaker_turn_time_conflict", "timestamp_unavailable"}


def read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path.name}")
    return value


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local(session: Path, value: str) -> Path:
    path = (session / value).resolve()
    if not path.is_relative_to(session.resolve()):
        raise ValueError("read-view path outside session")
    return path


def identity(path: Path) -> dict:
    return {"path": str(path), "sha256": sha(path) if path.is_file() else None}


def context(outcome: dict) -> dict:
    return {**{key: outcome.get(key) for key in ("selected_profile", "outcome", "verdict", "use_gate", "export_status")},
            "deferred_enrichment": next((g.get("value") for g in outcome.get("gates", [])
                                         if g.get("id") == "deferred_enrichment"), "not_reported")}


def basis(session: Path, outcome: dict) -> dict:
    session = session.expanduser().resolve()
    speaker = outcome.get("speaker_resolution") or {}
    selection_path = session / (PROVISIONAL if speaker.get("state") in {"provisional", "unavailable"} else STRICT)
    selection = read(selection_path)
    if selection.get("selected_profile") != outcome.get("selected_profile"):
        raise ValueError("read-view source profile mismatch")
    if (selection.get("state") != speaker.get("state") or
            selection.get("semantic_fingerprint") != speaker.get("selection_fingerprint") or
            (selection.get("selected_transcript") or {}).get("path") != speaker.get("transcript_path")):
        raise ValueError("read-view source selection mismatch")
    inputs = [selection_path, session / "derived/readiness/session_readiness.json",
              session / PLAN / "review_decisions_progress.json",
              session / PLAN / "review_decisions.template.jsonl", session / PLAN / "review_decisions.jsonl"]
    for key in ("selected_dialogue", "rich_transcript", "selected_transcript"):
        row = selection.get(key)
        if row is None and key == "rich_transcript":
            continue
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("read-view source identity missing")
        path = local(session, row["path"])
        if not path.is_file() or sha(path) != row.get("sha256"):
            raise ValueError("read-view source identity stale")
        inputs.append(path)
    implementations = [Path(__file__), Path(publication.__file__), Path(review.__file__),
                       Path(transcript_interval_evidence.__file__), Path(micro_asr_evidence.__file__)]
    snapshot = review.read_queue_snapshot(session)
    return {"source_selection": str(selection_path.relative_to(session)),
            "speaker_resolution": speaker, "context": context(outcome),
            "inputs": [identity(p) for p in inputs],
            "implementations": [identity(p.resolve()) for p in implementations],
            "queue_fingerprint": snapshot["fingerprint"] if snapshot else None}


def questions(session: Path, profile: str) -> tuple[list[dict], str]:
    session = session.expanduser().resolve()
    snapshot = review.read_queue_snapshot(session)
    if snapshot is None:
        return [], "unavailable_or_stale"
    pending = {item["id"] for item in snapshot["items"]}
    result = []
    # The queue also retains unresolved historical decisions absent from the
    # regenerated template. Their exact identity must still match the snapshot.
    lines = []
    for name in ("review_decisions.template.jsonl", "review_decisions.jsonl"):
        path = session / PLAN / name
        if path.is_file():
            lines.extend(path.read_text().splitlines())
    emitted = set()
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        qid = review.digest(review.row_identity(row))
        if qid not in pending or qid in emitted:
            continue
        emitted.add(qid)
        if row.get("session_id") != session.name:
            raise ValueError("read-view queue scope mismatch")
        features = row.get("review_features") or {}
        reasons = list(features.get("micro_asr_selection_review_reasons") or [])
        reasons += (features.get("interval_ownership_review") or {}).get("reasons") or []
        if not reasons:
            reasons = [row.get("label") or row.get("source") or "review_required"]
        source = row.get("source")
        facet = {"transcript_text": "text", "transcript_order": "time", "local_recall": "text"}.get(source, "role")
        ids = row.get("utterance_ids") or list(dict.fromkeys((row.get("me_utterance_ids") or []) + (row.get("remote_utterance_ids") or [])))
        result.append({"id": qid, "source_audit_id": row.get("source_audit_id"), "facet": facet,
                       "reasons": sorted(set(reasons)), "utterance_ids": ids,
                       "input_profile": row.get("input_profile"),
                       "placement_state": "current_profile" if row.get("input_profile") == profile else "historical_profile_unplaced",
                       "interval": row.get("interval"), "decision_scope": "individual_question"})
    if {q["id"] for q in result} != pending:
        raise ValueError("read-view queue incomplete")
    return result, "current"


def project(utterances: list[dict], pending: list[dict], queue_state: str,
            timing: dict | None = None, attributions: dict | None = None) -> list[dict]:
    turns = publication.display_turns(utterances, attributions=attributions, timing_evidence=timing)
    by_id: dict[str, list[dict]] = {}
    for question in pending:
        if question.get("placement_state") == "historical_profile_unplaced":
            continue
        for uid in question["utterance_ids"]:
            by_id.setdefault(uid, []).extend({"facet": question["facet"], "reason": reason,
                "question_id": question["id"], "evidence_key": question["source_audit_id"]}
                for reason in question["reasons"])
    for turn in turns:
        # A current full queue governs review closure. Historical source flags
        # must not resurrect questions already answered in their own scope.
        if queue_state == "current":
            turn["review_reasons"] = [r for r in turn["review_reasons"]
                                      if r["reason"] in DISPLAY_REASONS or r["evidence_key"] == "acoustic_timing"]
        turn["review_reasons"].extend(by_id.get(turn["utterance_id"], []))
    for row in utterances:
        projected = sorted((t for t in turns if t["utterance_id"] == row["id"]), key=lambda t: t["turn_index"])
        if "".join(t["text"] for t in projected) != row["text"]:
            raise ValueError("read-view text conservation failed")
    return turns


def render(payload: dict) -> str:
    ctx = payload["basis"]["context"]
    speaker = payload["basis"]["speaker_resolution"]
    ratio = speaker.get("attributed_remote_speech_ratio")
    coverage = f"{ratio * 100:.2f}%" if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) else "unknown"
    lines = ["# Transcript", "", "> Reading projection; original text and speaker evidence are unchanged.",
             f"> Quality: `{ctx['outcome']}`; use gate: `{ctx['use_gate']}`; export: `{ctx['export_status']}`.",
             f"> Attribution: `{speaker.get('state')}` / `{speaker.get('selected_speaker_profile')}`. Labels are not verified human identities.",
             f"> Attributed remote-speech coverage: {coverage}; this is not speaker or word accuracy.",
             f"> Attribution fallback reason: `{speaker.get('fallback_reason') or 'none'}`. Unknown labels do not identify one person.",
             f"> Additional checks: `{ctx['deferred_enrichment']}`. Attribution selection does not clear review gates.",
             f"> Review queue: `{payload['queue_state']}`; open questions: {len(payload['questions']) if payload['queue_state'] == 'current' else 'unknown'}.", ""]
    if payload["unplaced_question_ids"]:
        lines.extend(["> Some open questions have no matching utterance in this view; see the complete Review Questions appendix.", ""])
    lines.extend(publication.render_body(payload["display_turns"]))
    if payload["questions"]:
        lines.extend(["## Review Questions", "", "Each question has an independent decision; a reading projection does not answer it.", ""])
        for question in payload["questions"]:
            lines.extend([f"<a id=\"review-{question['id']}\"></a>",
                          f"- `{question['id']}` / `{question['source_audit_id']}` ({question['facet']}): "
                          + ", ".join(question["reasons"]) + "; utterances: "
                          + ", ".join(f"`{uid}`" for uid in question["utterance_ids"])
                          + f"; profile: `{question['input_profile']}`; placement: `{question['placement_state']}`.", ""])
    return "\n".join(lines).rstrip() + "\n"


def atomic_write(path: Path, data: bytes) -> None:
    check_deadline()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def materialize(session: Path, outcome: dict) -> dict:
    session = session.expanduser().resolve()
    source_basis = basis(session, outcome)
    selection = read(session / source_basis["source_selection"])
    rich_identity = selection.get("rich_transcript") or selection["selected_dialogue"]
    source = read(local(session, rich_identity["path"]))
    utterances = source.get("utterances") or []
    if not isinstance(utterances, list) or len({u["id"] for u in utterances}) != len(utterances):
        raise ValueError("read-view utterance IDs invalid")
    pending, queue_state = questions(session, outcome["selected_profile"])
    utterance_ids = {u["id"] for u in utterances}
    payload = {"schema": SCHEMA, "basis": source_basis, "queue_state": queue_state,
               "questions": pending, "utterances": utterances,
               "unplaced_question_ids": [q["id"] for q in pending if q["placement_state"] != "current_profile"
                                         or not set(q["utterance_ids"]) & utterance_ids],
               "display_turns": project(utterances, pending, queue_state, source.get("acoustic_timing_evidence"),
                                        source.get("remote_utterance_attributions"))}
    generation = hashlib.sha256(encoded(payload)).hexdigest()
    directory = session / DIRECTORY / "generations" / generation
    rich, markdown = directory / "transcript.read.json", directory / "transcript.read.md"
    atomic_write(rich, encoded(payload))
    atomic_write(markdown, render(payload).encode())
    if basis(session, outcome) != source_basis:
        raise ValueError("read-view inputs changed during publication")
    pointer = {"schema": SCHEMA, "basis": source_basis, "generation": generation,
               "rich": {"path": str(rich.relative_to(session)), "sha256": sha(rich), "bytes": rich.stat().st_size},
               "markdown": {"path": str(markdown.relative_to(session)), "sha256": sha(markdown), "bytes": markdown.stat().st_size}}
    atomic_write(session / DIRECTORY / "selection.json", encoded(pointer))
    return {"status": "current", "path": pointer["markdown"]["path"], "generation": generation}


def verified_path(session: Path, outcome: dict, *, expected_generation: str | None = None) -> Path | None:
    session = session.expanduser().resolve()
    try:
        pointer = read(session / DIRECTORY / "selection.json")
        if expected_generation is not None and pointer.get("generation") != expected_generation:
            return None
        if pointer.get("schema") != SCHEMA or pointer.get("basis") != basis(session, outcome):
            return None
        for kind in ("rich", "markdown"):
            path = local(session, pointer[kind]["path"])
            if sha(path) != pointer[kind]["sha256"]:
                return None
        rich = read(local(session, pointer["rich"]["path"]))
        if rich.get("basis") != pointer["basis"] or hashlib.sha256(encoded(rich)).hexdigest() != pointer["generation"]:
            return None
        return local(session, pointer["markdown"]["path"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
