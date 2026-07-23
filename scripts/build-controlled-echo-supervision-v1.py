#!/usr/bin/env python3
"""Build, replay or report Controlled Echo Supervision Lab v1 corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from controlled_echo_supervision import (
    default_policy_path,
    default_report_dir,
    default_sessions_root,
)
from controlled_echo_supervision_corpus import build, replay, status


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    for command in ("build", "replay", "status"):
        child = subparsers.add_parser(command)
        child.add_argument("--sessions-root", type=Path, default=default_sessions_root())
        child.add_argument("--out-dir", type=Path)
        child.add_argument("--policy", type=Path, default=default_policy_path())
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    sessions_root = args.sessions_root.resolve()
    output_dir = (
        args.out_dir.resolve()
        if args.out_dir
        else default_report_dir(sessions_root).resolve()
    )
    try:
        if args.command == "build":
            decision = build(
                sessions_root=sessions_root,
                output_dir=output_dir,
                policy_path=args.policy.resolve(),
            )
            print(f"decision: {decision['decision']}")
            print(f"report: {output_dir / 'corpus_decision.md'}")
            print("next: murmurmark corpus echo-supervision replay")
            return 0
        if args.command == "replay":
            report = replay(
                sessions_root=sessions_root,
                output_dir=output_dir,
                policy_path=args.policy.resolve(),
            )
            print(f"replay: {report['status']} ({report['matched_files']}/{report['total_files']})")
            print(f"report: {output_dir / 'replay_report.json'}")
            return 0 if report["status"] == "passed" else 2
        if args.command == "status":
            payload = status(output_dir)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
