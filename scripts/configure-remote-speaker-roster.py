#!/usr/bin/env python3
"""Record the expected remote participant roster without inferring voice identities."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.remote_speaker_roster/v1"
DEFAULT_OUTPUT = Path("derived/transcript-rich/speaker-roster-v1.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--expected-remote-speakers", type=int)
    parser.add_argument("--participant", action="append", default=[])
    parser.add_argument("--source", default="user_asserted_meeting_roster")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.expected_remote_speakers is not None and args.expected_remote_speakers <= 0:
        parser.error("--expected-remote-speakers must be positive")
    return args


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("roster must be a JSON object")
    return value


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate(payload: dict[str, Any]) -> None:
    if payload.get("schema") != SCHEMA:
        raise ValueError("unsupported roster schema")
    expected = int(payload.get("expected_remote_speakers") or 0)
    participants = payload.get("remote_participants") or []
    if expected <= 0 or not isinstance(participants, list):
        raise ValueError("invalid roster")
    if participants and len(participants) != expected:
        raise ValueError("participant count does not match expected remote speakers")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    output = args.output.expanduser()
    if not output.is_absolute():
        output = session / output
    output = output.resolve()
    if args.status:
        if not output.is_file():
            print(f"speaker_roster: missing path={output}")
            return 2
        payload = read_json(output)
        validate(payload)
        print(
            f"speaker_roster: ready expected_remote_speakers="
            f"{payload['expected_remote_speakers']} path={output}"
        )
        for row in payload.get("remote_participants") or []:
            print(f"  - {row['display_label']}")
        return 0

    participants = [value.strip() for value in args.participant if value.strip()]
    if len(participants) != len(set(participants)):
        raise ValueError("participant labels must be unique")
    expected = args.expected_remote_speakers or len(participants)
    if expected <= 0:
        raise ValueError("provide --expected-remote-speakers or at least one --participant")
    if participants and len(participants) != expected:
        raise ValueError("participant count does not match --expected-remote-speakers")
    payload = {
        "schema": SCHEMA,
        "session_id": session.name,
        "source": args.source,
        "expected_remote_speakers": expected,
        "remote_participants": [
            {"participant_id": f"participant_{index:02d}", "display_label": label}
            for index, label in enumerate(participants, start=1)
        ],
        "voice_identity_mapping": "not_asserted",
        "notes": "Roster constrains speaker count only; participant names are not assigned to voices.",
    }
    validate(payload)
    atomic_write(output, payload)
    print(f"speaker_roster: configured expected_remote_speakers={expected} path={output}")
    print(f"next: murmurmark transcript {session} --path-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
