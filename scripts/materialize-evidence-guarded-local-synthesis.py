#!/usr/bin/env python3
"""Qualify and publish evidence-guarded local meeting synthesis."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.evidence_guarded_local_synthesis_policy/v1"
HANDOFF_SCHEMA = "murmurmark.evidence_guarded_local_synthesis_handoff/v1"
SYNTHESIS_SCHEMA = "murmurmark.evidence_guarded_local_synthesis/v1"
MODEL_RUN_SCHEMA = "murmurmark.evidence_guarded_local_synthesis_model_run/v1"
REPORT_SCHEMA = "murmurmark.evidence_guarded_local_synthesis_report/v1"
FROZEN_SCHEMA = "murmurmark.evidence_guarded_local_synthesis_frozen_manifest/v1"
DEFAULT_OUTPUT = Path("derived/meeting-memory/local-synthesis-v1")
DEFAULT_POLICY = Path("policies/evidence-guarded-local-synthesis-v1.json")
OUTPUT_FILENAMES = {
    "synthesis_json": "local_synthesis.json",
    "model_run_json": "model_run.json",
    "meeting": "meeting.md",
    "notes": "notes.md",
    "transcript": "transcript.md",
    "quality_verdict": "quality_verdict.md",
}
CATEGORIES = ("summary", "decisions", "actions", "risks", "open_questions")
SOURCE_CATEGORY = {
    "outline": "summary",
    "decisions": "decisions",
    "actions": "actions",
    "risks": "risks",
    "open_questions": "open_questions",
}
HEADINGS = {
    "summary": "Summary",
    "decisions": "Potential Decisions",
    "actions": "Potential Actions",
    "risks": "Risks",
    "open_questions": "Open Questions",
}
STOP_WORDS = {
    "а", "без", "бы", "был", "была", "были", "было", "в", "вам", "вас", "весь", "во",
    "вот", "все", "всего", "вы", "где", "да", "для", "до", "его", "ее", "если", "есть",
    "еще", "же", "за", "здесь", "и", "из", "или", "им", "их", "к", "как", "когда", "кто",
    "ли", "мы", "на", "над", "нам", "нас", "не", "него", "нее", "нет", "но", "ну", "о",
    "об", "он", "она", "они", "оно", "от", "по", "под", "при", "про", "с", "со", "так",
    "там", "то", "того", "тоже", "только", "у", "уже", "чтобы", "что", "это", "этого", "я",
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "are", "be",
}
NEGATIONS = {"без", "не", "нельзя", "нет", "никогда", "никто", "ничего"}
COMMITMENT_MARKERS = {
    "будем", "делаем", "договорились", "нужно", "надо", "обязан", "обязаны", "решили",
    "сделаем", "сделаю", "согласовали", "тогда",
}
TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?|[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_+#./-]*")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


class LocalSynthesisError(RuntimeError):
    pass


class SimulatedInterruption(RuntimeError):
    pass


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise LocalSynthesisError(f"helper_cannot_be_loaded:{path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


speaker_memory = load_module(
    "reviewed_speaker_memory_for_local_synthesis",
    ROOT / "scripts/materialize-reviewed-speaker-memory.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--speaker-memory-dir", type=Path)
    parser.add_argument("--reviewed-speaker-dir", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--print-path", choices=sorted(OUTPUT_FILENAMES))
    parser.add_argument("--qualification-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--keep-alive", help=argparse.SUPPRESS)
    parser.add_argument("--simulate-interruption-before-publish", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def compact_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LocalSynthesisError(f"invalid_or_missing_json:{path.name}:{type(error).__name__}") from error
    if not isinstance(payload, dict):
        raise LocalSynthesisError(f"invalid_json_object:{path.name}")
    return payload


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def resolve_inside(root: Path, raw: str | Path) -> Path:
    candidate = Path(raw).expanduser()
    candidate = candidate if candidate.is_absolute() else root / candidate
    result = candidate.resolve()
    if not within(result, root):
        raise LocalSynthesisError("path_outside_session")
    return result


def repository_path(raw: str | Path) -> Path:
    candidate = Path(raw).expanduser()
    candidate = candidate if candidate.is_absolute() else ROOT / candidate
    result = candidate.resolve()
    if not within(result, ROOT):
        raise LocalSynthesisError("path_outside_repository")
    return result


def identity(path: Path, session: Path) -> dict[str, Any]:
    if not path.is_file() or not within(path, session):
        raise LocalSynthesisError(f"session_input_missing:{path.name}")
    return {
        "scope": "session",
        "path": str(path.resolve().relative_to(session.resolve())),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def repository_identity(path: Path) -> dict[str, Any]:
    if not path.is_file() or not within(path, ROOT):
        raise LocalSynthesisError(f"repository_input_missing:{path.name}")
    return {
        "scope": "repository",
        "path": str(path.resolve().relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def resolve_identity(row: Any, session: Path) -> Path | None:
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        return None
    try:
        if row.get("scope") == "session":
            return resolve_inside(session, row["path"])
        if row.get("scope") == "repository":
            return repository_path(row["path"])
    except LocalSynthesisError:
        return None
    return None


def identity_matches(row: Any, session: Path) -> bool:
    path = resolve_identity(row, session)
    return bool(
        path is not None
        and path.is_file()
        and int(row.get("bytes") or -1) == path.stat().st_size
        and row.get("sha256") == sha256_file(path)
    )


def implementation() -> dict[str, Any]:
    path = Path(__file__).resolve()
    return {"script": path.name, "version": SCRIPT_VERSION, "fingerprint": repository_identity(path)}


def validate_loopback_endpoint(raw: str) -> str:
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise LocalSynthesisError("ollama_endpoint_not_loopback")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
        raise LocalSynthesisError("ollama_endpoint_invalid")
    return raw.rstrip("/")


def policy_path(raw: Path | None) -> Path:
    return repository_path(raw or DEFAULT_POLICY)


def validate_policy(path: Path, *, allow_unpromoted: bool) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise LocalSynthesisError("policy_schema_mismatch")
    decision = policy.get("decision")
    allowed = {"PROMOTE_OPTIONAL_LOCAL_SYNTHESIS"}
    if allow_unpromoted:
        allowed.update({"QUALIFICATION_PENDING", "DO_NOT_PROMOTE"})
    if decision not in allowed:
        raise LocalSynthesisError("policy_not_promoted")
    prompt = policy.get("prompt") if isinstance(policy.get("prompt"), dict) else {}
    prompt_file = repository_path(str(prompt.get("path") or ""))
    if not prompt_file.is_file() or sha256_file(prompt_file) != prompt.get("sha256"):
        raise LocalSynthesisError("prompt_fingerprint_mismatch")
    runtime = policy.get("runtime") if isinstance(policy.get("runtime"), dict) else {}
    validate_loopback_endpoint(str(runtime.get("endpoint") or ""))
    if not allow_unpromoted:
        source = policy.get("source") if isinstance(policy.get("source"), dict) else {}
        if source.get("materializer") != implementation():
            raise LocalSynthesisError("policy_materializer_fingerprint_mismatch")
        raw_manifest = source.get("frozen_manifest_path")
        frozen_identity = source.get("frozen_manifest")
        if not isinstance(raw_manifest, str) or not isinstance(frozen_identity, dict):
            raise LocalSynthesisError("policy_frozen_manifest_missing")
        frozen_path = repository_path(raw_manifest)
        actual = repository_identity(frozen_path)
        if any(actual.get(key) != frozen_identity.get(key) for key in ("scope", "bytes", "sha256", "path")):
            raise LocalSynthesisError("policy_frozen_manifest_fingerprint_mismatch")
        frozen = read_json(frozen_path)
        if frozen.get("schema") != FROZEN_SCHEMA or frozen.get("decision") != decision:
            raise LocalSynthesisError("policy_frozen_manifest_not_promoted")
    return policy


def request_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise LocalSynthesisError(f"ollama_request_failed:{type(error).__name__}") from error
    if not isinstance(value, dict):
        raise LocalSynthesisError("ollama_response_not_object")
    return value


def ollama_version() -> str:
    executable = shutil.which("ollama")
    if executable is None:
        raise LocalSynthesisError("ollama_executable_missing")
    result = subprocess.run(
        [executable, "--version"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    match = re.search(r"(\d+\.\d+\.\d+)", result.stdout + result.stderr)
    if result.returncode != 0 or match is None:
        raise LocalSynthesisError("ollama_version_unavailable")
    return match.group(1)


def inspect_runtime(policy: dict[str, Any]) -> dict[str, Any]:
    runtime = policy["runtime"]
    endpoint = validate_loopback_endpoint(str(runtime["endpoint"]))
    version = ollama_version()
    if version != runtime.get("qualified_version"):
        raise LocalSynthesisError("ollama_version_mismatch")
    model_policy = policy["model"]
    show = request_json(f"{endpoint}/api/show", {"model": model_policy["name"]}, 30)
    modelfile = str(show.get("modelfile") or "")
    match = re.search(r"sha256-([0-9a-f]{64})", modelfile)
    details = show.get("details") if isinstance(show.get("details"), dict) else {}
    license_text = str(show.get("license") or "")
    checks = {
        "blob": match.group(1) if match else None,
        "license_sha256": sha256_bytes(license_text.encode("utf-8")),
        "architecture": details.get("family"),
        "parameter_size": details.get("parameter_size"),
        "quantization": details.get("quantization_level"),
    }
    expected = {
        "blob": model_policy.get("blob_sha256"),
        "license_sha256": model_policy.get("license_sha256"),
        "architecture": model_policy.get("architecture"),
        "parameter_size": model_policy.get("parameter_size"),
        "quantization": model_policy.get("quantization"),
    }
    if checks != expected:
        raise LocalSynthesisError("ollama_model_identity_mismatch")
    return {
        "runtime": "ollama",
        "runtime_version": version,
        "endpoint_scope": "loopback",
        "model": model_policy["name"],
        "model_blob_sha256": checks["blob"],
        "license": model_policy.get("license"),
        "license_sha256": checks["license_sha256"],
        "architecture": checks["architecture"],
        "parameter_size": checks["parameter_size"],
        "quantization": checks["quantization"],
    }


class OllamaSampler:
    def __init__(self, blob_sha256: str) -> None:
        self.blob_sha256 = blob_sha256
        self.stop_event = threading.Event()
        self.peak_rss_kb = 0
        self.pids: set[int] = set()
        self.nice_applied: set[int] = set()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "OllamaSampler":
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            result = subprocess.run(
                ["ps", "-axo", "pid=,rss=,command="],
                text=True,
                capture_output=True,
                check=False,
            )
            total = 0
            for line in result.stdout.splitlines():
                if "llama-server" not in line or self.blob_sha256 not in line:
                    continue
                parts = line.strip().split(maxsplit=2)
                if len(parts) < 3:
                    continue
                try:
                    pid, rss = int(parts[0]), int(parts[1])
                except ValueError:
                    continue
                self.pids.add(pid)
                total += rss
                if pid not in self.nice_applied and hasattr(os, "setpriority"):
                    try:
                        os.setpriority(os.PRIO_PROCESS, pid, 20)
                        self.nice_applied.add(pid)
                    except OSError:
                        pass
            self.peak_rss_kb = max(self.peak_rss_kb, total)
            self.stop_event.wait(0.2)


def run_model(
    prompt: str,
    policy: dict[str, Any],
    *,
    keep_alive: str | None = None,
    client: Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if client is not None:
        return client(prompt, policy)
    runtime_identity = inspect_runtime(policy)
    endpoint = validate_loopback_endpoint(str(policy["runtime"]["endpoint"]))
    generation = dict(policy["generation"])
    request_payload = {
        "model": policy["model"]["name"],
        "stream": False,
        "format": "json",
        "prompt": prompt,
        "keep_alive": keep_alive or generation.pop("keep_alive"),
        "options": generation,
    }
    started = time.monotonic()
    with OllamaSampler(str(policy["model"]["blob_sha256"])) as sampler:
        response = request_json(f"{endpoint}/api/generate", request_payload, 600)
    wall_sec = time.monotonic() - started
    raw_response = str(response.get("response") or "")
    if not raw_response.strip():
        raise LocalSynthesisError("ollama_empty_response")
    deterministic = {
        "runtime_identity": runtime_identity,
        "request": {
            "model": request_payload["model"],
            "format": request_payload["format"],
            "options": request_payload["options"],
        },
        "raw_response": raw_response,
        "raw_response_sha256": sha256_bytes(raw_response.encode("utf-8")),
        "prompt_eval_count": int(response.get("prompt_eval_count") or 0),
        "eval_count": int(response.get("eval_count") or 0),
    }
    performance = {
        "wall_sec": round(wall_sec, 6),
        "load_sec": round(float(response.get("load_duration") or 0) / 1_000_000_000, 6),
        "prompt_eval_sec": round(float(response.get("prompt_eval_duration") or 0) / 1_000_000_000, 6),
        "eval_sec": round(float(response.get("eval_duration") or 0) / 1_000_000_000, 6),
        "peak_model_rss_mb": round(sampler.peak_rss_kb / 1024, 3),
        "sampled_model_pids": sorted(sampler.pids),
        "nice_20_applied": bool(sampler.nice_applied),
    }
    return deterministic, performance


def normalize_token(value: str) -> str:
    return value.lower().replace("ё", "е").strip("-_/.")


def tokens(text: str) -> list[str]:
    return [normalize_token(match.group(0)) for match in TOKEN_RE.finditer(text) if normalize_token(match.group(0))]


def token_stem(value: str) -> str:
    if len(value) < 6 or any(character.isdigit() for character in value):
        return value
    for suffix in (
        "иями", "ями", "ами", "его", "ого", "ему", "ому", "ение", "ений", "ания", "аний",
        "аться", "яться", "ить", "ать", "ять", "ешь", "ете", "ем", "ам", "ям", "ах", "ях",
        "ов", "ев", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ей", "у", "ю",
        "а", "я", "ы", "и", "е",
    ):
        if value.endswith(suffix) and len(value) - len(suffix) >= 4:
            return value[: -len(suffix)]
    return value


def content_tokens(text: str) -> list[str]:
    return [item for item in tokens(text) if item not in STOP_WORDS and len(item) > 1]


def dedupe(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def build_prompt_input(memory_payload: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    bindings = {
        str(row.get("utterance_id")): row
        for row in memory_payload.get("utterance_bindings") or []
        if isinstance(row, dict) and row.get("utterance_id")
    }
    statements: list[dict[str, Any]] = []
    referenced_ids: set[str] = set()
    for row in memory_payload.get("statement_bindings") or []:
        if not isinstance(row, dict):
            continue
        category = SOURCE_CATEGORY.get(str(row.get("category") or ""))
        if category is None:
            continue
        evidence_ids = dedupe(row.get("evidence_utterance_ids") or [])
        if not evidence_ids:
            continue
        referenced_ids.update(evidence_ids)
        statements.append(
            {
                "statement_id": str(row.get("statement_id") or ""),
                "category": category,
                "text": str(row.get("text") or ""),
                "evidence_utterance_ids": evidence_ids,
                "needs_review": bool(row.get("needs_review")),
                "evidence_speakers": dedupe(
                    [item.get("display_label") for item in row.get("speaker_evidence") or [] if isinstance(item, dict)]
                ),
            }
        )
    evidence_rows = {
        str(row.get("id")): row
        for row in evidence.get("evidence_utterances") or []
        if isinstance(row, dict) and row.get("id")
    }
    missing = referenced_ids - set(evidence_rows)
    if missing:
        raise LocalSynthesisError("prompt_evidence_utterances_missing:" + ",".join(sorted(missing)))
    utterances = []
    for utterance_id in sorted(referenced_ids):
        row = evidence_rows[utterance_id]
        binding = bindings.get(utterance_id)
        if binding is None:
            raise LocalSynthesisError(f"prompt_speaker_binding_missing:{utterance_id}")
        utterances.append(
            {
                "utterance_id": utterance_id,
                "speaker": str(binding.get("display_label") or ""),
                "text": str(row.get("text") or ""),
            }
        )
    allowed_labels = sorted(
        {
            str(row.get("display_label") or "")
            for row in memory_payload.get("speaker_bindings") or []
            if isinstance(row, dict) and row.get("display_label")
        }
    )
    return {
        "schema": "murmurmark.local_synthesis_prompt_input/v1",
        "session_id": memory_payload.get("session_id"),
        "allowed_display_labels": allowed_labels,
        "statements": statements,
        "evidence_utterances": utterances,
    }


def render_prompt(policy: dict[str, Any], prompt_input: dict[str, Any]) -> tuple[str, Path]:
    prompt_path = repository_path(policy["prompt"]["path"])
    template = prompt_path.read_text(encoding="utf-8")
    marker = "{{INPUT_JSON}}"
    if template.count(marker) != 1:
        raise LocalSynthesisError("prompt_input_marker_invalid")
    rendered = template.replace(marker, compact_bytes(prompt_input).decode("utf-8"))
    return rendered, prompt_path


def prompt_subset(prompt_input: dict[str, Any], statements: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_ids = {
        item for row in statements for item in row.get("evidence_utterance_ids") or []
    }
    return {
        "schema": prompt_input["schema"],
        "session_id": prompt_input["session_id"],
        "allowed_display_labels": prompt_input["allowed_display_labels"],
        "statements": statements,
        "evidence_utterances": [
            row for row in prompt_input["evidence_utterances"] if row["utterance_id"] in evidence_ids
        ],
    }


def split_prompt_inputs(
    policy: dict[str, Any], prompt_input: dict[str, Any]
) -> list[tuple[dict[str, Any], str, Path]]:
    chunking = policy.get("prompt_chunking") if isinstance(policy.get("prompt_chunking"), dict) else {}
    max_bytes = int(chunking.get("max_rendered_bytes") or 0)
    max_statements = int(chunking.get("max_statements") or 0)
    if max_bytes <= 0 or max_statements <= 0:
        raise LocalSynthesisError("prompt_chunking_policy_invalid")
    statements = prompt_input["statements"]
    if not statements:
        rendered, prompt_path = render_prompt(policy, prompt_input)
        return [(prompt_input, rendered, prompt_path)]
    chunks: list[tuple[dict[str, Any], str, Path]] = []
    current: list[dict[str, Any]] = []
    for row in statements:
        candidate = current + [row]
        subset = prompt_subset(prompt_input, candidate)
        rendered, prompt_path = render_prompt(policy, subset)
        if current and (len(candidate) > max_statements or len(rendered.encode("utf-8")) > max_bytes):
            ready = prompt_subset(prompt_input, current)
            ready_rendered, ready_path = render_prompt(policy, ready)
            chunks.append((ready, ready_rendered, ready_path))
            current = [row]
            subset = prompt_subset(prompt_input, current)
            rendered, prompt_path = render_prompt(policy, subset)
        if len(rendered.encode("utf-8")) > max_bytes:
            raise LocalSynthesisError(f"single_statement_exceeds_prompt_budget:{row['statement_id']}")
        current = list(subset["statements"])
    if current:
        ready = prompt_subset(prompt_input, current)
        ready_rendered, ready_path = render_prompt(policy, ready)
        chunks.append((ready, ready_rendered, ready_path))
    return chunks


def parse_model_response(raw: str) -> tuple[dict[str, Any], list[str]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        stripped = raw.strip()
        raise LocalSynthesisError(
            "model_response_invalid_json:"
            f"bytes={len(raw.encode('utf-8'))}:"
            f"starts_object={stripped.startswith('{')}:ends_object={stripped.endswith('}')}"
        ) from error
    if not isinstance(payload, dict):
        raise LocalSynthesisError("model_response_shape_invalid:not_object")
    missing = sorted(set(CATEGORIES) - set(payload))
    extra = sorted(set(payload) - set(CATEGORIES))
    if extra:
        raise LocalSynthesisError(
            "model_response_shape_invalid:"
            f"missing={','.join(missing) or 'none'}:extra={','.join(extra) or 'none'}"
        )
    for category in missing:
        payload[category] = []
    return payload, missing


def verify_proposals(
    response: dict[str, Any],
    prompt_input: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    statement_by_id = {row["statement_id"]: row for row in prompt_input["statements"]}
    utterance_by_id = {row["utterance_id"]: row for row in prompt_input["evidence_utterances"]}
    allowed_labels = [str(item) for item in prompt_input["allowed_display_labels"]]
    support_policy = policy["support_gates"]
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    category_source_presence = {
        category: any(row["category"] == category for row in prompt_input["statements"])
        for category in CATEGORIES
    }
    for category in CATEGORIES:
        rows = response.get(category)
        if not isinstance(rows, list):
            raise LocalSynthesisError(f"model_category_not_array:{category}")
        limit = int(policy["limits"][category])
        for index, proposal in enumerate(rows):
            reasons: list[str] = []
            if index >= limit:
                reasons.append("category_limit_exceeded")
            if not isinstance(proposal, dict) or set(proposal) != {
                "text", "source_statement_ids", "evidence_utterance_ids"
            }:
                rejected.append({"category": category, "proposal": proposal, "reasons": ["item_shape_invalid"]})
                continue
            text = str(proposal.get("text") or "").strip()
            source_ids = dedupe(proposal.get("source_statement_ids") if isinstance(proposal.get("source_statement_ids"), list) else [])
            evidence_ids = dedupe(proposal.get("evidence_utterance_ids") if isinstance(proposal.get("evidence_utterance_ids"), list) else [])
            if not text:
                reasons.append("empty_text")
            if len(text) > int(support_policy["max_claim_chars"]):
                reasons.append("claim_too_long")
            if not source_ids:
                reasons.append("missing_source_statement_ids")
            if not evidence_ids:
                reasons.append("missing_evidence_utterance_ids")
            unknown_sources = set(source_ids) - set(statement_by_id)
            unknown_evidence = set(evidence_ids) - set(utterance_by_id)
            if unknown_sources:
                reasons.append("unknown_source_statement_ids")
            if unknown_evidence:
                reasons.append("unknown_evidence_utterance_ids")
            source_rows = [statement_by_id[item] for item in source_ids if item in statement_by_id]
            if source_rows and any(row["category"] != category for row in source_rows):
                reasons.append("source_category_mismatch")
            allowed_evidence = {
                item for row in source_rows for item in row.get("evidence_utterance_ids") or []
            }
            if evidence_ids and not set(evidence_ids).issubset(allowed_evidence):
                reasons.append("statement_evidence_membership_mismatch")
            source_text = " ".join(str(row.get("text") or "") for row in source_rows)
            evidence_text = " ".join(
                str(utterance_by_id[item].get("text") or "") for item in evidence_ids if item in utterance_by_id
            )
            support_text = f"{source_text} {evidence_text}".strip()
            claim_tokens = content_tokens(text)
            support_tokens = content_tokens(support_text)
            support_set = set(support_tokens)
            support_stems = {token_stem(item) for item in support_tokens}
            supported = [
                item for item in claim_tokens if item in support_set or token_stem(item) in support_stems
            ]
            coverage = len(supported) / len(claim_tokens) if claim_tokens else 0.0
            normalized_text = " ".join(tokens(text))
            normalized_support = " ".join(tokens(support_text))
            extractive = bool(normalized_text and normalized_text in normalized_support)
            if not extractive and coverage < float(support_policy["min_content_token_coverage"]):
                reasons.append("insufficient_content_token_support")
            claim_numbers = set(NUMBER_RE.findall(text))
            support_numbers = set(NUMBER_RE.findall(support_text))
            if claim_numbers - support_numbers:
                reasons.append("unsupported_number")
            claim_token_set = set(tokens(text))
            support_token_set = set(tokens(support_text))
            if (claim_token_set & NEGATIONS) - (support_token_set & NEGATIONS):
                reasons.append("unsupported_negation")
            if (claim_token_set & COMMITMENT_MARKERS) - (support_token_set & COMMITMENT_MARKERS):
                reasons.append("unsupported_commitment")
            selected_speakers = {
                str(utterance_by_id[item].get("speaker") or "")
                for item in evidence_ids
                if item in utterance_by_id
            }
            for label in allowed_labels:
                if label and label.lower() in text.lower() and label not in selected_speakers:
                    reasons.append("speaker_label_without_selected_evidence")
                    break
            duplicate_key = normalized_text
            if duplicate_key and duplicate_key in seen_text:
                reasons.append("duplicate_claim")
            row = {
                "category": category,
                "text": text,
                "source_statement_ids": source_ids,
                "evidence_utterance_ids": evidence_ids,
                "support": {
                    "content_token_coverage": round(coverage, 6),
                    "extractive_substring": extractive,
                    "numbers_preserved": not bool(claim_numbers - support_numbers),
                    "negation_preserved": "unsupported_negation" not in reasons,
                    "commitment_preserved": "unsupported_commitment" not in reasons,
                    "speaker_provenance_valid": "speaker_label_without_selected_evidence" not in reasons,
                },
                "needs_review": any(bool(item.get("needs_review")) for item in source_rows),
            }
            if reasons:
                rejected.append({**row, "reasons": dedupe(reasons)})
            else:
                seen_text.add(duplicate_key)
                accepted.append(
                    {
                        "id": f"local_{category}_{sum(item['category'] == category for item in accepted) + 1:02d}",
                        **row,
                        "evidence_verified": True,
                    }
                )
    proposed = len(accepted) + len(rejected)
    available_categories = sum(category_source_presence.values())
    accepted_categories = len({row["category"] for row in accepted})
    selection_only_reasons = {"category_limit_exceeded", "duplicate_claim"}
    rejection_reason_counts: dict[str, int] = {}
    for row in rejected:
        for reason in row.get("reasons") or []:
            rejection_reason_counts[str(reason)] = rejection_reason_counts.get(str(reason), 0) + 1
        row["rejection_class"] = (
            "selection_hidden"
            if set(row.get("reasons") or []).issubset(selection_only_reasons)
            else "safety_rejected"
        )
    safety_rejected = sum(row["rejection_class"] == "safety_rejected" for row in rejected)
    selection_hidden = sum(row["rejection_class"] == "selection_hidden" for row in rejected)
    metrics = {
        "proposed_claims": proposed,
        "accepted_claims": len(accepted),
        "rejected_claims": len(rejected),
        "rejected_ratio": round(len(rejected) / proposed, 6) if proposed else 0.0,
        "safety_rejected_claims": safety_rejected,
        "safety_rejected_ratio": round(safety_rejected / proposed, 6) if proposed else 0.0,
        "selection_hidden_claims": selection_hidden,
        "published_unsupported_claims": 0,
        "available_categories": available_categories,
        "accepted_categories": accepted_categories,
        "category_coverage_ratio": round(accepted_categories / available_categories, 6) if available_categories else 1.0,
        "accepted_evidence_utterances": len({item for row in accepted for item in row["evidence_utterance_ids"]}),
        "available_evidence_utterances": len(prompt_input["evidence_utterances"]),
        "accepted_needs_review_claims": sum(bool(row["needs_review"]) for row in accepted),
        "source_needs_review_statements": sum(bool(row["needs_review"]) for row in prompt_input["statements"]),
        "rejection_reason_counts": dict(sorted(rejection_reason_counts.items())),
        "referential_integrity": True,
    }
    return accepted, rejected, metrics


def material_paths(
    session: Path,
    decision_path: Path,
    memory_root: Path,
    reviewed_root: Path | None,
) -> tuple[dict[str, Any], dict[str, Path], dict[str, Any], dict[str, Any]]:
    memory_manifest, reasons = speaker_memory.verify_handoff(
        session,
        decision_path,
        memory_root,
        reviewed_root,
    )
    if memory_manifest is None:
        raise LocalSynthesisError("reviewed_speaker_memory_invalid:" + ",".join(reasons))
    paths: dict[str, Path] = {}
    for key in ("memory_json", "handoff_evidence", "meeting", "notes", "transcript", "quality_verdict"):
        path = speaker_memory.artifact_path(memory_manifest, session, key)
        if path is None:
            raise LocalSynthesisError(f"reviewed_speaker_memory_artifact_missing:{key}")
        paths[key] = path
    return memory_manifest, paths, read_json(paths["memory_json"]), read_json(paths["handoff_evidence"])


def render_notes(synthesis: dict[str, Any], prompt_input: dict[str, Any]) -> str:
    utterances = {row["utterance_id"]: row for row in prompt_input["evidence_utterances"]}
    lines = [
        "# Evidence-Guarded Local Notes",
        "",
        f"Session: `{synthesis['session_id']}`  ",
        "Mode: `optional_local_synthesis`  ",
        "Every published item passed deterministic evidence verification.",
        "",
    ]
    for category in CATEGORIES:
        lines.extend([f"## {HEADINGS[category]}", ""])
        rows = [row for row in synthesis["accepted"] if row["category"] == category]
        if not rows:
            lines.append("- None accepted.")
        for row in rows:
            citations = ", ".join(
                f"{utterances[item]['speaker']} [`{item}`]" for item in row["evidence_utterance_ids"]
            )
            review = " `needs_review`" if row["needs_review"] else ""
            lines.append(f"- {row['text']} (evidence: {citations}){review}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_meeting(synthesis: dict[str, Any]) -> str:
    metrics = synthesis["metrics"]
    return "\n".join(
        [
            f"# {synthesis['session_id']}",
            "",
            "- Bundle quality: `evidence_guarded_local_synthesis_v1`",
            "- Source: `reviewed_speaker_memory_v1`",
            f"- Accepted claims: `{metrics['accepted_claims']}`",
            f"- Rejected proposals: `{metrics['rejected_claims']}`",
            f"- Evidence category coverage: `{metrics['category_coverage_ratio']:.3f}`",
            "",
            "Artifacts: [Notes](notes.md) | [Transcript](transcript.md) | "
            "[Quality Verdict](quality_verdict.md) | [Synthesis JSON](local_synthesis.json) | "
            "[Model Run](model_run.json)",
            "",
        ]
    )


def build_material(
    session: Path,
    decision_path: Path,
    memory_root: Path,
    policy_file: Path,
    *,
    reviewed_root: Path | None = None,
    allow_unpromoted: bool = False,
    keep_alive: str | None = None,
    model_client: Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    policy = validate_policy(policy_file, allow_unpromoted=allow_unpromoted)
    memory_manifest, paths, memory_payload, evidence = material_paths(
        session, decision_path, memory_root, reviewed_root
    )
    prompt_input = build_prompt_input(memory_payload, evidence)
    prompt_chunks = split_prompt_inputs(policy, prompt_input)
    parsed = {category: [] for category in CATEGORIES}
    deterministic_runs: list[dict[str, Any]] = []
    performances: list[dict[str, Any]] = []
    prompt_file: Path | None = None
    runtime_identity: dict[str, Any] | None = None
    for index, (chunk_input, rendered_prompt, chunk_prompt_file) in enumerate(prompt_chunks):
        chunk_keep_alive = keep_alive
        if chunk_keep_alive is None and index + 1 < len(prompt_chunks):
            chunk_keep_alive = "5m"
        deterministic_run, chunk_performance = run_model(
            rendered_prompt,
            policy,
            keep_alive=chunk_keep_alive,
            client=model_client,
        )
        if runtime_identity is None:
            runtime_identity = deterministic_run["runtime_identity"]
        elif runtime_identity != deterministic_run["runtime_identity"]:
            raise LocalSynthesisError("model_identity_changed_between_chunks")
        response, normalized_missing = parse_model_response(deterministic_run["raw_response"])
        for category in CATEGORIES:
            parsed[category].extend(response[category])
        deterministic_runs.append(
            {
                "chunk_index": index,
                "statement_ids": [row["statement_id"] for row in chunk_input["statements"]],
                "categories": sorted({row["category"] for row in chunk_input["statements"]}),
                "rendered_prompt_bytes": len(rendered_prompt.encode("utf-8")),
                "rendered_prompt_sha256": sha256_bytes(rendered_prompt.encode("utf-8")),
                "normalized_missing_categories": normalized_missing,
                **deterministic_run,
            }
        )
        performances.append(chunk_performance)
        prompt_file = chunk_prompt_file
    if runtime_identity is None or prompt_file is None:
        raise LocalSynthesisError("model_run_missing")
    accepted, rejected, metrics = verify_proposals(parsed, prompt_input, policy)
    performance = {
        "wall_sec": round(sum(float(row.get("wall_sec") or 0) for row in performances), 6),
        "load_sec": round(sum(float(row.get("load_sec") or 0) for row in performances), 6),
        "prompt_eval_sec": round(sum(float(row.get("prompt_eval_sec") or 0) for row in performances), 6),
        "eval_sec": round(sum(float(row.get("eval_sec") or 0) for row in performances), 6),
        "peak_model_rss_mb": max(
            (float(row.get("peak_model_rss_mb") or 0) for row in performances), default=0.0
        ),
        "sampled_model_pids": sorted(
            {pid for row in performances for pid in row.get("sampled_model_pids") or []}
        ),
        "nice_20_applied": bool(performances) and all(
            bool(row.get("nice_20_applied")) for row in performances
        ),
        "chunk_count": len(prompt_chunks),
    }
    synthesis = {
        "schema": SYNTHESIS_SCHEMA,
        "version": 1,
        "status": "optional_evidence_verified",
        "session_id": session.name,
        "source_speaker_memory_fingerprint": memory_manifest.get("semantic_fingerprint"),
        "accepted": accepted,
        "rejected": rejected,
        "metrics": metrics,
        "constraints": {
            "authoritative": False,
            "transcript_rewritten": False,
            "cloud_or_external_writes": False,
            "cross_session_identity": False,
            "unsupported_claims_published": False,
        },
    }
    model_run = {
        "schema": MODEL_RUN_SCHEMA,
        "version": 1,
        "session_id": session.name,
        "model": runtime_identity,
        "request": deterministic_runs[0]["request"],
        "prompt": {
            "template": repository_identity(prompt_file),
            "chunk_count": len(prompt_chunks),
            "rendered_bytes": sum(row["rendered_prompt_bytes"] for row in deterministic_runs),
            "rendered_sha256": sha256_bytes(
                compact_bytes([row["rendered_prompt_sha256"] for row in deterministic_runs])
            ),
            "input_sha256": sha256_bytes(compact_bytes(prompt_input)),
        },
        "raw_response_sha256": sha256_bytes(
            compact_bytes([row["raw_response_sha256"] for row in deterministic_runs])
        ),
        "prompt_eval_count": sum(row["prompt_eval_count"] for row in deterministic_runs),
        "eval_count": sum(row["eval_count"] for row in deterministic_runs),
        "runs": [
            {
                key: value
                for key, value in row.items()
                if key not in {"runtime_identity", "request"}
            }
            for row in deterministic_runs
        ],
    }
    outputs = {
        "synthesis_json": canonical_bytes(synthesis),
        "model_run_json": canonical_bytes(model_run),
        "meeting": render_meeting(synthesis).encode("utf-8"),
        "notes": render_notes(synthesis, prompt_input).encode("utf-8"),
        "transcript": paths["transcript"].read_bytes(),
        "quality_verdict": paths["quality_verdict"].read_bytes(),
    }
    inputs = {
        "policy": repository_identity(policy_file),
        "prompt": repository_identity(prompt_file),
        "materializer": repository_identity(Path(__file__).resolve()),
        "reviewed_speaker_memory_manifest": identity(memory_root / "handoff_manifest.json", session),
        "speaker_aware_memory": identity(paths["memory_json"], session),
        "handoff_evidence": identity(paths["handoff_evidence"], session),
        "source_notes": identity(paths["notes"], session),
        "source_transcript": identity(paths["transcript"], session),
        "source_quality_verdict": identity(paths["quality_verdict"], session),
    }
    baseline = dict(inputs)
    for key, row in ((memory_manifest.get("safety") or {}).get("baseline_identities") or {}).items():
        if not identity_matches(row, session):
            raise LocalSynthesisError(f"ordinary_baseline_stale:{key}")
        baseline[f"ordinary_{key}"] = row
    return {
        "policy": policy,
        "memory_manifest": memory_manifest,
        "prompt_input": prompt_input,
        "inputs": inputs,
        "baseline": baseline,
        "outputs": outputs,
        "performance": performance,
        "model_identity": runtime_identity,
        "summary": metrics,
    }


def semantic_basis(session: Path, material: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "source_speaker_memory_fingerprint": material["memory_manifest"].get("semantic_fingerprint"),
        "model_identity": material["model_identity"],
        "inputs": material["inputs"],
        "outputs": {
            key: {
                "filename": OUTPUT_FILENAMES[key],
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            }
            for key, payload in sorted(material["outputs"].items())
        },
        "summary": material["summary"],
        "scope": "optional_local_evidence_guarded_meeting_memory",
    }


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_durable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    write_durable(temporary, payload)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def output_identity(path: str, payload: bytes) -> dict[str, Any]:
    return {"scope": "session", "path": path, "bytes": len(payload), "sha256": sha256_bytes(payload)}


def immutable_bundle_valid(bundle: Path, expected: dict[str, bytes]) -> bool:
    return all((bundle / name).is_file() and (bundle / name).read_bytes() == payload for name, payload in expected.items())


def report_payload(manifest: dict[str, Any], performance: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "session_id": manifest.get("session_id"),
        "state": manifest.get("state"),
        "semantic_fingerprint": manifest.get("semantic_fingerprint"),
        "summary": manifest.get("summary") or {},
        "performance": performance or {},
        "reasons": manifest.get("reasons") or [],
        "privacy": {
            "local_loopback_only": True,
            "display_labels_in_report": False,
            "external_writes": False,
        },
    }


def report_markdown(manifest: dict[str, Any], performance: dict[str, Any] | None = None) -> str:
    summary = manifest.get("summary") or {}
    performance = performance or {}
    lines = [
        "# Evidence-Guarded Local Synthesis v1",
        "",
        f"- State: `{manifest.get('state')}`",
        f"- Accepted claims: `{summary.get('accepted_claims', 0)}`",
        f"- Rejected proposals: `{summary.get('rejected_claims', 0)}`",
        f"- Category coverage: `{summary.get('category_coverage_ratio', 0)}`",
        f"- Wall time: `{performance.get('wall_sec', 0)}s`",
        f"- Peak model RSS: `{performance.get('peak_model_rss_mb', 0)} MB`",
        "",
        "Participant display labels are intentionally omitted from this report.",
    ]
    lines.extend(f"- Reason: `{reason}`" for reason in manifest.get("reasons") or [])
    return "\n".join(lines) + "\n"


def build_handoff(
    session: Path,
    decision_path: Path,
    memory_root: Path,
    root: Path,
    policy_file: Path,
    *,
    reviewed_root: Path | None = None,
    allow_unpromoted: bool = False,
    keep_alive: str | None = None,
    model_client: Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] | None = None,
    simulate_interruption_before_publish: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    material = build_material(
        session,
        decision_path,
        memory_root,
        policy_file,
        reviewed_root=reviewed_root,
        allow_unpromoted=allow_unpromoted,
        keep_alive=keep_alive,
        model_client=model_client,
    )
    basis = semantic_basis(session, material)
    fingerprint = sha256_bytes(compact_bytes(basis))
    bundle_relative = str(root.resolve().relative_to(session.resolve()) / "bundles" / fingerprint)
    bundle = session / bundle_relative
    files = {
        key: output_identity(f"{bundle_relative}/{OUTPUT_FILENAMES[key]}", payload)
        for key, payload in sorted(material["outputs"].items())
    }
    if not all(identity_matches(row, session) for row in material["baseline"].values()):
        raise LocalSynthesisError("source_output_changed_before_publication")
    promoted = material["policy"].get("decision") == "PROMOTE_OPTIONAL_LOCAL_SYNTHESIS"
    manifest = {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "state": "ready",
        "semantic_fingerprint": fingerprint,
        "fingerprint_basis": basis,
        "inputs": material["inputs"],
        "model_identity": material["model_identity"],
        "bundle": {"path": bundle_relative, "files": files},
        "summary": material["summary"],
        "gates": {
            "publish_optional_local_synthesis": promoted,
            "qualification_only": not promoted,
            "speaker_memory_current": True,
            "model_identity_current": True,
            "referential_integrity": True,
            "published_unsupported_claims": 0,
            "ordinary_outputs_unchanged": True,
        },
        "safety": {
            "baseline_identities": material["baseline"],
            "default_outputs_unchanged": True,
            "fallback": "ordinary_evidence_handoff_v2",
            "transcript_authoritative": False,
            "cloud_or_external_writes": False,
            "cross_session_identity": False,
        },
        "reasons": [],
        "recommended_next": (
            f'murmurmark notes "sessions/{session.name}" --local-synthesis' if promoted else None
        ),
    }
    expected = {OUTPUT_FILENAMES[key]: payload for key, payload in material["outputs"].items()}
    expected["handoff_manifest.json"] = canonical_bytes(manifest)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging.", dir=root))
    try:
        for name, payload in expected.items():
            write_durable(staging / name, payload)
        fsync_directory(staging)
        bundles = root / "bundles"
        bundles.mkdir(parents=True, exist_ok=True)
        if bundle.exists():
            if not immutable_bundle_valid(bundle, expected):
                raise LocalSynthesisError("existing_immutable_bundle_invalid")
            shutil.rmtree(staging)
        else:
            os.replace(staging, bundle)
            fsync_directory(bundles)
        if not all(identity_matches(row, session) for row in material["baseline"].values()):
            raise LocalSynthesisError("source_output_changed_during_publication")
        if simulate_interruption_before_publish:
            raise SimulatedInterruption("simulated interruption before local synthesis publish")
        atomic_write(root / "handoff_manifest.json", canonical_bytes(manifest))
        atomic_write(root / "report.json", canonical_bytes(report_payload(manifest, material["performance"])))
        atomic_write(root / "report.md", report_markdown(manifest, material["performance"]).encode("utf-8"))
        return manifest, material["performance"]
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def unavailable_manifest(session: Path, reason: str) -> dict[str, Any]:
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "state": "unavailable",
        "semantic_fingerprint": None,
        "bundle": None,
        "summary": {},
        "gates": {"publish_optional_local_synthesis": False},
        "safety": {"default_outputs_unchanged": True, "fallback": "ordinary_evidence_handoff_v2"},
        "reasons": [reason],
    }


def publish_unavailable_attempt(root: Path, manifest: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root / "last_attempt.json", canonical_bytes(report_payload(manifest)))
    atomic_write(root / "report.json", canonical_bytes(report_payload(manifest)))
    atomic_write(root / "report.md", report_markdown(manifest).encode("utf-8"))
    if not (root / "handoff_manifest.json").exists():
        atomic_write(root / "handoff_manifest.json", canonical_bytes(manifest))


def verify_handoff(
    session: Path,
    decision_path: Path,
    memory_root: Path,
    root: Path,
    policy_file: Path,
    reviewed_root: Path | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        policy = validate_policy(policy_file, allow_unpromoted=False)
        runtime_identity = inspect_runtime(policy)
        current = read_json(root / "handoff_manifest.json")
    except LocalSynthesisError as error:
        return None, [str(error)]
    if current.get("schema") != HANDOFF_SCHEMA or current.get("state") != "ready":
        return None, [str(item) for item in current.get("reasons") or ["local_synthesis_unavailable"]]
    memory_manifest, reasons = speaker_memory.verify_handoff(
        session, decision_path, memory_root, reviewed_root
    )
    if memory_manifest is None:
        return None, ["reviewed_speaker_memory_invalid:" + ",".join(reasons)]
    if current.get("generator") != implementation():
        return None, ["generator_fingerprint_mismatch"]
    if current.get("model_identity") != runtime_identity:
        return None, ["model_identity_mismatch"]
    if current.get("fingerprint_basis", {}).get("source_speaker_memory_fingerprint") != memory_manifest.get(
        "semantic_fingerprint"
    ):
        return None, ["speaker_memory_fingerprint_mismatch"]
    inputs = current.get("inputs") if isinstance(current.get("inputs"), dict) else {}
    if not inputs or not all(identity_matches(row, session) for row in inputs.values()):
        return None, ["input_fingerprint_mismatch"]
    basis = current.get("fingerprint_basis")
    fingerprint = sha256_bytes(compact_bytes(basis)) if isinstance(basis, dict) else ""
    if current.get("semantic_fingerprint") != fingerprint:
        return None, ["semantic_fingerprint_mismatch"]
    bundle = current.get("bundle") if isinstance(current.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    if set(files) != set(OUTPUT_FILENAMES) or not all(identity_matches(row, session) for row in files.values()):
        return None, ["bundle_file_identity_mismatch"]
    try:
        bundle_path = resolve_inside(session, str(bundle.get("path") or ""))
    except LocalSynthesisError:
        return None, ["bundle_path_invalid"]
    if bundle_path.name != fingerprint:
        return None, ["bundle_path_invalid"]
    bundle_manifest = bundle_path / "handoff_manifest.json"
    if not bundle_manifest.is_file() or bundle_manifest.read_bytes() != canonical_bytes(current):
        return None, ["bundle_manifest_mismatch"]
    synthesis_path = resolve_identity(files.get("synthesis_json"), session)
    if synthesis_path is None:
        return None, ["synthesis_artifact_missing"]
    synthesis = read_json(synthesis_path)
    if synthesis.get("schema") != SYNTHESIS_SCHEMA:
        return None, ["synthesis_schema_mismatch"]
    if any(not row.get("evidence_verified") for row in synthesis.get("accepted") or []):
        return None, ["published_claim_without_verification"]
    if (synthesis.get("metrics") or {}).get("published_unsupported_claims") != 0:
        return None, ["published_unsupported_claims"]
    baseline = (current.get("safety") or {}).get("baseline_identities")
    if not isinstance(baseline, dict) or not all(identity_matches(row, session) for row in baseline.values()):
        return None, ["ordinary_output_fingerprint_mismatch"]
    return current, []


def artifact_path(manifest: dict[str, Any], session: Path, key: str) -> Path | None:
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    path = resolve_identity(files.get(key), session)
    return path if path is not None and path.is_file() else None


def print_summary(manifest: dict[str, Any], root: Path, performance: dict[str, Any] | None = None) -> None:
    summary = manifest.get("summary") or {}
    print("evidence_guarded_local_synthesis:")
    print(f"  state: {manifest.get('state')}")
    print(f"  accepted_claims: {summary.get('accepted_claims', 0)}")
    print(f"  rejected_claims: {summary.get('rejected_claims', 0)}")
    print(f"  category_coverage_ratio: {summary.get('category_coverage_ratio', 0)}")
    if performance:
        print(f"  wall_sec: {performance.get('wall_sec', 0)}")
        print(f"  peak_model_rss_mb: {performance.get('peak_model_rss_mb', 0)}")
    if manifest.get("reasons"):
        print(f"  fallback_reason: {manifest['reasons'][0]}")
    print(f"  manifest: {root / 'handoff_manifest.json'}")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    if not (session / "session.json").is_file():
        print(f"error: session.json not found under {session}", file=sys.stderr)
        return 2
    try:
        decision_path = resolve_inside(session, args.decisions or speaker_memory.naming.DEFAULT_DECISIONS)
        memory_root = resolve_inside(session, args.speaker_memory_dir or speaker_memory.DEFAULT_OUTPUT)
        reviewed_root = (
            resolve_inside(session, args.reviewed_speaker_dir) if args.reviewed_speaker_dir else None
        )
        root = resolve_inside(session, args.out_dir or DEFAULT_OUTPUT)
        policy_file = policy_path(args.policy)
    except LocalSynthesisError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.verify_only:
        manifest, reasons = verify_handoff(
            session, decision_path, memory_root, root, policy_file, reviewed_root
        )
        if manifest is None:
            for reason in reasons:
                print(reason)
            return 2
        if args.print_path:
            path = artifact_path(manifest, session, args.print_path)
            if path is None:
                print(f"artifact_missing:{args.print_path}")
                return 2
            print(path)
        else:
            print_summary(manifest, root)
        return 0
    try:
        manifest, performance = build_handoff(
            session,
            decision_path,
            memory_root,
            root,
            policy_file,
            reviewed_root=reviewed_root,
            allow_unpromoted=args.qualification_run,
            keep_alive=args.keep_alive,
            simulate_interruption_before_publish=args.simulate_interruption_before_publish,
        )
    except SimulatedInterruption as error:
        print(str(error))
        return 3
    except LocalSynthesisError as error:
        manifest = unavailable_manifest(session, str(error))
        publish_unavailable_attempt(root, manifest)
        print_summary(manifest, root)
        return 2
    print_summary(manifest, root, performance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
