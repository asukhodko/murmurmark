#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


def load_module():
    path = Path(__file__).with_name("experiment-sidecar-contract.py")
    spec = importlib.util.spec_from_file_location("murmurmark_experiment_sidecar_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    with tempfile.TemporaryDirectory(prefix="murmurmark-compare-timeout-") as temp:
        session = Path(temp)
        (session / "session.json").write_text(
            json.dumps({"health": {"actual_duration_sec": 4200.0}}),
            encoding="utf-8",
        )
        previous = os.environ.pop("MURMURMARK_LIVE_BATCH_COMPARE_TIMEOUT_SEC", None)
        try:
            timeout, policy = module.compare_timeout(session)
            assert policy == "session_duration_adaptive", (timeout, policy)
            assert timeout == 624.0, timeout
            os.environ["MURMURMARK_LIVE_BATCH_COMPARE_TIMEOUT_SEC"] = "42"
            timeout, policy = module.compare_timeout(session)
            assert (timeout, policy) == (42.0, "environment_override"), (timeout, policy)
        finally:
            if previous is None:
                os.environ.pop("MURMURMARK_LIVE_BATCH_COMPARE_TIMEOUT_SEC", None)
            else:
                os.environ["MURMURMARK_LIVE_BATCH_COMPARE_TIMEOUT_SEC"] = previous
    print("experiment compare timeout checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
