#!/usr/bin/env python3
"""Prove that bounded review-clip workers preserve every generated WAV."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/build-audio-review-pack.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_audio_review_clip_parallelism", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


def make_source(path: Path, frequency: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=16000:duration=8",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
    )


def digest_tree(path: Path) -> dict[str, str]:
    return {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.glob("*.wav"))
    }


def main() -> int:
    items: dict[tuple[Any, ...], dict[str, Any]] = {}
    utterance = {
        "id": "utt_reused",
        "role": "Me",
        "source_track": "mic",
        "start": 10.0,
        "end": 12.0,
        "text": "Отправил в чат.",
        "quality": {"needs_review": True},
    }
    MODULE.add_item(
        items,
        start=10.0,
        end=12.0,
        reasons=["current"],
        utterances=[utterance],
        priority=10,
        source_context={"type": "current"},
    )
    MODULE.add_item(
        items,
        start=10.5,
        end=11.5,
        reasons=["overlapping"],
        utterances=[utterance],
        priority=20,
        source_context={"type": "overlapping"},
    )
    assert len(items) == 1
    MODULE.add_item(
        items,
        start=20.0,
        end=22.0,
        reasons=["stale_reused_id"],
        utterances=[utterance],
        priority=30,
        source_context={"type": "stale"},
    )
    assert len(items) == 2

    review_items: dict[tuple[Any, ...], dict[str, Any]] = {}
    by_id = {"utt_reused": utterance}
    MODULE.add_review_plan_items(
        review_items,
        [
            {
                "status": "todo",
                "decision": "todo",
                "source": "audio_review",
                "source_audit_id": "arp_stale",
                "review_lane": "classify_audio",
                "interval": {"start": 20.0, "end": 22.0},
                "utterance_ids": ["utt_reused"],
            }
        ],
        by_id,
    )
    assert review_items == {}
    MODULE.add_review_plan_items(
        review_items,
        [
            {
                "status": "todo",
                "decision": "todo",
                "source": "transcript_order",
                "source_audit_id": "order_current",
                "review_lane": "check_transcript_order",
                "interval": {"start": 10.0, "end": 12.0},
                "utterance_ids": ["utt_reused"],
            }
        ],
        by_id,
    )
    assert len(review_items) == 1

    with tempfile.TemporaryDirectory(prefix="murmurmark-review-clips-") as raw_root:
        root = Path(raw_root)
        sources = {
            "mic_raw": root / "sources/mic_raw.wav",
            "remote": root / "sources/remote.wav",
            "mic_clean": root / "sources/mic_clean.wav",
            "mic_role_masked": root / "sources/mic_role_masked.wav",
        }
        for index, source in enumerate(sources.values(), start=1):
            make_source(source, 200 + index * 80)

        base_items = [
            {"id": f"arp_{index:06d}", "interval": {"start": float(index), "end": float(index) + 1.25}}
            for index in range(1, 5)
        ]
        serial_items = copy.deepcopy(base_items)
        parallel_items = copy.deepcopy(base_items)
        serial_dir = root / "serial"
        parallel_dir = root / "parallel"

        for item in serial_items:
            MODULE.attach_clips(item, sources, serial_dir, 0.5)
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(MODULE.attach_clips, item, sources, parallel_dir, 0.5)
                for item in parallel_items
            ]
            for future in futures:
                future.result()

        assert digest_tree(serial_dir) == digest_tree(parallel_dir)
        assert [sorted((item.get("clips") or {}).keys()) for item in serial_items] == [
            sorted((item.get("clips") or {}).keys()) for item in parallel_items
        ]
    print("audio review clip parallelism checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
