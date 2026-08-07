#!/usr/bin/env python3
"""Regression checks for Evidence-Only Local Note Selection v1."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


selection = load_module(
    "evidence_only_local_note_selection_check",
    ROOT / "scripts/materialize-evidence-only-local-note-selection.py",
)
memory_check = load_module(
    "reviewed_speaker_memory_fixture_for_id_selection",
    ROOT / "scripts/check-reviewed-speaker-memory.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ordinary_hashes(session: Path, excluded_roots: list[Path]) -> dict[str, str]:
    return {
        str(path.relative_to(session)): sha256(path)
        for path in sorted(session.rglob("*"))
        if path.is_file() and not any(path.is_relative_to(root) for root in excluded_roots)
    }


def prompt_payload(prompt: str) -> dict[str, Any]:
    marker = "Входные данные:\n\n"
    return json.loads(prompt.split(marker, 1)[1])


def runtime_identity(policy: dict[str, Any]) -> dict[str, Any]:
    model = policy["model"]
    return {
        "runtime": "ollama",
        "runtime_version": policy["runtime"]["qualified_version"],
        "endpoint_scope": "loopback",
        "model": model["name"],
        "model_blob_sha256": model["blob_sha256"],
        "license": model["license"],
        "license_sha256": model["license_sha256"],
        "architecture": model["architecture"],
        "parameter_size": model["parameter_size"],
        "quantization": model["quantization"],
    }


def result_payload(prompt: str, policy: dict[str, Any], *, bad: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    source = prompt_payload(prompt)
    response: dict[str, list[str]] = {category: [] for category in selection.CATEGORIES}
    for category in selection.CATEGORIES:
        limit = source["selection_limits"][category]["max_model_selections"]
        candidates = [
            row
            for row in source["candidates"]
            if row["category"] == category and row["needs_review"]
        ][:limit]
        response[category] = [row["statement_id"] for row in candidates]
    if bad:
        response["summary"] = ["unknown:statement"]
    raw = json.dumps(response, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    deterministic = {
        "runtime_identity": runtime_identity(policy),
        "request": {
            "model": policy["model"]["name"],
            "format": selection.selection_response_schema(source),
            "options": {
                key: value for key, value in policy["generation"].items() if key != "keep_alive"
            },
        },
        "raw_response": raw,
        "raw_response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "prompt_eval_count": 64,
        "eval_count": 32,
    }
    performance = {
        "wall_sec": 0.1,
        "load_sec": 0.0,
        "prompt_eval_sec": 0.05,
        "eval_sec": 0.05,
        "peak_model_rss_mb": 32.0,
        "sampled_model_pids": [],
        "nice_20_applied": True,
    }
    return deterministic, performance


def good_client(prompt: str, policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return result_payload(prompt, policy)


def bad_client(prompt: str, policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return result_payload(prompt, policy, bad=True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=".evidence-selection-check-", dir=ROOT) as temporary:
        root = Path(temporary)
        session, decisions_path = memory_check.create_fixture(root)
        memory_root = session / selection.speaker_memory.DEFAULT_OUTPUT
        selection.speaker_memory.build_handoff(session, decisions_path, memory_root)
        output_root = session / selection.DEFAULT_OUTPUT
        failed_root = session / "derived/meeting-memory/.evidence-selection-failed-open"
        policy = ROOT / selection.DEFAULT_POLICY
        excluded = [
            output_root,
            failed_root,
            memory_root,
            session / selection.speaker_memory.naming.DEFAULT_OUTPUT,
            session / selection.speaker_memory.naming.rich.DEFAULT_OUTPUT_DIR,
            session / "review",
        ]
        before = ordinary_hashes(session, excluded)

        first, performance = selection.build_handoff(
            session,
            decisions_path,
            memory_root,
            output_root,
            policy,
            allow_unpromoted=True,
            model_client=good_client,
        )
        assert first["state"] == "ready"
        assert first["gates"]["publish_optional_evidence_selection"] is True
        assert first["gates"]["qualification_only"] is False
        assert first["gates"]["model_contract_valid"] is True
        assert first["gates"]["published_generated_claims"] == 0
        assert performance["nice_20_applied"] is True
        selection_path = selection.artifact_path(first, session, "selection_json")
        catalog_path = selection.artifact_path(first, session, "catalog_json")
        notes_path = selection.artifact_path(first, session, "notes")
        transcript_path = selection.artifact_path(first, session, "transcript")
        verdict_path = selection.artifact_path(first, session, "quality_verdict")
        assert all(
            path is not None
            for path in (selection_path, catalog_path, notes_path, transcript_path, verdict_path)
        )
        payload = json.loads(selection_path.read_text(encoding="utf-8"))
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        by_id = {row["statement_id"]: row for row in catalog["candidates"]}
        selected = [row for category in selection.CATEGORIES for row in payload["selected"][category]]
        assert selected
        assert payload["metrics"]["baseline_high_confidence_retention_ratio"] == 1.0
        assert payload["metrics"]["exact_source_publication"] is True
        for row in selected:
            source = by_id[row["statement_id"]]
            assert row["text"] == source["text"]
            assert row["text_sha256"] == source["text_sha256"]
            assert row["evidence_utterance_ids"] == source["evidence_utterance_ids"]
            assert row["speaker_evidence"] == source["speaker_evidence"]
        assert ordinary_hashes(session, excluded) == before

        memory_manifest, reasons = selection.speaker_memory.verify_handoff(
            session, decisions_path, memory_root
        )
        assert memory_manifest is not None, reasons
        assert transcript_path.read_bytes() == selection.speaker_memory.artifact_path(
            memory_manifest, session, "transcript"
        ).read_bytes()
        assert verdict_path.read_bytes() == selection.speaker_memory.artifact_path(
            memory_manifest, session, "quality_verdict"
        ).read_bytes()

        pointer = output_root / "handoff_manifest.json"
        pointer_before = pointer.read_bytes()
        replay, _ = selection.build_handoff(
            session,
            decisions_path,
            memory_root,
            output_root,
            policy,
            allow_unpromoted=True,
            model_client=good_client,
        )
        assert replay["semantic_fingerprint"] == first["semantic_fingerprint"]
        assert pointer.read_bytes() == pointer_before

        original_inspect_runtime = selection.legacy.inspect_runtime
        original_validate_policy = selection.validate_policy
        selection.legacy.inspect_runtime = runtime_identity
        selection.validate_policy = lambda path, *, allow_unpromoted: original_validate_policy(
            path, allow_unpromoted=True
        )
        try:
            verified, verify_reasons = selection.verify_handoff(
                session, decisions_path, memory_root, output_root, policy
            )
        finally:
            selection.legacy.inspect_runtime = original_inspect_runtime
            selection.validate_policy = original_validate_policy
        assert verified is not None, verify_reasons
        assert verify_reasons == []

        failed, _ = selection.build_handoff(
            session,
            decisions_path,
            memory_root,
            failed_root,
            policy,
            allow_unpromoted=True,
            model_client=bad_client,
        )
        assert failed["state"] == "failed_open"
        assert failed["gates"]["publish_optional_evidence_selection"] is False
        assert failed["reasons"]
        failed_selection = json.loads(
            selection.artifact_path(failed, session, "selection_json").read_text(encoding="utf-8")
        )
        assert all(not failed_selection["selected"][category] for category in selection.CATEGORIES)
        assert selection.artifact_path(failed, session, "notes").read_bytes() == selection.speaker_memory.artifact_path(
            memory_manifest, session, "notes"
        ).read_bytes()

        prompt = {
            "selection_limits": {
                category: {"max_model_selections": 1} for category in selection.CATEGORIES
            },
            "candidates": [
                {
                    "statement_id": "s1",
                    "category": "actions",
                    "needs_review": True,
                },
                {
                    "statement_id": "s2",
                    "category": "risks",
                    "needs_review": True,
                },
            ],
        }
        schema = selection.selection_response_schema(prompt)
        assert schema["additionalProperties"] is False
        assert schema["properties"]["actions"]["maxItems"] == 1
        assert schema["properties"]["actions"]["items"]["enum"] == ["s1"]
        assert schema["properties"]["open_questions"]["maxItems"] == 1
        assert "enum" not in schema["properties"]["open_questions"]["items"]
        routed = {category: [] for category in selection.CATEGORIES}
        routed["actions"] = ["s2"]
        routed["risks"] = ["s1"]
        selected_ids, trace, errors = selection.parse_model_selection(
            json.dumps(routed, ensure_ascii=False), prompt
        )
        assert trace["model_ranked_ids"] == ["s2", "s1"]
        assert selected_ids["risks"] == ["s2"]
        assert selected_ids["actions"] == ["s1"]
        assert len(trace["category_normalizations"]) == 2
        assert not trace["policy_dropped_ids"]
        assert not errors

        malformed = {category: [] for category in selection.CATEGORIES}
        malformed["actions"] = ["s1", "s1", "unknown:statement", 2]
        _, trace, errors = selection.parse_model_selection(
            json.dumps(malformed, ensure_ascii=False), prompt
        )
        assert any(
            item["reason"] == "duplicate_ignored"
            for item in trace["policy_dropped_ids"]
        )
        assert any(error.startswith("model_unknown_statement_id") for error in errors)
        assert any(error.startswith("model_statement_id_invalid") for error in errors)

        limited = {category: [] for category in selection.CATEGORIES}
        limited["actions"] = ["s1", "s3"]
        selected_ids, trace, errors = selection.parse_model_selection(
            json.dumps(limited, ensure_ascii=False),
            {
                **prompt,
                "candidates": [
                    *prompt["candidates"],
                    {
                        "statement_id": "s3",
                        "category": "actions",
                        "needs_review": True,
                    },
                ],
            },
        )
        assert trace["model_ranked_ids"] == ["s1", "s3"]
        assert selected_ids["actions"] == ["s1"]
        assert trace["policy_dropped_ids"] == [
            {"statement_id": "s3", "reason": "category_limit:actions"}
        ]
        assert not errors

        try:
            selection.build_handoff(
                session,
                decisions_path,
                memory_root,
                output_root,
                policy,
                allow_unpromoted=True,
                model_client=good_client,
                simulate_interruption_before_publish=True,
            )
        except selection.SimulatedInterruption:
            pass
        else:
            raise AssertionError("simulated interruption did not stop publication")
        assert pointer.read_bytes() == pointer_before

    print("evidence-only local note selection checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
