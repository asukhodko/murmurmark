#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORTER = ROOT / "scripts/report-meeting-lifecycle-corpus.py"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_session(
    root: Path,
    name: str,
    *,
    result: str,
    capture: float,
    after_stop: float,
    profile: str = "baseline",
    current_profile: str | None = None,
    remediation: str | None = None,
    unresolved: int = 0,
    resume: bool = False,
    budget_reason: str | None = None,
) -> Path:
    session = root / name
    transcript = f"derived/transcript/transcript.{profile}.md"
    report: dict[str, Any] = {
        "schema": "murmurmark.meeting_lifecycle_report/v1",
        "result": result,
        "reason": "fixture_reason",
        "transcript": str(session / transcript),
        "selected_profile": profile,
        "resume_available": resume,
        "resume_command": f"murmurmark meeting --resume {session}",
        "elapsed_sec": {
            "capture": capture,
            "total_after_stop": after_stop,
            "actions": {"process": after_stop - 1.0, "enrich": 1.0},
        },
        "unresolved_review": {"count": unresolved, "seconds": float(unresolved), "blockers": []},
        "export": {"status": "blocked_until_review" if unresolved else "exported", "blockers": ["review"] if unresolved else []},
    }
    if budget_reason:
        report["deferred_work"] = {"status": "deferred", "reason": budget_reason}
    write_json(session / "derived/meeting-lifecycle/report.json", report)
    write_json(session / "derived/meeting-lifecycle/next_action.json", {"action": "complete"})
    selected = current_profile or profile
    write_json(
        session / "derived/outcome/outcome.json",
        {
            "selected_profile": selected,
            "outputs": {"transcript": {"path": f"derived/transcript/transcript.{selected}.md"}},
        },
    )
    commands = [{"id": "next", "command": remediation}] if remediation else [
        {"id": "status_session", "command": f"murmurmark status {session}"}
    ]
    write_json(session / "derived/readiness/session_readiness.json", {"next_commands": commands})
    write_json(session / "derived/readiness/review-plan/review_plan.json", {"summary": {"review_action_count": 0}})
    return session


def run(sessions: list[Path], out: Path, *extra: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPORTER), *(str(item) for item in sessions), "--out-dir", str(out), *extra],
        check=check,
        text=True,
        capture_output=True,
    )


def write_spne_reuse(session: Path, *, valid: bool) -> None:
    chunks = [
        {"index": 1, "status": "bit_exact_baseline_reuse"},
        {
            "index": 2,
            "status": (
                "candidate_audio_identity_bounded_splice"
                if valid
                else "bit_exact_baseline_reuse"
            ),
        },
        {"index": 3, "status": "bit_exact_baseline_reuse"},
    ]
    write_json(
        session
        / "derived/preprocess/speaker-preserving-neural-echo-v2-15/direct-asr/chunk_report.json",
        {
            "schema": "murmurmark.speaker_preserving_neural_echo_chunk_asr/v2.15",
            "candidate_audio_is_primary_whisper_input": True,
            "changed_chunks": [2],
            "chunks": chunks,
        },
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-lifecycle-corpus-") as raw:
        root = Path(raw)
        sessions = [
            build_session(root, "ready", result="ready", capture=100.0, after_stop=20.0),
            build_session(
                root,
                "review",
                result="ready_with_review",
                capture=100.0,
                after_stop=30.0,
                unresolved=1,
                remediation=f"murmurmark review next {root / 'review'}",
            ),
            build_session(root, "interrupted", result="interrupted", capture=100.0, after_stop=10.0, resume=True),
        ]
        write_spne_reuse(sessions[0], valid=True)
        out = root / "report"
        run(
            sessions,
            out,
            "--freeze-inputs",
            "--require-frozen-inputs",
            "--min-eligible-sessions",
            "2",
            "--require-passing-gates",
        )
        payload = json.loads((out / "meeting_lifecycle_corpus_v1.json").read_text(encoding="utf-8"))
        assert payload["status"] == "passed", payload
        assert payload["summary"]["dead_end_blockers"] == 0
        assert payload["summary"]["stale_handoffs"] == 0
        assert payload["summary"]["ratio_p90"] < 1.0
        assert payload["summary"]["spne_reuse_applicable"] == 1
        assert payload["gates"]["spne_unchanged_windows_reused"] is True
        fingerprint = payload["inputs"]["fingerprint"]

        repeat = run(
            sessions,
            out,
            "--require-frozen-inputs",
            "--min-eligible-sessions",
            "2",
            "--require-passing-gates",
        )
        assert repeat.returncode == 0
        repeated = json.loads((out / "meeting_lifecycle_corpus_v1.json").read_text(encoding="utf-8"))
        assert repeated["inputs"]["fingerprint"] == fingerprint

        readiness = sessions[1] / "derived/readiness/session_readiness.json"
        write_json(readiness, {"next_commands": [{"command": f"murmurmark status {sessions[1]}"}]})
        failed = run(
            sessions,
            out,
            "--require-frozen-inputs",
            "--min-eligible-sessions",
            "2",
            "--require-passing-gates",
            check=False,
        )
        assert failed.returncode == 2, failed
        payload = json.loads((out / "meeting_lifecycle_corpus_v1.json").read_text(encoding="utf-8"))
        assert payload["gates"]["frozen_inputs_match"] is False
        assert payload["summary"]["dead_end_blockers"] == 1

        stale = build_session(
            root,
            "stale",
            result="ready_with_review",
            capture=100.0,
            after_stop=250.0,
            current_profile="reviewed",
            unresolved=1,
            budget_reason="candidate_budget_exhausted",
        )
        stale_out = root / "stale-report"
        result = run([*sessions, stale], stale_out, "--max-p90-ratio", "10", check=False)
        assert result.returncode == 0
        payload = json.loads((stale_out / "meeting_lifecycle_corpus_v1.json").read_text(encoding="utf-8"))
        assert payload["summary"]["stale_handoffs"] == 1
        assert payload["sessions"][-1]["budget"]["explicit_reason"] == "candidate_budget_exhausted"

        broken = build_session(
            root,
            "spne-broken",
            result="ready",
            capture=100.0,
            after_stop=20.0,
        )
        write_spne_reuse(broken, valid=False)
        broken_out = root / "spne-broken-report"
        result = run([sessions[0], broken], broken_out, check=False)
        assert result.returncode == 0
        payload = json.loads(
            (broken_out / "meeting_lifecycle_corpus_v1.json").read_text(encoding="utf-8")
        )
        assert payload["gates"]["spne_unchanged_windows_reused"] is False
        broken_row = next(row for row in payload["sessions"] if row["session_id"] == "spne-broken")
        assert broken_row["spne_asr_reuse"]["passed"] is False
        assert "decoded_set_does_not_match_changed_set" in broken_row["spne_asr_reuse"]["reason"]

    print("meeting lifecycle corpus checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
