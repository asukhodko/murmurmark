#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import importlib.util
import json
import math
import os
import shutil
import subprocess
import warnings
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import librosa
import numpy as np


SCHEMA_ENROLLMENT = "murmurmark.target_me_enrollment/v1"
SCHEMA_ROW = "murmurmark.target_me_audit/v1"
SCHEMA_SUMMARY = "murmurmark.target_me_summary/v1"
SCHEMA_CORPUS = "murmurmark.target_me_corpus_report/v1"
SCRIPT_VERSION = "0.2.0"
SAMPLE_RATE = 16000
EPS = 1e-9
DEFAULT_WAVLM_MODEL = Path.home() / ".local/share/murmurmark/models/target-me/wavlm-base-plus-sv"
OPTIONAL_LOCAL_BACKENDS = {
    "torch": "local tensor runtime",
    "transformers": "local model loading",
    "torchaudio": "audio model utilities",
    "speechbrain": "speaker embeddings / separation candidates",
    "pyannote": "speaker diarization candidates",
    "resemblyzer": "speaker embeddings",
    "asteroid": "source separation candidates",
    "wespeaker": "speaker embeddings",
    "faster_whisper": "local ASR judge, not speaker identity",
}
AUTO_PROFILE_ORDER = [
    "reviewed_v1",
    "order_repair_v1",
    "local_recall_repair_v1",
    "agent_reviewed_v1",
    "audit_cleanup_v7",
    "suggested_review_v1",
    "audit_cleanup_v6",
    "audit_cleanup_v5",
    "audit_cleanup_v4",
    "audit_cleanup_v3",
    "audit_cleanup_v2",
    "audit_cleanup_v1",
    "shadow_v2",
    "current",
]
ME_LABELS = {"me", "mic"}
REMOTE_LABELS = {"remote", "colleagues"}
TARGET_LABELS = {
    "target_me_confirmed",
    "target_me_possible",
    "target_me_absent_remote_like",
    "target_me_absent",
    "target_me_ambiguous",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a shadow Target-Me evidence report from local-only enrollment speech and review clips."
    )
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--profile", default="auto")
    parser.add_argument(
        "--method",
        default="auto",
        choices=["auto", "mfcc_voiceprint", "mfcc_contrastive", "resemblyzer_dvector", "wavlm_xvector"],
        help="Embedding backend. auto uses local WavLM, then resemblyzer, then mfcc_contrastive.",
    )
    parser.add_argument(
        "--wavlm-model",
        type=Path,
        default=None,
        help="Local microsoft/wavlm-base-plus-sv directory. Also reads MURMURMARK_TARGET_ME_WAVLM_MODEL.",
    )
    parser.add_argument("--out-dir-name", default="target-me")
    parser.add_argument("--corpus-out-dir", type=Path, default=Path("sessions/_reports/target-me"))
    parser.add_argument("--max-enrollment-segments", type=int, default=40)
    parser.add_argument("--max-enrollment-total-sec", type=float, default=180.0)
    parser.add_argument("--max-negative-enrollment-segments", type=int, default=40)
    parser.add_argument("--min-enrollment-sec", type=float, default=1.2)
    parser.add_argument("--max-enrollment-sec", type=float, default=14.0)
    parser.add_argument("--min-enrollment-local-ratio", type=float, default=0.65)
    parser.add_argument("--max-enrollment-remote-active-ratio", type=float, default=0.20)
    parser.add_argument("--max-items", type=int, default=80)
    parser.add_argument("--padding-sec", type=float, default=0.25)
    parser.add_argument("--skip-build-pack", action="store_true")
    parser.add_argument("--write-clips", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def progress(args: argparse.Namespace, message: str) -> None:
    if args.progress:
        print(f"target_me: {message}", flush=True)


def local_backend_probe() -> dict[str, Any]:
    modules: dict[str, Any] = {}
    for name, purpose in OPTIONAL_LOCAL_BACKENDS.items():
        try:
            available = importlib.util.find_spec(name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            available = False
        modules[name] = {"available": available, "purpose": purpose}
    wavlm_ready = bool(
        modules.get("transformers", {}).get("available")
        and modules.get("torch", {}).get("available")
        and DEFAULT_WAVLM_MODEL.exists()
        and (DEFAULT_WAVLM_MODEL / "config.json").exists()
    )
    resemblyzer_ready = bool(modules.get("resemblyzer", {}).get("available"))
    return {
        "modules": modules,
        "speaker_embedding_ready": wavlm_ready or resemblyzer_ready,
        "wavlm_ready": wavlm_ready,
        "resemblyzer_ready": resemblyzer_ready,
        "separation_candidate_available": any(
            modules.get(name, {}).get("available") for name in ("speechbrain", "asteroid")
        ),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def resolve_profile(session: Path, requested: str) -> str:
    if requested != "auto":
        return requested
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    for profile in AUTO_PROFILE_ORDER:
        if (resolved / f"clean_dialogue{suffix(profile)}.json").exists():
            return profile
    return "current"


def clean_dialogue_path(session: Path, profile: str) -> Path:
    return session / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix(profile)}.json"


def source_audio(session: Path) -> dict[str, Path]:
    return {
        "mic_raw": session / "audio/mic/000001.caf",
        "remote": session / "audio/remote/000001.caf",
        "mic_clean": session / "derived/preprocess/audio/mic_clean_local_fir.wav",
        "mic_role_masked": session / "derived/preprocess/audio/mic_role_masked_for_asr.wav",
    }


def best_enrollment_source(session: Path) -> tuple[str, Path]:
    sources = source_audio(session)
    for key in ("mic_clean", "mic_role_masked", "mic_raw"):
        if sources[key].exists():
            return key, sources[key]
    return "mic_raw", sources["mic_raw"]


def extract_wav(source: Path, output: Path, start: float, duration: float) -> bool:
    if not source.exists() or duration <= 0:
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-t",
        f"{max(0.05, duration):.3f}",
        "-i",
        str(source),
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        str(output),
    ]
    result = subprocess.run(command, check=False)
    return result.returncode == 0 and output.exists() and output.stat().st_size > 0


def role_of(row: dict[str, Any]) -> str:
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    track = str(row.get("source_track") or "").lower()
    if role in ME_LABELS or track == "mic":
        return "me"
    if role in REMOTE_LABELS or track == "remote":
        return "remote"
    if "colleague" in role:
        return "remote"
    return role or track


def text_token_count(text: Any) -> int:
    return len([token for token in str(text or "").replace("ё", "е").lower().split() if len(token.strip(".,!?;:")) > 1])


def load_speaker_state(session: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(session / "derived/preprocess/echo/speaker_state.jsonl")
    return sorted(rows, key=lambda row: safe_float(row.get("start")))


def interval_state_features(rows: list[dict[str, Any]], start: float, end: float) -> dict[str, Any]:
    duration = max(0.0, end - start)
    if duration <= 0:
        return {
            "local_only_ratio": 0.0,
            "remote_only_ratio": 0.0,
            "double_talk_ratio": 0.0,
            "silence_ratio": 0.0,
            "remote_active_ratio": 0.0,
            "local_score_proxy": 0.0,
            "remote_db_median": None,
            "mic_db_median": None,
        }
    totals = Counter()
    remote_db: list[float] = []
    mic_db: list[float] = []
    for row in rows:
        row_start = safe_float(row.get("start"))
        row_end = safe_float(row.get("end"), row_start)
        overlap = min(end, row_end) - max(start, row_start)
        if overlap <= 0:
            continue
        state = str(row.get("state") or "unknown")
        totals[state] += overlap
        if row.get("remote_db") is not None:
            remote_db.append(safe_float(row.get("remote_db"), -120.0))
        if row.get("mic_db") is not None:
            mic_db.append(safe_float(row.get("mic_db"), -120.0))
    local_only = totals["local_only"] / duration
    remote_only = totals["remote_only"] / duration
    double_talk = totals["double_talk"] / duration
    silence = totals["silence"] / duration
    remote_active = remote_only + double_talk
    local_score_proxy = min(1.0, local_only + 0.5 * double_talk)
    return {
        "local_only_ratio": round(local_only, 6),
        "remote_only_ratio": round(remote_only, 6),
        "double_talk_ratio": round(double_talk, 6),
        "silence_ratio": round(silence, 6),
        "remote_active_ratio": round(remote_active, 6),
        "local_score_proxy": round(local_score_proxy, 6),
        "remote_db_median": round(float(np.median(remote_db)), 3) if remote_db else None,
        "mic_db_median": round(float(np.median(mic_db)), 3) if mic_db else None,
    }


def load_audio_embedding(path: Path) -> tuple[np.ndarray | None, dict[str, Any]]:
    try:
        audio, sr = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    except Exception as error:
        return None, {"error": str(error), "path": str(path)}
    if audio.size < int(0.25 * SAMPLE_RATE):
        return None, {"error": "too_short", "path": str(path), "duration_sec": round(audio.size / SAMPLE_RATE, 3)}
    audio, _ = librosa.effects.trim(audio, top_db=35)
    if audio.size < int(0.25 * SAMPLE_RATE):
        return None, {"error": "too_silent_after_trim", "path": str(path)}
    audio = audio.astype(np.float32)
    peak = float(np.max(np.abs(audio)) + EPS)
    audio = audio / peak
    duration = audio.size / sr
    try:
        mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=20, n_fft=512, hop_length=160)
        delta = librosa.feature.delta(mfcc)
        centroid = librosa.feature.spectral_centroid(y=audio, sr=sr, n_fft=512, hop_length=160)
        bandwidth = librosa.feature.spectral_bandwidth(y=audio, sr=sr, n_fft=512, hop_length=160)
        rolloff = librosa.feature.spectral_rolloff(y=audio, sr=sr, n_fft=512, hop_length=160)
        zcr = librosa.feature.zero_crossing_rate(audio, frame_length=512, hop_length=160)
        rms = librosa.feature.rms(y=audio, frame_length=512, hop_length=160)
        features = [
            np.mean(mfcc, axis=1),
            np.std(mfcc, axis=1),
            np.mean(delta, axis=1),
            np.std(delta, axis=1),
            np.mean(centroid, axis=1),
            np.std(centroid, axis=1),
            np.mean(bandwidth, axis=1),
            np.std(bandwidth, axis=1),
            np.mean(rolloff, axis=1),
            np.std(rolloff, axis=1),
            np.mean(zcr, axis=1),
            np.std(zcr, axis=1),
            np.mean(rms, axis=1),
            np.std(rms, axis=1),
        ]
        pitch_features = pitch_stats(audio, sr)
        vector = np.concatenate(features + [pitch_features]).astype(np.float64)
    except Exception as error:
        return None, {"error": str(error), "path": str(path), "duration_sec": round(duration, 3)}
    vector = np.nan_to_num(vector, nan=0.0, posinf=0.0, neginf=0.0)
    norm = float(np.linalg.norm(vector))
    if norm <= EPS:
        return None, {"error": "zero_embedding", "path": str(path), "duration_sec": round(duration, 3)}
    return vector / norm, {"path": str(path), "duration_sec": round(duration, 3), "sample_rate": sr}


class EmbeddingBackend:
    method = "mfcc_voiceprint_v0"

    def ready(self) -> tuple[bool, str]:
        return True, "ok"

    def embed(self, path: Path) -> tuple[np.ndarray | None, dict[str, Any]]:
        embedding, info = load_audio_embedding(path)
        info["backend"] = self.method
        return embedding, info


class WavLMXVectorBackend(EmbeddingBackend):
    method = "wavlm_xvector_v0"

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self._processor: Any | None = None
        self._model: Any | None = None
        self._torch: Any | None = None

    def ready(self) -> tuple[bool, str]:
        if not self.model_path.exists():
            return False, f"model path not found: {self.model_path}"
        if not (self.model_path / "config.json").exists():
            return False, f"config.json not found: {self.model_path}"
        has_weights = any((self.model_path / name).exists() for name in ("model.safetensors", "pytorch_model.bin"))
        if not has_weights:
            return False, f"model weights not found: {self.model_path}"
        return True, "ok"

    def _load(self) -> None:
        if self._model is not None and self._processor is not None:
            return
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        try:
            import torch
            from transformers import AutoModelForAudioXVector, AutoProcessor
        except ImportError as error:
            raise RuntimeError("missing torch/transformers AutoModelForAudioXVector") from error
        self._torch = torch
        self._processor = AutoProcessor.from_pretrained(str(self.model_path), local_files_only=True)
        self._model = AutoModelForAudioXVector.from_pretrained(str(self.model_path), local_files_only=True)
        self._model.eval()

    def embed(self, path: Path) -> tuple[np.ndarray | None, dict[str, Any]]:
        ready, reason = self.ready()
        if not ready:
            return None, {"backend": self.method, "error": reason, "path": str(path)}
        try:
            audio, sr = librosa.load(path, sr=SAMPLE_RATE, mono=True)
        except Exception as error:
            return None, {"backend": self.method, "error": str(error), "path": str(path)}
        if audio.size < int(0.35 * SAMPLE_RATE):
            return None, {"backend": self.method, "error": "too_short", "path": str(path)}
        audio, _ = librosa.effects.trim(audio, top_db=35)
        if audio.size < int(0.35 * SAMPLE_RATE):
            return None, {"backend": self.method, "error": "too_silent_after_trim", "path": str(path)}
        try:
            self._load()
            assert self._processor is not None
            assert self._model is not None
            assert self._torch is not None
            inputs = self._processor(audio, sampling_rate=SAMPLE_RATE, return_tensors="pt", padding=True)
            with self._torch.no_grad():
                outputs = self._model(**inputs)
            embedding = getattr(outputs, "embeddings", None)
            if embedding is None:
                values = list(outputs.values()) if hasattr(outputs, "values") else list(outputs)
                embedding = values[-1]
            vector = embedding.detach().cpu().numpy()[0].astype(np.float64)
        except Exception as error:
            return None, {"backend": self.method, "error": str(error), "path": str(path)}
        vector = np.nan_to_num(vector, nan=0.0, posinf=0.0, neginf=0.0)
        norm = float(np.linalg.norm(vector))
        if norm <= EPS:
            return None, {"backend": self.method, "error": "zero_embedding", "path": str(path)}
        return vector / norm, {
            "backend": self.method,
            "path": str(path),
            "duration_sec": round(audio.size / SAMPLE_RATE, 3),
            "sample_rate": SAMPLE_RATE,
            "model": str(self.model_path),
        }


class ResemblyzerDVectorBackend(EmbeddingBackend):
    method = "resemblyzer_dvector_v0"

    def __init__(self) -> None:
        self._encoder: Any | None = None
        self._preprocess_wav: Any | None = None

    def ready(self) -> tuple[bool, str]:
        try:
            importlib.util.find_spec("resemblyzer")
        except (ImportError, ModuleNotFoundError, ValueError):
            return False, "resemblyzer not installed"
        return True, "ok"

    def _load(self) -> None:
        if self._encoder is not None and self._preprocess_wav is not None:
            return
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="pkg_resources is deprecated.*")
                from resemblyzer import VoiceEncoder, preprocess_wav
        except ImportError as error:
            raise RuntimeError("missing resemblyzer") from error
        self._preprocess_wav = preprocess_wav
        with redirect_stdout(io.StringIO()):
            self._encoder = VoiceEncoder(device="cpu")

    def embed(self, path: Path) -> tuple[np.ndarray | None, dict[str, Any]]:
        ready, reason = self.ready()
        if not ready:
            return None, {"backend": self.method, "error": reason, "path": str(path)}
        try:
            probe, _ = librosa.load(path, sr=SAMPLE_RATE, mono=True)
            if probe.size < int(0.35 * SAMPLE_RATE):
                return None, {"backend": self.method, "error": "too_short", "path": str(path)}
            probe_peak = float(np.max(np.abs(probe))) if probe.size else 0.0
            probe_rms = float(np.sqrt(np.mean(np.square(probe, dtype=np.float64)))) if probe.size else 0.0
            if not np.isfinite(probe_rms) or probe_peak <= EPS or probe_rms <= EPS:
                return None, {
                    "backend": self.method,
                    "error": "silence",
                    "path": str(path),
                    "duration_sec": round(probe.size / SAMPLE_RATE, 3),
                    "sample_rate": SAMPLE_RATE,
                }
            self._load()
            assert self._encoder is not None
            assert self._preprocess_wav is not None
            wav = self._preprocess_wav(path)
            if wav.size < int(0.35 * SAMPLE_RATE):
                return None, {"backend": self.method, "error": "too_short", "path": str(path)}
            vector = self._encoder.embed_utterance(wav).astype(np.float64)
        except Exception as error:
            return None, {"backend": self.method, "error": str(error), "path": str(path)}
        vector = np.nan_to_num(vector, nan=0.0, posinf=0.0, neginf=0.0)
        norm = float(np.linalg.norm(vector))
        if norm <= EPS:
            return None, {"backend": self.method, "error": "zero_embedding", "path": str(path)}
        return vector / norm, {
            "backend": self.method,
            "path": str(path),
            "duration_sec": round(wav.size / SAMPLE_RATE, 3),
            "sample_rate": SAMPLE_RATE,
            "model": "resemblyzer.VoiceEncoder",
        }


