#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts/causal-canonical-mic-asr.py"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fake_whisper(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import hashlib, json, sys
from pathlib import Path
args = sys.argv[1:]
output = Path(args[args.index('--output-file') + 1])
source = Path(args[args.index('--file') + 1])
text = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
payload = {
    'params': {'language': args[args.index('--language') + 1]},
    'transcription': [{
        'text': text,
        'offsets': {'from': 1000, 'to': 2000},
        'tokens': [{'text': text, 'offsets': {'from': 1000, 'to': 2000}}],
    }],
}
output.with_suffix('.json').write_text(json.dumps(payload, sort_keys=True) + '\\n')
output.with_suffix('.txt').write_text(text + '\\n')
output.with_suffix('.vtt').write_text('WEBVTT\\n')
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def run(command: list[str]) -> None:
    subprocess.run(
        command,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def audio(seconds: int = 66, rate: int = 16_000) -> np.ndarray:
    index = np.arange(seconds * rate, dtype=np.float64)
    return (0.12 * np.sin(2 * math.pi * 440.0 * index / rate)).astype(np.float32)


def export_raw(session: Path) -> None:
    target = session / "derived/asr/mic.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(session / "audio/mic/000001.caf"),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(target),
        ]
    )


def build_session(root: Path, name: str, *, selected: str = "raw_fallback") -> tuple[Path, Path, Path]:
    session = root / name
    raw = session / "audio/mic/000001.caf"
    raw.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(raw), audio(), 16_000, format="CAF", subtype="FLOAT")
    remote = session / "audio/remote/000001.caf"
    remote.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(remote), audio() * 0.5, 16_000, format="CAF", subtype="FLOAT")
    write_json(session / "session.json", {"schema": "murmurmark.session/v1", "session_id": name})
    write_json(
        session / "derived/preprocess/echo/echo_suppression_report.json",
        {
            "schema": "murmurmark.echo_suppression_report/v1",
            "decision": {"accepted_for_asr": selected != "raw_fallback"},
        },
    )
    export_raw(session)
    if selected != "raw_fallback":
        # A different finalized input represents a post-Echo whole-session branch.
        sf.write(str(session / "derived/asr/mic.wav"), np.zeros_like(audio()), 16_000, subtype="PCM_16")
    model = root / f"{name}.model.bin"
    model.write_bytes(b"model-v1")
    whisper = root / f"{name}.whisper-cli"
    fake_whisper(whisper)
    return session, model, whisper


def invoke(session: Path, model: Path, whisper: Path, *extra: str) -> dict[str, Any]:
    run(
        [
            sys.executable,
            str(TOOL),
            str(session),
            "--model",
            str(model),
            "--whisper-cli",
            str(whisper),
            "--threads",
            "2",
            "--decode-exact",
            *extra,
        ]
    )
    return json.loads(
        (
            session
            / "derived/experiments/live-shadow-v1/authoritative-mic-asr/report.json"
        ).read_text(encoding="utf-8")
    )


def raw_hash(session: Path) -> str:
    return hashlib.sha256((session / "audio/mic/000001.caf").read_bytes()).hexdigest()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-causal-canonical-mic-") as temporary:
        root = Path(temporary)

        exact, model, whisper = build_session(root, "exact")
        before = raw_hash(exact)
        report = invoke(exact, model, whisper)
        assert report["status"] == "completed", report
        assert report["selected_profile"] == "raw_fallback", report
        assert report["summary"]["windows_total"] == 2, report
        assert report["summary"]["exact_windows"] == 2, report
        assert report["summary"]["proofs_published"] == 2, report
        assert report["decision"] == "CANDIDATE_REQUIRES_RECORDING_TIME_EVIDENCE", report
        assert report["safety"]["raw_capture_unchanged"] is True, report
        assert raw_hash(exact) == before

        proof = (
            exact
            / "derived/experiments/live-shadow-v1/authoritative-mic-asr/chunks/000001/mic.proof.json"
        )
        proof_hash = hashlib.sha256(proof.read_bytes()).hexdigest()
        repeated = invoke(exact, model, whisper)
        assert repeated["summary"]["chunks_reused"] == 2, repeated
        assert hashlib.sha256(proof.read_bytes()).hexdigest() == proof_hash

        corrupted = proof.with_name("mic.json")
        corrupted.write_text("{}\n", encoding="utf-8")
        repaired = invoke(exact, model, whisper)
        assert repaired["summary"]["chunks_reused"] == 1, repaired
        assert isinstance(json.loads(corrupted.read_text())["transcription"], list)

        interrupted, interrupted_model, interrupted_whisper = build_session(root, "interrupted")
        first = invoke(
            interrupted,
            interrupted_model,
            interrupted_whisper,
            "--interrupt-after-chunks",
            "1",
        )
        assert first["status"] == "interrupted_fail_open", first
        assert first["summary"]["proofs_published"] == 1, first
        resumed = invoke(interrupted, interrupted_model, interrupted_whisper)
        assert resumed["status"] == "completed", resumed
        assert resumed["summary"]["proofs_published"] == 2, resumed
        assert resumed["summary"]["chunks_reused"] == 1, resumed

        bounded, bounded_model, bounded_whisper = build_session(root, "bounded")
        limited = invoke(
            bounded,
            bounded_model,
            bounded_whisper,
            "--max-decode-chunks",
            "1",
        )
        assert limited["summary"]["exact_windows"] == 2, limited
        assert limited["summary"]["proofs_published"] == 1, limited
        assert any(row.get("decode_status") == "bounded_limit" for row in json.loads("[" + ",".join(
            line for line in (
                bounded
                / "derived/experiments/live-shadow-v1/authoritative-mic-asr/windows.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ) + "]")), limited

        missing_model, absent_model, missing_model_whisper = build_session(root, "missing-model")
        absent_model.unlink()
        no_model = invoke(missing_model, absent_model, missing_model_whisper)
        assert no_model["status"] == "completed", no_model
        assert no_model["summary"]["exact_windows"] == 2, no_model
        assert no_model["summary"]["proofs_published"] == 0, no_model

        post_echo, post_model, post_whisper = build_session(root, "post-echo", selected="local_fir")
        mismatch = invoke(post_echo, post_model, post_whisper)
        assert mismatch["selected_profile"] == "local_fir_role_masked", mismatch
        assert mismatch["summary"]["exact_windows"] == 0, mismatch
        assert mismatch["summary"]["proofs_published"] == 0, mismatch
        assert mismatch["decision"] == "DO_NOT_PROMOTE", mismatch

        missing, missing_model, missing_whisper = build_session(root, "missing")
        (missing / "derived/asr/mic.wav").unlink()
        failed_open = invoke(missing, missing_model, missing_whisper)
        assert failed_open["status"] == "fallback", failed_open
        assert failed_open["reason"] == "canonical_mic_missing", failed_open
        assert failed_open["decision"] == "DO_NOT_PROMOTE", failed_open

    print("causal canonical mic ASR checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
