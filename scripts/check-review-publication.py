#!/usr/bin/env python3
"""No-inference publication after review and checkpointed cancellation regressions."""

from __future__ import annotations

from copy import deepcopy
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

from murmurmark_deadline import DEADLINE_ENV, run_bounded


ROOT = Path(__file__).resolve().parents[1]
REAL_SUBPROCESS_RUN = subprocess.run


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load("materialize-provisional-speaker-transcript")
B = load("apply-review-decisions-batch")


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(M.canonical_json_bytes(payload))


def manifest(directory, schema):
    write(directory / "artifact_manifest.json", {"schema": schema, "artifacts": {
        path.name: M.sha256_file(path) for path in directory.iterdir() if path.name != "artifact_manifest.json"
    }})


def compatible_fixture(root):
    session = root / "session"
    write(session / "session.json", {})
    write(session / "derived/synthesis-simple/extractive/quality_verdict.json",
          {"selected_transcript_profile": "reviewed_v1"})
    dialogue = session / "derived/clean.reviewed.json"
    aggregate = session / "derived/transcript.reviewed.md"
    rows = [
        {"id": "me", "role": "me", "text": "Local", "start": 0, "end": 1, "quality": {}},
        {"id": "remote", "role": "remote", "text": "First. Second.", "start": 1, "end": 5,
         "quality": {"needs_review": False, "overlap": False}},
    ]
    write(dialogue, {"utterances": rows})
    aggregate.write_text("Local\nFirst. Second.\n")
    write(session / "derived/readiness/session_readiness.json", {
        "schema": M.READINESS_SCHEMA, "selected_profile": "reviewed_v1", "outputs": {
            "transcript": {"path": str(aggregate.relative_to(session))},
            "clean_dialogue": {"path": str(dialogue.relative_to(session))},
        },
    })
    audio = session / "audio/remote/000001.caf"
    write(audio, {"fixture": "unchanged audio"})
    raw_json = session / "derived/raw.remote.json"
    write(raw_json, {"words": ["First", "Second"]})
    model = root / "resemblyzer/pretrained.pt"
    write(model, {"fixture": "weights"})
    model_identity = {"exists": True, "bytes": model.stat().st_size, "sha256": M.sha256_file(model)}
    evidence = session / M.STRICT_EVIDENCE_ROOT / "frozen"
    v1, v2, v3 = [evidence / name for name in (
        "remote-speaker-evidence-v1", "remote-speaker-diarization-v2", "remote-speaker-coverage-v3")]
    source = {"dialogue": M.identity(dialogue, session), "remote_audio": M.identity(audio, session),
              "raw_remote_before": M.identity(audio, session), "raw_remote_after": M.identity(audio, session),
              "speaker_roster": {"path": str(M.DEFAULT_ROSTER), "exists": False}, "profile": "old_v1"}
    write(v1 / "report.json", {"source": source, "model": {"model": model_identity}, "implementation": {
        "fingerprint": {"sha256": M.sha256_file(M.V1_IMPLEMENTATION)}}, "schema": M.V1_REPORT_SCHEMA})
    write(v1 / "utterance_attribution.jsonl", {})
    manifest(v1, M.V1_MANIFEST_SCHEMA)
    source2 = {"dialogue": source["dialogue"], "remote_audio": source["remote_audio"],
               "raw_remote_json": M.identity(raw_json, session), "profile": "old_v1",
               "v1_report": M.identity(v1 / "report.json", session),
               "v1_attribution": M.identity(v1 / "utterance_attribution.jsonl", session)}
    write(v2 / "report.json", {"schema": "murmurmark.remote_speaker_diarization_report/v2",
        "decision": "PUBLISH_EVIDENCE", "gates": {"inputs_current": True}, "source": source2,
        "model": {"model": model_identity}, "implementation": {"script": {
            "sha256": M.sha256_file(ROOT / "scripts/audit-remote-speaker-diarization.py")}}})
    v2_files = {"report": "report.json", "frames": "frame_attribution.jsonl", "words": "word_attribution.jsonl",
                "utterances": "utterance_attribution.jsonl", "speaker_map": "speaker_map.json",
                "rich": "transcript.rich.shadow.json", "manifest": "artifact_manifest.json"}
    for key, name in v2_files.items():
        if key not in {"report", "manifest"}:
            write(v2 / name, {})
    manifest(v2, "murmurmark.remote_speaker_diarization_artifact_manifest/v2")
    source3 = {**source2, "v2_artifacts": {key: M.identity(v2 / name, session) for key, name in v2_files.items()}}
    write(v3 / "report.json", {"schema": "murmurmark.remote_speaker_coverage_report/v3",
        "decision": "PUBLISH_EVIDENCE", "gates": {"selected_text_unchanged": True}, "source": source3,
        "implementation": {"script": {"sha256": M.sha256_file(ROOT / "scripts/audit-remote-speaker-coverage-v3.py")}},
        "summary": {"remote_speech_sec": 4, "attributed_speech_sec": 4,
                    "attributable_remote_speech_ratio": 1, "attributed_words": 2}})
    frozen = deepcopy(rows)
    frozen[1]["speaker_turns"] = [
        {"text": "First. ", "speaker_id": "remote_speaker_01", "start": 1, "end": 3},
        {"text": "Second.", "speaker_id": "remote_speaker_02", "start": 3, "end": 5},
    ]
    write(v3 / "transcript.rich.shadow.json", {"schema": "murmurmark.remote_speaker_rich_transcript/v3",
                                             "source": source3, "utterances": frozen})
    write(v3 / "word_attribution.jsonl", {})
    manifest(v3, "murmurmark.remote_speaker_coverage_artifact_manifest/v3")
    rows[0]["quality"] = {"human_review": "keep_me", "needs_review": False}
    rows[1]["quality"]["transcript_order_review"] = "keep_me"
    write(dialogue, {"utterances": rows})
    strict_path = session / M.STRICT_SELECTION
    write(strict_path, {"schema": M.STRICT_SELECTION_SCHEMA, "state": "fallback",
                       "selected_profile": "old_v1", "fallback_reason": "refreshed_v3_invalid"})
    return session, dialogue, aggregate, rows, model, v3


