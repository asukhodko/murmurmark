#!/usr/bin/env python3
"""Content-addressed helpers for recording-time causal recovery caches."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "murmurmark.live_recovery_incremental_cache/v1"
SCRIPT_VERSION = "1.0.0"
PATH_KEYS = {"input", "wav", "asr_wav", "json", "path"}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class FileDigestMemo:
    """Reuse a real SHA-256 while file identity is unchanged by stat evidence."""

    def __init__(self, root: Path, *, session: Path | None = None) -> None:
        self.root = root
        self.path = root / "file_digests.json"
        self.session = session.resolve() if session else None
        payload = read_json(self.path)
        self.entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}
        self.hits = 0
        self.misses = 0
        self.missing = 0

    def display_path(self, path: Path) -> str:
        resolved = path.resolve()
        if self.session:
            try:
                return str(resolved.relative_to(self.session))
            except ValueError:
                pass
        return str(resolved)

    def fingerprint(self, path: Path) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        display = self.display_path(resolved)
        try:
            stat = resolved.stat()
        except OSError:
            self.missing += 1
            return {"path": display, "status": "missing"}
        cache_key = str(resolved)
        previous = self.entries.get(cache_key) if isinstance(self.entries.get(cache_key), dict) else {}
        if (
            previous.get("bytes") == stat.st_size
            and previous.get("mtime_ns") == stat.st_mtime_ns
            and previous.get("inode") == stat.st_ino
            and previous.get("sha256")
        ):
            digest = str(previous["sha256"])
            self.hits += 1
        else:
            digest = file_sha256(resolved)
            self.misses += 1
        self.entries[cache_key] = {
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "inode": stat.st_ino,
            "sha256": digest,
        }
        return {
            "path": display,
            "status": "present",
            "bytes": stat.st_size,
            "sha256": digest,
        }

    def save(self) -> None:
        write_json(
            self.path,
            {
                "schema": SCHEMA,
                "generator": {
                    "name": "live_recovery_incremental_cache.FileDigestMemo",
                    "version": SCRIPT_VERSION,
                },
                "entries": self.entries,
            },
        )

    def telemetry(self) -> dict[str, int]:
        return {
            "digest_cache_hits": self.hits,
            "digest_cache_misses": self.misses,
            "missing_file_count": self.missing,
        }


def resolve_payload_paths(session: Path, payload: Any) -> list[Path]:
    paths: set[Path] = set()

    def visit(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
            return
        if isinstance(value, list):
            for child in value:
                visit(child, key)
            return
        if key not in PATH_KEYS or not isinstance(value, str) or not value.strip():
            return
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = session / candidate
        if candidate.exists() and candidate.is_file():
            paths.add(candidate.resolve())

    visit(payload)
    return sorted(paths, key=str)


def payload_file_fingerprints(
    session: Path,
    payloads: Iterable[Any],
    memo: FileDigestMemo,
) -> list[dict[str, Any]]:
    paths: set[Path] = set()
    for payload in payloads:
        paths.update(resolve_payload_paths(session, payload))
    return [memo.fingerprint(path) for path in sorted(paths, key=str)]


def algorithm_fingerprint(paths: Iterable[Path], memo: FileDigestMemo) -> dict[str, Any]:
    files = [memo.fingerprint(path) for path in sorted({path.resolve() for path in paths}, key=str)]
    return {"files": files, "sha256": content_hash(files)}


def context_fingerprint(
    *,
    stage: str,
    algorithm: dict[str, Any],
    model: dict[str, Any],
    configuration: dict[str, Any],
) -> dict[str, Any]:
    descriptor = {
        "schema": SCHEMA,
        "stage": stage,
        "algorithm": algorithm,
        "model": model,
        "configuration": configuration,
    }
    return {**descriptor, "sha256": content_hash(descriptor)}


def chunk_plan(
    *,
    previous_state: dict[str, Any],
    context_sha256: str,
    current_inputs: dict[int, str],
    current_components: dict[int, dict[str, str]] | None = None,
) -> dict[str, Any]:
    previous_entries = (
        previous_state.get("entries") if isinstance(previous_state.get("entries"), dict) else {}
    )
    previous_context = str(previous_state.get("context_sha256") or "")
    indexes = sorted(current_inputs)
    invalidation_reason: str | None = None
    earliest_invalidated: int | None = None
    changed_components: list[str] = []
    if previous_context and previous_context != context_sha256:
        invalidation_reason = "stage_context_changed"
        earliest_invalidated = indexes[0] if indexes else None
    elif not previous_context and previous_entries:
        invalidation_reason = "legacy_state_without_context"
        earliest_invalidated = indexes[0] if indexes else None
    else:
        for index in indexes:
            entry = previous_entries.get(str(index))
            if not isinstance(entry, dict):
                earliest_invalidated = index
                invalidation_reason = "new_closed_evidence"
                break
            if entry.get("input_sha256") != current_inputs[index]:
                earliest_invalidated = index
                invalidation_reason = "closed_evidence_changed"
                previous_components = (
                    entry.get("input_components")
                    if isinstance(entry.get("input_components"), dict)
                    else {}
                )
                next_components = (current_components or {}).get(index) or {}
                changed_components = sorted(
                    key
                    for key in set(previous_components) | set(next_components)
                    if previous_components.get(key) != next_components.get(key)
                )
                break
            object_path = Path(str(entry.get("object_path") or ""))
            if not object_path.is_absolute() and previous_state.get("cache_root"):
                object_path = Path(str(previous_state["cache_root"])) / object_path
            if not object_path.exists():
                earliest_invalidated = index
                invalidation_reason = "cache_object_missing"
                break
    if earliest_invalidated is None:
        reused = indexes
        process = []
    else:
        reused = [index for index in indexes if index < earliest_invalidated]
        process = [index for index in indexes if index >= earliest_invalidated]
    return {
        "reused_indexes": reused,
        "process_indexes": process,
        "earliest_invalidated_chunk": earliest_invalidated,
        "invalidation_reason": invalidation_reason,
        "changed_components": changed_components,
    }


def object_directory(cache_root: Path, input_sha256: str) -> Path:
    return cache_root / "objects" / input_sha256[:2] / input_sha256


def relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def resolve_cached_path(value: Any, cache_root: Path) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else cache_root / path


def atomic_replace_directory(temporary: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return
    os.replace(temporary, destination)
