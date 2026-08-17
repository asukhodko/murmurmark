#!/usr/bin/env python3
"""Synthetic checks for the private remote speaker cluster-purity reference corpus."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/report-remote-speaker-cluster-purity-reference-v1.py"
POLICY = ROOT / "policies/remote-speaker-cluster-purity-reference-v1.json"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(path: Path, session: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(session).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": digest(path),
    }


def clock(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    sessions = root / "sessions"
    session = sessions / "fixture"
    selected = session / "derived/transcript-simple/whisper-cpp/resolved"
    rich_root = session / (
        "derived/transcript-rich/speaker-resolved-default-v1/evidence/"
        "fingerprint/remote-speaker-coverage-v3"
    )
    selected.mkdir(parents=True)
    rich_root.mkdir(parents=True)
    speakers = ["Speaker A"] * 9 + ["Speaker B"] * 9 + ["Speaker C", "Speaker D"]
    clusters = ["remote_speaker_01"] * 9 + ["remote_speaker_02"] * 9 + ["remote_speaker_01", "unknown"]
    utterances = []
    reference_blocks: list[str] = []
    for index, (speaker, cluster) in enumerate(zip(speakers, clusters), 1):
        start = (index - 1) * 6
        end = start + 4
        local_start = start + 100
        local_end = end + 100
        text = f"synthetic phrase number {index} carries distinct evidence"
        status = "attributed" if cluster != "unknown" else "unknown"
        utterances.append(
            {
                "id": f"utt_{index:06d}",
                "start": local_start,
                "end": local_end,
                "role": "remote",
                "speaker_label": "Colleagues",
                "text": text,
                "speaker_turns": [
                    {
                        "start": local_start,
                        "end": local_end,
                        "speaker_id": None if cluster == "unknown" else cluster,
                        "status": status,
                        "text": text,
                    }
                ],
            }
        )
        reference_blocks += [f"{clock(start)} - {clock(end)}", speaker, text, ""]
    rich_path = rich_root / "transcript.rich.shadow.json"
    write_json(
        rich_path,
        {"schema": "murmurmark.remote_speaker_rich_transcript/v3", "utterances": utterances},
    )
    rich_markdown = rich_root / "transcript.rich.shadow.md"
    rich_markdown.write_text("# Synthetic rich transcript\n", encoding="utf-8")
    coverage = rich_root / "report.json"
    write_json(
        coverage,
        {
            "schema": "murmurmark.remote_speaker_coverage_report/v3",
            "summary": {"published_speakers": 2},
        },
    )
    dialogue = selected / "clean_dialogue.reviewed_v1.json"
    write_json(dialogue, {"schema": "murmurmark.clean_dialogue/v1", "utterances": utterances})
    aggregate = selected / "transcript.reviewed_v1.md"
    aggregate.write_text("# Synthetic aggregate transcript\n", encoding="utf-8")
    selection_path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    write_json(
        selection_path,
        {
            "schema": "murmurmark.speaker_resolved_transcript_selection/v1",
            "state": "selected",
            "selected_profile": "reviewed_v1",
            "selected_speaker_profile": "remote_speaker_coverage_v3",
            "rich_transcript": artifact(rich_path, session),
            "coverage_report": artifact(coverage, session),
            "selected_dialogue": artifact(dialogue, session),
            "aggregate_transcript": artifact(aggregate, session),
            "selected_transcript": artifact(rich_markdown, session),
        },
    )
    source = root / "external.txt"
    source.write_text("\n".join(reference_blocks), encoding="utf-8")
    return sessions, session, source, rich_path


def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"command returned {result.returncode}, expected {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-cluster-purity-") as raw:
        root = Path(raw)
        sessions, session, source, rich_path = fixture(root)
        out = root / "reports"
        manifest = root / "manifest.json"
        rich_before = digest(rich_path)
        aggregate = session / "derived/transcript-simple/whisper-cpp/resolved/transcript.reviewed_v1.md"
        aggregate_before = digest(aggregate)
        common = ("--out-dir", str(out), "--sessions-root", str(sessions), "--policy", str(POLICY))
        run(
            "import",
            "fixture",
            str(source),
            "--source-id",
            "synthetic_reference",
            *common,
        )
        run("evaluate", "--write-manifest", str(manifest), *common)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        assert report["schema"] == "murmurmark.remote_speaker_cluster_purity_reference_report/v1"
        assert report["decision"] == "ADVANCE_SEGMENTATION"
        metrics = report["evidence"][0]["metrics"]
        assert metrics["reference_remote_speakers"] == 4
        assert metrics["published_clusters"] == 2
        assert metrics["dominant_cluster_collisions"] >= 1
        assert metrics["dominant_cluster_weighted_purity"] < 1
        assert metrics["minority_speaker_recall"] == 0
        public = (out / "report.json").read_text(encoding="utf-8") + manifest.read_text(encoding="utf-8")
        assert "Speaker A" not in public
        assert "distinct evidence" not in public
        private = (out / "private/evaluations/synthetic_reference/evaluation.json").read_text(encoding="utf-8")
        assert "Speaker A" in private
        summary_path = session / "derived/audit/remote-speaker-cluster-purity-reference-v1/summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["cluster_scope"] == "session_local_acoustic_cluster"
        assert summary["identity_safety"] == "diagnostic_external_machine_reference"
        run("replay", "--write-manifest", str(manifest), *common)
        assert digest(rich_path) == rich_before
        assert digest(aggregate) == aggregate_before
        copied = out / "private/sources/synthetic_reference/source.txt"
        copied.write_text(copied.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        failure = run("evaluate", *common, expected=2)
        assert "missing or changed" in failure.stderr
    print("remote speaker cluster purity reference v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
