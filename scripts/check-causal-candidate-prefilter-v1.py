#!/usr/bin/env python3
"""Unit-style checks for the shared causal candidate prefilter."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def load_prefilter() -> Any:
    path = Path(__file__).with_name("causal_candidate_prefilter.py")
    spec = importlib.util.spec_from_file_location("murmurmark_prefilter_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def row(
    row_id: str,
    *,
    start: float,
    target: float | None,
    text: str,
    chunk_index: int = 1,
    positive_seed_count: int = 4,
    contract_valid: bool = True,
) -> dict[str, Any]:
    checks = {
        "timeline_causal": contract_valid,
        "selection_does_not_use_batch": contract_valid,
        "recording_time_committed_pcm": contract_valid,
        "recording_time_evidence": contract_valid,
        "past_only_enrollment": contract_valid,
        "source_text_contentful": contract_valid,
        "not_already_published": contract_valid,
    }
    scores = {"target": target} if target is not None else {}
    return {
        "id": row_id,
        "chunk_index": chunk_index,
        "start": start,
        "end": start + 1.0,
        "duration_sec": 1.0,
        "text": text,
        "checks": checks,
        "speaker_evidence": {
            "enrollment": {
                "positive_seed_count": positive_seed_count,
                "negative_seed_count": 3,
            },
            "scores": scores,
        },
        "source_evaluation": {"segment_gate_status": "suppressed"},
        "remote_audio_guard": {"remote_db": -20.0},
        "recording_time_evidence": {
            "status": "passed" if contract_valid else "failed"
        },
        "timeline_causal": contract_valid,
        "used_batch_fields_for_selection": not contract_valid,
    }


def main() -> int:
    prefilter = load_prefilter()
    rows = [
        row(
            "positive",
            start=10.0,
            target=0.22,
            text="моя уникальная локальная фраза",
        ),
        row(
            "context",
            start=11.05,
            target=None,
            text="продолжение локальной фразы",
        ),
        row(
            "before-future-positive",
            start=14.0,
            target=None,
            text="эта строка не должна видеть будущего соседа",
        ),
        row(
            "future-positive",
            start=15.05,
            target=0.24,
            text="будущая локальная фраза",
        ),
        row(
            "remote-copy",
            start=20.0,
            target=None,
            text="коллеги обсуждают состояние сервиса сегодня",
        ),
        row(
            "uncertain",
            start=30.0,
            target=0.09,
            text="возможно локальная короткая реплика",
        ),
        row(
            "invalid-contract",
            start=40.0,
            target=0.9,
            text="нельзя использовать некорректный вход",
            contract_valid=False,
        ),
    ]
    remote_text = {
        "positive": "совсем другая речь коллег",
        "context": "совсем другая речь коллег",
        "before-future-positive": "совсем другая речь коллег",
        "future-positive": "совсем другая речь коллег",
        "remote-copy": "коллеги обсуждают состояние сервиса сегодня",
        "uncertain": "другая удаленная речь",
        "invalid-contract": "",
    }
    routed, decisions = prefilter.route_rows(
        rows,
        remote_text_by_id=remote_text,
        session="fixture",
    )
    by_id = {decision["id"]: decision for decision in decisions}
    assert by_id["positive"]["route"] == "expensive_candidate"
    assert by_id["context"]["route"] == "expensive_candidate"
    assert by_id["context"]["routing"]["context_seed_id"] == "positive"
    assert by_id["before-future-positive"]["route"] == "unresolved"
    assert by_id["future-positive"]["route"] == "expensive_candidate"
    assert by_id["remote-copy"]["route"] == "cheap_reject"
    assert by_id["uncertain"]["route"] == "unresolved"
    assert by_id["invalid-contract"]["route"] == "unresolved"
    assert {item["id"] for item in routed} == {
        "positive",
        "context",
        "future-positive",
    }
    assert all(decision["used_batch_fields_for_selection"] is False for decision in decisions)
    assert all("evaluation_reference" not in decision for decision in decisions)
    first = prefilter.decision_set_fingerprint(decisions)
    _, repeated = prefilter.route_rows(
        rows,
        remote_text_by_id=remote_text,
        session="fixture",
    )
    assert first == prefilter.decision_set_fingerprint(repeated)
    print("causal candidate prefilter v1 check: passed")
    print(f"decision_fingerprint: {first}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
