#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMPARE = load_script("murmurmark_compare_fast", REPO_ROOT / "scripts/compare-live-batch.py")
CLEANUP = load_script("murmurmark_cleanup_recall", REPO_ROOT / "scripts/apply-audit-cleanup.py")


def write_local_recall(session: Path, **summary: Any) -> None:
    path = session / "derived/audit/local-recall/local_recall_audit.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"summary": summary}) + "\n", encoding="utf-8")


def check_local_recall_contract() -> None:
    base = {
        "possible_lost_me_seconds": 0.0,
        "needs_review_seconds": 0.99,
        "blocking_low_local_recall": False,
        "expected_missing_island_count": 10,
        "audited_missing_island_count": 10,
        "recommended_next_step": "local_recall_risk_explained",
        "likely_harmless_seconds": 8.28,
    }
    with tempfile.TemporaryDirectory(prefix="murmurmark-recall-contract-") as raw_root:
        session = Path(raw_root) / "session"
        write_local_recall(session, **base)
        explained, reason = CLEANUP.local_recall_low_score_explained(session)
        assert explained is True and reason == "local_recall_risk_explained:8.28s_harmless:0.99s_review"

        write_local_recall(session, **{**base, "possible_lost_me_seconds": 0.1})
        assert CLEANUP.local_recall_low_score_explained(session) == (False, None)

        write_local_recall(session, **{**base, "needs_review_seconds": 5.0})
        assert CLEANUP.local_recall_low_score_explained(session) == (False, None)

        write_local_recall(session, **{**base, "audited_missing_island_count": 9})
        assert CLEANUP.local_recall_low_score_explained(session) == (False, None)


def check_compare_contract() -> None:
    default = COMPARE.selected_lab_policies(
        SimpleNamespace(only_lab_policy=[], with_labs=False, lab_policy=[])
    )
    assert default == (), default
    policy = COMPARE.RUNTIME_CAUSAL_TARGET_ME_BASELINE_PROFILE_POLICY
    explicit = COMPARE.selected_lab_policies(
        SimpleNamespace(only_lab_policy=[], with_labs=False, lab_policy=[policy])
    )
    assert explicit == (policy,), explicit
    all_labs = COMPARE.selected_lab_policies(
        SimpleNamespace(only_lab_policy=[], with_labs=True, lab_policy=[])
    )
    assert all_labs == COMPARE.MATERIALIZED_TARGET_ME_SHADOW_POLICIES

    turns = [
        {
            "id": "live-1",
            "role": "Me",
            "source": "mic",
            "chunk_index": 1,
            "start": 1.0,
            "end": 2.0,
            "text": "первая фраза",
            "tokens": ["первая", "фраза"],
        },
        {
            "id": "live-2",
            "role": "Colleagues",
            "source": "remote",
            "chunk_index": 1,
            "start": 3.0,
            "end": 4.0,
            "text": "вторая фраза",
            "tokens": ["вторая", "фраза"],
        },
    ]
    batch = [
        {
            "id": "batch-2",
            "role": "Colleagues",
            "start": 8.0,
            "end": 9.0,
            "text": "вторая фраза",
            "tokens": ["вторая", "фраза"],
        },
        {
            "id": "batch-1",
            "role": "Me",
            "start": 12.0,
            "end": 13.0,
            "text": "первая фраза",
            "tokens": ["первая", "фраза"],
        },
    ]
    matched = COMPARE.matched_turn_rows(turns, batch)
    direct = COMPARE.order_mismatch_rows_for_turns(turns, batch)
    cached = COMPARE.order_mismatch_rows_from_matches(matched)
    assert direct == cached and len(cached) == 1, (direct, cached)


def main() -> int:
    check_local_recall_contract()
    check_compare_contract()
    print("fast diagnostics checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
