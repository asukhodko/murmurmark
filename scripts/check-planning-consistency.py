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
    ROOT / "docs/architecture/experimental-sidecar.md",
    ROOT / "docs/rfc/0001-v1-scope.md",
)

REQUIRED_ARCHIVES = (
    ROOT / "docs/history/README-development-log-through-2026-07-19.md",
    ROOT / "docs/history/current-goal-through-2026-07-19.md",
    ROOT / "docs/history/murmurmark-cli-roadmap-through-2026-07-19.md",
    ROOT / "docs/history/murmurmark-cli-roadmap-through-2026-07-19.plan.yaml",
)

CRITICAL_PATH = (
    "quality-residual-chronology-closure-v1",
    "quality-operational-rebaseline-v1",
    "quality-echo-suppression-promotion",
    "product-evidence-export-v2",
    "product-release-quality-cli",
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
    require(len(nodes) <= 35, f"active plan is too large: {len(nodes)} nodes, expected at most 35")

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

    require(current_goal_id == CRITICAL_PATH[0], "current goal must be the first critical-path stage")
    require(
        nodes["quality-residual-local-recall-closure-v1"].get("status") == "done",
        "Residual Local Recall Closure must be done before chronology becomes current",
    )
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
        "research-remote-diarization" in nodes["research-speaker-map"].get("deps", []),
        "speaker map must follow remote diarization",
    )
    require(
        "research-speaker-map" in nodes["research-rich-transcript"].get("deps", []),
        "rich transcript must follow speaker mapping",
    )
    require(nodes["parked-live-promotion"].get("status") == "blocked", "live promotion must stay blocked")
    require(nodes["parked-ui"].get("status") == "optional", "UI must stay optional")


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