def wavlm_model_path(args: argparse.Namespace) -> Path:
    if args.wavlm_model is not None:
        return args.wavlm_model.expanduser()
    env_value = os.environ.get("MURMURMARK_TARGET_ME_WAVLM_MODEL")
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_WAVLM_MODEL


def resolve_embedding_backend(args: argparse.Namespace) -> tuple[EmbeddingBackend, dict[str, Any]]:
    if args.method == "resemblyzer_dvector":
        resemblyzer = ResemblyzerDVectorBackend()
        ready, reason = resemblyzer.ready()
        return resemblyzer, {
            "requested": args.method,
            "selected": resemblyzer.method,
            "resemblyzer_ready": ready,
            "wavlm_ready": False,
            "reason": reason,
        }
    if args.method in {"auto", "wavlm_xvector"}:
        wavlm = WavLMXVectorBackend(wavlm_model_path(args))
        ready, reason = wavlm.ready()
        if ready:
            return wavlm, {"requested": args.method, "selected": wavlm.method, "wavlm_ready": True, "reason": reason}
        if args.method == "wavlm_xvector":
            return wavlm, {"requested": args.method, "selected": wavlm.method, "wavlm_ready": False, "reason": reason}
    if args.method == "auto":
        resemblyzer = ResemblyzerDVectorBackend()
        ready, reason = resemblyzer.ready()
        if ready:
            return resemblyzer, {
                "requested": args.method,
                "selected": resemblyzer.method,
                "wavlm_ready": False,
                "resemblyzer_ready": True,
                "reason": "wavlm model unavailable; using resemblyzer d-vector backend",
            }
    mfcc = EmbeddingBackend()
    selected = "mfcc_contrastive_v0" if args.method in {"auto", "mfcc_contrastive"} else mfcc.method
    return mfcc, {
        "requested": args.method,
        "selected": selected,
        "wavlm_ready": False,
        "reason": "wavlm model unavailable; using mfcc_contrastive baseline"
        if args.method == "auto"
        else "mfcc_contrastive requested"
        if args.method == "mfcc_contrastive"
        else "mfcc requested",
    }


