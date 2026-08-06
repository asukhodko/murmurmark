#!/usr/bin/env python3
"""Freeze and verify one stronger offline speech-separator prerequisite path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from murmurmark_resource_policy import apply_resource_policy, resolve_resource_policy  # noqa: E402


SCHEMA = "murmurmark.stronger_offline_target_speaker_separator_prerequisites_policy/v1"
PROFILE = "stronger_offline_target_speaker_separator_prerequisites_v1"
READY = "READY_FOR_STRONGER_SEPARATOR_QUALIFICATION"
RESOURCE_LIMIT = "CURRENT_RESOURCE_LIMIT_REACHED"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def with_fingerprint(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["fingerprint"] = digest_json(result)
    return result


def fingerprint_valid(value: dict[str, Any]) -> bool:
    expected = value.get("fingerprint")
    body = {key: item for key, item in value.items() if key != "fingerprint"}
    return isinstance(expected, str) and expected == digest_json(body)


def checked(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    return {"name": name, "observed": observed, "expected": expected, "passed": observed == expected}


def threshold(
    name: str,
    observed: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> dict[str, Any]:
    if (minimum is None) == (maximum is None):
        raise ValueError("exactly one threshold direction is required")
    passed = observed >= minimum if minimum is not None else observed <= maximum
    row: dict[str, Any] = {"name": name, "observed": round(float(observed), 6), "passed": bool(passed)}
    row["minimum" if minimum is not None else "maximum"] = minimum if minimum is not None else maximum
    return row


def display_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        pass
    try:
        return "~/" + str(resolved.relative_to(Path.home().resolve()))
    except ValueError:
        return str(resolved)


def resolve_path(value: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    return path if path.is_absolute() else ROOT / path


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != SCHEMA or policy.get("profile") != PROFILE:
        raise RuntimeError("unexpected stronger separator prerequisite policy")
    return policy


def verify_file(path: Path, expected: str) -> dict[str, Any]:
    error: str | None = None
    try:
        observed = sha256(path) if path.is_file() else None
    except OSError as caught:
        observed = None
        error = str(caught)
    row = {
        "path": display_path(path),
        "expected_sha256": expected,
        "observed_sha256": observed,
        "passed": observed == expected,
    }
    if error is not None:
        row["error"] = error
    return row


def verify_source_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
    path = resolve_path(str(descriptor["path"]))
    row = verify_file(path, str(descriptor["sha256"]))
    required = descriptor.get("required_decision")
    if required is not None:
        try:
            observed = read_json(path).get("decision") if path.is_file() else None
        except (OSError, ValueError) as error:
            observed = None
            row["error"] = str(error)
        row.update({"required_decision": required, "observed_decision": observed})
        row["passed"] = row["passed"] and observed == required
    return row


def verify_selected_backbone(policy: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    selected = policy["selected_backbone"]
    model_dir = resolve_path(str(selected["model_dir"]))
    wheel_dir = resolve_path(str(selected["runtime_wheels_dir"]))
    checks: list[dict[str, Any]] = []
    model_bytes = 0
    for name, expected in sorted(selected["model_files"].items()):
        path = model_dir / name
        if path.is_file():
            model_bytes += path.stat().st_size
        checks.append(verify_file(path, str(expected)))
    for name, expected in sorted(selected["runtime_wheels"].items()):
        checks.append(verify_file(wheel_dir / name, str(expected)))
    runtime_dir = resolve_path(str(selected["runtime_dir"]))
    checks.append(checked("runtime_dir_present", runtime_dir.is_dir(), True))
    checks.append(
        threshold(
            "model_bytes",
            model_bytes,
            maximum=float(policy["resource_budget"]["model_bytes_max"]),
        )
    )
    return checks, model_bytes


def split_speakers(policy: dict[str, Any]) -> dict[str, set[str]]:
    return {
        split: set(details["speakers"])
        for split, details in policy["supervision_expansion"]["splits"].items()
    }


def split_overlap(speakers: dict[str, set[str]]) -> dict[str, list[str]]:
    names = sorted(speakers)
    return {
        f"{left}:{right}": sorted(speakers[left] & speakers[right])
        for index, left in enumerate(names)
        for right in names[index + 1 :]
    }


def available_public_speakers() -> dict[str, set[str]]:
    base = ROOT / "sessions/_reports/target-me-identifiability-corpus-v1/sources/openslr31/extracted/LibriSpeech"
    result: dict[str, set[str]] = {}
    for subset in ("train-clean-5", "dev-clean-2"):
        directory = base / subset
        result[subset] = {
            f"slr31_{entry.name}"
            for entry in directory.iterdir()
            if entry.is_dir() and entry.name.isdigit()
        } if directory.is_dir() else set()
    return result


def metric_median(group: dict[str, Any], name: str) -> float:
    return float((group.get(name) or {}).get("median", 0.0))


def least_squares_adapter(stems: Any, mixture: Any) -> dict[str, Any]:
    import numpy as np

    stem_values = np.asarray(stems, dtype=np.float64)
    mixture_values = np.asarray(mixture, dtype=np.float64)
    if stem_values.ndim != 2 or stem_values.shape[0] != 2:
        raise ValueError("expected two separator stems")
    if stem_values.shape[1] != mixture_values.size:
        raise ValueError("separator stem length does not match mixture")
    coefficients, *_ = np.linalg.lstsq(stem_values.T, mixture_values, rcond=None)
    scaled = coefficients[:, None] * stem_values
    target_me = scaled[0]
    other_local = scaled[1]
    unexplained = mixture_values - target_me - other_local
    reconstruction = target_me + other_local + unexplained
    return {
        "coefficients": coefficients,
        "target_me": target_me,
        "other_local": other_local,
        "unexplained_residual": unexplained,
        "reconstruction": reconstruction,
    }


def build_gap_map(policy: dict[str, Any]) -> dict[str, Any]:
    report = read_json(resolve_path(policy["sources"]["multi_component_train_dev"]["path"]))
    aggregate = report["dev"]["aggregate"]
    metrics = aggregate["metrics"]
    roles = aggregate["roles"]
    families = aggregate["families"]
    rows = [
        ("target_me_snr_db_median", metric_median(roles["target_me"], "target_snr_db"), 8.0, "quiet Target-Me and overlapping local speech"),
        ("other_local_snr_db_median", metric_median(metrics, "other_local_snr_db"), 8.0, "nearby speaker isolation"),
        ("paired_query_margin_db_median", metric_median(metrics, "paired_query_margin_db"), 4.0, "speaker-query identity margin"),
        ("absent_query_attenuation_db_median", metric_median(metrics, "absent_query_attenuation_db"), 12.0, "target-absent rejection"),
        ("unexplained_residual_snr_db_median", metric_median(metrics, "unexplained_residual_snr_db"), 6.0, "keyboard and office residual accounting"),
    ]
    gaps = [
        {
            "metric": name,
            "observed": round(observed, 6),
            "required": required,
            "gap": round(required - observed, 6),
            "coverage_need": need,
            "passed": observed >= required,
        }
        for name, observed, required, need in rows
    ]
    family_rows = []
    for name in (
        "quiet_target_me",
        "quiet_other_local",
        "ordinary_double_talk",
        "opening_backchannel",
        "keyboard_background",
        "other_speaker_only",
    ):
        observed = metric_median(families[name], "target_snr_db")
        family_rows.append(
            {
                "family": name,
                "target_snr_db_median": round(observed, 6),
                "required": 8.0,
                "gap": round(8.0 - observed, 6),
            }
        )
    return with_fingerprint(
        {
            "schema": "murmurmark.stronger_separator_gap_map/v1",
            "profile": PROFILE,
            "status": "READY",
            "source_report": display_path(resolve_path(policy["sources"]["multi_component_train_dev"]["path"])),
            "source_report_sha256": policy["sources"]["multi_component_train_dev"]["sha256"],
            "failed_metrics": gaps,
            "family_gaps": family_rows,
            "diagnosis": [
                "the four-stem accounting contract is stable but the small FiLM-GRU lacks separation capacity",
                "target-absent and nearby-speaker cases need more split-disjoint negative identities",
                "quiet speech, openings, keyboard and office noise must remain explicit families",
                "ordinary meeting audio remains evaluation evidence and is not converted into hidden training labels",
            ],
        }
    )


def build_supervision_plan(policy: dict[str, Any]) -> dict[str, Any]:
    speakers = split_speakers(policy)
    overlaps = split_overlap(speakers)
    available = available_public_speakers()
    availability: dict[str, list[str]] = {}
    for split, details in policy["supervision_expansion"]["splits"].items():
        subset = details["subset"]
        availability[split] = sorted(speakers[split] - available.get(subset, set()))
    checks = [
        checked("split_disjoint_non_target_speakers", overlaps, {key: [] for key in overlaps}),
        checked("selected_public_speakers_available", availability, {key: [] for key in availability}),
        checked("ordinary_meeting_labels", policy["supervision_expansion"]["ordinary_meeting_labels"], False),
        checked("hard_access_in_prerequisite", policy["supervision_expansion"]["splits"]["future_hard"]["access_in_this_stage"], False),
    ]
    return with_fingerprint(
        {
            "schema": "murmurmark.stronger_separator_supervision_expansion/v1",
            "profile": PROFILE,
            "status": "READY_MANIFEST_ONLY" if all(row["passed"] for row in checks) else "BLOCKED",
            "target_identity": policy["supervision_expansion"]["target_identity"],
            "target_sources": policy["supervision_expansion"]["target_sources"],
            "splits": policy["supervision_expansion"]["splits"],
            "required_families": policy["supervision_expansion"]["required_families"],
            "speaker_overlap": overlaps,
            "missing_public_speakers": availability,
            "checks": checks,
        }
    )


def run_freeze(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    source_checks = [verify_source_descriptor(item) for item in policy["sources"].values()]
    corpus_root = resolve_path(policy["target_corpus"]["root"])
    corpus_checks = [
        verify_file(corpus_root / name, expected)
        for name, expected in sorted(policy["target_corpus"]["files"].items())
    ]
    backbone_checks, model_bytes = verify_selected_backbone(policy)
    supervision = build_supervision_plan(policy)
    try:
        gap_map = build_gap_map(policy)
    except (OSError, ValueError, KeyError, TypeError) as error:
        gap_map = with_fingerprint(
            {
                "schema": "murmurmark.stronger_separator_gap_map/v1",
                "profile": PROFILE,
                "status": "BLOCKED",
                "source_report": display_path(
                    resolve_path(policy["sources"]["multi_component_train_dev"]["path"])
                ),
                "source_report_sha256": policy["sources"]["multi_component_train_dev"]["sha256"],
                "error": str(error),
                "failed_metrics": [],
                "family_gaps": [],
                "diagnosis": ["the frozen train/dev report is unavailable or incompatible"],
            }
        )
    checks = source_checks + corpus_checks + backbone_checks + [
        checked("gap_map", gap_map["status"], "READY"),
        checked("supervision_expansion", supervision["status"], "READY_MANIFEST_ONLY"),
        checked("training_performed", policy["training_performed"], False),
        checked("hard_or_sealed_access", policy["hard_or_sealed_access"], False),
        checked("post_asr_cleanup_credit", policy["post_asr_cleanup_promotion_credit"], 0),
    ]
    frozen = with_fingerprint(
        {
            "schema": "murmurmark.stronger_separator_frozen_inputs/v1",
            "profile": PROFILE,
            "policy_path": display_path(policy_path),
            "policy_sha256": sha256(policy_path),
            "target_corpus_fingerprint": policy["target_corpus"]["fingerprint"],
            "selected_backbone": policy["selected_backbone"]["id"],
            "selected_model_bytes": model_bytes,
            "passed": all(row["passed"] for row in checks),
            "checks": checks,
        }
    )
    shortlist = with_fingerprint(
        {
            "schema": "murmurmark.stronger_separator_backbone_shortlist/v1",
            "profile": PROFILE,
            "candidates": policy["backbone_shortlist"],
            "selected_count": sum(item["status"] == "selected" for item in policy["backbone_shortlist"]),
        }
    )
    selected_candidate = next(
        item for item in policy["backbone_shortlist"] if item["status"] == "selected"
    )
    license_evidence = with_fingerprint(
        {
            "schema": "murmurmark.stronger_separator_license_evidence/v1",
            "profile": PROFILE,
            "selected_backbone": policy["selected_backbone"]["id"],
            "license": "Apache-2.0",
            "source_revision": selected_candidate["source_revision"],
            "model_revision": selected_candidate["model_revision"],
            "primary_sources": [
                "https://github.com/speechbrain/speechbrain/blob/36c180c7bfad3bf5c48bd76a24799812952c4565/LICENSE",
                "https://huggingface.co/speechbrain/sepformer-libri2mix/tree/eb43c5bfbb2aa654630adbf849373bcec0a20ed4",
            ],
            "redistribution_in_murmurmark": False,
            "local_weights_only": True,
        }
    )
    adapter_plan = with_fingerprint(
        {
            "schema": "murmurmark.stronger_separator_four_stem_adapter_plan/v1",
            "profile": PROFILE,
            **policy["four_stem_adapter"],
            "known_limits": [
                "the selected backbone separates two anonymous speech sources and needs external speaker assignment",
                "the backbone runs at 8 kHz, so preservation of short Russian consonants remains a qualification gate",
                "raw separator outputs are scale-indeterminate and require frozen least-squares scaling",
                "more than two simultaneous local speakers remains unsupported and must fall back",
            ],
        }
    )
    for name, value in (
        ("frozen_inputs.json", frozen),
        ("gap_map.json", gap_map),
        ("supervision_expansion.json", supervision),
        ("backbone_shortlist.json", shortlist),
        ("license_evidence.json", license_evidence),
        ("four_stem_adapter_plan.json", adapter_plan),
    ):
        write_json(output_dir / name, value)
    return frozen


def block_network() -> list[str]:
    attempts: list[str] = []
    original_socket = socket.socket

    class OfflineSocket(original_socket):
        def connect(self, address: Any) -> Any:
            attempts.append(str(address))
            raise OSError("network disabled by MurmurMark prerequisite preflight")

        def connect_ex(self, address: Any) -> int:
            attempts.append(str(address))
            return 101

    def blocked_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(str(address))
        raise OSError("network disabled by MurmurMark prerequisite preflight")

    socket.socket = OfflineSocket
    socket.create_connection = blocked_create_connection
    return attempts


def child_probe(policy_path: Path) -> dict[str, Any]:
    import resource

    import numpy as np

    policy = load_policy(policy_path)
    selected = policy["selected_backbone"]
    runtime_dir = resolve_path(selected["runtime_dir"])
    model_dir = resolve_path(selected["model_dir"])
    sys.path.insert(0, str(runtime_dir))
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    resource_report = apply_resource_policy(resolve_resource_policy("background", 4))
    attempts = block_network()

    import torch
    import torchaudio
    from speechbrain import __version__ as speechbrain_version
    from speechbrain.inference.separation import SepformerSeparation

    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    sample_rate = int(policy["resource_budget"]["probe_sample_rate"])
    samples = int(policy["resource_budget"]["probe_samples"])
    seconds = np.arange(samples, dtype=np.float32) / float(sample_rate)
    mixture = (
        0.03 * np.sin(2.0 * np.pi * 233.0 * seconds)
        + 0.02 * np.sin(2.0 * np.pi * 487.0 * seconds)
    ).astype(np.float32)

    started = time.monotonic()
    model = SepformerSeparation.from_hparams(
        source=str(model_dir), savedir=str(model_dir), run_opts={"device": "cpu"}
    )
    load_sec = time.monotonic() - started
    tensor = torch.from_numpy(mixture)[None]
    started = time.monotonic()
    with torch.inference_mode():
        output = model.separate_batch(tensor)
    inference_sec = time.monotonic() - started
    values = output.detach().cpu().numpy().astype("<f4", copy=False)
    stems = values[0].T.astype(np.float64)
    adapter = least_squares_adapter(stems, mixture)
    coefficients = adapter["coefficients"]
    reconstruction = adapter["reconstruction"]
    max_error = float(np.max(np.abs(reconstruction - mixture)))
    rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = rss_raw / (1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0)
    return {
        "schema": "murmurmark.stronger_separator_probe_child/v1",
        "profile": PROFILE,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch_version": torch.__version__,
        "torchaudio_version": torchaudio.__version__,
        "speechbrain_version": speechbrain_version,
        "load_sec": round(load_sec, 6),
        "inference_sec": round(inference_sec, 6),
        "peak_rss_mb": round(rss_mb, 6),
        "output_shape": list(values.shape),
        "output_finite": bool(np.isfinite(values).all()),
        "output_nonzero": bool(np.max(np.abs(values)) > 0.0),
        "raw_output_peak": round(float(np.max(np.abs(values))), 6),
        "output_sha256": hashlib.sha256(values.tobytes()).hexdigest(),
        "adapter_coefficients": [round(float(value), 9) for value in coefficients],
        "adapter_reconstruction_max_abs_error": round(max_error, 12),
        "adapter_finite": bool(np.isfinite(reconstruction).all()),
        "network_attempts": attempts,
        "torch_threads": torch.get_num_threads(),
        "resource_policy": resource_report,
    }


def run_probe_child_process(policy_path: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    environment = dict(os.environ)
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "MURMURMARK_RESOURCE_PROFILE": "background",
            "MURMURMARK_MAX_COMPUTE_THREADS": "4",
        }
    )
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "_probe-child", "--policy", str(policy_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        check=False,
        timeout=30,
    )
    meta = {
        "returncode": completed.returncode,
        "wall_sec": round(time.monotonic() - started, 6),
        "stderr": completed.stderr[-4000:],
    }
    if completed.returncode != 0:
        return None, meta
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        return json.loads(lines[-1]), meta
    except (IndexError, json.JSONDecodeError):
        meta["stdout"] = completed.stdout[-4000:]
        return None, meta


def run_resource_preflight(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    file_checks, model_bytes = verify_selected_backbone(policy)
    blockers = [row["path"] if "path" in row else row["name"] for row in file_checks if not row["passed"]]
    runs: list[dict[str, Any]] = []
    process_meta: list[dict[str, Any]] = []
    if not blockers:
        for _ in range(2):
            try:
                run, meta = run_probe_child_process(policy_path)
            except (OSError, subprocess.SubprocessError) as error:
                run, meta = None, {"error": str(error)}
            process_meta.append(meta)
            if run is not None:
                runs.append(run)
    budget = policy["resource_budget"]
    checks = list(file_checks)
    checks.append(checked("probe_runs", len(runs), 2))
    if len(runs) == 2:
        first = runs[0]
        checks.extend(
            [
                checked("output_replay_sha256", runs[1]["output_sha256"], first["output_sha256"]),
                checked("output_shape", first["output_shape"], budget["expected_output_shape"]),
                checked("output_finite", first["output_finite"], True),
                checked("output_nonzero", first["output_nonzero"], True),
                checked("adapter_finite", first["adapter_finite"], True),
                threshold("adapter_reconstruction", first["adapter_reconstruction_max_abs_error"], maximum=1.0e-8),
                checked("network_attempts", first["network_attempts"], []),
                threshold("nice", first["resource_policy"].get("nice_after", -1), minimum=float(budget["nice_min"])),
                threshold("torch_threads", first["torch_threads"], maximum=float(budget["torch_threads_max"])),
                threshold("peak_rss_mb", max(run["peak_rss_mb"] for run in runs), maximum=float(budget["peak_rss_mb_max"])),
                threshold("load_sec", max(run["load_sec"] for run in runs), maximum=float(budget["load_sec_max"])),
                threshold("inference_sec", max(run["inference_sec"] for run in runs), maximum=float(budget["one_second_inference_sec_max"])),
                threshold("raw_output_peak", max(run["raw_output_peak"] for run in runs), maximum=float(budget["raw_output_peak_max"])),
            ]
        )
    passed = all(row["passed"] for row in checks)
    deterministic_evidence = {
        "selected_backbone": policy["selected_backbone"]["id"],
        "model_files": policy["selected_backbone"]["model_files"],
        "runtime_wheels": policy["selected_backbone"]["runtime_wheels"],
        "decision": "RESOURCE_PREFLIGHT_PASSED" if passed else "RESOURCE_PREFLIGHT_FAILED",
        "runs": [
            {
                "python": run.get("python"),
                "platform": run.get("platform"),
                "torch_version": run.get("torch_version"),
                "torchaudio_version": run.get("torchaudio_version"),
                "speechbrain_version": run.get("speechbrain_version"),
                "output_shape": run.get("output_shape"),
                "output_finite": run.get("output_finite"),
                "output_nonzero": run.get("output_nonzero"),
                "output_sha256": run.get("output_sha256"),
                "adapter_finite": run.get("adapter_finite"),
                "adapter_reconstruction_max_abs_error": run.get("adapter_reconstruction_max_abs_error"),
                "network_attempts": run.get("network_attempts"),
                "torch_threads": run.get("torch_threads"),
                "nice_after": (run.get("resource_policy") or {}).get("nice_after"),
            }
            for run in runs
        ],
        "passed_check_names": sorted(row.get("name") or row.get("path") for row in checks if row["passed"]),
        "failed_check_names": sorted(row.get("name") or row.get("path") for row in checks if not row["passed"]),
    }
    report = with_fingerprint(
        {
            "schema": "murmurmark.stronger_separator_resource_preflight/v1",
            "profile": PROFILE,
            "decision": "RESOURCE_PREFLIGHT_PASSED" if passed else "RESOURCE_PREFLIGHT_FAILED",
            "passed": passed,
            "model_bytes": model_bytes,
            "runs": runs,
            "process_meta": process_meta,
            "checks": checks,
            "blockers": [row.get("name") or row.get("path") for row in checks if not row["passed"]],
            "evidence_fingerprint": digest_json(deterministic_evidence),
        }
    )
    write_json(output_dir / "resource_preflight.json", report)
    return report


def decision_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Stronger Offline Target-Speaker Separator Prerequisites v1",
        "",
        f"Decision: `{report['decision']}`",
        "",
        f"Selected backbone: `{report['selected_backbone']}`",
        f"Resource preflight: `{report['resource_preflight']}`",
        f"Supervision expansion: `{report['supervision_expansion']}`",
        "",
    ]
    if report["decision"] == READY:
        lines.extend(
            [
                "The next bounded stage may qualify the pinned backbone on expanded train/dev evidence.",
                "Hard, sealed, direct ASR and production publication remain closed.",
            ]
        )
    else:
        lines.append("The current machine, model or evidence set is not ready; production v2 remains exact fallback.")
    if report["blockers"]:
        lines.extend(("", "Blockers:", *[f"- `{item}`" for item in report["blockers"]]))
    return "\n".join(lines) + "\n"


def run_decide(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    frozen = read_json(output_dir / "frozen_inputs.json")
    supervision = read_json(output_dir / "supervision_expansion.json")
    preflight = read_json(output_dir / "resource_preflight.json")
    blockers: list[str] = []
    if not frozen.get("passed"):
        blockers.append("frozen_inputs")
    if supervision.get("status") != "READY_MANIFEST_ONLY":
        blockers.append("supervision_expansion")
    if not preflight.get("passed"):
        blockers.extend(str(item) for item in preflight.get("blockers") or ["resource_preflight"])
    if sum(item["status"] == "selected" for item in policy["backbone_shortlist"]) != 1:
        blockers.append("selected_backbone_count")
    decision = READY if not blockers else RESOURCE_LIMIT
    checks = [
        checked("frozen_inputs_fingerprint", fingerprint_valid(frozen), True),
        checked("supervision_fingerprint", fingerprint_valid(supervision), True),
        checked("resource_preflight_fingerprint", fingerprint_valid(preflight), True),
        checked("production_publication", policy["production_publication"], "forbidden"),
        checked("hard_or_sealed_access", policy["hard_or_sealed_access"], False),
        checked("training_performed", policy["training_performed"], False),
        checked("direct_asr_before_dev", policy["four_stem_adapter"]["direct_asr_before_dev_pass"], False),
        checked("post_asr_cleanup_credit", policy["post_asr_cleanup_promotion_credit"], 0),
    ]
    if not all(row["passed"] for row in checks):
        decision = RESOURCE_LIMIT
        blockers.extend(row["name"] for row in checks if not row["passed"])
    manifest = with_fingerprint(
        {
            "schema": "murmurmark.stronger_separator_readiness_manifest/v1",
            "profile": PROFILE,
            "policy_sha256": sha256(policy_path),
            "selected_backbone": policy["selected_backbone"]["id"],
            "frozen_inputs_fingerprint": frozen["fingerprint"],
            "gap_map_fingerprint": read_json(output_dir / "gap_map.json")["fingerprint"],
            "supervision_expansion_fingerprint": supervision["fingerprint"],
            "backbone_shortlist_fingerprint": read_json(output_dir / "backbone_shortlist.json")["fingerprint"],
            "license_evidence_fingerprint": read_json(output_dir / "license_evidence.json")["fingerprint"],
            "resource_preflight_evidence_fingerprint": preflight["evidence_fingerprint"],
            "adapter_plan_fingerprint": read_json(output_dir / "four_stem_adapter_plan.json")["fingerprint"],
            "next_stage": "stronger_separator_train_dev_qualification_v1" if decision == READY else None,
            "hard_opened": False,
            "sealed_opened": False,
            "direct_asr_opened": False,
            "production_changed": False,
        }
    )
    report = with_fingerprint(
        {
            "schema": "murmurmark.stronger_separator_prerequisites_decision/v1",
            "profile": PROFILE,
            "decision": decision,
            "selected_backbone": policy["selected_backbone"]["id"],
            "resource_preflight": preflight.get("decision"),
            "supervision_expansion": supervision.get("status"),
            "readiness_manifest_fingerprint": manifest["fingerprint"],
            "blockers": sorted(set(blockers)),
            "hard_opened": False,
            "sealed_opened": False,
            "direct_asr_opened": False,
            "production_changed": False,
            "checks": checks,
        }
    )
    write_json(output_dir / "readiness_manifest.json", manifest)
    write_json(output_dir / "decision.json", report)
    (output_dir / "decision.md").write_text(decision_markdown(report), encoding="utf-8")
    return report


def run_verify(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    names = (
        "frozen_inputs.json",
        "gap_map.json",
        "supervision_expansion.json",
        "backbone_shortlist.json",
        "license_evidence.json",
        "resource_preflight.json",
        "four_stem_adapter_plan.json",
        "readiness_manifest.json",
        "decision.json",
    )
    artifacts = {name: read_json(output_dir / name) for name in names}
    decision_value = artifacts["decision.json"].get("decision")
    decision_consistent = (
        decision_value == READY
        and artifacts["frozen_inputs.json"].get("passed") is True
        and artifacts["supervision_expansion.json"].get("status") == "READY_MANIFEST_ONLY"
        and artifacts["resource_preflight.json"].get("passed") is True
    ) or (
        decision_value == RESOURCE_LIMIT
        and bool(artifacts["decision.json"].get("blockers"))
    )
    source_checks = [verify_source_descriptor(item) for item in policy["sources"].values()]
    model_checks, _ = verify_selected_backbone(policy)
    input_checks = source_checks + model_checks
    input_integrity_passed = all(row["passed"] for row in input_checks)
    checks = [
        *[checked(f"fingerprint:{name}", fingerprint_valid(value), True) for name, value in artifacts.items()],
        checked("terminal_decision", decision_value in policy["terminal_decisions"], True),
        checked("decision_evidence_consistency", decision_consistent, True),
        checked(
            "input_integrity_required_for_ready",
            decision_value != READY or input_integrity_passed,
            True,
        ),
        checked("production_changed", artifacts["decision.json"].get("production_changed"), False),
        checked("hard_not_opened", artifacts["decision.json"].get("hard_opened"), False),
        checked("sealed_not_opened", artifacts["decision.json"].get("sealed_opened"), False),
        checked("direct_asr_not_opened", artifacts["decision.json"].get("direct_asr_opened"), False),
        checked("hard_access_marker_absent", (output_dir / "hard_access.json").exists(), False),
        checked("sealed_access_marker_absent", (output_dir / "sealed_access.json").exists(), False),
    ]
    report = with_fingerprint(
        {
            "schema": "murmurmark.stronger_separator_prerequisites_verification/v1",
            "profile": PROFILE,
            "passed": all(row["passed"] for row in checks),
            "decision": decision_value,
            "checks": checks,
            "input_checks": input_checks,
        }
    )
    write_json(output_dir / "verification_report.json", report)
    if not report["passed"]:
        raise RuntimeError("stronger separator prerequisite verification failed")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("freeze", "resource-preflight", "decide", "verify", "run", "_probe-child"),
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "policies/stronger-offline-target-speaker-separator-prerequisites-v1.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "sessions/_reports/stronger-offline-target-speaker-separator-prerequisites-v1",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "_probe-child":
        print(canonical_json(child_probe(args.policy.resolve())))
        return 0
    if args.command == "freeze":
        report = run_freeze(policy_path=args.policy.resolve(), output_dir=args.output_dir.resolve())
    elif args.command == "resource-preflight":
        report = run_resource_preflight(policy_path=args.policy.resolve(), output_dir=args.output_dir.resolve())
    elif args.command == "decide":
        report = run_decide(policy_path=args.policy.resolve(), output_dir=args.output_dir.resolve())
    elif args.command == "verify":
        report = run_verify(policy_path=args.policy.resolve(), output_dir=args.output_dir.resolve())
    else:
        run_freeze(policy_path=args.policy.resolve(), output_dir=args.output_dir.resolve())
        run_resource_preflight(policy_path=args.policy.resolve(), output_dir=args.output_dir.resolve())
        report = run_decide(policy_path=args.policy.resolve(), output_dir=args.output_dir.resolve())
        run_verify(policy_path=args.policy.resolve(), output_dir=args.output_dir.resolve())
    print(
        json.dumps(
            {
                "decision": report.get("decision"),
                "fingerprint": report.get("fingerprint"),
                "output_dir": display_path(args.output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
