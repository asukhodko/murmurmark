#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


SCRIPT_VERSION = "0.2.0"
SCHEMA_REPORT = "murmurmark.audio_judge_v0_report/v1"
SCHEMA_PREDICTION = "murmurmark.audio_judge_v0_prediction/v1"
SCHEMA_CV_PREDICTION = "murmurmark.audio_judge_v0_cv_prediction/v1"

TRAINING_BUCKETS = {
    "silver_cleanup_positive": "drop_error",
    "weak_cleanup_positive": "drop_error",
    "mark_only_regression": "mark_only_error",
    "needs_audio_judge": "uncertain",
    "silver_keep_negative": "keep",
}
FEATURE_NAMES = [
    "confidence",
    "duration_sec",
    "score_local_support",
    "score_remote_similarity",
    "score_remote_duplicate",
    "score_remote_leak",
    "score_asr_noise",
    "score_double_talk",
    "score_timing_overlap",
    "score_lost_me",
    "score_likely_reliable",
    "text_similarity",
    "text_containment",
    "text_sequence_ratio",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and evaluate a local audio judge v0 on the regression corpus.")
    parser.add_argument("--corpus-dir", type=Path, default=Path("sessions/_reports/regression-corpus"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/audio-judge-v0"))
    parser.add_argument(
        "--operational-readiness",
        type=Path,
        default=Path("sessions/_reports/operational-readiness/operational_readiness_report.json"),
    )
    parser.add_argument("--min-confidence", type=float, default=0.58)
    return parser.parse_args()


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


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def safe_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def one_hot(value: str, expected: str) -> float:
    return 1.0 if value == expected else 0.0


def feature_vector(item: dict[str, Any]) -> list[float]:
    scores = item.get("scores") if isinstance(item.get("scores"), dict) else {}
    text = item.get("text_features") if isinstance(item.get("text_features"), dict) else {}
    interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
    values = [
        safe_float(item.get("confidence")),
        safe_float(interval.get("duration_sec")),
        safe_float(scores.get("local_support")),
        safe_float(scores.get("remote_similarity")),
        safe_float(scores.get("remote_duplicate")),
        safe_float(scores.get("remote_leak")),
        safe_float(scores.get("asr_noise")),
        safe_float(scores.get("double_talk")),
        safe_float(scores.get("timing_overlap")),
        safe_float(scores.get("lost_me")),
        safe_float(scores.get("likely_reliable")),
        safe_float(text.get("similarity")),
        safe_float(text.get("containment")),
        safe_float(text.get("sequence_ratio")),
    ]
    return values


def training_label(item: dict[str, Any]) -> str | None:
    bucket = str(item.get("readiness_bucket") or "")
    return TRAINING_BUCKETS.get(bucket)


def load_training(corpus_dir: Path) -> tuple[list[dict[str, Any]], np.ndarray, np.ndarray, np.ndarray]:
    corpus_items = {str(item.get("id")): item for item in read_jsonl(corpus_dir / "regression_corpus_items.jsonl")}
    eval_items = read_jsonl(corpus_dir / "regression_corpus_evaluation_items.jsonl")
    rows: list[dict[str, Any]] = []
    features: list[list[float]] = []
    labels: list[str] = []
    groups: list[str] = []
    for eval_item in eval_items:
        item_id = str(eval_item.get("id") or "")
        corpus_item = corpus_items.get(item_id)
        if not corpus_item:
            continue
        label = training_label(eval_item)
        if not label:
            continue
        merged = dict(corpus_item)
        merged["readiness_bucket"] = eval_item.get("readiness_bucket")
        merged["training_label"] = label
        rows.append(merged)
        features.append(feature_vector(merged))
        labels.append(label)
        groups.append(str(merged.get("session_id") or "unknown"))
    if not rows:
        raise SystemExit(f"no trainable rows in {corpus_dir}")
    return rows, np.asarray(features, dtype=np.float64), np.asarray(labels), np.asarray(groups)


def make_model() -> Pipeline:
    return Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "clf",
                LogisticRegression(
                    max_iter=1000,
                    class_weight="balanced",
                    solver="lbfgs",
                    random_state=0,
                ),
            ),
        ]
    )


