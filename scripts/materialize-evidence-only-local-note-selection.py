#!/usr/bin/env python3
"""Select exact evidence statements without allowing model-authored wording."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.evidence_only_local_note_selection_policy/v1"
HANDOFF_SCHEMA = "murmurmark.evidence_only_local_note_selection_handoff/v1"
CATALOG_SCHEMA = "murmurmark.evidence_statement_catalog/v1"
SELECTION_SCHEMA = "murmurmark.evidence_only_local_note_selection/v1"
MODEL_RUN_SCHEMA = "murmurmark.evidence_only_local_note_selection_model_run/v1"
REPORT_SCHEMA = "murmurmark.evidence_only_local_note_selection_report/v1"
FROZEN_SCHEMA = "murmurmark.evidence_only_local_note_selection_frozen_manifest/v1"
DEFAULT_OUTPUT = Path("derived/meeting-memory/evidence-only-selection-v1")
DEFAULT_POLICY = Path("policies/evidence-only-local-note-selection-v1.json")
OUTPUT_FILENAMES = {
    "catalog_json": "candidate_catalog.json",
    "selection_json": "selection.json",
    "model_run_json": "model_run.json",
    "notes": "notes.md",
    "transcript": "transcript.md",
    "quality_verdict": "quality_verdict.md",
}
CATEGORIES = ("summary", "decisions", "actions", "risks", "open_questions")
SOURCE_CATEGORY = {
    "outline": "summary",
    "decisions": "decisions",
    "actions": "actions",
    "risks": "risks",
    "open_questions": "open_questions",
}
HEADINGS = {
    "summary": "Summary",
    "decisions": "Potential Decisions",
    "actions": "Potential Actions",
    "risks": "Risks",
    "open_questions": "Open Questions",
}


class SelectionError(RuntimeError):
    pass


class SimulatedInterruption(RuntimeError):
    pass


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SelectionError(f"helper_cannot_be_loaded:{path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = load_module(
    "evidence_guarded_local_synthesis_for_id_selection",
    ROOT / "scripts/materialize-evidence-guarded-local-synthesis.py",
)
speaker_memory = legacy.speaker_memory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--speaker-memory-dir", type=Path)
    parser.add_argument("--reviewed-speaker-dir", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--print-path", choices=sorted(OUTPUT_FILENAMES))
    parser.add_argument("--qualification-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--keep-alive", help=argparse.SUPPRESS)
    parser.add_argument("--simulate-interruption-before-publish", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def canonical_bytes(value: Any) -> bytes:
    return legacy.canonical_bytes(value)


def compact_bytes(value: Any) -> bytes:
    return legacy.compact_bytes(value)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        return legacy.read_json(path)
    except legacy.LocalSynthesisError as error:
        raise SelectionError(str(error)) from error


def implementation() -> dict[str, Any]:
    path = Path(__file__).resolve()
    return {
        "script": path.name,
        "version": SCRIPT_VERSION,
        "fingerprint": legacy.repository_identity(path),
    }


def policy_path(raw: Path | None) -> Path:
    try:
        return legacy.repository_path(raw or DEFAULT_POLICY)
    except legacy.LocalSynthesisError as error:
        raise SelectionError(str(error)) from error


def validate_policy(path: Path, *, allow_unpromoted: bool) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise SelectionError("policy_schema_mismatch")
    allowed = {"PROMOTE_OPTIONAL_EVIDENCE_SELECTION"}
    if allow_unpromoted:
        allowed.update({"QUALIFICATION_PENDING", "DO_NOT_PROMOTE"})
    if policy.get("decision") not in allowed:
        raise SelectionError("policy_not_promoted")
    prompt = policy.get("prompt") if isinstance(policy.get("prompt"), dict) else {}
    try:
        prompt_file = legacy.repository_path(str(prompt.get("path") or ""))
    except legacy.LocalSynthesisError as error:
        raise SelectionError(str(error)) from error
    if not prompt_file.is_file() or legacy.sha256_file(prompt_file) != prompt.get("sha256"):
        raise SelectionError("prompt_fingerprint_mismatch")
    runtime = policy.get("runtime") if isinstance(policy.get("runtime"), dict) else {}
    try:
        legacy.validate_loopback_endpoint(str(runtime.get("endpoint") or ""))
    except legacy.LocalSynthesisError as error:
        raise SelectionError(str(error)) from error
    source = policy.get("source") if isinstance(policy.get("source"), dict) else {}
    source_manifest = source.get("source_manifest")
    if not isinstance(source_manifest, dict) or not legacy.identity_matches(source_manifest, ROOT):
        raise SelectionError("source_manifest_fingerprint_mismatch")
    if not allow_unpromoted:
        if source.get("materializer") != implementation():
            raise SelectionError("policy_materializer_fingerprint_mismatch")
        frozen_identity = source.get("frozen_manifest")
        if not isinstance(frozen_identity, dict) or not legacy.identity_matches(frozen_identity, ROOT):
            raise SelectionError("policy_frozen_manifest_fingerprint_mismatch")
        frozen_path = legacy.resolve_identity(frozen_identity, ROOT)
        if frozen_path is None:
            raise SelectionError("policy_frozen_manifest_missing")
        frozen = read_json(frozen_path)
        if frozen.get("schema") != FROZEN_SCHEMA or frozen.get("decision") != policy.get("decision"):
            raise SelectionError("policy_frozen_manifest_not_promoted")
    return policy


def material_paths(
    session: Path,
    decision_path: Path,
    memory_root: Path,
    reviewed_root: Path | None,
) -> tuple[dict[str, Any], dict[str, Path], dict[str, Any]]:
    memory_manifest, reasons = speaker_memory.verify_handoff(
        session,
        decision_path,
        memory_root,
        reviewed_root,
    )
    if memory_manifest is None:
        raise SelectionError("reviewed_speaker_memory_invalid:" + ",".join(reasons))
    paths: dict[str, Path] = {}
    for key in ("memory_json", "notes", "transcript", "quality_verdict"):
        path = speaker_memory.artifact_path(memory_manifest, session, key)
        if path is None:
            raise SelectionError(f"reviewed_speaker_memory_artifact_missing:{key}")
        paths[key] = path
    return memory_manifest, paths, read_json(paths["memory_json"])


def dedupe(values: list[Any]) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = str(raw)
        if value and value not in result:
            result.append(value)
    return result


def build_catalog(session: Path, memory: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    rows = memory.get("statement_bindings")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SelectionError("speaker_memory_statement_bindings_missing")
    high_categories = set((policy.get("selection") or {}).get("high_confidence_categories") or [])
    candidates: list[dict[str, Any]] = []
    known: set[str] = set()
    for source_order, row in enumerate(rows):
        source_category = str(row.get("category") or "")
        category = SOURCE_CATEGORY.get(source_category)
        if category is None:
            continue
        statement_id = str(row.get("statement_id") or "")
        text = str(row.get("text") or "")
        evidence_ids = dedupe(row.get("evidence_utterance_ids") or [])
        context_ids = dedupe(row.get("context_utterance_ids") or [])
        speaker_evidence = row.get("speaker_evidence")
        if not statement_id or statement_id in known:
            raise SelectionError("candidate_statement_id_invalid")
        if not text or not evidence_ids:
            raise SelectionError(f"candidate_evidence_invalid:{statement_id}")
        if row.get("text_sha256") != sha256_bytes(text.encode("utf-8")):
            raise SelectionError(f"candidate_text_fingerprint_mismatch:{statement_id}")
        if not isinstance(speaker_evidence, list) or not all(
            isinstance(item, dict) for item in speaker_evidence
        ):
            raise SelectionError(f"candidate_speaker_evidence_invalid:{statement_id}")
        speaker_ids = [str(item.get("utterance_id") or "") for item in speaker_evidence]
        if speaker_ids != evidence_ids:
            raise SelectionError(f"candidate_speaker_evidence_membership_mismatch:{statement_id}")
        speakers = dedupe([item.get("display_label") for item in speaker_evidence])
        if not speakers:
            raise SelectionError(f"candidate_speaker_label_missing:{statement_id}")
        needs_review = bool(row.get("needs_review"))
        high_confidence = category in high_categories and not needs_review
        candidates.append(
            {
                "statement_id": statement_id,
                "category": category,
                "source_category": source_category,
                "source_order": source_order,
                "text": text,
                "text_sha256": row["text_sha256"],
                "evidence_utterance_ids": evidence_ids,
                "context_utterance_ids": context_ids,
                "speaker_evidence": speaker_evidence,
                "evidence_speakers": speakers,
                "needs_review": needs_review,
                "high_confidence": high_confidence,
            }
        )
        known.add(statement_id)
    if not candidates:
        raise SelectionError("candidate_catalog_empty")
    basis = {
        "schema": CATALOG_SCHEMA,
        "version": 1,
        "session_id": session.name,
        "source_speaker_memory_fingerprint": memory.get("source", {}).get(
            "reviewed_speaker_fingerprint"
        ),
        "candidates": candidates,
    }
    return {**basis, "catalog_sha256": sha256_bytes(compact_bytes(basis))}


def build_prompt_input(catalog: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    limits = policy["limits"]
    candidates = catalog["candidates"]
    selection_limits: dict[str, dict[str, int]] = {}
    for category in CATEGORIES:
        required = sum(
            row["category"] == category and row["high_confidence"] for row in candidates
        )
        limit = int(limits[category])
        if required > limit:
            raise SelectionError(f"baseline_high_confidence_exceeds_limit:{category}")
        selection_limits[category] = {
            "total_limit": limit,
            "required_high_confidence": required,
            "max_model_selections": limit - required,
        }
    return {
        "schema": "murmurmark.evidence_only_selection_prompt_input/v1",
        "session_id": catalog["session_id"],
        "catalog_sha256": catalog["catalog_sha256"],
        "selection_limits": selection_limits,
        "max_total_model_selections": sum(
            row["max_model_selections"] for row in selection_limits.values()
        ),
        "candidates": [
            {
                "statement_id": row["statement_id"],
                "category": row["category"],
                "text": row["text"],
                "needs_review": row["needs_review"],
                "high_confidence": row["high_confidence"],
                "evidence_speakers": row["evidence_speakers"],
                "evidence_utterance_ids": row["evidence_utterance_ids"],
            }
            for row in candidates
        ],
    }


def render_prompt(prompt_input: dict[str, Any], policy: dict[str, Any]) -> tuple[str, Path]:
    prompt_file = legacy.repository_path(str(policy["prompt"]["path"]))
    template = prompt_file.read_text(encoding="utf-8")
    marker = "{{INPUT_JSON}}"
    if template.count(marker) != 1:
        raise SelectionError("prompt_marker_invalid")
    rendered = template.replace(marker, json.dumps(prompt_input, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    rendered_bytes = len(rendered.encode("utf-8"))
    if rendered_bytes > int(policy["prompt_budget"]["max_rendered_bytes"]):
        raise SelectionError(f"prompt_budget_exceeded:{rendered_bytes}")
    return rendered, prompt_file


def selection_response_schema(prompt_input: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for category in CATEGORIES:
        allowed = [
            row["statement_id"]
            for row in prompt_input["candidates"]
            if row["category"] == category and not row.get("high_confidence")
        ]
        item_schema: dict[str, Any] = {"type": "string"}
        if allowed:
            item_schema["enum"] = allowed
        properties[category] = {
            "type": "array",
            "items": item_schema,
            "maxItems": int(
                prompt_input["selection_limits"][category]["max_model_selections"]
            ),
            "uniqueItems": True,
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(CATEGORIES),
        "additionalProperties": False,
    }


def run_selection_model(
    prompt: str,
    prompt_input: dict[str, Any],
    policy: dict[str, Any],
    *,
    keep_alive: str | None,
    client: Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if client is not None:
        return client(prompt, policy)
    runtime_identity = legacy.inspect_runtime(policy)
    endpoint = legacy.validate_loopback_endpoint(str(policy["runtime"]["endpoint"]))
    generation = dict(policy["generation"])
    default_keep_alive = str(generation.pop("keep_alive"))
    response_schema = selection_response_schema(prompt_input)
    request_payload = {
        "model": policy["model"]["name"],
        "stream": False,
        "format": response_schema,
        "prompt": prompt,
        "keep_alive": keep_alive or default_keep_alive,
        "options": generation,
    }
    started = time.monotonic()
    with legacy.OllamaSampler(str(policy["model"]["blob_sha256"])) as sampler:
        response = legacy.request_json(
            f"{endpoint}/api/generate", request_payload, 600
        )
    wall_sec = time.monotonic() - started
    raw_response = str(response.get("response") or "")
    if not raw_response.strip():
        raise SelectionError("ollama_empty_response")
    deterministic = {
        "runtime_identity": runtime_identity,
        "request": {
            "model": request_payload["model"],
            "format": response_schema,
            "options": request_payload["options"],
        },
        "raw_response": raw_response,
        "raw_response_sha256": sha256_bytes(raw_response.encode("utf-8")),
        "prompt_eval_count": int(response.get("prompt_eval_count") or 0),
        "eval_count": int(response.get("eval_count") or 0),
    }
    performance = {
        "wall_sec": round(wall_sec, 6),
        "load_sec": round(float(response.get("load_duration") or 0) / 1_000_000_000, 6),
        "prompt_eval_sec": round(
            float(response.get("prompt_eval_duration") or 0) / 1_000_000_000, 6
        ),
        "eval_sec": round(float(response.get("eval_duration") or 0) / 1_000_000_000, 6),
        "peak_model_rss_mb": round(sampler.peak_rss_kb / 1024, 3),
        "sampled_model_pids": sorted(sampler.pids),
        "nice_20_applied": bool(sampler.nice_applied),
    }
    return deterministic, performance


def parse_model_selection(
    raw: str,
    prompt_input: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, Any], list[str]]:
    errors: list[str] = []
    empty = {category: [] for category in CATEGORIES}
    empty_trace = {
        "model_container_ids": empty,
        "model_ranked_ids": [],
        "policy_dropped_ids": [],
        "category_normalizations": [],
    }
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return empty, empty_trace, ["model_response_invalid_json"]
    if not isinstance(payload, dict):
        return empty, empty_trace, ["model_response_not_object"]
    if set(payload) != set(CATEGORIES):
        errors.append("model_response_category_set_mismatch")
    known = {row["statement_id"]: row for row in prompt_input["candidates"]}
    container_ids: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    ranked_ids: list[str] = []
    seen: set[str] = set()
    dropped: list[dict[str, str]] = []
    normalizations: list[dict[str, str]] = []
    for container in CATEGORIES:
        rows = payload.get(container)
        if not isinstance(rows, list):
            errors.append(f"model_category_not_array:{container}")
            continue
        for index, statement_id in enumerate(rows, start=1):
            if not isinstance(statement_id, str) or not statement_id:
                errors.append(f"model_statement_id_invalid:{container}:{index}")
                continue
            container_ids[container].append(statement_id)
            if statement_id not in known:
                errors.append(f"model_unknown_statement_id:{statement_id}")
                continue
            if statement_id in seen:
                dropped.append({"statement_id": statement_id, "reason": "duplicate_ignored"})
                continue
            seen.add(statement_id)
            ranked_ids.append(statement_id)
            source_category = known[statement_id]["category"]
            if source_category != container:
                normalizations.append(
                    {
                        "statement_id": statement_id,
                        "from": container,
                        "to": source_category,
                    }
                )
    selected: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    for statement_id in ranked_ids:
        source = known[statement_id]
        category = source["category"]
        if source.get("high_confidence"):
            dropped.append(
                {"statement_id": statement_id, "reason": "baseline_high_confidence_auto_kept"}
            )
            continue
        limit = int(prompt_input["selection_limits"][category]["max_model_selections"])
        if len(selected[category]) >= limit:
            dropped.append(
                {"statement_id": statement_id, "reason": f"category_limit:{category}"}
            )
            continue
        selected[category].append(statement_id)
    trace = {
        "model_container_ids": container_ids,
        "model_ranked_ids": ranked_ids,
        "policy_dropped_ids": dropped,
        "category_normalizations": normalizations,
    }
    return selected, trace, dedupe(errors)


def exact_selected_row(source: dict[str, Any], rank: int, selection_source: str) -> dict[str, Any]:
    return {
        "statement_id": source["statement_id"],
        "category": source["category"],
        "rank": rank,
        "selection_source": selection_source,
        "text": source["text"],
        "text_sha256": source["text_sha256"],
        "evidence_utterance_ids": source["evidence_utterance_ids"],
        "context_utterance_ids": source["context_utterance_ids"],
        "speaker_evidence": source["speaker_evidence"],
        "evidence_speakers": source["evidence_speakers"],
        "needs_review": source["needs_review"],
        "high_confidence": source["high_confidence"],
    }


def build_selected(
    catalog: dict[str, Any],
    model_ids: dict[str, list[str]],
    policy: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    by_id = {row["statement_id"]: row for row in catalog["candidates"]}
    selected: dict[str, list[dict[str, Any]]] = {category: [] for category in CATEGORIES}
    errors: list[str] = []
    for category in CATEGORIES:
        source_rows = [row for row in catalog["candidates"] if row["category"] == category]
        required = [row["statement_id"] for row in source_rows if row["high_confidence"]]
        ordered = required + [item for item in model_ids[category] if item not in required]
        if len(ordered) > int(policy["limits"][category]):
            errors.append(f"merged_category_limit_exceeded:{category}")
            continue
        for rank, statement_id in enumerate(ordered, start=1):
            selected[category].append(
                exact_selected_row(
                    by_id[statement_id],
                    rank,
                    "baseline_high_confidence" if statement_id in required else "model_id_selection",
                )
            )
    return selected, errors


def selection_metrics(
    catalog: dict[str, Any],
    selected: dict[str, list[dict[str, Any]]],
    contract_errors: list[str],
) -> dict[str, Any]:
    source = catalog["candidates"]
    chosen = [row for category in CATEGORIES for row in selected[category]]
    source_review = [row for row in source if row["needs_review"]]
    chosen_review = [row for row in chosen if row["needs_review"]]
    source_high = [row for row in source if row["high_confidence"]]
    chosen_ids = {row["statement_id"] for row in chosen}
    retained_high = [row for row in source_high if row["statement_id"] in chosen_ids]
    available_categories = {row["category"] for row in source}
    selected_categories = {row["category"] for row in chosen}
    available_speakers = {speaker for row in source for speaker in row["evidence_speakers"]}
    selected_speakers = {speaker for row in chosen for speaker in row["evidence_speakers"]}
    source_by_category = {
        category: sum(row["category"] == category for row in source)
        for category in CATEGORIES
    }
    selected_by_category = {
        category: sum(row["category"] == category for row in chosen)
        for category in CATEGORIES
    }
    source_review_by_category = {
        category: sum(
            row["category"] == category and row["needs_review"] for row in source
        )
        for category in CATEGORIES
    }
    selected_review_by_category = {
        category: sum(
            row["category"] == category and row["needs_review"] for row in chosen
        )
        for category in CATEGORIES
    }
    review_ratio = len(chosen_review) / len(source_review) if source_review else 0.0
    high_ratio = len(retained_high) / len(source_high) if source_high else 1.0
    category_ratio = len(selected_categories) / len(available_categories) if available_categories else 1.0
    speaker_ratio = len(selected_speakers) / len(available_speakers) if available_speakers else 1.0
    by_id = {row["statement_id"]: row for row in source}
    exact = all(
        row["text"] == by_id[row["statement_id"]]["text"]
        and row["text_sha256"] == by_id[row["statement_id"]]["text_sha256"]
        and row["evidence_utterance_ids"] == by_id[row["statement_id"]]["evidence_utterance_ids"]
        and row["speaker_evidence"] == by_id[row["statement_id"]]["speaker_evidence"]
        for row in chosen
    )
    return {
        "source_candidates": len(source),
        "selected_statements": len(chosen),
        "source_review_marked": len(source_review),
        "selected_review_marked": len(chosen_review),
        "selected_review_ratio": round(review_ratio, 6),
        "review_compression_ratio": round(1.0 - review_ratio, 6),
        "baseline_high_confidence": len(source_high),
        "retained_baseline_high_confidence": len(retained_high),
        "baseline_high_confidence_retention_ratio": round(high_ratio, 6),
        "available_categories": len(available_categories),
        "selected_categories": len(selected_categories),
        "category_coverage_ratio": round(category_ratio, 6),
        "available_speakers": len(available_speakers),
        "selected_speakers": len(selected_speakers),
        "speaker_coverage_ratio": round(speaker_ratio, 6),
        "selected_evidence_utterances": len(
            {item for row in chosen for item in row["evidence_utterance_ids"]}
        ),
        "source_candidates_by_category": source_by_category,
        "selected_statements_by_category": selected_by_category,
        "source_review_marked_by_category": source_review_by_category,
        "selected_review_marked_by_category": selected_review_by_category,
        "selection_contract_errors": len(contract_errors),
        "model_contract_valid": not contract_errors,
        "exact_source_publication": exact and not contract_errors,
        "referential_integrity": exact and not contract_errors,
        "published_generated_claims": 0,
    }


def render_notes(selection: dict[str, Any]) -> str:
    lines = [
        "# Evidence-Only Local Notes",
        "",
        f"Session: `{selection['session_id']}`  ",
        "Mode: `optional_exact_evidence_selection`  ",
        "Every item below is copied byte-for-byte from reviewed speaker-aware evidence.",
        "",
    ]
    for category in CATEGORIES:
        lines.extend([f"## {HEADINGS[category]}", ""])
        rows = selection["selected"][category]
        if not rows:
            lines.append("- None selected.")
        for row in rows:
            citations = ", ".join(
                f"{item['display_label']} [`{item['utterance_id']}`]"
                for item in row["speaker_evidence"]
            )
            review = " `needs_review`" if row["needs_review"] else ""
            lines.append(f"- {row['text']} (evidence: {citations}){review}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_material(
    session: Path,
    decision_path: Path,
    memory_root: Path,
    policy_file: Path,
    *,
    reviewed_root: Path | None = None,
    allow_unpromoted: bool = False,
    keep_alive: str | None = None,
    model_client: Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    policy = validate_policy(policy_file, allow_unpromoted=allow_unpromoted)
    memory_manifest, paths, memory = material_paths(
        session, decision_path, memory_root, reviewed_root
    )
    catalog = build_catalog(session, memory, policy)
    prompt_input = build_prompt_input(catalog, policy)
    rendered_prompt, prompt_file = render_prompt(prompt_input, policy)
    try:
        deterministic_run, performance = run_selection_model(
            rendered_prompt,
            prompt_input,
            policy,
            keep_alive=keep_alive,
            client=model_client,
        )
    except legacy.LocalSynthesisError as error:
        raise SelectionError(str(error)) from error
    model_ids, model_trace, contract_errors = parse_model_selection(
        deterministic_run["raw_response"], prompt_input
    )
    selected, merge_errors = build_selected(catalog, model_ids, policy)
    contract_errors.extend(merge_errors)
    contract_errors = dedupe(contract_errors)
    if contract_errors:
        selected = {category: [] for category in CATEGORIES}
    metrics = selection_metrics(catalog, selected, contract_errors)
    selection = {
        "schema": SELECTION_SCHEMA,
        "version": 1,
        "status": "optional_exact_evidence_selection" if not contract_errors else "failed_open",
        "session_id": session.name,
        "source_speaker_memory_fingerprint": memory_manifest.get("semantic_fingerprint"),
        "catalog_sha256": catalog["catalog_sha256"],
        **model_trace,
        "model_selected_ids": model_ids,
        "selected": selected,
        "contract_errors": contract_errors,
        "metrics": metrics,
        "constraints": {
            "authoritative": False,
            "model_authored_text": False,
            "transcript_rewritten": False,
            "cloud_or_external_writes": False,
            "cross_session_identity": False,
        },
    }
    model_run = {
        "schema": MODEL_RUN_SCHEMA,
        "version": 1,
        "session_id": session.name,
        "model": deterministic_run["runtime_identity"],
        "request": deterministic_run["request"],
        "prompt": {
            "template": legacy.repository_identity(prompt_file),
            "input_sha256": sha256_bytes(compact_bytes(prompt_input)),
            "rendered_bytes": len(rendered_prompt.encode("utf-8")),
            "rendered_sha256": sha256_bytes(rendered_prompt.encode("utf-8")),
        },
        "raw_response": deterministic_run["raw_response"],
        "raw_response_sha256": deterministic_run["raw_response_sha256"],
        "prompt_eval_count": deterministic_run["prompt_eval_count"],
        "eval_count": deterministic_run["eval_count"],
        "contract_errors": contract_errors,
    }
    outputs = {
        "catalog_json": canonical_bytes(catalog),
        "selection_json": canonical_bytes(selection),
        "model_run_json": canonical_bytes(model_run),
        "notes": (
            render_notes(selection).encode("utf-8")
            if not contract_errors
            else paths["notes"].read_bytes()
        ),
        "transcript": paths["transcript"].read_bytes(),
        "quality_verdict": paths["quality_verdict"].read_bytes(),
    }
    inputs = {
        "policy": legacy.repository_identity(policy_file),
        "prompt": legacy.repository_identity(prompt_file),
        "materializer": legacy.repository_identity(Path(__file__).resolve()),
        "model_runtime_helper": legacy.repository_identity(Path(legacy.__file__).resolve()),
        "reviewed_speaker_memory_manifest": legacy.identity(
            memory_root / "handoff_manifest.json", session
        ),
        "speaker_aware_memory": legacy.identity(paths["memory_json"], session),
        "source_notes": legacy.identity(paths["notes"], session),
        "source_transcript": legacy.identity(paths["transcript"], session),
        "source_quality_verdict": legacy.identity(paths["quality_verdict"], session),
    }
    baseline = dict(inputs)
    for key, row in ((memory_manifest.get("safety") or {}).get("baseline_identities") or {}).items():
        if not legacy.identity_matches(row, session):
            raise SelectionError(f"ordinary_baseline_stale:{key}")
        baseline[f"ordinary_{key}"] = row
    return {
        "policy": policy,
        "memory_manifest": memory_manifest,
        "catalog": catalog,
        "inputs": inputs,
        "baseline": baseline,
        "outputs": outputs,
        "performance": performance,
        "model_identity": deterministic_run["runtime_identity"],
        "summary": metrics,
        "contract_errors": contract_errors,
    }


def semantic_basis(session: Path, material: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "source_speaker_memory_fingerprint": material["memory_manifest"].get(
            "semantic_fingerprint"
        ),
        "model_identity": material["model_identity"],
        "inputs": material["inputs"],
        "outputs": {
            key: {
                "filename": OUTPUT_FILENAMES[key],
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            }
            for key, payload in sorted(material["outputs"].items())
        },
        "summary": material["summary"],
        "contract_errors": material["contract_errors"],
        "scope": "optional_exact_evidence_note_selection",
    }


def output_identity(path: str, payload: bytes) -> dict[str, Any]:
    return {
        "scope": "session",
        "path": path,
        "bytes": len(payload),
        "sha256": sha256_bytes(payload),
    }


def report_payload(manifest: dict[str, Any], performance: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "session_id": manifest.get("session_id"),
        "state": manifest.get("state"),
        "semantic_fingerprint": manifest.get("semantic_fingerprint"),
        "summary": manifest.get("summary") or {},
        "performance": performance or {},
        "reasons": manifest.get("reasons") or [],
        "privacy": {
            "local_loopback_only": True,
            "model_authored_text": False,
            "external_writes": False,
        },
    }


def report_markdown(manifest: dict[str, Any], performance: dict[str, Any] | None = None) -> str:
    summary = manifest.get("summary") or {}
    performance = performance or {}
    lines = [
        "# Evidence-Only Local Note Selection v1",
        "",
        f"- State: `{manifest.get('state')}`",
        f"- Selected/source statements: `{summary.get('selected_statements', 0)}/{summary.get('source_candidates', 0)}`",
        f"- Review compression: `{summary.get('review_compression_ratio', 0)}`",
        f"- High-confidence retention: `{summary.get('baseline_high_confidence_retention_ratio', 0)}`",
        f"- Category/speaker coverage: `{summary.get('category_coverage_ratio', 0)}/{summary.get('speaker_coverage_ratio', 0)}`",
        f"- Wall time: `{performance.get('wall_sec', 0)}s`",
        f"- Peak model RSS: `{performance.get('peak_model_rss_mb', 0)} MB`",
        "",
        "Participant labels and meeting text are intentionally omitted from this report.",
    ]
    lines.extend(f"- Reason: `{reason}`" for reason in manifest.get("reasons") or [])
    return "\n".join(lines) + "\n"


def build_handoff(
    session: Path,
    decision_path: Path,
    memory_root: Path,
    root: Path,
    policy_file: Path,
    *,
    reviewed_root: Path | None = None,
    allow_unpromoted: bool = False,
    keep_alive: str | None = None,
    model_client: Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] | None = None,
    simulate_interruption_before_publish: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    material = build_material(
        session,
        decision_path,
        memory_root,
        policy_file,
        reviewed_root=reviewed_root,
        allow_unpromoted=allow_unpromoted,
        keep_alive=keep_alive,
        model_client=model_client,
    )
    basis = semantic_basis(session, material)
    fingerprint = sha256_bytes(compact_bytes(basis))
    bundle_relative = str(root.resolve().relative_to(session.resolve()) / "bundles" / fingerprint)
    bundle = session / bundle_relative
    files = {
        key: output_identity(f"{bundle_relative}/{OUTPUT_FILENAMES[key]}", payload)
        for key, payload in sorted(material["outputs"].items())
    }
    if not all(legacy.identity_matches(row, session) for row in material["baseline"].values()):
        raise SelectionError("source_output_changed_before_publication")
    contract_valid = not material["contract_errors"]
    promoted = (
        contract_valid
        and material["policy"].get("decision") == "PROMOTE_OPTIONAL_EVIDENCE_SELECTION"
    )
    manifest = {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "state": "ready" if contract_valid else "failed_open",
        "semantic_fingerprint": fingerprint,
        "fingerprint_basis": basis,
        "inputs": material["inputs"],
        "model_identity": material["model_identity"],
        "bundle": {"path": bundle_relative, "files": files},
        "summary": material["summary"],
        "gates": {
            "publish_optional_evidence_selection": promoted,
            "qualification_only": not promoted,
            "speaker_memory_current": True,
            "model_identity_current": True,
            "model_contract_valid": contract_valid,
            "referential_integrity": material["summary"]["referential_integrity"],
            "exact_source_publication": material["summary"]["exact_source_publication"],
            "published_generated_claims": 0,
            "ordinary_outputs_unchanged": True,
        },
        "safety": {
            "baseline_identities": material["baseline"],
            "default_outputs_unchanged": True,
            "fallback": "reviewed_speaker_memory_v1_exact_extractive_notes",
            "transcript_authoritative": False,
            "model_authored_text": False,
            "cloud_or_external_writes": False,
            "cross_session_identity": False,
        },
        "reasons": material["contract_errors"],
        "recommended_next": (
            "less \"$(.venv/bin/python "
            "scripts/materialize-evidence-only-local-note-selection.py "
            f'"sessions/{session.name}" --verify-only --print-path notes)\"'
            if promoted
            else None
        ),
    }
    expected = {OUTPUT_FILENAMES[key]: payload for key, payload in material["outputs"].items()}
    expected["handoff_manifest.json"] = canonical_bytes(manifest)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging.", dir=root))
    try:
        for name, payload in expected.items():
            legacy.write_durable(staging / name, payload)
        legacy.fsync_directory(staging)
        bundles = root / "bundles"
        bundles.mkdir(parents=True, exist_ok=True)
        if bundle.exists():
            if not legacy.immutable_bundle_valid(bundle, expected):
                raise SelectionError("existing_immutable_bundle_invalid")
            shutil.rmtree(staging)
        else:
            os.replace(staging, bundle)
            legacy.fsync_directory(bundles)
        if not all(legacy.identity_matches(row, session) for row in material["baseline"].values()):
            raise SelectionError("source_output_changed_during_publication")
        if simulate_interruption_before_publish:
            raise SimulatedInterruption("simulated interruption before evidence selection publish")
        legacy.atomic_write(root / "handoff_manifest.json", canonical_bytes(manifest))
        legacy.atomic_write(root / "report.json", canonical_bytes(report_payload(manifest, material["performance"])))
        legacy.atomic_write(root / "report.md", report_markdown(manifest, material["performance"]).encode("utf-8"))
        return manifest, material["performance"]
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def unavailable_manifest(session: Path, reason: str) -> dict[str, Any]:
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "state": "unavailable",
        "semantic_fingerprint": None,
        "bundle": None,
        "summary": {},
        "gates": {"publish_optional_evidence_selection": False},
        "safety": {
            "default_outputs_unchanged": True,
            "fallback": "reviewed_speaker_memory_v1_exact_extractive_notes",
        },
        "reasons": [reason],
    }


def publish_unavailable_attempt(root: Path, manifest: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    legacy.atomic_write(root / "last_attempt.json", canonical_bytes(report_payload(manifest)))
    legacy.atomic_write(root / "report.json", canonical_bytes(report_payload(manifest)))
    legacy.atomic_write(root / "report.md", report_markdown(manifest).encode("utf-8"))
    if not (root / "handoff_manifest.json").exists():
        legacy.atomic_write(root / "handoff_manifest.json", canonical_bytes(manifest))


def verify_handoff(
    session: Path,
    decision_path: Path,
    memory_root: Path,
    root: Path,
    policy_file: Path,
    reviewed_root: Path | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        policy = validate_policy(policy_file, allow_unpromoted=False)
        runtime_identity = legacy.inspect_runtime(policy)
        current = read_json(root / "handoff_manifest.json")
    except (SelectionError, legacy.LocalSynthesisError) as error:
        return None, [str(error)]
    if current.get("schema") != HANDOFF_SCHEMA or current.get("state") != "ready":
        return None, [str(item) for item in current.get("reasons") or ["selection_unavailable"]]
    if (current.get("gates") or {}).get("publish_optional_evidence_selection") is not True:
        return None, ["selection_not_promoted"]
    memory_manifest, reasons = speaker_memory.verify_handoff(
        session, decision_path, memory_root, reviewed_root
    )
    if memory_manifest is None:
        return None, ["reviewed_speaker_memory_invalid:" + ",".join(reasons)]
    if current.get("generator") != implementation():
        return None, ["generator_fingerprint_mismatch"]
    if current.get("model_identity") != runtime_identity:
        return None, ["model_identity_mismatch"]
    if current.get("fingerprint_basis", {}).get("source_speaker_memory_fingerprint") != memory_manifest.get(
        "semantic_fingerprint"
    ):
        return None, ["speaker_memory_fingerprint_mismatch"]
    inputs = current.get("inputs") if isinstance(current.get("inputs"), dict) else {}
    if not inputs or not all(legacy.identity_matches(row, session) for row in inputs.values()):
        return None, ["input_fingerprint_mismatch"]
    basis = current.get("fingerprint_basis")
    fingerprint = sha256_bytes(compact_bytes(basis)) if isinstance(basis, dict) else ""
    if current.get("semantic_fingerprint") != fingerprint:
        return None, ["semantic_fingerprint_mismatch"]
    bundle = current.get("bundle") if isinstance(current.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    if set(files) != set(OUTPUT_FILENAMES) or not all(
        legacy.identity_matches(row, session) for row in files.values()
    ):
        return None, ["bundle_file_identity_mismatch"]
    try:
        bundle_path = legacy.resolve_inside(session, str(bundle.get("path") or ""))
    except legacy.LocalSynthesisError:
        return None, ["bundle_path_invalid"]
    if bundle_path.name != fingerprint:
        return None, ["bundle_path_invalid"]
    bundle_manifest = bundle_path / "handoff_manifest.json"
    if not bundle_manifest.is_file() or bundle_manifest.read_bytes() != canonical_bytes(current):
        return None, ["bundle_manifest_mismatch"]
    selection_path = legacy.resolve_identity(files.get("selection_json"), session)
    catalog_path = legacy.resolve_identity(files.get("catalog_json"), session)
    if selection_path is None or catalog_path is None:
        return None, ["selection_artifact_missing"]
    selection = read_json(selection_path)
    catalog = read_json(catalog_path)
    if selection.get("schema") != SELECTION_SCHEMA or catalog.get("schema") != CATALOG_SCHEMA:
        return None, ["selection_schema_mismatch"]
    by_id = {row["statement_id"]: row for row in catalog.get("candidates") or []}
    for category in CATEGORIES:
        for row in (selection.get("selected") or {}).get(category) or []:
            source = by_id.get(row.get("statement_id"))
            if source is None:
                return None, ["selected_unknown_statement_id"]
            if any(
                row.get(key) != source.get(key)
                for key in ("category", "text", "text_sha256", "evidence_utterance_ids", "speaker_evidence")
            ):
                return None, ["selected_source_projection_mismatch"]
    baseline = (current.get("safety") or {}).get("baseline_identities")
    if not isinstance(baseline, dict) or not all(
        legacy.identity_matches(row, session) for row in baseline.values()
    ):
        return None, ["ordinary_output_fingerprint_mismatch"]
    return current, []


def artifact_path(manifest: dict[str, Any], session: Path, key: str) -> Path | None:
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    path = legacy.resolve_identity(files.get(key), session)
    return path if path is not None and path.is_file() else None


def print_summary(manifest: dict[str, Any], root: Path, performance: dict[str, Any] | None = None) -> None:
    summary = manifest.get("summary") or {}
    print("evidence_only_local_note_selection:")
    print(f"  state: {manifest.get('state')}")
    print(f"  selected_statements: {summary.get('selected_statements', 0)}")
    print(f"  review_compression_ratio: {summary.get('review_compression_ratio', 0)}")
    print(f"  high_confidence_retention: {summary.get('baseline_high_confidence_retention_ratio', 0)}")
    if performance:
        print(f"  wall_sec: {performance.get('wall_sec', 0)}")
        print(f"  peak_model_rss_mb: {performance.get('peak_model_rss_mb', 0)}")
    if manifest.get("reasons"):
        print(f"  fallback_reason: {manifest['reasons'][0]}")
    print(f"  manifest: {root / 'handoff_manifest.json'}")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    if not (session / "session.json").is_file():
        print(f"error: session.json not found under {session}", file=sys.stderr)
        return 2
    try:
        decision_path = legacy.resolve_inside(
            session, args.decisions or speaker_memory.naming.DEFAULT_DECISIONS
        )
        memory_root = legacy.resolve_inside(
            session, args.speaker_memory_dir or speaker_memory.DEFAULT_OUTPUT
        )
        reviewed_root = (
            legacy.resolve_inside(session, args.reviewed_speaker_dir)
            if args.reviewed_speaker_dir
            else None
        )
        root = legacy.resolve_inside(session, args.out_dir or DEFAULT_OUTPUT)
        selected_policy = policy_path(args.policy)
    except (SelectionError, legacy.LocalSynthesisError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.verify_only:
        manifest, reasons = verify_handoff(
            session, decision_path, memory_root, root, selected_policy, reviewed_root
        )
        if manifest is None:
            for reason in reasons:
                print(reason)
            return 2
        if args.print_path:
            path = artifact_path(manifest, session, args.print_path)
            if path is None:
                print(f"artifact_missing:{args.print_path}")
                return 2
            print(path)
        else:
            print_summary(manifest, root)
        return 0
    try:
        manifest, performance = build_handoff(
            session,
            decision_path,
            memory_root,
            root,
            selected_policy,
            reviewed_root=reviewed_root,
            allow_unpromoted=args.qualification_run,
            keep_alive=args.keep_alive,
            simulate_interruption_before_publish=args.simulate_interruption_before_publish,
        )
    except SimulatedInterruption as error:
        print(str(error))
        return 3
    except (SelectionError, legacy.LocalSynthesisError) as error:
        manifest = unavailable_manifest(session, str(error))
        publish_unavailable_attempt(root, manifest)
        print_summary(manifest, root)
        return 2
    print_summary(manifest, root, performance)
    return 0 if manifest.get("state") == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