def check_publication(root):
    session, dialogue, aggregate, rows, model, v3 = compatible_fixture(root)
    out = session / M.DEFAULT_OUT_DIR
    args = SimpleNamespace(session=session, out_dir=M.DEFAULT_OUT_DIR, cached_only=True)
    with (patch.object(importlib.util, "find_spec", return_value=SimpleNamespace(origin=str(model.with_name("__init__.py")))),
          patch.object(subprocess, "run", side_effect=AssertionError("inference must not run"))):
        payload = M.materialize(args)
        assert payload["state"] == "provisional", payload
        assert payload["summary"]["speaker_clusters"] == 2
        rich = M.read_json(session / payload["rich_transcript"]["path"])
        assert rich["utterances"][1]["speaker_turns"][1]["speaker_id"] == "remote_speaker_02"
        assert rich["utterances"][0]["quality"] == rows[0]["quality"]
        markdown = (session / payload["selected_transcript"]["path"]).read_text()
        assert "00:01 remote_speaker_01" in markdown and "00:03 remote_speaker_02" in markdown
        assert "provisional" in markdown
        assert "preserved from verified compatible v3 evidence" in markdown
        assert payload["evidence_reuse"]["acoustic_evidence_status"] == "verified_compatible"
        first = (out / "selection.json").read_bytes()
        assert M.materialize(args) == payload
        assert (out / "selection.json").read_bytes() == first
        changed_review = deepcopy(rows)
        changed_review[0]["quality"]["human_review"] = "another keep annotation"
        write(dialogue, {"utterances": changed_review})
        original_write = M.atomic_write

        def interrupt_publication(path, data):
            if path.resolve() == (out / "selection.json").resolve():
                raise KeyboardInterrupt
            original_write(path, data)

        with patch.object(M, "atomic_write", side_effect=interrupt_publication):
            try:
                M.materialize(args)
                raise AssertionError("publication was not interrupted")
            except KeyboardInterrupt:
                pass
        assert (out / "selection.json").read_bytes() == first
        assert M.read_json(session / payload["rich_transcript"]["path"]) == rich
        write(dialogue, {"utterances": rows})
        binary = ROOT / ".build/debug/murmurmark"
        if binary.is_file():
            runner = root / "verify-only-python"
            log = root / "reader-commands.jsonl"
            runner.write_text(f"#!{sys.executable}\nimport json, sys\n"
                              f"with open({str(log)!r}, 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                              "if '--verify-only' not in sys.argv: raise SystemExit(55)\n"
                              "raise SystemExit(2 if sys.argv[1].endswith('/select-speaker-resolved-transcript.py') else 0)\n")
            runner.chmod(0o755)
            before = {str(p): (p.stat().st_size, p.stat().st_mtime_ns)
                      for p in session.rglob("*") if p.is_file()}
            # Use the real CLI; only the model-verification subprocess is replaced.
            with patch.object(subprocess, "run", wraps=REAL_SUBPROCESS_RUN):
                for command in (["transcript", str(session), "--path-only"], ["status", str(session)]):
                    result = subprocess.run([str(binary), *command], cwd=ROOT, capture_output=True, text=True,
                                            env={**os.environ, "MURMURMARK_PYTHON": str(runner)})
                    assert result.returncode == 0, result.stderr
            invocations = [json.loads(line) for line in log.read_text().splitlines()]
            assert invocations and all("--verify-only" in command for command in invocations)
            assert before == {str(p): (p.stat().st_size, p.stat().st_mtime_ns)
                              for p in session.rglob("*") if p.is_file()}
        strict = M.read_json(session / M.STRICT_SELECTION)
        strict["fallback_reason"] = "cached_coverage_invalid"
        write(session / M.STRICT_SELECTION, strict)
        assert not M.verify_existing(session, out, "reviewed_v1", aggregate, dialogue)[1]
        for module, reason in (
            (M.publication, "selection_publication_implementation_stale"),
            (M.micro_asr_evidence, "selection_micro_evidence_implementation_stale"),
            (M.acoustic_timing_evidence, "selection_acoustic_timing_implementation_stale"),
            (M.transcript_interval_evidence, "selection_interval_evidence_implementation_stale"),
        ):
            with patch.object(module, "__file__", str(root / "missing_helper.py")):
                assert reason in M.verify_existing(session, out, "reviewed_v1", aggregate, dialogue)[1]
        with patch.object(M.acoustic_timing_evidence, "fingerprint", return_value={"exists": False}):
            assert "selection_timing_audio_stale" in M.verify_existing(session, out, "reviewed_v1", aggregate, dialogue)[1]
        extra_audio = session / "audio/remote/000002.caf"
        extra_audio.write_bytes(b"additional source")
        assert "selection_timing_layout_stale" in M.verify_existing(session, out, "reviewed_v1", aggregate, dialogue)[1]
        extra_audio.unlink()
        for field, value in (("text", "Changed"), ("start", 1.01), ("role", "me"), ("source_start", 0.8)):
            changed = deepcopy(rows)
            changed[1][field] = value
            assert M.compatible_v3_evidence(session, v3, changed) is None, field
        for field, value in (("words", [{"word": "First", "start": 1.5, "end": 2.5}]),
                             ("new_acoustic_input", "different"), ("corrections", ["changed"])):
            changed = deepcopy(rows)
            changed[1][field] = value
            assert M.compatible_v3_evidence(session, v3, changed) is None, field
        reviewed = deepcopy(rows)
        reviewed[1]["overlap_ids"] = ["new_audit_link"]
        reviewed[1]["quality"]["audit_cleanup"] = {"reason": "review refreshed"}
        assert M.compatible_v3_evidence(session, v3, reviewed) is not None
        write(dialogue, {"utterances": reviewed})
        reviewed_selection = M.materialize(args)
        reviewed_rich = M.read_json(session / reviewed_selection["rich_transcript"]["path"])
        assert reviewed_rich["utterances"][1]["speaker_turns"] == rich["utterances"][1]["speaker_turns"]
        assert reviewed_rich["utterances"][1]["quality"] == reviewed[1]["quality"]
        assert reviewed_selection["evidence_reuse"]["strict_publication_promoted"] is False
        write(dialogue, {"utterances": rows})
        for field in ("needs_review", "overlap"):
            changed = deepcopy(rows)
            changed[1]["quality"][field] = True
            assert M.compatible_v3_evidence(session, v3, changed) is None, field
        assert M.compatible_v3_evidence(session, v3, rows[:-1]) is None
        assert M.compatible_v3_evidence(session, v3, rows[1:]) is not None
        write(dialogue, {"utterances": rows[1:]})
        after_drop = M.materialize(args)
        assert after_drop["summary"]["speaker_clusters"] == 2
        assert after_drop["summary"]["attributed_remote_speech_ratio"] == 1
        after_rich = M.read_json(session / after_drop["rich_transcript"]["path"])
        assert len(after_rich["utterances"]) == 1
        assert after_rich["utterances"][0]["text"] == rows[1]["text"]
        write(dialogue, {"utterances": rows})
        frozen_review = deepcopy(rows)
        frozen_review[1]["quality"]["needs_review"] = True
        assert M.review_evidence_compatible(frozen_review, rows)
        assert not M.review_evidence_compatible(rows, frozen_review)
        assert not M.review_evidence_compatible(rows, [rows[0], rows[0], rows[1]])
        model.write_text("changed weights")
        assert M.verify_existing(session, out, "reviewed_v1", aggregate, dialogue)[1]
        write(model, {"fixture": "weights"})
        write(session / M.DEFAULT_ROSTER, {"participant_count": 2})
        assert M.compatible_v3_evidence(session, v3, rows) is None
        (session / M.DEFAULT_ROSTER).unlink()
        (session / "audio/remote/000001.caf").write_text("changed audio")
        assert M.compatible_v3_evidence(session, v3, rows) is None