def probability_map(classes: np.ndarray, probabilities: np.ndarray) -> dict[str, float]:
    return {str(label): round(float(prob), 6) for label, prob in zip(classes, probabilities)}


def predict_rows(model: Pipeline, rows: list[dict[str, Any]], features: np.ndarray, min_confidence: float) -> list[dict[str, Any]]:
    classes = model.named_steps["clf"].classes_
    probabilities = model.predict_proba(features)
    predictions = model.predict(features)
    output: list[dict[str, Any]] = []
    for row, predicted, probs in zip(rows, predictions, probabilities):
        confidence = float(max(probs))
        judge_label = str(predicted) if confidence >= min_confidence else "uncertain"
        output.append(
            {
                "schema": SCHEMA_PREDICTION,
                "id": row.get("id"),
                "session_id": row.get("session_id"),
                "source_audit_id": row.get("source_audit_id"),
                "corpus_label": row.get("label"),
                "readiness_bucket": row.get("readiness_bucket"),
                "training_label": row.get("training_label"),
                "judge_label": judge_label,
                "judge_confidence": round(confidence, 6),
                "probabilities": probability_map(classes, probs),
                "interval": row.get("interval"),
                "utterance_ids": row.get("utterance_ids", []),
                "commands": row.get("commands", {}),
            }
        )
    return output


def cross_validated_outputs(
    features: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    classes: list[str],
) -> tuple[np.ndarray, np.ndarray, str]:
    probabilities = np.zeros((len(labels), len(classes)), dtype=np.float64)
    predictions = np.asarray(labels.copy())
    unique_groups = sorted(set(groups))
    if len(unique_groups) < 2 or min(Counter(labels).values()) < 2:
        for index, label in enumerate(labels):
            probabilities[index, classes.index(str(label))] = 1.0
        return predictions, probabilities, "identity"

    logo = LeaveOneGroupOut()
    for train_index, test_index in logo.split(features, labels, groups=groups):
        train_labels = labels[train_index]
        if len(set(train_labels)) < 2:
            majority = Counter(str(label) for label in train_labels).most_common(1)[0][0]
            predictions[test_index] = majority
            probabilities[test_index, classes.index(majority)] = 1.0
            continue
        fold_model = make_model()
        fold_model.fit(features[train_index], train_labels)
        fold_predictions = fold_model.predict(features[test_index])
        fold_probabilities = fold_model.predict_proba(features[test_index])
        fold_classes = [str(item) for item in fold_model.named_steps["clf"].classes_]
        predictions[test_index] = fold_predictions
        for local_column, class_name in enumerate(fold_classes):
            probabilities[test_index, classes.index(class_name)] = fold_probabilities[:, local_column]
    return predictions, probabilities, "leave_one_session_out"


