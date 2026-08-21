#!/usr/bin/env python3
"""Regression checks for speaker-bounded chronology arbitration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
SCRIPT = ROOT / "scripts/report-speaker-bounded-chronology-arbitration-v1.py"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def run(arguments: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(PYTHON), str(SCRIPT), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != expected:
        raise AssertionError(
            f"unexpected exit {completed.returncode}, expected {expected}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def order_row(
    item_id: str,
    label: str,
    start: float,
    duration: float,
    me_id: str,
    remote_id: str,
    *,
    wraps: bool = False,
) -> dict[str, Any]:
    return {
        "schema": "murmurmark.transcript_order_item/v1",
        "item_id": item_id,
        "label": label,
        "confidence": 0.86 if label == "probable_order_risk" else 0.68,
        "reason": "fixture",
        "interval": {"start": start, "end": start + duration, "duration_sec": duration},
        "utterances": {
            "me": {"id": me_id, "start": start - 1, "end": start + duration, "text": f"local text {item_id}"},
            "remote": {"id": remote_id, "start": start, "end": start + duration + 1, "text": f"remote text {item_id}"},
        },
        "features": {
            "me_wraps_remote": wraps,
            "post_remote_tail_sec": 1.0 if wraps else 0.0,
        },
    }


def group_row(
    source: dict[str, Any],
    label: str,
    confidence: float,
    *,
    local: float,
    leak: float,
    clean_rms: float,
    remote_rms: float,
    local_only: float,
    remote_only: float,
    double_talk: float,
) -> dict[str, Any]:
    interval = source["interval"]
    return {
        "schema": "murmurmark.group_overlap_audit/v1",
        "id": f"group_{source['item_id']}",
        "interval": interval,
        "utterances": source["utterances"],
        "features": {
            "speaker_state": {
                "local_only_ratio": local_only,
                "remote_only_ratio": remote_only,
                "double_talk_ratio": double_talk,
            },
            "audio": {"rms_db": {"mic_clean": clean_rms, "remote": remote_rms}},
            "text": {"similarity_max": 0.1},
            "interval": {"near_boundary": True},
        },
        "scores": {"local_evidence": local, "audio_leak": leak},
        "classification": {"label": label, "confidence": confidence},
    }


def judge_row(source: dict[str, Any], label: str, confidence: float) -> dict[str, Any]:
    utterances = source["utterances"]
    return {
        "schema": "murmurmark.faster_whisper_judge/v1",
        "id": f"judge_{source['item_id']}",
        "utterance_ids": [utterances["me"]["id"], utterances["remote"]["id"]],
        "classification": {"label": label, "confidence": confidence},
    }


def fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    session = root / "private-session-name"
    order_dir = session / "derived/audit/order"
    group_dir = session / "derived/audit/group-overlaps"
    judge_dir = session / "derived/audit/audio-review-pack"
    selected = session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.reviewed_v1.json"
    rows = [
        order_row("order_0001", "needs_review", 10.0, 4.0, "me_1", "remote_1"),
        order_row("order_0002", "needs_review", 20.0, 6.0, "me_2", "remote_2"),
        order_row("order_0003", "needs_review", 30.0, 1.0, "me_3", "remote_3"),
        order_row("order_0004", "probable_order_risk", 40.0, 2.0, "me_4", "remote_4", wraps=True),
    ]
    groups = [
        group_row(rows[0], "probable_timing_overlap", 0.75, local=75, leak=0, clean_rms=-40, remote_rms=-80, local_only=1, remote_only=0, double_talk=0),
        group_row(rows[1], "needs_human_review", 0.68, local=75, leak=20, clean_rms=-40, remote_rms=-30, local_only=0.7, remote_only=0, double_talk=0.3),
        group_row(rows[2], "probable_remote_leak", 0.85, local=10, leak=90, clean_rms=-60, remote_rms=-25, local_only=0, remote_only=1, double_talk=0),
        group_row(rows[3], "needs_human_review", 0.68, local=20, leak=20, clean_rms=-60, remote_rms=-35, local_only=0, remote_only=0, double_talk=0),
    ]
    judges = [
        judge_row(rows[1], "confirm_timing_or_doubletalk", 0.92),
        judge_row(rows[2], "uncertain", 0.69),
    ]
    write_json(order_dir / "transcript_order_audit.json", {"schema": "murmurmark.transcript_order_audit/v1"})
    write_jsonl(order_dir / "transcript_order_items.jsonl", rows)
    write_jsonl(group_dir / "group_overlap_audit.jsonl", groups)
    write_json(group_dir / "group_overlap_summary.json", {"schema": "murmurmark.group_overlap_summary/v1"})
    write_jsonl(judge_dir / "faster_whisper_judge.jsonl", judges)
    write_json(judge_dir / "faster_whisper_judge_summary.json", {"schema": "murmurmark.faster_whisper_judge_summary/v1"})
    write_json(selected, {"utterances": [value for row in rows for value in row["utterances"].values()]})
    (session / "derived/preprocess/echo").mkdir(parents=True, exist_ok=True)
    (session / "derived/preprocess/echo/speaker_state.jsonl").write_text("{}\n", encoding="utf-8")
    for path in (
        session / "derived/preprocess/audio/mic_clean_local_fir.wav",
        session / "derived/preprocess/audio/mic_role_masked_for_asr.wav",
        session / "audio/mic/000001.caf",
        session / "audio/remote/000001.caf",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture-audio")

    rebaseline = root / "rebaseline/private/input_manifest.json"
    write_json(
        rebaseline,
        {
            "schema": "murmurmark.post_segmentation_transcript_rebaseline_input/v1",
            "sessions": [
                {
                    "alias": "session_01",
                    "session_name": session.name,
                    "session_path": str(session),
                    "selected_profile": "reviewed_v1",
                    "semantic_fingerprint": "fixture",
                    "artifacts": {
                        "order_audit": artifact(order_dir / "transcript_order_audit.json"),
                        "selected_dialogue": artifact(selected),
                    },
                }
            ],
        },
    )
    policy = root / "policy.json"
    write_json(
        policy,
        {
            "schema": "murmurmark.speaker_bounded_chronology_arbitration_policy/v1",
            "version": 1,
            "rebaseline_manifest": str(rebaseline),
            "thresholds": {
                "expected_queue_items": 4,
                "expected_queue_seconds": 13.0,
                "minimum_closed_item_ratio": 0.5,
                "minimum_closed_seconds_ratio": 0.5,
                "group_timing_confidence": 0.75,
                "judge_confidence": 0.78,
                "minimum_local_evidence": 60,
                "maximum_safe_audio_leak": 70,
                "minimum_clean_rms_db": -52,
                "remote_active_rms_db": -48,
                "minimum_text_identity_similarity": 0.95,
                "maximum_interval_identity_delta_sec": 0.1,
            },
            "safety": {
                "read_only": True,
                "raw_audio_mutation": False,
                "selected_transcript_mutation": False,
                "role_mutation": False,
                "timestamp_mutation": False,
                "primary_asr_mutation": False,
                "cloud_inference": False,
            },
            "privacy": {
                "public_session_ids": False,
                "public_absolute_paths": False,
                "public_speech_text": False,
                "private_provenance_under_sessions": True,
            },
        },
    )
    return policy, root / "out", root / "snapshot.json", selected


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-chronology-arbitration-") as raw:
        root = Path(raw)
        policy, out, snapshot, selected = fixture(root)
        selected_before = sha256(selected)
        base = ["--policy", str(policy), "--out-dir", str(out), "--snapshot", str(snapshot)]
        run(["preflight", *base])
        run(["all", "--refresh", "--write-snapshot", *base])
        report = read_json(out / "speaker_bounded_chronology_arbitration_report.json")
        assert report["decision"] == "PROMOTE_CHRONOLOGY_EVIDENCE_ARBITRATION_V1"
        assert report["summary"]["frozen_items"] == 4
        assert report["summary"]["closed_items"] == 2
        assert report["summary"]["closed_seconds"] == 10.0
        assert report["summary"]["remaining_items"] == 2
        assert report["summary"]["by_outcome"]["benign_turn_boundary"]["items"] == 1
        assert report["summary"]["by_outcome"]["confirmed_double_talk"]["items"] == 1
        assert report["summary"]["by_outcome"]["remote_leak_or_asr_segmentation"]["items"] == 1
        assert report["summary"]["by_outcome"]["true_chronology_risk"]["items"] == 1
        assert sha256(selected) == selected_before
        public = "\n".join(
            (out / name).read_text(encoding="utf-8")
            for name in (
                "speaker_bounded_chronology_arbitration_report.json",
                "speaker_bounded_chronology_arbitration_report.md",
                "arbitration_items.jsonl",
            )
        )
        assert str(root) not in public
        assert session_name_not_public(public)
        assert "local text" not in public and "remote text" not in public
        replay = read_json(out / "replay_report.json")
        assert replay["decision"] == "REPLAY_EXACT"

        group = root / "private-session-name/derived/audit/group-overlaps/group_overlap_audit.jsonl"
        original = group.read_bytes()
        group.write_bytes(original + b"\n")
        run(["evaluate", *base], expected=2)
        stale = read_json(out / "speaker_bounded_chronology_arbitration_report.json")
        assert stale["decision"] == "EVIDENCE_INCOMPLETE"
        assert any("group_items_stale" in issue for issue in stale["issues"])
    print("speaker bounded chronology arbitration checks ok")
    return 0


def session_name_not_public(value: str) -> bool:
    return "private-session-name" not in value


if __name__ == "__main__":
    raise SystemExit(main())
