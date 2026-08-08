#!/usr/bin/env python3
"""Deterministic oracle decomposition for frozen remote-speaker attribution evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_REPO_ROOT = SCRIPT_PATH.parents[1]
DEFAULT_POLICY = Path("policies/remote-speaker-attribution-error-decomposition-v1.json")
DEFAULT_OUT = Path("sessions/_reports/remote-speaker-attribution-error-decomposition-v1")

POLICY_SCHEMA = "murmurmark.remote_speaker_attribution_error_decomposition_policy/v1"
INPUT_SCHEMA = "murmurmark.remote_speaker_attribution_error_decomposition_input/v1"
PUBLIC_INPUT_SCHEMA = "murmurmark.remote_speaker_attribution_error_decomposition_public_input/v1"
WORD_SCHEMA = "murmurmark.remote_speaker_attribution_word_error/v1"
BOUNDARY_SCHEMA = "murmurmark.remote_speaker_attribution_boundary_error/v1"
REPORT_SCHEMA = "murmurmark.remote_speaker_attribution_error_decomposition_report/v1"
REPLAY_SCHEMA = "murmurmark.remote_speaker_attribution_error_decomposition_replay/v1"
VERSION = "0.1.0"

ORACLE_TRACKS = (
    "current",
    "oracle_boundaries_current_identity",
    "current_boundaries_oracle_identity",
    "overlap_open_set_oracle",
    "full_oracle_control",
)
SPECIAL_LABELS = {"unknown_speaker", "mixed"}


class DecompositionError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def compact_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(compact_json(row) for row in rows)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DecompositionError(f"invalid_json:{path}:{error}") from error
    if not isinstance(value, dict):
        raise DecompositionError(f"json_object_required:{path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise DecompositionError(f"jsonl_object_required:{path}:{number}")
            rows.append(row)
    except (OSError, json.JSONDecodeError) as error:
        raise DecompositionError(f"invalid_jsonl:{path}:{error}") from error
    return rows


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, canonical_json(value))


def resolve(repo_root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def portable(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as error:
        raise DecompositionError(f"path_outside_repository:{path}") from error


def fingerprint(path: Path, repo_root: Path, role: str) -> dict[str, Any]:
    if not path.is_file():
        raise DecompositionError(f"required_artifact_missing:{role}:{path}")
    return {
        "role": role,
        "path": portable(path, repo_root),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise DecompositionError("unsupported_policy_schema")
    if policy.get("scope", {}).get("production_candidate_selection") is not False:
        raise DecompositionError("production_candidate_selection_must_be_false")
    if policy.get("scope", {}).get("hard_sets_may_be_reopened") is not False:
        raise DecompositionError("hard_sets_may_remain_closed")
    matrix = tuple(row.get("track_id") for row in policy.get("oracle_matrix", []))
    if matrix != ORACLE_TRACKS:
        raise DecompositionError("oracle_matrix_changed")
    expected = {
        "ADVANCE_DEDICATED_SEGMENTATION",
        "ADVANCE_STRONGER_SPEAKER_IDENTITY",
        "ADVANCE_OVERLAP_OPEN_SET_MODEL",
        "CURRENT_LOCAL_ATTRIBUTION_LIMIT",
    }
    if set(policy.get("decision", {}).get("allowed_outcomes", [])) != expected:
        raise DecompositionError("decision_outcomes_changed")
    weights = policy["decision"]["axis_gain_weights"]
    if round(sum(float(value) for value in weights.values()), 9) != 1.0:
        raise DecompositionError("axis_gain_weights_must_sum_to_one")
    return policy


def corpus_path(repo_root: Path, spec: dict[str, Any], key: str) -> Path:
    root = resolve(repo_root, str(spec["root"]))
    return root / str(spec[key])


def track_specs(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    rows = [("primary", str(spec["primary_track"]["track_id"]), spec["primary_track"])]
    rows.extend(("control", str(row["track_id"]), row) for row in spec.get("control_tracks", []))
    return rows


def scoped_predictions(path: Path, split: str) -> list[dict[str, Any]]:
    return [row for row in read_jsonl(path) if str(row.get("split")) == split]


def collect_corpus_snapshot(
    repo_root: Path, spec: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    corpus_id = str(spec["corpus_id"])
    root = resolve(repo_root, str(spec["root"]))
    split = str(spec["split"])
    frozen_path = corpus_path(repo_root, spec, "frozen_manifest")
    frozen = read_json(frozen_path)
    frozen_artifacts = frozen.get("artifacts")
    if not isinstance(frozen_artifacts, dict):
        raise DecompositionError(f"frozen_artifacts_missing:{corpus_id}")

    word_paths = sorted(root.glob(str(spec["truth_words_glob"])))
    boundary_paths = sorted(root.glob(str(spec["truth_boundaries_glob"])))
    if not word_paths or len(word_paths) != len(boundary_paths):
        raise DecompositionError(f"truth_files_incomplete:{corpus_id}")

    words: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    artifacts.append(fingerprint(frozen_path, repo_root, "frozen_manifest"))

    for path in word_paths:
        rows = read_jsonl(path)
        if any(str(row.get("split")) != split for row in rows):
            raise DecompositionError(f"truth_split_mismatch:{corpus_id}:{path.name}")
        words.extend(rows)
        artifacts.append(fingerprint(path, repo_root, f"truth_words:{path.parent.name}"))
    for path in boundary_paths:
        rows = read_jsonl(path)
        if any(str(row.get("split")) != split for row in rows):
            raise DecompositionError(f"boundary_split_mismatch:{corpus_id}:{path.name}")
        boundaries.extend(rows)
        artifacts.append(fingerprint(path, repo_root, f"truth_boundaries:{path.parent.name}"))

    for path in word_paths + boundary_paths:
        try:
            relative = path.relative_to(frozen_path.parent).as_posix()
        except ValueError as error:
            raise DecompositionError(f"truth_outside_frozen_root:{corpus_id}:{path}") from error
        expected_hash = frozen_artifacts.get(relative)
        if expected_hash != sha256(path):
            raise DecompositionError(f"truth_not_covered_by_frozen_manifest:{corpus_id}:{relative}")

    word_ids = [str(row.get("word_id")) for row in words]
    if len(word_ids) != len(set(word_ids)):
        raise DecompositionError(f"duplicate_truth_word_id:{corpus_id}")
    if any(row.get("truth_source") != "exact_scripted" for row in words):
        raise DecompositionError(f"non_exact_truth:{corpus_id}")

    track_rows = []
    for role, track_id, track in track_specs(spec):
        path = root / str(track["predictions"])
        predictions = scoped_predictions(path, split)
        prediction_ids = [str(row.get("word_id")) for row in predictions]
        if len(prediction_ids) != len(set(prediction_ids)) or set(prediction_ids) != set(word_ids):
            raise DecompositionError(f"prediction_word_set_mismatch:{corpus_id}:{track_id}")
        if track.get("segment_key") == "explicit_segment_index" and any(
            row.get("segment_index") is None for row in predictions
        ):
            raise DecompositionError(f"explicit_segment_index_missing:{corpus_id}:{track_id}")
        artifact_role = f"{role}_predictions:{track_id}"
        artifact = fingerprint(path, repo_root, artifact_role)
        artifacts.append(artifact)
        track_rows.append(
            {
                "role": role,
                "track_id": track_id,
                "segment_key": str(track["segment_key"]),
                "prediction_count": len(predictions),
                "artifact_sha256": artifact["sha256"],
            }
        )

    fixed_roles = (
        ("decision_report", "decision_report", False),
        ("tracked_manifest", "tracked_manifest", True),
        ("replay_report", "upstream_replay_report", False),
        ("opening_ledger", "opening_ledger", False),
        ("candidate_freeze", "candidate_freeze", False),
        ("private_spec", "private_spec", False),
    )
    loaded: dict[str, dict[str, Any]] = {}
    for key, role, repository_relative in fixed_roles:
        value = spec.get(key)
        if value is None:
            continue
        path = resolve(repo_root, str(value)) if repository_relative else root / str(value)
        artifacts.append(fingerprint(path, repo_root, role))
        if path.suffix == ".json":
            loaded[key] = read_json(path)

    report = loaded["decision_report"]
    if report.get("decision") != spec.get("expected_decision"):
        raise DecompositionError(f"upstream_decision_changed:{corpus_id}")
    if "opening_ledger" in loaded:
        ledger = loaded["opening_ledger"]
        if ledger.get("status") != "completed" or ledger.get("decision_open_count") != 1:
            raise DecompositionError(f"opening_ledger_not_completed_once:{corpus_id}")
        if ledger.get("decision") != spec.get("expected_decision"):
            raise DecompositionError(f"opening_ledger_decision_changed:{corpus_id}")

    evaluated_boundaries = [row for row in boundaries if row.get("evaluation") is True]
    scenario_ids = sorted({str(row["scenario_id"]) for row in words})
    summary = {
        "corpus_id": corpus_id,
        "split": split,
        "corpus_sha256": str(frozen.get("corpus_sha256")),
        "scenario_count": len(scenario_ids),
        "word_count": len(words),
        "known_word_count": sum(row.get("truth_class") == "known_speaker" for row in words),
        "open_set_word_count": sum(row.get("truth_class") == "open_set_speaker" for row in words),
        "mixed_word_count": sum(row.get("truth_class") == "mixed" for row in words),
        "evaluated_boundary_count": len(evaluated_boundaries),
        "upstream_decision": str(report["decision"]),
        "decision_open_count": loaded.get("opening_ledger", {}).get("decision_open_count"),
        "tracks": track_rows,
        "artifacts": sorted(artifacts, key=lambda row: (row["role"], row["path"])),
    }
    raw = {"words": words, "boundaries": boundaries}
    return summary, raw


def build_input_manifest(repo_root: Path, policy_path: Path, policy: dict[str, Any]) -> dict[str, Any]:
    corpus_rows = []
    for spec in policy["corpora"]:
        summary, _ = collect_corpus_snapshot(repo_root, spec)
        corpus_rows.append(summary)
    guards = [fingerprint(resolve(repo_root, path), repo_root, f"production_guard:{index}")
              for index, path in enumerate(policy["production_guards"])]
    value: dict[str, Any] = {
        "schema": INPUT_SCHEMA,
        "version": VERSION,
        "policy": fingerprint(policy_path, repo_root, "policy"),
        "corpora": corpus_rows,
        "production_guards": guards,
        "hard_sets_reopened": False,
    }
    value["freeze_sha256"] = sha256_bytes(canonical_json(value))
    return value

def public_input_manifest(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": PUBLIC_INPUT_SCHEMA,
        "version": VERSION,
        "freeze_sha256": value["freeze_sha256"],
        "policy_sha256": value["policy"]["sha256"],
        "hard_sets_reopened": False,
        "corpora": [
            {
                key: row[key]
                for key in (
                    "corpus_id",
                    "split",
                    "corpus_sha256",
                    "scenario_count",
                    "word_count",
                    "known_word_count",
                    "open_set_word_count",
                    "mixed_word_count",
                    "evaluated_boundary_count",
                    "upstream_decision",
                    "decision_open_count",
                    "tracks",
                )
            }
            | {
                "artifact_hashes": [
                    {"role": item["role"], "bytes": item["bytes"], "sha256": item["sha256"]}
                    for item in row["artifacts"]
                ]
            }
            for row in value["corpora"]
        ],
        "production_guard_hashes": [
            {"role": row["role"], "bytes": row["bytes"], "sha256": row["sha256"]}
            for row in value["production_guards"]
        ],
    }


def freeze(args: argparse.Namespace, policy: dict[str, Any], policy_path: Path) -> dict[str, Any]:
    value = build_input_manifest(args.repo_root, policy_path, policy)
    private_path = args.out_dir / "private/input_manifest.json"
    public_path = args.out_dir / "input_manifest.public.json"
    if private_path.exists():
        existing = read_json(private_path)
        if canonical_json(existing) != canonical_json(value):
            raise DecompositionError("frozen_input_changed")
    else:
        write_json(private_path, value)
    public = public_input_manifest(value)
    if public_path.exists() and canonical_json(read_json(public_path)) != canonical_json(public):
        raise DecompositionError("public_input_freeze_changed")
    write_json(public_path, public)
    print(f"freeze_sha256: {value['freeze_sha256']}")
    print(f"corpora: {len(value['corpora'])}")
    print("status: INPUTS_FROZEN")
    return value


def verified_input(args: argparse.Namespace, policy: dict[str, Any], policy_path: Path) -> dict[str, Any]:
    path = args.out_dir / "private/input_manifest.json"
    if not path.is_file():
        raise DecompositionError("input_manifest_missing_run_freeze")
    frozen = read_json(path)
    current = build_input_manifest(args.repo_root, policy_path, policy)
    if canonical_json(frozen) != canonical_json(current):
        raise DecompositionError("frozen_input_changed")
    return frozen


def normalized_truth_label(word: dict[str, Any]) -> str:
    truth_class = str(word["truth_class"])
    if truth_class == "known_speaker":
        return str(word["speaker_id"])
    if truth_class == "open_set_speaker":
        return "unknown_speaker"
    if truth_class == "mixed":
        return "mixed"
    raise DecompositionError(f"unsupported_truth_class:{truth_class}")


def plurality(values: Iterable[str]) -> str:
    counts = Counter(values)
    if not counts:
        return "unknown_speaker"
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def gap_bin(value: float | None, thresholds: list[float]) -> str:
    if value is None:
        return "scenario_start"
    if value < thresholds[0]:
        return "overlap"
    if value < thresholds[1]:
        return "tight"
    if value < thresholds[2]:
        return "short"
    return "long"


def duration_bin(value: float, thresholds: list[float]) -> str:
    if value < thresholds[0]:
        return "short"
    if value < thresholds[1]:
        return "medium"
    return "long"


def load_corpus(
    repo_root: Path,
    policy: dict[str, Any],
    spec: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    corpus_id = str(spec["corpus_id"])
    root = resolve(repo_root, str(spec["root"]))
    words: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []
    for path in sorted(root.glob(str(spec["truth_words_glob"]))):
        source = portable(path, repo_root)
        for source_row in read_jsonl(path):
            row = dict(source_row)
            row["corpus_id"] = corpus_id
            row["uid"] = f"{corpus_id}:{row['word_id']}"
            row["truth_path"] = source
            words.append(row)
    for path in sorted(root.glob(str(spec["truth_boundaries_glob"]))):
        source = portable(path, repo_root)
        for source_row in read_jsonl(path):
            if source_row.get("evaluation") is not True:
                continue
            row = dict(source_row)
            row["corpus_id"] = corpus_id
            row["uid"] = f"{corpus_id}:{row['boundary_id']}"
            row["left_uid"] = f"{corpus_id}:{row['left_word_id']}"
            row["right_uid"] = f"{corpus_id}:{row['right_word_id']}"
            row["truth_path"] = source
            boundaries.append(row)
    words.sort(key=lambda row: (str(row["scenario_id"]), float(row["start"]), float(row["end"]), str(row["word_id"])))
    boundaries.sort(key=lambda row: (str(row["scenario_id"]), float(row["time"]), str(row["boundary_id"])))

    previous: dict[str, dict[str, Any]] = {}
    duration_thresholds = [float(value) for value in policy["strata"]["word_duration_sec"]]
    gap_thresholds = [float(value) for value in policy["strata"]["preceding_gap_sec"]]
    for word in words:
        scenario_id = str(word["scenario_id"])
        prior = previous.get(scenario_id)
        gap = None if prior is None else float(word["start"]) - float(prior["end"])
        duration = max(0.0, float(word["end"]) - float(word["start"]))
        word["duration_sec"] = round(duration, 6)
        word["duration_bin"] = duration_bin(duration, duration_thresholds)
        word["preceding_gap_sec"] = None if gap is None else round(gap, 6)
        word["preceding_gap_bin"] = gap_bin(gap, gap_thresholds)
        if word["truth_class"] == "mixed" or word.get("overlap_word_ids"):
            word["overlap_state"] = "mixed_or_overlap"
        elif word["truth_class"] == "open_set_speaker":
            word["overlap_state"] = "open_set"
        else:
            word["overlap_state"] = "clean_known"
        previous[scenario_id] = word

    tracks = []
    split = str(spec["split"])
    artifacts_by_role = {row["role"]: row for row in snapshot["artifacts"]}
    for role, track_id, track_spec in track_specs(spec):
        path = root / str(track_spec["predictions"])
        predictions = scoped_predictions(path, split)
        by_uid = {f"{corpus_id}:{row['word_id']}": row for row in predictions}
        artifact = artifacts_by_role[f"{role}_predictions:{track_id}"]
        tracks.append(
            {
                "role": role,
                "track_id": track_id,
                "segment_key": str(track_spec["segment_key"]),
                "predictions": by_uid,
                "prediction_path": portable(path, repo_root),
                "prediction_sha256": artifact["sha256"],
            }
        )
    return {
        "corpus_id": corpus_id,
        "snapshot": snapshot,
        "words": words,
        "boundaries": boundaries,
        "tracks": tracks,
    }


def current_labels(corpus: dict[str, Any], track: dict[str, Any]) -> dict[str, str]:
    return {
        str(word["uid"]): str(track["predictions"][str(word["uid"])]["speaker_id"])
        for word in corpus["words"]
    }


def current_segments(
    corpus: dict[str, Any], track: dict[str, Any], labels: dict[str, str]
) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
    segment_by_word: dict[str, str] = {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for word in corpus["words"]:
        by_scenario[str(word["scenario_id"])].append(word)
    mode = str(track["segment_key"])
    for scenario_id, words in sorted(by_scenario.items()):
        words.sort(key=lambda row: (float(row["start"]), float(row["end"]), str(row["word_id"])))
        run = -1
        previous_label: str | None = None
        seen_explicit: set[str] = set()
        previous_explicit: str | None = None
        for word in words:
            uid = str(word["uid"])
            prediction = track["predictions"][uid]
            if mode == "explicit_segment_index":
                if prediction.get("segment_index") is None:
                    raise DecompositionError(f"explicit_segment_index_missing:{corpus['corpus_id']}:{uid}")
                explicit = str(prediction["segment_index"])
                if previous_explicit is not None and explicit != previous_explicit and explicit in seen_explicit:
                    raise DecompositionError(f"noncontiguous_explicit_segment:{corpus['corpus_id']}:{scenario_id}:{explicit}")
                seen_explicit.add(explicit)
                previous_explicit = explicit
                segment_id = f"{scenario_id}:explicit:{explicit}"
            elif mode == "contiguous_prediction_label":
                label = labels[uid]
                if previous_label is None or label != previous_label:
                    run += 1
                previous_label = label
                segment_id = f"{scenario_id}:run:{run:04d}"
            else:
                raise DecompositionError(f"unsupported_segment_key:{mode}")
            segment_by_word[uid] = segment_id
            groups[segment_id].append(word)
    return segment_by_word, dict(groups)


def oracle_labels(
    corpus: dict[str, Any], track: dict[str, Any]
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    current = current_labels(corpus, track)
    segment_by_word, segments = current_segments(corpus, track, current)

    oracle_boundaries = dict(current)
    events: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for word in corpus["words"]:
        if word["truth_class"] != "mixed":
            events[(str(word["scenario_id"]), str(word["event_id"]))].append(word)
    for words in events.values():
        identity = plurality(current[str(word["uid"])] for word in words)
        for word in words:
            oracle_boundaries[str(word["uid"])] = identity

    oracle_identity = dict(current)
    for words in segments.values():
        known = [word for word in words if word["truth_class"] == "known_speaker"]
        if not known:
            continue
        identity = plurality(str(word["speaker_id"]) for word in known)
        for word in known:
            oracle_identity[str(word["uid"])] = identity

    special = dict(current)
    full = dict(current)
    for word in corpus["words"]:
        uid = str(word["uid"])
        truth = normalized_truth_label(word)
        full[uid] = truth
        if word["truth_class"] in {"open_set_speaker", "mixed"}:
            special[uid] = truth

    return {
        "current": current,
        "oracle_boundaries_current_identity": oracle_boundaries,
        "current_boundaries_oracle_identity": oracle_identity,
        "overlap_open_set_oracle": special,
        "full_oracle_control": full,
    }, segment_by_word


def bcubed(truth: list[str], predicted: list[str]) -> dict[str, float]:
    truth_members: dict[str, set[int]] = defaultdict(set)
    predicted_members: dict[str, set[int]] = defaultdict(set)
    for index, (expected, actual) in enumerate(zip(truth, predicted)):
        truth_members[expected].add(index)
        predicted_members[actual].add(index)
    precision_rows = []
    recall_rows = []
    for index, (expected, actual) in enumerate(zip(truth, predicted)):
        intersection = len(truth_members[expected] & predicted_members[actual])
        precision_rows.append(intersection / len(predicted_members[actual]))
        recall_rows.append(intersection / len(truth_members[expected]))
    precision = sum(precision_rows) / len(precision_rows) if precision_rows else 0.0
    recall = sum(recall_rows) / len(recall_rows) if recall_rows else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def pairwise(truth: list[str], predicted: list[str]) -> dict[str, Any]:
    truth_counts: Counter[str] = Counter()
    predicted_counts: Counter[str] = Counter()
    contingency: Counter[tuple[str, str]] = Counter()
    for expected, actual in zip(truth, predicted):
        truth_counts[expected] += 1
        if not actual.startswith("unknown:"):
            predicted_counts[actual] += 1
            contingency[(expected, actual)] += 1
    true_positive = sum(count * (count - 1) // 2 for count in contingency.values())
    predicted_positive = sum(count * (count - 1) // 2 for count in predicted_counts.values())
    truth_positive = sum(count * (count - 1) // 2 for count in truth_counts.values())
    false_positive = predicted_positive - true_positive
    false_negative = truth_positive - true_positive
    precision = true_positive / predicted_positive if predicted_positive else 1.0
    recall = true_positive / truth_positive if truth_positive else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "true_positive_pairs": true_positive,
        "false_positive_pairs": false_positive,
        "false_negative_pairs": false_negative,
    }


def namespaced_label(corpus_id: str, value: str) -> str:
    return value if value in SPECIAL_LABELS else f"{corpus_id}:{value}"


def evaluate(
    words: list[dict[str, Any]], boundaries: list[dict[str, Any]], labels: dict[str, str]
) -> dict[str, Any]:
    expected_uids = {str(word["uid"]) for word in words}
    conservation = len(labels) == len(words) and set(labels) == expected_uids
    if not conservation:
        raise DecompositionError("oracle_word_conservation_failed")
    by_uid = {str(word["uid"]): word for word in words}
    known = [word for word in words if word["truth_class"] == "known_speaker"]
    truth_clusters = []
    predicted_clusters = []
    accepted = 0
    correct = 0
    for word in known:
        uid = str(word["uid"])
        corpus_id = str(word["corpus_id"])
        expected = namespaced_label(corpus_id, str(word["speaker_id"]))
        actual_raw = labels[uid]
        actual = namespaced_label(corpus_id, actual_raw)
        truth_clusters.append(expected)
        if actual_raw in SPECIAL_LABELS:
            predicted_clusters.append(f"unknown:{uid}")
        else:
            predicted_clusters.append(actual)
            accepted += 1
            correct += int(actual == expected)
    open_set = [word for word in words if word["truth_class"] == "open_set_speaker"]
    mixed = [word for word in words if word["truth_class"] == "mixed"]
    open_false = sum(labels[str(word["uid"])] not in SPECIAL_LABELS for word in open_set)
    mixed_safe = sum(labels[str(word["uid"])] == "mixed" for word in mixed)

    recovered = 0
    for boundary in boundaries:
        left_word = by_uid[str(boundary["left_uid"])]
        right_word = by_uid[str(boundary["right_uid"])]
        left_expected = namespaced_label(str(left_word["corpus_id"]), normalized_truth_label(left_word))
        right_expected = namespaced_label(str(right_word["corpus_id"]), normalized_truth_label(right_word))
        left_actual = namespaced_label(str(left_word["corpus_id"]), labels[str(left_word["uid"])])
        right_actual = namespaced_label(str(right_word["corpus_id"]), labels[str(right_word["uid"])])
        recovered += int(left_actual == left_expected and right_actual == right_expected and left_actual != right_actual)

    known_count = len(known)
    boundary_count = len(boundaries)
    return {
        "word_count": len(words),
        "prediction_count": len(labels),
        "word_conservation": conservation,
        "known_single_speaker_words": known_count,
        "known_attributed_words": accepted,
        "known_correct_words": correct,
        "known_attribution_coverage": round(accepted / known_count, 6) if known_count else 0.0,
        "known_speaker_recall": round(correct / known_count, 6) if known_count else 0.0,
        "known_attributed_precision": round(correct / accepted, 6) if accepted else None,
        "bcubed": bcubed(truth_clusters, predicted_clusters),
        "pairwise": pairwise(truth_clusters, predicted_clusters),
        "open_set_words": len(open_set),
        "open_set_false_attributions": open_false,
        "mixed_words": len(mixed),
        "mixed_safely_marked": mixed_safe,
        "mixed_fail_closed_rate": round(mixed_safe / len(mixed), 6) if mixed else 1.0,
        "boundary_count": boundary_count,
        "boundaries_recovered": recovered,
        "boundary_recall": round(recovered / boundary_count, 6) if boundary_count else 1.0,
    }


def track_decomposition(
    corpus: dict[str, Any], track: dict[str, Any], input_freeze_sha256: str
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, str]]]:
    matrices, segment_by_word = oracle_labels(corpus, track)
    metrics = {track_id: evaluate(corpus["words"], corpus["boundaries"], labels)
               for track_id, labels in matrices.items()}
    artifact_by_path = {
        row["path"]: row for row in corpus["snapshot"]["artifacts"]
    }
    frozen_sha = next(row["sha256"] for row in corpus["snapshot"]["artifacts"] if row["role"] == "frozen_manifest")
    word_rows = []
    for word in corpus["words"]:
        uid = str(word["uid"])
        prediction = track["predictions"][uid]
        truth_artifact = artifact_by_path[str(word["truth_path"])]
        word_rows.append(
            {
                "schema": WORD_SCHEMA,
                "version": VERSION,
                "input_freeze_sha256": input_freeze_sha256,
                "corpus_id": corpus["corpus_id"],
                "track_role": track["role"],
                "track_id": track["track_id"],
                "scenario_id": word["scenario_id"],
                "word_id": word["word_id"],
                "start": word["start"],
                "end": word["end"],
                "duration_sec": word["duration_sec"],
                "duration_bin": word["duration_bin"],
                "preceding_gap_sec": word["preceding_gap_sec"],
                "preceding_gap_bin": word["preceding_gap_bin"],
                "event_id": word["event_id"],
                "current_segment_id": segment_by_word[uid],
                "truth_class": word["truth_class"],
                "truth_speaker_id": word["speaker_id"],
                "overlap_state": word["overlap_state"],
                "current_reason": prediction.get("reason"),
                "labels": {track_id: matrices[track_id][uid] for track_id in ORACLE_TRACKS},
                "provenance": {
                    "truth_sha256": truth_artifact["sha256"],
                    "predictions_sha256": track["prediction_sha256"],
                    "frozen_manifest_sha256": frozen_sha,
                },
            }
        )
    word_by_uid = {str(word["uid"]): word for word in corpus["words"]}
    boundary_rows = []
    for boundary in corpus["boundaries"]:
        left = word_by_uid[str(boundary["left_uid"])]
        right = word_by_uid[str(boundary["right_uid"])]
        truth_artifact = artifact_by_path[str(boundary["truth_path"])]
        transition = f"{left['truth_class']}_to_{right['truth_class']}"
        recovered = {}
        for track_id in ORACLE_TRACKS:
            labels = matrices[track_id]
            expected_left = normalized_truth_label(left)
            expected_right = normalized_truth_label(right)
            actual_left = labels[str(left["uid"])]
            actual_right = labels[str(right["uid"])]
            recovered[track_id] = actual_left == expected_left and actual_right == expected_right and actual_left != actual_right
        boundary_rows.append(
            {
                "schema": BOUNDARY_SCHEMA,
                "version": VERSION,
                "input_freeze_sha256": input_freeze_sha256,
                "corpus_id": corpus["corpus_id"],
                "track_role": track["role"],
                "track_id": track["track_id"],
                "scenario_id": boundary["scenario_id"],
                "boundary_id": boundary["boundary_id"],
                "time": boundary["time"],
                "kind": boundary["kind"],
                "transition": transition,
                "left_word_id": boundary["left_word_id"],
                "right_word_id": boundary["right_word_id"],
                "recovered": recovered,
                "provenance": {
                    "truth_sha256": truth_artifact["sha256"],
                    "predictions_sha256": track["prediction_sha256"],
                    "frozen_manifest_sha256": frozen_sha,
                },
            }
        )
    return metrics, word_rows, boundary_rows, matrices


def subset_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"word_count": len(rows)}
    known = [row for row in rows if row["truth_class"] == "known_speaker"]
    open_set = [row for row in rows if row["truth_class"] == "open_set_speaker"]
    mixed = [row for row in rows if row["truth_class"] == "mixed"]
    result.update({"known_words": len(known), "open_set_words": len(open_set), "mixed_words": len(mixed)})
    for track_id in ORACLE_TRACKS:
        result[track_id] = {
            "known_correct": sum(row["labels"][track_id] == row["truth_speaker_id"] for row in known),
            "open_set_false_attributions": sum(row["labels"][track_id] not in SPECIAL_LABELS for row in open_set),
            "mixed_safely_marked": sum(row["labels"][track_id] == "mixed" for row in mixed),
        }
    return result


def strata_summary(
    word_rows: list[dict[str, Any]], boundary_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    primary_words = [row for row in word_rows if row["track_role"] == "primary"]
    primary_boundaries = [row for row in boundary_rows if row["track_role"] == "primary"]
    dimensions = {
        "corpus": lambda row: str(row["corpus_id"]),
        "speaker": lambda row: f"{row['corpus_id']}:{row['truth_speaker_id']}",
        "word_duration": lambda row: str(row["duration_bin"]),
        "preceding_gap": lambda row: str(row["preceding_gap_bin"]),
        "overlap_state": lambda row: str(row["overlap_state"]),
    }
    word_summary = []
    for dimension, selector in dimensions.items():
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in primary_words:
            groups[selector(row)].append(row)
        for value, rows in sorted(groups.items()):
            word_summary.append({"dimension": dimension, "value": value, **subset_stats(rows)})

    boundary_summary = []
    for dimension, selector in (
        ("corpus", lambda row: str(row["corpus_id"])),
        ("transition", lambda row: str(row["transition"])),
        ("boundary_kind", lambda row: str(row["kind"])),
    ):
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in primary_boundaries:
            groups[selector(row)].append(row)
        for value, rows in sorted(groups.items()):
            boundary_summary.append(
                {
                    "dimension": dimension,
                    "value": value,
                    "boundary_count": len(rows),
                    "recovered": {
                        track_id: sum(bool(row["recovered"][track_id]) for row in rows)
                        for track_id in ORACLE_TRACKS
                    },
                }
            )
    return {"words": word_summary, "boundaries": boundary_summary}


def gate_results(metrics: dict[str, Any], policy: dict[str, Any]) -> dict[str, bool]:
    gates = policy["decision"]["quality_reference_gates"]
    return {
        "bcubed_f1": metrics["bcubed"]["f1"] >= float(gates["minimum_bcubed_f1"]),
        "pairwise_precision": metrics["pairwise"]["precision"] >= float(gates["minimum_pairwise_precision"]),
        "known_speaker_recall": metrics["known_speaker_recall"] >= float(gates["minimum_known_speaker_recall"]),
        "boundary_recall": metrics["boundary_recall"] >= float(gates["minimum_boundary_recall"]),
        "open_set_false_attributions": metrics["open_set_false_attributions"] <= int(gates["maximum_open_set_false_attributions"]),
        "mixed_fail_closed_rate": metrics["mixed_fail_closed_rate"] >= float(gates["minimum_mixed_fail_closed_rate"]),
    }


def routing_decision(matrix: dict[str, dict[str, Any]], policy: dict[str, Any], invariants: dict[str, bool]) -> dict[str, Any]:
    current = matrix["current"]
    boundaries = matrix["oracle_boundaries_current_identity"]
    identity = matrix["current_boundaries_oracle_identity"]
    special = matrix["overlap_open_set_oracle"]
    weights = policy["decision"]["axis_gain_weights"]

    def weighted_gain(candidate: dict[str, Any]) -> float:
        return round(
            float(weights["known_speaker_recall"]) * (candidate["known_speaker_recall"] - current["known_speaker_recall"])
            + float(weights["boundary_recall"]) * (candidate["boundary_recall"] - current["boundary_recall"]),
            6,
        )

    special_total = current["open_set_words"] + current["mixed_words"]
    current_special_errors = current["open_set_false_attributions"] + current["mixed_words"] - current["mixed_safely_marked"]
    oracle_special_errors = special["open_set_false_attributions"] + special["mixed_words"] - special["mixed_safely_marked"]
    special_gain = round((current_special_errors - oracle_special_errors) / special_total, 6) if special_total else 0.0
    gains = {
        "segmentation": weighted_gain(boundaries),
        "speaker_identity": weighted_gain(identity),
        "overlap_open_set": special_gain,
    }
    minimum = float(policy["decision"]["minimum_material_axis_gain"])
    dominance = float(policy["decision"]["minimum_dominance_margin"])
    if not all(invariants.values()):
        decision = "CURRENT_LOCAL_ATTRIBUTION_LIMIT"
        rule = "invariant_failure_means_current_local_attribution_limit"
    elif (
        gains["speaker_identity"] >= minimum
        and gains["speaker_identity"] >= gains["segmentation"] + dominance
        and gains["speaker_identity"] >= gains["overlap_open_set"] + dominance
    ):
        decision = "ADVANCE_STRONGER_SPEAKER_IDENTITY"
        rule = "dominant_identity_gain_means_advance_stronger_speaker_identity"
    elif (
        gains["segmentation"] >= minimum
        and gains["segmentation"] >= gains["speaker_identity"]
        and gains["segmentation"] >= gains["overlap_open_set"]
    ):
        decision = "ADVANCE_DEDICATED_SEGMENTATION"
        rule = "material_boundary_gain_means_advance_dedicated_segmentation"
    elif current_special_errors > 0 and gains["overlap_open_set"] >= minimum:
        decision = "ADVANCE_OVERLAP_OPEN_SET_MODEL"
        rule = "remaining_special_errors_fixed_by_special_oracle_mean_advance_overlap_open_set_model"
    else:
        decision = "CURRENT_LOCAL_ATTRIBUTION_LIMIT"
        rule = "otherwise_current_local_attribution_limit"
    return {
        "decision": decision,
        "matched_rule": rule,
        "axis_gains": gains,
        "minimum_material_axis_gain": minimum,
        "minimum_dominance_margin": dominance,
        "current_special_error_count": current_special_errors,
        "oracle_special_error_count": oracle_special_errors,
        "current_gates": gate_results(current, policy),
        "oracle_gates": {track_id: gate_results(matrix[track_id], policy) for track_id in ORACLE_TRACKS[1:]},
    }


def assert_public_safe(value: Any) -> None:
    forbidden_keys = {"text", "voice", "private_seed", "truth_path", "prediction_path"}
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if forbidden_keys & set(item):
                raise DecompositionError(f"private_key_in_public_output:{sorted(forbidden_keys & set(item))}")
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str) and (item.startswith("/Users/") or "/Users/" in item):
            raise DecompositionError("absolute_private_path_in_public_output")


def report_markdown(report: dict[str, Any]) -> str:
    routing = report["routing_evidence"]
    current = report["aggregate_primary"]["current"]
    boundary = report["aggregate_primary"]["oracle_boundaries_current_identity"]
    identity = report["aggregate_primary"]["current_boundaries_oracle_identity"]
    special = report["aggregate_primary"]["overlap_open_set_oracle"]
    lines = [
        "# Remote Speaker Attribution Error Decomposition v1",
        "",
        f"Decision: **{report['decision']}**",
        "",
        "## Frozen Evidence",
        "",
        f"- Input freeze: `{report['input_freeze_sha256']}`",
        f"- Exact corpora: `{len(report['corpora'])}`",
        f"- Primary words: `{current['word_count']}`",
        f"- Evaluated boundaries: `{current['boundary_count']}`",
        "- Production changed: `false`",
        "",
        "## Oracle Matrix",
        "",
        "| Track | Known recall | Boundary recall | B-cubed F1 | Open-set false |",
        "|---|---:|---:|---:|---:|",
        f"| current | {current['known_speaker_recall']:.6f} | {current['boundary_recall']:.6f} | {current['bcubed']['f1']:.6f} | {current['open_set_false_attributions']} |",
        f"| oracle boundaries + current identity | {boundary['known_speaker_recall']:.6f} | {boundary['boundary_recall']:.6f} | {boundary['bcubed']['f1']:.6f} | {boundary['open_set_false_attributions']} |",
        f"| current boundaries + oracle identity | {identity['known_speaker_recall']:.6f} | {identity['boundary_recall']:.6f} | {identity['bcubed']['f1']:.6f} | {identity['open_set_false_attributions']} |",
        f"| overlap/open-set oracle | {special['known_speaker_recall']:.6f} | {special['boundary_recall']:.6f} | {special['bcubed']['f1']:.6f} | {special['open_set_false_attributions']} |",
        "",
        "## Routing Evidence",
        "",
        f"- Segmentation gain: `{routing['axis_gains']['segmentation']:.6f}`",
        f"- Speaker identity gain: `{routing['axis_gains']['speaker_identity']:.6f}`",
        f"- Overlap/open-set gain: `{routing['axis_gains']['overlap_open_set']:.6f}`",
        f"- Matched rule: `{routing['matched_rule']}`",
        "",
        "## Interpretation",
        "",
        report["interpretation"],
        "",
        "This is a diagnostic routing decision. No transcript or production attribution profile was changed.",
        "",
    ]
    return "\n".join(lines)


def compute_outputs(
    args: argparse.Namespace, policy: dict[str, Any], input_manifest: dict[str, Any]
) -> dict[str, Any]:
    snapshots = {row["corpus_id"]: row for row in input_manifest["corpora"]}
    corpora = [load_corpus(args.repo_root, policy, spec, snapshots[str(spec["corpus_id"])]) for spec in policy["corpora"]]
    all_word_rows: list[dict[str, Any]] = []
    all_boundary_rows: list[dict[str, Any]] = []
    corpus_reports = []
    aggregate_words: list[dict[str, Any]] = []
    aggregate_boundaries: list[dict[str, Any]] = []
    aggregate_labels: dict[str, dict[str, str]] = {track_id: {} for track_id in ORACLE_TRACKS}

    for corpus in corpora:
        track_reports = []
        for track in corpus["tracks"]:
            metrics, word_rows, boundary_rows, matrices = track_decomposition(
                corpus, track, str(input_manifest["freeze_sha256"])
            )
            all_word_rows.extend(word_rows)
            all_boundary_rows.extend(boundary_rows)
            track_reports.append(
                {
                    "role": track["role"],
                    "track_id": track["track_id"],
                    "segment_key": track["segment_key"],
                    "metrics": metrics,
                }
            )
            if track["role"] == "primary":
                aggregate_words.extend(corpus["words"])
                aggregate_boundaries.extend(corpus["boundaries"])
                for track_id in ORACLE_TRACKS:
                    aggregate_labels[track_id].update(matrices[track_id])
        corpus_reports.append(
            {
                "corpus_id": corpus["corpus_id"],
                "word_count": len(corpus["words"]),
                "boundary_count": len(corpus["boundaries"]),
                "tracks": track_reports,
            }
        )

    aggregate = {
        track_id: evaluate(aggregate_words, aggregate_boundaries, labels)
        for track_id, labels in aggregate_labels.items()
    }
    full = aggregate["full_oracle_control"]
    word_uids = [str(word["uid"]) for word in aggregate_words]
    boundary_uids = [str(boundary["uid"]) for boundary in aggregate_boundaries]
    word_uid_set = set(word_uids)
    invariants = {
        "exact_truth_counted_once": len(word_uids) == len(set(word_uids))
        and len(boundary_uids) == len(set(boundary_uids)),
        "timestamps_valid": all(float(word["end"]) >= float(word["start"]) for word in aggregate_words)
        and all(
            str(boundary["left_uid"]) in word_uid_set
            and str(boundary["right_uid"]) in word_uid_set
            for boundary in aggregate_boundaries
        ),
        "all_words_conserved": all(
            metrics["word_conservation"]
            for corpus in corpus_reports
            for track in corpus["tracks"]
            for metrics in track["metrics"].values()
        ),
        "full_oracle_known_recall": full["known_speaker_recall"] == 1.0,
        "full_oracle_boundary_recall": full["boundary_recall"] == 1.0,
        "full_oracle_bcubed": full["bcubed"]["f1"] == 1.0,
        "full_oracle_pairwise_precision": full["pairwise"]["precision"] == 1.0,
        "full_oracle_open_set_safe": full["open_set_false_attributions"] == 0,
        "full_oracle_mixed_safe": full["mixed_fail_closed_rate"] == 1.0,
        "hard_sets_not_reopened": input_manifest["hard_sets_reopened"] is False,
        "upstream_decisions_and_ledgers_frozen": True,
        "production_guards_frozen": True,
    }
    routing = routing_decision(aggregate, policy, invariants)
    decision = str(routing["decision"])
    interpretation = {
        "ADVANCE_DEDICATED_SEGMENTATION": "Exact boundaries provide the dominant material recovery. Qualify a dedicated local segmentation backend next.",
        "ADVANCE_STRONGER_SPEAKER_IDENTITY": "Oracle identity on the existing partitions provides the dominant material recovery. Qualify a stronger local speaker-identity backend next, while retaining explicit abstention.",
        "ADVANCE_OVERLAP_OPEN_SET_MODEL": "Known-speaker attribution is not the dominant recoverable loss; special-class abstention is. Qualify an overlap/open-set detector next.",
        "CURRENT_LOCAL_ATTRIBUTION_LIMIT": "No isolated oracle axis provides a safe material route under the fixed rules. Keep current production attribution and obtain a new evidence or model class.",
    }[decision]

    word_bytes = jsonl_bytes(all_word_rows)
    boundary_bytes = jsonl_bytes(all_boundary_rows)
    public_input = public_input_manifest(input_manifest)
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "input_freeze_sha256": input_manifest["freeze_sha256"],
        "policy_sha256": input_manifest["policy"]["sha256"],
        "implementation": {
            "path": "scripts/analyze-remote-speaker-attribution-errors-v1.py",
            "sha256": sha256(SCRIPT_PATH),
        },
        "scope": {
            "diagnostic_only": True,
            "production_candidate_selected": False,
            "synthetic_labels_exported_to_real_sessions": False,
        },
        "invariants": invariants,
        "corpora": corpus_reports,
        "aggregate_primary": aggregate,
        "routing_evidence": routing,
        "strata": strata_summary(all_word_rows, all_boundary_rows),
        "private_artifact_hashes": {
            "word_error_decomposition_jsonl": sha256_bytes(word_bytes),
            "boundary_error_decomposition_jsonl": sha256_bytes(boundary_bytes),
        },
        "production_changed": False,
        "interpretation": interpretation,
        "limitations": [
            "exact_synthetic_truth_only",
            "truth_lab_v1_and_hard_v2_expose_only_implicit_prediction_label_segments",
            "oracles_measure_ceilings_and_do_not_define_a_production_algorithm",
            "human_names_and_cross_session_voice_identity_remain_out_of_scope",
        ],
        "next": {
            "ADVANCE_DEDICATED_SEGMENTATION": "Dedicated Remote Speaker Segmentation Qualification v1",
            "ADVANCE_STRONGER_SPEAKER_IDENTITY": "Stronger Remote Speaker Identity Backend Qualification v1",
            "ADVANCE_OVERLAP_OPEN_SET_MODEL": "Remote Overlap And Open-Set Abstention Qualification v1",
            "CURRENT_LOCAL_ATTRIBUTION_LIMIT": "Remote Speaker Human Reference Expansion v1",
        }[decision],
    }
    assert_public_safe(public_input)
    assert_public_safe(report)
    markdown = report_markdown(report).encode("utf-8")
    return {
        "public_input": canonical_json(public_input),
        "word_rows": word_bytes,
        "boundary_rows": boundary_bytes,
        "report": canonical_json(report),
        "markdown": markdown,
        "decision": decision,
    }


def analyze(args: argparse.Namespace, policy: dict[str, Any], policy_path: Path) -> dict[str, Any]:
    input_manifest = verified_input(args, policy, policy_path)
    outputs = compute_outputs(args, policy, input_manifest)
    atomic_write(args.out_dir / "input_manifest.public.json", outputs["public_input"])
    atomic_write(args.out_dir / "private/word_error_decomposition.jsonl", outputs["word_rows"])
    atomic_write(args.out_dir / "private/boundary_error_decomposition.jsonl", outputs["boundary_rows"])
    atomic_write(args.out_dir / "remote_speaker_attribution_error_decomposition_report.json", outputs["report"])
    atomic_write(args.out_dir / "remote_speaker_attribution_error_decomposition_report.md", outputs["markdown"])
    report = json.loads(outputs["report"])
    print(f"decision: {report['decision']}")
    print(f"words: {report['aggregate_primary']['current']['word_count']}")
    print(f"boundaries: {report['aggregate_primary']['current']['boundary_count']}")
    for axis, gain in report["routing_evidence"]["axis_gains"].items():
        print(f"{axis}_gain: {gain:.6f}")
    return report


def replay(args: argparse.Namespace, policy: dict[str, Any], policy_path: Path) -> dict[str, Any]:
    input_manifest = verified_input(args, policy, policy_path)
    outputs = compute_outputs(args, policy, input_manifest)
    expected = {
        "public_input": args.out_dir / "input_manifest.public.json",
        "word_rows": args.out_dir / "private/word_error_decomposition.jsonl",
        "boundary_rows": args.out_dir / "private/boundary_error_decomposition.jsonl",
        "report": args.out_dir / "remote_speaker_attribution_error_decomposition_report.json",
        "markdown": args.out_dir / "remote_speaker_attribution_error_decomposition_report.md",
    }
    matches = {}
    for key, path in expected.items():
        if not path.is_file():
            raise DecompositionError(f"replay_artifact_missing:{path}")
        matches[key] = path.read_bytes() == outputs[key]
    value = {
        "schema": REPLAY_SCHEMA,
        "version": VERSION,
        "decision": "DETERMINISTIC_REPLAY_VERIFIED" if all(matches.values()) else "REPLAY_MISMATCH",
        "analysis_decision": outputs["decision"],
        "input_freeze_sha256": input_manifest["freeze_sha256"],
        "matches": matches,
    }
    write_json(args.out_dir / "replay_report.json", value)
    if not all(matches.values()):
        raise DecompositionError("deterministic_replay_mismatch")
    print("decision: DETERMINISTIC_REPLAY_VERIFIED")
    return value


def status(args: argparse.Namespace) -> int:
    path = args.out_dir / "remote_speaker_attribution_error_decomposition_report.json"
    if not path.is_file():
        print("decision: BLOCKED")
        print("reason: report_missing")
        return 2
    report = read_json(path)
    print(f"decision: {report['decision']}")
    print(f"input_freeze_sha256: {report['input_freeze_sha256']}")
    print(f"words: {report['aggregate_primary']['current']['word_count']}")
    print(f"boundaries: {report['aggregate_primary']['current']['boundary_count']}")
    for axis, gain in report["routing_evidence"]["axis_gains"].items():
        print(f"{axis}_gain: {gain:.6f}")
    print(f"next: {report['next']}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "analyze", "status", "replay", "all"))
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.repo_root = args.repo_root.expanduser().resolve()
    args.policy = resolve(args.repo_root, args.policy)
    args.out_dir = resolve(args.repo_root, args.out_dir)
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.command == "status":
            return status(args)
        policy = load_policy(args.policy)
        if args.command == "freeze":
            freeze(args, policy, args.policy)
        elif args.command == "analyze":
            analyze(args, policy, args.policy)
        elif args.command == "replay":
            replay(args, policy, args.policy)
        else:
            freeze(args, policy, args.policy)
            analyze(args, policy, args.policy)
            replay(args, policy, args.policy)
        return 0
    except DecompositionError as error:
        print(f"error: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
