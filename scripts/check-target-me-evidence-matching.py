#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).with_name("audit-target-me.py")


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_target_me_evidence_matching", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Target-Me module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pack_item(item_id: str, start: float, text: str, profile: str = "reviewed_v1") -> dict[str, Any]:
    end = start + 1.0
    utterance = {
        "id": f"utt_{int(start):06d}",
        "role": "me",
        "source_track": "mic",
        "start": start,
        "end": end,
        "text": text,
    }
    return {
        "id": item_id,
        "session_id": "fixture",
        "profile": profile,
        "interval": {"start": start, "end": end},
        "utterance_ids": [utterance["id"]],
        "utterances": [utterance],
    }


def judge_row(module: Any, item: dict[str, Any], source_id: str) -> dict[str, Any]:
    row = dict(item)
    row.pop("id", None)
    row["source_pack_item_id"] = source_id
    row["source_pack_item_fingerprint"] = module.item_fingerprint(item)
    return row


def main() -> int:
    module = load_module()
    current = pack_item("arp_000001", 10.0, "точная локальная реплика")
    stale_same_id = judge_row(
        module,
        pack_item("arp_000001", 20.0, "другой интервал"),
        "arp_000001",
    )
    matching_renumbered = judge_row(module, current, "arp_000099")

    matched = module.evidence_rows_by_item_id(
        [current],
        [stale_same_id, matching_renumbered],
    )
    assert matched == {"arp_000001": matching_renumbered}, matched

    legacy = dict(current)
    legacy.pop("id", None)
    legacy["source_pack_item_id"] = "arp_legacy"
    assert module.evidence_rows_by_item_id([current], [legacy]) == {"arp_000001": legacy}

    stale_profile = judge_row(
        module,
        pack_item("arp_000001", 10.0, "точная локальная реплика", profile="audit_cleanup_v2"),
        "arp_000001",
    )
    assert module.evidence_rows_by_item_id([current], [stale_profile]) == {}

    print("Target-Me evidence matching checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
