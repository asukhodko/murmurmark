#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "sessions/_reports/residual-local-recall-v1"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    report_path = REPORT_DIR / "residual_local_recall_corpus_report.json"
    if not report_path.exists():
        print("residual local recall corpus check skipped: no local corpus artifacts")
        return 0
    baseline = read_json(REPORT_DIR / "residual_local_recall_baseline.json")
    report = read_json(report_path)
    decision = read_json(REPORT_DIR / "residual_local_recall_decision.json")
    summary = report["summary"]
    assert baseline["queue"]["item_count"] == 13
    assert abs(float(baseline["queue"]["seconds"]) - 48.073) < 0.001
    assert baseline["excluded_queues"]["audio_review"]["item_count"] == 66
    assert baseline["excluded_queues"]["transcript_order"]["item_count"] == 14
    assert len(report["sessions"]) == len(baseline["sessions"])
    assert sum((row.get("summary") or {}).get("queue_items", 0) for row in report["sessions"]) == 13
    assert report["decision"] in {"PROMOTE_RESIDUAL_LOCAL_RECALL_V1", "DO_NOT_PROMOTE"}
    assert decision["decision"] == report["decision"]
    assert decision["decision_fingerprint"] == report["decision_fingerprint"]
    if report["decision"] == "PROMOTE_RESIDUAL_LOCAL_RECALL_V1":
        assert summary["closed_items"] >= 3
        assert float(summary["closed_seconds"]) >= 9.615
        assert report["gates"]["passed"] is True
        assert len(report["promoted_sessions"]) == len(report["sessions"])
        assert report["synthesis_gates"]["passed"] is True
        assert report["synthesis_gates"]["sessions_checked"] == len(report["sessions"])
        for session in report["sessions"]:
            synthesis = session["synthesis"]
            assert synthesis["passed"] is True
            assert synthesis["missing_evidence_utterance_ids"] == []
    assert sha256(REPORT_DIR / "residual_local_recall_baseline.json") == report["baseline_sha256"]
    print("residual local recall corpus checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
