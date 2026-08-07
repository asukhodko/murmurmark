#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
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

    with tempfile.TemporaryDirectory(prefix="murmurmark-target-me-pack-") as value:
        session = Path(value) / "fixture-session"
        canonical = session / "derived/audit/audio-review-pack"
        canonical.mkdir(parents=True)
        sentinel = canonical / "review_pack_items.jsonl"
        sentinel.write_text('{"id":"canonical"}\n', encoding="utf-8")
        args = SimpleNamespace(
            skip_build_pack=False,
            out_dir_name="target-me",
            max_items=17,
            write_clips=False,
        )
        commands: list[list[str]] = []
        original_run = module.run
        module.run = lambda command: commands.append(command)
        try:
            isolated = module.ensure_audio_pack(session, "reviewed_v1", args)
        finally:
            module.run = original_run
        assert isolated == session / "derived/audit/target-me/audio-review-pack", isolated
        assert commands and commands[0][commands[0].index("--out-dir") + 1] == str(isolated), commands
        assert sentinel.read_text(encoding="utf-8") == '{"id":"canonical"}\n'

        skip_args = SimpleNamespace(**{**vars(args), "skip_build_pack": True})
        assert module.ensure_audio_pack(session, "reviewed_v1", skip_args) == canonical
        isolated.mkdir(parents=True)
        (isolated / "review_pack_items.jsonl").write_text("", encoding="utf-8")
        assert module.ensure_audio_pack(session, "reviewed_v1", skip_args) == isolated

        lane_path = session / "review_lane_pack.classify_audio.json"
        lane_path.write_text(
            json.dumps(
                {
                    "lane": "classify_audio",
                    "items": [
                        {
                            "session_id": session.name,
                            "source_audit_id": "arp_000117",
                            "source_audit_ids": ["arp_000117"],
                            "label": "uncertain",
                            "utterance_ids": ["utt_remote", "utt_me"],
                            "me_utterance_ids": ["utt_me"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        dialogue = [
            {
                "id": "utt_remote",
                "role": "remote",
                "source_track": "remote",
                "start": 9.8,
                "end": 12.0,
                "text": "remote phrase",
            },
            {
                "id": "utt_me",
                "role": "me",
                "source_track": "mic",
                "start": 10.0,
                "end": 11.5,
                "text": "exact local phrase",
            },
        ]
        lane_args = SimpleNamespace(review_lane_pack=[lane_path], padding_sec=0.25)
        original_extract = module.extract_wav

        def fake_extract(_source: Path, output: Path, _start: float, _duration: float) -> bool:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fixture")
            return True

        module.extract_wav = fake_extract
        try:
            lane_items, missing = module.review_lane_pack_items(
                session=session,
                profile="reviewed_v1",
                dialogue_rows=dialogue,
                out_dir=session / "derived/audit/target-me",
                args=lane_args,
            )
        finally:
            module.extract_wav = original_extract
        assert missing == [], missing
        assert len(lane_items) == 1, lane_items
        lane_item = lane_items[0]
        assert lane_item["interval"] == {
            "start": 10.0,
            "end": 11.5,
            "duration_sec": 1.5,
            "start_time": "00:10",
            "end_time": "00:11",
        }, lane_item
        assert lane_item["clip_interval"] == {"start": 9.75, "end": 11.75}, lane_item
        assert lane_item["source_audit_ids"] == ["arp_000117"], lane_item
        assert set(lane_item["clips"]) == {"mic_raw", "remote", "mic_clean", "mic_role_masked"}, lane_item

        compatible_judge = {
            "source_pack_item_id": "arp_000117",
            "session_id": session.name,
            "utterance_ids": ["utt_remote", "utt_me"],
            "utterances": dialogue,
        }
        assert module.evidence_rows_by_item_id([lane_item], [compatible_judge]) == {
            lane_item["id"]: compatible_judge
        }
        stale_judge = {
            **compatible_judge,
            "utterances": [dialogue[0], {**dialogue[1], "text": "stale local phrase"}],
        }
        assert module.evidence_rows_by_item_id([lane_item], [stale_judge]) == {}

    print("Target-Me evidence matching checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
