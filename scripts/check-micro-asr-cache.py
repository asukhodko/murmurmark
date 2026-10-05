#!/usr/bin/env python3
"""Content cache regressions without loading an ASR model."""
from __future__ import annotations

import importlib.util
import fcntl
import json
import sys
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from murmurmark_deadline import ActionStopped, DEADLINE_ENV

import micro_asr_cache as cache


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-micro-cache-") as temporary:
        root = Path(temporary)
        model, executable = root / "model", root / "whisper-cli"
        model.write_bytes(b"model-v1")
        executable.write_bytes(b"whisper-v1")
        audio = root / "audio.wav"
        with wave.open(str(audio), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * 16000)
        calls = []

        def decode(command, log_path):
            calls.append(command)
            time.sleep(0.01)
            target = Path(command[command.index("--output-file") + 1])
            target.with_suffix(".json").write_text(json.dumps({"transcription": [
                {"text": "hello", "offsets": {"from": 100, "to": 500}},
            ]}))
            log_path.write_text("ok")
            return {"mode": "default"}

        def invoke(alias, *, language="ru", force=False, decoder=decode, extra=(), cache_root=None):
            base = root / alias
            return cache.materialize([
                str(executable), "--model", str(model), "--language", language,
                "--output-file", str(base), "--file", str(audio),
                *extra,
            ], base, force=force, decode=decoder, cache_root=cache_root)

        # Alias/window/source names and parallel callers cannot multiply decoding.
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(invoke, [f"alias_{i}" for i in range(6)]))
        assert len(calls) == 1, calls
        assert sum(not row["cache_hit"] for row in results) == 1
        assert len({row["decode_key"] for row in results}) == 1
        assert len({(root / f"alias_{i}.json").read_bytes() for i in range(6)}) == 1
        key = results[0]["decode_key"]
        entry = root / ".decode-cache-v1" / key
        Path(results[0]["cached_output_base"]).with_suffix(".json").write_text("{broken")
        assert not invoke("corrupt")["cache_hit"]
        assert len(calls) == 2
        corrupted = json.loads((entry / "completion.json").read_text())
        corrupted["config"]["model_sha256"] = "another model"
        (entry / "completion.json").write_text(json.dumps(corrupted))
        assert not invoke("corrupt_provenance")["cache_hit"]
        assert len(calls) == 3
        assert not invoke("language", language="en")["cache_hit"]
        model.write_bytes(b"model-v2")
        assert not invoke("model")["cache_hit"]
        executable.write_bytes(b"whisper-v2")
        assert not invoke("binary")["cache_hit"]
        assert not invoke("forced", force=True)["cache_hit"]
        with patch.dict(cache.os.environ, {"GGML_TEST_CONFIG": "different"}):
            assert not invoke("environment")["cache_hit"]
        before = len(calls)

        def fail(command, log):
            Path(command[command.index("--output-file") + 1]).with_suffix(".json").write_text("{}")
            raise RuntimeError("interrupted decode")

        try:
            invoke("failed", force=True, decoder=fail)
            raise AssertionError("failed decode succeeded")
        except RuntimeError:
            pass
        assert not (root / "failed.json").exists()
        assert invoke("after_failure")["cache_hit"], "failed retry destroyed the valid cache"
        assert len(calls) == before
        # A failed manifest replacement leaves the old generation usable.
        with patch.object(cache.artifacts, "atomic_write_json", side_effect=OSError("fixture interrupted publication")):
            try:
                invoke("publish_failure", force=True)
            except OSError:
                pass
            else:
                raise AssertionError("publication failure was ignored")
        assert invoke("after_publish_failure")["cache_hit"]
        shared = root / "session-cache"
        assert not invoke("current/clip", cache_root=shared)["cache_hit"]
        assert invoke("shadow/clip", cache_root=shared)["cache_hit"]
        assert not invoke("different-session/clip", cache_root=root / "other-session-cache")["cache_hit"]
        prompt = root / "prompt.txt"
        prompt.write_text("first prompt")
        prompted = invoke("prompt_one", extra=("--prompt-file", str(prompt)))
        assert not prompted["cache_hit"]
        prompt.write_text("second prompt")
        assert not invoke("prompt_two", extra=("--prompt-file", str(prompt)))["cache_hit"]
        current = invoke("lock_owner")
        lock_path = root / ".decode-cache-v1" / (current["decode_key"] + ".lock")
        with lock_path.open("a") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            with patch.dict(cache.os.environ, {DEADLINE_ENV: str(time.time() + 0.1)}):
                try:
                    invoke("lock_waiter")
                except ActionStopped as error:
                    assert error.returncode == 75
                else:
                    raise AssertionError("waiter ignored the deadline")
        assert invoke("after_lock_wait")["cache_hit"]
        original_config = cache.decode_config
        configurations = 0

        def changed_during_wait(command, pcm):
            nonlocal configurations
            configurations += 1
            config = original_config(command, pcm)
            if configurations > 1:
                config["model_sha256"] = "changed while waiting"
            return config

        with patch.object(cache, "decode_config", side_effect=changed_during_wait):
            try:
                invoke("changed_during_wait")
            except ValueError as error:
                assert "while waiting" in str(error)
            else:
                raise AssertionError("cache used inputs fingerprinted before the lock wait")
        assert not (root / "changed_during_wait.json").exists()

        def cpu_fallback(command, log):
            decode(command, log)
            return {"mode": "cpu_fallback"}

        fallback_root = root / "fallback-cache"
        assert not invoke("fallback", decoder=cpu_fallback, cache_root=fallback_root)["cache_hit"]
        assert not invoke("after_fallback", cache_root=fallback_root)["cache_hit"]
        assert invoke("after_success", cache_root=fallback_root)["cache_hit"]
        for bad in (float("nan"), float("inf"), -1, True):
            assert not cache.valid_decode({"transcription": [{"text": "hello", "offsets": {"from": bad, "to": 1}}]})
        with wave.open(str(audio), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x02\x00" * 16000)
        assert not invoke("new_audio")["cache_hit"]
        # A valid-looking legacy alias JSON has no model/config provenance.
        (root / "legacy.json").write_text('{"transcription": [{"text": "wrong"}]}')
        assert invoke("legacy")["cache_hit"]
        assert "wrong" not in (root / "legacy.json").read_text()

        spec = importlib.util.spec_from_file_location("micro_cache_transcriber", Path(__file__).with_name("transcribe-simple-whispercpp.py"))
        transcribe = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = transcribe
        spec.loader.exec_module(transcribe)
        first, rows = transcribe.read_micro_reasr_text(root / "legacy.json", 10000, 10000, 11000)
        second, shifted = transcribe.read_micro_reasr_text(root / "legacy.json", 20000, 20000, 21000)
        assert first == second == "hello"
        assert rows[0]["start_ms"] == 10100 and shifted[0]["start_ms"] == 20100
        # Padding changes PCM and global-offset rebinding, never cached absolute times.
        assert transcribe.read_micro_reasr_text(root / "legacy.json", 19600, 19500, 20500)[1][0]["start_ms"] == 19700
        spec = importlib.util.spec_from_file_location("micro_cache_replay", Path(__file__).with_name("check-micro-asr-cache-replay.py"))
        replay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(replay)
        session = root / "opportunity"
        clip_dir = session / "derived/transcript-simple/whisper-cpp/timeline-repair-shadow_v2/micro_reasr"
        clip_dir.mkdir(parents=True)
        for alias in ("normal", "wide"):
            (clip_dir / f"{alias}.wav").write_bytes(audio.read_bytes())
            (clip_dir / f"{alias}.json").write_bytes((root / "legacy.json").read_bytes())
        report, groups = replay.inspect_clips(session)
        assert report["clips"] == 2 and report["unique_pcm"] == 1 and len(groups) == 1
        assert report["identical_saved_decode_groups"] == 1
        (clip_dir / "wide.json").write_text('{"transcription": [{"text": "changed", "offsets": {"from": 100, "to": 500}}]}')
        assert replay.inspect_clips(session)[0]["conflicting_saved_decode_groups"] == 1
        (clip_dir / "wide.json").unlink()
        assert replay.inspect_clips(session)[0]["missing_or_invalid_decode_groups"] == 1
    print("micro-ASR content cache checks ok")


if __name__ == "__main__":
    main()
