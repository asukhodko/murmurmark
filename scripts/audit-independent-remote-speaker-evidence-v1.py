#!/usr/bin/env python3
"""Recover v3 remote-speaker residuals with an independent WavLM XVector backend."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
REPORT_SCHEMA = "murmurmark.independent_remote_speaker_evidence_report/v1"
WORD_SCHEMA = "murmurmark.independent_remote_speaker_word/v1"
UTTERANCE_SCHEMA = "murmurmark.independent_remote_speaker_utterance/v1"
UNIT_SCHEMA = "murmurmark.independent_remote_speaker_residual_unit/v1"
DECISION_SCHEMA = "murmurmark.independent_remote_speaker_decision/v1"
CAUSE_MAP_SCHEMA = "murmurmark.independent_remote_speaker_cause_map/v1"
ENROLLMENT_SCHEMA = "murmurmark.independent_remote_speaker_enrollment/v1"
RICH_SCHEMA = "murmurmark.independent_remote_speaker_rich_transcript/v1"
MAP_SCHEMA = "murmurmark.independent_remote_speaker_map/v1"
MANIFEST_SCHEMA = "murmurmark.independent_remote_speaker_artifact_manifest/v1"
FIXTURE_SCHEMA = "murmurmark.independent_remote_speaker_embedding_fixture/v1"
CACHE_SCHEMA = "murmurmark.independent_remote_speaker_embedding_cache/v1"
V3_REPORT_SCHEMA = "murmurmark.remote_speaker_coverage_report/v3"
V3_WORD_SCHEMA = "murmurmark.remote_speaker_word/v3"
V3_UTTERANCE_SCHEMA = "murmurmark.remote_speaker_utterance/v3"
V3_MAP_SCHEMA = "murmurmark.remote_speaker_map/v3"
V3_RICH_SCHEMA = "murmurmark.remote_speaker_rich_transcript/v3"
V3_MANIFEST_SCHEMA = "murmurmark.remote_speaker_coverage_artifact_manifest/v3"
DEFAULT_INPUT_DIR = Path("derived/audit/remote-speaker-coverage-v3")
DEFAULT_OUTPUT_DIR = Path("derived/audit/independent-remote-speaker-evidence-v1")
V3_AUDIT = ROOT / "scripts/audit-remote-speaker-coverage-v3.py"
V3_POLICY = ROOT / "policies/remote-speaker-coverage-v3.json"
INDEPENDENT_POLICY = ROOT / "policies/independent-remote-speaker-evidence-v1.json"
TARGET_CAUSES = (
    "similarity_below_threshold",
    "embedding_unavailable",
    "margin_below_threshold",
)
COPY_ON_FALLBACK = (
    "word_attribution.jsonl",
    "utterance_attribution.jsonl",
    "speaker_map.json",
    "transcript.rich.shadow.json",
    "transcript.rich.shadow.md",
)


class ResidualEvidenceError(RuntimeError):
    pass


def load_v3_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_remote_coverage_v3", V3_AUDIT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot_load_remote_coverage_v3")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V3 = load_v3_module()


def parse_args() -> argparse.Namespace:
    policy = json.loads(INDEPENDENT_POLICY.read_text(encoding="utf-8"))
    decision = policy["decision"]
    enrollment = policy["enrollment"]
    model_default = Path(
        os.environ.get(
            "MURMURMARK_REMOTE_SPEAKER_WAVLM_MODEL",
            str(policy["backend"]["default_path"]),
        )
    ).expanduser()
    parser = argparse.ArgumentParser(
        description="Recover selected v3 unknown words with independent local WavLM evidence."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--embedding-fixture", type=Path)
    parser.add_argument("--model-path", type=Path, default=model_default)
    parser.add_argument("--embedding-cache", type=Path)
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--min-similarity", type=float, default=float(decision["minimum_similarity"]))
    parser.add_argument("--min-margin", type=float, default=float(decision["minimum_margin"]))
    parser.add_argument(
        "--min-enrollment-split-similarity",
        type=float,
        default=float(enrollment["minimum_split_similarity"]),
    )
    parser.add_argument(
        "--strict-exact-similarity",
        type=float,
        default=float(decision["strict_exact_similarity"]),
    )
    parser.add_argument(
        "--strict-exact-margin",
        type=float,
        default=float(decision["strict_exact_margin"]),
    )
    parser.add_argument("--min-window-sec", type=float, default=float(decision["minimum_window_sec"]))
    parser.add_argument("--max-unit-sec", type=float, default=float(decision["maximum_unit_sec"]))
    parser.add_argument(
        "--min-enrollment-sec",
        type=float,
        default=float(enrollment["minimum_interval_sec"]),
    )
    parser.add_argument(
        "--max-enrollment-sec",
        type=float,
        default=float(enrollment["maximum_interval_sec"]),
    )
    parser.add_argument("--target-cause", action="append", choices=TARGET_CAUSES)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--require-promoted", action="store_true")
    args = parser.parse_args()
    for name in (
        "min_similarity",
        "min_margin",
        "min_enrollment_split_similarity",
        "strict_exact_similarity",
        "strict_exact_margin",
    ):
        if not 0 <= float(getattr(args, name)) <= 1:
            parser.error(name.replace("_", "-") + " must be in [0, 1]")
    if args.min_window_sec <= 0 or args.max_unit_sec < args.min_window_sec:
        parser.error("window durations are invalid")
    if args.batch_size <= 0:
        parser.error("batch-size must be positive")
    if args.min_enrollment_sec <= 0 or args.max_enrollment_sec < args.min_enrollment_sec:
        parser.error("enrollment durations are invalid")
    args.target_causes = tuple(args.target_cause or TARGET_CAUSES)
    return args


def canonical_json(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ResidualEvidenceError(f"expected_json_object:{path.name}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ResidualEvidenceError(f"expected_jsonl_objects:{path.name}")
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(payload))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def session_path(path: Path, session: Path) -> str:
    return str(path.resolve().relative_to(session.resolve()))


def fingerprint(path: Path, session: Path | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": session_path(path, session) if session else str(path.resolve()),
        "exists": path.is_file(),
    }
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256(path)})
    return row


def same_fingerprint(row: Any, path: Path) -> bool:
    return (
        isinstance(row, dict)
        and row.get("exists") is True
        and path.is_file()
        and int(row.get("bytes") or -1) == path.stat().st_size
        and row.get("sha256") == sha256(path)
    )


def resolve_source_path(session: Path, row: Any) -> Path | None:
    if not isinstance(row, dict) or not row.get("path"):
        return None
    path = Path(str(row["path"])).expanduser()
    return path if path.is_absolute() else session / path


def normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(value))
    if norm <= 1e-9:
        raise ValueError("zero_embedding")
    return value / norm


def implementation() -> dict[str, Any]:
    return {"script": fingerprint(Path(__file__).resolve()), "version": VERSION}


def input_paths(input_dir: Path) -> dict[str, Path]:
    return {
        "report": input_dir / "report.json",
        "manifest": input_dir / "artifact_manifest.json",
        "decisions": input_dir / "recovery_decisions.jsonl",
        "cause_map": input_dir / "unknown_cause_map.json",
        "words": input_dir / "word_attribution.jsonl",
        "utterances": input_dir / "utterance_attribution.jsonl",
        "speaker_map": input_dir / "speaker_map.json",
        "rich": input_dir / "transcript.rich.shadow.json",
        "rich_markdown": input_dir / "transcript.rich.shadow.md",
    }


def verify_v3_inputs(session: Path, paths: dict[str, Path]) -> dict[str, Any]:
    if any(not path.is_file() for path in paths.values()):
        raise ResidualEvidenceError("v3_artifact_missing")
    report = read_json(paths["report"])
    manifest = read_json(paths["manifest"])
    if report.get("schema") != V3_REPORT_SCHEMA or report.get("decision") != "PUBLISH_EVIDENCE":
        raise ResidualEvidenceError("v3_report_not_publishable")
    if manifest.get("schema") != V3_MANIFEST_SCHEMA:
        raise ResidualEvidenceError("v3_manifest_schema_invalid")
    for name, digest in (manifest.get("artifacts") or {}).items():
        path = paths["manifest"].parent / str(name)
        if not path.is_file() or sha256(path) != digest:
            raise ResidualEvidenceError(f"v3_artifact_stale:{name}")
    if not V3.verify_v3_promotion(report):
        raise ResidualEvidenceError("v3_policy_not_promoted")
    for key in ("dialogue", "remote_audio", "v1_attribution"):
        row = (report.get("source") or {}).get(key)
        path = resolve_source_path(session, row)
        if path is None or not same_fingerprint(row, path):
            raise ResidualEvidenceError(f"v3_source_stale:{key}")
    return report


def verify_independent_promotion(report: dict[str, Any]) -> bool:
    try:
        policy = read_json(INDEPENDENT_POLICY)
        manifest_row = policy["corpus_manifest"]
        manifest_path = ROOT / str(manifest_row["path"])
        manifest = read_json(manifest_path)
        audit = (manifest.get("implementation") or {}).get("audit") or {}
        report_script = (report.get("implementation") or {}).get("script") or {}
        return (
            policy.get("schema") == "murmurmark.independent_remote_speaker_policy/v1"
            and policy.get("state") == "promoted"
            and manifest.get("schema")
            == "murmurmark.independent_remote_speaker_frozen_manifest/v1"
            and manifest.get("decision") == "PROMOTE_INDEPENDENT_REMOTE_SPEAKER_EVIDENCE_V1"
            and manifest_row.get("sha256") == sha256(manifest_path)
            and audit.get("sha256") == sha256(Path(__file__).resolve())
            and report_script.get("sha256") == audit.get("sha256")
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False


class EmbeddingBackend:
    def __init__(self, args: argparse.Namespace):
        self.status = "unavailable"
        self.reason: str | None = None
        self.fixture: dict[str, list[float]] | None = None
        self.cache: dict[str, np.ndarray] = {}
        self.cache_path = args.embedding_cache
        self.batch_size = int(args.batch_size)
        self.model_path = args.model_path.expanduser().resolve()
        self.policy = read_json(INDEPENDENT_POLICY)
        self.processor: Any = None
        self.model: Any = None
        self.torch: Any = None
        self.model_tree_sha256: str | None = None
        self.provenance: dict[str, Any] = {
            "method": "wavlm_xvector_independent_v1",
            "runtime": {"python": sys.version.split()[0], "numpy": np.__version__},
        }
        if args.embedding_fixture:
            fixture_path = args.embedding_fixture.expanduser().resolve()
            try:
                fixture = read_json(fixture_path)
            except Exception as error:
                self.reason = f"embedding_fixture_invalid:{type(error).__name__}"
                return
            if fixture.get("schema") != FIXTURE_SCHEMA or not isinstance(
                fixture.get("embeddings"), dict
            ):
                self.reason = "embedding_fixture_invalid_schema"
                return
            self.fixture = fixture["embeddings"]
            self.provenance = {
                "method": "deterministic_fixture",
                "fixture": fingerprint(fixture_path),
                "runtime": {"python": sys.version.split()[0], "numpy": np.__version__},
            }
            self.status = "ready"
            return

        model_rows: list[dict[str, Any]] = []
        for name, expected_sha in sorted(self.policy["backend"]["files"].items()):
            path = self.model_path / name
            if not path.is_file():
                self.reason = f"speaker_model_file_missing:{name}"
                return
            actual_sha = sha256(path)
            if actual_sha != expected_sha:
                self.reason = f"speaker_model_file_stale:{name}"
                return
            model_rows.append(
                {"name": name, "bytes": path.stat().st_size, "sha256": actual_sha}
            )
        self.model_tree_sha256 = hashlib.sha256(canonical_json(model_rows)).hexdigest()
        try:
            import torch
            import transformers
            from transformers import AutoFeatureExtractor, AutoModelForAudioXVector  # noqa: F401
        except (ImportError, ModuleNotFoundError) as error:
            self.reason = f"wavlm_runtime_unavailable:{type(error).__name__}"
            return
        expected_runtime = self.policy["runtime"]
        runtime = {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "torch": str(torch.__version__),
            "transformers": str(transformers.__version__),
            "soundfile": importlib.metadata.version("soundfile"),
            "device": "cpu",
            "offline": True,
        }
        mismatches = [
            name
            for name in ("numpy", "torch", "transformers", "soundfile")
            if str(runtime[name]) != str(expected_runtime[name])
        ]
        if mismatches:
            self.reason = "wavlm_runtime_stale:" + ",".join(mismatches)
            return
        display_path = str(self.model_path)
        try:
            display_path = "~/" + str(self.model_path.relative_to(Path.home()))
        except ValueError:
            pass
        self.provenance = {
            "method": "wavlm_xvector_independent_v1",
            "architecture": "WavLMForXVector",
            "independent_from": "resemblyzer",
            "model": {
                "id": self.policy["backend"]["model_id"],
                "path": display_path,
                "tree_sha256": self.model_tree_sha256,
                "files": model_rows,
            },
            "license": self.policy["backend"]["license"],
            "license_url": self.policy["backend"]["license_url"],
            "redistributed_by_murmurmark": False,
            "runtime": runtime,
        }
        self.status = "ready"

    def _load(self) -> None:
        if self.fixture is not None or self.model is not None:
            return
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch
        from transformers import AutoFeatureExtractor, AutoModelForAudioXVector

        torch.set_num_threads(int(self.policy["runtime"]["max_torch_threads"]))
        torch.use_deterministic_algorithms(True, warn_only=True)
        self.torch = torch
        self.processor = AutoFeatureExtractor.from_pretrained(
            str(self.model_path), local_files_only=True
        )
        self.model = AutoModelForAudioXVector.from_pretrained(
            str(self.model_path), local_files_only=True
        )
        self.model.eval()

    def _load_cache(self, audio_sha256: str) -> None:
        if self.cache_path is None or not self.cache_path.is_file():
            return
        try:
            rows = read_jsonl(self.cache_path)
        except (OSError, ValueError, json.JSONDecodeError, ResidualEvidenceError):
            return
        for row in rows:
            if (
                row.get("schema") == CACHE_SCHEMA
                and row.get("audio_sha256") == audio_sha256
                and row.get("model_tree_sha256") == self.model_tree_sha256
                and isinstance(row.get("vector"), list)
            ):
                try:
                    self.cache[str(row["key"])] = normalize(
                        np.asarray(row["vector"], dtype=np.float32)
                    )
                except (KeyError, TypeError, ValueError):
                    continue

    @staticmethod
    def _prepare_waveform(waveform: np.ndarray, sample_rate: int) -> np.ndarray | None:
        values = np.asarray(waveform, dtype=np.float32).reshape(-1)
        if sample_rate != 16_000:
            import librosa

            values = librosa.resample(values, orig_sr=sample_rate, target_sr=16_000)
        if values.size < 5_600:
            return None
        rms = float(np.sqrt(np.mean(np.square(values, dtype=np.float64))))
        if not np.isfinite(rms) or rms < 1e-7:
            return None
        return values.astype(np.float32, copy=False)

    def precompute(
        self,
        audio_path: Path,
        requests: list[dict[str, Any]],
        *,
        rebuild: bool,
    ) -> dict[str, Any]:
        if self.fixture is not None:
            return {"status": "fixture", "requested": len(requests), "computed": 0, "reused": 0}
        audio_sha = sha256(audio_path)
        if not rebuild:
            self._load_cache(audio_sha)
        unique = {
            str(row["key"]): {
                "key": str(row["key"]),
                "start": round(float(row["start"]), 6),
                "end": round(float(row["end"]), 6),
            }
            for row in requests
        }
        missing = [row for key, row in sorted(unique.items()) if key not in self.cache]
        prepared: list[tuple[dict[str, Any], np.ndarray]] = []
        failures: dict[str, str] = {}
        if missing:
            self._load()
            assert self.processor is not None and self.model is not None and self.torch is not None
            with sf.SoundFile(str(audio_path)) as audio:
                for row in missing:
                    start_frame = max(0, int(round(float(row["start"]) * audio.samplerate)))
                    end_frame = min(len(audio), int(round(float(row["end"]) * audio.samplerate)))
                    if end_frame <= start_frame:
                        failures[str(row["key"])] = "empty_audio_slice"
                        continue
                    audio.seek(start_frame)
                    waveform = audio.read(
                        end_frame - start_frame, dtype="float32", always_2d=True
                    ).mean(axis=1)
                    values = self._prepare_waveform(waveform, audio.samplerate)
                    if values is None:
                        failures[str(row["key"])] = "insufficient_or_silent_audio"
                        continue
                    prepared.append((row, values))
            prepared.sort(key=lambda item: (len(item[1]), str(item[0]["key"])))
            for offset in range(0, len(prepared), self.batch_size):
                batch = prepared[offset : offset + self.batch_size]
                inputs = self.processor(
                    [waveform for _, waveform in batch],
                    sampling_rate=16_000,
                    return_tensors="pt",
                    padding=True,
                )
                with self.torch.no_grad():
                    vectors = self.model(**inputs).embeddings.detach().cpu().numpy()
                for (row, _), vector in zip(batch, vectors):
                    self.cache[str(row["key"])] = normalize(vector)
        if self.cache_path is not None:
            rows = [
                {
                    "schema": CACHE_SCHEMA,
                    "key": key,
                    "start": unique[key]["start"],
                    "end": unique[key]["end"],
                    "audio_sha256": audio_sha,
                    "model_tree_sha256": self.model_tree_sha256,
                    "vector": [round(float(value), 9) for value in self.cache[key]],
                }
                for key in sorted(unique)
                if key in self.cache
            ]
            write_jsonl(self.cache_path, rows)
        return {
            "status": "ready" if not failures else "partial",
            "requested": len(unique),
            "computed": sum(row["key"] in self.cache for row in missing),
            "reused": len(unique) - len(missing),
            "available": sum(key in self.cache for key in unique),
            "failures": dict(sorted(failures.items())),
            "audio_sha256": audio_sha,
        }

    def embed(self, audio: sf.SoundFile | None, key: str, start: float, end: float) -> np.ndarray:
        if self.fixture is not None:
            value = self.fixture.get(key)
            if not isinstance(value, list):
                raise ValueError("fixture_embedding_missing")
            return normalize(np.asarray(value, dtype=np.float32))
        value = self.cache.get(key)
        if value is None:
            raise ValueError("embedding_cache_miss")
        return value


def classify(
    embedding: np.ndarray,
    centroids: dict[str, np.ndarray],
    min_similarity: float,
    min_margin: float,
) -> dict[str, Any]:
    scores = sorted(
        ((float(embedding @ centroid), speaker) for speaker, centroid in centroids.items()),
        reverse=True,
    )
    if not scores:
        return {
            "speaker_id": None,
            "top_speaker_id": None,
            "similarity": None,
            "margin": None,
            "speaker_scores": {},
        }
    similarity, speaker = scores[0]
    margin = similarity - scores[1][0] if len(scores) > 1 else similarity
    accepted = similarity >= min_similarity and margin >= min_margin
    return {
        "speaker_id": speaker if accepted else None,
        "top_speaker_id": speaker,
        "similarity": round(similarity, 6),
        "margin": round(margin, 6),
        "speaker_scores": {candidate: round(score, 6) for score, candidate in scores},
    }


def build_enrollment(
    backend: EmbeddingBackend,
    audio: sf.SoundFile | None,
    v1_rows: list[dict[str, Any]],
    speakers: set[str],
    args: argparse.Namespace,
) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    values: dict[str, list[tuple[str, np.ndarray, int, float, float]]] = defaultdict(list)
    failures: list[dict[str, str]] = []
    eligible = sorted(
        (
            row
            for row in v1_rows
            if row.get("speaker_id") in speakers
            and row.get("status") == "attributed"
            and not (row.get("overlap_utterance_ids") or [])
            and float(row.get("end") or 0) - float(row.get("start") or 0)
            >= args.min_enrollment_sec
        ),
        key=lambda row: (
            str(row.get("speaker_id")),
            float(row.get("start") or 0),
            str(row.get("utterance_id") or ""),
        ),
    )
    for row in eligible:
        speaker = str(row["speaker_id"])
        uid = str(row["utterance_id"])
        key = f"enroll:{speaker}:{uid}"
        start = float(row["start"])
        end = float(row["end"])
        if end - start > args.max_enrollment_sec:
            midpoint = (start + end) / 2
            start = midpoint - args.max_enrollment_sec / 2
            end = midpoint + args.max_enrollment_sec / 2
        bucket = int(hashlib.sha256(uid.encode()).hexdigest()[:8], 16) % 4
        try:
            value = backend.embed(audio, key, start, end)
            values[speaker].append((uid, value, bucket, start, end))
        except Exception as error:
            failures.append({"speaker_id": speaker, "utterance_id": uid, "reason": type(error).__name__})

    centroids: dict[str, dict[str, np.ndarray]] = {}
    rows: list[dict[str, Any]] = []
    test_samples: list[tuple[str, str, np.ndarray]] = []
    for speaker in sorted(speakers):
        samples = values.get(speaker, [])
        enrollment_samples = [row for row in samples if row[2] != 3]
        speaker_tests = [row for row in samples if row[2] == 3]
        a_values = [row[1] for index, row in enumerate(enrollment_samples) if index % 2 == 0]
        b_values = [row[1] for index, row in enumerate(enrollment_samples) if index % 2 == 1]
        if (
            len(samples) < int(backend.policy["enrollment"]["minimum_samples_per_speaker"])
            or not a_values
            or not b_values
            or not speaker_tests
        ):
            rows.append(
                {
                    "speaker_id": speaker,
                    "status": "insufficient_enrollment_or_test_split",
                    "sample_count": len(samples),
                    "enrollment_count": len(enrollment_samples),
                    "test_count": len(speaker_tests),
                    "split_a_count": len(a_values),
                    "split_b_count": len(b_values),
                    "split_similarity": None,
                    "enrollment_utterance_ids": [row[0] for row in enrollment_samples],
                    "test_utterance_ids": [row[0] for row in speaker_tests],
                }
            )
            continue
        split_a = normalize(np.mean(a_values, axis=0))
        split_b = normalize(np.mean(b_values, axis=0))
        full = normalize(np.mean([row[1] for row in enrollment_samples], axis=0))
        agreement = float(split_a @ split_b)
        status = (
            "stable"
            if agreement >= args.min_enrollment_split_similarity
            else "split_disagreement"
        )
        rows.append(
            {
                "speaker_id": speaker,
                "status": status,
                "sample_count": len(samples),
                "enrollment_count": len(enrollment_samples),
                "test_count": len(speaker_tests),
                "split_a_count": len(a_values),
                "split_b_count": len(b_values),
                "split_similarity": round(agreement, 6),
                "enrollment_utterance_ids": [row[0] for row in enrollment_samples],
                "test_utterance_ids": [row[0] for row in speaker_tests],
            }
        )
        if status == "stable":
            centroids[speaker] = {"full": full, "split_a": split_a, "split_b": split_b}
            test_samples.extend((speaker, row[0], row[1]) for row in speaker_tests)
    complete = set(centroids) == speakers
    test_rows: list[dict[str, Any]] = []
    full_centroids = {speaker: values["full"] for speaker, values in centroids.items()}
    for truth, uid, embedding in sorted(test_samples):
        result = classify(embedding, full_centroids, args.min_similarity, args.min_margin)
        test_rows.append(
            {
                "speaker_id": truth,
                "utterance_id": uid,
                "predicted_speaker_id": result["top_speaker_id"],
                "accepted_speaker_id": result["speaker_id"],
                "top1_correct": result["top_speaker_id"] == truth,
                "accepted_correct": result["speaker_id"] == truth,
                "similarity": result["similarity"],
                "margin": result["margin"],
            }
        )
    accepted_tests = [row for row in test_rows if row["accepted_speaker_id"]]
    accepted_correct = sum(bool(row["accepted_correct"]) for row in accepted_tests)
    payload = {
        "schema": ENROLLMENT_SCHEMA,
        "status": "ready" if complete else "incomplete",
        "published_speakers": len(speakers),
        "stable_speakers": len(centroids),
        "split_rule": "sha256(utterance_id)_mod_4_bucket_3_is_test",
        "minimum_split_similarity": args.min_enrollment_split_similarity,
        "speakers": rows,
        "test_evaluation": {
            "rows": len(test_rows),
            "top1_correct": sum(bool(row["top1_correct"]) for row in test_rows),
            "top1_accuracy": round(
                sum(bool(row["top1_correct"]) for row in test_rows) / len(test_rows), 6
            )
            if test_rows
            else 0.0,
            "accepted_rows": len(accepted_tests),
            "accepted_correct": accepted_correct,
            "accepted_precision": round(accepted_correct / len(accepted_tests), 6)
            if accepted_tests
            else None,
            "accepted_coverage": round(len(accepted_tests) / len(test_rows), 6)
            if test_rows
            else 0.0,
            "items": test_rows,
        },
        "failures": failures,
    }
    return centroids, payload


def embedding_requests(
    v1_rows: list[dict[str, Any]],
    speakers: set[str],
    units: list[dict[str, Any]],
    remote_by_id: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for row in sorted(
        v1_rows,
        key=lambda item: (
            str(item.get("speaker_id") or ""),
            float(item.get("start") or 0),
            str(item.get("utterance_id") or ""),
        ),
    ):
        if (
            row.get("speaker_id") not in speakers
            or row.get("status") != "attributed"
            or row.get("overlap_utterance_ids")
        ):
            continue
        start = float(row.get("start") or 0)
        end = float(row.get("end") or 0)
        if end - start < args.min_enrollment_sec:
            continue
        if end - start > args.max_enrollment_sec:
            midpoint = (start + end) / 2
            start = midpoint - args.max_enrollment_sec / 2
            end = midpoint + args.max_enrollment_sec / 2
        requests.append(
            {
                "key": f"enroll:{row['speaker_id']}:{row['utterance_id']}",
                "start": start,
                "end": end,
            }
        )
    for unit in units:
        uid = str(unit["utterance_id"])
        utterance = remote_by_id.get(uid)
        if utterance is None:
            continue
        left = unit.get("left_anchor_speaker")
        right = unit.get("right_anchor_speaker")
        if left and right and left != right:
            continue
        for window in analysis_windows(unit, utterance, args.min_window_sec):
            requests.append(
                {
                    "key": f"residual:{unit['unit_id']}:{window['name']}",
                    "start": window["start"],
                    "end": window["end"],
                }
            )
    return requests


def split_centroid_sets(
    enrollment: dict[str, dict[str, np.ndarray]],
) -> dict[str, dict[str, np.ndarray]]:
    return {
        split: {speaker: values[split] for speaker, values in enrollment.items()}
        for split in ("full", "split_a", "split_b")
    }


def classify_with_split_enrollment(
    embedding: np.ndarray,
    centroid_sets: dict[str, dict[str, np.ndarray]],
    min_similarity: float,
    min_margin: float,
) -> dict[str, Any]:
    rows = {
        split: classify(embedding, centroids, min_similarity, min_margin)
        for split, centroids in centroid_sets.items()
    }
    accepted = {row.get("speaker_id") for row in rows.values() if row.get("speaker_id")}
    top = {row.get("top_speaker_id") for row in rows.values() if row.get("top_speaker_id")}
    speaker = next(iter(accepted)) if len(accepted) == 1 and len(top) == 1 else None
    return {
        "speaker_id": speaker,
        "status": "accepted" if speaker else "split_disagreement" if len(top) > 1 else "below_threshold",
        "splits": rows,
        "minimum_similarity": min(
            (float(row["similarity"]) for row in rows.values() if row.get("similarity") is not None),
            default=None,
        ),
        "minimum_margin": min(
            (float(row["margin"]) for row in rows.values() if row.get("margin") is not None),
            default=None,
        ),
    }


def residual_units(
    words_by_utterance: dict[str, list[dict[str, Any]]],
    target_causes: set[str],
    max_unit_sec: float,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for uid in sorted(words_by_utterance):
        words = words_by_utterance[uid]
        index = 0
        while index < len(words):
            word = words[index]
            cause = str(word.get("v3_reason") or "")
            if word.get("speaker_id") or cause not in target_causes:
                index += 1
                continue
            run_end = index + 1
            while (
                run_end < len(words)
                and not words[run_end].get("speaker_id")
                and str(words[run_end].get("v3_reason") or "") == cause
            ):
                run_end += 1
            cursor = index
            while cursor < run_end:
                right = cursor + 1
                while right < run_end:
                    proposed = float(words[right]["end"]) - float(words[cursor]["start"])
                    if proposed > max_unit_sec:
                        break
                    right += 1
                unit_words = words[cursor:right]
                left_speaker = words[cursor - 1].get("speaker_id") if cursor > 0 else None
                right_speaker = words[right].get("speaker_id") if right < len(words) else None
                unit_id = f"residual_{len(units) + 1:06d}"
                units.append(
                    {
                        "schema": UNIT_SCHEMA,
                        "unit_id": unit_id,
                        "utterance_id": uid,
                        "cause": cause,
                        "start": float(unit_words[0]["start"]),
                        "end": float(unit_words[-1]["end"]),
                        "duration_sec": round(
                            float(unit_words[-1]["end"]) - float(unit_words[0]["start"]), 6
                        ),
                        "coverage_weight_sec": round(
                            sum(float(row.get("coverage_weight_sec") or 0) for row in unit_words), 9
                        ),
                        "word_ids": [str(row["word_id"]) for row in unit_words],
                        "left_anchor_speaker": left_speaker,
                        "right_anchor_speaker": right_speaker,
                    }
                )
                cursor = right
            index = run_end
    return units


def analysis_windows(
    unit: dict[str, Any], utterance: dict[str, Any], min_window_sec: float
) -> list[dict[str, Any]]:
    start = float(unit["start"])
    end = float(unit["end"])
    utterance_start_value = utterance.get("start")
    utterance_end_value = utterance.get("end")
    utterance_start = float(start if utterance_start_value is None else utterance_start_value)
    utterance_end = float(end if utterance_end_value is None else utterance_end_value)
    duration = end - start
    windows: list[tuple[str, float, float]] = []
    if duration >= min_window_sec:
        windows.append(("exact", start, end))
    required = max(0.0, min_window_sec - duration)
    for name, padding in (("compact", required / 2 + 0.2), ("context", required / 2 + 0.65)):
        left = max(utterance_start, start - padding)
        right = min(utterance_end, end + padding)
        if right - left >= min_window_sec:
            windows.append((name, left, right))
    if duration >= min_window_sec * 2.2:
        midpoint = (start + end) / 2
        windows.extend((("left_half", start, midpoint), ("right_half", midpoint, end)))
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for name, left, right in windows:
        key = (round(left * 1000), round(right * 1000))
        if key in seen or right - left < min_window_sec:
            continue
        seen.add(key)
        unique.append({"name": name, "start": round(left, 6), "end": round(right, 6)})
    return unique


def evaluate_unit(
    unit: dict[str, Any],
    utterance: dict[str, Any],
    backend: EmbeddingBackend,
    audio: sf.SoundFile | None,
    centroid_sets: dict[str, dict[str, np.ndarray]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    left = unit.get("left_anchor_speaker")
    right = unit.get("right_anchor_speaker")
    if left and right and left != right:
        return {**unit, "outcome": "unknown", "reason": "conflicting_boundary_anchors", "windows": []}
    anchor = left or right
    windows = analysis_windows(unit, utterance, args.min_window_sec)
    evidence: list[dict[str, Any]] = []
    for window in windows:
        key = f"residual:{unit['unit_id']}:{window['name']}"
        try:
            embedding = backend.embed(audio, key, float(window["start"]), float(window["end"]))
            result = classify_with_split_enrollment(
                embedding, centroid_sets, args.min_similarity, args.min_margin
            )
            evidence.append({**window, "key": key, **result})
        except Exception as error:
            evidence.append(
                {
                    **window,
                    "key": key,
                    "speaker_id": None,
                    "status": "embedding_failed",
                    "reason": type(error).__name__,
                }
            )
    accepted = [row for row in evidence if row.get("speaker_id")]
    accepted_speakers = {str(row["speaker_id"]) for row in accepted}
    if len(accepted_speakers) != 1:
        reason = "candidate_windows_disagree" if len(accepted_speakers) > 1 else "no_window_above_v3_threshold"
        return {**unit, "outcome": "unknown", "reason": reason, "windows": evidence}
    speaker = next(iter(accepted_speakers))
    if anchor and speaker != anchor:
        return {
            **unit,
            "outcome": "unknown",
            "reason": "candidate_conflicts_with_boundary_anchor",
            "candidate_speaker_id": speaker,
            "windows": evidence,
        }
    strict_exact = [
        row
        for row in accepted
        if row.get("name") == "exact"
        and float(row.get("minimum_similarity") or 0) >= args.strict_exact_similarity
        and float(row.get("minimum_margin") or 0) >= args.strict_exact_margin
        and float(unit.get("duration_sec") or 0) >= 1.25
    ]
    if len(accepted) < 2 and not strict_exact:
        return {
            **unit,
            "outcome": "unknown",
            "reason": "insufficient_independent_windows",
            "candidate_speaker_id": speaker,
            "windows": evidence,
        }
    return {
        **unit,
        "outcome": "attributed",
        "reason": "independent_wavlm_split_enrollment_consensus",
        "speaker_id": speaker,
        "acceptance_mode": "multi_window_consensus" if len(accepted) >= 2 else "strict_exact_window",
        "window_count": len(accepted),
        "minimum_similarity": round(
            min(float(row["minimum_similarity"]) for row in accepted), 6
        ),
        "minimum_margin": round(min(float(row["minimum_margin"]) for row in accepted), 6),
        "windows": evidence,
    }


def build_output_manifest(out_dir: Path, session_id: str) -> dict[str, Any]:
    names = (
        "embedding_cache.jsonl",
        "residual_units.jsonl",
        "residual_decisions.jsonl",
        "split_enrollment.json",
        "cause_ceiling.json",
        "word_attribution.jsonl",
        "utterance_attribution.jsonl",
        "speaker_map.json",
        "transcript.rich.shadow.json",
        "transcript.rich.shadow.md",
        "report.json",
        "report.md",
    )
    return {
        "schema": MANIFEST_SCHEMA,
        "session_id": session_id,
        "artifacts": {name: sha256(out_dir / name) for name in names if (out_dir / name).is_file()},
    }


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Independent Remote Speaker Evidence v1",
        "",
        f"Decision: `{report['decision']}`",
        f"Status: `{report['status']}`",
        f"Recovered: `{summary.get('recovered_words', 0)}` words / `{summary.get('recovered_seconds', 0):.3f}s`",
        f"Remaining: `{summary.get('remaining_unknown_words', 0)}` words / `{summary.get('remaining_unknown_seconds', 0):.3f}s`",
        "",
        "## Cause Ceiling",
        "",
    ]
    for row in report.get("cause_ceiling") or []:
        lines.append(
            f"- `{row['cause']}`: recovered {row['recovered_words']} / {row['baseline_words']} words, "
            f"{row['recovered_seconds']:.3f}s / {row['baseline_seconds']:.3f}s"
        )
    if report.get("reasons"):
        lines.extend(("", "## Reasons", ""))
        lines.extend(f"- `{reason}`" for reason in report["reasons"])
    return "\n".join(lines) + "\n"


def fallback(
    session: Path,
    input_dir: Path,
    out_dir: Path,
    reason: str,
    source: dict[str, Any],
    v3_report: dict[str, Any] | None = None,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in COPY_ON_FALLBACK:
        source_path = input_dir / name
        if source_path.is_file():
            shutil.copyfile(source_path, out_dir / name)
    baseline = (v3_report or {}).get("summary") or {}
    summary = {
        "remote_utterances": int(baseline.get("remote_utterances") or 0),
        "remote_words": int(baseline.get("remote_words") or 0),
        "baseline_attributed_words": int(baseline.get("attributed_words") or 0),
        "attributed_words": int(baseline.get("attributed_words") or 0),
        "baseline_unknown_words": int(baseline.get("remaining_unknown_words") or 0),
        "baseline_unknown_seconds": float(baseline.get("remaining_unknown_seconds") or 0),
        "recovered_words": 0,
        "recovered_seconds": 0.0,
        "remaining_unknown_words": int(baseline.get("remaining_unknown_words") or 0),
        "remaining_unknown_seconds": float(baseline.get("remaining_unknown_seconds") or 0),
        "remote_speech_sec": float(baseline.get("remote_speech_sec") or 0),
        "attributed_speech_sec": float(baseline.get("attributed_speech_sec") or 0),
        "attributable_remote_speech_ratio": float(
            baseline.get("attributable_remote_speech_ratio") or 0
        ),
        "published_speakers": int(baseline.get("published_speakers") or 0),
        "internal_change_utterances": int(baseline.get("internal_change_utterances") or 0),
    }
    write_jsonl(out_dir / "residual_units.jsonl", [])
    write_jsonl(out_dir / "residual_decisions.jsonl", [])
    write_json(
        out_dir / "split_enrollment.json",
        {"schema": ENROLLMENT_SCHEMA, "status": "unavailable", "reason": reason, "speakers": []},
    )
    fallback_words = int(baseline.get("remaining_unknown_words") or 0)
    fallback_seconds = float(baseline.get("remaining_unknown_seconds") or 0)
    write_json(
        out_dir / "cause_ceiling.json",
        {
            "schema": CAUSE_MAP_SCHEMA,
            "status": "fallback",
            "reason": reason,
            "baseline_unknown_words": fallback_words,
            "baseline_unknown_seconds": fallback_seconds,
            "causes": [
                {
                    "cause": "fallback_v3",
                    "baseline_words": fallback_words,
                    "baseline_seconds": fallback_seconds,
                    "recovered_words": 0,
                    "recovered_seconds": 0.0,
                    "remaining_words": fallback_words,
                    "remaining_seconds": fallback_seconds,
                }
            ],
            "failure_reasons": {reason: fallback_words},
        },
    )
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "status": "fallback",
        "decision": "FALLBACK_V3",
        "reasons": [reason],
        "source": source,
        "implementation": implementation(),
        "summary": summary,
        "gates": {"publish_session_evidence": False, "exact_v3_fallback": True},
        "safety": {
            "plain_transcript_unchanged": True,
            "selected_text_unchanged": True,
            "me_unchanged": True,
            "existing_v3_labels_unchanged": True,
            "raw_audio_unchanged": True,
            "fallback": "remote_speaker_coverage_v3",
        },
        "cause_ceiling": [],
    }
    write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    write_json(out_dir / "artifact_manifest.json", build_output_manifest(out_dir, session.name))
    print(f"independent_remote_speaker_v1: decision=FALLBACK_V3 reason={reason}")
    return 0


def verify_existing(session: Path, out_dir: Path, require_promoted: bool) -> int:
    try:
        report = read_json(out_dir / "report.json")
        manifest = read_json(out_dir / "artifact_manifest.json")
        current = (
            report.get("schema") == REPORT_SCHEMA
            and manifest.get("schema") == MANIFEST_SCHEMA
            and same_fingerprint(
                (report.get("implementation") or {}).get("script"), Path(__file__).resolve()
            )
            and same_fingerprint(
                (report.get("source") or {}).get("independent_policy"), INDEPENDENT_POLICY
            )
            and all(
                (out_dir / name).is_file() and sha256(out_dir / name) == digest
                for name, digest in (manifest.get("artifacts") or {}).items()
            )
            and (not require_promoted or verify_independent_promotion(report))
        )
    except (OSError, ValueError, json.JSONDecodeError, ResidualEvidenceError):
        current = False
    if not current:
        print("independent remote speaker v1 artifacts are stale or not promoted", file=sys.stderr)
        return 2
    print("independent remote speaker v1 artifacts verified")
    return 0


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    input_dir = args.input_dir if args.input_dir.is_absolute() else session / args.input_dir
    out_dir = args.out_dir if args.out_dir.is_absolute() else session / args.out_dir
    if args.verify_only:
        return verify_existing(session, out_dir, args.require_promoted)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = input_paths(input_dir)
    source = {
        "session_id": session.name,
        "independent_policy": fingerprint(INDEPENDENT_POLICY),
        "v3_artifacts": {name: fingerprint(path, session) for name, path in paths.items()},
    }
    v3_report: dict[str, Any] | None = None
    try:
        v3_report = verify_v3_inputs(session, paths)
        source.update(deepcopy(v3_report.get("source") or {}))
        source["session_id"] = session.name
        words = read_jsonl(paths["words"])
        v3_utterances = read_jsonl(paths["utterances"])
        speaker_map = read_json(paths["speaker_map"])
        rich = read_json(paths["rich"])
        if any(row.get("schema") != V3_WORD_SCHEMA for row in words):
            raise ResidualEvidenceError("v3_word_schema_invalid")
        if any(row.get("schema") != V3_UTTERANCE_SCHEMA for row in v3_utterances):
            raise ResidualEvidenceError("v3_utterance_schema_invalid")
        if speaker_map.get("schema") != V3_MAP_SCHEMA or rich.get("schema") != V3_RICH_SCHEMA:
            raise ResidualEvidenceError("v3_rich_schema_invalid")
        remote_audio = resolve_source_path(session, source.get("remote_audio"))
        v1_path = resolve_source_path(session, source.get("v1_attribution"))
        if remote_audio is None or v1_path is None:
            raise ResidualEvidenceError("v3_audio_or_enrollment_source_missing")
        v1_rows = read_jsonl(v1_path)
    except (ResidualEvidenceError, KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        return fallback(session, input_dir, out_dir, str(error), source, v3_report)

    speakers = {
        str(row["speaker_id"])
        for row in speaker_map.get("speakers") or []
        if row.get("speaker_id") and int(row.get("seed_units") or 0) > 0
    }
    if not speakers:
        return fallback(session, input_dir, out_dir, "seeded_speaker_map_empty", source, v3_report)

    words_by_utterance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        words_by_utterance[str(word["utterance_id"])].append(deepcopy(word))
    for utterance_words in words_by_utterance.values():
        utterance_words.sort(
            key=lambda row: (float(row["start"]), float(row["end"]), str(row["word_id"]))
        )
    rich_utterances = deepcopy(rich.get("utterances") or [])
    remote_by_id = {
        str(row["id"]): row
        for row in rich_utterances
        if row.get("role") == "remote" and row.get("id")
    }
    units = residual_units(words_by_utterance, set(args.target_causes), args.max_unit_sec)
    if args.embedding_cache is None:
        args.embedding_cache = out_dir / "embedding_cache.jsonl"
    elif not args.embedding_cache.is_absolute():
        args.embedding_cache = session / args.embedding_cache

    backend = EmbeddingBackend(args)
    if backend.status != "ready":
        source["embedding_backend"] = backend.provenance
        return fallback(
            session, input_dir, out_dir, backend.reason or "speaker_backend_unavailable", source, v3_report
        )

    try:
        requests = embedding_requests(v1_rows, speakers, units, remote_by_id, args)
        cache_report = backend.precompute(
            remote_audio,
            requests,
            rebuild=bool(args.rebuild_cache),
        )
        enrollment, enrollment_report = build_enrollment(
            backend, None, v1_rows, speakers, args
        )
        if set(enrollment) != speakers:
            source["embedding_backend"] = backend.provenance
            source["embedding_cache"] = cache_report
            source["enrollment"] = enrollment_report
            return fallback(
                session, input_dir, out_dir, "incomplete_split_enrollment", source, v3_report
            )
        centroid_sets = split_centroid_sets(enrollment)
        decisions = [
            evaluate_unit(
                unit,
                remote_by_id[str(unit["utterance_id"])],
                backend,
                None,
                centroid_sets,
                args,
            )
            for unit in units
            if str(unit["utterance_id"]) in remote_by_id
        ]
    except Exception as error:
        source["embedding_backend"] = backend.provenance
        return fallback(session, input_dir, out_dir, f"residual_inference_failed:{type(error).__name__}", source, v3_report)
    source["embedding_backend"] = backend.provenance
    source["embedding_cache"] = cache_report
    source["enrollment"] = {
        "split_rule": enrollment_report["split_rule"],
        "test_evaluation": enrollment_report["test_evaluation"],
    }

    decision_by_word = {
        word_id: decision
        for decision in decisions
        for word_id in decision.get("word_ids") or []
    }
    baseline_assignments = {str(row["word_id"]): row.get("speaker_id") for row in words}
    baseline_words = {str(row["word_id"]): row for row in words}
    baseline_causes: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"words": 0, "seconds": 0.0}
    )
    recovered_causes: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"words": 0, "seconds": 0.0}
    )
    failure_causes: Counter[str] = Counter()
    recovered_words = 0
    recovered_seconds = 0.0
    baseline_unknown_words = 0
    baseline_unknown_seconds = 0.0
    output_words_by_utterance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    word_decisions: list[dict[str, Any]] = []
    for baseline in words:
        word = deepcopy(baseline)
        word["schema"] = WORD_SCHEMA
        word_id = str(word["word_id"])
        uid = str(word["utterance_id"])
        weight = float(word.get("coverage_weight_sec") or 0)
        if baseline.get("speaker_id"):
            word["independent_v1_reason"] = "preserved_v3_attribution"
            output_words_by_utterance[uid].append(word)
            continue
        baseline_unknown_words += 1
        baseline_unknown_seconds += weight
        cause = str(baseline.get("v3_reason") or baseline.get("reason") or "unknown")
        baseline_causes[cause]["words"] = int(baseline_causes[cause]["words"]) + 1
        baseline_causes[cause]["seconds"] = float(baseline_causes[cause]["seconds"]) + weight
        decision = decision_by_word.get(word_id)
        recovered = bool(decision and decision.get("outcome") == "attributed")
        if recovered:
            speaker = str(decision["speaker_id"])
            word.update(
                {
                    "speaker_id": speaker,
                    "speaker_label": speaker,
                    "status": "attributed",
                    "reason": "independent_wavlm_split_enrollment_consensus",
                    "independent_v1_reason": "recovered_residual_audio_evidence",
                    "residual_unit_id": decision["unit_id"],
                    "confidence": {
                        "similarity": decision["minimum_similarity"],
                        "margin": decision["minimum_margin"],
                        "window_count": decision["window_count"],
                    },
                }
            )
            recovered_words += 1
            recovered_seconds += weight
            recovered_causes[cause]["words"] = int(recovered_causes[cause]["words"]) + 1
            recovered_causes[cause]["seconds"] = (
                float(recovered_causes[cause]["seconds"]) + weight
            )
        else:
            reason = str((decision or {}).get("reason") or "cause_not_targeted")
            word["independent_v1_reason"] = reason
            failure_causes[reason] += 1
        word_decisions.append(
            {
                "schema": DECISION_SCHEMA,
                "word_id": word_id,
                "utterance_id": uid,
                "start": word["start"],
                "end": word["end"],
                "coverage_weight_sec": weight,
                "baseline_cause": cause,
                "outcome": "attributed" if recovered else "unknown",
                "speaker_id": word.get("speaker_id"),
                "reason": word.get("independent_v1_reason"),
                "residual_unit_id": (decision or {}).get("unit_id"),
            }
        )
        output_words_by_utterance[uid].append(word)

    attributions: list[dict[str, Any]] = []
    internal_changes = 0
    speaker_weights: Counter[str] = Counter()
    for utterance in rich_utterances:
        if utterance.get("role") != "remote" or not utterance.get("id"):
            continue
        uid = str(utterance["id"])
        utterance_words = output_words_by_utterance.get(uid, [])
        turns = V3.build_turns(str(utterance.get("text") or ""), utterance_words)
        utterance["speaker_turns"] = turns
        distinct = list(dict.fromkeys(str(row["speaker_id"]) for row in turns if row.get("speaker_id")))
        if len(distinct) > 1:
            internal_changes += 1
        weights: Counter[str] = Counter()
        for word in utterance_words:
            if word.get("speaker_id"):
                weight = float(word.get("coverage_weight_sec") or 0)
                weights[str(word["speaker_id"])] += weight
                speaker_weights[str(word["speaker_id"])] += weight
        total = sum(float(row.get("coverage_weight_sec") or 0) for row in utterance_words)
        attributed = sum(weights.values())
        dominant = weights.most_common(1)[0][0] if len(weights) == 1 else None
        if dominant and total and attributed / total < 0.80:
            dominant = None
            status = "partial"
        elif len(weights) > 1:
            status = "mixed"
        elif dominant:
            status = "attributed"
        else:
            status = "aggregate"
        attributions.append(
            {
                "schema": UTTERANCE_SCHEMA,
                "utterance_id": uid,
                "start": float(utterance.get("start") or 0),
                "end": float(utterance.get("end") or 0),
                "speaker_id": dominant,
                "speaker_label": dominant or "Colleagues",
                "status": status,
                "reason": "word_level_independent_v1_evidence" if dominant else "insufficient_word_level_evidence",
                "speaker_turns": turns,
                "attributed_weight_sec": round(attributed, 9),
                "total_weight_sec": round(total, 9),
            }
        )

    all_words = [
        word
        for utterance in rich_utterances
        if utterance.get("role") == "remote"
        for word in output_words_by_utterance.get(str(utterance.get("id")), [])
    ]
    selected_text_unchanged = all(
        "".join(str(turn.get("text") or "") for turn in utterance.get("speaker_turns") or [])
        == str(utterance.get("text") or "")
        for utterance in rich_utterances
        if utterance.get("role") == "remote"
    )
    baseline_attributions_preserved = all(
        baseline_assignments[str(word["word_id"])] in {None, word.get("speaker_id")}
        for word in all_words
    )
    timestamps_unchanged = all(
        float(word["start"]) == float(baseline_words[str(word["word_id"])]["start"])
        and float(word["end"]) == float(baseline_words[str(word["word_id"])]["end"])
        for word in all_words
    )
    protected_preserved = all(
        word.get("speaker_id") is None
        for word in all_words
        if baseline_words[str(word["word_id"])].get("v3_reason")
        in {"protected_remote_overlap", "conflicting_frame_speakers"}
    )
    existing_speakers_only = all(
        word.get("speaker_id") is None or str(word["speaker_id"]) in speakers for word in all_words
    )
    remaining_words = baseline_unknown_words - recovered_words
    remaining_seconds = max(0.0, baseline_unknown_seconds - recovered_seconds)
    baseline_attributed = float(v3_report["summary"]["attributed_speech_sec"])
    remote_speech = float(v3_report["summary"]["remote_speech_sec"])
    cause_ceiling = [
        {
            "cause": cause,
            "baseline_words": int(values["words"]),
            "baseline_seconds": round(float(values["seconds"]), 6),
            "recovered_words": int(recovered_causes[cause]["words"]),
            "recovered_seconds": round(float(recovered_causes[cause]["seconds"]), 6),
            "remaining_words": int(values["words"]) - int(recovered_causes[cause]["words"]),
            "remaining_seconds": round(
                float(values["seconds"]) - float(recovered_causes[cause]["seconds"]), 6
            ),
        }
        for cause, values in sorted(baseline_causes.items())
    ]
    test_evaluation = enrollment_report["test_evaluation"]
    aggregate_fallback_exact = all(
        word.get("speaker_id") is not None
        or all(
            word.get(key) == baseline_words[str(word["word_id"])].get(key)
            for key in (
                "word_id",
                "utterance_id",
                "role",
                "text",
                "start",
                "end",
                "coverage_weight_sec",
                "status",
                "reason",
            )
        )
        for word in all_words
    )
    gates = {
        "v3_inputs_current": True,
        "v3_policy_promoted": True,
        "complete_split_enrollment": set(enrollment) == speakers,
        "enrollment_test_has_rows": int(test_evaluation["rows"]) > 0,
        "enrollment_test_accepted_precision": (
            int(test_evaluation["accepted_rows"]) > 0
            and float(test_evaluation["accepted_precision"] or 0) >= 1.0
        ),
        "seeded_speakers_only": existing_speakers_only,
        "baseline_attributions_preserved": baseline_attributions_preserved,
        "selected_text_unchanged": selected_text_unchanged,
        "word_timestamps_unchanged": timestamps_unchanged,
        "word_conservation": selected_text_unchanged,
        "timestamp_order": timestamps_unchanged,
        "protected_causes_preserved": protected_preserved,
        "aggregate_fallback_exact": aggregate_fallback_exact,
        "me_unchanged": True,
    }
    gates["publish_session_evidence"] = all(gates.values())
    summary = {
        "remote_utterances": len(attributions),
        "remote_words": len(all_words),
        "baseline_attributed_words": len(all_words) - baseline_unknown_words,
        "attributed_words": len(all_words) - remaining_words,
        "baseline_unknown_words": baseline_unknown_words,
        "baseline_unknown_seconds": round(baseline_unknown_seconds, 6),
        "recovered_words": recovered_words,
        "recovered_seconds": round(recovered_seconds, 6),
        "remaining_unknown_words": remaining_words,
        "remaining_unknown_seconds": round(remaining_seconds, 6),
        "unknown_words_reduction_ratio": round(recovered_words / baseline_unknown_words, 6)
        if baseline_unknown_words
        else 0.0,
        "unknown_seconds_reduction_ratio": round(recovered_seconds / baseline_unknown_seconds, 6)
        if baseline_unknown_seconds
        else 0.0,
        "remote_speech_sec": round(remote_speech, 6),
        "attributed_speech_sec": round(baseline_attributed + recovered_seconds, 6),
        "attributable_remote_speech_ratio": round(
            (baseline_attributed + recovered_seconds) / remote_speech, 6
        )
        if remote_speech
        else 0.0,
        "published_speakers": len(speakers),
        "internal_change_utterances": internal_changes,
        "residual_units": len(units),
        "recovered_units": sum(row.get("outcome") == "attributed" for row in decisions),
    }
    source["profile"] = str((v3_report.get("source") or {}).get("profile") or "auto")
    source["embedding_backend"] = backend.provenance
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "status": "completed" if gates["publish_session_evidence"] else "fallback",
        "decision": "PUBLISH_EVIDENCE" if gates["publish_session_evidence"] else "FALLBACK_V3",
        "reasons": [key for key, value in gates.items() if not value],
        "source": source,
        "implementation": implementation(),
        "parameters": {
            "profile": "wavlm_xvector_independent_residual_v1",
            "target_causes": list(args.target_causes),
            "min_similarity": args.min_similarity,
            "min_margin": args.min_margin,
            "min_enrollment_split_similarity": args.min_enrollment_split_similarity,
            "strict_exact_similarity": args.strict_exact_similarity,
            "strict_exact_margin": args.strict_exact_margin,
            "min_enrollment_sec": args.min_enrollment_sec,
            "max_enrollment_sec": args.max_enrollment_sec,
            "min_window_sec": args.min_window_sec,
            "max_unit_sec": args.max_unit_sec,
            "enrollment_split": enrollment_report["split_rule"],
            "embedding_cache": args.embedding_cache.name,
            "requires_multiple_windows_or_strict_exact": True,
            "requires_split_enrollment_consensus": True,
            "protected_v3_causes": ["conflicting_frame_speakers", "protected_remote_overlap"],
        },
        "summary": summary,
        "gates": gates,
        "safety": {
            "plain_transcript_unchanged": True,
            "selected_text_unchanged": selected_text_unchanged,
            "me_unchanged": True,
            "existing_v3_labels_unchanged": baseline_attributions_preserved,
            "session_local_anonymous_only": True,
            "identity_inference": False,
            "external_writes": False,
            "raw_audio_unchanged": bool((v3_report.get("safety") or {}).get("raw_audio_unchanged")),
            "fallback": "remote_speaker_coverage_v3",
        },
        "cause_ceiling": cause_ceiling,
        "failure_reasons": dict(sorted(failure_causes.items())),
    }
    if report["decision"] != "PUBLISH_EVIDENCE":
        return fallback(session, input_dir, out_dir, report["reasons"][0], source, v3_report)

    baseline_speakers = {
        str(row["speaker_id"]): row for row in speaker_map.get("speakers") or []
    }
    output_speakers = []
    for speaker in sorted(speakers):
        row = deepcopy(baseline_speakers[speaker])
        row["attributed_speech_sec"] = round(float(speaker_weights[speaker]), 6)
        output_speakers.append(row)
    write_jsonl(out_dir / "residual_units.jsonl", decisions)
    write_jsonl(out_dir / "residual_decisions.jsonl", word_decisions)
    write_json(out_dir / "split_enrollment.json", enrollment_report)
    write_json(
        out_dir / "cause_ceiling.json",
        {
            "schema": CAUSE_MAP_SCHEMA,
            "session_id": session.name,
            "baseline_unknown_words": baseline_unknown_words,
            "baseline_unknown_seconds": round(baseline_unknown_seconds, 6),
            "causes": cause_ceiling,
            "failure_reasons": dict(sorted(failure_causes.items())),
        },
    )
    write_jsonl(out_dir / "word_attribution.jsonl", all_words)
    write_jsonl(out_dir / "utterance_attribution.jsonl", attributions)
    write_json(
        out_dir / "speaker_map.json",
        {
            "schema": MAP_SCHEMA,
            "session_id": session.name,
            "selected_profile": source["profile"],
            "decision": report["decision"],
            "speakers": output_speakers,
        },
    )
    write_json(
        out_dir / "transcript.rich.shadow.json",
        {
            "schema": RICH_SCHEMA,
            "session_id": session.name,
            "selected_profile": source["profile"],
            "decision": report["decision"],
            "source": source,
            "utterances": rich_utterances,
            "remote_speaker_attributions": attributions,
            "remote_word_attributions": all_words,
            "speaker_map": output_speakers,
            "safety": report["safety"],
        },
    )
    (out_dir / "transcript.rich.shadow.md").write_text(
        V3.transcript_markdown(rich_utterances, source["profile"]), encoding="utf-8"
    )
    write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    write_json(out_dir / "artifact_manifest.json", build_output_manifest(out_dir, session.name))
    print(
        f"independent_remote_speaker_v1: decision={report['decision']} recovered={recovered_words}w/"
        f"{recovered_seconds:.3f}s remaining={remaining_words}w/{remaining_seconds:.3f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