def cv_prediction_rows(
    rows: list[dict[str, Any]],
    labels: np.ndarray,
    cv_pred: np.ndarray,
    cv_probabilities: np.ndarray,
    classes: list[str],
    min_confidence: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row, expected, predicted, probs in zip(rows, labels, cv_pred, cv_probabilities):
        confidence = float(max(probs)) if len(probs) else 0.0
        policy_label = str(predicted) if confidence >= min_confidence else "uncertain"
        output.append(
            {
                "schema": SCHEMA_CV_PREDICTION,
                "id": row.get("id"),
                "session_id": row.get("session_id"),
                "source_audit_id": row.get("source_audit_id"),
                "corpus_label": row.get("label"),
                "readiness_bucket": row.get("readiness_bucket"),
                "training_label": str(expected),
                "cv_label": str(predicted),
                "policy_label": policy_label,
                "cv_confidence": round(confidence, 6),
                "cv_correct": str(predicted) == str(expected),
                "policy_correct": policy_label == str(expected),
                "probabilities": probability_map(np.asarray(classes), probs),
                "interval": row.get("interval"),
                "utterance_ids": row.get("utterance_ids", []),
                "commands": row.get("commands", {}),
            }
        )
    return output


def accuracy(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return round(sum(1 for row in rows if row.get(key) is True) / len(rows), 6)


def session_evaluation(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_session[str(row.get("session_id") or "unknown")].append(row)
    output: list[dict[str, Any]] = []
    for session_id, items in sorted(by_session.items()):
        errors = [row for row in items if row.get("cv_correct") is not True]
        policy_errors = [row for row in items if row.get("policy_correct") is not True]
        output.append(
            {
                "session_id": session_id,
                "items": len(items),
                "cv_accuracy": accuracy(items, "cv_correct"),
                "policy_accuracy": accuracy(items, "policy_correct"),
                "cv_errors": len(errors),
                "policy_errors": len(policy_errors),
                "labels": dict(sorted(Counter(str(row.get("training_label")) for row in items).items())),
                "predicted": dict(sorted(Counter(str(row.get("cv_label")) for row in items).items())),
                "top_errors": [
                    {
                        "id": row.get("id"),
                        "expected": row.get("training_label"),
                        "predicted": row.get("cv_label"),
                        "confidence": row.get("cv_confidence"),
                        "source_audit_id": row.get("source_audit_id"),
                    }
                    for row in sorted(errors, key=lambda item: -safe_float(item.get("cv_confidence")))[:5]
                ],
            }
        )
    output.sort(key=lambda item: (item["cv_accuracy"], -item["items"], item["session_id"]))
    return output


def confidence_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bounds = [
        (0.0, 0.6, "0.00-0.60"),
        (0.6, 0.75, "0.60-0.75"),
        (0.75, 0.9, "0.75-0.90"),
        (0.9, 1.000001, "0.90-1.00"),
    ]
    output: list[dict[str, Any]] = []
    for lower, upper, label in bounds:
        items = [row for row in rows if lower <= safe_float(row.get("cv_confidence")) < upper]
        output.append(
            {
                "bucket": label,
                "items": len(items),
                "cv_accuracy": accuracy(items, "cv_correct"),
                "policy_accuracy": accuracy(items, "policy_correct"),
                "errors": sum(1 for row in items if row.get("cv_correct") is not True),
                "labels": dict(sorted(Counter(str(row.get("training_label")) for row in items).items())),
                "predicted": dict(sorted(Counter(str(row.get("cv_label")) for row in items).items())),
            }
        )
    return output


def precision_recall_for_label(rows: list[dict[str, Any]], predicted_label: str, expected_label: str, threshold: float) -> dict[str, Any]:
    predicted = [
        row
        for row in rows
        if row.get("cv_label") == predicted_label and safe_float(row.get("cv_confidence")) >= threshold
    ]
    true_positive = [row for row in predicted if row.get("training_label") == expected_label]
    expected = [row for row in rows if row.get("training_label") == expected_label]
    return {
        "predicted_label": predicted_label,
        "expected_label": expected_label,
        "confidence_threshold": threshold,
        "candidates": len(predicted),
        "true_positive": len(true_positive),
        "expected_total": len(expected),
        "precision": round(len(true_positive) / len(predicted), 6) if predicted else None,
        "recall": round(len(true_positive) / len(expected), 6) if expected else None,
    }


def high_confidence_errors(rows: list[dict[str, Any]], limit: int = 25) -> list[dict[str, Any]]:
    errors = [row for row in rows if row.get("cv_correct") is not True]
    output: list[dict[str, Any]] = []
    for row in sorted(errors, key=lambda item: -safe_float(item.get("cv_confidence")))[:limit]:
        commands = row.get("commands") if isinstance(row.get("commands"), dict) else {}
        output.append(
            {
                "id": row.get("id"),
                "session_id": row.get("session_id"),
                "source_audit_id": row.get("source_audit_id"),
                "expected": row.get("training_label"),
                "predicted": row.get("cv_label"),
                "policy_label": row.get("policy_label"),
                "confidence": row.get("cv_confidence"),
                "interval": row.get("interval"),
                "utterance_ids": row.get("utterance_ids", []),
                "stereo_command": commands.get("stereo_clean_left_remote_right")
                or commands.get("stereo_mic_left_remote_right"),
            }
        )
    return output


def build_evaluation_detail(cv_rows: list[dict[str, Any]]) -> dict[str, Any]:
    thresholds = [0.75, 0.85, 0.93]
    cleanup_eval = [
        precision_recall_for_label(cv_rows, "drop_error", "drop_error", threshold)
        for threshold in thresholds
    ]
    keep_eval = [
        precision_recall_for_label(cv_rows, "keep", "keep", threshold)
        for threshold in thresholds
    ]
    return {
        "policy_accuracy": accuracy(cv_rows, "policy_correct"),
        "per_session": session_evaluation(cv_rows),
        "confidence_buckets": confidence_buckets(cv_rows),
        "high_confidence_errors": high_confidence_errors(cv_rows),
        "cleanup_precision_by_threshold": cleanup_eval,
        "keep_precision_by_threshold": keep_eval,
    }


def role_from_utterance(row: dict[str, Any]) -> str:
    role = str(row.get("role") or "").lower()
    source = str(row.get("source_track") or "").lower()
    if role == "me" or source == "mic":
        return "me"
    if "colleague" in role or source == "remote":
        return "remote"
    return role or source


def text_features_from_utterances(utterances: list[dict[str, Any]]) -> dict[str, Any]:
    for row in utterances:
        if not isinstance(row, dict):
            continue
    me_text = " ".join(str(row.get("text") or "") for row in utterances if isinstance(row, dict) and role_from_utterance(row) == "me")
    remote_text = " ".join(str(row.get("text") or "") for row in utterances if isinstance(row, dict) and role_from_utterance(row) == "remote")
    return {"me_text": me_text, "remote_text": remote_text}


def queue_feature_item(session_path: Path, queue_item: dict[str, Any]) -> dict[str, Any] | None:
    audit_id = str(queue_item.get("source_audit_id") or "")
    if not audit_id:
        return None
    audit_rows = read_jsonl(session_path / "derived/audit/audio-review-pack/audio_review_audit.jsonl")
    source = next((row for row in audit_rows if str(row.get("id") or "") == audit_id), None)
    if not source:
        return None
    classification = source.get("classification") if isinstance(source.get("classification"), dict) else {}
    features = source.get("features") if isinstance(source.get("features"), dict) else {}
    text = features.get("text") if isinstance(features.get("text"), dict) else text_features_from_utterances(source.get("utterances") or [])
    return {
        "id": f"queue_{queue_item.get('session_id')}_{audit_id}",
        "session_id": queue_item.get("session_id"),
        "source_audit_id": audit_id,
        "label": classification.get("label"),
        "verdict": classification.get("verdict"),
        "confidence": classification.get("confidence"),
        "interval": source.get("interval", queue_item.get("interval")),
        "scores": source.get("scores") if isinstance(source.get("scores"), dict) else {},
        "text_features": text,
        "utterance_ids": source.get("utterance_ids", queue_item.get("utterance_ids", [])),
        "commands": source.get("commands", queue_item.get("commands", {})),
    }


def load_review_queue(path: Path) -> list[dict[str, Any]]:
    report = read_json(path)
    if not report:
        return []
    rows: list[dict[str, Any]] = []
    for item in report.get("review_queue") or []:
        if not isinstance(item, dict):
            continue
        session_path = Path(str(item.get("session") or ""))
        if not session_path.exists():
            continue
        feature_item = queue_feature_item(session_path, item)
        if feature_item:
            rows.append(feature_item)
    return rows


def predict_queue(model: Pipeline, rows: list[dict[str, Any]], min_confidence: float) -> list[dict[str, Any]]:
    if not rows:
        return []
    features = np.asarray([feature_vector(row) for row in rows], dtype=np.float64)
    classes = model.named_steps["clf"].classes_
    probabilities = model.predict_proba(features)
    predictions = model.predict(features)
    output: list[dict[str, Any]] = []
    for row, predicted, probs in zip(rows, predictions, probabilities):
        confidence = float(max(probs))
        judge_label = str(predicted) if confidence >= min_confidence else "uncertain"
        output.append(
            {
                "schema": "murmurmark.audio_judge_v0_queue_prediction/v1",
                "id": row.get("id"),
                "session_id": row.get("session_id"),
                "source_audit_id": row.get("source_audit_id"),
                "audio_review_label": row.get("label"),
                "audio_review_verdict": row.get("verdict"),
                "judge_label": judge_label,
                "judge_confidence": round(confidence, 6),
                "probabilities": probability_map(classes, probs),
                "interval": row.get("interval"),
                "utterance_ids": row.get("utterance_ids", []),
                "commands": row.get("commands", {}),
                "shadow_action": shadow_action(judge_label, confidence),
            }
        )
    output.sort(key=lambda item: (-safe_float(item.get("judge_confidence")), str(item.get("session_id") or "")))
    return output


def shadow_action(judge_label: str, confidence: float) -> str:
    if confidence < 0.75:
        return "keep_in_human_review_queue"
    if judge_label == "drop_error":
        return "candidate_future_cleanup_review"
    if judge_label == "keep":
        return "candidate_remove_from_review_queue"
    if judge_label == "mark_only_error":
        return "candidate_mark_only_review"
    return "keep_in_human_review_queue"


def queue_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_label = Counter(str(row.get("judge_label") or "unknown") for row in rows)
    by_action = Counter(str(row.get("shadow_action") or "unknown") for row in rows)
    removable = by_action.get("candidate_remove_from_review_queue", 0)
    candidate_drop = by_action.get("candidate_future_cleanup_review", 0)
    mark_only = by_action.get("candidate_mark_only_review", 0)
    direct_review = by_action.get("keep_in_human_review_queue", 0)
    remaining_review = len(rows) - removable
    return {
        "items": len(rows),
        "by_judge_label": dict(sorted(by_label.items())),
        "by_shadow_action": dict(sorted(by_action.items())),
        "candidate_review_reduction_items": removable,
        "candidate_future_cleanup_items": candidate_drop,
        "candidate_mark_only_items": mark_only,
        "direct_human_review_items": direct_review,
        "remaining_human_review_items": remaining_review,
    }


def top_features(model: Pipeline, class_name: str, limit: int = 8) -> list[dict[str, Any]]:
    clf: LogisticRegression = model.named_steps["clf"]
    if class_name not in clf.classes_:
        return []
    index = list(clf.classes_).index(class_name)
    coef = clf.coef_[index]
    pairs = sorted(zip(FEATURE_NAMES, coef), key=lambda item: abs(float(item[1])), reverse=True)[:limit]
    return [{"feature": name, "weight": round(float(weight), 6)} for name, weight in pairs]


def evaluate_predictions(labels: np.ndarray, predictions: np.ndarray, classes: list[str]) -> dict[str, Any]:
    matrix = confusion_matrix(labels, predictions, labels=classes)
    report = classification_report(labels, predictions, labels=classes, output_dict=True, zero_division=0)
    return {
        "classes": classes,
        "confusion_matrix": matrix.tolist(),
        "classification_report": report,
    }


def write_markdown(
    path: Path,
    report: dict[str, Any],
    predictions: list[dict[str, Any]],
    cv_predictions: list[dict[str, Any]],
    queue_predictions: list[dict[str, Any]],
) -> None:
    lines = [
        "# Audio Judge v0",
        "",
        f"Readiness: `{report['readiness']}`",
        f"Training rows: `{report['training']['rows']}`",
        f"Sessions: `{report['training']['sessions']}`",
        f"CV accuracy: `{report['evaluation']['cv_accuracy']}`",
        f"Policy accuracy: `{report['evaluation']['policy_accuracy']}`",
        f"Queue items: `{report['review_queue']['items']}`",
        "",
        "## Label Counts",
        "",
    ]
    for label, count in report["training"]["label_counts"].items():
        lines.append(f"- `{label}`: `{count}`")
    lines.extend(["", "## Top Features", ""])
    for label, features in report["top_features"].items():
        lines.append(f"### `{label}`")
        for item in features:
            lines.append(f"- `{item['feature']}`: `{item['weight']}`")
        lines.append("")
    lines.extend(["", "## Out-of-Fold Evaluation", ""])
    lines.extend(
        [
            "| Session | Items | CV accuracy | Policy accuracy | CV errors |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in report["evaluation_detail"]["per_session"]:
        lines.append(
            f"| `{item['session_id']}` | `{item['items']}` | `{item['cv_accuracy']}` | `{item['policy_accuracy']}` | `{item['cv_errors']}` |"
        )
    lines.extend(["", "### Confidence Buckets", ""])
    lines.extend(
        [
            "| Confidence | Items | CV accuracy | Policy accuracy | Errors |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in report["evaluation_detail"]["confidence_buckets"]:
        lines.append(
            f"| `{item['bucket']}` | `{item['items']}` | `{item['cv_accuracy']}` | `{item['policy_accuracy']}` | `{item['errors']}` |"
        )
    lines.extend(["", "### Cleanup Precision by Threshold", ""])
    for item in report["evaluation_detail"]["cleanup_precision_by_threshold"]:
        lines.append(
            f"- `drop_error >= {item['confidence_threshold']}`: candidates `{item['candidates']}`, precision `{item['precision']}`, recall `{item['recall']}`"
        )
    lines.extend(["", "### High-Confidence CV Errors", ""])
    for row in report["evaluation_detail"]["high_confidence_errors"][:15]:
        lines.extend(
            [
                f"#### {row['id']} `{row['expected']}` -> `{row['predicted']}`",
                "",
                f"- Session: `{row['session_id']}`",
                f"- Confidence: `{row['confidence']}`",
                f"- Audit id: `{row['source_audit_id']}`",
            ]
        )
        if row.get("stereo_command"):
            lines.append(f"- Stereo: `{row['stereo_command']}`")
        lines.append("")
    lines.extend(["", "## Highest-Confidence Predictions", ""])
    for row in sorted(predictions, key=lambda item: -float(item.get("judge_confidence", 0.0)))[:25]:
        lines.extend(
            [
                f"### {row['id']} `{row['judge_label']}` {row['session_id']}",
                "",
                f"- Confidence: `{row['judge_confidence']}`",
                f"- Training label: `{row['training_label']}`",
                f"- Corpus label: `{row['corpus_label']}`",
            ]
        )
        commands = row.get("commands") or {}
        stereo = commands.get("stereo_clean_left_remote_right") or commands.get("stereo_mic_left_remote_right")
        if stereo:
            lines.append(f"- Stereo: `{stereo}`")
        lines.append("")
    lines.extend(["", "## Highest-Confidence Out-of-Fold Predictions", ""])
    for row in sorted(cv_predictions, key=lambda item: -float(item.get("cv_confidence", 0.0)))[:25]:
        lines.extend(
            [
                f"### {row['id']} `{row['cv_label']}` {row['session_id']}",
                "",
                f"- Confidence: `{row['cv_confidence']}`",
                f"- Expected: `{row['training_label']}`",
                f"- Correct: `{row['cv_correct']}`",
            ]
        )
        commands = row.get("commands") or {}
        stereo = commands.get("stereo_clean_left_remote_right") or commands.get("stereo_mic_left_remote_right")
        if stereo:
            lines.append(f"- Stereo: `{stereo}`")
        lines.append("")
    lines.extend(["", "## Review Queue Shadow Predictions", ""])
    for row in queue_predictions[:25]:
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        commands = row.get("commands") or {}
        stereo = commands.get("stereo_clean_left_remote_right") or commands.get("stereo_mic_left_remote_right")
        lines.extend(
            [
                f"### {row['session_id']} `{row['judge_label']}` {interval.get('start_time', '')}-{interval.get('end_time', '')}",
                "",
                f"- Confidence: `{row['judge_confidence']}`",
                f"- Shadow action: `{row['shadow_action']}`",
                f"- Audio review: `{row.get('audio_review_label')}` / `{row.get('audio_review_verdict')}`",
                f"- Audit id: `{row.get('source_audit_id')}`",
            ]
        )
        if stereo:
            lines.append(f"- Stereo: `{stereo}`")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    rows, features, labels, groups = load_training(args.corpus_dir)
    unique_groups = sorted(set(groups))
    classes = sorted(set(labels))
    model = make_model()
    cv_pred, cv_probabilities, cv_method = cross_validated_outputs(features, labels, groups, classes)
    cv_predictions = cv_prediction_rows(rows, labels, cv_pred, cv_probabilities, classes, args.min_confidence)
    evaluation_detail = build_evaluation_detail(cv_predictions)
    evaluation = evaluate_predictions(labels, cv_pred, classes)
    cv_accuracy = round(float(np.mean(cv_pred == labels)), 6)
    model.fit(features, labels)
    predictions = predict_rows(model, rows, features, args.min_confidence)
    queue_rows = load_review_queue(args.operational_readiness)
    queue_predictions = predict_queue(model, queue_rows, args.min_confidence)
    label_counts = Counter(str(label) for label in labels)
    judge_counts = Counter(row["judge_label"] for row in predictions)
    readiness = "experimental"
    if cv_accuracy >= 0.70 and len(rows) >= 50 and len(unique_groups) >= 5:
        readiness = "shadow_ready"
    if cv_accuracy >= 0.82 and len(rows) >= 100 and len(unique_groups) >= 8:
        readiness = "cleanup_shadow_candidate"
    report = {
        "schema": SCHEMA_REPORT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "train-audio-judge-v0", "version": SCRIPT_VERSION},
        "inputs": {
            "corpus_dir": str(args.corpus_dir),
            "items": str(args.corpus_dir / "regression_corpus_items.jsonl"),
            "evaluation_items": str(args.corpus_dir / "regression_corpus_evaluation_items.jsonl"),
        },
        "readiness": readiness,
        "training": {
            "rows": len(rows),
            "sessions": len(unique_groups),
            "label_counts": dict(sorted(label_counts.items())),
            "feature_names": FEATURE_NAMES,
        },
        "evaluation": {
            "method": cv_method,
            "cv_accuracy": cv_accuracy,
            "policy_accuracy": evaluation_detail["policy_accuracy"],
            **evaluation,
        },
        "evaluation_detail": evaluation_detail,
        "judge_label_counts": dict(sorted(judge_counts.items())),
        "review_queue": queue_summary(queue_predictions),
        "top_features": {label: top_features(model, label) for label in classes},
        "policy": {
            "min_confidence": args.min_confidence,
            "mode": "shadow_only",
            "may_modify_transcript": False,
        },
    }
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "audio_judge_v0_report.json", report)
    write_jsonl(out_dir / "audio_judge_v0_predictions.jsonl", predictions)
    write_jsonl(out_dir / "audio_judge_v0_cv_predictions.jsonl", cv_predictions)
    write_jsonl(out_dir / "audio_judge_v0_queue_predictions.jsonl", queue_predictions)
    write_markdown(out_dir / "audio_judge_v0_report.md", report, predictions, cv_predictions, queue_predictions)
    print(f"readiness: {readiness}")
    print(f"cv_accuracy: {cv_accuracy}")
    print(f"written: {out_dir / 'audio_judge_v0_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
