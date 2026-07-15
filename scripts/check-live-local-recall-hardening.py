#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


def load_module():
    path = Path(__file__).with_name("report-live-local-recall-hardening.py")
    spec = importlib.util.spec_from_file_location("murmurmark_live_local_recall_hardening_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_turn() -> dict:
    return {
        "id": "candidate_1",
        "role": "Me",
        "start": 1.0,
        "end": 3.0,
        "text": "local action phrase",
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "remote_audio_guard": {"status": "passed"},
        "selection_features": {
            "remote_free_localization": {"reason": "no_remote_overlap"},
            "speaker_scores": {"target": 0.42},
        },
    }


def main() -> int:
    module = load_module()
    contract = module.causal_candidate_contract(candidate_turn())
    assert contract["passed"] is True, contract

    unsafe = candidate_turn()
    unsafe["used_batch_fields_for_selection"] = True
    unsafe_contract = module.causal_candidate_contract(unsafe)
    assert unsafe_contract["passed"] is False, unsafe_contract

    remote_free, _ = module.classify_disposition(
        "local_missing",
        2.0,
        [],
        [{"overlap_sec": 1.8, "contract": contract}],
    )
    assert remote_free == "remote_free_local_island", remote_free

    partial, _ = module.classify_disposition(
        "local_missing",
        4.0,
        [],
        [{"overlap_sec": 1.0, "contract": contract}],
    )
    assert partial == "partial_safe_tail", partial

    mixed, _ = module.classify_disposition(
        "local_missing",
        3.0,
        [
            {
                "overlap_sec": 2.0,
                "batch_role_label": "mixed",
                "segment_gate_reason": "segment_duplicates_overlapping_remote",
            }
        ],
        [],
    )
    assert mixed == "mixed_double_talk", mixed

    duplicate, _ = module.classify_disposition(
        "local_missing_suspicious_batch_me",
        1.0,
        [],
        [],
    )
    assert duplicate == "duplicate_context", duplicate

    unresolved, _ = module.classify_disposition(
        "local_missing",
        2.0,
        [{"overlap_sec": 1.0, "batch_role_label": "me_dominant"}],
        [],
    )
    assert unresolved == "unresolved", unresolved

    unsupported, _ = module.classify_disposition("local_missing", 2.0, [], [])
    assert unsupported == "unsupported", unsupported

    assert {
        remote_free,
        partial,
        mixed,
        duplicate,
        unresolved,
        unsupported,
    } == module.DISPOSITIONS
    assert module.MATERIAL_BLOCKER_RECOVERY_SECONDS == 10.0
    print("live local-recall hardening checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
