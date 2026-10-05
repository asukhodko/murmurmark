#!/usr/bin/env python3
"""Measure exact-PCM reuse without changing the frozen production ASR or session."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import authoritative_asr_cache as artifacts
import micro_asr_cache as cache
from murmurmark_deadline import ActionStopped, DEADLINE_ENV, remaining_seconds, run_bounded
from murmurmark_resource_policy import apply_resource_policy, resolve_resource_policy


ROOT = Path(__file__).resolve().parents[1]


def inspect_clips(session: Path) -> tuple[dict, list[list[Path]]]:
    groups: dict[str, list[Path]] = defaultdict(list)
    root = session / "derived/transcript-simple/whisper-cpp"
    inputs = {}
    for clip in sorted(root.glob("timeline-repair*/micro_reasr/*.wav")):
        groups[json.dumps(cache.pcm_identity(clip), sort_keys=True)].append(clip)
        for path in (clip, clip.with_suffix(".json")):
            inputs[str(path.relative_to(session))] = cache.digest(path) if path.is_file() else None
    duplicates = [rows for rows in groups.values() if len(rows) > 1]
    same = missing = 0
    for rows in duplicates:
        results = []
        for clip in rows:
            try:
                result = json.loads(clip.with_suffix(".json").read_text())
                if not cache.valid_decode(result):
                    raise ValueError("invalid decode")
                results.append(json.dumps(result["transcription"], sort_keys=True))
            except (OSError, ValueError):
                break
        if len(results) != len(rows):
            missing += 1
        elif len(set(results)) == 1:
            same += 1
    return {
        "inputs": inputs, "clips": sum(map(len, groups.values())), "unique_pcm": len(groups),
        "duplicate_pcm_clips": sum(len(rows) - 1 for rows in duplicates),
        "duplicate_groups": len(duplicates), "identical_saved_decode_groups": same,
        "missing_or_invalid_decode_groups": missing,
        "conflicting_saved_decode_groups": len(duplicates) - same - missing,
    }, duplicates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--out", required=True, type=Path, help="Private report outside the input session.")
    parser.add_argument("--decode-groups", type=int, default=0, help="Optional bounded real-model cold/warm canaries.")
    parser.add_argument("--timeout-sec", type=float, default=120)
    args = parser.parse_args()
    session, out = args.session.expanduser().resolve(), args.out.expanduser().resolve()
    if out.is_relative_to(session) or args.decode_groups < 0 or not 0 < args.timeout_sec <= 600:
        parser.error("use an output outside the session, nonnegative groups and a timeout in (0, 600]")
    if not (session / "session.json").is_file():
        parser.error("session.json missing")
    config = json.loads((ROOT / "murmurmark.config.json").read_text())
    processing = config.get("processing") or {}
    policy = resolve_resource_policy(processing.get("resource_profile"), processing.get("max_compute_threads"))
    resource = apply_resource_policy(policy)
    report, groups = inspect_clips(session)
    report.update(schema="murmurmark.micro_asr_cache_replay/v1", session=str(session),
                  resource_policy=resource, status="running", canaries=[],
                  production_decision="DO_NOT_PROMOTE", reason="producer_corpus_qualification_required",
                  helper_sha256=cache.digest(Path(cache.__file__)))
    artifacts.atomic_write_json(out, report)
    root = out.parent / (out.stem + "-artifacts")
    try:
        model = Path(config["transcription"]["model"]).expanduser().resolve()
        for index, clips in enumerate(groups[:args.decode_groups]):
            decoded = 0

            def decode(command, log):
                nonlocal decoded
                decoded += 1
                result = run_bounded(command, max_seconds=args.timeout_sec)
                log.write_text(result["stdout"] + "\n" + result["stderr"])
                if result["status"] != "completed":
                    raise ActionStopped(result["returncode"])
                if result["returncode"]:
                    raise subprocess.CalledProcessError(result["returncode"], command)
                return {"mode": "default"}

            results, durations, texts = [], [], []
            for attempt, clip in enumerate(clips[:2]):
                base = root / str(index) / ("cold" if attempt == 0 else "warm")
                command = [shutil.which("whisper-cli") or "whisper-cli", "--model", str(model),
                           "--language", config["transcription"].get("language", "ru"),
                           "--threads", str(policy.max_compute_threads or 3), "--max-context", "0",
                           "--temperature", "0", "--temperature-inc", "0", "--no-fallback",
                           "--output-json", "--output-json-full", "--output-txt", "--output-file", str(base),
                           "--no-prints", "--log-score", "--suppress-nst", "--suppress-regex",
                           r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
                           "--file", str(clip)]
                start = time.monotonic()
                previous_deadline = os.environ.get(DEADLINE_ENV)
                remaining = remaining_seconds()
                budget = min(args.timeout_sec, remaining) if remaining is not None else args.timeout_sec
                os.environ[DEADLINE_ENV] = str(time.time() + budget)
                try:
                    results.append(cache.materialize(command, base, force=attempt == 0, decode=decode,
                                                     cache_root=root / "cache"))
                finally:
                    if previous_deadline is None:
                        os.environ.pop(DEADLINE_ENV, None)
                    else:
                        os.environ[DEADLINE_ENV] = previous_deadline
                durations.append(round(time.monotonic() - start, 6))
                texts.append(json.loads(base.with_suffix(".json").read_text())["transcription"])
            row = {"clips": [str(p.relative_to(session)) for p in clips[:2]], "decodes": decoded,
                   "cold_sec": durations[0], "warm_sec": durations[1],
                   "results": results, "relative_decode_equal": texts[0] == texts[1]}
            row["passed"] = decoded == 1 and not results[0]["cache_hit"] and results[1]["cache_hit"] and row["relative_decode_equal"]
            report["canaries"].append(row)
            artifacts.atomic_write_json(out, report)
        report["inputs_unchanged"] = all(
            (cache.digest(session / path) if (session / path).is_file() else None) == digest
            for path, digest in report["inputs"].items())
        report["status"] = "passed" if (report["clips"] and report["inputs_unchanged"]
                                         and all(row["passed"] for row in report["canaries"])) else "failed"
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, ActionStopped, KeyboardInterrupt) as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
    artifacts.atomic_write_json(out, report)
    print(f"micro_asr_cache_replay: {report['status']}; clips={report['clips']} unique_pcm={report['unique_pcm']}; report={out}")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
