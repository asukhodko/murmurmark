#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

from live_recovery_incremental_cache import (
    FileDigestMemo,
    chunk_plan,
    content_hash,
    object_directory,
)


def load_module(filename: str, name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def complete_object(root: Path, digest: str) -> Path:
    path = object_directory(root, digest) / "object.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"status": "complete", "input_sha256": digest}) + "\n",
        encoding="utf-8",
    )
    return path


def check_hash_and_digest_memo(root: Path) -> None:
    assert content_hash({"b": 2, "a": 1}) == content_hash({"a": 1, "b": 2})
    session = root / "session"
    session.mkdir()
    audio = session / "chunk.wav"
    audio.write_bytes(b"committed-pcm-v1")
    memo_root = root / "memo"

    cold = FileDigestMemo(memo_root, session=session)
    first = cold.fingerprint(audio)
    assert cold.telemetry()["digest_cache_misses"] == 1
    cold.save()

    warm = FileDigestMemo(memo_root, session=session)
    second = warm.fingerprint(audio)
    assert first["sha256"] == second["sha256"]
    assert warm.telemetry()["digest_cache_hits"] == 1

    audio.write_bytes(b"committed-pcm-v2-with-change")
    changed = warm.fingerprint(audio)
    assert changed["sha256"] != first["sha256"]
    assert warm.telemetry()["digest_cache_misses"] == 1


def check_chunk_plans(root: Path) -> None:
    cache = root / "objects"
    paths = {index: complete_object(cache, f"input-{index}") for index in (1, 2)}
    previous = {
        "context_sha256": "context-a",
        "cache_root": str(cache),
        "entries": {
            str(index): {
                "input_sha256": f"input-{index}",
                "input_components": {"audio": f"audio-{index}", "previous_chain": f"chain-{index}"},
                "object_path": str(paths[index]),
            }
            for index in (1, 2)
        },
    }
    components = {
        index: {"audio": f"audio-{index}", "previous_chain": f"chain-{index}"}
        for index in (1, 2, 3)
    }

    warm = chunk_plan(
        previous_state=previous,
        context_sha256="context-a",
        current_inputs={1: "input-1", 2: "input-2"},
        current_components=components,
    )
    assert warm["reused_indexes"] == [1, 2] and warm["process_indexes"] == [], warm

    appended = chunk_plan(
        previous_state=previous,
        context_sha256="context-a",
        current_inputs={1: "input-1", 2: "input-2", 3: "input-3"},
        current_components=components,
    )
    assert appended["reused_indexes"] == [1, 2], appended
    assert appended["process_indexes"] == [3], appended
    assert appended["invalidation_reason"] == "new_closed_evidence", appended

    changed_components = dict(components)
    changed_components[1] = {"audio": "audio-mutated", "previous_chain": "chain-1"}
    changed = chunk_plan(
        previous_state=previous,
        context_sha256="context-a",
        current_inputs={1: "changed-1", 2: "changed-2"},
        current_components=changed_components,
    )
    assert changed["process_indexes"] == [1, 2], changed
    assert changed["changed_components"] == ["audio"], changed

    context = chunk_plan(
        previous_state=previous,
        context_sha256="context-b",
        current_inputs={1: "input-1", 2: "input-2"},
        current_components=components,
    )
    assert context["process_indexes"] == [1, 2], context
    assert context["invalidation_reason"] == "stage_context_changed", context

    paths[2].unlink()
    corrupt = chunk_plan(
        previous_state=previous,
        context_sha256="context-a",
        current_inputs={1: "input-1", 2: "input-2"},
        current_components=components,
    )
    assert corrupt["reused_indexes"] == [1], corrupt
    assert corrupt["invalidation_reason"] == "cache_object_missing", corrupt

    recovered_digest = "interrupted-input"
    recovered_path = complete_object(cache, recovered_digest)
    interrupted = chunk_plan(
        previous_state={"context_sha256": "context-a", "entries": {}},
        context_sha256="context-a",
        current_inputs={1: recovered_digest},
        current_components={1: {"audio": "stable"}},
    )
    assert interrupted["process_indexes"] == [1], interrupted
    assert recovered_path.exists()
    assert object_directory(cache, recovered_digest) == recovered_path.parent


def check_bounded_candidate_invalidation(remote: ModuleType) -> None:
    args = SimpleNamespace(
        language="ru",
        max_asr_groups=24,
        skip_asr=False,
        runtime_shadow=True,
    )
    algorithm = {"sha256": "algorithm-a", "files": []}
    whisper = {"sha256": "whisper-a", "status": "present"}
    prep_a, candidate_a = remote.incremental_contexts(
        args=args,
        algorithm=algorithm,
        model={"sha256": "model-a", "status": "present"},
        whisper_cli=whisper,
    )
    prep_b, candidate_b = remote.incremental_contexts(
        args=args,
        algorithm=algorithm,
        model={"sha256": "model-b", "status": "present"},
        whisper_cli=whisper,
    )
    assert prep_a["sha256"] == prep_b["sha256"]
    assert candidate_a["sha256"] != candidate_b["sha256"]

    row = {"id": "candidate-1", "chunk_index": 1, "prepared_input_sha256": "dsp-object"}
    key_a = remote.candidate_outcome_key(
        row=row,
        asr_allowed=True,
        existing_me=[],
        accepted_so_far=[],
        max_asr_groups=24,
        candidate_context_sha256=candidate_a["sha256"],
    )
    key_b = remote.candidate_outcome_key(
        row=row,
        asr_allowed=True,
        existing_me=[],
        accepted_so_far=[],
        max_asr_groups=24,
        candidate_context_sha256=candidate_b["sha256"],
    )
    assert key_a != key_b


