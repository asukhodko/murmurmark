#!/usr/bin/env python3
"""Check current-profile reconciliation of the legacy remote-leak repair plan."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "report-session-quality.py"


def load_module():
    spec = importlib.util.spec_from_file_location("report_session_quality", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def item(item_id: str, utterance_id: str, text: str, seconds: float) -> dict:
    return {
        "schema": "murmurmark.remote_leak_segment_repair_item/v1",
        "id": item_id,
        "profile": "audit_cleanup_v2",
        "interval": {"start": 0.0, "end": seconds, "duration_sec": seconds},
        "utterances": [
            {
                "id": utterance_id,
                "role": "me",
                "source_track": "mic",
                "text": text,
            }
        ],
        "diagnostic": {"protect_local_content": True},
    }


def main() -> None:
    module = load_module()
    with tempfile.TemporaryDirectory(prefix="murmurmark-session-quality-reconcile-") as temporary:
        session = Path(temporary) / "session"
        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        plan_dir = session / "derived/transcript-simple/whisper-cpp/remote-leak-repair"
        rows = [
            item("active", "utt_active", "active text", 1.0),
            item("reviewed", "utt_reviewed", "reviewed text", 2.0),
            item("removed", "utt_removed", "removed text", 3.0),
            item("stale", "utt_stale", "old text", 4.0),
        ]
        plan_dir.mkdir(parents=True, exist_ok=True)
        (plan_dir / "remote_leak_segment_repair_items.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        write_json(
            resolved / "clean_dialogue.reviewed_v1.json",
            {
                "schema": "murmurmark.clean_dialogue/v1",
                "utterances": [
                    {
                        "id": "utt_active",
                        "role": "me",
                        "text": "active text",
                        "quality": {"needs_review": True},
                    },
                    {
                        "id": "utt_reviewed",
                        "role": "me",
                        "text": "reviewed text",
                        "quality": {
                            "needs_review": False,
                            "human_review": {"decisions": ["keep_me"]},
                        },
                    },
                    {
                        "id": "utt_stale",
                        "role": "me",
                        "text": "new text",
                        "quality": {"needs_review": True},
                    },
                ],
            },
        )
        plan = {
            "schema": "murmurmark.remote_leak_segment_repair_plan/v1",
            "summary": {
                "items": 4,
                "seconds": 10.0,
                "protect_local_content_items": 4,
                "protect_local_content_seconds": 10.0,
            },
            "action_plan": [{"next_work": "segment_repair"}],
        }
        metrics = module.remote_leak_segment_plan_metrics(session, "reviewed_v1", plan)
        assert metrics["remote_leak_segment_plan_status"] == "stale_current_profile"
        assert metrics["remote_leak_segment_plan_items"] == 1
        assert metrics["remote_leak_segment_plan_seconds"] == 1.0
        assert metrics["remote_leak_segment_plan_protect_local_content_items"] == 1
        assert metrics["remote_leak_segment_plan_source_items"] == 4
        assert metrics["remote_leak_segment_plan_resolved_items"] == 2
        assert metrics["remote_leak_segment_plan_resolved_seconds"] == 5.0
        assert metrics["remote_leak_segment_plan_stale_items"] == 1
        assert metrics["remote_leak_segment_plan_stale_seconds"] == 4.0
        assert metrics["remote_leak_segment_plan_source_profiles"] == ["audit_cleanup_v2"]
        assert metrics["remote_leak_segment_plan_selected_profile"] == "reviewed_v1"
        assert metrics["remote_leak_segment_plan_next_work"] == "segment_repair"

    print("session quality reconciliation checks passed")


if __name__ == "__main__":
    main()
