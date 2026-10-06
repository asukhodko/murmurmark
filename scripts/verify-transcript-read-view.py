#!/usr/bin/env python3
"""Verify a saved reading projection without inference or session writes."""

import argparse
import importlib.util
from pathlib import Path

import transcript_read_view as view


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--verify-only", action="store_true", help="Explicit alias for the default read-only mode.")
    parser.add_argument("--expected-generation", help="Reject a pointer replaced after the caller selected it.")
    parser.add_argument("--print-path", action="store_true")
    args = parser.parse_args()
    session = args.session.expanduser().resolve()
    try:
        outcome = view.read(session / "derived/outcome/outcome.json")
        spec = importlib.util.spec_from_file_location("outcome_reader", Path(__file__).with_name("evaluate-outcome.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        current = module.speaker_resolution(session, outcome.get("selected_profile"))
        if current != outcome.get("speaker_resolution"):
            return 2
        path = view.verified_path(session, outcome, expected_generation=args.expected_generation)
        if path is None:
            return 2
        if args.print_path:
            print(path)
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
