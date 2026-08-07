#!/usr/bin/env python3
"""Regression checks for Evidence-Guarded Local Synthesis v1."""

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


local = load_module(
    "evidence_guarded_local_synthesis_check",
    ROOT / "scripts/materialize-evidence-guarded-local-synthesis.py",
)
memory_check = load_module(
    "reviewed_speaker_memory_fixture_for_local_synthesis",
    ROOT / "scripts/check-reviewed-speaker-memory.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ordinary_hashes(session: Path) -> dict[str, str]:
    excluded = (
        session / local.DEFAULT_OUTPUT,
        session / local.speaker_memory.DEFAULT_OUTPUT,
        session / local.speaker_memory.naming.DEFAULT_OUTPUT,
        session / local.speaker_memory.naming.rich.DEFAULT_OUTPUT_DIR,
        session / "review",
    )
    return {
        str(path.relative_to(session)): sha256(path)
        for path in sorted(session.rglob("*"))
        if path.is_file() and not any(path.is_relative_to(root) for root in excluded)
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


def mock_client(prompt: str, policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source = prompt_payload(prompt)
    response: dict[str, list[dict[str, Any]]] = {category: [] for category in local.CATEGORIES}
    for category in local.CATEGORIES:
        candidates = [row for row in source["statements"] if row["category"] == category]
        if not candidates:
            continue
        row = candidates[0]
        response[category].append(
            {
                "text": row["text"],
                "source_statement_ids": [row["statement_id"]],
                "evidence_utterance_ids": row["evidence_utterance_ids"],
            }
        )
    response["summary"].append(
        {
            "text": "Несуществующий участник решил 2099 не делать задачу",
            "source_statement_ids": ["unknown:statement"],
            "evidence_utterance_ids": ["utt_unknown"],
        }
    )
    raw = json.dumps(response, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    deterministic = {
        "runtime_identity": runtime_identity(policy),
        "request": {
            "model": policy["model"]["name"],
            "format": "json",
            "options": {
                key: value for key, value in policy["generation"].items() if key != "keep_alive"
            },
        },
        "raw_response": raw,
        "raw_response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "prompt_eval_count": 128,
        "eval_count": 64,
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=".local-synthesis-check-", dir=ROOT) as temporary:
        root = Path(temporary)
        session, decisions_path = memory_check.create_fixture(root)
        memory_root = session / local.speaker_memory.DEFAULT_OUTPUT
        local.speaker_memory.build_handoff(session, decisions_path, memory_root)
        output_root = session / local.DEFAULT_OUTPUT
        policy = ROOT / local.DEFAULT_POLICY
        before = ordinary_hashes(session)

        first, performance = local.build_handoff(
            session,
            decisions_path,
            memory_root,
            output_root,
            policy,
            allow_unpromoted=True,
            model_client=mock_client,
        )
        assert first["state"] == "ready"
        assert first["gates"]["publish_optional_local_synthesis"] is False
        assert first["gates"]["qualification_only"] is True
        assert first["recommended_next"] is None
        assert first["gates"]["published_unsupported_claims"] == 0
        assert performance["nice_20_applied"] is True
        synthesis_path = local.artifact_path(first, session, "synthesis_json")
        notes_path = local.artifact_path(first, session, "notes")
        transcript_path = local.artifact_path(first, session, "transcript")
        verdict_path = local.artifact_path(first, session, "quality_verdict")
        assert all(path is not None for path in (synthesis_path, notes_path, transcript_path, verdict_path))
        synthesis = json.loads(synthesis_path.read_text(encoding="utf-8"))
        assert synthesis["schema"] == local.SYNTHESIS_SCHEMA
        assert synthesis["accepted"]
        assert all(row["evidence_verified"] for row in synthesis["accepted"])
        assert any("unknown_source_statement_ids" in row["reasons"] for row in synthesis["rejected"])
        assert "Несуществующий участник" not in notes_path.read_text(encoding="utf-8")
        assert ordinary_hashes(session) == before

        memory_manifest, reasons = local.speaker_memory.verify_handoff(
            session, decisions_path, memory_root
        )
        assert memory_manifest is not None, reasons
        assert transcript_path.read_bytes() == local.speaker_memory.artifact_path(
            memory_manifest, session, "transcript"
        ).read_bytes()
        assert verdict_path.read_bytes() == local.speaker_memory.artifact_path(
            memory_manifest, session, "quality_verdict"
        ).read_bytes()

        pointer = output_root / "handoff_manifest.json"
        pointer_before = pointer.read_bytes()
        replay, _ = local.build_handoff(
            session,
            decisions_path,
            memory_root,
            output_root,
            policy,
            allow_unpromoted=True,
            model_client=mock_client,
        )
        assert replay["semantic_fingerprint"] == first["semantic_fingerprint"]
        assert pointer.read_bytes() == pointer_before

        verified, verify_reasons = local.verify_handoff(
            session, decisions_path, memory_root, output_root, policy
        )
        assert verified is None
        assert verify_reasons == ["policy_not_promoted"]

        try:
            local.build_handoff(
                session,
                decisions_path,
                memory_root,
                output_root,
                policy,
                allow_unpromoted=True,
                model_client=mock_client,
                simulate_interruption_before_publish=True,
            )
        except local.SimulatedInterruption:
            pass
        else:
            raise AssertionError("simulated interruption did not stop publication")
        assert pointer.read_bytes() == pointer_before

        prompt_input = {
            "allowed_display_labels": ["Me", "Участник A"],
            "statements": [
                {
                    "statement_id": "s1",
                    "category": "actions",
                    "text": "Нужно проверить логи",
                    "evidence_utterance_ids": ["u1"],
                    "needs_review": True,
                    "evidence_speakers": ["Me"],
                }
            ],
            "evidence_utterances": [
                {"utterance_id": "u1", "speaker": "Me", "text": "Нужно проверить логи"}
            ],
        }
        bad = {category: [] for category in local.CATEGORIES}
        bad["actions"] = [
            {
                "text": "Участник A обязан проверить 42 лога и не закрывать задачу",
                "source_statement_ids": ["s1"],
                "evidence_utterance_ids": ["u1"],
            }
        ]
        accepted, rejected, metrics = local.verify_proposals(
            bad, prompt_input, json.loads(policy.read_text(encoding="utf-8"))
        )
        assert not accepted
        assert metrics["published_unsupported_claims"] == 0
        reasons = set(rejected[0]["reasons"])
        assert {"unsupported_number", "unsupported_negation", "unsupported_commitment"}.issubset(reasons)
        assert "speaker_label_without_selected_evidence" in reasons

    print("evidence-guarded local synthesis checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