def check_v1_reuse(root):
    session, dialogue, aggregate, rows, model, v3 = compatible_fixture(root)
    v1 = v3.parent / "remote-speaker-evidence-v1"
    report = M.read_json(v1 / "report.json")
    report.update(decision="DO_NOT_PUBLISH", parameters={}, clusters=[{
        "cluster": 1, "unit_count": 12, "speech_sec": 70, "span_sec": 80,
        "cohesion_median": 0.95, "first_start": 1,
    }])
    write(v1 / "report.json", report)
    write(v1 / "transcript.rich.shadow.json", {"schema": "murmurmark.transcript_rich_shadow/v1", "utterances": rows})
    (v1 / "utterance_attribution.jsonl").write_text(json.dumps({"utterance_id": "remote", "cluster": 1}) + "\n")
    manifest(v1, M.V1_MANIFEST_SCHEMA)
    args = SimpleNamespace(session=session, out_dir=M.DEFAULT_OUT_DIR, cached_only=True)
    with (patch.object(importlib.util, "find_spec", return_value=SimpleNamespace(origin=str(model.with_name("__init__.py")))),
          patch.object(subprocess, "run", side_effect=AssertionError("no acoustic recomputation"))):
        selection = M.materialize(args)
        assert selection["evidence_reuse"]["kind"] == "v1", selection
        assert selection["summary"]["attributed_remote_utterances"] == 1
        assert not M.verify_existing(session, session / M.DEFAULT_OUT_DIR, "reviewed_v1", aggregate, dialogue)[1]


