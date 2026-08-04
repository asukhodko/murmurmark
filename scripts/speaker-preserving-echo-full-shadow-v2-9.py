#!/usr/bin/env python3
"""Run a profile-matched full shadow for the frozen v2.7 audio candidate."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = "speaker_preserving_neural_echo_v2_9"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


COMMON = load_module(
    ROOT / "scripts/speaker_preserving_echo_full_shadow_v2_5.py",
    "murmurmark_spne_v29_shadow_common",
)
SYNTHESIS = load_module(
    ROOT / "scripts/synthesize-simple-extractive.py",
    "murmurmark_spne_v29_shadow_synthesis",
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("session", type=Path)
    value.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    value.add_argument("--refresh", action="store_true")
    return value


def baseline_shadow_verdict(session: Path, destination: Path) -> dict[str, Any]:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    profile, paths, comparison, selection_risks = SYNTHESIS.choose_profile(
        resolved, "shadow_v2"
    )
    if profile != "shadow_v2":
        raise RuntimeError(f"shadow_v2 baseline resolved to unexpected profile: {profile}")
    quality, utterances, overlap_and_input_risks = SYNTHESIS.load_inputs(profile, paths)
    input_risks = [
        row for row in overlap_and_input_risks if "duration_sec" not in row
    ]
    overlaps = [row for row in overlap_and_input_risks if "duration_sec" in row]
    metrics = SYNTHESIS.metrics_from_quality(quality, utterances, overlaps)
    verdict, risk_items = SYNTHESIS.verdict_from_metrics(
        profile,
        metrics,
        selection_risks + input_risks,
        comparison,
    )
    inputs = {
        key: {
            "path": COMMON.PROMOTION.relative(path, session),
            "bytes": path.stat().st_size,
            "sha256": COMMON.sha256(path),
        }
        for key, path in {
            "clean_dialogue": paths["clean_dialogue"],
            "quality_report": paths["quality_report"],
            "overlaps": paths["overlaps"],
            "repair_comparison": paths["repair_comparison"],
        }.items()
        if path.is_file()
    }
    payload = {
        "schema": "murmurmark.profile_matched_quality_verdict/v1",
        "selected_transcript_profile": profile,
        "verdict": verdict,
        "metrics": metrics,
        "risk_items": risk_items,
        "inputs": inputs,
    }
    COMMON.PROMOTION.write_json(destination, payload)
    return payload


def run(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    candidate_root = session / "derived/preprocess/speaker-preserving-neural-echo-v2-7"
    candidate_audio = candidate_root / "candidate_clean_mic_pcm16.wav"
    output_root = candidate_root / "full-shadow-v2-9"
    candidate_dir = output_root / "candidates" / CANDIDATE
    baseline_audio = session / "derived/asr/mic.wav"
    remote_audio = session / "derived/preprocess/audio/remote_for_aec.wav"
    for path in (candidate_audio, baseline_audio, remote_audio):
        if not path.is_file():
            raise RuntimeError(f"required audio missing: {path}")

    COMMON.PROMOTION.safe_link(candidate_audio, candidate_dir / "mic_for_asr.wav")
    COMMON.PROMOTION.safe_link(baseline_audio, output_root / "canonical/mic.wav")
    COMMON.PROMOTION.safe_link(
        remote_audio, output_root / "canonical/remote_aligned.wav"
    )
    stage = candidate_dir / "full-shadow-precomputed-session"
    prior_stage = (
        candidate_root
        / "full-shadow-v2-7/candidates/speaker_preserving_neural_echo_v2_7/"
        "full-shadow-precomputed-session/derived/transcript-simple/whisper-cpp"
    )
    if prior_stage.is_dir() and not stage.exists():
        stage_cache = stage / "derived/transcript-simple/whisper-cpp"
        for relative_path in COMMON.MICRO_ASR_CACHE_PATHS:
            COMMON.seed_file_cache(
                prior_stage / relative_path,
                stage_cache / relative_path,
            )
    verdict_path = output_root / "baseline_shadow_v2_verdict.json"
    baseline_verdict = baseline_shadow_verdict(session, verdict_path)

    original_build_input_basis = COMMON.build_input_basis
    original_read_json = COMMON.PROMOTION.read_json
    ordinary_verdict_path = (
        session / "derived/synthesis-simple/extractive/quality_verdict.json"
    ).resolve()

    def matched_build_input_basis(**kwargs: Any) -> dict[str, Any]:
        basis = original_build_input_basis(**kwargs)
        basis["baseline_verdict"] = {
            "path": COMMON.PROMOTION.relative(verdict_path, session),
            "bytes": verdict_path.stat().st_size,
            "sha256": COMMON.sha256(verdict_path),
        }
        return basis

    def matched_read_json(path: Path) -> dict[str, Any]:
        if Path(path).resolve() == ordinary_verdict_path:
            return baseline_verdict
        return original_read_json(path)

    COMMON.build_input_basis = matched_build_input_basis
    COMMON.PROMOTION.read_json = matched_read_json
    try:
        payload = COMMON.full_shadow_stage(
            session=session,
            output_root=output_root,
            candidate=CANDIDATE,
            candidate_mic_asr=candidate_root / "direct-asr/raw/mic.json",
            candidate_asr_report=candidate_root / "direct-asr/chunk_report.json",
            whisper_model=args.whisper_model.expanduser().resolve(),
            refresh=args.refresh,
            reuse_micro_asr_cache=True,
        )
    finally:
        COMMON.build_input_basis = original_build_input_basis
        COMMON.PROMOTION.read_json = original_read_json

    candidate_verdict_path = (
        candidate_dir
        / "full-shadow-precomputed-session/derived/synthesis-simple/extractive/quality_verdict.json"
    )
    candidate_verdict = original_read_json(candidate_verdict_path)
    profiles_match = (
        baseline_verdict.get("selected_transcript_profile") == "shadow_v2"
        and candidate_verdict.get("selected_transcript_profile") == "shadow_v2"
    )
    payload["schema"] = "murmurmark.echo_suppression_full_shadow/v2.9"
    payload["candidate_profile"] = CANDIDATE
    payload["candidate_runtime_schema"] = (
        "murmurmark.speaker_preserving_neural_echo_runtime/v2.7"
    )
    payload["verdict_comparison"] = {
        "baseline_profile": baseline_verdict.get("selected_transcript_profile"),
        "candidate_profile": candidate_verdict.get("selected_transcript_profile"),
        "baseline_verdict": baseline_verdict.get("verdict"),
        "candidate_verdict": candidate_verdict.get("verdict"),
        "baseline_snapshot": COMMON.PROMOTION.fingerprint(verdict_path, session),
    }
    payload.setdefault("gates", {})["verdict_profiles_match"] = profiles_match
    payload["passed"] = bool(payload["gates"]) and all(payload["gates"].values())
    COMMON.PROMOTION.write_json(
        candidate_dir / "full_shadow_precomputed_report.json", payload
    )
    return payload


def main() -> int:
    args = parser().parse_args()
    payload = run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("passed") is True else 6


if __name__ == "__main__":
    raise SystemExit(main())
