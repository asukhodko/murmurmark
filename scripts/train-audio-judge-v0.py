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
from sklearn.model_selection import LeaveOneGroupOut, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


SCRIPT_VERSION = "0.1.0"
SCHEMA_REPORT = "murmurmark.audio_judge_v0_report/v1"
SCHEMA_PREDICTION = "murmurmark.audio_judge_v0_prediction/v1"

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


def write_markdown(path: Path, report: dict[str, Any], predictions: list[dict[str, Any]]) -> None:
    lines = [
        "# Audio Judge v0",
        "",
        f"Readiness: `{report['readiness']}`",
        f"Training rows: `{report['training']['rows']}`",
        f"Sessions: `{report['training']['sessions']}`",
        f"CV accuracy: `{report['evaluation']['cv_accuracy']}`",
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
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    rows, features, labels, groups = load_training(args.corpus_dir)
    unique_groups = sorted(set(groups))
    classes = sorted(set(labels))
    model = make_model()
    if len(unique_groups) >= 2 and min(Counter(labels).values()) >= 2:
        logo = LeaveOneGroupOut()
        cv_pred = cross_val_predict(model, features, labels, groups=groups, cv=logo)
    else:
        cv_pred = labels.copy()
    evaluation = evaluate_predictions(labels, cv_pred, classes)
    cv_accuracy = round(float(np.mean(cv_pred == labels)), 6)
    model.fit(features, labels)
    predictions = predict_rows(model, rows, features, args.min_confidence)
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
            "method": "leave_one_session_out" if len(unique_groups) >= 2 else "identity",
            "cv_accuracy": cv_accuracy,
            **evaluation,
        },
        "judge_label_counts": dict(sorted(judge_counts.items())),
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
    write_markdown(out_dir / "audio_judge_v0_report.md", report, predictions)
    print(f"readiness: {readiness}")
    print(f"cv_accuracy: {cv_accuracy}")
    print(f"written: {out_dir / 'audio_judge_v0_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
