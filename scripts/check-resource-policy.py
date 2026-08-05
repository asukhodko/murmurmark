#!/usr/bin/env python3
"""Check low-impact scheduling defaults without changing the test runner priority."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import murmurmark_resource_policy as POLICY  # noqa: E402


def load_pipeline() -> object:
    path = SCRIPTS / "run-session-pipeline.py"
    spec = importlib.util.spec_from_file_location("murmurmark_resource_policy_pipeline", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check_defaults() -> None:
    background = POLICY.resolve_resource_policy("background")
    assert background.nice == 20
    assert background.darwin_background is True
    assert background.max_compute_threads == 4
    assert background.asr_threads == 4
    assert background.asr_track_workers == 1
    assert background.micro_asr_workers == 1
    assert background.live_asr_parallelism == 1

    opportunistic = POLICY.resolve_resource_policy("opportunistic")
    assert opportunistic.nice == 20
    assert opportunistic.darwin_background is False
    assert opportunistic.max_compute_threads == 0
    assert opportunistic.asr_threads == 6
    assert opportunistic.asr_track_workers == 2
    assert opportunistic.micro_asr_workers == 4
    assert opportunistic.live_asr_parallelism == 2

    performance = POLICY.resolve_resource_policy("performance")
    assert performance.nice is None
    assert performance.max_compute_threads == 0
    assert performance.asr_track_workers == 2
    assert performance.micro_asr_workers == 4


def check_environment_caps() -> None:
    policy = POLICY.resolve_resource_policy("background", 2)
    environment = {
        "OMP_NUM_THREADS": "16",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "invalid",
    }
    with mock.patch.dict(os.environ, environment, clear=True):
        applied = POLICY.configure_thread_environment(policy)
        assert applied["OMP_NUM_THREADS"] == "2"
        assert applied["OPENBLAS_NUM_THREADS"] == "1"
        assert applied["MKL_NUM_THREADS"] == "2"
        assert os.environ["MURMURMARK_MAX_COMPUTE_THREADS"] == "2"
        assert os.environ["TOKENIZERS_PARALLELISM"] == "false"


def check_applied_subprocess() -> None:
    code = f"""
import json, os, subprocess, sys
sys.path.insert(0, {str(SCRIPTS)!r})
import murmurmark_resource_policy as policy
report = policy.apply_resource_policy(policy.resolve_resource_policy('background', 2))
child = subprocess.check_output([sys.executable, '-c', 'import json, os; print(json.dumps({{"nice": os.getpriority(os.PRIO_PROCESS, 0), "threads": os.environ.get("OMP_NUM_THREADS")}}))'], text=True)
print(json.dumps({{"report": report, "child": json.loads(child)}}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    report = payload["report"]
    assert report["profile"] == "background"
    assert report["nice_after"] == 20
    assert report["max_compute_threads"] == 2
    if sys.platform == "darwin" and Path("/usr/sbin/taskpolicy").exists():
        assert report["taskpolicy_status"] == "applied"
    assert payload["child"] == {"nice": 20, "threads": "2"}


def check_opportunistic_subprocess() -> None:
    code = f"""
import json, os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
import murmurmark_resource_policy as policy
report = policy.apply_resource_policy(policy.resolve_resource_policy('opportunistic'))
print(json.dumps(report))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    report = json.loads(completed.stdout)
    assert report["profile"] == "opportunistic"
    assert report["nice_after"] == 20
    assert report["taskpolicy_status"] == "disabled"
    assert report["max_compute_threads"] == 0
    assert report["asr_defaults"] == {
        "threads": 6,
        "track_workers": 2,
        "micro_asr_workers": 4,
    }


def parse_pipeline_args(*extra: str) -> object:
    module = load_pipeline()
    argv = [str(SCRIPTS / "run-session-pipeline.py"), "sessions/example", *extra]
    with mock.patch.object(sys, "argv", argv), mock.patch.dict(os.environ, {}, clear=True):
        return module.parse_args()


def check_pipeline_defaults() -> None:
    background = parse_pipeline_args()
    assert background.resource_profile == "background"
    assert background.max_compute_threads == 4
    assert background.asr_threads == 4
    assert background.asr_track_workers == 1
    assert background.micro_asr_workers == 1

    opportunistic = parse_pipeline_args("--resource-profile", "opportunistic")
    assert opportunistic.resource_profile == "opportunistic"
    assert opportunistic.max_compute_threads == 0
    assert opportunistic.asr_threads == 6
    assert opportunistic.asr_track_workers == 2
    assert opportunistic.micro_asr_workers == 4

    performance = parse_pipeline_args("--resource-profile", "performance")
    assert performance.resource_profile == "performance"
    assert performance.max_compute_threads == 0
    assert performance.asr_threads == 6
    assert performance.asr_track_workers == 2
    assert performance.micro_asr_workers == 4

    bounded = parse_pipeline_args(
        "--resource-profile",
        "background",
        "--max-compute-threads",
        "2",
        "--asr-threads",
        "6",
    )
    assert bounded.asr_threads == 2


def check_integration_points() -> None:
    pipeline = (SCRIPTS / "run-session-pipeline.py").read_text(encoding="utf-8")
    live = (SCRIPTS / "live-pipeline-shadow.py").read_text(encoding="utf-8")
    assert "resource_policy_report = apply_resource_policy(args.resource_policy_spec)" in pipeline
    assert "args.resource_policy_report = apply_resource_policy(args.resource_policy_spec)" in live
    assert "configure_thread_environment(_early_policy)" in live


def main() -> int:
    check_defaults()
    check_environment_caps()
    check_applied_subprocess()
    check_opportunistic_subprocess()
    check_pipeline_defaults()
    check_integration_points()
    print("resource policy checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
