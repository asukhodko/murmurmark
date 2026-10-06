#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("reconcile-session-state.py")
spec = importlib.util.spec_from_file_location("murmurmark_reconcile_session_state", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-reconcile-interrupt-") as directory:
        session = Path(directory) / "session"
        session.mkdir()
        (session / "session.json").write_text("{}\n", encoding="utf-8")
        for reason, command in (
            ("deferred_enrichment", "enrich"),
            ("explicit_refresh", "report"),
        ):
            with (
                patch.object(sys, "argv", [str(SCRIPT), str(session), "--reason", reason]),
                patch.object(module, "refresh_review_plan", side_effect=KeyboardInterrupt),
                patch.object(module, "run_stage", return_value=0),
            ):
                assert module.main() == 130
            report = json.loads(
                (session / "derived/pipeline-run/state-reconciliation/state_reconciliation_report.json").read_text(
                    encoding="utf-8"
                )
            )
            assert report["status"] == "interrupted_recoverable", report
            assert report["resume_command"] == f"murmurmark {command} {session.resolve()}", report
            assert report["previous_authoritative_fallback"] is None, report
        commands = []

        def collect(stages, name, command, **kwargs):
            commands.append((name, command))
            return 0

        with (
            patch.object(sys, "argv", [str(SCRIPT), str(session)]),
            patch.dict(os.environ, {"MURMURMARK_FINALIZE_ONLY": "1"}),
            patch.object(module, "refresh_review_plan") as refresh,
            patch.object(module, "run_stage", side_effect=collect),
            patch.object(module, "verify_consistency", return_value={"passed": True}),
        ):
            assert module.main() == 0
        refresh.assert_called_once()
        assert refresh.call_args.kwargs == {"rebase": False, "metadata_only": True}
        assert not any(name == "apply_rebased_review" for name, _ in commands)
        for name in ("speaker_selection", "provisional_speaker_transcript"):
            invocation = next(command for stage, command in commands if stage == name)
            expected = "--verify-only" if name == "speaker_selection" else "--cached-only"
            assert expected in invocation and "--refresh-evidence" not in invocation
        audit = next(command for name, command in commands if name == "transcript_order_current_profile")
        assert audit[-2:] == ["--profile", "auto"]
        commands.clear()
        with patch.object(module, "run_stage", side_effect=collect):
            module.refresh_review_plan(session, SCRIPT.parent.parent, [], rebase=False, metadata_only=True)
        workspace = next(command for name, command in commands if "workspace" in name)
        assert "--metadata-only" in workspace and "--rebase-decisions" not in workspace
    print("session state reconciliation interruption ok")


if __name__ == "__main__":
    main()
