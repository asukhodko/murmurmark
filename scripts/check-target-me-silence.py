#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


SCRIPT = Path(__file__).with_name("audit-target-me.py")


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_target_me_silence", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Target-Me module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    backend = module.ResemblyzerDVectorBackend()
    backend.ready = lambda: (True, "ok")
    backend._load = lambda: (_ for _ in ()).throw(
        AssertionError("silence must be rejected before loading resemblyzer")
    )
    with tempfile.TemporaryDirectory(prefix="murmurmark-target-me-silence-") as value:
        path = Path(value) / "silence.wav"
        sf.write(path, np.zeros(module.SAMPLE_RATE, dtype=np.float32), module.SAMPLE_RATE)
        embedding, info = backend.embed(path)
    assert embedding is None, embedding
    assert info["error"] == "silence", info
    print("Target-Me silence checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