def check_deadlines_and_review_order(root):
    commands = []
    args = SimpleNamespace(session_quality_out_dir=root / "quality", operational_readiness_out_dir=root / "ops",
                           review_plan_out_dir=root / "plan", corpus_evaluation=root / "corpus",
                           audio_judge=root / "judge", audio_judge_queue=root / "queue")

    def collect(command):
        commands.append(command)
        return {"returncode": 0}

    B.refresh_reports(args, ROOT, [root / "session"], collect)
    assert "report-session-quality.py" in commands[0][1]
    assert "materialize-provisional-speaker-transcript.py" in commands[-1][1]
    assert "--cached-only" in commands[-1]
    assert not any("--refresh-evidence" in command for command in commands)
    with patch.dict(os.environ, {DEADLINE_ENV: str(time.time() - 1)}):
        result = run_bounded([sys.executable, "-c", "raise AssertionError('must not start')"])
        assert result["returncode"] == 75 and not result["started"]
    started = time.monotonic()
    with patch.dict(os.environ, {DEADLINE_ENV: str(time.time() + 0.15)}):
        result = run_bounded([sys.executable, "-c", "import time; time.sleep(30)"])
        assert result["status"] == "budget_exhausted" and time.monotonic() - started < 4
    for code, status in ((75, "budget_exhausted"), (130, "interrupted"), (2, "completed")):
        result = run_bounded([sys.executable, "-c", f"raise SystemExit({code})"])
        assert result["returncode"] == code and result["status"] == status
    report_path = root / "batch.json"
    batch_args = SimpleNamespace(out=report_path, decisions=root / "decisions", review_template=root / "template")

    def interrupted_batch(args, run, results):
        results.append({"session": "fixture", "apply": {"returncode": 0, "applied_rows": 17}})
        run([sys.executable, "-c", "pass"])

    with (patch.object(B, "parse_args", return_value=batch_args),
          patch.object(B, "apply_batch", side_effect=interrupted_batch),
          patch.dict(os.environ, {DEADLINE_ENV: str(time.time() - 1)})):
        assert B.main() == 75
    saved = json.loads(report_path.read_text())
    assert saved["status"] == "deferred_budget_exhausted"
    assert saved["sessions"][0]["apply"]["applied_rows"] == 17

    def cooperative_batch(args, run, results):
        results.append({"session": "fixture", "apply": {"returncode": 0, "applied_rows": 17}})
        run([sys.executable, "-c", "raise SystemExit(75)"])

    with (patch.object(B, "parse_args", return_value=batch_args),
          patch.object(B, "apply_batch", side_effect=cooperative_batch)):
        assert B.main() == 75
    saved = json.loads(report_path.read_text())
    assert saved["status"] == "deferred_budget_exhausted"
    assert saved["sessions"][0]["apply"]["applied_rows"] == 17


