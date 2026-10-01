"""Bounded exact-silence evidence for a read view, never a speech/word aligner."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

MIN_SILENCE_SEC = 2.0
MAX_WINDOW_SEC = 120.0


def fingerprint(path: Path, session: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path.relative_to(session)), "exists": path.is_file()}
    if path.is_file():
        try:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            result.update(bytes=path.stat().st_size, sha256=digest.hexdigest())
        except OSError as error:
            result["error"] = type(error).__name__
    return result


def bounds(row: dict[str, Any]) -> tuple[float, float] | None:
    start, end = row.get("start"), row.get("end")
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in (start, end)):
        return None
    return (start, end) if 0 <= start < end else None


def inspect(session: Path, utterances: list[dict[str, Any]]) -> dict[str, Any]:
    """Seek only in the canonical single remote CAF, preserving the session clock.

    Any nonzero sample on any channel stops the silence proof. Quiet speech,
    opposite-phase stereo and background noise must not be treated as silence.
    """
    audio = session / "audio/remote/000001.caf"
    paths = sorted((session / "audio/remote").glob("*.caf"))
    payload: dict[str, Any] = {"schema": "murmurmark.acoustic_timing_read_view/v1",
                               "status": "unavailable", "audio": fingerprint(audio, session),
                               "source_files": [str(p.relative_to(session)) for p in paths],
                               "min_silence_sec": MIN_SILENCE_SEC, "max_window_sec": MAX_WINDOW_SEC,
                               "utterances": {}}
    if paths != [audio]:
        payload["reason"] = "single_canonical_remote_required"
        return payload
    if payload["audio"].get("error"):
        payload["reason"] = "audio_fingerprint_unavailable"
        return payload
    try:
        import numpy as np
        import soundfile as sf

        with sf.SoundFile(audio) as handle:
            rate = handle.samplerate
            payload.update(status="checked", sample_rate=rate, frames=len(handle))
            for row in utterances:
                interval = bounds(row)
                if row.get("role") != "remote" or not interval or not row.get("id"):
                    continue
                start, end = interval
                source = bounds({"start": row.get("source_start"), "end": row.get("source_end")})
                if source is None or source[0] > start:
                    continue
                # Earlier speech in the ASR source window invalidates a prefix-silence
                # proof, even if the selected (token-derived) start lies in silence.
                source_start, source_end = source
                stop = min(max(end, source_end), source_start + MAX_WINDOW_SEC)
                begin = math.floor(source_start * rate)
                last = min(len(handle), math.floor(stop * rate))
                if last - begin < MIN_SILENCE_SEC * rate:
                    continue
                handle.seek(begin)
                offset = begin
                first = None
                invalid = False
                while offset < last:
                    block = handle.read(min(rate, last - offset), dtype="float64", always_2d=True)
                    if not len(block) or not np.isfinite(block).all():
                        invalid = True
                        break
                    active = np.flatnonzero(np.any(block != 0, axis=1))
                    if len(active):
                        first = offset + int(active[0])
                        break
                    offset += len(block)
                if invalid or first is not None and first / rate - start < MIN_SILENCE_SEC:
                    continue
                payload["utterances"][str(row["id"])] = {
                    "status": "needs_review", "scope": "timing",
                    "reason": "remote_timestamp_in_digital_silence" if first is not None else "remote_window_digital_silence",
                    "original_interval": {"start": interval[0], "end": interval[1]},
                    "checked_interval": {"start": begin / rate, "end": last / rate},
                    "sound_not_before": first / rate if first is not None else None,
                    "first_nonzero_frame": first,
                    "word_alignment_established": False,
                }
    except (OSError, RuntimeError, ImportError, ValueError) as error:
        payload.update(status="unavailable", reason=f"audio_read_failed:{type(error).__name__}", utterances={})
    if payload["audio"] != fingerprint(audio, session):
        payload.update(status="unavailable", reason="audio_changed_during_analysis", utterances={})
    return payload
