#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
from argparse import Namespace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/report-capture-continuity-loss-closure-v1.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("capture_continuity_loss_closure", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load closure reporter")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_capture(session: Path, *, controlled: bool, gap_seconds: float) -> None:
    for source in ("mic", "remote"):
        path = session / f"audio/{source}/000001.caf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    write_json(
        session / "session.json",
        {
            "health": {
                "actual_duration_sec": 30.0 if controlled else 600.0,
                "capture_complete": gap_seconds == 0.0,
            }
        },
    )
    session.joinpath("events.jsonl").write_text(
        "".join(
            json.dumps({"type": event_type}) + "\n"
            for event_type in (
                "capture.stopped",
                "manifest.written",
                "capture.recording_lock_released",
            )
        ),
        encoding="utf-8",
    )
    provenance = (
        [
            {
                "attempt_id": 1,
                "terminal_event_count": 1,
                "provenance_complete": True,
            }
        ]
        if controlled
        else []
    )
    write_json(
        session / "derived/audit/capture-continuity/capture_continuity_report.json",
        {
            "schema": "murmurmark.capture_continuity/v1",
            "status": "capture_incomplete" if gap_seconds else "ok",
            "capture_complete": gap_seconds == 0.0,
            "observed_gap_count": 1 if gap_seconds else 0,
            "observed_gap_seconds": gap_seconds,
            "restart_attempt_count": 1 if controlled else 0,
            "restart_provenance_status": "complete" if controlled else "not_applicable",
            "restart_provenance": provenance,
            "restart_latency": {
                "max_software_idle_ms": 2.0 if controlled else None,
                "max_start_api_ms": 40.0 if controlled else None,
                "max_request_to_all_sources_committed_ms": 80.0 if controlled else None,
            },
        },
    )


def main() -> int:
    module = load_module()
    with tempfile.TemporaryDirectory(prefix="murmurmark-continuity-closure-") as temporary:
        root = Path(temporary)
        frozen_session = root / "frozen"
        controlled = root / "controlled"
        soak = root / "soak"
        write_capture(frozen_session, controlled=True, gap_seconds=2.268542)
        frozen_report = (
            frozen_session / "derived/audit/capture-continuity/capture_continuity_report.json"
        )
        frozen_payload = json.loads(frozen_report.read_text(encoding="utf-8"))
        frozen_payload["screen_capture_restart_count"] = 3
        frozen_payload["observed_gap_count"] = 3
        write_json(frozen_report, frozen_payload)
        write_capture(controlled, controlled=True, gap_seconds=0.08)
        write_capture(soak, controlled=False, gap_seconds=0.0)

        frozen_identity = root / "frozen-input.bin"
        frozen_identity.write_bytes(b"frozen")
        digest = hashlib.sha256(frozen_identity.read_bytes()).hexdigest()
        policy_path = root / "policy.json"
        manifest_path = root / "manifest.json"
        write_json(
            policy_path,
            {
                "schema": "murmurmark.capture_continuity_loss_closure_policy/v1",
                "frozen_case": {
                    "session": str(frozen_session),
                    "restart_count": 3,
                    "gap_count": 3,
                    "gap_seconds": 2.268542,
                },
                "thresholds": {
                    "maximum_software_idle_ms": 50.0,
                    "required_no_restart_gap_seconds": 0.0,
                    "minimum_soak_duration_sec": 600.0,
                },
            },
        )
        write_json(
            manifest_path,
            {
                "schema": "murmurmark.capture_continuity_loss_closure_manifest/v1",
                "files": [
                    {
                        "path": str(frozen_identity),
                        "bytes": frozen_identity.stat().st_size,
                        "sha256": digest,
                    }
                ],
            },
        )
        args = Namespace(
            policy=policy_path,
            manifest=manifest_path,
            controlled_session=controlled,
            soak_session=soak,
            verify_frozen_only=False,
        )
        report = module.build_report(args)
        assert report["decision"] == "EVIDENCE_BOUND", report
        assert report["controlled_restart"]["max_software_idle_ms"] == 2.0, report
        assert all(report["controlled_restart"]["gates"].values()), report
        assert all(report["no_restart_soak"]["gates"].values()), report
        pins = module.pinned_sessions(report, json.loads(policy_path.read_text(encoding="utf-8")))
        assert pins["schema"] == "murmurmark.pinned_sessions/v1", pins
        assert pins["sessions"] == ["controlled", "frozen", "soak"], pins

        controlled_report = (
            controlled / "derived/audit/capture-continuity/capture_continuity_report.json"
        )
        payload = json.loads(controlled_report.read_text(encoding="utf-8"))
        payload["capture_complete"] = True
        payload["observed_gap_count"] = 0
        payload["observed_gap_seconds"] = 0.0
        write_json(controlled_report, payload)
        promoted = module.build_report(args)
        assert promoted["decision"] == "PROMOTE_RESTART_HARDENING", promoted
    print("capture continuity loss closure checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