def pitch_stats(audio: np.ndarray, sr: int) -> np.ndarray:
    try:
        values = librosa.yin(audio, fmin=70, fmax=450, sr=sr, frame_length=1024, hop_length=160)
        voiced = values[np.isfinite(values)]
        voiced = voiced[(voiced >= 70) & (voiced <= 450)]
        if voiced.size == 0:
            return np.zeros(4, dtype=np.float64)
        return np.asarray(
            [
                float(np.median(voiced)),
                float(np.std(voiced)),
                float(np.percentile(voiced, 10)),
                float(np.percentile(voiced, 90)),
            ],
            dtype=np.float64,
        )
    except Exception:
        return np.zeros(4, dtype=np.float64)


def cosine(left: np.ndarray | None, right: np.ndarray | None) -> float:
    if left is None or right is None:
        return 0.0
    return float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right) + EPS))


def make_centroid(embeddings: list[np.ndarray]) -> np.ndarray | None:
    if not embeddings:
        return None
    matrix = np.vstack(embeddings)
    centroid = np.median(matrix, axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm <= EPS:
        return None
    return centroid / norm


def model_score(embedding: np.ndarray | None, model: dict[str, Any]) -> dict[str, float]:
    positive = model.get("positive_centroid")
    negative = model.get("negative_centroid")
    positive_score = cosine(embedding, positive) if isinstance(positive, np.ndarray) else 0.0
    negative_score = cosine(embedding, negative) if isinstance(negative, np.ndarray) else 0.0
    scoring = str(model.get("scoring") or "cosine")
    target = positive_score - negative_score if scoring == "contrastive" else positive_score
    return {
        "target_similarity": float(target),
        "positive_similarity": float(positive_score),
        "negative_similarity": float(negative_score),
    }


def percentile(values: list[float], q: float, default: float = 0.0) -> float:
    if not values:
        return default
    return float(np.percentile(np.asarray(values, dtype=np.float64), q))


def build_enrollment(
    session: Path,
    profile: str,
    utterances: list[dict[str, Any]],
    state_rows: list[dict[str, Any]],
    out_dir: Path,
    backend: EmbeddingBackend,
    backend_status: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_name, source = best_enrollment_source(session)
    method = str(backend_status.get("selected") or backend.method)
    scoring = "contrastive" if method == "mfcc_contrastive_v0" else "cosine"
    candidates: list[dict[str, Any]] = []
    for row in utterances:
        if not isinstance(row, dict) or role_of(row) != "me":
            continue
        quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        duration = end - start
        if duration < args.min_enrollment_sec or duration > args.max_enrollment_sec:
            continue
        if quality.get("needs_review") is True:
            continue
        if text_token_count(row.get("text")) < 2:
            continue
        state = interval_state_features(state_rows, start, end)
        if state["local_only_ratio"] < args.min_enrollment_local_ratio:
            continue
        if state["remote_active_ratio"] > args.max_enrollment_remote_active_ratio:
            continue
        score = (
            state["local_only_ratio"] * 100
            - state["remote_active_ratio"] * 80
            + min(12.0, duration) * 2
            + min(12, text_token_count(row.get("text")))
        )
        candidates.append(
            {
                "utterance_id": str(row.get("id") or ""),
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_sec": round(duration, 3),
                "text": str(row.get("text") or ""),
                "state": state,
                "score": round(float(score), 3),
            }
        )
    candidates.sort(key=lambda item: (-float(item["score"]), float(item["start"])))

    selected: list[dict[str, Any]] = []
    total_sec = 0.0
    embeddings: list[np.ndarray] = []
    clips_dir = out_dir / "clips/enrollment"
    for index, item in enumerate(candidates, start=1):
        if len(selected) >= args.max_enrollment_segments:
            break
        if total_sec >= args.max_enrollment_total_sec:
            break
        start = max(0.0, float(item["start"]) - args.padding_sec)
        end = float(item["end"]) + args.padding_sec
        clip = clips_dir / f"enroll_{len(selected) + 1:04d}_{item['utterance_id']}.wav"
        if not extract_wav(source, clip, start, end - start):
            continue
        embedding, info = backend.embed(clip)
        item = dict(item)
        item["clip"] = str(clip) if args.write_clips else ""
        item["embedding_info"] = info
        item["source_audio"] = source_name
        if embedding is None:
            item["accepted"] = False
            item["reject_reason"] = info.get("error") or "embedding_failed"
            continue
        item["accepted"] = True
        selected.append(item)
        embeddings.append(embedding)
        total_sec += float(item["duration_sec"])

    negative_selected: list[dict[str, Any]] = []
    negative_embeddings: list[np.ndarray] = []
    negative_total_sec = 0.0
    if scoring == "contrastive":
        remote_source = source_audio(session)["remote"]
        remote_candidates: list[dict[str, Any]] = []
        for row in utterances:
            if not isinstance(row, dict) or role_of(row) != "remote":
                continue
            quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
            start = safe_float(row.get("start"))
            end = safe_float(row.get("end"), start)
            duration = end - start
            if duration < args.min_enrollment_sec or duration > args.max_enrollment_sec:
                continue
            if quality.get("needs_review") is True:
                continue
            if text_token_count(row.get("text")) < 2:
                continue
            score = min(12.0, duration) * 2 + min(12, text_token_count(row.get("text")))
            remote_candidates.append(
                {
                    "utterance_id": str(row.get("id") or ""),
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration_sec": round(duration, 3),
                    "text": str(row.get("text") or ""),
                    "score": round(float(score), 3),
                }
            )
        remote_candidates.sort(key=lambda item: (-float(item["score"]), float(item["start"])))
        negative_dir = out_dir / "clips/enrollment_negative"
        for item in remote_candidates[: args.max_negative_enrollment_segments]:
            start = max(0.0, float(item["start"]) - args.padding_sec)
            end = float(item["end"]) + args.padding_sec
            clip = negative_dir / f"remote_{len(negative_selected) + 1:04d}_{item['utterance_id']}.wav"
            if not extract_wav(remote_source, clip, start, end - start):
                continue
            embedding, info = backend.embed(clip)
            item = dict(item)
            item["clip"] = str(clip) if args.write_clips else ""
            item["embedding_info"] = info
            item["source_audio"] = "remote"
            if embedding is None:
                item["accepted"] = False
                item["reject_reason"] = info.get("error") or "embedding_failed"
                continue
            item["accepted"] = True
            negative_selected.append(item)
            negative_embeddings.append(embedding)
            negative_total_sec += float(item["duration_sec"])

    centroid = make_centroid(embeddings)
    negative_centroid = make_centroid(negative_embeddings)
    target_model: dict[str, Any] = {
        "positive_centroid": centroid,
        "negative_centroid": negative_centroid,
        "scoring": scoring,
    }
    calibration: dict[str, Any] = {}
    if centroid is not None:
        similarities = [model_score(vector, target_model)["target_similarity"] for vector in embeddings]
        negative_similarities = [
            model_score(vector, target_model)["target_similarity"] for vector in negative_embeddings
        ]
        calibration = {
            "similarity_to_centroid": {
                "p10": round(float(np.percentile(similarities, 10)), 6),
                "p25": round(float(np.percentile(similarities, 25)), 6),
                "p50": round(float(np.percentile(similarities, 50)), 6),
                "p75": round(float(np.percentile(similarities, 75)), 6),
                "min": round(float(np.min(similarities)), 6),
                "max": round(float(np.max(similarities)), 6),
            },
        }
        if negative_similarities:
            positive_floor = percentile(similarities, 10)
            negative_ceiling = percentile(negative_similarities, 90)
            negative_mid = percentile(negative_similarities, 75)
            calibration["negative_similarity_to_target"] = {
                "p10": round(percentile(negative_similarities, 10), 6),
                "p50": round(percentile(negative_similarities, 50), 6),
                "p75": round(negative_mid, 6),
                "p90": round(negative_ceiling, 6),
                "min": round(min(negative_similarities), 6),
                "max": round(max(negative_similarities), 6),
            }
            calibration["target_threshold"] = round((positive_floor + negative_ceiling) / 2.0, 6)
            calibration["weak_target_threshold"] = round((positive_floor + negative_mid) / 2.0, 6)
            calibration["positive_negative_margin"] = round(percentile(similarities, 50) - percentile(negative_similarities, 50), 6)
        else:
            calibration["target_threshold"] = round(max(0.45, float(np.percentile(similarities, 10)) - 0.12), 6)
            calibration["weak_target_threshold"] = round(max(0.38, float(np.percentile(similarities, 10)) - 0.20), 6)

    enrollment = {
        "schema": SCHEMA_ENROLLMENT,
        "generator": {"name": "audit-target-me", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "session": str(session),
        "session_id": session.name,
        "profile": profile,
        "method": method,
        "embedding_backend": backend_status,
        "scoring": scoring,
        "source_audio": {"name": source_name, "path": str(source), "exists": source.exists()},
        "config": {
            "sample_rate": SAMPLE_RATE,
            "max_enrollment_segments": args.max_enrollment_segments,
            "max_enrollment_total_sec": args.max_enrollment_total_sec,
            "max_negative_enrollment_segments": args.max_negative_enrollment_segments,
            "min_enrollment_sec": args.min_enrollment_sec,
            "max_enrollment_sec": args.max_enrollment_sec,
            "min_enrollment_local_ratio": args.min_enrollment_local_ratio,
            "max_enrollment_remote_active_ratio": args.max_enrollment_remote_active_ratio,
        },
        "candidate_count": len(candidates),
        "accepted_count": len(selected),
        "accepted_total_sec": round(total_sec, 3),
        "negative_accepted_count": len(negative_selected),
        "negative_accepted_total_sec": round(negative_total_sec, 3),
        "calibration": calibration,
        "segments": selected,
        "negative_segments": negative_selected,
        "status": "ready"
        if centroid is not None and len(selected) >= 3 and (scoring != "contrastive" or len(negative_selected) >= 3)
        else "insufficient_enrollment",
    }
    write_json(out_dir / "target_me_enrollment.json", enrollment)
    return enrollment, target_model


def ensure_audio_pack(session: Path, profile: str, args: argparse.Namespace) -> None:
    if args.skip_build_pack:
        return
    script = Path(__file__).resolve().parent / "build-audio-review-pack.py"
    command = [
        str(script),
        str(session),
        "--profile",
        profile,
        "--max-items",
        str(args.max_items),
    ]
    if args.write_clips:
        command.append("--write-clips")
    else:
        command.append("--no-write-clips")
    run(command)


def utterance_texts(item: dict[str, Any]) -> tuple[str, str]:
    me: list[str] = []
    remote: list[str] = []
    for row in item.get("utterances") or []:
        if not isinstance(row, dict):
            continue
        role = role_of(row)
        text = str(row.get("text") or "")
        if role == "me":
            me.append(text)
        elif role == "remote":
            remote.append(text)
    return " ".join(me).strip(), " ".join(remote).strip()


def utterance_ids(item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for row in item.get("utterances") or []:
        if isinstance(row, dict) and row.get("id"):
            ids.append(str(row["id"]))
    return list(dict.fromkeys(ids))


def me_utterance_ids(item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for row in item.get("utterances") or []:
        if isinstance(row, dict) and role_of(row) == "me" and row.get("id"):
            ids.append(str(row["id"]))
    return list(dict.fromkeys(ids))


def item_source_audit_ids(item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for context in item.get("source_contexts") or []:
        if not isinstance(context, dict):
            continue
        for key in ("source_audit_id", "id"):
            value = context.get(key)
            if value:
                ids.append(str(value))
        for nested_key in ("row", "review_item", "patch", "overlap"):
            nested = context.get(nested_key)
            if isinstance(nested, dict):
                value = nested.get("source_audit_id") or nested.get("id")
                if value:
                    ids.append(str(value))
    return list(dict.fromkeys(ids))


def existing_audit_by_id(path: Path) -> dict[str, dict[str, Any]]:
    return {str(row.get("id") or row.get("source_pack_item_id") or ""): row for row in read_jsonl(path)}


def source_reasons_priority(item: dict[str, Any]) -> int:
    reasons = [str(value) for value in item.get("source_reasons") or []]
    if any("local_recall" in reason or "lost" in reason for reason in reasons):
        return 0
    if any("needs_human_review" in reason or "remote_leak" in reason for reason in reasons):
        return 1
    if any("probable_duplicate" in reason or "probable_asr_noise" in reason for reason in reasons):
        return 2
    if any("cross_role_overlap" in reason for reason in reasons):
        return 3
    return 8


def select_pack_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    candidates = [item for item in items if me_utterance_ids(item)]
    ranked = sorted(
        candidates,
        key=lambda item: (
            source_reasons_priority(item),
            -safe_float((item.get("interval") or {}).get("duration_sec")),
            safe_float((item.get("interval") or {}).get("start")),
        ),
    )
    if limit > 0:
        return ranked[:limit]
    return ranked


def embedding_for_clip(path_value: Any, backend: EmbeddingBackend) -> tuple[np.ndarray | None, dict[str, Any]]:
    if not path_value:
        return None, {"exists": False}
    path = Path(str(path_value))
    if not path.exists():
        return None, {"exists": False, "path": str(path)}
    return backend.embed(path)


def classify_target_me(
    *,
    item: dict[str, Any],
    target_model: dict[str, Any],
    calibration: dict[str, Any],
    state: dict[str, Any],
    source_scores: dict[str, dict[str, Any]],
    audio_review: dict[str, Any] | None,
    stronger_judge: dict[str, Any] | None,
) -> dict[str, Any]:
    if target_model.get("positive_centroid") is None:
        return {
            "label": "target_me_ambiguous",
            "confidence": 0.0,
            "suggested_decision": "needs_review",
            "reason": "insufficient enrollment centroid",
        }
    target_threshold = safe_float(calibration.get("target_threshold"), 0.45)
    weak_threshold = safe_float(calibration.get("weak_target_threshold"), 0.38)
    mic_sources = ["mic_role_masked", "mic_clean", "mic_raw"]
    mic_scores = [(safe_float(source_scores.get(source, {}).get("target_similarity")), source) for source in mic_sources]
    best_mic, best_source = max(mic_scores, default=(0.0, ""))
    remote_score = safe_float(source_scores.get("remote", {}).get("target_similarity"))
    delta_vs_remote = best_mic - remote_score
    remote_active = safe_float(state.get("remote_active_ratio"))
    local_score = safe_float(state.get("local_score_proxy"))
    local_ratio = safe_float(state.get("local_only_ratio"))
    double_talk = safe_float(state.get("double_talk_ratio"))

    audio_label = ""
    audio_verdict = ""
    audio_scores: dict[str, Any] = {}
    if isinstance(audio_review, dict):
        audio_class = audio_review.get("classification") if isinstance(audio_review.get("classification"), dict) else {}
        audio_label = str(audio_class.get("label") or "")
        audio_verdict = str(audio_class.get("verdict") or "")
        audio_scores = audio_review.get("scores") if isinstance(audio_review.get("scores"), dict) else {}
    judge_label = ""
    if isinstance(stronger_judge, dict):
        judge_class = stronger_judge.get("classification") if isinstance(stronger_judge.get("classification"), dict) else {}
        judge_label = str(judge_class.get("label") or "")

    reasons: list[str] = []
    label = "target_me_ambiguous"
    suggested = "needs_review"
    confidence = 0.45

    existing_drop_evidence = (
        audio_verdict == "probable_transcript_error"
        and audio_label in {"remote_duplicate", "remote_leak", "asr_noise", "uncertain"}
    ) or judge_label in {"confirm_remote_duplicate", "confirm_asr_noise"}
    existing_keep_evidence = judge_label in {"confirm_me", "confirm_timing_or_doubletalk"}

    if best_mic >= target_threshold and delta_vs_remote >= 0.08:
        label = "target_me_confirmed"
        suggested = "keep_me"
        confidence = min(0.94, max(0.72, 0.55 + best_mic * 0.35 + max(0.0, delta_vs_remote) * 0.45))
        reasons.append(f"{best_source} matches Target-Me voiceprint")
        if remote_score > weak_threshold:
            reasons.append("remote also has some similarity; keep as evidence, not automatic truth")
    elif best_mic >= weak_threshold and (local_score >= 0.25 or double_talk >= 0.20 or existing_keep_evidence):
        label = "target_me_possible"
        suggested = "needs_review"
        confidence = min(0.78, max(0.55, best_mic + 0.10))
        reasons.append(f"{best_source} weakly matches Target-Me voiceprint")
    elif best_mic < weak_threshold and remote_active >= 0.45 and existing_drop_evidence:
        label = "target_me_absent_remote_like"
        suggested = "drop_me_evidence_only"
        confidence = min(0.90, max(0.70, 0.62 + (weak_threshold - best_mic) * 0.35 + remote_active * 0.15))
        reasons.append("mic does not match Target-Me while existing audits point to remote/noise")
    elif best_mic < weak_threshold and local_ratio < 0.20 and remote_active >= 0.35:
        label = "target_me_absent"
        suggested = "needs_review"
        confidence = min(0.76, max(0.55, 0.50 + (weak_threshold - best_mic) * 0.30 + remote_active * 0.12))
        reasons.append("low Target-Me similarity and weak local-only state")
    else:
        label = "target_me_ambiguous"
        suggested = "needs_review"
        confidence = min(0.69, max(best_mic, remote_score, 0.40))
        reasons.append("Target-Me voiceprint evidence is weak or conflicting")

    if label in {"target_me_absent_remote_like", "target_me_absent"} and text_token_count(utterance_texts(item)[0]) >= 8:
        confidence = min(confidence, 0.74)
        if suggested == "drop_me_evidence_only":
            suggested = "needs_review"
        reasons.append("long Me text prevents drop-style suggestion")

    return {
        "label": label,
        "confidence": round(float(confidence), 3),
        "suggested_decision": suggested,
        "reason": "; ".join(reasons),
        "scores": {
            "best_mic_target_similarity": round(best_mic, 6),
            "best_mic_source": best_source,
            "remote_target_similarity": round(remote_score, 6),
            "delta_vs_remote": round(delta_vs_remote, 6),
            "target_threshold": round(target_threshold, 6),
            "weak_target_threshold": round(weak_threshold, 6),
            "state_local_score_proxy": round(local_score, 6),
            "state_remote_active_ratio": round(remote_active, 6),
            "audio_review_remote_similarity": safe_float(audio_scores.get("remote_similarity")),
            "audio_review_local_support": safe_float(audio_scores.get("local_support")),
        },
        "existing_evidence": {
            "audio_review_label": audio_label,
            "audio_review_verdict": audio_verdict,
            "stronger_audio_judge_label": judge_label,
        },
    }


def classify_target_me_impact(classification: dict[str, Any]) -> dict[str, Any]:
    label = str(classification.get("label") or "")
    existing = classification.get("existing_evidence") if isinstance(classification.get("existing_evidence"), dict) else {}
    audio_label = str(existing.get("audio_review_label") or "")
    audio_verdict = str(existing.get("audio_review_verdict") or "")
    judge_label = str(existing.get("stronger_audio_judge_label") or "")
    already_confirmed_keep = audio_label == "likely_reliable" or judge_label in {
        "confirm_me",
        "confirm_timing_or_doubletalk",
    }
    already_confirmed_drop = judge_label in {"confirm_remote_duplicate", "confirm_asr_noise"}
    unresolved_or_conflicting = (
        audio_label in {"uncertain", "remote_leak", "remote_duplicate", "asr_noise", "needs_human_review"}
        or audio_verdict in {"needs_stronger_audio_judge", "probable_transcript_error"}
        or judge_label in {"uncertain"}
        or (not audio_label and not judge_label)
    )
    if label == "target_me_confirmed" and unresolved_or_conflicting and not already_confirmed_keep:
        return {
            "category": "new_keep_evidence",
            "direction": "keep_me",
            "review_burden_effect": "candidate_reduce_review",
            "reason": "Target-Me confirms local speaker where existing audits were unresolved or conflicting.",
        }
    if label == "target_me_absent_remote_like" and unresolved_or_conflicting and not already_confirmed_drop:
        return {
            "category": "new_drop_evidence",
            "direction": "drop_me_evidence_only",
            "review_burden_effect": "candidate_reduce_review",
            "reason": "Target-Me rejects local speaker where existing audits were unresolved or conflicting.",
        }
    if label in {"target_me_confirmed", "target_me_absent_remote_like"}:
        return {
            "category": "corroborates_existing_evidence",
            "direction": str(classification.get("suggested_decision") or "needs_review"),
            "review_burden_effect": "no_new_reduction",
            "reason": "Target-Me agrees with evidence that was already strong enough.",
        }
    return {
        "category": "not_actionable",
        "direction": "needs_review",
        "review_burden_effect": "no_new_reduction",
        "reason": "Target-Me evidence is weak or ambiguous.",
    }


def audit_item(
    item: dict[str, Any],
    target_model: dict[str, Any],
    calibration: dict[str, Any],
    backend: EmbeddingBackend,
    state_rows: list[dict[str, Any]],
    audio_review_by_id: dict[str, dict[str, Any]],
    stronger_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
    start = safe_float(interval.get("start"))
    end = safe_float(interval.get("end"), start)
    clips = item.get("clips") if isinstance(item.get("clips"), dict) else {}
    source_scores: dict[str, dict[str, Any]] = {}
    for source in ("mic_role_masked", "mic_clean", "mic_raw", "remote"):
        embedding, info = embedding_for_clip(clips.get(source), backend)
        source_scores[source] = dict(info)
        scores = model_score(embedding, target_model)
        source_scores[source]["target_similarity"] = round(scores["target_similarity"], 6)
        source_scores[source]["positive_similarity"] = round(scores["positive_similarity"], 6)
        source_scores[source]["negative_similarity"] = round(scores["negative_similarity"], 6)
    state = interval_state_features(state_rows, start, end)
    pack_id = str(item.get("id") or "")
    stronger = stronger_by_id.get(pack_id)
    classification = classify_target_me(
        item=item,
        target_model=target_model,
        calibration=calibration,
        state=state,
        source_scores=source_scores,
        audio_review=audio_review_by_id.get(pack_id),
        stronger_judge=stronger,
    )
    impact = classify_target_me_impact(classification)
    me_text, remote_text = utterance_texts(item)
    return {
        "schema": SCHEMA_ROW,
        "id": f"tme_{pack_id.replace('arp_', '')}" if pack_id else "",
        "source_pack_item_id": pack_id,
        "session_id": item.get("session_id"),
        "profile": item.get("profile"),
        "interval": interval,
        "source_reasons": item.get("source_reasons") or [],
        "source_audit_ids": item_source_audit_ids(item),
        "utterance_ids": utterance_ids(item),
        "me_utterance_ids": me_utterance_ids(item),
        "utterances": item.get("utterances") or [],
        "text": {
            "me": me_text,
            "remote": remote_text,
        },
        "state": state,
        "source_scores": source_scores,
        "classification": classification,
        "impact": impact,
    }


def summarize_session(
    *,
    session: Path,
    profile: str,
    out_dir: Path,
    enrollment: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_label: dict[str, dict[str, Any]] = {}
    by_impact: dict[str, dict[str, Any]] = {}
    by_existing_audio_review_label: dict[str, dict[str, Any]] = {}
    by_existing_stronger_judge_label: dict[str, dict[str, Any]] = {}
    by_source_reason: dict[str, dict[str, Any]] = {}
    helpful_count = 0
    helpful_seconds = 0.0
    corroborating_count = 0
    corroborating_seconds = 0.0
    keep_seconds = 0.0
    absent_seconds = 0.0
    for row in rows:
        duration = safe_float((row.get("interval") or {}).get("duration_sec"))
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        label = str(classification.get("label") or "unknown")
        bucket = by_label.setdefault(label, {"count": 0, "seconds": 0.0})
        bucket["count"] += 1
        bucket["seconds"] += duration
        impact = row.get("impact") if isinstance(row.get("impact"), dict) else classify_target_me_impact(classification)
        impact_category = str(impact.get("category") or "unknown")
        impact_bucket = by_impact.setdefault(impact_category, {"count": 0, "seconds": 0.0})
        impact_bucket["count"] += 1
        impact_bucket["seconds"] += duration
        for reason in row.get("source_reasons") or ["none"]:
            reason_bucket = by_source_reason.setdefault(str(reason), {"count": 0, "seconds": 0.0})
            reason_bucket["count"] += 1
            reason_bucket["seconds"] += duration
        existing = classification.get("existing_evidence") if isinstance(classification.get("existing_evidence"), dict) else {}
        audio_label = str(existing.get("audio_review_label") or "")
        judge_label = str(existing.get("stronger_audio_judge_label") or "")
        existing_bucket = by_existing_audio_review_label.setdefault(audio_label or "none", {"count": 0, "seconds": 0.0})
        existing_bucket["count"] += 1
        existing_bucket["seconds"] += duration
        judge_bucket = by_existing_stronger_judge_label.setdefault(judge_label or "none", {"count": 0, "seconds": 0.0})
        judge_bucket["count"] += 1
        judge_bucket["seconds"] += duration
        if impact_category in {"new_keep_evidence", "new_drop_evidence"}:
            helpful_count += 1
            helpful_seconds += duration
        elif impact_category == "corroborates_existing_evidence":
            corroborating_count += 1
            corroborating_seconds += duration
        if label == "target_me_confirmed":
            keep_seconds += duration
        if label in {"target_me_absent", "target_me_absent_remote_like"}:
            absent_seconds += duration
    for bucket in by_label.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    for bucket in by_impact.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    for bucket in by_existing_audio_review_label.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    for bucket in by_existing_stronger_judge_label.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    for bucket in by_source_reason.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 3)

    total_seconds = round(sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in rows), 3)
    status = "ready" if enrollment.get("status") == "ready" and rows else "insufficient_data"
    if rows and enrollment.get("status") == "ready":
        useful_rows = sum(
            1
            for row in rows
            if (row.get("classification") or {}).get("label") in {"target_me_confirmed", "target_me_absent_remote_like"}
        )
        status = "promising_shadow_evidence" if useful_rows else "no_clear_target_me_gain"
    return {
        "schema": SCHEMA_SUMMARY,
        "generator": {"name": "audit-target-me", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "session": str(session),
        "session_id": session.name,
        "profile": profile,
        "method": enrollment.get("method") or "unknown",
        "embedding_backend": enrollment.get("embedding_backend") or {},
        "local_backend_probe": local_backend_probe(),
        "status": status,
        "enrollment": {
            "status": enrollment.get("status"),
            "accepted_count": enrollment.get("accepted_count"),
            "accepted_total_sec": enrollment.get("accepted_total_sec"),
            "target_threshold": (enrollment.get("calibration") or {}).get("target_threshold"),
            "weak_target_threshold": (enrollment.get("calibration") or {}).get("weak_target_threshold"),
        },
        "items": len(rows),
        "total_seconds": total_seconds,
        "by_label": dict(sorted(by_label.items())),
        "by_impact": dict(sorted(by_impact.items())),
        "by_existing_audio_review_label": dict(sorted(by_existing_audio_review_label.items())),
        "by_existing_stronger_judge_label": dict(sorted(by_existing_stronger_judge_label.items())),
        "by_source_reason": dict(sorted(by_source_reason.items())),
        "target_me_helpful_items": helpful_count,
        "target_me_helpful_seconds": round(helpful_seconds, 3),
        "target_me_corroborating_items": corroborating_count,
        "target_me_corroborating_seconds": round(corroborating_seconds, 3),
        "target_me_confirmed_seconds": round(keep_seconds, 3),
        "target_me_absent_seconds": round(absent_seconds, 3),
        "outputs": {
            "enrollment": str(out_dir / "target_me_enrollment.json"),
            "audit": str(out_dir / "target_me_audit.jsonl"),
            "summary": str(out_dir / "target_me_summary.json"),
            "report": str(out_dir / "target_me_report.md"),
        },
        "promotion_decision": "shadow_only_do_not_promote",
    }


def write_session_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    enrollment = summary.get("enrollment") if isinstance(summary.get("enrollment"), dict) else {}
    lines = [
        "# Target-Me Evidence Audit",
        "",
        "This is a shadow-only voice evidence report. It does not edit audio, transcripts or cleanup profiles.",
        "",
        "## Summary",
        "",
        f"- Session: `{summary['session_id']}`",
        f"- Profile: `{summary['profile']}`",
        f"- Method: `{summary['method']}`",
        f"- Status: `{summary['status']}`",
        f"- Local speaker backend ready: `{(summary.get('local_backend_probe') or {}).get('speaker_embedding_ready')}`",
        f"- Local separation candidate available: `{(summary.get('local_backend_probe') or {}).get('separation_candidate_available')}`",
        f"- Enrollment: `{enrollment.get('accepted_count', 0)}` segments, `{enrollment.get('accepted_total_sec', 0.0)}` sec",
        f"- Items: `{summary['items']}` / `{summary['total_seconds']}` sec",
        f"- Helpful Target-Me rows: `{summary['target_me_helpful_items']}` / `{summary['target_me_helpful_seconds']}` sec",
        f"- Corroborating rows: `{summary.get('target_me_corroborating_items', 0)}` / `{summary.get('target_me_corroborating_seconds', 0.0)}` sec",
        f"- Promotion: `{summary['promotion_decision']}`",
        "",
        "## By Label",
        "",
    ]
    for label, bucket in summary.get("by_label", {}).items():
        lines.append(f"- `{label}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")
    lines.extend(["", "## Top Evidence Rows", ""])
    ordered = sorted(
        rows,
        key=lambda row: (
            -safe_float((row.get("classification") or {}).get("confidence")),
            safe_float((row.get("interval") or {}).get("start")),
        ),
    )
    for row in ordered[:30]:
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        scores = classification.get("scores") if isinstance(classification.get("scores"), dict) else {}
        lines.extend(
            [
                f"### {row.get('source_pack_item_id')} {interval.get('start_time') or format_time(safe_float(interval.get('start')))}-{interval.get('end_time') or format_time(safe_float(interval.get('end')))}",
                "",
                f"- Label: `{classification.get('label')}`",
                f"- Suggested: `{classification.get('suggested_decision')}`",
                f"- Confidence: `{classification.get('confidence')}`",
                f"- Best mic source: `{scores.get('best_mic_source')}`",
                f"- Similarity: mic `{scores.get('best_mic_target_similarity')}`, remote `{scores.get('remote_target_similarity')}`, delta `{scores.get('delta_vs_remote')}`",
                f"- Reason: {classification.get('reason')}",
            ]
        )
        for utterance in row.get("utterances") or []:
            role = utterance.get("role") or utterance.get("source_track") or "?"
            lines.append(f"- {role} `{utterance.get('id')}`: {utterance.get('text')}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def audit_session(session: Path, args: argparse.Namespace) -> dict[str, Any]:
    profile = resolve_profile(session, args.profile)
    backend, backend_status = resolve_embedding_backend(args)
    dialogue_path = clean_dialogue_path(session, profile)
    dialogue = read_json(dialogue_path)
    if not dialogue or not isinstance(dialogue.get("utterances"), list):
        out_dir = session / "derived/audit" / args.out_dir_name
        summary = {
            "schema": SCHEMA_SUMMARY,
            "generator": {"name": "audit-target-me", "version": SCRIPT_VERSION},
            "created_at": now_iso(),
            "session": str(session),
            "session_id": session.name,
            "profile": profile,
            "method": backend.method,
            "embedding_backend": backend_status,
            "local_backend_probe": local_backend_probe(),
            "status": "missing_clean_dialogue",
            "items": 0,
            "total_seconds": 0.0,
            "promotion_decision": "shadow_only_do_not_promote",
            "error": f"missing clean dialogue: {dialogue_path}",
        }
        write_json(out_dir / "target_me_summary.json", summary)
        return summary

    state_rows = load_speaker_state(session)
    out_dir = session / "derived/audit" / args.out_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)
    missing_embedding_backend = (
        args.method == "wavlm_xvector"
        and backend_status.get("wavlm_ready") is not True
    ) or (
        args.method == "resemblyzer_dvector"
        and backend_status.get("resemblyzer_ready") is False
    )
    if missing_embedding_backend:
        summary = {
            "schema": SCHEMA_SUMMARY,
            "generator": {"name": "audit-target-me", "version": SCRIPT_VERSION},
            "created_at": now_iso(),
            "session": str(session),
            "session_id": session.name,
            "profile": profile,
            "method": backend.method,
            "embedding_backend": backend_status,
            "local_backend_probe": local_backend_probe(),
            "status": "missing_embedding_model",
            "items": 0,
            "total_seconds": 0.0,
            "by_label": {},
            "target_me_helpful_items": 0,
            "target_me_helpful_seconds": 0.0,
            "target_me_corroborating_items": 0,
            "target_me_corroborating_seconds": 0.0,
            "promotion_decision": "shadow_only_do_not_promote",
            "error": str(backend_status.get("reason") or "missing wavlm model"),
            "outputs": {
                "summary": str(out_dir / "target_me_summary.json"),
                "report": str(out_dir / "target_me_report.md"),
            },
        }
        write_jsonl(out_dir / "target_me_audit.jsonl", [])
        write_json(out_dir / "target_me_summary.json", summary)
        write_session_report(out_dir / "target_me_report.md", summary, [])
        return summary
    progress(args, f"{session.name}: profile={profile} build enrollment")
    enrollment, target_model = build_enrollment(
        session,
        profile,
        dialogue["utterances"],
        state_rows,
        out_dir,
        backend,
        backend_status,
        args,
    )
    if enrollment.get("status") != "ready":
        summary = summarize_session(session=session, profile=profile, out_dir=out_dir, enrollment=enrollment, rows=[])
        write_jsonl(out_dir / "target_me_audit.jsonl", [])
        write_json(out_dir / "target_me_summary.json", summary)
        write_session_report(out_dir / "target_me_report.md", summary, [])
        return summary

    progress(args, f"{session.name}: ensure audio review pack")
    ensure_audio_pack(session, profile, args)
    pack_dir = session / "derived/audit/audio-review-pack"
    items = read_jsonl(pack_dir / "review_pack_items.jsonl")
    selected = select_pack_items(items, args.max_items)
    audio_review_by_id = existing_audit_by_id(pack_dir / "audio_review_audit.jsonl")
    stronger_by_id = {
        str(row.get("source_pack_item_id") or ""): row
        for row in read_jsonl(pack_dir / "faster_whisper_judge.jsonl")
        if row.get("source_pack_item_id")
    }
    calibration = enrollment.get("calibration") if isinstance(enrollment.get("calibration"), dict) else {}
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(selected, start=1):
        progress(args, f"{session.name}: audit {index}/{len(selected)} {item.get('id')}")
        rows.append(audit_item(item, target_model, calibration, backend, state_rows, audio_review_by_id, stronger_by_id))
    summary = summarize_session(session=session, profile=profile, out_dir=out_dir, enrollment=enrollment, rows=rows)
    write_jsonl(out_dir / "target_me_audit.jsonl", rows)
    write_json(out_dir / "target_me_summary.json", summary)
    write_session_report(out_dir / "target_me_report.md", summary, rows)
    return summary


def write_corpus_report(path: Path, summaries: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter(str(row.get("status") or "unknown") for row in summaries)
    by_method = Counter(str(row.get("method") or "unknown") for row in summaries)
    by_label: dict[str, dict[str, Any]] = {}
    by_impact: dict[str, dict[str, Any]] = {}
    by_existing_audio_review_label: dict[str, dict[str, Any]] = {}
    by_existing_stronger_judge_label: dict[str, dict[str, Any]] = {}
    by_source_reason: dict[str, dict[str, Any]] = {}
    total_items = 0
    total_seconds = 0.0
    helpful_items = 0
    helpful_seconds = 0.0
    corroborating_items = 0
    corroborating_seconds = 0.0
    ready_sessions = 0
    for summary in summaries:
        if summary.get("enrollment", {}).get("status") == "ready":
            ready_sessions += 1
        total_items += int(summary.get("items") or 0)
        total_seconds += safe_float(summary.get("total_seconds"))
        helpful_items += int(summary.get("target_me_helpful_items") or 0)
        helpful_seconds += safe_float(summary.get("target_me_helpful_seconds"))
        corroborating_items += int(summary.get("target_me_corroborating_items") or 0)
        corroborating_seconds += safe_float(summary.get("target_me_corroborating_seconds"))
        labels = summary.get("by_label") if isinstance(summary.get("by_label"), dict) else {}
        for label, bucket in labels.items():
            target = by_label.setdefault(str(label), {"count": 0, "seconds": 0.0})
            target["count"] += int(bucket.get("count") or 0)
            target["seconds"] += safe_float(bucket.get("seconds"))
        impacts = summary.get("by_impact") if isinstance(summary.get("by_impact"), dict) else {}
        for label, bucket in impacts.items():
            target = by_impact.setdefault(str(label), {"count": 0, "seconds": 0.0})
            target["count"] += int(bucket.get("count") or 0)
            target["seconds"] += safe_float(bucket.get("seconds"))
        existing_labels = (
            summary.get("by_existing_audio_review_label")
            if isinstance(summary.get("by_existing_audio_review_label"), dict)
            else {}
        )
        for label, bucket in existing_labels.items():
            target = by_existing_audio_review_label.setdefault(str(label), {"count": 0, "seconds": 0.0})
            target["count"] += int(bucket.get("count") or 0)
            target["seconds"] += safe_float(bucket.get("seconds"))
        judge_labels = (
            summary.get("by_existing_stronger_judge_label")
            if isinstance(summary.get("by_existing_stronger_judge_label"), dict)
            else {}
        )
        for label, bucket in judge_labels.items():
            target = by_existing_stronger_judge_label.setdefault(str(label), {"count": 0, "seconds": 0.0})
            target["count"] += int(bucket.get("count") or 0)
            target["seconds"] += safe_float(bucket.get("seconds"))
        source_reasons = summary.get("by_source_reason") if isinstance(summary.get("by_source_reason"), dict) else {}
        for label, bucket in source_reasons.items():
            target = by_source_reason.setdefault(str(label), {"count": 0, "seconds": 0.0})
            target["count"] += int(bucket.get("count") or 0)
            target["seconds"] += safe_float(bucket.get("seconds"))
    for bucket in by_label.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    for bucket in by_impact.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    for bucket in by_existing_audio_review_label.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    for bucket in by_existing_stronger_judge_label.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    for bucket in by_source_reason.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    if helpful_items > 0:
        decision = "promising_shadow_evidence_continue"
    elif corroborating_items > 0:
        decision = "corroborates_existing_evidence_but_no_review_gain"
    elif ready_sessions > 0 and total_items > 0:
        decision = "no_clear_gain_yet_keep_as_evidence"
    else:
        decision = "insufficient_target_me_data"
    report = {
        "schema": SCHEMA_CORPUS,
        "generator": {"name": "audit-target-me", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "method": next(iter(by_method)) if len(by_method) == 1 else "mixed",
        "by_method": dict(sorted(by_method.items())),
        "local_backend_probe": local_backend_probe(),
        "sessions": len(summaries),
        "ready_enrollment_sessions": ready_sessions,
        "by_status": dict(sorted(by_status.items())),
        "items": total_items,
        "total_seconds": round(total_seconds, 3),
        "by_label": dict(sorted(by_label.items())),
        "by_impact": dict(sorted(by_impact.items())),
        "by_existing_audio_review_label": dict(sorted(by_existing_audio_review_label.items())),
        "by_existing_stronger_judge_label": dict(sorted(by_existing_stronger_judge_label.items())),
        "by_source_reason": dict(sorted(by_source_reason.items())),
        "target_me_helpful_items": helpful_items,
        "target_me_helpful_seconds": round(helpful_seconds, 3),
        "target_me_corroborating_items": corroborating_items,
        "target_me_corroborating_seconds": round(corroborating_seconds, 3),
        "readiness_impact": {
            "mode": "shadow_only_not_applied",
            "actual_ready_for_notes_delta": 0,
            "actual_review_first_delta": 0,
            "actual_risky_delta": 0,
            "candidate_review_burden_reduction_items": helpful_items,
            "candidate_review_burden_reduction_seconds": round(helpful_seconds, 3),
            "note": "Target-Me evidence is not integrated into cleanup/review decisions yet.",
        },
        "promotion_decision": "shadow_only_do_not_promote",
        "research_decision": decision,
        "session_summaries": summaries,
    }
    write_json(path, report)
    md_path = path.with_suffix(".md")
    write_corpus_markdown(md_path, report)
    return report


def write_corpus_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Target-Me Corpus Report",
        "",
        "This report summarizes shadow Target-Me evidence. It does not promote any audio or transcript candidate.",
        "",
        "## Summary",
        "",
        f"- Sessions: `{report['sessions']}`",
        f"- Ready enrollment sessions: `{report['ready_enrollment_sessions']}`",
        f"- Method: `{report.get('method')}`",
        f"- Local speaker backend ready: `{(report.get('local_backend_probe') or {}).get('speaker_embedding_ready')}`",
        f"- Local separation candidate available: `{(report.get('local_backend_probe') or {}).get('separation_candidate_available')}`",
        f"- Items: `{report['items']}` / `{report['total_seconds']}` sec",
        f"- Helpful Target-Me rows: `{report['target_me_helpful_items']}` / `{report['target_me_helpful_seconds']}` sec",
        f"- Corroborating rows: `{report.get('target_me_corroborating_items', 0)}` / `{report.get('target_me_corroborating_seconds', 0.0)}` sec",
        f"- Readiness impact: `{(report.get('readiness_impact') or {}).get('mode')}`",
        f"- Research decision: `{report['research_decision']}`",
        f"- Promotion: `{report['promotion_decision']}`",
        "",
        "## By Status",
        "",
    ]
    for status, count in report.get("by_status", {}).items():
        lines.append(f"- `{status}`: `{count}`")
    lines.extend(["", "## By Label", ""])
    for label, bucket in report.get("by_label", {}).items():
        lines.append(f"- `{label}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")
    lines.extend(["", "## By Impact", ""])
    for label, bucket in report.get("by_impact", {}).items():
        lines.append(f"- `{label}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")
    lines.extend(["", "## By Existing Audio Review Label", ""])
    for label, bucket in report.get("by_existing_audio_review_label", {}).items():
        lines.append(f"- `{label}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")
    lines.extend(["", "## By Existing Stronger Judge Label", ""])
    for label, bucket in report.get("by_existing_stronger_judge_label", {}).items():
        lines.append(f"- `{label}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")
    lines.extend(["", "## By Source Reason", ""])
    for label, bucket in report.get("by_source_reason", {}).items():
        lines.append(f"- `{label}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")
    lines.extend(["", "## Sessions", ""])
    for summary in report.get("session_summaries", []):
        lines.append(
            f"- `{summary.get('session_id')}`: `{summary.get('status')}`, "
            f"items `{summary.get('items')}`, helpful `{summary.get('target_me_helpful_items')}`, "
            f"report `{summary.get('outputs', {}).get('report', '')}`"
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    summaries: list[dict[str, Any]] = []
    for session in args.sessions:
        summaries.append(audit_session(session, args))
    if len(args.sessions) > 1:
        args.corpus_out_dir.mkdir(parents=True, exist_ok=True)
        corpus = write_corpus_report(args.corpus_out_dir / "target_me_corpus_report.json", summaries)
        print(f"target_me_corpus: {args.corpus_out_dir / 'target_me_corpus_report.json'}")
        print(f"research_decision: {corpus['research_decision']}")
        print(f"helpful_items: {corpus['target_me_helpful_items']}")
    else:
        summary = summaries[0]
        print(f"target_me_summary: {summary.get('outputs', {}).get('summary')}")
        print(f"status: {summary.get('status')}")
        print(f"helpful_items: {summary.get('target_me_helpful_items', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
