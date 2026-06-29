#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.review_lane_audio_probe/v1"
SCRIPT_VERSION = "0.1.0"
DEFAULT_TRACKS = ("mic_clean", "mic_role_masked", "remote")
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_+-]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe per-track clips from a review lane pack to aid digital review."
    )
    parser.add_argument("manifest", type=Path, help="review_lane_pack.<lane>.json")
    parser.add_argument(
        "--model",
        default=os.environ.get(
            "MURMURMARK_WHISPER_MODEL",
            str(Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"),
        ),
        help="whisper.cpp GGML model path.",
    )
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default=os.environ.get("MURMURMARK_WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--tracks", nargs="+", default=list(DEFAULT_TRACKS), help="Clip track suffixes to transcribe.")
    parser.add_argument("--max-items", type=int, default=0, help="Limit lane items, 0 means all.")
    parser.add_argument("--force", action="store_true", help="Regenerate even if output JSON exists.")
    parser.add_argument("--dry-run", action="store_true", help="Write probe commands without running whisper-cli.")
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-md", type=Path)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def shell_path(path: Path) -> str:
    return shlex.quote(str(path))


def path_from_afplay(command: Any) -> Path | None:
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts or parts[0] != "afplay":
        return None
    for part in parts[1:]:
        if part.startswith("-"):
            continue
        return Path(part)
    return None


def audit_clip_dir(item: dict[str, Any]) -> Path | None:
    commands: list[str] = []
    for key in ("command",):
        if isinstance(item.get(key), str):
            commands.append(str(item[key]))
    for group in item.get("group_commands") or []:
        if isinstance(group, dict) and isinstance(group.get("command"), str):
            commands.append(str(group["command"]))
    for command in commands:
        path = path_from_afplay(command)
        if path:
            return path.parent
    return None


def item_source_ids(item: dict[str, Any]) -> list[str]:
    ids = item.get("source_audit_ids")
    if isinstance(ids, list) and ids:
        return [str(value) for value in ids if value]
    source_id = item.get("source_audit_id")
    return [str(source_id)] if source_id else []


def evidence_text(item: dict[str, Any], role: str) -> str:
    parts: list[str] = []
    for row in item.get("evidence_text") or []:
        if not isinstance(row, dict):
            continue
        row_role = str(row.get("role") or "").lower()
        if role in row_role:
            parts.append(str(row.get("text") or ""))
    return " ".join(parts).strip()


def tokens(text: str) -> list[str]:
    return [part.lower().replace("ё", "е") for part in TOKEN_RE.findall(text)]


def text_scores(text: str, me_text: str, remote_text: str) -> dict[str, Any]:
    text_tokens = set(tokens(text))
    me_tokens = set(tokens(me_text))
    remote_tokens = set(tokens(remote_text))

    def overlap(base: set[str]) -> float:
        if not base:
            return 0.0
        return round(len(text_tokens & base) / len(base), 6)

    return {
        "char_similarity_to_me": round(SequenceMatcher(None, text, me_text).ratio(), 6) if me_text else 0.0,
        "char_similarity_to_remote": round(SequenceMatcher(None, text, remote_text).ratio(), 6) if remote_text else 0.0,
        "token_overlap_to_me": overlap(me_tokens),
        "token_overlap_to_remote": overlap(remote_tokens),
    }


def transcribe_clip(args: argparse.Namespace, clip: Path, out_prefix: Path) -> tuple[str, list[str], int]:
    command = [
        args.whisper_cli,
        "-m",
        str(Path(args.model).expanduser()),
        "-l",
        args.language,
        "--max-context",
        "0",
        "--temperature",
        "0",
        "--no-fallback",
        "-otxt",
        "-of",
        str(out_prefix),
        str(clip),
    ]
    if args.dry_run:
        return "", command, 0
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    text_path = out_prefix.with_suffix(".txt")
    text = text_path.read_text(encoding="utf-8").strip() if text_path.exists() else ""
    return text, command, result.returncode


def build_probe(args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    if args.max_items > 0:
        items = items[: args.max_items]
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="murmurmark-review-lane-probe.") as tmp:
        tmp_dir = Path(tmp)
        for item in items:
            clip_dir = audit_clip_dir(item)
            source_ids = item_source_ids(item)
            me_text = evidence_text(item, "me")
            remote_text = evidence_text(item, "remote")
            source_records: list[dict[str, Any]] = []
            for source_id in source_ids:
                track_records: list[dict[str, Any]] = []
                for track in args.tracks:
                    clip = clip_dir / f"{source_id}_{track}.wav" if clip_dir else Path(f"{source_id}_{track}.wav")
                    out_prefix = tmp_dir / f"{source_id}_{track}"
                    exists = clip.exists()
                    text = ""
                    command: list[str] = [
                        args.whisper_cli,
                        "-m",
                        str(Path(args.model).expanduser()),
                        "-l",
                        args.language,
                        "--max-context",
                        "0",
                        "--temperature",
                        "0",
                        "--no-fallback",
                        "-otxt",
                        "-of",
                        str(out_prefix),
                        str(clip),
                    ]
                    returncode: int | None = None
                    if exists:
                        text, command, returncode = transcribe_clip(args, clip, out_prefix)
                    track_records.append(
                        {
                            "track": track,
                            "clip": str(clip),
                            "exists": exists,
                            "dry_run": bool(args.dry_run),
                            "command": " ".join(shlex.quote(part) for part in command),
                            "returncode": returncode,
                            "text": text,
                            "scores": text_scores(text, me_text, remote_text) if text else {},
                        }
                    )
                source_records.append({"source_audit_id": source_id, "tracks": track_records})
            records.append(
                {
                    "index": item.get("index"),
                    "source_audit_id": item.get("source_audit_id"),
                    "source_audit_ids": source_ids,
                    "label": item.get("label"),
                    "suggested_decision": item.get("suggested_decision"),
                    "allowed_decisions": item.get("allowed_decisions"),
                    "me_utterance_ids": item.get("me_utterance_ids"),
                    "remote_utterance_ids": item.get("remote_utterance_ids"),
                    "pack_time": {"start": item.get("pack_start_time"), "end": item.get("pack_end_time")},
                    "evidence": {"me_text": me_text, "remote_text": remote_text},
                    "sources": source_records,
                }
            )
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "probe-review-lane-pack-audio", "version": SCRIPT_VERSION},
        "input": {"manifest": str(args.manifest), "lane": manifest.get("lane")},
        "parameters": {
            "language": args.language,
            "model": str(Path(args.model).expanduser()),
            "tracks": args.tracks,
            "dry_run": bool(args.dry_run),
            "max_items": args.max_items,
        },
        "summary": {
            "items": len(records),
            "source_clips": sum(len(row["source_audit_ids"]) for row in records),
            "track_probes": sum(len(source["tracks"]) for row in records for source in row["sources"]),
            "missing_clips": sum(
                1
                for row in records
                for source in row["sources"]
                for track in source["tracks"]
                if not track["exists"]
            ),
        },
        "items": records,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# MurmurMark Review Lane Audio Probe",
        "",
        f"Lane: `{report['input'].get('lane')}`",
        f"Manifest: `{report['input'].get('manifest')}`",
        f"Mode: `{'dry-run' if report['parameters'].get('dry_run') else 'transcribed'}`",
        "",
        "This probe is evidence for review, not an automatic decision source.",
        "",
        "## Summary",
        "",
        f"- Items: `{report['summary']['items']}`",
        f"- Source clips: `{report['summary']['source_clips']}`",
        f"- Track probes: `{report['summary']['track_probes']}`",
        f"- Missing clips: `{report['summary']['missing_clips']}`",
        "",
        "## Items",
        "",
    ]
    for item in report["items"]:
        lines.extend(
            [
                f"### {item.get('index')}. `{item.get('source_audit_id')}` / `{item.get('label')}`",
                "",
                f"- Pack: `{item.get('pack_time', {}).get('start')}`-`{item.get('pack_time', {}).get('end')}`",
                f"- Suggested: `{item.get('suggested_decision')}`",
                f"- Allowed: `{', '.join(item.get('allowed_decisions') or [])}`",
                f"- Me evidence: {item['evidence'].get('me_text') or '-'}",
                f"- Remote evidence: {item['evidence'].get('remote_text') or '-'}",
                "",
            ]
        )
        for source in item["sources"]:
            lines.append(f"#### `{source['source_audit_id']}`")
            lines.append("")
            for track in source["tracks"]:
                text = track.get("text") or ""
                text_line = text.replace("\n", " ").strip() or "(no transcript)"
                lines.append(f"- `{track['track']}`: {text_line}")
                if track.get("scores"):
                    scores = track["scores"]
                    lines.append(
                        "  "
                        f"`me={scores.get('token_overlap_to_me')}` "
                        f"`remote={scores.get('token_overlap_to_remote')}` "
                        f"`char_me={scores.get('char_similarity_to_me')}` "
                        f"`char_remote={scores.get('char_similarity_to_remote')}`"
                    )
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    manifest_path = args.manifest.expanduser()
    manifest = read_json(manifest_path)
    lane = str(manifest.get("lane") or "lane")
    out_json = args.out_json or manifest_path.with_name(f"review_lane_probe.{lane}.json")
    out_md = args.out_md or manifest_path.with_name(f"review_lane_probe.{lane}.md")
    if out_json.exists() and out_md.exists() and not args.force:
        print(f"review_lane_probe: {out_json}")
        print(f"markdown: {out_md}")
        print("status: reused")
        return
    report = build_probe(args, manifest)
    write_json(out_json, report)
    write_markdown(out_md, report)
    print(f"review_lane_probe: {out_json}")
    print(f"markdown: {out_md}")
    print(f"items: {report['summary']['items']}")
    print(f"track_probes: {report['summary']['track_probes']}")
    print(f"missing_clips: {report['summary']['missing_clips']}")


if __name__ == "__main__":
    main()