def check_cli_stopped(root):
    binary = ROOT / ".build/debug/murmurmark"
    assert binary.is_file(), "swift build must run before the publication checker"
    session = root / "cli-session"
    write(session / "session.json", {})
    runner = root / "stopped-python"
    runner.write_text(f"#!{sys.executable}\nimport os\nraise SystemExit(int(os.environ['TEST_CHILD_EXIT']))\n")
    runner.chmod(0o755)
    for code, state in ((75, "deferred_budget_exhausted"), (130, "interrupted_recoverable"), (2, None)):
        result = subprocess.run(
            [str(binary), "report", str(session)], cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "MURMURMARK_PYTHON": str(runner), "TEST_CHILD_EXIT": str(code)},
        )
        if state is None:
            assert result.returncode != 0 and "error:" in result.stderr, result
        else:
            assert result.returncode == code and state in result.stderr, result
            assert "completed steps saved" in result.stderr and "error:" not in result.stderr


def check_nested_stops(root):
    pipeline = load("run-session-pipeline")
    child = root / "scripts" / "cooperative.py"
    child.parent.mkdir(parents=True)
    child.write_text("import sys\nraise SystemExit(int(sys.argv[1]))\n")
    for code, status in ((0, "passed"), (75, "deferred_budget_exhausted"),
                         (130, "interrupted"), (2, "failed")):
        result = pipeline.run_step(
            {"name": "cooperative_fixture", "enabled": True,
             "command": [sys.executable, str(child), str(code)]}, root, False,
            progress_interval_sec=60, session=root / "session", report_path=root / "report.json",
            pipeline_started_at="fixture", pipeline_phase=pipeline.DEFERRED_PHASE,
        )
        assert result["status"] == status and result["returncode"] == code, result
        bounded = run_bounded([sys.executable, str(child), str(code)])
        assert bounded["returncode"] == code, bounded
    result = pipeline.run_step(
        {"name": "external_fixture", "enabled": True,
         "command": [sys.executable, "-c", "raise SystemExit(75)"]}, root, False,
        progress_interval_sec=60, session=root / "session", report_path=root / "report.json",
        pipeline_started_at="fixture", pipeline_phase=pipeline.DEFERRED_PHASE,
    )
    assert result["status"] == "failed", result


