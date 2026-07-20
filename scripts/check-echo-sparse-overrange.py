#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/echo-guard-session-local-fir.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("echo_guard_session_local_fir", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load local FIR helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    sample_rate = 16_000
    base = np.full(sample_rate * 20, 0.1, dtype=np.float32)

    sparse = base.copy()
    sparse[80_000:80_080] = 2.5
    limited, sparse_report = module.limit_sparse_input_overrange(sparse, sample_rate)
    assert sparse_report["applied"] is True, sparse_report
    assert sparse_report["sample_count"] == 80, sparse_report
    assert sparse_report["region_count"] == 1, sparse_report
    assert sparse_report["duration_ms"] == 5.0, sparse_report
    assert float(np.max(np.abs(limited))) <= module.INPUT_PEAK_LIMIT + 1.0e-6, sparse_report
    assert np.array_equal(limited[:80_000], sparse[:80_000])

    sustained = base.copy()
    sustained[80_000:88_000] = 2.5
    unchanged, sustained_report = module.limit_sparse_input_overrange(sustained, sample_rate)
    assert sustained_report["applied"] is False, sustained_report
    assert sustained_report["region_count"] == 1, sustained_report
    assert sustained_report["duration_ms"] == 500.0, sustained_report
    assert float(np.max(np.abs(unchanged))) == 2.5, sustained_report

    clean, clean_report = module.limit_sparse_input_overrange(base, sample_rate)
    assert clean_report["applied"] is False, clean_report
    assert clean_report["sample_count"] == 0, clean_report
    assert clean_report["region_count"] == 0, clean_report
    assert np.array_equal(clean, base)

    print("echo sparse overrange checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
