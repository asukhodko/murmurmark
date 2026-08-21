#!/usr/bin/env python3
"""Validate the small active planning surface and its cross-document contract."""

from __future__ import annotations

import re
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "docs/roadmap/murmurmark-cli-roadmap.plan.yaml"
CURRENT_GOAL_PATH = ROOT / "docs/project/current-goal.md"
ROADMAP_PATH = ROOT / "docs/roadmap/murmurmark-cli-roadmap.md"
README_PATH = ROOT / "README.md"

ACTIVE_DOCS = (
    README_PATH,
    ROOT / "docs/00-index.md",
    ROOT / "docs/product/vision.md",
    ROOT / "docs/product/prd-v1.md",
    CURRENT_GOAL_PATH,
    ROOT / "docs/project/reliable-transcription-route.md",
    ROADMAP_PATH,
    ROOT / "docs/architecture/system-overview.md",
    ROOT / "docs/architecture/transcription.md",
    ROOT / "docs/architecture/experimental-sidecar.md",
    ROOT / "docs/contracts/domain-pack.md",
    ROOT / "docs/contracts/lexical-accuracy-reference-corpus.md",
    ROOT / "docs/rfc/0001-v1-scope.md",
)

REQUIRED_ARCHIVES = (
    ROOT / "docs/history/README-development-log-through-2026-07-19.md",
    ROOT / "docs/history/current-goal-through-2026-07-19.md",
    ROOT / "docs/history/murmurmark-cli-roadmap-through-2026-07-19.md",
    ROOT / "docs/history/murmurmark-cli-roadmap-through-2026-07-19.plan.yaml",
)

CRITICAL_PATH = (
    "product-one-command-meeting-lifecycle-v1",
    "quality-mixed-utterance-separation-v1",
    "quality-echo-suppression-promotion",
    "quality-neural-residual-echo-v1",
    "quality-speaker-preserving-echo-adaptation-corpus-v1",
    "quality-controlled-echo-supervision-lab-v1",
    "research-speaker-preserving-neural-echo-v2",
    "research-reference-conditioned-target-me-separation-v1",
    "quality-target-me-identifiability-corpus-v1",
    "research-reference-conditioned-target-me-separation-v2",
    "product-release-quality-cli",
    "product-reliable-final-handoff-v1",
    "product-authoritative-incremental-asr-v1",
    "product-canonical-live-asr-producer-v1",
    "product-causal-canonical-mic-asr-v1",
    "research-remote-diarization",
    "research-rich-transcript",
    "research-speaker-map",
    "quality-pre-asr-target-me-isolation-limit-v1",
    "quality-remote-speaker-diarization-v2",
    "quality-transcript-perfection-corpus-v1",
    "quality-remote-speaker-coverage-v3",
    "quality-remote-speaker-residual-evidence-v4",
    "product-speaker-resolved-transcript-default-v1",
    "quality-lexical-accuracy-reference-corpus-v1",
    "quality-independent-remote-speaker-evidence-v1",
    "quality-remote-speaker-residual-reference-corpus-v1",
    "quality-controlled-remote-speaker-truth-lab-v1",
    "quality-duration-aware-remote-speaker-attribution-v2",
    "quality-segment-context-remote-speaker-attribution-v1",
    "quality-remote-speaker-attribution-error-decomposition-v1",
    "quality-stronger-remote-speaker-identity-backend-qualification-v1",
    "quality-ecapa-remote-speaker-shadow-qualification-v1",
    "quality-remote-speaker-shadow-error-decomposition-v1",
    "quality-bounded-remote-speaker-interval-purification-v1",
    "quality-session-local-remote-speaker-enrollment-hardening-v1",
    "quality-remote-speaker-direct-truth-seed-v1",
    "quality-remote-speaker-direct-truth-candidate-adjudication-v1",
    "quality-remote-speaker-enrollment-purity-abstention-hardening-v2",
    "quality-session-local-homogeneous-remote-speaker-enrollment-mining-v1",
    "quality-lightweight-remote-speaker-representation-frontier-v1",
    "quality-remote-speaker-disjoint-truth-expansion-v2",
    "quality-disjoint-remote-speaker-model-qualification-v1",
    "quality-remote-speaker-usability-gate-error-decomposition-v1",
    "quality-residual-transcript-integrity-hardening-v1",
    "quality-remote-speaker-boundary-minority-segmentation-v1",
    "quality-post-segmentation-transcript-rebaseline-v1",
    "quality-capture-continuity-loss-closure-v1",
    "quality-remote-unknown-evidence-recovery-v1",
    "quality-human-reviewed-lexical-seed-v1",
    "quality-session-scoped-lexical-context-v1",
    "product-speaker-resolved-transcript-terminal-gate-v1",
)