def check_nested_worker_cleanup(root):
    pid_file = root / "nested-worker.pid"
    worker = root / "stubborn-worker.py"
    worker.write_text(
        "import os, signal, time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    wrapper = root / "nested-wrapper.py"
    wrapper.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        "from murmurmark_deadline import run_bounded\n"
        f"run_bounded([sys.executable, {str(worker)!r}], max_seconds=30)\n"
    )
    pid = None
    try:
        result = run_bounded([sys.executable, str(wrapper)], max_seconds=0.5)
        assert result["returncode"] == 75, result
        assert pid_file.is_file(), "fixture worker did not start"
        pid = int(pid_file.read_text())
        state = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
        assert not state.stdout.strip() or state.stdout.strip().startswith("Z"), "orphan worker after budget"
    finally:
        if pid is None and pid_file.exists():
            pid = int(pid_file.read_text())
        if pid is not None:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-session", type=Path, action="append", default=[],
                        help="Explicitly refresh cached publication/reports of a local session, never run ASR.")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.real_session:
        if args.report is None:
            parser.error("--real-session requires a private --report path")
        check_real_corpus(args.real_session, args.report)
        return
    with tempfile.TemporaryDirectory(prefix="murmurmark-review-publication-") as temp:
        root = Path(temp)
        check_publication(root)
        check_v1_reuse(root / "v1")
        check_deadlines_and_review_order(root)
        check_cli_stopped(root)
        check_nested_stops(root / "nested")
        check_nested_worker_cleanup(root)
    print("review publication and deadline checks passed")