def check_local_stage_resume_and_invalidation(root: Path, local: ModuleType) -> None:
    session = root / "incremental-session"
    session.mkdir()
    model = root / "model.bin"
    whisper = root / "whisper-cli"
    model.write_bytes(b"model-v1")
    whisper.write_bytes(b"cli-v1")
    cache = session / "derived/live/runtime/incremental-cache-v1/local-island-v2"
    chunk_paths: dict[int, Path] = {}
    chunks: dict[int, dict[str, object]] = {}
    evaluations: list[dict[str, object]] = []

    def add_chunk(index: int) -> None:
        chunk_path = session / f"chunk-{index:06d}.json"
        chunk_path.write_text(json.dumps({"index": index, "revision": 1}) + "\n", encoding="utf-8")
        chunk_paths[index] = chunk_path
        chunks[index] = {"index": index}
        evaluations.append({"chunk_index": index, "text": f"local candidate {index}"})

    add_chunk(1)

    def fake_select(**kwargs):
        rows = list(kwargs.get("evaluations") or [])
        selected = [dict(row) for row in rows]
        decisions = [
            {**dict(row), "status": "selected", "id": f"selection-{row['chunk_index']}"}
            for row in rows
        ]
        return selected, decisions

    class FakeMaterializer:
        def __init__(self, **_kwargs):
            self.output = root
            self.accepted = []

        def materialize(self, group, _index):
            chunk_index = int(group[0]["chunk_index"])
            return {
                "id": f"local-candidate-{chunk_index}",
                "chunk_index": chunk_index,
                "status": "accepted",
                "text": f"local candidate {chunk_index}",
            }

    local.select_strict_evaluations = fake_select
    local.group_selected_evaluations = lambda rows: [[row] for row in rows]
    local.MicroASRMaterializer = FakeMaterializer
    args = SimpleNamespace(
        incremental_cache_dir=cache,
        force=False,
        model=str(model),
        language="ru",
        whisper_cli=str(whisper),
        max_groups=120,
        runtime_shadow=True,
    )
    progressive = SimpleNamespace(REMOTE_AUDIO_QUIET_MAX_DB=-65.0)

    def run():
        return local.run_incremental(
            args=args,
            session=session,
            progressive=progressive,
            evaluations=evaluations,
            chunks=chunks,
            chunk_paths=chunk_paths,
            segment_rows=[],
            segments_by_key={},
            existing_me=[],
        )

    cold_candidates, _cold_decisions, cold = run()
    warm_candidates, _warm_decisions, warm = run()
    assert cold["new_chunk_count"] == 1 and cold["candidate_cache_misses"] == 1, cold
    assert warm["new_chunk_count"] == 0 and warm["candidate_cache_hits"] == 1, warm
    assert cold_candidates == warm_candidates

    add_chunk(2)
    appended_candidates, _appended_decisions, appended = run()
    assert appended["new_chunk_count"] == 1 and appended["reused_chunk_count"] == 1, appended
    assert [row["id"] for row in appended_candidates] == [
        "local-candidate-1",
        "local-candidate-2",
    ]

    state_path = cache / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["entries"].pop("2")
    state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    resumed_candidates, _resumed_decisions, resumed = run()
    assert resumed_candidates == appended_candidates
    assert resumed["new_chunk_count"] == 0, resumed
    assert resumed["recovered_object_count"] == 1, resumed

    state = json.loads(state_path.read_text(encoding="utf-8"))
    second_manifest = Path(state["entries"]["2"]["object_path"])
    if not second_manifest.is_absolute():
        second_manifest = cache / second_manifest
    (second_manifest.parent / "candidates.jsonl").unlink()
    repaired_candidates, _repaired_decisions, repaired = run()
    assert repaired_candidates == appended_candidates
    assert repaired["new_chunk_count"] == 1, repaired
    assert repaired["invalidation_reason"] == "cache_object_corrupt", repaired

    chunk_paths[1].write_text(json.dumps({"index": 1, "revision": 2}) + "\n", encoding="utf-8")
    changed_candidates, _changed_decisions, changed = run()
    assert changed_candidates == appended_candidates
    assert changed["new_chunk_count"] == 2, changed
    assert changed["earliest_invalidated_chunk"] == 1, changed
    assert "chunk_json" in changed["changed_input_components"], changed


def main() -> int:
    remote = load_module(
        "live-causal-remote-active-me-separation.py",
        "murmurmark_incremental_cache_remote",
    )
    local = load_module(
        "live-causal-local-island-micro-asr.py",
        "murmurmark_incremental_cache_local",
    )
    with tempfile.TemporaryDirectory(prefix="murmurmark-live-recovery-cache-") as temporary:
        root = Path(temporary)
        check_hash_and_digest_memo(root)
        check_chunk_plans(root)
        check_local_stage_resume_and_invalidation(root, local)
    check_bounded_candidate_invalidation(remote)
    print("live recovery incremental cache checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
