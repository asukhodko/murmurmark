#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


def load_manager() -> object:
    path = Path(__file__).with_name("live-causal-me-recovery-manager.py")
    spec = importlib.util.spec_from_file_location("murmurmark_runtime_manager_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_compare() -> object:
    path = Path(__file__).with_name("compare-live-batch.py")
    spec = importlib.util.spec_from_file_location("murmurmark_runtime_compare_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wait(manager: object, captured_sec: float, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while manager.process is not None and time.monotonic() < deadline:
        manager.poll(captured_sec=captured_sec)
        time.sleep(0.02)
    manager.poll(captured_sec=captured_sec)


def main() -> int:
    module = load_manager()
    compare = load_compare()
    runtime_policy = compare.RUNTIME_CAUSAL_ME_RECOVERY_V1_PROFILE_POLICY
    default_policies = compare.selected_lab_policies(
        SimpleNamespace(only_lab_policy=[], with_labs=False, lab_policy=[])
    )
    assert runtime_policy not in default_policies
    explicit_policies = compare.selected_lab_policies(
        SimpleNamespace(only_lab_policy=[runtime_policy], with_labs=False, lab_policy=[])
    )
    assert explicit_policies == (runtime_policy,)
    with tempfile.TemporaryDirectory(prefix="murmurmark-causal-runtime-") as temporary:
        root = Path(temporary)
        session = root / "sessions/test"
        normal_preview = session / "derived/live/transcript.preview.md"
        normal_preview.parent.mkdir(parents=True)
        normal_preview.write_text("authoritative base draft\n", encoding="utf-8")
        before = digest(normal_preview)
        fake = root / "fake-runtime.py"
        fake.write_text(
            "import os,sys,time\n"
            "time.sleep(float(os.environ.get('FAKE_RUNTIME_DELAY','0')))\n"
            "raise SystemExit(int(os.environ.get('FAKE_RUNTIME_EXIT','0')))\n",
            encoding="utf-8",
        )

        os.environ["FAKE_RUNTIME_DELAY"] = "0.02"
        os.environ["FAKE_RUNTIME_EXIT"] = "0"
        success = module.CausalMeRecoveryManager(
            session=session,
            model=Path("model.bin"),
            language="ru",
            whisper_cli="whisper-cli",
            timeout_sec=2.0,
            max_live_lag_sec=30.0,
            runtime_script=fake,
        )
        success.submit(chunk_index=1, chunk_end_sec=30.0, captured_sec=30.0, recording_active=True)
        wait(success, 30.0)
        success.finish(captured_sec=30.0, wait_sec=0.1)
        assert success.completed_invocations == 1
        assert success.failed_invocations == 0
        assert success.final_live_lag_sec == 0.0

        os.environ["FAKE_RUNTIME_DELAY"] = "1.0"
        timed_out = module.CausalMeRecoveryManager(
            session=session / "timeout",
            model=Path("model.bin"),
            language="ru",
            whisper_cli="whisper-cli",
            timeout_sec=0.05,
            max_live_lag_sec=30.0,
            runtime_script=fake,
        )
        timed_out.submit(chunk_index=1, chunk_end_sec=30.0, captured_sec=30.0, recording_active=True)
        wait(timed_out, 30.0)
        timed_out.finish(captured_sec=30.0, wait_sec=0.0)
        assert timed_out.timed_out_invocations == 1
        assert timed_out.failed_invocations == 1

        os.environ["FAKE_RUNTIME_DELAY"] = "0.01"
        os.environ["FAKE_RUNTIME_EXIT"] = "7"
        failed = module.CausalMeRecoveryManager(
            session=session / "failed",
            model=Path("model.bin"),
            language="ru",
            whisper_cli="whisper-cli",
            timeout_sec=2.0,
            max_live_lag_sec=30.0,
            runtime_script=fake,
        )
        failed.submit(chunk_index=1, chunk_end_sec=30.0, captured_sec=30.0, recording_active=True)
        wait(failed, 30.0)
        failed.finish(captured_sec=30.0, wait_sec=0.0)
        assert failed.failed_invocations == 1
        assert failed.last_error == "child_exit_7"

        os.environ["FAKE_RUNTIME_DELAY"] = "0.15"
        os.environ["FAKE_RUNTIME_EXIT"] = "0"
        coalesced = module.CausalMeRecoveryManager(
            session=session / "coalesced",
            model=Path("model.bin"),
            language="ru",
            whisper_cli="whisper-cli",
            timeout_sec=2.0,
            max_live_lag_sec=60.0,
            runtime_script=fake,
        )
        coalesced.submit(chunk_index=1, chunk_end_sec=30.0, captured_sec=30.0, recording_active=True)
        coalesced.submit(chunk_index=2, chunk_end_sec=60.0, captured_sec=60.0, recording_active=True)
        coalesced.submit(chunk_index=3, chunk_end_sec=90.0, captured_sec=90.0, recording_active=True)
        wait(coalesced, 90.0)
        wait(coalesced, 90.0)
        coalesced.finish(captured_sec=90.0, wait_sec=1.0)
        assert coalesced.coalesced_submissions == 1
        assert coalesced.last_completed_chunk == 3
        assert coalesced.final_live_lag_sec == 0.0
        assert coalesced.max_observed_live_lag_sec <= 60.0

        lagged = module.CausalMeRecoveryManager(
            session=session / "lagged",
            model=Path("model.bin"),
            language="ru",
            whisper_cli="whisper-cli",
            timeout_sec=2.0,
            max_live_lag_sec=10.0,
            runtime_script=fake,
        )
        lagged.submit(chunk_index=1, chunk_end_sec=30.0, captured_sec=90.0, recording_active=True)
        assert lagged.process is None
        assert lagged.last_error == "live_lag_budget_exceeded"
        lagged.finish(captured_sec=90.0, wait_sec=0.0)
        assert lagged.final_live_lag_sec == 90.0

        os.environ["FAKE_RUNTIME_DELAY"] = "1.0"
        disabled = module.CausalMeRecoveryManager(
            session=session / "disabled",
            model=Path("model.bin"),
            language="ru",
            whisper_cli="whisper-cli",
            timeout_sec=2.0,
            max_live_lag_sec=30.0,
            runtime_script=fake,
        )
        disabled.submit(chunk_index=1, chunk_end_sec=30.0, captured_sec=30.0, recording_active=True)
        disabled.disable("synthetic_manager_failure")
        disabled_state = json.loads(disabled.state_path.read_text(encoding="utf-8"))
        assert disabled.process is None
        assert disabled_state["status"] == "disabled_fail_open"
        assert disabled_state["base_draft_fallback"] is True

        assert digest(normal_preview) == before
    print("live causal Me recovery runtime checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