def check_real_corpus(sessions, report_path):
    report = {"schema": "murmurmark.review_publication_corpus_check/v1", "sessions": []}
    for session in sessions:
        session = session.resolve()
        files = set(session.glob("audio/**/*.caf"))
        files.update(session.glob("derived/asr/**/*.json"))
        files.update(path for path in session.glob("derived/transcript-simple/**/raw/**/*")
                     if path.is_file() and path.suffix in {".json", ".jsonl", ".txt", ".vtt"})
        files.update(session.glob("derived/readiness/review-plan/review_decisions.jsonl"))
        files.update(session.glob("derived/transcript-simple/whisper-cpp/resolved/clean_dialogue*.json"))
        files.update(session.glob("derived/transcript-simple/whisper-cpp/resolved/transcript*.md"))
        files.update(session.glob("derived/pipeline-run/pipeline_run_report*.json"))
        files.update(session.glob("derived/meeting-lifecycle/report.json"))
        before = {str(path): M.sha256_file(path) for path in sorted(files)}
        row = {"session": str(session), "status": "running", "frozen_inputs": before}
        report["sessions"].append(row)
        write(report_path, report)
        start = time.monotonic()
        result = run_bounded([
            sys.executable, str(ROOT / "scripts/reconcile-session-state.py"), str(session),
            "--reason", "review_reliability_corpus_check", "--skip-review-rebase", "--cached-speakers-only",
        ], max_seconds=180, cwd=ROOT)
        row.update({"elapsed_sec": round(time.monotonic() - start, 3), "command_result": result})
        after = {str(path): M.sha256_file(path) if path.is_file() else None for path in sorted(files)}
        row["inputs_unchanged"] = before == after
        outcome = M.read_json(session / "derived/outcome/outcome.json")
        provisional = M.read_json(session / M.DEFAULT_OUT_DIR / "selection.json")
        row["speaker_state"] = provisional.get("state")
        row["attribution_summary"] = provisional.get("summary")
        row["selected_profile"] = outcome.get("selected_profile")
        row["selected_speaker_profile"] = outcome.get("selected_speaker_profile")
        row["status"] = "passed" if result["returncode"] == 0 and row["inputs_unchanged"] else "failed"
        write(report_path, report)
        assert row["status"] == "passed", {"session": str(session), "result": result}
        rich = M.read_json(session / provisional["rich_transcript"]["path"])
        source = M.read_json(session / provisional["selected_dialogue"]["path"])
        preserved = ("id", "role", "text", "start", "end", "source_track", "quality")
        row["current_text_and_quality_preserved"] = [{key: item.get(key) for key in preserved} for item in rich["utterances"]] == [
            {key: item.get(key) for key in preserved} for item in source["utterances"]]
        if not row["current_text_and_quality_preserved"]:
            row["status"] = "failed"
        write(report_path, report)
        assert row["current_text_and_quality_preserved"], "publication changed transcript content"
        display = rich["display_turns"]
        expected_display = M.publication.display_turns(
            rich["utterances"], rich.get("remote_utterance_attributions"), rich.get("acoustic_timing_evidence"))
        assert display == expected_display
        positions = [turn["start"] for turn in display if turn["start"] is not None]
        assert positions == sorted(positions), "display timeline regressed"
        for parent in rich["utterances"]:
            assert "".join(turn["text"] for turn in display if turn["utterance_id"] == parent["id"]) == parent["text"]
        markdown = (session / provisional["selected_transcript"]["path"]).read_text()
        assert "\n".join(M.publication.render_body(display)).rstrip() in markdown
        row["publication_checks"] = {
            "ordered_display": True, "all_words_preserved": True, "json_markdown_agree": True,
            "warning_turns": sum(bool(turn["review_reasons"]) for turn in display),
            "acoustic_lower_bound_turns": sum(turn["time_basis"] == "acoustic_lower_bound" for turn in display),
            "parent_interval_turns": sum(turn["time_basis"] == "parent_interval" for turn in display),
        }
        write(report_path, report)
        metadata = lambda: {str(path.relative_to(session)): (path.stat().st_size, path.stat().st_mtime_ns)
                            for path in session.rglob("*") if path.is_file()}
        before_reads = metadata()
        reads = []
        binary = ROOT / ".build/debug/murmurmark"
        for _ in range(2):
            for args in (["status", str(session)], ["outcome", str(session)],
                         ["transcript", str(session), "--path-only"]):
                read = run_bounded([str(binary), *args], max_seconds=30, cwd=ROOT)
                reads.append({"command": args[0], "returncode": read["returncode"]})
                assert read["returncode"] == 0, read
                if args[0] == "transcript":
                    selected = Path(read["stdout"])
                    selected = selected if selected.is_absolute() else ROOT / selected
                    assert selected.is_file() and selected.read_bytes() == (
                        session / outcome["speaker_resolution"]["transcript_path"]
                    ).read_bytes(), "CLI/outcome publication differs"
        row["readonly_cli_checks"] = reads
        row["reads_do_not_write"] = metadata() == before_reads
        if not row["reads_do_not_write"]:
            row["status"] = "failed"
        write(report_path, report)
        assert row["reads_do_not_write"], "ordinary CLI reads changed session artifacts"
        print(f"cached corpus: {session.name}: {row['status']} ({row['elapsed_sec']}s)", flush=True)
        write(report_path, report)


if __name__ == "__main__":
    main()
