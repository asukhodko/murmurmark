#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path


def load_module():
    path = Path(__file__).with_name("residual-audio-arbitration.py")
    spec = importlib.util.spec_from_file_location("murmurmark_check_residual_audio", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


def calibration(status: str = "reliable") -> dict:
    return {
        "status": status,
        "thresholds": {"keep": 0.70, "weak": 0.55, "positive_p50_minus_negative_p90": 0.20},
    }


def voice(mic: float, remote: float) -> dict:
    return {
        "mic_clean:whole": {"positive_similarity": mic},
        "mic_raw:whole": {"positive_similarity": mic - 0.01},
        "mic_role_masked:whole": {"positive_similarity": mic},
        "remote:whole": {"positive_similarity": remote},
    }


def transcripts(me: str, remote: str) -> dict:
    return {
        "mic_clean:judge_context": {"text": me},
        "mic_raw:judge_context": {"text": me},
        "mic_role_masked:judge_context": {"text": me},
        "remote:judge_context": {"text": remote},
    }


def queue(text: str, detail: dict | None = None) -> dict:
    return {
        "source": "audio_review",
        "source_audit_id": "arp_000001",
        "me_utterance_ids": ["utt_me_1"],
        "target_text": text,
        "source_detail": {"classification": detail or {}},
    }


def judge(label: str, confidence: float) -> dict:
    return {"classification": {"label": label, "confidence": confidence}}


def state(local: float, remote: float, double_talk: float = 0.0) -> dict:
    return {
        "local_only_ratio": local,
        "double_talk_ratio": double_talk,
        "remote_active_ratio": remote,
    }


def decide(
    row: dict,
    *,
    calibration_value: dict,
    voice_value: dict,
    state_value: dict,
    transcript_value: dict,
    judge_value: dict | None,
    notes_ids: set[str] | None = None,
) -> dict:
    return MODULE.classify(
        row,
        calibration=calibration_value,
        voice_scores=voice_value,
        state=state_value,
        transcripts=transcript_value,
        judge=judge_value,
        notes_ids=notes_ids or set(),
    )


def main() -> int:
    genuine = decide(
        queue("проверю логи деплоя"),
        calibration_value=calibration(),
        voice_value=voice(0.84, 0.30),
        state_value=state(0.90, 0.05),
        transcript_value=transcripts("проверю логи деплоя", "обсудим квоты"),
        judge_value=judge("confirm_me", 0.92),
    )
    assert genuine["outcome"] == "genuine_me"
    assert genuine["action"] == "keep_me"

    duplicate_detail = {
        "label": "remote_duplicate",
        "verdict": "probable_transcript_error",
        "confidence": 0.96,
    }
    duplicate_row = queue("обсудим квоты", duplicate_detail)
    duplicate = decide(
        duplicate_row,
        calibration_value=calibration(),
        voice_value=voice(0.30, 0.72),
        state_value=state(0.0, 0.95),
        transcript_value=transcripts("обсудим квоты", "обсудим квоты"),
        judge_value=judge("confirm_remote_duplicate", 0.95),
    )
    assert duplicate["outcome"] == "remote_duplicate_or_leak"
    assert duplicate["action"] == "drop_duplicate_or_noise"

    audio_similarity_only = copy.deepcopy(duplicate_row)
    audio_similarity_only["source_detail"] = {"classification": {}}
    not_dropped = decide(
        audio_similarity_only,
        calibration_value=calibration(),
        voice_value=voice(0.30, 0.72),
        state_value=state(0.0, 0.95),
        transcript_value=transcripts("обсудим квоты", "обсудим квоты"),
        judge_value=None,
    )
    assert not_dropped["action"] == "needs_review"

    protected = decide(
        queue("надо проверить логи", duplicate_detail),
        calibration_value=calibration(),
        voice_value=voice(0.30, 0.72),
        state_value=state(0.0, 0.95),
        transcript_value=transcripts("надо проверить логи", "надо проверить логи"),
        judge_value=judge("confirm_remote_duplicate", 0.95),
    )
    assert protected["action"] == "needs_review"
    assert protected["checks"]["protected_content"] is True

    double_talk = decide(
        queue("я проверю отдельно"),
        calibration_value=calibration(),
        voice_value=voice(0.84, 0.30),
        state_value=state(0.30, 0.80, 0.50),
        transcript_value=transcripts("я проверю отдельно", "проверим вместе"),
        judge_value=judge("confirm_timing_or_doubletalk", 0.93),
    )
    assert double_talk["outcome"] == "real_double_talk"
    assert double_talk["action"] == "needs_review"

    weak_enrollment = decide(
        queue("проверю логи деплоя"),
        calibration_value=calibration("weak"),
        voice_value=voice(0.90, 0.10),
        state_value=state(1.0, 0.0),
        transcript_value=transcripts("проверю логи деплоя", ""),
        judge_value=judge("confirm_me", 0.99),
    )
    assert weak_enrollment["action"] == "needs_review"

    missing_model_calibration = calibration()
    missing_model_calibration["required_evidence_ready"] = False
    missing_model = decide(
        duplicate_row,
        calibration_value=missing_model_calibration,
        voice_value=voice(0.30, 0.72),
        state_value=state(0.0, 0.95),
        transcript_value=transcripts("обсудим квоты", "обсудим квоты"),
        judge_value=judge("confirm_remote_duplicate", 0.99),
    )
    assert missing_model["action"] == "needs_review"

    first = json.dumps(genuine, ensure_ascii=False, sort_keys=True)
    second = json.dumps(
        decide(
            queue("проверю логи деплоя"),
            calibration_value=calibration(),
            voice_value=voice(0.84, 0.30),
            state_value=state(0.90, 0.05),
            transcript_value=transcripts("проверю логи деплоя", "обсудим квоты"),
            judge_value=judge("confirm_me", 0.92),
        ),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert first == second
    print("residual audio arbitration checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
