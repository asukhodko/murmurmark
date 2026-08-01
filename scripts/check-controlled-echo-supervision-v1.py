#!/usr/bin/env python3
"""Deterministic checks for Controlled Echo Supervision Lab v1."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from controlled_echo_supervision import (
    ANALYSIS_SAMPLE_RATE,
    SCHEMA_CAPTURE,
    SCHEMA_INSPECTION,
    build_schedule,
    convert_audio,
    default_policy_path,
    fingerprint,
    load_policy,
    local_only_gate_reasons,
    materialize_looped_stimulus,
    phase_operator_instruction,
    policy_sha,
    prompt_operator_action,
    prompt_operator_message,
    prompts_for_phase,
    read_jsonl,
    read_audio,
    remote_only_gate_reasons,
    sha256,
    speaker_validation_chunk_sec,
    total_duration,
    validate_stimulus_audio,
    validate_schedule,
    write_audio,
    write_json,
    write_jsonl,
)
from controlled_echo_supervision_corpus import (
    EXACT_OUTPUTS,
    build,
    finite_and_unclipped,
    replay,
    status,
)


ROOT = Path(__file__).resolve().parent.parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def fixture_policy(repo: Path) -> Path:
    source_dir = repo / "source"
    source_dir.mkdir(parents=True)
    source_files = {
        "previous_policy.json": b'{"fixture":"previous_policy"}\n',
        "previous_decision.json": b'{"decision":"DO_NOT_TRAIN"}\n',
        "production_policy.json": b'{"production":"local_fir_role_masked"}\n',
    }
    for name, content in source_files.items():
        (source_dir / name).write_bytes(content)
    hard_mic = source_dir / "hard_mic.bin"
    hard_remote = source_dir / "hard_remote.bin"
    hard_evidence = source_dir / "hard_evidence.json"
    hard_mic.write_bytes(b"fixture-hard-mic\n")
    hard_remote.write_bytes(b"fixture-hard-remote\n")
    hard_evidence.write_bytes(b'{"fixture":"hard-evidence"}\n')
    write_json(
        source_dir / "previous_hard_test.json",
        {
            "schema": "murmurmark.echo_adaptation_immutable_hard_test/v1",
            "items": [
                {
                    "id": "fixture-hard-01",
                    "split": "hard_test",
                    "usage": "hard_evaluation",
                    "audio_sources": {
                        "mic": {
                            "path": str(hard_mic.relative_to(repo)),
                            "sha256": sha256(hard_mic),
                        },
                        "remote_aligned": {
                            "path": str(hard_remote.relative_to(repo)),
                            "sha256": sha256(hard_remote),
                        },
                    },
                    "evidence": {
                        "source_artifact": str(hard_evidence.relative_to(repo)),
                        "source_sha256": sha256(hard_evidence),
                    },
                }
            ],
        },
    )

    policy = copy.deepcopy(load_policy(default_policy_path()))
    durations = {
        "settle": 1,
        "silence_background": 8,
        "guard_before_remote": 1,
        "remote_only": 8,
        "guard_before_local": 1,
        "local_only": 8,
        "guard_before_noise": 1,
        "keyboard_noise": 8,
        "guard_before_double_talk": 1,
        "controlled_double_talk": 8,
        "guard_before_opening": 1,
        "opening_backchannel": 8,
        "tail": 1,
    }
    for phase in policy["phases"]:
        phase["duration_sec"] = durations[phase["id"]]
        if phase.get("prompt_set"):
            phase["prompt_interval_sec"] = 2
    policy["capture"]["phase_trim_sec"] = 0
    policy["coverage_gates"] = {
        "minimum_train_capture_count": 4,
        "minimum_dev_capture_count": 1,
        "minimum_controlled_hard_test_capture_count": 1,
        "minimum_train_local_only_seconds": 24,
        "minimum_train_remote_only_seconds": 24,
        "minimum_train_synthetic_mixture_seconds": 100,
        "minimum_dev_local_only_seconds": 8,
        "minimum_dev_remote_only_seconds": 8,
        "minimum_dev_synthetic_mixture_seconds": 24,
        "minimum_controlled_hard_test_double_talk_seconds": 8,
        "minimum_protected_local_items": 12,
        "minimum_opening_backchannel_items": 6,
    }
    mapping = (
        ("previous_corpus_policy", "previous_corpus_policy_sha256", "previous_policy.json"),
        ("previous_corpus_decision", "previous_corpus_decision_sha256", "previous_decision.json"),
        ("previous_hard_test", "previous_hard_test_sha256", "previous_hard_test.json"),
        ("production_policy", "production_policy_sha256", "production_policy.json"),
    )
    for path_key, hash_key, name in mapping:
        path = source_dir / name
        policy["source"][path_key] = str(path.relative_to(repo))
        policy["source"][hash_key] = sha256(path)
    policy_path = repo / "policies" / "controlled-echo-supervision-v1.json"
    write_json(policy_path, policy)
    return policy_path


def phase_audio(kind: str, session_index: int, seconds: int = 8) -> tuple[np.ndarray, np.ndarray]:
    count = seconds * ANALYSIS_SAMPLE_RATE
    timeline = np.arange(count, dtype=np.float64) / ANALYSIS_SAMPLE_RATE
    remote = np.zeros(count, dtype=np.float32)
    mic = np.zeros(count, dtype=np.float32)
    if kind == "remote_only":
        remote = np.asarray(0.03 * np.sin(2 * np.pi * 440 * timeline), dtype=np.float32)
        mic[160:] = remote[:-160] * 0.15
    elif kind == "local_only":
        frequency = 210 + session_index * 17
        mic = np.asarray(0.02 * np.sin(2 * np.pi * frequency * timeline), dtype=np.float32)
    elif kind == "keyboard_noise":
        generator = np.random.default_rng(10_000 + session_index)
        mic = np.asarray(generator.normal(0.0, 0.002, count), dtype=np.float32)
    elif kind == "controlled_double_talk":
        remote = np.asarray(0.03 * np.sin(2 * np.pi * 440 * timeline), dtype=np.float32)
        local = np.asarray(
            0.02 * np.sin(2 * np.pi * (260 + session_index * 13) * timeline),
            dtype=np.float32,
        )
        echo = np.zeros_like(remote)
        echo[160:] = remote[:-160] * 0.15
        mic = local + echo
    elif kind == "opening_backchannel":
        mic = np.asarray(
            0.02 * np.sin(2 * np.pi * (310 + session_index * 11) * timeline),
            dtype=np.float32,
        )
    return mic, remote


def make_session(
    *,
    repo: Path,
    sessions_root: Path,
    policy_path: Path,
    scenario: str,
    index: int,
) -> Path:
    policy = load_policy(policy_path)
    schedule = build_schedule(policy)
    session = sessions_root / f"fixture-{index:02d}-{scenario}"
    raw_mic = session / "audio" / "mic" / "000001.caf"
    raw_remote = session / "audio" / "remote" / "000001.caf"
    raw_mic.parent.mkdir(parents=True)
    raw_remote.parent.mkdir(parents=True)
    raw_mic.write_bytes(f"raw-mic-{scenario}\n".encode())
    raw_remote.write_bytes(f"raw-remote-{scenario}\n".encode())
    derived = session / "derived" / "echo-lab"
    phases_dir = derived / "phases"
    phase_rows: list[dict] = []
    for phase in schedule:
        mic, remote = phase_audio(str(phase["kind"]), index, int(phase["duration_sec"]))
        mic_path = phases_dir / f"{int(phase['index']):02d}_{phase['id']}_mic.wav"
        remote_path = phases_dir / f"{int(phase['index']):02d}_{phase['id']}_remote.wav"
        write_audio(mic_path, mic)
        write_audio(remote_path, remote)
        artifacts = {
            "mic": fingerprint(mic_path, session),
            "remote": fingerprint(remote_path, session),
        }
        if phase["kind"] == "remote_only":
            aligned_path = phases_dir / f"{int(phase['index']):02d}_{phase['id']}_remote_aligned.wav"
            aligned = np.zeros_like(remote)
            aligned[160:] = remote[:-160]
            write_audio(aligned_path, aligned)
            artifacts["aligned_remote"] = fingerprint(aligned_path, session)
        phase_rows.append(
            {
                "schema": "murmurmark.controlled_echo_phase_inspection/v1",
                "phase_id": phase["id"],
                "kind": phase["kind"],
                "planned_start_sec": phase["planned_start_sec"],
                "planned_end_sec": phase["planned_end_sec"],
                "analysis_start_sec": phase["planned_start_sec"],
                "analysis_end_sec": phase["planned_end_sec"],
                "accepted": phase["kind"] != "guard",
                "outcome": "included" if phase["kind"] != "guard" else "excluded",
                "reasons": [] if phase["kind"] != "guard" else ["guard_phase"],
                "metrics": {
                    "duration_sec": phase["duration_sec"],
                    "finite": True,
                    "mic_peak": float(np.max(np.abs(mic))) if mic.size else 0.0,
                    "remote_peak": float(np.max(np.abs(remote))) if remote.size else 0.0,
                    "mic_clipped_ratio": 0.0,
                    "remote_clipped_ratio": 0.0,
                    "mic_rms_db": -30.0,
                    "remote_rms_db": -30.0 if np.any(remote) else -240.0,
                    "lag_ms": 10.0 if phase["kind"] == "remote_only" else 0.0,
                    "lagged_correlation": 0.9 if phase["kind"] == "remote_only" else 0.0,
                },
                "evidence": {},
                "artifacts": artifacts,
            }
        )
    prompts = [prompt for phase in schedule for prompt in prompts_for_phase(phase)]
    write_json(
        derived / "echo_lab_schedule.json",
        {
            "schema": "murmurmark.controlled_echo_schedule/v1",
            "policy_sha256": policy_sha(policy_path),
            "scenario": scenario,
            "split": policy["scenarios"][scenario]["split"],
            "total_duration_sec": total_duration(policy),
            "phases": schedule,
            "prompts": prompts,
        },
    )
    write_jsonl(derived / "phase_inventory.jsonl", phase_rows)
    capture_manifest = {
        "schema": SCHEMA_CAPTURE,
        "session_id": session.name,
        "scenario": scenario,
        "split": policy["scenarios"][scenario]["split"],
        "policy_sha256": policy_sha(policy_path),
        "raw": [fingerprint(raw_mic, session), fingerprint(raw_remote, session)],
        "capture_mode": {
            "durable_raw_writer": True,
            "live_shadow": False,
            "second_capture": False,
        },
    }
    write_json(derived / "capture_manifest.json", capture_manifest)
    inspection = {
        "schema": SCHEMA_INSPECTION,
        "profile": policy["profile"],
        "session_id": session.name,
        "scenario": scenario,
        "split": policy["scenarios"][scenario]["split"],
        "policy_sha256": policy_sha(policy_path),
        "accepted": True,
        "outcome": "accepted",
        "reasons": [],
        "phases": phase_rows,
    }
    write_json(derived / "inspection.json", inspection)
    return session


def check_gate_helpers(policy: dict) -> None:
    validation = policy["validation"]
    clean_metrics = {
        "remote_rms_db": -20,
        "mic_rms_db": -35,
        "lagged_correlation": 0.8,
    }
    clean_evidence = {
        "remote_stimulus_token_recall": 0.9,
        "remote_stimulus_unique_token_ratio": 0.0,
        "mic_unique_token_ratio_against_remote": 0.0,
        "target_me_remote_contamination": False,
    }
    require(
        not remote_only_gate_reasons(clean_metrics, clean_evidence, validation),
        "clean remote-only evidence must pass",
    )
    contaminated = dict(clean_evidence)
    contaminated["target_me_remote_contamination"] = None
    require(
        "remote_only_target_me_contamination"
        in remote_only_gate_reasons(clean_metrics, contaminated, validation),
        "missing Target-Me evidence must fail closed",
    )
    local_metrics = {"remote_rms_db": -240, "mic_rms_db": -25}
    local_evidence = {
        "prompt_token_recall": 0.9,
        "target_me_local_similarity": 0.9,
        "target_me_validation_chunks": 8,
    }
    require(
        not local_only_gate_reasons(local_metrics, local_evidence, validation),
        "clean local-only evidence must pass",
    )
    local_evidence["target_me_local_similarity"] = 0.1
    require(
        "target_me_local_not_confirmed"
        in local_only_gate_reasons(local_metrics, local_evidence, validation),
        "weak enrollment must fail closed",
    )


def main() -> int:
    base_policy = load_policy(default_policy_path())
    schedule = build_schedule(base_policy)
    validate_schedule(schedule, total_duration(base_policy))
    require(schedule == build_schedule(base_policy), "phase schedule is not deterministic")
    require(
        "ПРОИЗНЕСИ ВСЛУХ" in prompt_operator_message("local_only", "Проверка"),
        "spoken prompt is not explicit",
    )
    require(
        "МОЛЧИ" in phase_operator_instruction("remote_only"),
        "remote-only instruction does not require silence",
    )
    require(
        prompt_operator_action("controlled_double_talk") == "speak",
        "double-talk prompt lost the operator action",
    )
    require(
        prompt_operator_action("keyboard_noise") == "type",
        "keyboard prompt lost the operator action",
    )
    require(
        "НЕ В ЭТОМ ТЕРМИНАЛЕ" in prompt_operator_message("keyboard_noise", ""),
        "keyboard prompt may leave buffered shell input behind",
    )
    require(
        speaker_validation_chunk_sec("opening_backchannel") == 8.0,
        "short opening phrases lost their wider speaker-validation window",
    )
    require(
        speaker_validation_chunk_sec("controlled_double_talk") == 4.0,
        "double-talk speaker-validation window changed unexpectedly",
    )
    capture_help = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/controlled-echo-supervision-lab.py"),
            "capture",
            "--help",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(capture_help.returncode == 0, "capture help failed")
    require(
        "--confirm-operator-instructions" in capture_help.stdout,
        "non-interactive operator confirmation flag is undocumented",
    )
    for phase in schedule:
        for prompt in prompts_for_phase(phase):
            require(
                float(phase["planned_start_sec"])
                < float(prompt["planned_at_sec"])
                < float(phase["planned_end_sec"]),
                "prompt lies outside its phase",
            )
    check_gate_helpers(base_policy)
    valid, reasons = finite_and_unclipped(
        np.asarray([0.0, np.nan], dtype=np.float32),
        float(base_policy["validation"]["maximum_abs_peak"]),
    )
    require(not valid and "non_finite" in reasons, "non-finite audio was accepted")
    valid, reasons = finite_and_unclipped(
        np.asarray([0.0, 1.0], dtype=np.float32),
        float(base_policy["validation"]["maximum_abs_peak"]),
    )
    require(not valid and "clipping" in reasons, "clipped audio was accepted")

    with tempfile.TemporaryDirectory(prefix="murmurmark-controlled-echo-check-") as temporary:
        repo = Path(temporary) / "repo"
        debug_cli = ROOT / ".build/debug/murmurmark"
        if debug_cli.is_file():
            fake_python = repo / "fake-python"
            invocation_log = repo / "echo-confirmation-invocations.txt"
            fake_python.parent.mkdir(parents=True, exist_ok=True)
            fake_python.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$MURMURMARK_ECHO_CONFIRM_LOG\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            confirmation_env = os.environ.copy()
            confirmation_env["MURMURMARK_PYTHON"] = str(fake_python)
            confirmation_env["MURMURMARK_ECHO_CONFIRM_LOG"] = str(invocation_log)
            confirmed = subprocess.run(
                [
                    str(debug_cli),
                    "echo-lab",
                    "capture",
                    "--out",
                    str(repo / "confirmed-session"),
                    "--scenario",
                    "speaker_train_quiet",
                ],
                cwd=ROOT,
                env=confirmation_env,
                input="ГОТОВ\n",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            require(confirmed.returncode == 0, f"foreground confirmation failed: {confirmed.stdout}")
            invocations = invocation_log.read_text(encoding="utf-8").splitlines()
            require(len(invocations) == 2, f"unexpected confirmation process count: {invocations}")
            require("--preflight-only" in invocations[0], "CLI skipped echo-lab preflight")
            require(
                "--confirm-operator-instructions" in invocations[1]
                and "--operator-confirmation-mode interactive_cli" in invocations[1],
                "confirmed capture did not receive foreground CLI provenance",
            )
            invocation_log.unlink()
            rejected = subprocess.run(
                [
                    str(debug_cli),
                    "echo-lab",
                    "capture",
                    "--out",
                    str(repo / "rejected-session"),
                    "--scenario",
                    "speaker_train_quiet",
                ],
                cwd=ROOT,
                env=confirmation_env,
                input="НЕТ\n",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            require(rejected.returncode != 0, "unconfirmed echo-lab capture was accepted")
            rejected_invocations = invocation_log.read_text(encoding="utf-8").splitlines()
            require(
                len(rejected_invocations) == 1 and "--preflight-only" in rejected_invocations[0],
                "unconfirmed capture advanced beyond preflight",
            )
        valid_stimulus = repo / "valid-stimulus.wav"
        write_audio(
            valid_stimulus,
            np.zeros(48_000, dtype=np.float32),
            sample_rate=48_000,
        )
        validation = validate_stimulus_audio(
            valid_stimulus,
            duration_sec=1.0,
            sample_rate=48_000,
            maximum_peak=0.995,
        )
        require(validation["frames"] == 48_000, "valid stimulus frame count was lost")
        try:
            validate_stimulus_audio(
                valid_stimulus,
                duration_sec=2.0,
                sample_rate=48_000,
                maximum_peak=0.995,
            )
        except RuntimeError as error:
            require("stimulus_duration" in str(error), "wrong stimulus failure reason")
        else:
            raise AssertionError("wrong stimulus duration was accepted")
        source_aiff = repo / "source.aiff"
        source = np.asarray(
            0.4 * np.sin(2.0 * np.pi * 220.0 * np.arange(24_000) / 24_000),
            dtype=np.float32,
        )
        import soundfile as sf

        sf.write(source_aiff, source, 24_000, format="AIFF", subtype="PCM_16")
        looped_stimulus = repo / "looped.wav"
        materialize_looped_stimulus(
            source_aiff,
            looped_stimulus,
            duration_sec=2.5,
            sample_rate=48_000,
        )
        looped_validation = validate_stimulus_audio(
            looped_stimulus,
            duration_sec=2.5,
            sample_rate=48_000,
            maximum_peak=0.995,
        )
        require(looped_validation["frames"] == 120_000, "looped stimulus duration drifted")
        stereo_source = repo / "stereo-source.wav"
        stereo_samples = np.column_stack(
            (
                np.full(48_000, 0.5, dtype=np.float32),
                np.full(48_000, 0.5, dtype=np.float32),
            )
        )
        sf.write(stereo_source, stereo_samples, 48_000, subtype="FLOAT")
        mono_analysis = repo / "mono-analysis.wav"
        convert_audio(stereo_source, mono_analysis)
        mono_peak = float(np.max(np.abs(read_audio(mono_analysis).samples)))
        require(
            abs(mono_peak - 0.5) < 1.0e-4,
            f"stereo analysis downmix changed the signal level: {mono_peak}",
        )
        sessions_root = repo / "sessions"
        sessions_root.mkdir(parents=True)
        policy_path = fixture_policy(repo)
        policy = load_policy(policy_path)
        aborted_session = sessions_root / "aborted-capture"
        write_json(
            aborted_session / "derived/echo-lab/capture_abort.json",
            {
                "schema": "murmurmark.controlled_echo_capture_abort/v1",
                "phase_id": "local_only",
                "reason": "fixture volume drift",
            },
        )
        aborted_inspect = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/controlled-echo-supervision-lab.py"),
                "--repo-root",
                str(repo),
                "inspect",
                str(aborted_session),
                "--policy",
                str(policy_path),
                "--sessions-root",
                str(sessions_root),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        require(aborted_inspect.returncode == 1, "aborted capture was accepted")
        require(
            "capture was aborted during local_only" in aborted_inspect.stderr,
            "aborted capture inspection is not actionable",
        )
        stale_stimuli = sessions_root / "_echo_lab/controlled-echo-supervision-v1/stimuli"
        stale_stimuli.mkdir(parents=True)
        stale_stimulus = stale_stimuli / "double_talk.wav"
        stale_stimulus.write_bytes(b"changed-stimulus\n")
        write_json(
            stale_stimuli / "stimuli_manifest.json",
            {
                "schema": "murmurmark.controlled_echo_stimuli/v1",
                "policy_sha256": policy_sha(policy_path),
                "stimuli": [
                    {
                        "id": "double_talk",
                        "path": str(stale_stimulus.relative_to(sessions_root)),
                        "duration_sec": 8,
                        "sample_rate": 48_000,
                        "sha256": "0" * 64,
                    }
                ],
            },
        )
        stale_capture = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/controlled-echo-supervision-lab.py"),
                "--repo-root",
                str(repo),
                "capture",
                "--out",
                str(sessions_root / "stale-stimulus-capture"),
                "--scenario",
                "speaker_train_quiet",
                "--policy",
                str(policy_path),
                "--sessions-root",
                str(sessions_root),
                "--murmurmark-executable",
                "/usr/bin/true",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        require(stale_capture.returncode == 1, "changed stimulus was accepted")
        require(
            "run `murmurmark echo-lab prepare`" in stale_capture.stderr,
            "changed stimulus error omitted the recovery command",
        )
        require(
            "actual_sha256=" in stale_capture.stderr,
            "changed stimulus error omitted the observed identity",
        )
        require(
            not (sessions_root / "stale-stimulus-capture").exists(),
            "failed stimulus preflight created a capture session",
        )
        scenarios = (
            "speaker_train_quiet",
            "speaker_train_normal_a",
            "speaker_train_normal_b",
            "speaker_train_loud",
            "speaker_dev_normal",
            "speaker_hard_doubletalk",
        )
        sessions = [
            make_session(
                repo=repo,
                sessions_root=sessions_root,
                policy_path=policy_path,
                scenario=scenario,
                index=index,
            )
            for index, scenario in enumerate(scenarios, 1)
        ]
        excluded_session = make_session(
            repo=repo,
            sessions_root=sessions_root,
            policy_path=policy_path,
            scenario="speaker_train_quiet",
            index=99,
        )
        excluded_path = excluded_session / "derived" / "echo-lab" / "inspection.json"
        excluded_payload = json.loads(excluded_path.read_text(encoding="utf-8"))
        excluded_payload["accepted"] = False
        excluded_payload["outcome"] = "excluded"
        excluded_payload["reasons"] = ["fixture_contamination"]
        write_json(excluded_path, excluded_payload)
        raw_hashes = {
            path: sha256(path)
            for session in sessions
            for path in (
                session / "audio" / "mic" / "000001.caf",
                session / "audio" / "remote" / "000001.caf",
            )
        }
        source_hashes = {
            path: sha256(path)
            for path in (repo / "source").iterdir()
            if path.is_file()
        }
        output_dir = sessions_root / "_reports" / "controlled-echo-supervision-v1"
        decision = build(
            sessions_root=sessions_root,
            output_dir=output_dir,
            policy_path=policy_path,
        )
        require(decision["decision"] == "READY_FOR_ADAPTATION", "fixture corpus must pass")
        require(all((output_dir / name).is_file() for name in EXACT_OUTPUTS), "missing corpus output")
        require(
            all(sha256(path) == digest for path, digest in raw_hashes.items()),
            "build modified raw capture",
        )
        require(
            all(sha256(path) == digest for path, digest in source_hashes.items()),
            "build modified production/source evidence",
        )
        split_manifest = json.loads((output_dir / "split_manifest.json").read_text())
        require(split_manifest["session_disjoint"], "fixture sessions leaked across splits")
        exclusion_report = json.loads((output_dir / "exclusion_report.json").read_text())
        require(
            any(
                row.get("session_id") == excluded_session.name
                and "fixture_contamination" in row.get("reasons", [])
                for row in exclusion_report["discovery"]
            ),
            "excluded capture provenance was lost",
        )
        supervision = read_jsonl(output_dir / "supervision_manifest.jsonl")
        hard_training = [
            row for row in supervision if row["split"] == "hard_test" and row["training_eligible"]
        ]
        require(not hard_training, "hard-test item entered training")
        synthetic = [row for row in supervision if row["kind"] == "synthetic_double_talk"]
        require(synthetic, "fixture produced no synthetic pairs")
        for row in synthetic:
            require(row["duration_sec"] == 4.0, "synthetic duration is not exact")
            require(
                row["reconstruction_max_abs_error"]
                <= policy["oracle_gates"]["synthetic_reconstruction_max_abs_error"],
                "synthetic reconstruction failed",
            )
            require(
                row["target_remote_correlation"]
                <= policy["oracle_gates"]["target_remote_correlation_max"],
                "target leaked remote reference",
            )
            require(
                row["mixture_target_correlation"]
                >= policy["oracle_gates"]["mixture_contains_target_min_correlation"],
                "synthetic mixture lost its target",
            )
            audio_path = output_dir / row["mixture"]["path"]
            audio = read_audio_for_check(audio_path)
            require(audio.size == 4 * ANALYSIS_SAMPLE_RATE, "materialized sample count mismatch")
            require(np.all(np.isfinite(audio)), "materialized audio is non-finite")
            require(float(np.max(np.abs(audio))) <= policy["validation"]["maximum_abs_peak"], "clipping")

        replay_report = replay(
            sessions_root=sessions_root,
            output_dir=output_dir,
            policy_path=policy_path,
        )
        require(
            replay_report["status"] == "passed",
            f"byte-stable replay failed: {replay_report['mismatches'][:3]}",
        )
        status_payload = status(output_dir)
        require(status_payload["decision"] == "READY_FOR_ADAPTATION", "status lost decision")
        require(not status_payload["missing_scenarios"], "status reports completed scenarios missing")
        require(
            status_payload["next_command"]
            == "start the separate Speaker-Preserving Neural Echo v2 goal",
            "status lost the post-lab route",
        )
        require(
            all(sha256(path) == digest for path, digest in raw_hashes.items()),
            "replay modified raw capture",
        )

        tampered = sessions[0] / "audio" / "mic" / "000001.caf"
        original = tampered.read_bytes()
        tampered.write_bytes(original + b"tamper")
        tamper_report = replay(
            sessions_root=sessions_root,
            output_dir=output_dir,
            policy_path=policy_path,
        )
        require(tamper_report["status"] == "failed", "SHA tamper did not fail closed")
        tampered.write_bytes(original)

    tracked_audio = subprocess.run(
        ["git", "ls-files", "*.wav", "*.caf", "*.aiff"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    require(not tracked_audio, f"private audio is tracked: {tracked_audio}")
    print("controlled echo supervision checks ok")
    return 0


def read_audio_for_check(path: Path) -> np.ndarray:
    import soundfile as sf

    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    require(sample_rate == ANALYSIS_SAMPLE_RATE, "unexpected materialized sample rate")
    return np.asarray(audio, dtype=np.float32)


if __name__ == "__main__":
    raise SystemExit(main())
