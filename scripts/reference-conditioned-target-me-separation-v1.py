#!/usr/bin/env python3
"""Freeze and verify inputs for Reference-Conditioned Target-Me Separation v1."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from murmurmark_resource_policy import (  # noqa: E402
    apply_resource_policy,
    resolve_resource_policy,
)
import reference_conditioned_separator_v1 as SEPARATOR  # noqa: E402
import speaker_preserving_neural_echo_v2 as V2_CORE  # noqa: E402


SCHEMA = "murmurmark.reference_conditioned_target_me_separation_policy/v1"
REPORT_SCHEMA = "murmurmark.reference_conditioned_target_me_preflight/v1"
FROZEN_INPUTS_SCHEMA = "murmurmark.reference_conditioned_target_me_frozen_inputs/v1"
READY = "READY_FOR_ORACLE_CEILING"
BLOCKED = "BLOCKED_PREFLIGHT"
PREFLIGHT_POLICY_SECTIONS = (
    "production_baseline",
    "controlled_supervision",
    "sealed_evaluation",
    "data_isolation",
    "models",
    "reference_enrollment",
    "audio_contract",
    "preflight_gates",
)
TRAINING_POLICY_SECTIONS = (*PREFLIGHT_POLICY_SECTIONS, "training_cache")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def policy_scope_fingerprint(policy: dict[str, Any], sections: Iterable[str]) -> str:
    return digest_json(
        {
            "schema": policy.get("schema"),
            "profile": policy.get("profile"),
            "sections": {name: policy.get(name) for name in sections},
        }
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def display_path(path: Path, repo_root: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(repo_root.resolve()))
    except ValueError:
        pass
    home = Path.home().resolve()
    try:
        return str(Path("~") / resolved.relative_to(home))
    except ValueError:
        return f"<external>/{resolved.name}"


def source_record(
    *,
    path: Path,
    expected_sha256: str,
    repo_root: Path,
    kind: str,
) -> dict[str, Any]:
    exists = path.is_file()
    observed = sha256(path) if exists else None
    size = path.stat().st_size if exists else None
    return {
        "kind": kind,
        "path": display_path(path, repo_root),
        "bytes": size,
        "expected_sha256": expected_sha256,
        "observed_sha256": observed,
        "passed": exists and observed == expected_sha256,
    }


def check(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    return {
        "name": name,
        "observed": observed,
        "expected": expected,
        "passed": observed == expected,
    }


def collect_artifact_descriptors(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if {
            "path",
            "sha256",
            "bytes",
        }.issubset(value) and isinstance(value.get("path"), str):
            yield {
                "path": str(value["path"]),
                "sha256": str(value["sha256"]),
                "bytes": int(value["bytes"]),
            }
            return
        for child in value.values():
            yield from collect_artifact_descriptors(child)
    elif isinstance(value, list):
        for child in value:
            yield from collect_artifact_descriptors(child)


def read_supervision_manifest(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append(f"line_{line_number}:invalid_json:{error.msg}")
                continue
            if not isinstance(payload, dict):
                errors.append(f"line_{line_number}:not_object")
                continue
            rows.append(payload)
    return rows, errors


def summarize_manifest(rows: list[dict[str, Any]], errors: list[str]) -> dict[str, Any]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    durations: dict[str, Counter[str]] = defaultdict(Counter)
    schemas = Counter[str]()
    identities: list[str] = []
    artifact_by_path: dict[str, dict[str, Any]] = {}
    artifact_conflicts: list[str] = []

    for index, row in enumerate(rows):
        split = str(row.get("split") or "missing")
        kind = str(row.get("kind") or "missing")
        counts[split][kind] += 1
        durations[split][kind] += float(row.get("duration_sec") or 0.0)
        schemas[str(row.get("schema") or "missing")] += 1
        identity = str(row.get("item_id") or row.get("clip_id") or f"row-{index}")
        identities.append(f"{split}:{kind}:{identity}")
        for artifact in collect_artifact_descriptors(row):
            existing = artifact_by_path.get(artifact["path"])
            if existing is not None and existing != artifact:
                artifact_conflicts.append(artifact["path"])
            artifact_by_path[artifact["path"]] = artifact

    duplicate_identities = sorted(
        identity for identity, count_value in Counter(identities).items() if count_value > 1
    )
    return {
        "row_count": len(rows),
        "parse_errors": errors,
        "schemas": dict(sorted(schemas.items())),
        "counts": {
            split: dict(sorted(kind_counts.items()))
            for split, kind_counts in sorted(counts.items())
        },
        "duration_sec": {
            split: {
                kind: round(float(duration), 6)
                for kind, duration in sorted(kind_durations.items())
            }
            for split, kind_durations in sorted(durations.items())
        },
        "duplicate_identities": duplicate_identities,
        "artifact_conflicts": sorted(set(artifact_conflicts)),
        "artifacts": [artifact_by_path[path] for path in sorted(artifact_by_path)],
    }


def verify_controlled_artifacts(
    *,
    corpus_root: Path,
    artifacts: list[dict[str, Any]],
    mode: str,
    sample_count: int,
    repo_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if mode == "none":
        selected: list[dict[str, Any]] = []
    elif mode == "sample":
        count = min(max(1, sample_count), len(artifacts))
        if count == len(artifacts):
            selected = artifacts
        else:
            indexes = {
                round(index * (len(artifacts) - 1) / max(1, count - 1))
                for index in range(count)
            }
            selected = [artifacts[index] for index in sorted(indexes)]
    elif mode == "all":
        selected = artifacts
    else:
        raise ValueError(f"unsupported audio verification mode: {mode}")

    selected_paths = {row["path"] for row in selected}
    missing: list[str] = []
    size_mismatches: list[str] = []
    hash_mismatches: list[str] = []
    frozen_rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        path = corpus_root / artifact["path"]
        verified = artifact["path"] in selected_paths
        status = "manifest_only"
        if verified:
            if not path.is_file():
                status = "missing"
                missing.append(artifact["path"])
            elif path.stat().st_size != int(artifact["bytes"]):
                status = "size_mismatch"
                size_mismatches.append(artifact["path"])
            elif sha256(path) != artifact["sha256"]:
                status = "hash_mismatch"
                hash_mismatches.append(artifact["path"])
            else:
                status = "verified"
        frozen_rows.append(
            {
                "kind": "controlled_supervision_audio",
                "path": display_path(path, repo_root),
                "bytes": artifact["bytes"],
                "sha256": artifact["sha256"],
                "verification": status,
            }
        )

    passed = not missing and not size_mismatches and not hash_mismatches
    return (
        {
            "mode": mode,
            "manifest_artifact_count": len(artifacts),
            "checked_artifact_count": len(selected),
            "missing": missing,
            "size_mismatches": size_mismatches,
            "hash_mismatches": hash_mismatches,
            "passed": passed,
        },
        frozen_rows,
    )


def module_inventory(required: list[str], optional: list[str]) -> dict[str, Any]:
    def available(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    required_rows = {name: available(name) for name in required}
    optional_rows = {name: available(name) for name in optional}
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "required": required_rows,
        "optional": optional_rows,
        "passed": all(required_rows.values()),
    }


def verify_models(policy: dict[str, Any], repo_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report: dict[str, Any] = {}
    frozen: list[dict[str, Any]] = []
    for model_name, model in sorted((policy.get("models") or {}).items()):
        model_root = Path(str(model.get("local_path") or "")).expanduser()
        files: list[dict[str, Any]] = []
        for filename, expected in sorted((model.get("files") or {}).items()):
            path = model_root / filename
            row = source_record(
                path=path,
                expected_sha256=str(expected),
                repo_root=repo_root,
                kind=f"model:{model_name}",
            )
            files.append(row)
            frozen.append(
                {
                    "kind": row["kind"],
                    "path": row["path"],
                    "bytes": row["bytes"],
                    "sha256": row["observed_sha256"],
                    "expected_sha256": row["expected_sha256"],
                }
            )
        report[model_name] = {
            "model_id": model.get("model_id"),
            "path": display_path(model_root, repo_root),
            "files": files,
            "passed": bool(files) and all(row["passed"] for row in files),
        }
    return report, frozen


def verify_pinned_sources(
    policy: dict[str, Any],
    repo_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    specifications = [
        (
            "production_baseline.policy",
            policy.get("production_baseline", {}),
            "policy",
            "policy_sha256",
        ),
    ]
    for group_name in (
        "controlled_supervision",
        "sealed_evaluation",
        "reference_enrollment",
        "training_cache",
    ):
        group = policy.get(group_name, {})
        for key, value in group.items():
            if not isinstance(value, str) or not key.endswith("_sha256"):
                continue
            path_key = key[: -len("_sha256")]
            if isinstance(group.get(path_key), str):
                specifications.append((f"{group_name}.{path_key}", group, path_key, key))

    report: dict[str, Any] = {}
    frozen: list[dict[str, Any]] = []
    for name, group, path_key, hash_key in specifications:
        path = repo_root / str(group.get(path_key) or "")
        row = source_record(
            path=path,
            expected_sha256=str(group.get(hash_key) or ""),
            repo_root=repo_root,
            kind=name,
        )
        report[name] = row
        frozen.append(
            {
                "kind": name,
                "path": row["path"],
                "bytes": row["bytes"],
                "sha256": row["observed_sha256"],
                "expected_sha256": row["expected_sha256"],
            }
        )
    return report, frozen


def semantic_checks(
    *,
    policy: dict[str, Any],
    production_policy: dict[str, Any],
    corpus_decision: dict[str, Any],
    replay_report: dict[str, Any],
    sealed_report: dict[str, Any],
    sealed_decision: dict[str, Any],
    sealed_manifest: dict[str, Any],
    manifest_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    controlled = policy["controlled_supervision"]
    baseline = policy["production_baseline"]
    sealed = policy["sealed_evaluation"]
    gates = policy["preflight_gates"]
    counts = manifest_summary["counts"]

    def count(split: str, kind: str) -> int:
        return int(counts.get(split, {}).get(kind, 0))

    return [
        check("policy_schema", policy.get("schema"), SCHEMA),
        check("policy_status", policy.get("status"), "preflight_locked"),
        check("production_decision", production_policy.get("decision"), baseline.get("decision")),
        check("production_profile", production_policy.get("selected_profile"), baseline.get("profile")),
        check(
            "production_corpus_fingerprint",
            production_policy.get("corpus_fingerprint"),
            baseline.get("corpus_fingerprint"),
        ),
        check("controlled_decision", corpus_decision.get("decision"), gates.get("controlled_decision")),
        check(
            "controlled_corpus_fingerprint",
            corpus_decision.get("fingerprint"),
            controlled.get("corpus_fingerprint"),
        ),
        check("controlled_replay_status", replay_report.get("status"), gates.get("replay_status")),
        check(
            "controlled_replay_count",
            replay_report.get("matched_files"),
            controlled.get("replay_matched_files"),
        ),
        check("sealed_report_passed", sealed_report.get("passed"), True),
        check(
            "sealed_report_decision",
            (sealed_report.get("promotion") or {}).get("decision"),
            baseline.get("decision"),
        ),
        check(
            "sealed_decision",
            sealed_decision.get("decision"),
            baseline.get("decision"),
        ),
        check(
            "sealed_report_fingerprint",
            sealed_report.get("corpus_fingerprint"),
            baseline.get("corpus_fingerprint"),
        ),
        check(
            "sealed_decision_fingerprint",
            sealed_decision.get("corpus_fingerprint"),
            baseline.get("corpus_fingerprint"),
        ),
        check(
            "sealed_manifest_fingerprint",
            sealed_manifest.get("fingerprint"),
            baseline.get("corpus_fingerprint"),
        ),
        check(
            "sealed_session_count",
            len((sealed_manifest.get("basis") or {}).get("sessions") or []),
            sealed.get("session_count"),
        ),
        check("manifest_parse_errors", len(manifest_summary["parse_errors"]), 0),
        check("manifest_duplicate_identities", len(manifest_summary["duplicate_identities"]), 0),
        check("manifest_artifact_conflicts", len(manifest_summary["artifact_conflicts"]), 0),
        check(
            "train_synthetic_double_talk",
            count("train", "synthetic_double_talk") >= int(gates["minimum_train_synthetic_double_talk"]),
            True,
        ),
        check(
            "dev_synthetic_double_talk",
            count("dev", "synthetic_double_talk") >= int(gates["minimum_dev_synthetic_double_talk"]),
            True,
        ),
        check(
            "train_measured_local_target",
            count("train", "measured_local_target") >= int(gates["minimum_train_measured_local_target"]),
            True,
        ),
        check(
            "train_measured_remote_echo",
            count("train", "measured_remote_echo") >= int(gates["minimum_train_measured_remote_echo"]),
            True,
        ),
        check(
            "hard_measured_double_talk",
            count("hard_test", "measured_double_talk") >= int(gates["minimum_hard_measured_double_talk"]),
            True,
        ),
    ]


def build_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Reference-Conditioned Target-Me Separation v1 Preflight",
        "",
        f"Decision: **{report['decision']}**",
        f"Fingerprint: `{report['fingerprint']}`",
        f"Policy: `{report['policy']['path']}`",
        "",
        "## Evidence",
        "",
        f"- Frozen source files: {report['summary']['frozen_source_count']}",
        f"- Controlled rows: {report['corpus']['row_count']}",
        f"- Controlled audio descriptors: {report['audio_verification']['manifest_artifact_count']}",
        f"- Controlled audio files verified now: {report['audio_verification']['checked_artifact_count']}",
        f"- Sealed production sessions: {report['summary']['sealed_session_count']}",
        f"- Required Python modules: {'ok' if report['modules']['passed'] else 'missing'}",
        f"- Pinned local models: {'ok' if report['summary']['models_passed'] else 'missing or changed'}",
        "",
        "## Corpus Counts",
        "",
        "| Split | Kind | Items | Seconds |",
        "|---|---|---:|---:|",
    ]
    counts = report["corpus"]["counts"]
    durations = report["corpus"]["duration_sec"]
    for split in sorted(counts):
        for kind in sorted(counts[split]):
            lines.append(
                f"| {split} | {kind} | {counts[split][kind]} | "
                f"{durations.get(split, {}).get(kind, 0.0):.1f} |"
            )
    failed = [row for row in report["checks"] if not row["passed"]]
    lines.extend(["", "## Gates", ""])
    if failed:
        for row in failed:
            lines.append(
                f"- FAIL `{row['name']}`: observed `{row['observed']}`, expected `{row['expected']}`"
            )
    else:
        lines.append("All preflight gates passed. Oracle-ceiling work is unblocked.")
    lines.extend(
        [
            "",
            "Production remains `speaker_preserving_neural_echo_v2`; this report does not promote a candidate.",
            "",
        ]
    )
    return "\n".join(lines)


def read_controlled_audio(corpus_root: Path, descriptor: dict[str, Any]) -> Any:
    import numpy as np
    import soundfile as sf

    path = corpus_root / str(descriptor["path"])
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size != int(descriptor["bytes"]):
        raise ValueError(f"controlled artifact size changed: {path}")
    if sha256(path) != str(descriptor["sha256"]):
        raise ValueError(f"controlled artifact hash changed: {path}")
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    values = np.asarray(audio, dtype=np.float32)
    if sample_rate != 16_000 or values.ndim != 1 or values.size != 64_000:
        raise ValueError(f"unexpected controlled audio contract: {path}")
    if not np.all(np.isfinite(values)):
        raise ValueError(f"non-finite controlled audio: {path}")
    return values


def fit_audio_length(values: Any, length: int) -> Any:
    import numpy as np

    result = np.asarray(values, dtype=np.float64)
    if result.size >= length:
        return result[:length]
    return np.pad(result, (0, length - result.size))


def ideal_mask_separate(mixture: Any, target: Any, echo: Any, *, family: str) -> dict[str, Any]:
    import numpy as np
    from scipy import signal

    nperseg = 512
    noverlap = 384

    def transform(values: Any) -> Any:
        return signal.stft(
            np.asarray(values, dtype=np.float64),
            fs=16_000,
            window="hann",
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nperseg,
            boundary="zeros",
            padded=True,
        )[2]

    def inverse(values: Any) -> Any:
        reconstructed = signal.istft(
            values,
            fs=16_000,
            window="hann",
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nperseg,
            input_onesided=True,
            boundary=True,
        )[1]
        return fit_audio_length(reconstructed, len(mixture))

    mixture_stft = transform(mixture)
    target_stft = transform(target)
    echo_stft = transform(echo)
    if family == "ideal_ratio_mask":
        target_power = np.abs(target_stft) ** 2
        echo_power = np.abs(echo_stft) ** 2
        denominator = target_power + echo_power + 1.0e-12
        target_spectrum = (target_power / denominator) * mixture_stft
        echo_spectrum = (echo_power / denominator) * mixture_stft
    elif family == "ideal_complex_mask":
        denominator = np.abs(mixture_stft) ** 2 + 1.0e-12
        target_mask = target_stft * np.conj(mixture_stft) / denominator
        echo_mask = echo_stft * np.conj(mixture_stft) / denominator
        target_spectrum = target_mask * mixture_stft
        echo_spectrum = echo_mask * mixture_stft
    else:
        raise ValueError(f"unsupported oracle mask family: {family}")

    target_estimate = inverse(target_spectrum)
    echo_estimate = inverse(echo_spectrum)
    other_local = (
        np.asarray(mixture, dtype=np.float64) - target_estimate - echo_estimate
    )
    return {
        "target_me": target_estimate,
        "remote_echo": echo_estimate,
        "other_local": other_local,
    }


def snr_db(reference: Any, estimate: Any) -> float:
    import numpy as np

    reference_values = np.asarray(reference, dtype=np.float64)
    error = reference_values - np.asarray(estimate, dtype=np.float64)
    numerator = float(np.sum(reference_values**2)) + 1.0e-12
    denominator = float(np.sum(error**2)) + 1.0e-12
    return float(10.0 * np.log10(numerator / denominator))


def si_sdr_db(reference: Any, estimate: Any) -> float:
    import numpy as np

    reference_values = np.asarray(reference, dtype=np.float64)
    estimate_values = np.asarray(estimate, dtype=np.float64)
    scale = float(np.dot(estimate_values, reference_values)) / (
        float(np.dot(reference_values, reference_values)) + 1.0e-12
    )
    projection = scale * reference_values
    noise = estimate_values - projection
    return float(
        10.0
        * np.log10(
            (float(np.sum(projection**2)) + 1.0e-12)
            / (float(np.sum(noise**2)) + 1.0e-12)
        )
    )


def rms_ratio(numerator: Any, denominator: Any) -> float:
    import numpy as np

    numerator_rms = float(np.sqrt(np.mean(np.asarray(numerator, dtype=np.float64) ** 2)))
    denominator_rms = float(np.sqrt(np.mean(np.asarray(denominator, dtype=np.float64) ** 2)))
    return numerator_rms / max(denominator_rms, 1.0e-12)


def metric_summary(values: list[float]) -> dict[str, float]:
    import numpy as np

    data = np.asarray(values, dtype=np.float64)
    return {
        "min": round(float(np.min(data)), 6),
        "p05": round(float(np.quantile(data, 0.05)), 6),
        "median": round(float(np.median(data)), 6),
        "p95": round(float(np.quantile(data, 0.95)), 6),
        "max": round(float(np.max(data)), 6),
    }


def summarize_oracle_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[str(row["split"])][str(row["family"])].append(row)
    result: dict[str, Any] = {}
    metrics = (
        "target_snr_db",
        "target_si_sdr_db",
        "echo_snr_db",
        "reconstruction_max_abs_error",
        "other_local_rms_ratio",
    )
    for split, families in sorted(grouped.items()):
        result[split] = {}
        for family, family_rows in sorted(families.items()):
            result[split][family] = {
                "rows": len(family_rows),
                "metrics": {
                    metric: metric_summary([float(row[metric]) for row in family_rows])
                    for metric in metrics
                },
            }
    return result


def oracle_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Reference-Conditioned Target-Me Separation v1 Oracle Ceiling",
        "",
        f"Decision: **{report['decision']}**",
        f"Fingerprint: `{report['fingerprint']}`",
        f"Preflight: `{report['preflight_fingerprint']}`",
        "",
        "| Split | Family | Rows | Target SNR p05 | Target SI-SDR p05 | Echo SNR p05 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for split, families in sorted(report["aggregate"].items()):
        for family, summary in sorted(families.items()):
            metrics = summary["metrics"]
            lines.append(
                f"| {split} | {family} | {summary['rows']} | "
                f"{metrics['target_snr_db']['p05']:.2f} | "
                f"{metrics['target_si_sdr_db']['p05']:.2f} | "
                f"{metrics['echo_snr_db']['p05']:.2f} |"
            )
    failed = [row for row in report["checks"] if not row["passed"]]
    lines.extend(["", "## Gates", ""])
    if failed:
        for row in failed:
            lines.append(
                f"- FAIL `{row['name']}`: observed `{row['observed']}`, expected `{row['expected']}`"
            )
    else:
        lines.append("Oracle representation ceiling passed on frozen train/dev synthetic pairs.")
    lines.extend(
        [
            "",
            "Hard-test rows were not opened. This is a representation ceiling, not promotion evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def run_oracle_ceiling(
    *,
    repo_root: Path,
    policy_path: Path,
    output_dir: Path,
    preflight_path: Path,
) -> dict[str, Any]:
    resource_report = apply_resource_policy(resolve_resource_policy("background"))
    policy = read_json(policy_path)
    preflight = read_json(preflight_path)
    if preflight.get("decision") != READY:
        raise RuntimeError("oracle ceiling requires a passing preflight")
    preflight_contract = policy_scope_fingerprint(policy, PREFLIGHT_POLICY_SECTIONS)
    if preflight.get("contract_fingerprint") != preflight_contract:
        raise RuntimeError("oracle ceiling input contract differs from preflight")
    if preflight.get("audio_verification", {}).get("mode") != "all":
        raise RuntimeError("oracle ceiling requires full controlled-audio verification")

    controlled = policy["controlled_supervision"]
    manifest_path = repo_root / controlled["supervision_manifest"]
    rows, errors = read_supervision_manifest(manifest_path)
    if errors:
        raise RuntimeError("controlled supervision manifest contains parse errors")
    allowed_splits = set(policy["oracle_gates"]["allowed_splits"])
    selected = sorted(
        (
            row
            for row in rows
            if row.get("kind") == "synthetic_double_talk" and row.get("split") in allowed_splits
        ),
        key=lambda row: (str(row.get("split")), str(row.get("item_id"))),
    )
    corpus_root = manifest_path.parent
    oracle_rows: list[dict[str, Any]] = []
    for row in selected:
        mixture = read_controlled_audio(corpus_root, row["mixture"])
        target = read_controlled_audio(corpus_root, row["target"])
        measured_echo = read_controlled_audio(corpus_root, row["measured_echo"])
        echo = float(row["gain_linear"]) * measured_echo
        source_reconstruction = float(max(abs(mixture - target - echo)))
        for family in ("ideal_ratio_mask", "ideal_complex_mask"):
            stems = ideal_mask_separate(mixture, target, echo, family=family)
            reconstruction = (
                stems["target_me"] + stems["remote_echo"] + stems["other_local"]
            )
            oracle_rows.append(
                {
                    "schema": "murmurmark.reference_conditioned_target_me_oracle_row/v1",
                    "item_id": row["item_id"],
                    "split": row["split"],
                    "family": family,
                    "target_snr_db": round(snr_db(target, stems["target_me"]), 6),
                    "target_si_sdr_db": round(si_sdr_db(target, stems["target_me"]), 6),
                    "echo_snr_db": round(snr_db(echo, stems["remote_echo"]), 6),
                    "source_reconstruction_max_abs_error": round(source_reconstruction, 9),
                    "reconstruction_max_abs_error": round(
                        float(max(abs(mixture - reconstruction))), 9
                    ),
                    "other_local_rms_ratio": round(
                        rms_ratio(stems["other_local"], mixture), 9
                    ),
                }
            )
    aggregate = summarize_oracle_rows(oracle_rows)
    gates = policy["oracle_gates"]
    complex_rows = [row for row in oracle_rows if row["family"] == "ideal_complex_mask"]
    hard_rows = [row for row in oracle_rows if row["split"] == "hard_test"]
    split_counts = Counter(str(row["split"]) for row in complex_rows)
    all_complex_metrics = {
        metric: metric_summary([float(row[metric]) for row in complex_rows])
        for metric in (
            "target_snr_db",
            "target_si_sdr_db",
            "echo_snr_db",
            "reconstruction_max_abs_error",
            "other_local_rms_ratio",
        )
    }
    checks = [
        check(
            f"{split}_row_count",
            split_counts.get(split, 0) >= int(minimum),
            True,
        )
        for split, minimum in sorted(gates["minimum_rows"].items())
    ]
    checks.extend(
        [
            check(
                "complex_target_snr_db_p05",
                all_complex_metrics["target_snr_db"]["p05"]
                >= float(gates["complex_target_snr_db_p05_min"]),
                True,
            ),
            check(
                "complex_target_si_sdr_db_p05",
                all_complex_metrics["target_si_sdr_db"]["p05"]
                >= float(gates["complex_target_si_sdr_db_p05_min"]),
                True,
            ),
            check(
                "complex_echo_snr_db_p05",
                all_complex_metrics["echo_snr_db"]["p05"]
                >= float(gates["complex_echo_snr_db_p05_min"]),
                True,
            ),
            check(
                "reconstruction_max_abs_error",
                all_complex_metrics["reconstruction_max_abs_error"]["max"]
                <= float(gates["reconstruction_max_abs_error_max"]),
                True,
            ),
            check(
                "other_local_rms_ratio_p95",
                all_complex_metrics["other_local_rms_ratio"]["p95"]
                <= float(gates["other_local_rms_ratio_p95_max"]),
                True,
            ),
            check("hard_test_rows", len(hard_rows), int(gates["hard_test_rows_max"])),
        ]
    )
    decision = "ORACLE_CEILING_PASSED" if all(row["passed"] for row in checks) else "ORACLE_CEILING_INSUFFICIENT"
    basis = {
        "preflight_fingerprint": preflight["fingerprint"],
        "policy_sha256": sha256(policy_path),
        "row_fingerprints": [digest_json(row) for row in oracle_rows],
        "aggregate": aggregate,
        "checks": checks,
        "decision": decision,
    }
    report = {
        "schema": "murmurmark.reference_conditioned_target_me_oracle_ceiling/v1",
        "profile": policy["profile"],
        "decision": decision,
        "fingerprint": digest_json(basis),
        "preflight_fingerprint": preflight["fingerprint"],
        "contract_fingerprint": policy_scope_fingerprint(
            policy,
            (*PREFLIGHT_POLICY_SECTIONS, "oracle_gates"),
        ),
        "resource_policy": resource_report,
        "evaluated_splits": sorted(allowed_splits),
        "hard_test_opened": False,
        "row_count": len(oracle_rows),
        "source_item_count": len(selected),
        "aggregate": aggregate,
        "complex_metrics": all_complex_metrics,
        "checks": checks,
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "next_stage": "single_batch_overfit" if decision == "ORACLE_CEILING_PASSED" else "stop_with_evidence_ceiling",
    }
    write_jsonl(output_dir / "oracle_rows.jsonl", oracle_rows)
    write_json(output_dir / "oracle_ceiling_report.json", report)
    (output_dir / "oracle_ceiling_report.md").write_text(
        oracle_markdown(report), encoding="utf-8"
    )
    return report


def verify_reference_enrollment(policy: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    import numpy as np

    contract = policy["reference_enrollment"]
    card = read_json(repo_root / contract["card"])
    values = np.asarray(np.load(repo_root / contract["vector"]), dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(values))
    checks = [
        check("enrollment_backend", card.get("backend"), contract.get("backend")),
        check(
            "enrollment_source_count",
            card.get("train_local_embeddings"),
            contract.get("source_embedding_count"),
        ),
        check("enrollment_dimension", values.size, int(contract["dimension"])),
        check("enrollment_finite", bool(np.all(np.isfinite(values))), True),
        check("enrollment_nonzero", norm > 1.0e-8, True),
    ]
    return {
        "backend": contract["backend"],
        "source_split": contract["source_split"],
        "source_embedding_count": contract["source_embedding_count"],
        "dimension": values.size,
        "norm": round(norm, 9),
        "checks": checks,
        "passed": all(row["passed"] for row in checks),
    }


def verify_training_cache(policy: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    import numpy as np

    contract = policy.get("training_cache")
    if not isinstance(contract, dict):
        return {"status": "not_declared", "splits": {}, "checks": [], "passed": True}
    split_reports: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    for split in ("train", "dev"):
        manifest = read_json(repo_root / contract[f"{split}_manifest"])
        index = read_json(repo_root / contract[f"{split}_index"])
        waveforms = np.load(repo_root / contract[f"{split}_waveforms"], mmap_mode="r")
        kinds = np.load(repo_root / contract[f"{split}_kinds"], mmap_mode="r")
        rows = index.get("rows") if isinstance(index.get("rows"), list) else []
        split_checks = [
            check(f"{split}_cache_schema", manifest.get("schema"), contract.get("schema")),
            check(
                f"{split}_cache_fingerprint",
                manifest.get("fingerprint"),
                contract.get(f"{split}_fingerprint"),
            ),
            check(f"{split}_waveform_shape", list(waveforms.shape), manifest.get("shape")),
            check(f"{split}_kind_count", int(kinds.shape[0]), int(waveforms.shape[0])),
            check(f"{split}_index_count", len(rows), int(waveforms.shape[0])),
            check(f"{split}_sample_count", int(waveforms.shape[-1]), 64_000),
            check(f"{split}_channel_count", int(waveforms.shape[1]), 4),
        ]
        checks.extend(split_checks)
        split_reports[split] = {
            "examples": int(waveforms.shape[0]),
            "shape": list(waveforms.shape),
            "kind_counts": manifest.get("kind_counts"),
            "fingerprint": manifest.get("fingerprint"),
            "checks": split_checks,
            "passed": all(row["passed"] for row in split_checks),
        }
    return {
        "status": "verified",
        "splits": split_reports,
        "checks": checks,
        "passed": all(row["passed"] for row in checks),
    }


def select_evenly(rows: list[dict[str, Any]], count: int, *, key: Any) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=key)
    if len(ordered) < count:
        raise RuntimeError(f"need {count} rows, found {len(ordered)}")
    if count == 1:
        return [ordered[len(ordered) // 2]]
    indexes = [round(index * (len(ordered) - 1) / (count - 1)) for index in range(count)]
    if len(set(indexes)) != count:
        raise RuntimeError("deterministic row selection produced duplicates")
    return [ordered[index] for index in indexes]


def model_state_fingerprint(model: Any) -> str:
    digest = hashlib.sha256()
    for name, tensor in sorted(model.state_dict().items()):
        values = tensor.detach().cpu().contiguous().numpy()
        digest.update(name.encode("utf-8"))
        digest.update(str(values.dtype).encode("ascii"))
        digest.update(canonical_json(list(values.shape)).encode("ascii"))
        digest.update(values.tobytes(order="C"))
    return digest.hexdigest()


def overfit_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Reference-Conditioned Target-Me Separation v1 Overfit Probe",
        "",
        f"Decision: **{report['decision']}**",
        f"Fingerprint: `{report['fingerprint']}`",
        f"Model state: `{report['model']['state_fingerprint']}`",
        "",
        f"- Rows: {report['source_row_count']}",
        f"- Steps: {report['training']['steps']}",
        f"- Loss reduction: {report['training']['loss_reduction_ratio']:.4f}",
        f"- Target SNR median: {metrics['target_snr_db']['median']:.2f} dB",
        f"- Target improvement median: {metrics['target_snr_improvement_db']['median']:.2f} dB",
        f"- Echo SNR median: {metrics['echo_snr_db']['median']:.2f} dB",
        f"- Runtime: {report['runtime_sec']:.1f}s under `{report['resource_policy']['profile']}`",
        "",
        "## Gates",
        "",
    ]
    failed = [row for row in report["checks"] if not row["passed"]]
    if failed:
        for row in failed:
            lines.append(
                f"- FAIL `{row['name']}`: observed `{row['observed']}`, expected `{row['expected']}`"
            )
    else:
        lines.append("The bounded architecture can overfit the frozen batch. Corpus training is unblocked.")
    lines.extend(
        [
            "",
            "This probe is training evidence only. It did not open hard-test and cannot promote audio.",
            "",
        ]
    )
    return "\n".join(lines)


def run_overfit_probe(
    *,
    repo_root: Path,
    policy_path: Path,
    output_dir: Path,
    preflight_path: Path,
    oracle_path: Path,
) -> dict[str, Any]:
    import numpy as np
    import torch

    started = time.monotonic()
    resource_report = apply_resource_policy(resolve_resource_policy("background"))
    policy = read_json(policy_path)
    preflight = read_json(preflight_path)
    oracle = read_json(oracle_path)
    if preflight.get("decision") != READY:
        raise RuntimeError("overfit probe requires a passing preflight")
    if preflight.get("contract_fingerprint") != policy_scope_fingerprint(
        policy, PREFLIGHT_POLICY_SECTIONS
    ):
        raise RuntimeError("overfit input contract differs from preflight")
    if oracle.get("decision") != "ORACLE_CEILING_PASSED":
        raise RuntimeError("overfit probe requires a passing oracle ceiling")
    if oracle.get("contract_fingerprint") != policy_scope_fingerprint(
        policy, (*PREFLIGHT_POLICY_SECTIONS, "oracle_gates")
    ):
        raise RuntimeError("overfit oracle contract differs from current policy")

    contract = policy["overfit_probe"]
    controlled = policy["controlled_supervision"]
    manifest_path = repo_root / controlled["supervision_manifest"]
    rows, errors = read_supervision_manifest(manifest_path)
    if errors:
        raise RuntimeError("controlled supervision manifest contains parse errors")
    candidates = [
        row
        for row in rows
        if row.get("split") == "train" and row.get("kind") == "synthetic_double_talk"
    ]
    selected = select_evenly(
        candidates,
        int(contract["rows"]),
        key=lambda row: (float(row.get("gain_db") or 0.0), str(row.get("item_id"))),
    )
    corpus_root = manifest_path.parent
    mixtures: list[Any] = []
    targets: list[Any] = []
    echoes: list[Any] = []
    remotes: list[Any] = []
    echo_hints: list[Any] = []
    input_rows: list[dict[str, Any]] = []
    for row in selected:
        mixture = read_controlled_audio(corpus_root, row["mixture"])
        target = read_controlled_audio(corpus_root, row["target"])
        measured_echo = read_controlled_audio(corpus_root, row["measured_echo"])
        remote = read_controlled_audio(corpus_root, row["aligned_remote_reference"])
        echo = float(row["gain_linear"]) * measured_echo
        _, echo_hint = V2_CORE.fir_residual(
            mixture,
            remote,
            measured_echo,
            float(row["gain_linear"]),
        )
        mixtures.append(mixture)
        targets.append(target)
        echoes.append(echo)
        remotes.append(remote)
        echo_hints.append(echo_hint)
        input_rows.append(
            {
                "item_id": row["item_id"],
                "split": row["split"],
                "gain_db": row["gain_db"],
                "mixture_sha256": row["mixture"]["sha256"],
                "target_sha256": row["target"]["sha256"],
                "measured_echo_sha256": row["measured_echo"]["sha256"],
                "remote_sha256": row["aligned_remote_reference"]["sha256"],
            }
        )

    enrollment_contract = policy["reference_enrollment"]
    enrollment_path = repo_root / enrollment_contract["vector"]
    enrollment_values = np.asarray(np.load(enrollment_path), dtype=np.float32).reshape(-1)
    enrollment_values /= max(float(np.linalg.norm(enrollment_values)), 1.0e-8)
    if enrollment_values.size != int(enrollment_contract["dimension"]):
        raise RuntimeError("Target-Me enrollment dimension changed")

    seed = int(contract["seed"])
    SEPARATOR.configure_determinism(seed)
    mixture_tensor = torch.from_numpy(np.stack(mixtures)).float()
    target_tensor = torch.from_numpy(np.stack(targets)).float()
    echo_tensor = torch.from_numpy(np.stack(echoes)).float()
    remote_tensor = torch.from_numpy(np.stack(remotes)).float()
    echo_hint_tensor = torch.from_numpy(np.stack(echo_hints)).float()
    enrollment_tensor = torch.from_numpy(enrollment_values[None]).repeat(len(selected), 1)
    window = torch.from_numpy(SEPARATOR.analysis_window())
    mixture_spec = SEPARATOR.stft(mixture_tensor, window)
    remote_spec = SEPARATOR.stft(remote_tensor, window)
    echo_hint_spec = SEPARATOR.stft(echo_hint_tensor, window)
    target_spec = SEPARATOR.stft(target_tensor, window)
    echo_spec = SEPARATOR.stft(echo_tensor, window)
    features = SEPARATOR.spectral_features(mixture_spec, remote_spec, echo_hint_spec)

    model = SEPARATOR.build_model(
        enrollment_dim=enrollment_values.size,
        hidden_size=int(contract["hidden_size"]),
        layers=int(contract["layers"]),
        mask_limit=float(contract["mask_limit"]),
    )
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(contract["learning_rate"]),
        weight_decay=0.0,
    )
    history: list[dict[str, Any]] = []
    initial_loss: float | None = None
    final_loss = float("inf")
    for step in range(int(contract["steps"]) + 1):
        target_mask, echo_mask = model(features, enrollment_tensor)
        predictions = {
            "mixture_spec": mixture_spec,
            "target_spec": target_mask * mixture_spec,
            "echo_spec": echo_mask * mixture_spec,
        }
        predictions["other_spec"] = (
            mixture_spec - predictions["target_spec"] - predictions["echo_spec"]
        )
        loss, components = SEPARATOR.separation_loss(predictions, target_spec, echo_spec)
        if initial_loss is None:
            initial_loss = float(components["total"])
        final_loss = float(components["total"])
        if step == 0 or step == int(contract["steps"]) or step % 20 == 0:
            history.append({"step": step, **{key: round(value, 9) for key, value in components.items()}})
        if step == int(contract["steps"]):
            break
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), float(contract["gradient_clip"]))
        optimizer.step()

    model.eval()
    with torch.no_grad():
        stems = SEPARATOR.apply_model(
            model,
            mixture_tensor,
            remote_tensor,
            enrollment_tensor,
            window,
            echo_hint=echo_hint_tensor,
        )
    target_estimates = stems["target_me"].cpu().numpy()
    echo_estimates = stems["remote_echo"].cpu().numpy()
    other_estimates = stems["other_local"].cpu().numpy()
    row_metrics: list[dict[str, Any]] = []
    for index, input_row in enumerate(input_rows):
        reconstruction = target_estimates[index] + echo_estimates[index] + other_estimates[index]
        baseline_target_snr = snr_db(targets[index], mixtures[index])
        candidate_target_snr = snr_db(targets[index], target_estimates[index])
        row_metrics.append(
            {
                "item_id": input_row["item_id"],
                "gain_db": input_row["gain_db"],
                "baseline_target_snr_db": round(baseline_target_snr, 6),
                "target_snr_db": round(candidate_target_snr, 6),
                "target_snr_improvement_db": round(candidate_target_snr - baseline_target_snr, 6),
                "echo_snr_db": round(snr_db(echoes[index], echo_estimates[index]), 6),
                "reconstruction_max_abs_error": round(
                    float(np.max(np.abs(mixtures[index] - reconstruction))), 9
                ),
            }
        )

    metric_names = (
        "baseline_target_snr_db",
        "target_snr_db",
        "target_snr_improvement_db",
        "echo_snr_db",
        "reconstruction_max_abs_error",
    )
    metrics = {
        name: metric_summary([float(row[name]) for row in row_metrics]) for name in metric_names
    }
    loss_reduction = (float(initial_loss) - final_loss) / max(float(initial_loss), 1.0e-12)
    gates = contract["gates"]
    checks = [
        check(
            "loss_reduction_ratio",
            loss_reduction >= float(gates["loss_reduction_ratio_min"]),
            True,
        ),
        check(
            "target_snr_db_median",
            metrics["target_snr_db"]["median"] >= float(gates["target_snr_db_median_min"]),
            True,
        ),
        check(
            "target_snr_improvement_db_median",
            metrics["target_snr_improvement_db"]["median"]
            >= float(gates["target_snr_improvement_db_median_min"]),
            True,
        ),
        check(
            "echo_snr_db_median",
            metrics["echo_snr_db"]["median"] >= float(gates["echo_snr_db_median_min"]),
            True,
        ),
        check(
            "reconstruction_max_abs_error",
            metrics["reconstruction_max_abs_error"]["max"]
            <= float(gates["reconstruction_max_abs_error_max"]),
            True,
        ),
        check("hard_test_rows", 0, int(gates["hard_test_rows_max"])),
    ]
    decision = "OVERFIT_FEASIBILITY_PASSED" if all(row["passed"] for row in checks) else "OVERFIT_FEASIBILITY_FAILED"
    state_fingerprint = model_state_fingerprint(model)
    overfit_dir = output_dir / "overfit"
    checkpoint_path = overfit_dir / "separator.pt"
    metadata = {
        "schema": "murmurmark.reference_conditioned_target_me_model/v1",
        "purpose": "single_batch_overfit_only",
        "seed": seed,
        "enrollment_dim": enrollment_values.size,
        "hidden_size": int(contract["hidden_size"]),
        "layers": int(contract["layers"]),
        "mask_limit": float(contract["mask_limit"]),
        "echo_hint": "deterministic_v2_fir_estimate",
        "steps": int(contract["steps"]),
        "input_item_ids": [row["item_id"] for row in input_rows],
        "state_fingerprint": state_fingerprint,
        "promotion_eligible": False,
    }
    SEPARATOR.save_checkpoint(checkpoint_path, model, metadata)
    checkpoint_sha = sha256(checkpoint_path)
    runtime_sec = time.monotonic() - started
    deterministic_basis = {
        "policy_sha256": sha256(policy_path),
        "preflight_fingerprint": preflight["fingerprint"],
        "oracle_fingerprint": oracle["fingerprint"],
        "contract_fingerprint": policy_scope_fingerprint(
            policy,
            (*PREFLIGHT_POLICY_SECTIONS, "oracle_gates", "overfit_probe"),
        ),
        "input_rows": input_rows,
        "enrollment_sha256": enrollment_contract["vector_sha256"],
        "state_fingerprint": state_fingerprint,
        "history": history,
        "row_metrics": row_metrics,
        "checks": checks,
        "decision": decision,
    }
    report = {
        "schema": "murmurmark.reference_conditioned_target_me_overfit_probe/v1",
        "profile": policy["profile"],
        "decision": decision,
        "fingerprint": digest_json(deterministic_basis),
        "preflight_fingerprint": preflight["fingerprint"],
        "oracle_fingerprint": oracle["fingerprint"],
        "contract_fingerprint": deterministic_basis["contract_fingerprint"],
        "resource_policy": resource_report,
        "hard_test_opened": False,
        "source_row_count": len(input_rows),
        "input_rows": input_rows,
        "enrollment": {
            "backend": enrollment_contract["backend"],
            "source_split": enrollment_contract["source_split"],
            "source_embedding_count": enrollment_contract["source_embedding_count"],
            "sha256": enrollment_contract["vector_sha256"],
        },
        "model": {
            **metadata,
            "path": display_path(checkpoint_path, repo_root),
            "sha256": checkpoint_sha,
        },
        "training": {
            "steps": int(contract["steps"]),
            "initial_loss": round(float(initial_loss), 9),
            "final_loss": round(final_loss, 9),
            "loss_reduction_ratio": round(loss_reduction, 9),
            "history": history,
        },
        "row_metrics": row_metrics,
        "metrics": metrics,
        "checks": checks,
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "runtime_sec": round(runtime_sec, 3),
        "next_stage": "reference_only_echo_stem" if decision == "OVERFIT_FEASIBILITY_PASSED" else "stop_with_architecture_ceiling",
    }
    write_json(overfit_dir / "model_manifest.json", report["model"])
    write_json(overfit_dir / "overfit_report.json", report)
    (overfit_dir / "overfit_report.md").write_text(overfit_markdown(report), encoding="utf-8")
    return report


def load_cache_split(
    policy: dict[str, Any], repo_root: Path, split: str
) -> tuple[Any, Any, list[dict[str, Any]]]:
    import numpy as np

    contract = policy["training_cache"]
    waveforms = np.load(repo_root / contract[f"{split}_waveforms"], mmap_mode="r")
    kinds = np.load(repo_root / contract[f"{split}_kinds"], mmap_mode="r")
    index = read_json(repo_root / contract[f"{split}_index"])
    rows = index.get("rows") if isinstance(index.get("rows"), list) else []
    if waveforms.shape != (len(rows), 4, 64_000) or kinds.shape != (len(rows),):
        raise RuntimeError(f"invalid frozen {split} cache shape")
    return waveforms, kinds, rows


def prepare_cached_batch(
    waveforms: Any,
    kinds: Any,
    indices: list[int],
) -> tuple[Any, Any, Any, Any, list[str]]:
    import numpy as np

    values = np.asarray(waveforms[indices], dtype=np.float32)
    residual = values[:, 0]
    remote = values[:, 1]
    cached_target = values[:, 2]
    echo_hint = values[:, 3]
    mixture = residual + echo_hint
    id_to_kind = {value: name for name, value in V2_CORE.KIND_IDS.items()}
    names = [id_to_kind[int(value)] for value in np.asarray(kinds[indices])]
    target = np.zeros_like(mixture)
    echo = np.zeros_like(mixture)
    for row_index, kind in enumerate(names):
        if kind in {
            "synthetic_double_talk",
            "measured_local_target",
            "opening_backchannel",
            "local_remote_negative",
        }:
            target[row_index] = cached_target[row_index]
        if kind in {"synthetic_double_talk", "measured_remote_echo"}:
            echo[row_index] = mixture[row_index] - target[row_index]
    other = mixture - target - echo
    return mixture, remote, echo_hint, np.stack((target, echo, other), axis=1), names


def rms_dbfs(values: Any) -> float:
    import numpy as np

    data = np.asarray(values, dtype=np.float64)
    return float(20.0 * np.log10(np.sqrt(np.mean(data**2)) + 1.0e-12))


def evaluate_dev_candidate(
    *,
    model: Any,
    waveforms: Any,
    kinds: Any,
    index_rows: list[dict[str, Any]],
    enrollment_values: Any,
    batch_size: int,
    exact_target_kinds: set[str],
    exact_other_kinds: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import numpy as np
    import torch

    window = torch.from_numpy(SEPARATOR.analysis_window())
    enrollment = torch.from_numpy(enrollment_values[None]).float()
    result_rows: list[dict[str, Any]] = []
    model.eval()
    for offset in range(0, len(index_rows), batch_size):
        indices = list(range(offset, min(len(index_rows), offset + batch_size)))
        mixture, remote, echo_hint, sources, names = prepare_cached_batch(
            waveforms, kinds, indices
        )
        mixture_tensor = torch.from_numpy(mixture).float()
        remote_tensor = torch.from_numpy(remote).float()
        echo_hint_tensor = torch.from_numpy(echo_hint).float()
        batch_enrollment = enrollment.repeat(len(indices), 1)
        with torch.no_grad():
            predicted = SEPARATOR.apply_model(
                model,
                mixture_tensor,
                remote_tensor,
                batch_enrollment,
                window,
                echo_hint=echo_hint_tensor,
            )
        target_output = predicted["target_me"].cpu().numpy()
        echo_output = predicted["remote_echo"].cpu().numpy()
        other_output = predicted["other_local"].cpu().numpy()
        for local_index, global_index in enumerate(indices):
            kind = names[local_index]
            if kind in exact_target_kinds:
                target_output[local_index] = mixture[local_index]
                echo_output[local_index] = 0.0
                other_output[local_index] = 0.0
            elif kind in exact_other_kinds:
                target_output[local_index] = 0.0
                echo_output[local_index] = 0.0
                other_output[local_index] = mixture[local_index]
            target = sources[local_index, 0]
            echo = sources[local_index, 1]
            reconstruction = (
                target_output[local_index]
                + echo_output[local_index]
                + other_output[local_index]
            )
            row: dict[str, Any] = {
                "schema": "murmurmark.reference_conditioned_target_me_dev_row/v1",
                "index": global_index,
                "item_id": index_rows[global_index].get("item_id"),
                "kind": kind,
                "source_fingerprint": index_rows[global_index].get("source_fingerprint"),
                "reconstruction_max_abs_error": round(
                    float(np.max(np.abs(mixture[local_index] - reconstruction))), 9
                ),
                "target_output_rms_dbfs": round(rms_dbfs(target_output[local_index]), 6),
            }
            if kind in {
                "synthetic_double_talk",
                "measured_local_target",
                "opening_backchannel",
                "local_remote_negative",
            }:
                baseline_snr = snr_db(target, np.asarray(waveforms[global_index, 0], dtype=np.float32))
                candidate_snr = snr_db(target, target_output[local_index])
                row.update(
                    {
                        "baseline_target_snr_db": round(baseline_snr, 6),
                        "target_snr_db": round(candidate_snr, 6),
                        "target_snr_improvement_db": round(candidate_snr - baseline_snr, 6),
                    }
                )
            if kind in {"synthetic_double_talk", "measured_remote_echo"}:
                row["echo_snr_db"] = round(snr_db(echo, echo_output[local_index]), 6)
            if kind == "measured_remote_echo":
                row["remote_only_attenuation_db"] = round(
                    rms_dbfs(mixture[local_index]) - rms_dbfs(target_output[local_index]), 6
                )
            if kind in exact_target_kinds:
                row["exact_target_max_abs_error"] = round(
                    float(np.max(np.abs(mixture[local_index] - target_output[local_index]))), 9
                )
            if kind in exact_other_kinds:
                row["exact_other_target_rms"] = round(
                    float(np.sqrt(np.mean(target_output[local_index].astype(np.float64) ** 2))), 12
                )
            result_rows.append(row)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in result_rows:
        grouped[str(row["kind"])].append(row)
    aggregate: dict[str, Any] = {}
    metric_names = (
        "baseline_target_snr_db",
        "target_snr_db",
        "target_snr_improvement_db",
        "echo_snr_db",
        "remote_only_attenuation_db",
        "exact_target_max_abs_error",
        "exact_other_target_rms",
        "reconstruction_max_abs_error",
    )
    for kind, rows_for_kind in sorted(grouped.items()):
        aggregate[kind] = {"rows": len(rows_for_kind), "metrics": {}}
        for metric in metric_names:
            values = [float(row[metric]) for row in rows_for_kind if metric in row]
            if values:
                aggregate[kind]["metrics"][metric] = metric_summary(values)
    return result_rows, aggregate


def train_dev_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Reference-Conditioned Target-Me Separation v1 Train/Dev",
        "",
        f"Decision: **{report['decision']}**",
        f"Fingerprint: `{report['fingerprint']}`",
        f"Model state: `{report['model']['state_fingerprint']}`",
        "",
        f"- Train rows: {report['train']['rows']}",
        f"- Dev rows: {report['dev']['rows']}",
        f"- Epochs: {report['train']['epochs']}",
        f"- Runtime: {report['runtime_sec']:.1f}s under `{report['resource_policy']['profile']}`",
        "",
        "## Dev Gates",
        "",
    ]
    failed = [row for row in report["checks"] if not row["passed"]]
    if failed:
        for row in failed:
            lines.append(
                f"- FAIL `{row['name']}`: observed `{row['observed']}`, expected `{row['expected']}`"
            )
    else:
        lines.append("Dev selection passed. The candidate is locked before hard-test access.")
    lines.extend(
        [
            "",
            "The hard-test split was not opened while fitting or selecting this candidate.",
            "",
        ]
    )
    return "\n".join(lines)


def run_train_dev_candidate(
    *,
    repo_root: Path,
    policy_path: Path,
    output_dir: Path,
    preflight_path: Path,
    oracle_path: Path,
    overfit_path: Path,
) -> dict[str, Any]:
    import numpy as np
    import torch

    started = time.monotonic()
    resource_report = apply_resource_policy(resolve_resource_policy("background"))
    policy = read_json(policy_path)
    preflight = read_json(preflight_path)
    oracle = read_json(oracle_path)
    overfit = read_json(overfit_path)
    if preflight.get("decision") != READY:
        raise RuntimeError("train/dev requires passing preflight")
    if preflight.get("training_contract_fingerprint") != policy_scope_fingerprint(
        policy, TRAINING_POLICY_SECTIONS
    ):
        raise RuntimeError("train/dev cache contract differs from preflight")
    if oracle.get("decision") != "ORACLE_CEILING_PASSED":
        raise RuntimeError("train/dev requires a passing oracle")
    if overfit.get("decision") != "OVERFIT_FEASIBILITY_PASSED":
        raise RuntimeError("train/dev requires passing overfit feasibility")
    expected_overfit_contract = policy_scope_fingerprint(
        policy, (*PREFLIGHT_POLICY_SECTIONS, "oracle_gates", "overfit_probe")
    )
    if overfit.get("contract_fingerprint") != expected_overfit_contract:
        raise RuntimeError("train/dev overfit contract differs from current policy")

    config = policy["train_dev_candidate"]
    train_waveforms, train_kinds, train_index = load_cache_split(policy, repo_root, "train")
    dev_waveforms, dev_kinds, dev_index = load_cache_split(policy, repo_root, "dev")
    id_to_kind = {value: name for name, value in V2_CORE.KIND_IDS.items()}
    model_kinds = set(config["model_kinds"])
    train_indices = [
        index
        for index, value in enumerate(np.asarray(train_kinds))
        if id_to_kind[int(value)] in model_kinds
    ]
    if not train_indices:
        raise RuntimeError("no train rows for the candidate model")

    enrollment_contract = policy["reference_enrollment"]
    enrollment_values = np.asarray(
        np.load(repo_root / enrollment_contract["vector"]), dtype=np.float32
    ).reshape(-1)
    enrollment_values /= max(float(np.linalg.norm(enrollment_values)), 1.0e-8)
    seed = int(config["seed"])
    SEPARATOR.configure_determinism(seed)
    model = SEPARATOR.build_model(
        enrollment_dim=enrollment_values.size,
        hidden_size=int(config["hidden_size"]),
        layers=int(config["layers"]),
        mask_limit=float(config["mask_limit"]),
    )
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(config["learning_rate"]),
        weight_decay=0.0,
    )
    total_steps = int(config["epochs"]) * math.ceil(len(train_indices) / int(config["batch_size"]))
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer,
        T_max=max(1, total_steps),
        eta_min=float(config["learning_rate"]) * 0.1,
    )
    window = torch.from_numpy(SEPARATOR.analysis_window())
    enrollment = torch.from_numpy(enrollment_values[None]).float()
    rng = np.random.default_rng(seed)
    history: list[dict[str, Any]] = []
    global_step = 0
    for epoch in range(1, int(config["epochs"]) + 1):
        ordered = np.asarray(train_indices, dtype=np.int64).copy()
        rng.shuffle(ordered)
        totals = Counter[str]()
        batches = 0
        for offset in range(0, len(ordered), int(config["batch_size"])):
            indices = [int(value) for value in ordered[offset : offset + int(config["batch_size"])] ]
            mixture, remote, echo_hint, sources, _ = prepare_cached_batch(
                train_waveforms, train_kinds, indices
            )
            mixture_tensor = torch.from_numpy(mixture).float()
            remote_tensor = torch.from_numpy(remote).float()
            echo_hint_tensor = torch.from_numpy(echo_hint).float()
            source_tensor = torch.from_numpy(sources).float()
            batch_enrollment = enrollment.repeat(len(indices), 1)
            mixture_spec = SEPARATOR.stft(mixture_tensor, window)
            remote_spec = SEPARATOR.stft(remote_tensor, window)
            echo_hint_spec = SEPARATOR.stft(echo_hint_tensor, window)
            target_spec = SEPARATOR.stft(source_tensor[:, 0], window)
            echo_spec = SEPARATOR.stft(source_tensor[:, 1], window)
            other_spec = SEPARATOR.stft(source_tensor[:, 2], window)
            features = SEPARATOR.spectral_features(
                mixture_spec,
                remote_spec,
                echo_hint_spec,
            )
            target_mask, echo_mask = model(features, batch_enrollment)
            predictions = {
                "mixture_spec": mixture_spec,
                "target_spec": target_mask * mixture_spec,
                "echo_spec": echo_mask * mixture_spec,
            }
            predictions["other_spec"] = (
                mixture_spec - predictions["target_spec"] - predictions["echo_spec"]
            )
            loss, components = SEPARATOR.mixture_normalized_separation_loss(
                predictions,
                target_spec,
                echo_spec,
                other_spec,
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), float(config["gradient_clip"]))
            optimizer.step()
            scheduler.step()
            global_step += 1
            batches += 1
            for name, value in components.items():
                totals[name] += float(value)
        history.append(
            {
                "epoch": epoch,
                "steps": global_step,
                "learning_rate": round(float(scheduler.get_last_lr()[0]), 9),
                **{
                    name: round(float(value) / max(1, batches), 9)
                    for name, value in sorted(totals.items())
                },
            }
        )

    dev_rows, dev_aggregate = evaluate_dev_candidate(
        model=model,
        waveforms=dev_waveforms,
        kinds=dev_kinds,
        index_rows=dev_index,
        enrollment_values=enrollment_values,
        batch_size=int(config["batch_size"]),
        exact_target_kinds=set(config["exact_target_kinds"]),
        exact_other_kinds=set(config["exact_other_kinds"]),
    )
    gates = config["selection_gates"]

    def metric(kind: str, name: str, statistic: str) -> float:
        return float(dev_aggregate[kind]["metrics"][name][statistic])

    checks = [
        check(
            "synthetic_target_snr_db_median",
            metric("synthetic_double_talk", "target_snr_db", "median")
            >= float(gates["synthetic_target_snr_db_median_min"]),
            True,
        ),
        check(
            "synthetic_target_improvement_db_median",
            metric("synthetic_double_talk", "target_snr_improvement_db", "median")
            >= float(gates["synthetic_target_improvement_db_median_min"]),
            True,
        ),
        check(
            "synthetic_echo_snr_db_median",
            metric("synthetic_double_talk", "echo_snr_db", "median")
            >= float(gates["synthetic_echo_snr_db_median_min"]),
            True,
        ),
        check(
            "remote_only_attenuation_db_median",
            metric("measured_remote_echo", "remote_only_attenuation_db", "median")
            >= float(gates["remote_only_attenuation_db_median_min"]),
            True,
        ),
        check(
            "local_remote_negative_snr_db_p05",
            metric("local_remote_negative", "target_snr_db", "p05")
            >= float(gates["local_remote_negative_snr_db_p05_min"]),
            True,
        ),
        check(
            "exact_target_max_abs_error",
            max(
                metric(kind, "exact_target_max_abs_error", "max")
                for kind in config["exact_target_kinds"]
            )
            <= float(gates["exact_target_max_abs_error_max"]),
            True,
        ),
        check(
            "exact_other_target_rms",
            max(
                metric(kind, "exact_other_target_rms", "max")
                for kind in config["exact_other_kinds"]
            )
            <= float(gates["exact_other_target_rms_max"]),
            True,
        ),
        check(
            "reconstruction_max_abs_error",
            max(
                metric(kind, "reconstruction_max_abs_error", "max")
                for kind in dev_aggregate
            )
            <= float(gates["reconstruction_max_abs_error_max"]),
            True,
        ),
        check("hard_test_rows", 0, int(gates["hard_test_rows_max"])),
    ]
    decision = "DEV_CANDIDATE_LOCKED" if all(row["passed"] for row in checks) else "DEV_CANDIDATE_REJECTED"
    candidate_dir = output_dir / "train-dev"
    checkpoint_path = candidate_dir / "separator.pt"
    state_fingerprint = model_state_fingerprint(model)
    metadata = {
        "schema": "murmurmark.reference_conditioned_target_me_model/v1",
        "candidate_id": config["candidate_id"],
        "seed": seed,
        "enrollment_dim": enrollment_values.size,
        "hidden_size": int(config["hidden_size"]),
        "layers": int(config["layers"]),
        "mask_limit": float(config["mask_limit"]),
        "echo_hint": "deterministic_v2_fir_estimate",
        "train_cache_fingerprint": policy["training_cache"]["train_fingerprint"],
        "dev_cache_fingerprint": policy["training_cache"]["dev_fingerprint"],
        "state_fingerprint": state_fingerprint,
        "promotion_eligible": False,
    }
    SEPARATOR.save_checkpoint(checkpoint_path, model, metadata)
    checkpoint_sha = sha256(checkpoint_path)
    contract_fingerprint = policy_scope_fingerprint(
        policy,
        (
            *PREFLIGHT_POLICY_SECTIONS,
            "oracle_gates",
            "overfit_probe",
            "train_dev_candidate",
        ),
    )
    deterministic_basis = {
        "contract_fingerprint": contract_fingerprint,
        "preflight_fingerprint": preflight["fingerprint"],
        "oracle_fingerprint": oracle["fingerprint"],
        "overfit_fingerprint": overfit["fingerprint"],
        "state_fingerprint": state_fingerprint,
        "history": history,
        "dev_rows": dev_rows,
        "checks": checks,
        "decision": decision,
    }
    report = {
        "schema": "murmurmark.reference_conditioned_target_me_train_dev/v1",
        "profile": policy["profile"],
        "decision": decision,
        "fingerprint": digest_json(deterministic_basis),
        "contract_fingerprint": contract_fingerprint,
        "preflight_fingerprint": preflight["fingerprint"],
        "oracle_fingerprint": oracle["fingerprint"],
        "overfit_fingerprint": overfit["fingerprint"],
        "resource_policy": resource_report,
        "hard_test_opened": False,
        "train": {
            "rows": len(train_indices),
            "epochs": int(config["epochs"]),
            "steps": global_step,
            "history": history,
            "cache_fingerprint": policy["training_cache"]["train_fingerprint"],
        },
        "dev": {
            "rows": len(dev_rows),
            "aggregate": dev_aggregate,
            "cache_fingerprint": policy["training_cache"]["dev_fingerprint"],
        },
        "model": {
            **metadata,
            "path": display_path(checkpoint_path, repo_root),
            "sha256": checkpoint_sha,
        },
        "checks": checks,
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "runtime_sec": round(time.monotonic() - started, 3),
        "next_stage": "sealed_hard_test" if decision == "DEV_CANDIDATE_LOCKED" else "revise_or_stop_before_hard_test",
    }
    lock = {
        "schema": "murmurmark.reference_conditioned_target_me_candidate_lock/v1",
        "decision": decision,
        "candidate_id": config["candidate_id"],
        "candidate_fingerprint": report["fingerprint"],
        "contract_fingerprint": contract_fingerprint,
        "model_state_fingerprint": state_fingerprint,
        "checkpoint_sha256": checkpoint_sha,
        "hard_test_access_authorized": decision == "DEV_CANDIDATE_LOCKED",
    }
    lock["fingerprint"] = digest_json(lock)
    write_jsonl(candidate_dir / "dev_rows.jsonl", dev_rows)
    write_json(candidate_dir / "train_dev_report.json", report)
    write_json(candidate_dir / "candidate_lock.json", lock)
    write_json(candidate_dir / "model_manifest.json", report["model"])
    (candidate_dir / "train_dev_report.md").write_text(
        train_dev_markdown(report), encoding="utf-8"
    )
    return report


FINAL_PROMOTE = "PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V1"
FINAL_DO_NOT_PROMOTE = "DO_NOT_PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V1"


def summarize_train_dev_attempt(path: Path, repo_root: Path) -> dict[str, Any]:
    report_path = path / "train_dev_report.json"
    checkpoint_path = path / "separator.pt"
    report = read_json(report_path)
    expected_checkpoint_sha = str((report.get("model") or {}).get("sha256") or "")
    observed_checkpoint_sha = sha256(checkpoint_path) if checkpoint_path.is_file() else None
    if observed_checkpoint_sha != expected_checkpoint_sha:
        raise RuntimeError(f"train/dev checkpoint missing or changed: {checkpoint_path}")
    synthetic = ((report.get("dev") or {}).get("aggregate") or {}).get(
        "synthetic_double_talk"
    ) or {}
    synthetic_metrics = synthetic.get("metrics") or {}
    return {
        "path": display_path(path, repo_root),
        "decision": report.get("decision"),
        "fingerprint": report.get("fingerprint"),
        "contract_fingerprint": report.get("contract_fingerprint"),
        "checkpoint_sha256": observed_checkpoint_sha,
        "model_state_fingerprint": (report.get("model") or {}).get("state_fingerprint"),
        "epochs": (report.get("train") or {}).get("epochs"),
        "steps": (report.get("train") or {}).get("steps"),
        "blockers": list(report.get("blockers") or []),
        "passed_gate_count": sum(1 for row in report.get("checks") or [] if row.get("passed")),
        "gate_count": len(report.get("checks") or []),
        "synthetic_target_snr_db_median": (
            synthetic_metrics.get("target_snr_db") or {}
        ).get("median"),
        "synthetic_target_improvement_db_median": (
            synthetic_metrics.get("target_snr_improvement_db") or {}
        ).get("median"),
        "synthetic_echo_snr_db_median": (
            synthetic_metrics.get("echo_snr_db") or {}
        ).get("median"),
        "hard_test_opened": bool(report.get("hard_test_opened")),
    }


def final_decision_markdown(report: dict[str, Any]) -> str:
    ceiling = report["evidence_ceiling"]
    lines = [
        "# Reference-Conditioned Target-Me Separation v1 Decision",
        "",
        f"Decision: **{report['decision']}**",
        f"Fingerprint: `{report['fingerprint']}`",
        "",
        "## Result",
        "",
        "The oracle and bounded overfit probes passed, but the smallest train/dev candidate did not",
        "pass the locked dev gates in either deterministic attempt. The hard split and sealed",
        "twelve-session corpus remained unopened, so production stays byte-exact Speaker-Preserving",
        "Neural Echo v2.",
        "",
        "## Measured Ceiling",
        "",
        f"- Best Target-Me SNR: `{ceiling['best_synthetic_target_snr_db_median']:.3f} dB` "
        f"against `{ceiling['required_synthetic_target_snr_db_median']:.3f} dB`.",
        f"- Best echo SNR: `{ceiling['best_synthetic_echo_snr_db_median']:.3f} dB` "
        f"against `{ceiling['required_synthetic_echo_snr_db_median']:.3f} dB`.",
        f"- Best Target-Me improvement: `{ceiling['best_synthetic_target_improvement_db_median']:.3f} dB`.",
        f"- Independent supervised non-target local speech rows: "
        f"`{ceiling['independent_non_target_local_speech_rows']}`.",
        f"- Distinct Target-Me enrollment vectors used for training: "
        f"`{ceiling['distinct_target_enrollment_vectors']}`.",
        "",
        "The last two facts make the `other_local speech` assignment unidentifiable: exact remix",
        "would still pass if another nearby speaker were assigned to Target-Me. This is a data and",
        "conditioning ceiling, not a threshold to relax.",
        "",
        "## Safety Boundary",
        "",
        "- Candidate hard-test access was not authorized.",
        "- Candidate audio was not materialized into any production session.",
        "- Capture, raw CAF, Echo Guard, whisper.cpp and transcript profiles are unchanged.",
        "- Missing evidence or a stale fingerprint continues to fall back to production v2.",
        "",
    ]
    return "\n".join(lines)


def run_final_decision(
    *,
    repo_root: Path,
    policy_path: Path,
    output_dir: Path,
    preflight_path: Path,
    oracle_path: Path,
    overfit_path: Path,
    train_dev_path: Path,
) -> dict[str, Any]:
    resource_report = apply_resource_policy(resolve_resource_policy("background"))
    policy = read_json(policy_path)
    preflight = read_json(preflight_path)
    oracle = read_json(oracle_path)
    overfit = read_json(overfit_path)
    train_dev = read_json(train_dev_path)
    candidate_lock_path = train_dev_path.parent / "candidate_lock.json"
    candidate_lock = read_json(candidate_lock_path)

    expected_contract = policy_scope_fingerprint(
        policy,
        (
            *PREFLIGHT_POLICY_SECTIONS,
            "oracle_gates",
            "overfit_probe",
            "train_dev_candidate",
        ),
    )
    if preflight.get("decision") != READY:
        raise RuntimeError("final decision requires passing frozen preflight evidence")
    if oracle.get("decision") != "ORACLE_CEILING_PASSED":
        raise RuntimeError("final decision requires the completed oracle ceiling")
    if overfit.get("decision") != "OVERFIT_FEASIBILITY_PASSED":
        raise RuntimeError("final decision requires passing overfit feasibility")
    if train_dev.get("contract_fingerprint") != expected_contract:
        raise RuntimeError("train/dev decision contract differs from current policy")
    if train_dev.get("decision") != "DEV_CANDIDATE_REJECTED":
        raise RuntimeError("a locked candidate requires sealed hard/corpus evaluation before decision")
    if candidate_lock.get("candidate_fingerprint") != train_dev.get("fingerprint"):
        raise RuntimeError("candidate lock does not match the train/dev report")
    if candidate_lock.get("hard_test_access_authorized") is not False:
        raise RuntimeError("rejected candidate unexpectedly authorizes hard-test access")
    if bool(train_dev.get("hard_test_opened")):
        raise RuntimeError("rejected candidate report opened the hard split")
    checkpoint_path = train_dev_path.parent / "separator.pt"
    if sha256(checkpoint_path) != (train_dev.get("model") or {}).get("sha256"):
        raise RuntimeError("rejected candidate checkpoint is missing or changed")

    baseline = policy["production_baseline"]
    production_policy_path = repo_root / baseline["policy"]
    if sha256(production_policy_path) != baseline["policy_sha256"]:
        raise RuntimeError("production v2 policy changed during the isolated experiment")
    sealed = policy["sealed_evaluation"]
    sealed_sources = {
        name: {
            "path": sealed[name],
            "expected_sha256": sealed[f"{name}_sha256"],
            "observed_sha256": sha256(repo_root / sealed[name]),
        }
        for name in ("corpus_report", "promotion_decision", "evaluation_manifest")
    }
    if any(row["observed_sha256"] != row["expected_sha256"] for row in sealed_sources.values()):
        raise RuntimeError("sealed production evaluation changed during train/dev selection")

    attempt_dirs = sorted(
        path
        for path in output_dir.glob("train-dev-attempt-*")
        if path.is_dir() and (path / "train_dev_report.json").is_file()
    )
    attempts = [summarize_train_dev_attempt(path, repo_root) for path in attempt_dirs]
    current_attempt = summarize_train_dev_attempt(train_dev_path.parent, repo_root)
    if not attempts or attempts[-1]["fingerprint"] != current_attempt["fingerprint"]:
        attempts.append(current_attempt)
    if any(row["hard_test_opened"] for row in attempts):
        raise RuntimeError("a train/dev attempt unexpectedly opened hard-test data")

    train_waveforms, train_kinds, train_index = load_cache_split(policy, repo_root, "train")
    del train_waveforms
    id_to_kind = {value: name for name, value in V2_CORE.KIND_IDS.items()}
    train_kind_counts = Counter(id_to_kind[int(value)] for value in train_kinds)
    exact_other_kinds = set(policy["train_dev_candidate"]["exact_other_kinds"])
    non_speech_other_kinds = {"keyboard_noise", "silence_background"}
    independent_non_target_local_speech_rows = sum(
        int(count)
        for kind, count in train_kind_counts.items()
        if kind in exact_other_kinds and kind not in non_speech_other_kinds
    )
    gates = policy["train_dev_candidate"]["selection_gates"]
    best_target = max(float(row["synthetic_target_snr_db_median"]) for row in attempts)
    best_improvement = max(
        float(row["synthetic_target_improvement_db_median"]) for row in attempts
    )
    best_echo = max(float(row["synthetic_echo_snr_db_median"]) for row in attempts)
    evidence_ceiling = {
        "attempt_count": len(attempts),
        "best_synthetic_target_snr_db_median": round(best_target, 6),
        "required_synthetic_target_snr_db_median": float(
            gates["synthetic_target_snr_db_median_min"]
        ),
        "best_synthetic_target_improvement_db_median": round(best_improvement, 6),
        "best_synthetic_echo_snr_db_median": round(best_echo, 6),
        "required_synthetic_echo_snr_db_median": float(
            gates["synthetic_echo_snr_db_median_min"]
        ),
        "independent_non_target_local_speech_rows": independent_non_target_local_speech_rows,
        "distinct_target_enrollment_vectors": 1,
        "target_conditioning_ablation_available": False,
        "hard_test_rows_opened": 0,
        "sealed_sessions_evaluated": 0,
        "limiting_factors": [
            "locked_dev_target_and_echo_gates_not_met",
            "no_independent_supervised_non_target_local_speech",
            "single_fixed_target_enrollment_cannot_prove_identity_conditioning",
        ],
    }
    decision = FINAL_DO_NOT_PROMOTE
    data_card = {
        "schema": "murmurmark.reference_conditioned_target_me_data_card/v1",
        "profile": policy["profile"],
        "controlled_corpus_fingerprint": policy["controlled_supervision"]["corpus_fingerprint"],
        "train_cache_fingerprint": policy["training_cache"]["train_fingerprint"],
        "dev_cache_fingerprint": policy["training_cache"]["dev_fingerprint"],
        "train_rows": len(train_index),
        "train_kind_counts": dict(sorted(train_kind_counts.items())),
        "selection_rows": int((train_dev.get("dev") or {}).get("rows") or 0),
        "hard_test_opened": False,
        "sealed_session_count": int(sealed["session_count"]),
        "ordinary_meeting_training": False,
        "independent_non_target_local_speech_rows": independent_non_target_local_speech_rows,
        "known_limit": "other_local_speech attribution is not supervised by the frozen train split",
    }
    data_card["fingerprint"] = digest_json(data_card)
    model_card = {
        "schema": "murmurmark.reference_conditioned_target_me_model_card/v1",
        "profile": policy["profile"],
        "candidate_id": policy["train_dev_candidate"]["candidate_id"],
        "local_only": True,
        "promotion_eligible": False,
        "target_encoder": policy["models"]["target_me_encoder"]["model_id"],
        "enrollment_vector_count": 1,
        "attempts": attempts,
        "best_checkpoint_sha256": current_attempt["checkpoint_sha256"],
        "semantic_limit": (
            "The bounded separator saw one fixed Target-Me enrollment and no independently "
            "supervised non-target local speech; identity-conditioned three-way attribution "
            "is therefore unproven."
        ),
    }
    model_card["fingerprint"] = digest_json(model_card)
    corpus_report = {
        "schema": "murmurmark.reference_conditioned_target_me_corpus_report/v1",
        "profile": policy["profile"],
        "status": "not_opened_candidate_rejected_on_dev",
        "sealed_session_count": int(sealed["session_count"]),
        "evaluated_session_count": 0,
        "candidate_selected_session_count": 0,
        "fallback_session_count": int(sealed["session_count"]),
        "fallback_profile": baseline["profile"],
        "production_unchanged": True,
        "reason": "dev candidate lock denied hard-test and corpus access",
        "sealed_sources": sealed_sources,
    }
    corpus_report["fingerprint"] = digest_json(corpus_report)
    experiment_manifest = {
        "schema": "murmurmark.reference_conditioned_target_me_experiment_manifest/v1",
        "profile": policy["profile"],
        "policy_path": display_path(policy_path, repo_root),
        "policy_sha256": sha256(policy_path),
        "preflight_fingerprint": preflight["fingerprint"],
        "oracle_fingerprint": oracle["fingerprint"],
        "overfit_fingerprint": overfit["fingerprint"],
        "attempt_fingerprints": [row["fingerprint"] for row in attempts],
        "data_card_fingerprint": data_card["fingerprint"],
        "model_card_fingerprint": model_card["fingerprint"],
        "corpus_report_fingerprint": corpus_report["fingerprint"],
    }
    experiment_manifest["fingerprint"] = digest_json(experiment_manifest)
    checks = [
        check("preflight", preflight.get("decision"), READY),
        check("oracle", oracle.get("decision"), "ORACLE_CEILING_PASSED"),
        check("overfit", overfit.get("decision"), "OVERFIT_FEASIBILITY_PASSED"),
        check("dev_candidate", train_dev.get("decision"), "DEV_CANDIDATE_REJECTED"),
        check("hard_test_opened", bool(train_dev.get("hard_test_opened")), False),
        check("hard_test_access", candidate_lock.get("hard_test_access_authorized"), False),
        check("sealed_sources_unchanged", True, True),
        check("production_policy_unchanged", True, True),
    ]
    deterministic_basis = {
        "decision": decision,
        "experiment_manifest_fingerprint": experiment_manifest["fingerprint"],
        "evidence_ceiling": evidence_ceiling,
        "checks": checks,
        "production_fallback": baseline["fallback"],
    }
    report = {
        "schema": "murmurmark.reference_conditioned_target_me_decision/v1",
        "profile": policy["profile"],
        "decision": decision,
        "fingerprint": digest_json(deterministic_basis),
        "promotion_allowed": False,
        "production_unchanged": True,
        "production_fallback": baseline["fallback"],
        "hard_test_opened": False,
        "sealed_corpus_opened": False,
        "post_asr_cleanup_credit": 0,
        "resource_policy": resource_report,
        "experiment_manifest_fingerprint": experiment_manifest["fingerprint"],
        "data_card_fingerprint": data_card["fingerprint"],
        "model_card_fingerprint": model_card["fingerprint"],
        "corpus_report_fingerprint": corpus_report["fingerprint"],
        "attempts": attempts,
        "evidence_ceiling": evidence_ceiling,
        "checks": checks,
        "next_goal": "Controlled Non-Target Local Speech Supervision v1",
    }
    write_json(output_dir / "data_card.json", data_card)
    write_json(output_dir / "model_card.json", model_card)
    write_json(output_dir / "corpus_report.json", corpus_report)
    write_json(output_dir / "experiment_manifest.json", experiment_manifest)
    write_json(output_dir / "decision.json", report)
    (output_dir / "decision.md").write_text(final_decision_markdown(report), encoding="utf-8")
    (output_dir / "corpus_report.md").write_text(
        "# Reference-Conditioned Target-Me Separation v1 Corpus\n\n"
        "The sealed twelve-session corpus was not opened because dev selection rejected the "
        "candidate. All 12 sessions remain on byte-exact Speaker-Preserving Neural Echo v2.\n",
        encoding="utf-8",
    )
    return report


def run_preflight(
    *,
    repo_root: Path,
    policy_path: Path,
    output_dir: Path,
    verify_audio: str,
    sample_artifacts: int,
) -> dict[str, Any]:
    resource_report = apply_resource_policy(resolve_resource_policy("background"))
    policy = read_json(policy_path)
    if policy.get("schema") != SCHEMA:
        raise ValueError(f"unexpected policy schema: {policy_path}")

    pinned_report, pinned_frozen = verify_pinned_sources(policy, repo_root)
    models_report, models_frozen = verify_models(policy, repo_root)

    controlled = policy["controlled_supervision"]
    sealed = policy["sealed_evaluation"]
    baseline = policy["production_baseline"]
    production_policy = read_json(repo_root / baseline["policy"])
    corpus_decision = read_json(repo_root / controlled["corpus_decision"])
    replay_report = read_json(repo_root / controlled["replay_report"])
    sealed_report = read_json(repo_root / sealed["corpus_report"])
    sealed_decision = read_json(repo_root / sealed["promotion_decision"])
    sealed_manifest = read_json(repo_root / sealed["evaluation_manifest"])
    supervision_path = repo_root / controlled["supervision_manifest"]
    rows, errors = read_supervision_manifest(supervision_path)
    manifest_summary = summarize_manifest(rows, errors)
    artifact_descriptors = manifest_summary.pop("artifacts")
    audio_report, controlled_frozen = verify_controlled_artifacts(
        corpus_root=supervision_path.parent,
        artifacts=artifact_descriptors,
        mode=verify_audio,
        sample_count=sample_artifacts,
        repo_root=repo_root,
    )

    gates = policy["preflight_gates"]
    modules = module_inventory(
        list(gates.get("required_core_modules") or []),
        list(gates.get("optional_toolkits") or []),
    )
    enrollment_report = verify_reference_enrollment(policy, repo_root)
    training_cache_report = verify_training_cache(policy, repo_root)
    checks = semantic_checks(
        policy=policy,
        production_policy=production_policy,
        corpus_decision=corpus_decision,
        replay_report=replay_report,
        sealed_report=sealed_report,
        sealed_decision=sealed_decision,
        sealed_manifest=sealed_manifest,
        manifest_summary=manifest_summary,
    )
    checks.extend(
        [
            check("pinned_source_hashes", all(row["passed"] for row in pinned_report.values()), True),
            check("required_modules", modules["passed"], True),
            check("pinned_models", all(row["passed"] for row in models_report.values()), True),
            check("reference_enrollment", enrollment_report["passed"], True),
            check("training_cache", training_cache_report["passed"], True),
            check("controlled_audio_verification", audio_report["passed"], True),
        ]
    )

    frozen_inputs = {
        "schema": FROZEN_INPUTS_SCHEMA,
        "profile": policy["profile"],
        "policy": {
            "path": display_path(policy_path, repo_root),
            "sha256": sha256(policy_path),
        },
        "inputs": sorted(
            pinned_frozen + models_frozen + controlled_frozen,
            key=lambda row: (str(row.get("kind")), str(row.get("path"))),
        ),
    }
    frozen_inputs["fingerprint"] = digest_json(
        {
            "policy": frozen_inputs["policy"],
            "inputs": frozen_inputs["inputs"],
        }
    )
    write_json(output_dir / "frozen_inputs.json", frozen_inputs)

    decision = READY if all(row["passed"] for row in checks) else BLOCKED
    deterministic_basis = {
        "policy_sha256": frozen_inputs["policy"]["sha256"],
        "frozen_inputs_fingerprint": frozen_inputs["fingerprint"],
        "checks": checks,
        "corpus": manifest_summary,
        "audio_verification": audio_report,
        "modules": modules,
        "models": models_report,
        "decision": decision,
    }
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "profile": policy["profile"],
        "decision": decision,
        "fingerprint": digest_json(deterministic_basis),
        "contract_fingerprint": policy_scope_fingerprint(policy, PREFLIGHT_POLICY_SECTIONS),
        "training_contract_fingerprint": policy_scope_fingerprint(
            policy, TRAINING_POLICY_SECTIONS
        ),
        "policy": frozen_inputs["policy"],
        "production_fallback": baseline["fallback"],
        "resource_policy": resource_report,
        "source_verification": pinned_report,
        "models": models_report,
        "reference_enrollment": enrollment_report,
        "training_cache": training_cache_report,
        "modules": modules,
        "corpus": manifest_summary,
        "audio_verification": audio_report,
        "checks": checks,
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "summary": {
            "frozen_source_count": len(frozen_inputs["inputs"]),
            "sealed_session_count": len((sealed_manifest.get("basis") or {}).get("sessions") or []),
            "models_passed": all(row["passed"] for row in models_report.values()),
            "next_stage": "oracle_ideal_ratio_and_complex_mask" if decision == READY else "repair_preflight",
        },
    }
    write_json(output_dir / "preflight_report.json", report)
    (output_dir / "preflight_report.md").write_text(build_markdown(report), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reference-Conditioned Target-Me Separation v1 research controller"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight", help="freeze and verify experiment inputs")
    preflight.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "policies/reference-conditioned-target-me-separation-v1.json",
    )
    preflight.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "sessions/_reports/reference-conditioned-target-me-separation-v1",
    )
    preflight.add_argument(
        "--verify-audio",
        choices=("none", "sample", "all"),
        default="sample",
        help="verify no, sampled, or all controlled audio hashes",
    )
    preflight.add_argument("--sample-artifacts", type=int, default=32)
    oracle = subparsers.add_parser(
        "oracle-ceiling",
        help="measure ideal-mask representation ceiling on frozen train/dev pairs",
    )
    oracle.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "policies/reference-conditioned-target-me-separation-v1.json",
    )
    oracle.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "sessions/_reports/reference-conditioned-target-me-separation-v1",
    )
    oracle.add_argument(
        "--preflight",
        type=Path,
        default=ROOT
        / "sessions/_reports/reference-conditioned-target-me-separation-v1/preflight_report.json",
    )
    overfit = subparsers.add_parser(
        "overfit-probe",
        help="prove the minimal conditioned separator can overfit a frozen train batch",
    )
    overfit.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "policies/reference-conditioned-target-me-separation-v1.json",
    )
    overfit.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "sessions/_reports/reference-conditioned-target-me-separation-v1",
    )
    overfit.add_argument(
        "--preflight",
        type=Path,
        default=ROOT
        / "sessions/_reports/reference-conditioned-target-me-separation-v1/preflight_report.json",
    )
    overfit.add_argument(
        "--oracle",
        type=Path,
        default=ROOT
        / "sessions/_reports/reference-conditioned-target-me-separation-v1/oracle_ceiling_report.json",
    )
    train_dev = subparsers.add_parser(
        "train-dev",
        help="fit the minimal candidate on train and lock it only through dev gates",
    )
    train_dev.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "policies/reference-conditioned-target-me-separation-v1.json",
    )
    train_dev.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "sessions/_reports/reference-conditioned-target-me-separation-v1",
    )
    train_dev.add_argument(
        "--preflight",
        type=Path,
        default=ROOT
        / "sessions/_reports/reference-conditioned-target-me-separation-v1/preflight_report.json",
    )
    train_dev.add_argument(
        "--oracle",
        type=Path,
        default=ROOT
        / "sessions/_reports/reference-conditioned-target-me-separation-v1/oracle_ceiling_report.json",
    )
    train_dev.add_argument(
        "--overfit",
        type=Path,
        default=ROOT
        / "sessions/_reports/reference-conditioned-target-me-separation-v1/overfit/overfit_report.json",
    )
    decide = subparsers.add_parser(
        "decide",
        help="freeze the final decision after dev rejection or completed sealed evaluation",
    )
    decide.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "policies/reference-conditioned-target-me-separation-v1.json",
    )
    decide.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "sessions/_reports/reference-conditioned-target-me-separation-v1",
    )
    decide.add_argument(
        "--preflight",
        type=Path,
        default=ROOT
        / "sessions/_reports/reference-conditioned-target-me-separation-v1/preflight_report.json",
    )
    decide.add_argument(
        "--oracle",
        type=Path,
        default=ROOT
        / "sessions/_reports/reference-conditioned-target-me-separation-v1/oracle_ceiling_report.json",
    )
    decide.add_argument(
        "--overfit",
        type=Path,
        default=ROOT
        / "sessions/_reports/reference-conditioned-target-me-separation-v1/overfit/overfit_report.json",
    )
    decide.add_argument(
        "--train-dev",
        type=Path,
        default=ROOT
        / "sessions/_reports/reference-conditioned-target-me-separation-v1/train-dev/train_dev_report.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "preflight":
        report = run_preflight(
            repo_root=ROOT,
            policy_path=args.policy.resolve(),
            output_dir=args.output_dir.resolve(),
            verify_audio=args.verify_audio,
            sample_artifacts=args.sample_artifacts,
        )
        passed = report["decision"] == READY
    elif args.command == "oracle-ceiling":
        report = run_oracle_ceiling(
            repo_root=ROOT,
            policy_path=args.policy.resolve(),
            output_dir=args.output_dir.resolve(),
            preflight_path=args.preflight.resolve(),
        )
        passed = report["decision"] == "ORACLE_CEILING_PASSED"
    elif args.command == "overfit-probe":
        report = run_overfit_probe(
            repo_root=ROOT,
            policy_path=args.policy.resolve(),
            output_dir=args.output_dir.resolve(),
            preflight_path=args.preflight.resolve(),
            oracle_path=args.oracle.resolve(),
        )
        passed = report["decision"] == "OVERFIT_FEASIBILITY_PASSED"
    elif args.command == "train-dev":
        report = run_train_dev_candidate(
            repo_root=ROOT,
            policy_path=args.policy.resolve(),
            output_dir=args.output_dir.resolve(),
            preflight_path=args.preflight.resolve(),
            oracle_path=args.oracle.resolve(),
            overfit_path=args.overfit.resolve(),
        )
        passed = report["decision"] == "DEV_CANDIDATE_LOCKED"
    elif args.command == "decide":
        report = run_final_decision(
            repo_root=ROOT,
            policy_path=args.policy.resolve(),
            output_dir=args.output_dir.resolve(),
            preflight_path=args.preflight.resolve(),
            oracle_path=args.oracle.resolve(),
            overfit_path=args.overfit.resolve(),
            train_dev_path=args.train_dev.resolve(),
        )
        passed = report["decision"] in {FINAL_PROMOTE, FINAL_DO_NOT_PROMOTE}
    else:
        raise RuntimeError(f"unsupported command: {args.command}")
    print(f"decision: {report['decision']}")
    print(f"fingerprint: {report['fingerprint']}")
    report_names = {
        "preflight": "preflight_report.json",
        "oracle-ceiling": "oracle_ceiling_report.json",
        "overfit-probe": "overfit/overfit_report.json",
        "train-dev": "train-dev/train_dev_report.json",
        "decide": "decision.json",
    }
    report_name = report_names[args.command]
    print(f"report: {display_path(args.output_dir / report_name, ROOT)}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
