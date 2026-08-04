#!/usr/bin/env python3
"""Shared low-impact scheduling policy for MurmurMark derived work."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_PROFILE = "background"
THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


@dataclass(frozen=True)
class ResourcePolicy:
    profile: str
    nice: int | None
    darwin_background: bool
    max_compute_threads: int
    asr_threads: int
    asr_track_workers: int
    micro_asr_workers: int
    live_asr_threads: int
    live_asr_parallelism: int


PROFILE_DEFAULTS: dict[str, ResourcePolicy] = {
    "background": ResourcePolicy(
        profile="background",
        nice=20,
        darwin_background=True,
        max_compute_threads=4,
        asr_threads=4,
        asr_track_workers=1,
        micro_asr_workers=1,
        live_asr_threads=3,
        live_asr_parallelism=1,
    ),
    "performance": ResourcePolicy(
        profile="performance",
        nice=None,
        darwin_background=False,
        max_compute_threads=0,
        asr_threads=6,
        asr_track_workers=2,
        micro_asr_workers=4,
        live_asr_threads=4,
        live_asr_parallelism=2,
    ),
}


def profile_name_from_environment() -> str:
    return (os.environ.get("MURMURMARK_RESOURCE_PROFILE") or DEFAULT_PROFILE).strip().lower()


def max_threads_from_environment() -> int | None:
    value = (os.environ.get("MURMURMARK_MAX_COMPUTE_THREADS") or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("MURMURMARK_MAX_COMPUTE_THREADS must be an integer") from error
    if parsed < 0:
        raise ValueError("MURMURMARK_MAX_COMPUTE_THREADS must be >= 0")
    return parsed


def resolve_resource_policy(
    profile: str | None = None,
    max_compute_threads: int | None = None,
) -> ResourcePolicy:
    name = (profile or profile_name_from_environment()).strip().lower()
    if name not in PROFILE_DEFAULTS:
        choices = ", ".join(sorted(PROFILE_DEFAULTS))
        raise ValueError(f"unknown MurmurMark resource profile {name!r}; expected one of: {choices}")
    base = PROFILE_DEFAULTS[name]
    limit = base.max_compute_threads if max_compute_threads is None else int(max_compute_threads)
    if limit < 0:
        raise ValueError("max_compute_threads must be >= 0")
    return ResourcePolicy(**{**asdict(base), "max_compute_threads": limit})


def bounded_threads(requested: int, policy: ResourcePolicy) -> int:
    value = max(1, int(requested))
    if policy.max_compute_threads > 0:
        value = min(value, policy.max_compute_threads)
    return value


def configure_thread_environment(policy: ResourcePolicy) -> dict[str, str]:
    if policy.max_compute_threads <= 0:
        os.environ["MURMURMARK_RESOURCE_PROFILE"] = policy.profile
        os.environ.pop("MURMURMARK_MAX_COMPUTE_THREADS", None)
        return {}

    limit = str(policy.max_compute_threads)
    applied: dict[str, str] = {}
    for name in THREAD_ENV_VARS:
        current = (os.environ.get(name) or "").strip()
        try:
            current_value = int(current) if current else None
        except ValueError:
            current_value = None
        if current_value is None or current_value > policy.max_compute_threads:
            os.environ[name] = limit
        applied[name] = os.environ[name]
    os.environ["MURMURMARK_RESOURCE_PROFILE"] = policy.profile
    os.environ["MURMURMARK_MAX_COMPUTE_THREADS"] = limit
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return applied


def configure_thread_environment_from_environment() -> ResourcePolicy:
    try:
        policy = resolve_resource_policy(max_compute_threads=max_threads_from_environment())
    except ValueError:
        # A live sidecar must fail low-impact rather than importing numeric libraries unconstrained.
        policy = resolve_resource_policy(DEFAULT_PROFILE)
    configure_thread_environment(policy)
    return policy


def apply_resource_policy(policy: ResourcePolicy) -> dict[str, Any]:
    thread_environment = configure_thread_environment(policy)
    nice_before: int | None = None
    nice_after: int | None = None
    nice_status = "disabled"
    taskpolicy_status = "disabled"
    warnings: list[str] = []

    if hasattr(os, "getpriority"):
        try:
            nice_before = os.getpriority(os.PRIO_PROCESS, 0)
        except OSError as error:
            warnings.append(f"getpriority_failed:{error}")

    if policy.nice is not None and hasattr(os, "setpriority"):
        try:
            os.setpriority(os.PRIO_PROCESS, 0, policy.nice)
            nice_status = "applied"
        except OSError as error:
            nice_status = "failed_open"
            warnings.append(f"setpriority_failed:{error}")

    if hasattr(os, "getpriority"):
        try:
            nice_after = os.getpriority(os.PRIO_PROCESS, 0)
        except OSError as error:
            warnings.append(f"getpriority_after_failed:{error}")

    taskpolicy = Path("/usr/sbin/taskpolicy")
    if policy.darwin_background and sys.platform == "darwin" and taskpolicy.is_file():
        completed = subprocess.run(
            [str(taskpolicy), "-b", "-p", str(os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            taskpolicy_status = "applied"
        else:
            taskpolicy_status = "failed_open"
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            warnings.append(f"taskpolicy_failed:{detail}")
    elif policy.darwin_background:
        taskpolicy_status = "unavailable"

    os.environ["MURMURMARK_RESOURCE_POLICY_APPLIED"] = "1"
    status = "disabled" if policy.profile == "performance" else ("applied" if not warnings else "applied_with_warnings")
    return {
        "schema": "murmurmark.resource_policy/v1",
        "status": status,
        "profile": policy.profile,
        "pid": os.getpid(),
        "nice_requested": policy.nice,
        "nice_before": nice_before,
        "nice_after": nice_after,
        "nice_status": nice_status,
        "darwin_background_requested": policy.darwin_background,
        "taskpolicy_status": taskpolicy_status,
        "max_compute_threads": policy.max_compute_threads,
        "thread_environment": thread_environment,
        "asr_defaults": {
            "threads": bounded_threads(policy.asr_threads, policy),
            "track_workers": policy.asr_track_workers,
            "micro_asr_workers": policy.micro_asr_workers,
        },
        "live_asr_defaults": {
            "threads": bounded_threads(policy.live_asr_threads, policy),
            "parallelism": policy.live_asr_parallelism,
        },
        "warnings": warnings,
    }


def print_resource_policy(report: dict[str, Any], *, prefix: str = "resource_policy") -> None:
    print(
        f"{prefix}: profile={report.get('profile')} "
        f"nice={report.get('nice_after')} "
        f"taskpolicy={report.get('taskpolicy_status')} "
        f"max_compute_threads={report.get('max_compute_threads')}",
        flush=True,
    )
