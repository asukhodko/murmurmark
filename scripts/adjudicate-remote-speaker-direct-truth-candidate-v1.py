#!/usr/bin/env python3
"""One-shot direct-truth adjudication for the frozen remote speaker candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/remote-speaker-direct-truth-candidate-adjudication-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/remote-speaker-direct-truth-candidate-adjudication-v1"

POLICY_SCHEMA = "murmurmark.remote_speaker_direct_truth_candidate_adjudication_policy/v1"
ITEM_SCHEMA = "murmurmark.remote_speaker_direct_truth_candidate_adjudication_item/v1"
CORE_SCHEMA = "murmurmark.remote_speaker_direct_truth_candidate_adjudication_core/v1"
REPORT_SCHEMA = "murmurmark.remote_speaker_direct_truth_candidate_adjudication_report/v1"
REPLAY_SCHEMA = "murmurmark.remote_speaker_direct_truth_candidate_adjudication_replay/v1"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_direct_truth_candidate_adjudication_manifest/v1"


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True).encode() + b"\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write(path, b"".join(canonical(row) for row in rows))


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def repo_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"source path must be repository-relative: {raw}")
    return ROOT / path


def artifact(path: Path, artifact_id: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    if artifact_id:
        row["id"] = artifact_id
    return row


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise ValueError(f"unsupported policy schema: {policy.get('schema')}")
    if policy.get("state") != "frozen_before_candidate_evaluation":
        raise ValueError("policy was not frozen before candidate evaluation")
    decision = policy.get("decision") or {}
    expected = {
        "ADVANCE_DIRECT_TRUTH_IDENTITY",
        "KEEP_COVERAGE_V3",
        "EVIDENCE_BOUND",
    }
    if set(decision.get("allowed_outcomes") or []) != expected:
        raise ValueError("policy terminal outcomes differ from the v1 contract")
    if decision.get("production_promotion_allowed") is not False:
        raise ValueError("production promotion must remain disabled")
    return policy


def verify_sources(policy: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
    payloads: dict[str, dict[str, Any]] = {}
    verified: list[dict[str, Any]] = []
    failures: list[str] = []
    source_ids: set[str] = set()
    for source in policy.get("sources") or []:
        source_id = str(source.get("id") or "")
        if not source_id or source_id in source_ids:
            failures.append(f"invalid_source_id:{source_id}")
            continue
        source_ids.add(source_id)
        path = repo_path(str(source.get("path") or ""))
        row = {"id": source_id, "path": str(source.get("path") or ""), "status": "verified"}
        if not path.is_file():
            row["status"] = "missing"
        else:
            row["bytes"] = path.stat().st_size
            row["sha256"] = sha256(path)
            if row["bytes"] != int(source.get("bytes", -1)):
                row["status"] = "size_mismatch"
            if row["sha256"] != source.get("sha256"):
                row["status"] = "sha256_mismatch"
            if row["status"] == "verified":
                try:
                    payloads[source_id] = {
                        "path": path,
                        "value": read_jsonl(path) if path.suffix == ".jsonl" else read_json(path),
                    }
                except (ValueError, json.JSONDecodeError):
                    row["status"] = "invalid_json"
        if row["status"] != "verified":
            failures.append(f"{row['status']}:{source_id}")
        verified.append(row)
    return payloads, verified, failures


def verify_pack_artifacts(pack: dict[str, Any], expected_count: int) -> tuple[list[dict[str, Any]], list[str]]:
    frozen = pack.get("frozen_artifacts") or {}
    failures: list[str] = []
    rows: list[dict[str, Any]] = []
    if len(frozen) != expected_count:
        failures.append(f"frozen_artifact_count:{len(frozen)}")
    for artifact_id, expected in sorted(frozen.items()):
        path = repo_path(str(expected.get("path") or ""))
        row = {"id": artifact_id, "path": expected.get("path"), "status": "verified"}
        if not path.is_file():
            row["status"] = "missing"
        else:
            actual_bytes = path.stat().st_size
            actual_sha = sha256(path)
            if actual_bytes != int(expected.get("bytes", -1)):
                row["status"] = "size_mismatch"
            if actual_sha != expected.get("sha256"):
                row["status"] = "sha256_mismatch"
        if row["status"] != "verified":
            failures.append(f"frozen_{row['status']}:{artifact_id}")
        rows.append(row)
    return rows, failures


def verify_artifact_list(artifacts: list[dict[str, Any]], expected_count: int) -> tuple[list[dict[str, Any]], list[str]]:
    failures: list[str] = []
    rows: list[dict[str, Any]] = []
    if len(artifacts) != expected_count:
        failures.append(f"inherited_artifact_count:{len(artifacts)}")
    ids: set[str] = set()
    for expected in artifacts:
        artifact_id = str(expected.get("id") or "")
        row = {"id": artifact_id, "path": expected.get("path"), "status": "verified"}
        if not artifact_id or artifact_id in ids:
            row["status"] = "invalid_id"
        ids.add(artifact_id)
        path = repo_path(str(expected.get("path") or ""))
        if not path.is_file():
            row["status"] = "missing"
        elif path.stat().st_size != int(expected.get("bytes", -1)):
            row["status"] = "size_mismatch"
        elif sha256(path) != expected.get("sha256"):
            row["status"] = "sha256_mismatch"
        if row["status"] != "verified":
            failures.append(f"inherited_{row['status']}:{artifact_id}")
        rows.append(row)
    return rows, failures


def index_unique(rows: list[dict[str, Any]], key: str, label: str, failures: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value or value in result:
            failures.append(f"duplicate_or_empty:{label}:{value}")
            continue
        result[value] = row
    return result


def prediction_outcome(truth: str, prediction: str | None, positive_prefix: str) -> str:
    if truth.startswith(positive_prefix):
        if prediction is None:
            return "abstained_positive"
        if prediction == truth:
            return "correct_identity"
        return "false_identity"
    return "safe_abstention" if prediction is None else "unsafe_fail_closed_acceptance"


def aggregate_side(rows: list[dict[str, Any]], side: str) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    words: Counter[str] = Counter()
    seconds: Counter[str] = Counter()
    for row in rows:
        outcome = str(row[f"{side}_outcome"])
        counts[outcome] += 1
        words[outcome] += int(row["word_count"])
        seconds[outcome] += float(row["coverage_weight_sec"])
    accepted = counts["correct_identity"] + counts["false_identity"] + counts["unsafe_fail_closed_acceptance"]
    identity_accepted = counts["correct_identity"] + counts["false_identity"]
    return {
        "items": dict(sorted(counts.items())),
        "words": dict(sorted(words.items())),
        "seconds": {key: round(value, 6) for key, value in sorted(seconds.items())},
        "accepted_items": accepted,
        "identity_precision": (
            round(counts["correct_identity"] / identity_accepted, 6) if identity_accepted else None
        ),
    }


def evaluate_core(policy: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    payloads, verified_sources, failures = verify_sources(policy)
    required_ids = {
        "direct_truth_pack",
        "direct_truth_selection",
        "direct_truth_slot_map",
        "direct_truth_answers",
        "direct_truth_report",
        "direct_truth_replay",
        "enrollment_input_manifest",
        "enrollment_item_comparison",
        "enrollment_report",
        "coverage_v3_report",
    }
    missing_payloads = sorted(required_ids - set(payloads))
    failures.extend(f"missing_payload:{source_id}" for source_id in missing_payloads)
    if missing_payloads:
        core = {
            "schema": CORE_SCHEMA,
            "decision": "EVIDENCE_BOUND",
            "scope": {
                "source_items": int(policy["scope"]["source_items"]),
                "source_words": int(policy["scope"]["source_words"]),
                "primary_items": 0,
                "repeat_items": 0,
                "changed_items": 0,
                "attributed_primary_items": 0,
                "fail_closed_primary_items": 0,
            },
            "repeat_review": {"matches": 0, "compared": 0, "consistency": 0.0, "counted_in_identity_metrics": False},
            "control": {"items": {}, "words": {}, "seconds": {}, "accepted_items": 0, "identity_precision": None},
            "candidate": {"items": {}, "words": {}, "seconds": {}, "accepted_items": 0, "identity_precision": None},
            "comparison": {
                "gained_correct_identity_items": 0,
                "lost_correct_control_identity_items": 0,
                "net_additional_correct_identity_items": 0,
                "net_additional_correct_identity_ratio": 0.0,
                "new_false_identity_items": 0,
                "control_fail_closed_unsafe_acceptance_items": 0,
                "candidate_fail_closed_unsafe_acceptance_items": 0,
                "fail_closed_unsafe_acceptance_regression_items": 0,
                "change_matrix": [],
            },
            "integrity_failures": sorted(set(failures)),
            "gates": {"input_integrity": False},
            "limitations": {
                "direct_identity_labels": 0,
                "exemplar_purity_directly_graded": False,
                "fail_closed_rows_are_not_positive_identity_truth": True,
                "candidate_thresholds_were_not_tuned": True,
            },
            "safety": {
                "shadow_only": True,
                "production_mutated": False,
                "coverage_v3_mutated": False,
                "selected_transcript_mutated": False,
                "raw_audio_mutated": False,
                "primary_asr_mutated": False,
                "echo_guard_mutated": False,
                "thresholds_tuned": False,
                "cross_session_identity_used": False,
                "speech_text_public": False,
                "session_ids_public": False,
            },
        }
        return core, [], {"sources": verified_sources, "frozen_artifacts": []}

    pack = payloads["direct_truth_pack"]["value"]
    scope = policy["scope"]
    frozen_rows, frozen_failures = verify_pack_artifacts(pack, int(scope["pack_artifacts"]))
    failures.extend(frozen_failures)
    enrollment_input = payloads["enrollment_input_manifest"]["value"]
    inherited_rows, inherited_failures = verify_artifact_list(
        enrollment_input.get("inherited_artifacts") or [], int(scope["inherited_artifacts"])
    )
    failures.extend(inherited_failures)

    selection = payloads["direct_truth_selection"]["value"]
    slot_map = payloads["direct_truth_slot_map"]["value"]
    answers = payloads["direct_truth_answers"]["value"]
    comparisons = payloads["enrollment_item_comparison"]["value"]
    direct_report = payloads["direct_truth_report"]["value"]
    direct_replay = payloads["direct_truth_replay"]["value"]
    enrollment_report = payloads["enrollment_report"]["value"]
    coverage_report = payloads["coverage_v3_report"]["value"]

    selected = index_unique(selection, "item_id", "selection", failures)
    answer_by_slot = index_unique(answers, "slot_id", "answer", failures)
    comparison_by_item = index_unique(comparisons, "item_id", "comparison", failures)
    slot_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    slot_ids: set[str] = set()
    for row in slot_map:
        slot_id = str(row.get("slot_id") or "")
        if not slot_id or slot_id in slot_ids:
            failures.append(f"duplicate_or_empty:slot:{slot_id}")
        slot_ids.add(slot_id)
        slot_by_item[str(row.get("item_id") or "")].append(row)

    expected_primary = int(scope["primary_items"])
    expected_repeat = int(scope["repeat_items"])
    primary_slots = [row for row in slot_map if row.get("kind") == "primary"]
    repeat_slots = [row for row in slot_map if row.get("kind") == "repeat"]
    if len(selection) != expected_primary or len(selected) != expected_primary:
        failures.append(f"primary_selection_count:{len(selection)}")
    if len(primary_slots) != expected_primary:
        failures.append(f"primary_slot_count:{len(primary_slots)}")
    if len(repeat_slots) != expected_repeat:
        failures.append(f"repeat_slot_count:{len(repeat_slots)}")
    if set(answer_by_slot) != slot_ids:
        failures.append("answer_slot_conservation")
    if any(row.get("truth_grade") != policy["truth"]["truth_grade"] for row in answers):
        failures.append("truth_grade_mismatch")
    if set(selected) - set(comparison_by_item):
        failures.append("selected_item_missing_comparison")

    expected_strata = {key: int(value) for key, value in scope["strata"].items()}
    observed_strata = Counter(str(row.get("stratum") or "") for row in selection)
    if dict(sorted(observed_strata.items())) != dict(sorted(expected_strata.items())):
        failures.append("strata_mismatch")
    changed_strata = {"newly_accepted", "removed_acceptance"}
    changed_count = sum(observed_strata[name] for name in changed_strata)
    if changed_count != int(scope["changed_items"]):
        failures.append(f"changed_item_count:{changed_count}")

    if direct_report.get("decision") != "DIRECT_TRUTH_SEED_READY":
        failures.append("direct_truth_not_ready")
    if not direct_report.get("invariants") or not all(direct_report["invariants"].values()):
        failures.append("direct_truth_invariants")
    if not direct_report.get("gates") or not all(direct_report["gates"].values()):
        failures.append("direct_truth_gates")
    if direct_replay.get("byte_exact") is not True:
        failures.append("direct_truth_replay")
    if coverage_report.get("decision") != "PROMOTE" or not all((coverage_report.get("gates") or {}).values()):
        failures.append("coverage_v3_not_frozen_promoted_control")
    if enrollment_report.get("candidate", {}).get("id") != policy["candidate"]["id"]:
        failures.append("candidate_id_mismatch")
    if enrollment_report.get("safety", {}).get("thresholds_tuned") is not False:
        failures.append("candidate_thresholds_tuned")
    if enrollment_report.get("safety", {}).get("production_mutated") is not False:
        failures.append("candidate_production_mutated")

    repeat_matches = 0
    repeat_compared = 0
    for item_id, mappings in slot_by_item.items():
        primary = [row for row in mappings if row.get("kind") == "primary"]
        repeats = [row for row in mappings if row.get("kind") == "repeat"]
        if len(primary) != 1 or len(repeats) > 1:
            failures.append(f"slot_mapping_shape:{item_id}")
            continue
        if repeats:
            repeat_compared += 1
            primary_answer = answer_by_slot.get(str(primary[0].get("slot_id") or ""), {}).get("outcome")
            repeat_answer = answer_by_slot.get(str(repeats[0].get("slot_id") or ""), {}).get("outcome")
            repeat_matches += int(primary_answer == repeat_answer)
    repeat_consistency = round(repeat_matches / repeat_compared, 6) if repeat_compared else 0.0
    if repeat_compared != expected_repeat:
        failures.append(f"repeat_compared:{repeat_compared}")
    if repeat_consistency < float(policy["truth"]["repeat_consistency_floor"]):
        failures.append(f"repeat_consistency:{repeat_consistency}")

    positive_prefix = str(policy["truth"]["positive_prefix"])
    fail_closed = set(policy["truth"]["fail_closed_outcomes"])
    item_rows: list[dict[str, Any]] = []
    for item_id in sorted(selected):
        selection_row = selected[item_id]
        primary = [row for row in slot_by_item.get(item_id, []) if row.get("kind") == "primary"]
        if len(primary) != 1:
            failures.append(f"primary_mapping:{item_id}")
            continue
        answer = answer_by_slot.get(str(primary[0].get("slot_id") or ""))
        comparison = comparison_by_item.get(item_id)
        if answer is None or comparison is None:
            failures.append(f"adjudication_input_missing:{item_id}")
            continue
        truth = str(answer.get("outcome") or "")
        if not truth.startswith(positive_prefix) and truth not in fail_closed:
            failures.append(f"unknown_truth_outcome:{item_id}")
            continue
        control_prediction = comparison.get("control", {}).get("speaker_id")
        candidate_prediction = comparison.get("candidate", {}).get("speaker_id")
        control_outcome = prediction_outcome(truth, control_prediction, positive_prefix)
        candidate_outcome = prediction_outcome(truth, candidate_prediction, positive_prefix)
        item_rows.append({
            "schema": ITEM_SCHEMA,
            "item_id": item_id,
            "session_id": selection_row.get("session_id"),
            "stratum": selection_row.get("stratum"),
            "truth_kind": "positive_identity" if truth.startswith(positive_prefix) else truth,
            "truth_outcome": truth,
            "control_prediction": control_prediction,
            "candidate_prediction": candidate_prediction,
            "control_outcome": control_outcome,
            "candidate_outcome": candidate_outcome,
            "change": comparison.get("change"),
            "word_count": int(selection_row.get("word_count") or 0),
            "coverage_weight_sec": round(float(selection_row.get("coverage_weight_sec") or 0), 6),
            "source_item_sha256": selection_row.get("source_item_sha256"),
        })

    if len(item_rows) != expected_primary:
        failures.append(f"adjudicated_item_count:{len(item_rows)}")
    positive_rows = [row for row in item_rows if row["truth_kind"] == "positive_identity"]
    if len(positive_rows) != int(scope["attributed_primary_items"]):
        failures.append(f"attributed_primary_count:{len(positive_rows)}")

    control = aggregate_side(item_rows, "control")
    candidate = aggregate_side(item_rows, "candidate")
    control_correct = control["items"].get("correct_identity", 0)
    candidate_correct = candidate["items"].get("correct_identity", 0)
    additional_correct = candidate_correct - control_correct
    additional_correct_ratio = round(additional_correct / len(positive_rows), 6) if positive_rows else 0.0
    new_false = max(
        0,
        candidate["items"].get("false_identity", 0) - control["items"].get("false_identity", 0),
    )
    lost_correct = sum(
        1
        for row in positive_rows
        if row["control_outcome"] == "correct_identity" and row["candidate_outcome"] != "correct_identity"
    )
    gained_correct = sum(
        1
        for row in positive_rows
        if row["control_outcome"] != "correct_identity" and row["candidate_outcome"] == "correct_identity"
    )
    control_unsafe = control["items"].get("unsafe_fail_closed_acceptance", 0)
    candidate_unsafe = candidate["items"].get("unsafe_fail_closed_acceptance", 0)
    unsafe_regression = candidate_unsafe - control_unsafe

    decision_policy = policy["decision"]
    advance_gates = {
        "minimum_additional_correct_identity_items": additional_correct
        >= int(decision_policy["minimum_additional_correct_identity_items"]),
        "minimum_additional_correct_identity_ratio": additional_correct_ratio
        >= float(decision_policy["minimum_additional_correct_identity_ratio"]),
        "no_new_false_identity": new_false == 0,
        "no_lost_correct_control_identity": lost_correct == 0,
        "no_fail_closed_acceptance_regression": unsafe_regression <= 0,
    }
    integrity_ok = not failures
    decision = (
        "EVIDENCE_BOUND"
        if not integrity_ok
        else "ADVANCE_DIRECT_TRUTH_IDENTITY"
        if all(advance_gates.values())
        else "KEEP_COVERAGE_V3"
    )

    change_matrix = Counter(
        (row["stratum"], row["truth_kind"], row["control_outcome"], row["candidate_outcome"])
        for row in item_rows
    )
    matrix = [
        {
            "stratum": key[0],
            "truth_kind": key[1],
            "control_outcome": key[2],
            "candidate_outcome": key[3],
            "items": value,
        }
        for key, value in sorted(change_matrix.items())
    ]
    core = {
        "schema": CORE_SCHEMA,
        "generator": {
            "name": "adjudicate-remote-speaker-direct-truth-candidate-v1",
            "version": "0.1.0",
            "mode": "deterministic_offline_one_shot",
        },
        "decision": decision,
        "scope": {
            "source_items": int(scope["source_items"]),
            "source_words": int(scope["source_words"]),
            "primary_items": len(item_rows),
            "repeat_items": repeat_compared,
            "changed_items": changed_count,
            "attributed_primary_items": len(positive_rows),
            "fail_closed_primary_items": len(item_rows) - len(positive_rows),
        },
        "repeat_review": {
            "matches": repeat_matches,
            "compared": repeat_compared,
            "consistency": repeat_consistency,
            "counted_in_identity_metrics": False,
        },
        "control": control,
        "candidate": candidate,
        "comparison": {
            "gained_correct_identity_items": gained_correct,
            "lost_correct_control_identity_items": lost_correct,
            "net_additional_correct_identity_items": additional_correct,
            "net_additional_correct_identity_ratio": additional_correct_ratio,
            "new_false_identity_items": new_false,
            "control_fail_closed_unsafe_acceptance_items": control_unsafe,
            "candidate_fail_closed_unsafe_acceptance_items": candidate_unsafe,
            "fail_closed_unsafe_acceptance_regression_items": unsafe_regression,
            "change_matrix": matrix,
        },
        "gates": {
            "input_integrity": integrity_ok,
            "all_primary_items_adjudicated": len(item_rows) == expected_primary,
            "all_changed_items_adjudicated": sum(
                1 for row in item_rows if row["stratum"] in changed_strata
            ) == int(scope["changed_items"]),
            "repeat_consistency": repeat_consistency >= float(policy["truth"]["repeat_consistency_floor"]),
            **advance_gates,
        },
        "limitations": {
            "direct_identity_labels": len(positive_rows),
            "exemplar_purity_directly_graded": False,
            "fail_closed_rows_are_not_positive_identity_truth": True,
            "candidate_thresholds_were_not_tuned": True,
        },
        "integrity_failures": sorted(set(failures)),
        "safety": {
            "shadow_only": True,
            "production_mutated": False,
            "coverage_v3_mutated": False,
            "selected_transcript_mutated": False,
            "raw_audio_mutated": False,
            "primary_asr_mutated": False,
            "echo_guard_mutated": False,
            "thresholds_tuned": False,
            "cross_session_identity_used": False,
            "speech_text_public": False,
            "session_ids_public": False,
        },
    }
    source_fingerprint = hashlib.sha256(
        canonical([(row["id"], row.get("sha256")) for row in verified_sources])
    ).hexdigest()
    public_input = {
        "schema": "murmurmark.remote_speaker_direct_truth_candidate_adjudication_input/v1",
        "source_fingerprint": source_fingerprint,
        "sources": verified_sources,
        "frozen_artifacts": {
            "expected": int(scope["pack_artifacts"]),
            "verified": sum(row["status"] == "verified" for row in frozen_rows),
            "all_verified": not frozen_failures,
        },
        "inherited_production_guards": {
            "expected": int(scope["inherited_artifacts"]),
            "verified": sum(row["status"] == "verified" for row in inherited_rows),
            "all_verified": not inherited_failures,
        },
    }
    return core, item_rows, public_input


def public_report(core: dict[str, Any], replay_verified: bool) -> dict[str, Any]:
    report = {key: value for key, value in core.items() if key not in {"schema"}}
    report["schema"] = REPORT_SCHEMA
    report["replay_verified"] = replay_verified
    report["portable_aggregate"] = {
        "decision": report["decision"],
        "direct_identity_items": report.get("scope", {}).get("attributed_primary_items", 0),
        "repeat_consistency": report.get("repeat_review", {}).get("consistency"),
        "control_correct_identity_items": report.get("control", {}).get("items", {}).get("correct_identity", 0),
        "candidate_correct_identity_items": report.get("candidate", {}).get("items", {}).get("correct_identity", 0),
        "gained_correct_identity_items": report.get("comparison", {}).get("gained_correct_identity_items", 0),
        "lost_correct_control_identity_items": report.get("comparison", {}).get("lost_correct_control_identity_items", 0),
        "control_fail_closed_unsafe_acceptance_items": report.get("comparison", {}).get(
            "control_fail_closed_unsafe_acceptance_items", 0
        ),
        "candidate_fail_closed_unsafe_acceptance_items": report.get("comparison", {}).get(
            "candidate_fail_closed_unsafe_acceptance_items", 0
        ),
        "production_promoted": False,
    }
    return report


def markdown(report: dict[str, Any]) -> str:
    aggregate = report["portable_aggregate"]
    failed = [name for name, passed in report.get("gates", {}).items() if not passed]
    lines = [
        "# Remote Speaker Direct-Truth Candidate Adjudication v1",
        "",
        f"Decision: `{report['decision']}`",
        "",
        "## Direct Evidence",
        "",
        f"- primary items: `{report['scope']['primary_items']}`; direct identity: `{aggregate['direct_identity_items']}`;",
        f"- hidden repeats: `{report['repeat_review']['matches']}/{report['repeat_review']['compared']}` = `{aggregate['repeat_consistency']}`;",
        f"- control correct identities: `{aggregate['control_correct_identity_items']}`; candidate: `{aggregate['candidate_correct_identity_items']}`;",
        f"- candidate gained `{aggregate['gained_correct_identity_items']}` correct identities and lost `{aggregate['lost_correct_control_identity_items']}` correct control identities;",
        f"- unsafe accepts on fail-closed truth: control `{aggregate['control_fail_closed_unsafe_acceptance_items']}`, candidate `{aggregate['candidate_fail_closed_unsafe_acceptance_items']}`.",
        "",
        "## Decision",
        "",
    ]
    if report["decision"] == "ADVANCE_DIRECT_TRUTH_IDENTITY":
        lines.append("The frozen candidate earned a separate corpus-wide identity qualification. Production is unchanged.")
    elif report["decision"] == "KEEP_COVERAGE_V3":
        lines.append("Coverage v3 remains the safer control. The frozen enrollment candidate is closed without production promotion.")
    else:
        lines.append("The comparison is evidence-bound. Only provenance or bounded truth acquisition may continue.")
    lines.extend([
        "",
        "## Gates",
        "",
        f"Failed gates: `{', '.join(failed) if failed else 'none'}`.",
        f"Deterministic replay: `{'passed' if report.get('replay_verified') else 'not verified'}`.",
        "",
        "`unknown_speaker`, `mixed` and `unusable` are abstention evidence only. No speech text, session IDs, human names or reviewer identity are published.",
        "",
    ])
    return "\n".join(lines)


def evaluate(policy: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    core, rows, public_input = evaluate_core(policy)
    private = out_dir / "private"
    write_json(private / "evaluation_core.json", core)
    write_jsonl(private / "item_adjudication.jsonl", rows)
    write_json(out_dir / "input_manifest.public.json", public_input)
    return core


def replay(policy: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    expected_core = out_dir / "private/evaluation_core.json"
    expected_items = out_dir / "private/item_adjudication.jsonl"
    if not expected_core.is_file() or not expected_items.is_file():
        raise ValueError("evaluate before replay")
    with tempfile.TemporaryDirectory(prefix=".direct-truth-adjudication-replay-", dir=ROOT) as temporary:
        replay_dir = Path(temporary) / "out"
        evaluate(policy, replay_dir)
        matched = (
            expected_core.read_bytes() == (replay_dir / "private/evaluation_core.json").read_bytes()
            and expected_items.read_bytes() == (replay_dir / "private/item_adjudication.jsonl").read_bytes()
        )
    result = {
        "schema": REPLAY_SCHEMA,
        "matched": matched,
        "core_sha256": sha256(expected_core),
        "items_sha256": sha256(expected_items),
    }
    write_json(out_dir / "replay_report.json", result)
    return result


def finalize(policy_path: Path, policy: dict[str, Any], out_dir: Path, manifest_path: Path | None) -> dict[str, Any]:
    core = read_json(out_dir / "private/evaluation_core.json")
    replay_result = read_json(out_dir / "replay_report.json")
    replay_verified = replay_result.get("matched") is True
    if not replay_verified:
        core["decision"] = "EVIDENCE_BOUND"
        core.setdefault("integrity_failures", []).append("deterministic_replay")
        core.setdefault("gates", {})["deterministic_replay"] = False
    else:
        core.setdefault("gates", {})["deterministic_replay"] = True
    report = public_report(core, replay_verified)
    report_path = out_dir / "remote_speaker_direct_truth_candidate_adjudication_report.json"
    markdown_path = out_dir / "remote_speaker_direct_truth_candidate_adjudication_report.md"
    write_json(report_path, report)
    atomic_write(markdown_path, markdown(report).encode())
    if manifest_path:
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "decision": report["decision"],
            "scope": report.get("scope"),
            "comparison": report.get("comparison"),
            "gates": report.get("gates"),
            "limitations": report.get("limitations"),
            "safety": report.get("safety"),
            "replay_verified": replay_verified,
            "artifacts": {
                "policy": artifact(policy_path, "policy"),
                "public_input_manifest": artifact(out_dir / "input_manifest.public.json", "public_input_manifest"),
                "report": artifact(report_path, "report"),
                "markdown_report": artifact(markdown_path, "markdown_report"),
                "replay_report": artifact(out_dir / "replay_report.json", "replay_report"),
            },
        }
        write_json(manifest_path, manifest)
    return report


def status(out_dir: Path) -> int:
    report_path = out_dir / "remote_speaker_direct_truth_candidate_adjudication_report.json"
    if not report_path.is_file():
        print("decision: NOT_EVALUATED")
        return 2
    report = read_json(report_path)
    aggregate = report["portable_aggregate"]
    print(f"decision: {report['decision']}")
    print(f"direct identity items: {aggregate['direct_identity_items']}")
    print(f"correct identities: control={aggregate['control_correct_identity_items']} candidate={aggregate['candidate_correct_identity_items']}")
    print(f"lost correct controls: {aggregate['lost_correct_control_identity_items']}")
    print(
        "fail-closed unsafe accepts: "
        f"control={aggregate['control_fail_closed_unsafe_acceptance_items']} "
        f"candidate={aggregate['candidate_fail_closed_unsafe_acceptance_items']}"
    )
    print(f"replay verified: {report['replay_verified']}")
    return 2 if report["decision"] == "EVIDENCE_BOUND" else 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=("preflight", "evaluate", "status", "replay", "finalize", "all"))
    result.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    result.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    result.add_argument("--write-manifest", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    policy_path = args.policy.resolve()
    out_dir = args.out_dir.resolve()
    policy = load_policy(policy_path)
    try:
        if args.action == "preflight":
            core, _, public_input = evaluate_core(policy)
            print(json.dumps({"decision": core["decision"], "input": public_input["frozen_artifacts"], "failures": core["integrity_failures"]}, sort_keys=True))
            return 2 if core["decision"] == "EVIDENCE_BOUND" else 0
        if args.action in {"evaluate", "all"}:
            evaluate(policy, out_dir)
        if args.action in {"replay", "all"}:
            replay(policy, out_dir)
        if args.action in {"finalize", "all"}:
            report = finalize(policy_path, policy, out_dir, args.write_manifest)
            print(json.dumps({"decision": report["decision"], "report": str(out_dir)}, sort_keys=True))
            return 2 if report["decision"] == "EVIDENCE_BOUND" else 0
        if args.action == "status":
            return status(out_dir)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
