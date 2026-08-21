#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts/run-session-pipeline.py"
SYNTHESIZER = REPO_ROOT / "scripts/synthesize-simple-extractive.py"
QUALITY_REPORTER = REPO_ROOT / "scripts/report-session-quality.py"
ORDER_AUDITOR = REPO_ROOT / "scripts/audit-transcript-order.py"


def load_script(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_script("murmurmark_session_pipeline", RUNNER)
SYNTHESIS_MODULE = load_script("murmurmark_synthesis", SYNTHESIZER)
QUALITY_MODULE = load_script("murmurmark_quality", QUALITY_REPORTER)
ORDER_MODULE = load_script("murmurmark_order", ORDER_AUDITOR)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_fixture(session: Path) -> Path:
    transcript = session / "derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v2.md"
    notes = session / "derived/synthesis-simple/extractive/notes.audit_cleanup_v2.md"
    verdict_md = session / "derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v2.md"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    notes.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("# transcript\n", encoding="utf-8")
    notes.write_text("# notes\n", encoding="utf-8")
    verdict_md.write_text("# verdict\n", encoding="utf-8")
    write_json(
        session / "derived/synthesis-simple/extractive/quality_verdict.json",
        {"verdict": "good", "selected_transcript_profile": "audit_cleanup_v2"},
    )
    write_json(
        session / "derived/readiness/session_readiness.json",
        {
            "selected_profile": "audit_cleanup_v2",
            "verdict": "good",
            "use_gate": "exportable",
            "recommended_next": "murmurmark export fixture",
            "outputs": {
                "transcript": {"path": str(transcript.relative_to(session))},
                "notes": {"path": str(notes.relative_to(session))},
                "quality_verdict": {"path": str(verdict_md.relative_to(session))},
            },
        },
    )
    return transcript


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-handoff-") as raw_root:
        session = Path(raw_root) / "sessions/fixture"
        transcript = build_fixture(session)
        report = session / "derived/pipeline-run/pipeline_run_report.json"
        checkpoint = MODULE.build_authoritative_handoff(
            session=session,
            repo_root=Path(raw_root),
            started_at="2026-07-17T00:00:00+00:00",
            elapsed_sec=12.5,
            report_path=report,
            asr_invocation_context={"mode": "forced_batch", "track_workers": 2, "micro_asr_workers": 2},
        )
        assert checkpoint["status"] == "ready", checkpoint
        assert checkpoint["selected_transcript_profile"] == "audit_cleanup_v2"
        assert checkpoint["deferred_enrichment"]["status"] == "pending"
        assert checkpoint["asr_provenance"]["invocation"]["mode"] == "forced_batch"
        assert MODULE.handoff_fingerprint_matches(checkpoint, session)
        assert SYNTHESIS_MODULE.authoritative_handoff_profile(session) == ("audit_cleanup_v2", None)
        assert QUALITY_MODULE.authoritative_handoff_profile(session) == "audit_cleanup_v2"
        assert ORDER_MODULE.resolve_profile(session, "authoritative") == "audit_cleanup_v2"

        review_session = Path(raw_root) / "sessions/review"
        build_fixture(review_session)
        write_json(
            review_session / "derived/synthesis-simple/extractive/quality_verdict.json",
            {"verdict": "risky", "selected_transcript_profile": "audit_cleanup_v2"},
        )
        readiness_path = review_session / "derived/readiness/session_readiness.json"
        review_readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        review_readiness.update(
            {
                "verdict": "risky",
                "use_gate": "review_first",
                "recommended_next": "murmurmark review suggested sessions/review",
            }
        )
        write_json(readiness_path, review_readiness)
        review_checkpoint = MODULE.build_authoritative_handoff(
            session=review_session,
            repo_root=Path(raw_root),
            started_at="2026-07-20T00:00:00+00:00",
            elapsed_sec=15.0,
            report_path=review_session / "derived/pipeline-run/pipeline_run_report.json",
        )
        assert review_checkpoint["status"] == "review_required", review_checkpoint
        assert review_checkpoint["recommended_next"] == "murmurmark enrich sessions/review", review_checkpoint

        MODULE.append_authoritative_handoff_run(
            session=session,
            mode="computed",
            started_at="2026-07-17T00:00:00+00:00",
            elapsed_sec=12.5,
            checkpoint=checkpoint,
            args=SimpleNamespace(
                asr_track_workers=2,
                asr_threads=6,
                micro_asr_workers=2,
                force_asr=True,
                reuse_asr_cache=False,
            ),
        )
        history = [
            json.loads(line)
            for line in MODULE.authoritative_handoff_runs_path(session).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert history[0]["schema"] == MODULE.HANDOFF_RUN_SCHEMA
        assert history[0]["mode"] == "computed"
        assert history[0]["runtime"]["asr_track_workers"] == 2
        reuse_args = SimpleNamespace(
            force_asr=False,
            reuse_asr_cache=False,
            skip_build=True,
            skip_preprocess=False,
            skip_transcription=False,
            skip_audits=False,
            skip_stronger_audio_judge=False,
            skip_cleanup=False,
            allow_partial=False,
            plan_only=False,
        )
        assert MODULE.default_handoff_reuse_allowed(reuse_args) is True
        assert history[0]["runtime"]["asr_threads"] == 6

        before = checkpoint["transcript_fingerprint"]["sha256"]
        updated = MODULE.update_deferred_checkpoint(
            session=session,
            status="completed",
            elapsed_sec=3.0,
            report_path=session / "derived/pipeline-run/deferred_enrichment_report.json",
        )
        assert updated is not None
        assert updated["deferred_enrichment"]["status"] == "completed"
        assert "error" not in updated["deferred_enrichment"]
        assert updated["transcript_fingerprint"]["sha256"] == before
        assert MODULE.handoff_fingerprint_matches(updated, session)

        child_pid_path = session / "derived/pipeline-run/orphan-child.pid"
        child_code = (
            "import signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(30)"
        )
        parent_code = (
            "import pathlib,subprocess,sys,time; "
            f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
            f"pathlib.Path({str(child_pid_path)!r}).write_text(str(p.pid)); "
            "time.sleep(30)"
        )
        orphan_test = MODULE.run_step(
            MODULE.step("orphan_guard", [sys.executable, "-c", parent_code], phase=MODULE.DEFERRED_PHASE),
            REPO_ROOT,
            False,
            progress_interval_sec=0,
            session=session,
            report_path=report,
            pipeline_started_at="2026-07-17T00:00:00+00:00",
            pipeline_phase=MODULE.DEFERRED_PHASE,
            timeout_sec=1,
        )
        assert orphan_test["status"] == "failed", orphan_test
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        child_alive = True
        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                child_alive = False
                break
            time.sleep(0.1)
        if child_alive:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        assert not child_alive, f"orphan child survived timeout: pid={child_pid}"

        readiness_path = session / "derived/readiness/session_readiness.json"
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        readiness["selected_profile"] = "reviewed_v1"
        write_json(readiness_path, readiness)
        assert not MODULE.handoff_fingerprint_matches(updated, session)
        assert MODULE.handoff_artifact_fingerprint_matches(updated, session)
        readiness["selected_profile"] = "audit_cleanup_v2"
        write_json(readiness_path, readiness)
        assert MODULE.handoff_fingerprint_matches(updated, session)

        steps = [
            MODULE.step("critical", ["true"]),
            MODULE.step("optional", ["true"], phase=MODULE.DEFERRED_PHASE),
        ]
        assert [item["name"] for item in MODULE.steps_for_phase(steps, "handoff")] == ["critical"]
        assert [item["name"] for item in MODULE.steps_for_phase(steps, "deferred")] == ["optional"]
        assert [item["name"] for item in MODULE.steps_for_phase(steps, "full")] == ["critical", "optional"]

        pipeline_args = SimpleNamespace(
            model=Path("model.bin"),
            language="ru",
            asr_track_workers=2,
            asr_threads=6,
            micro_asr_workers=2,
            prompt_file=None,
            force_asr=False,
            reuse_asr_cache=False,
            audio_judge_queue=session / "missing-audio-judge.jsonl",
            max_stronger_audio_judge_items=80,
            stronger_audio_judge_exhaustive=False,
            skip_build=False,
            skip_preprocess=False,
            skip_transcription=False,
            skip_audits=False,
            skip_stronger_audio_judge=False,
            skip_cleanup=False,
            max_clips=80,
            max_audio_review_items=80,
            murmurmark_bin=Path("murmurmark"),
        )
        pipeline_steps = MODULE.build_steps(pipeline_args, REPO_ROOT, session)
        transcribe_steps = [item for item in pipeline_steps if item["name"].startswith("transcribe_")]
        assert [item["name"] for item in transcribe_steps] == ["transcribe_current"]
        assert transcribe_steps[0]["command"][-2:] == ["--repair-profile", "shadow_v2"]
        assert "--skip-export" in transcribe_steps[0]["command"]
        export_step = next(item for item in pipeline_steps if item["name"] == "export_asr_audio")
        assert export_step["command"][1] == "export-audio"
        assert export_step["enabled"] is True
        step_names = [item["name"] for item in pipeline_steps]
        assert step_names.index("echo_suppression_policy") < step_names.index("export_asr_audio")
        assert step_names.index("export_asr_audio") < step_names.index(
            "speaker_preserving_neural_echo_v2_prepare"
        )
        assert step_names.index("speaker_preserving_neural_echo_v2_prepare") < step_names.index(
            "transcribe_current"
        )
        assert step_names.index("transcribe_current") < step_names.index(
            "synthesize_neural_echo_baseline"
        )
        assert step_names.index("synthesize_neural_echo_baseline") < step_names.index(
            "speaker_preserving_neural_echo_v2"
        )
        neural_baseline = next(
            item for item in pipeline_steps if item["name"] == "synthesize_neural_echo_baseline"
        )
        neural_selector = next(
            item for item in pipeline_steps if item["name"] == "speaker_preserving_neural_echo_v2"
        )
        assert neural_baseline["command"][-2:] == ["--transcript-profile", "shadow_v2"]
        assert neural_baseline["enabled"] is True
        assert neural_baseline["phase"] == MODULE.DEFERRED_PHASE
        assert neural_selector["phase"] == MODULE.DEFERRED_PHASE
        handoff_names = [item["name"] for item in MODULE.steps_for_phase(pipeline_steps, "handoff")]
        deferred_names = [item["name"] for item in MODULE.steps_for_phase(pipeline_steps, "deferred")]
        assert "transcribe_current" in handoff_names
        assert "speaker_preserving_neural_echo_v2" not in handoff_names
        assert "speaker_preserving_neural_echo_v2" in deferred_names
        assert "audit_local_recall" in handoff_names and "audit_local_recall" in deferred_names
        assert "transcript_integrity" in handoff_names and "transcript_integrity" in deferred_names

        write_json(
            session / "session.json",
            {"health": {"actual_duration_sec": 5897.418}},
        )
        previous_bounded = os.environ.get(MODULE.DEFERRED_BOUNDED_ENV)
        previous_budget = os.environ.get(MODULE.DEFERRED_BUDGET_ENV)
        try:
            os.environ[MODULE.DEFERRED_BOUNDED_ENV] = "1"
            os.environ[MODULE.DEFERRED_BUDGET_ENV] = "1800"
            decision = MODULE.deferred_echo_budget_decision(REPO_ROOT, session)
            assert decision["enabled"] is False, decision
            assert decision["estimated_sec"] > decision["available_sec"], decision
            bounded_steps = MODULE.build_steps(pipeline_args, REPO_ROOT, session)
            bounded_selector = next(
                item
                for item in bounded_steps
                if item["name"] == "speaker_preserving_neural_echo_v2"
            )
            assert bounded_selector["enabled"] is False, bounded_selector
            assert "run `murmurmark enrich SESSION` explicitly" in bounded_selector["skip_reason"]

            write_json(
                session / "session.json",
                {"health": {"actual_duration_sec": 2340.0}},
            )
            write_json(
                session / "derived/preprocess/echo/local_fir_report.json",
                {
                    "acoustic_mode": {
                        "mode": "speaker_playback",
                        "confidence": 0.95,
                        "metrics": {"coupled_window_ratio": 0.5},
                    }
                },
            )
            severe_decision = MODULE.deferred_echo_budget_decision(REPO_ROOT, session)
            assert severe_decision["severe_speaker_playback"] is True, severe_decision
            assert severe_decision["reserve_sec"] == 0.0, severe_decision
            assert severe_decision["enabled"] is True, severe_decision
        finally:
            if previous_bounded is None:
                os.environ.pop(MODULE.DEFERRED_BOUNDED_ENV, None)
            else:
                os.environ[MODULE.DEFERRED_BOUNDED_ENV] = previous_bounded
            if previous_budget is None:
                os.environ.pop(MODULE.DEFERRED_BUDGET_ENV, None)
            else:
                os.environ[MODULE.DEFERRED_BUDGET_ENV] = previous_budget

        synthesis_session = Path(raw_root) / "sessions/synthesis-fixture"
        synthesis_resolved = (
            synthesis_session / "derived/transcript-simple/whisper-cpp/resolved"
        )
        synthesis_paths = SYNTHESIS_MODULE.source_profile_paths(
            synthesis_resolved, "reviewed_v1"
        )
        write_json(
            synthesis_paths["clean_dialogue"],
            {
                "schema": "murmurmark.clean_dialogue/v1",
                "utterances": [
                    {"id": "utt_000001", "role": "me", "start": 0.0, "end": 1.0, "text": "Проверка"}
                ],
            },
        )
        write_json(
            synthesis_paths["quality_report"],
            {
                "utterances": 1,
                "needs_review_count": 0,
                "cross_role_overlap_gt2_count": 0,
                "local_only_island_recall": 0.74,
                "audit_harmful_seconds_after": 0.0,
                "audit_review_seconds": 0.0,
                "audit_cleanup": {"profile": "audit_cleanup_v2"},
                "human_review": {"input_profile": "audit_cleanup_v2"},
            },
        )
        write_json(synthesis_paths["overlaps"], {"overlaps": []})
        write_json(
            synthesis_paths["audit_cleanup_report"].parent
            / "audit_cleanup_report.audit_cleanup_v2.json",
            {
                "gates": {
                    "passed": True,
                    "local_recall_explanation": "local_recall_risk_explained:test",
                }
            },
        )
        quality, utterances, overlaps = SYNTHESIS_MODULE.load_inputs(
            "reviewed_v1", synthesis_paths
        )
        assert quality["local_recall_low_score_explained"] is True
        assert quality["local_recall_explanation"] == "local_recall_risk_explained:test"
        metrics = SYNTHESIS_MODULE.metrics_from_quality(quality, utterances, overlaps)
        verdict, risk_items = SYNTHESIS_MODULE.verdict_from_metrics(
            "reviewed_v1", metrics, [], None
        )
        assert verdict == "good", (verdict, risk_items)
        deferred_names = [item["name"] for item in MODULE.steps_for_phase(pipeline_steps, "deferred")]
        assert deferred_names.index("session_operational_readiness_for_audio_review") < deferred_names.index(
            "review_plan_for_audio_review"
        )
        assert deferred_names.index("review_plan_for_audio_review") < deferred_names.index(
            "rebuild_audio_review_pack_v2"
        )
        assert deferred_names.index("rebuild_audio_review_pack_v2") < deferred_names.index(
            "audit_stronger_audio_judge"
        )
        assert deferred_names[-3:] == [
            "synthesize_authoritative_final",
            "audit_transcript_order_authoritative_final",
            "reconcile_session_state",
        ]
        final_synthesis = next(item for item in pipeline_steps if item["name"] == "synthesize_authoritative_final")
        assert final_synthesis["command"][-2:] == ["--transcript-profile", "authoritative"]
        reconcile = next(item for item in pipeline_steps if item["name"] == "reconcile_session_state")
        assert reconcile["command"][-2:] == ["--reason", "deferred_enrichment"]

        started = time.monotonic()
        timed_out = MODULE.run_step(
            MODULE.step("bounded_deferred", [sys.executable, "-c", "import time; time.sleep(30)"], phase=MODULE.DEFERRED_PHASE),
            REPO_ROOT,
            False,
            progress_interval_sec=0,
            session=session,
            report_path=report,
            pipeline_started_at="2026-07-17T00:00:00+00:00",
            pipeline_phase=MODULE.DEFERRED_PHASE,
            timeout_sec=1,
        )
        assert timed_out["status"] == "failed", timed_out
        assert "exceeded timeout" in timed_out["message"]
        assert time.monotonic() - started < 8
        assert MODULE.handoff_fingerprint_matches(updated, session)

        graceful_marker = session / "derived/pipeline-run/graceful-interrupt.txt"
        graceful_code = "\n".join(
            [
                "import gc",
                "import multiprocessing as mp",
                "import pathlib",
                "import time",
                "semaphore = mp.Semaphore(1)",
                "try:",
                "    time.sleep(30)",
                "except KeyboardInterrupt:",
                f"    pathlib.Path({str(graceful_marker)!r}).write_text('checkpointed\\n')",
                "    del semaphore",
                "    gc.collect()",
            ]
        )
        graceful = MODULE.run_step(
            MODULE.step(
                "graceful_interrupt",
                [sys.executable, "-c", graceful_code],
                phase=MODULE.DEFERRED_PHASE,
            ),
            REPO_ROOT,
            False,
            progress_interval_sec=0,
            session=session,
            report_path=report,
            pipeline_started_at="2026-07-17T00:00:00+00:00",
            pipeline_phase=MODULE.DEFERRED_PHASE,
            timeout_sec=1,
        )
        assert graceful["status"] == "failed", graceful
        assert graceful_marker.read_text(encoding="utf-8") == "checkpointed\n"
        assert "resource_tracker" not in graceful.get("stderr_tail", ""), graceful

        transcript.write_text("mutated\n", encoding="utf-8")
        assert not MODULE.handoff_fingerprint_matches(updated, session)

    print("authoritative handoff checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