EXPECTED_STATUSES = {"done", "current", "next", "later", "idea", "optional", "blocked"}
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)\n```", flags=re.DOTALL)
LOCAL_ABSOLUTE_RE = re.compile(r"(?:/" + "Users/|/" + "home/|" + r"[A-Za-z]:\\\\)")


class PlanningError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PlanningError(message)


def load_plan() -> dict:
    try:
        plan = yaml.safe_load(PLAN_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise PlanningError(f"cannot load active OpsKarta plan: {error}") from error
    require(isinstance(plan, dict), "active OpsKarta plan must be a mapping")
    require(plan.get("version") == 3, "active OpsKarta plan must use version 3")
    return plan


def validate_official_opskarta() -> bool:
    configured = os.environ.get("MURMURMARK_OPSKARTA_REPO")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.append(ROOT.parent / "opskarta")
    opskarta_repo = next(
        (candidate.resolve() for candidate in candidates if (candidate / "specs/v3/tools/cli.py").is_file()),
        None,
    )
    if opskarta_repo is None:
        require(
            os.environ.get("MURMURMARK_REQUIRE_OPSKARTA") != "1",
            "official OpsKarta v3 tooling is required but was not found",
        )
        return False

    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{opskarta_repo}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(opskarta_repo)
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "specs.v3.tools.cli",
            "validate",
            str(PLAN_PATH),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    require(
        result.returncode == 0,
        f"official OpsKarta v3 validation failed: {(result.stderr or result.stdout).strip()}",
    )
    return True


def validate_statuses_and_goal(plan: dict) -> tuple[dict, str]:
    statuses = plan.get("statuses")
    nodes = plan.get("nodes")
    require(isinstance(statuses, dict), "plan.statuses must be a mapping")
    require(set(statuses) == EXPECTED_STATUSES, "plan status set does not match the planning contract")
    require(isinstance(nodes, dict) and nodes, "plan.nodes must be a non-empty mapping")
    require(len(nodes) <= 62, f"active plan is too large: {len(nodes)} nodes, expected at most 62")

    current = [(node_id, node) for node_id, node in nodes.items() if node.get("status") == "current"]
    current_tasks = [(node_id, node) for node_id, node in current if node.get("kind") == "task"]
    current_summaries = [(node_id, node) for node_id, node in current if node.get("kind") == "summary"]
    require(len(current) == 2, f"expected current program plus current goal, found {len(current)} current nodes")
    require(len(current_tasks) == 1, f"expected exactly one executable current task, found {len(current_tasks)}")
    require(len(current_summaries) == 1, "expected exactly one current program summary")

    program = plan.get("x", {}).get("exec", {}).get("program", {})
    nearest_goal = program.get("nearest_goal")
    require(isinstance(nearest_goal, str) and nearest_goal.strip(), "x.exec.program.nearest_goal is required")
    current_title = current_tasks[0][1].get("title")
    require(nearest_goal.startswith(f"{current_title}:"), "nearest_goal must start with the current task title")

    texts = {
        "README": README_PATH.read_text(encoding="utf-8"),
        "current-goal": CURRENT_GOAL_PATH.read_text(encoding="utf-8"),
        "roadmap": ROADMAP_PATH.read_text(encoding="utf-8"),
    }
    for label, text in texts.items():
        require(current_title in text, f"{label} does not name the current goal {current_title!r}")
    require(f"## {current_title}" in texts["current-goal"], "current-goal heading must match nearest_goal")
    current_goal_match = re.search(
        r"^OpsKarta nearest goal: (.+(?:\n(?!\n).+)*)$",
        texts["current-goal"],
        flags=re.MULTILINE,
    )
    require(current_goal_match is not None, "current-goal must contain the OpsKarta nearest goal")
    current_goal_value = " ".join(current_goal_match.group(1).split())
    require(current_goal_value == nearest_goal, "current-goal and x.exec.program.nearest_goal differ")

    return nodes, current_tasks[0][0]


def validate_dependencies(nodes: dict, current_goal_id: str) -> None:
    for node_id, node in nodes.items():
        parent = node.get("parent")
        if parent is not None:
            require(parent in nodes, f"{node_id} references missing parent {parent}")
        for dependency in node.get("deps", []):
            require(dependency in nodes, f"{node_id} references missing dependency {dependency}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise PlanningError(f"dependency cycle detected at {node_id}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in nodes[node_id].get("deps", []):
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes:
        visit(node_id)

    unfinished_critical_path = [
        node_id for node_id in CRITICAL_PATH if nodes[node_id].get("status") != "done"
    ]
    require(unfinished_critical_path, "critical path must retain at least one unfinished stage")
    require(
        current_goal_id == unfinished_critical_path[0],
        "current goal must be the first unfinished critical-path stage",
    )
    for predecessor in (
        "quality-residual-local-recall-closure-v1",
        "quality-residual-chronology-closure-v1",
        "quality-operational-rebaseline-v1",
        "quality-speaker-mode-hardening-v1",
        "quality-evidence-backed-me-completion-v2",
    ):
        require(nodes[predecessor].get("status") == "done", f"current goal requires done predecessor {predecessor}")
    for previous, current in zip(CRITICAL_PATH, CRITICAL_PATH[1:]):
        require(previous in nodes[current].get("deps", []), f"critical path is broken: {current} must depend on {previous}")

    for node_id, node in nodes.items():
        if node.get("status") == "done":
            unfinished = [
                dependency
                for dependency in node.get("deps", [])
                if nodes[dependency].get("status") != "done"
            ]
            require(not unfinished, f"done node {node_id} depends on unfinished nodes: {unfinished}")

    require(
        "quality-operational-rebaseline-v1" in nodes["research-remote-diarization"].get("deps", []),
        "remote diarization must follow base quality closure",
    )
    require(
        "research-remote-diarization" in nodes["research-rich-transcript"].get("deps", []),
        "anonymous rich transcript must follow remote speaker evidence",
    )
    require(
        "research-rich-transcript" in nodes["research-speaker-map"].get("deps", []),
        "reviewed speaker naming must follow anonymous rich transcript",
    )
    require(
        "research-speaker-map"
        in nodes["quality-pre-asr-target-me-isolation-limit-v1"].get("deps", []),
        "the active audio frontier must follow the completed reviewed-speaker checkpoint",
    )
    require(
        "quality-pre-asr-target-me-isolation-limit-v1"
        in nodes["quality-remote-speaker-diarization-v2"].get("deps", []),
        "remote diarization v2 must follow the completed audio frontier",
    )
    require(
        "research-remote-diarization"
        in nodes["quality-remote-speaker-diarization-v2"].get("deps", []),
        "remote diarization v2 must build on the audit-only speaker evidence",
    )
    require(
        "quality-remote-speaker-diarization-v2"
        in nodes["quality-transcript-perfection-corpus-v1"].get("deps", []),
        "transcript perfection corpus must follow remote diarization v2",
    )
    require(
        nodes["quality-remote-speaker-diarization-v2"].get("status") == "done",
        "remote diarization v2 must remain a completed promoted checkpoint",
    )
    require(
        nodes["quality-transcript-perfection-corpus-v1"].get("status") == "done",
        "transcript perfection corpus must remain a completed baseline",
    )
    require(
        "quality-transcript-perfection-corpus-v1"
        in nodes["quality-remote-speaker-coverage-v3"].get("deps", []),
        "remote speaker coverage v3 must follow the perfection corpus baseline",
    )
    require(
        "quality-remote-speaker-coverage-v3"
        in nodes["quality-remote-speaker-residual-evidence-v4"].get("deps", []),
        "remote speaker residual evidence v4 must follow promoted coverage v3",
    )
    require(
        "quality-remote-speaker-residual-evidence-v4"
        in nodes["product-speaker-resolved-transcript-default-v1"].get("deps", []),
        "speaker-resolved default must follow the current ranked residual closure",
    )
    require(
        nodes["quality-remote-speaker-coverage-v3"].get("status") == "done",
        "remote speaker coverage v3 must remain a completed promoted checkpoint",
    )
    require(
        nodes["quality-remote-speaker-residual-evidence-v4"].get("status") == "done",
        "remote speaker residual evidence v4 must remain a completed measured ceiling",
    )
    require(
        nodes["product-speaker-resolved-transcript-default-v1"].get("status") == "done",
        "speaker-resolved default must remain a completed promoted checkpoint",
    )
    require(
        "product-speaker-resolved-transcript-default-v1"
        in nodes["quality-lexical-accuracy-reference-corpus-v1"].get("deps", []),
        "lexical reference corpus must follow the promoted default transcript",
    )
    require(
        nodes["quality-lexical-accuracy-reference-corpus-v1"].get("status") == "done",
        "lexical reference corpus must remain a completed evidence checkpoint",
    )
    require(
        "quality-lexical-accuracy-reference-corpus-v1"
        in nodes["quality-independent-remote-speaker-evidence-v1"].get("deps", []),
        "independent remote speaker evidence must follow the lexical evidence decision",
    )
    require(
        "quality-remote-speaker-residual-evidence-v4"
        in nodes["quality-independent-remote-speaker-evidence-v1"].get("deps", []),
        "independent remote speaker evidence must target the frozen v4 residual",
    )
    require(
        nodes["quality-independent-remote-speaker-evidence-v1"].get("status") == "done",
        "independent remote speaker evidence must remain a completed measured ceiling",
    )
    require(
        "quality-independent-remote-speaker-evidence-v1"
        in nodes["quality-remote-speaker-residual-reference-corpus-v1"].get("deps", []),
        "remote residual reference must follow independent speaker evidence",
    )
    require(
        nodes["quality-remote-speaker-residual-reference-corpus-v1"].get("status") == "done",
        "remote residual reference corpus must remain a completed evidence checkpoint",
    )
    require(
        "quality-remote-speaker-residual-reference-corpus-v1"
        in nodes["quality-controlled-remote-speaker-truth-lab-v1"].get("deps", []),
        "controlled remote speaker truth lab must follow the blind residual reference decision",
    )
    require(
        nodes["quality-controlled-remote-speaker-truth-lab-v1"].get("status") == "done",
        "controlled remote speaker truth lab must remain a completed exact evidence checkpoint",
    )
    require(
        "quality-controlled-remote-speaker-truth-lab-v1"
        in nodes["quality-duration-aware-remote-speaker-attribution-v2"].get("deps", []),
        "duration-aware remote speaker attribution must follow the controlled truth lab",
    )
    require(
        nodes["quality-duration-aware-remote-speaker-attribution-v2"].get("status") == "done",
        "duration-aware remote speaker attribution must remain a completed measured ceiling",
    )
    require(
        "quality-duration-aware-remote-speaker-attribution-v2"
        in nodes["quality-segment-context-remote-speaker-attribution-v1"].get("deps", []),
        "segment-context remote speaker attribution must follow the duration-aware result",
    )
    require(
        nodes["quality-segment-context-remote-speaker-attribution-v1"].get("status") == "done",
        "segment-context remote speaker attribution must remain a completed measured ceiling",
    )
    require(
        "quality-segment-context-remote-speaker-attribution-v1"
        in nodes["quality-remote-speaker-attribution-error-decomposition-v1"].get("deps", []),
        "remote speaker error decomposition must follow the segment-context result",
    )
    require(
        nodes["quality-remote-speaker-attribution-error-decomposition-v1"].get("status") == "done",
        "remote speaker error decomposition must remain a completed diagnostic checkpoint",
    )
    require(
        "quality-remote-speaker-attribution-error-decomposition-v1"
        in nodes["quality-stronger-remote-speaker-identity-backend-qualification-v1"].get("deps", []),
        "stronger identity backend qualification must follow error decomposition",
    )
    require(
        nodes["quality-stronger-remote-speaker-identity-backend-qualification-v1"].get("status") == "done",
        "stronger remote speaker identity backend qualification must remain a completed lab checkpoint",
    )
    require(
        "quality-stronger-remote-speaker-identity-backend-qualification-v1"
        in nodes["quality-ecapa-remote-speaker-shadow-qualification-v1"].get("deps", []),
        "ECAPA real-session shadow qualification must follow the completed lab qualification",
    )
    require(
        nodes["quality-ecapa-remote-speaker-shadow-qualification-v1"].get("status") == "done",
        "ECAPA remote speaker shadow qualification must remain a completed real-session checkpoint",
    )
    require(
        "quality-ecapa-remote-speaker-shadow-qualification-v1"
        in nodes["quality-remote-speaker-shadow-error-decomposition-v1"].get("deps", []),
        "remote speaker shadow error decomposition must follow ECAPA real-session qualification",
    )
    require(
        nodes["quality-remote-speaker-shadow-error-decomposition-v1"].get("status") == "done",
        "remote speaker shadow error decomposition must remain a completed diagnostic checkpoint",
    )
    require(
        "quality-remote-speaker-shadow-error-decomposition-v1"
        in nodes["quality-bounded-remote-speaker-interval-purification-v1"].get("deps", []),
        "bounded interval purification must follow shadow error decomposition",
    )
    require(
        nodes["quality-bounded-remote-speaker-interval-purification-v1"].get("status") == "done",
        "bounded remote speaker interval purification must remain a completed one-shot checkpoint",
    )
    require(
        "quality-bounded-remote-speaker-interval-purification-v1"
        in nodes["quality-session-local-remote-speaker-enrollment-hardening-v1"].get("deps", []),
        "session-local enrollment hardening must follow bounded interval purification",
    )
    require(
        nodes["quality-session-local-remote-speaker-enrollment-hardening-v1"].get("status")
        == "done",
        "session-local remote speaker enrollment hardening must remain a completed checkpoint",
    )
    require(
        "quality-session-local-remote-speaker-enrollment-hardening-v1"
        in nodes["quality-remote-speaker-direct-truth-seed-v1"].get("deps", []),
        "direct speaker truth seed must follow session-local enrollment hardening",
    )
    require(
        nodes["quality-remote-speaker-direct-truth-seed-v1"].get("status") == "done",
        "remote speaker direct truth seed must remain a completed frozen checkpoint",
    )
    require(
        "quality-remote-speaker-direct-truth-seed-v1"
        in nodes["quality-remote-speaker-direct-truth-candidate-adjudication-v1"].get("deps", []),
        "direct-truth candidate adjudication must follow completed blind review",
    )
    require(
        nodes["quality-remote-speaker-direct-truth-candidate-adjudication-v1"].get("status")
        == "done",
        "direct-truth candidate adjudication must remain a completed checkpoint",
    )
    require(
        "quality-remote-speaker-direct-truth-candidate-adjudication-v1"
        in nodes["quality-remote-speaker-enrollment-purity-abstention-hardening-v2"].get("deps", []),
        "enrollment purity hardening v2 must follow direct-truth adjudication",
    )
    require(
        nodes["quality-remote-speaker-enrollment-purity-abstention-hardening-v2"].get("status")
        == "done",
        "enrollment purity and abstention hardening v2 must remain completed",
    )
    require(
        "quality-remote-speaker-enrollment-purity-abstention-hardening-v2"
        in nodes["quality-session-local-homogeneous-remote-speaker-enrollment-mining-v1"].get("deps", []),
        "homogeneous enrollment mining must follow purity hardening v2",
    )
    require(
        nodes["quality-session-local-homogeneous-remote-speaker-enrollment-mining-v1"].get("status")
        == "done",
        "homogeneous remote speaker enrollment mining must remain completed",
    )
    require(
        "quality-session-local-homogeneous-remote-speaker-enrollment-mining-v1"
        in nodes["quality-lightweight-remote-speaker-representation-frontier-v1"].get("deps", []),
        "lightweight representation frontier must follow homogeneous enrollment mining",
    )
    require(
        nodes["quality-lightweight-remote-speaker-representation-frontier-v1"].get("status")
        == "done",
        "lightweight representation frontier must remain completed",
    )
    require(
        "quality-lightweight-remote-speaker-representation-frontier-v1"
        in nodes["quality-remote-speaker-disjoint-truth-expansion-v2"].get("deps", []),
        "disjoint truth expansion must follow the completed local representation frontier",
    )
    require(
        nodes["quality-remote-speaker-disjoint-truth-expansion-v2"].get("status")
        == "done",
        "disjoint remote-speaker truth expansion must remain completed",
    )
    require(
        "quality-remote-speaker-disjoint-truth-expansion-v2"
        in nodes["quality-disjoint-remote-speaker-model-qualification-v1"].get("deps", []),
        "disjoint model qualification must follow completed truth expansion",
    )
    require(
        nodes["quality-disjoint-remote-speaker-model-qualification-v1"].get("status")
        == "done",
        "disjoint remote-speaker model qualification must remain completed",
    )
    require(
        "quality-disjoint-remote-speaker-model-qualification-v1"
        in nodes["quality-remote-speaker-usability-gate-error-decomposition-v1"].get("deps", []),
        "remote-speaker usability decomposition must follow disjoint model qualification",
    )
    require(
        nodes["quality-remote-speaker-usability-gate-error-decomposition-v1"].get("status")
        == "done",
        "remote-speaker usability decomposition must remain completed",
    )
    require(
        nodes["quality-residual-transcript-integrity-hardening-v1"].get("status") == "done",
        "residual transcript integrity hardening must remain completed",
    )
    require(
        "quality-residual-transcript-integrity-hardening-v1"
        in nodes["quality-remote-speaker-boundary-minority-segmentation-v1"].get("deps", []),
        "remote-speaker boundary work must follow residual transcript integrity hardening",
    )
    require(
        "quality-remote-speaker-usability-gate-error-decomposition-v1"
        in nodes["quality-remote-speaker-boundary-minority-segmentation-v1"].get("deps", []),
        "remote-speaker boundary and minority segmentation must follow purity decomposition",
    )
    require(
        nodes["quality-remote-speaker-boundary-minority-segmentation-v1"].get("status")
        == "done",
        "remote-speaker boundary and minority segmentation must remain completed",
    )
    require(
        nodes["quality-post-segmentation-transcript-rebaseline-v1"].get("status") == "done",
        "post-segmentation transcript rebaseline must remain completed",
    )
    require(
        "quality-post-segmentation-transcript-rebaseline-v1"
        in nodes["quality-capture-continuity-loss-closure-v1"].get("deps", []),
        "capture continuity closure must follow the fresh transcript rebaseline",
    )
    require(
        nodes["quality-capture-continuity-loss-closure-v1"].get("status") == "done",
        "capture continuity loss closure must remain completed",
    )
    require(
        "quality-capture-continuity-loss-closure-v1"
        in nodes["quality-remote-unknown-evidence-recovery-v1"].get("deps", []),
        "remote unknown recovery must follow source continuity closure",
    )
    require(
        nodes["quality-remote-unknown-evidence-recovery-v1"].get("status") == "done",
        "remote unknown evidence recovery must remain completed",
    )
    require(
        nodes["quality-human-reviewed-lexical-seed-v1"].get("status") == "current",
        "human-reviewed lexical seed must be the current direct-evidence goal",
    )
    require(
        nodes["quality-session-scoped-lexical-context-v1"].get("status") == "blocked",
        "session-scoped lexical context must remain blocked on direct lexical truth",
    )
    require(
        nodes["product-speaker-resolved-transcript-terminal-gate-v1"].get("status") == "later",
        "speaker-resolved terminal gate must remain a dependent milestone",
    )
    require(
        nodes["quality-local-multi-speaker-diarization-v1"].get("status") == "idea",
        "local multi-speaker diarization must remain a conditional idea",
    )
    require(
        nodes["research-heavy-local-asr-validator-v1"].get("status") == "idea",
        "heavy local ASR validator must remain a conditional research idea",
    )
    require("parked-ui" not in nodes, "UI must not occupy the active CLI roadmap")


def validate_markdown() -> None:
    for path in ACTIVE_DOCS:
        require(path.is_file(), f"missing active document: {path.relative_to(ROOT)}")
        text = path.read_text(encoding="utf-8")
        require(text.count("```") % 2 == 0, f"unbalanced fenced code block in {path.relative_to(ROOT)}")
        require(not LOCAL_ABSOLUTE_RE.search(text), f"local absolute path found in {path.relative_to(ROOT)}")
        mermaid_blocks = MERMAID_RE.findall(text)
        require(
            len(mermaid_blocks) == text.count("```mermaid"),
            f"malformed Mermaid fence in {path.relative_to(ROOT)}",
        )
        for block in mermaid_blocks:
            first_line = next((line.strip() for line in block.splitlines() if line.strip()), "")
            require(
                first_line.startswith(("flowchart ", "graph ", "sequenceDiagram", "stateDiagram")),
                f"unsupported or empty Mermaid block in {path.relative_to(ROOT)}",
            )

        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            clean_target = unquote(target.split("#", 1)[0])
            resolved = (path.parent / clean_target).resolve()
            require(resolved.exists(), f"broken link in {path.relative_to(ROOT)}: {target}")

    require(len(README_PATH.read_text(encoding="utf-8").splitlines()) <= 400, "README must stay under 400 lines")
    require(len(ROADMAP_PATH.read_text(encoding="utf-8").splitlines()) <= 300, "readable roadmap must stay under 300 lines")
    require(len(CURRENT_GOAL_PATH.read_text(encoding="utf-8").splitlines()) <= 180, "current-goal must stay under 180 lines")

    for path in REQUIRED_ARCHIVES:
        require(path.is_file(), f"missing historical snapshot: {path.relative_to(ROOT)}")


def main() -> int:
    try:
        plan = load_plan()
        official_validation = validate_official_opskarta()
        nodes, current_goal_id = validate_statuses_and_goal(plan)
        validate_dependencies(nodes, current_goal_id)
        validate_markdown()
    except PlanningError as error:
        print(f"planning consistency: failed: {error}", file=sys.stderr)
        return 1

    print(
        "planning consistency: ok "
        f"(nodes={len(nodes)}, current_goal={nodes[current_goal_id]['title']}, "
        f"official_opskarta={'yes' if official_validation else 'unavailable'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
