#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GENERATOR_VERSION = "0.3.1"
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_./+-]+")

DEFAULT_RULES: dict[str, Any] = {
    "selection": {
        "max_outline_blocks": 8,
        "max_decisions": 5,
        "max_actions": 8,
        "max_risks": 5,
        "max_open_questions": 5,
        "representative_utterances_per_block": 4,
        "topic_keywords_per_block": 4,
    },
    "thresholds": {
        "selected_action": 75,
        "candidate_action": 55,
        "weak_action": 35,
        "selected_decision": 75,
        "candidate_decision": 55,
        "weak_decision": 35,
        "selected_risk": 70,
        "candidate_risk": 50,
        "weak_risk": 35,
        "selected_open_question": 70,
        "candidate_open_question": 50,
        "weak_open_question": 35,
    },
    "outline": {
        "min_block_sec": 120,
        "target_block_sec": 480,
        "max_block_sec": 720,
        "pause_boundary_sec": 30,
        "strong_pause_boundary_sec": 60,
        "min_salience_score": 18,
    },
}


def review_report_is_partial_scope_only(review_report: dict[str, Any]) -> bool:
    gates = review_report.get("gates") if isinstance(review_report.get("gates"), dict) else {}
    summary = review_report.get("summary") if isinstance(review_report.get("summary"), dict) else {}
    coverage = review_report.get("coverage") if isinstance(review_report.get("coverage"), dict) else {}
    hard_failures = set(gates.get("hard_failures") or [])
    if hard_failures != {"incomplete_review_scope"}:
        return False
    if coverage.get("complete") is True:
        return False
    applied = int(summary.get("applied_decision_rows") or 0)
    conflicts = int(summary.get("conflict_count") or 0)
    rejected = int(summary.get("rejected_decision_rows") or 0)
    return applied > 0 and conflicts == 0 and rejected == 0

STOP_WORDS = {
    "а",
    "бы",
    "в",
    "во",
    "вообще",
    "вот",
    "все",
    "всё",
    "всех",
    "всего",
    "да",
    "давайте",
    "давай",
    "для",
    "его",
    "ее",
    "её",
    "же",
    "здесь",
    "и",
    "если",
    "или",
    "как",
    "как-то",
    "какая",
    "какие",
    "какие-то",
    "какой",
    "какой-то",
    "когда",
    "который",
    "которая",
    "которые",
    "которых",
    "меня",
    "мы",
    "на",
    "надо",
    "нам",
    "нас",
    "наш",
    "наша",
    "наше",
    "наши",
    "ну",
    "о",
    "об",
    "он",
    "она",
    "они",
    "по",
    "пока",
    "потому",
    "почему",
    "просто",
    "с",
    "со",
    "сейчас",
    "там",
    "типа",
    "то",
    "тут",
    "ты",
    "у",
    "это",
    "этот",
    "эта",
    "эти",
    "этим",
    "этими",
    "этих",
    "этой",
    "этом",
    "этого",
    "этому",
    "эту",
    "такой",
    "такое",
    "такую",
    "такая",
    "такие",
    "что",
    "чтобы",
    "я",
    "еще",
    "ещё",
    "есть",
}
FILLER_WORDS = {
    "ага",
    "алло",
    "да",
    "ладно",
    "ну",
    "ок",
    "окей",
    "понял",
    "сейчас",
    "так",
    "угу",
    "хм",
    "хм-хм",
    "это",
}
DOMAIN_TERMS = {
    "api",
    "backend",
    "ci",
    "ci/cd",
    "deploy",
    "git",
    "gitlab",
    "github",
    "kubernetes",
    "mcp",
    "merge",
    "mr",
    "openapi",
    "pipeline",
    "retro",
    "sre",
    "slo",
    "админка",
    "агент",
    "агенты",
    "алерт",
    "алерты",
    "алертами",
    "бэкенд",
    "дашборд",
    "деплой",
    "деплоя",
    "дока",
    "доки",
    "документация",
    "квота",
    "квоты",
    "лог",
    "логи",
    "миграция",
    "пайплайн",
    "пайплайна",
    "прод",
    "ретро",
    "сервис",
    "сервисы",
    "стейдж",
    "стейджи",
    "троттлинг",
    "фича",
}

ACTION_STRONG_MARKERS = (
    "я сделаю",
    "я посмотрю",
    "я проверю",
    "я добавлю",
    "я допишу",
    "я создам",
    "я заведу",
    "я перенесу",
    "я отпишусь",
    "я скину",
    "я подготовлю",
    "я обновлю",
    "я поправлю",
    "я разберусь",
    "я возьму",
    "беру на себя",
    "за мной",
)
ACTION_MEDIUM_MARKERS = ("надо", "нужно", "нужен", "нужна", "давай", "давайте", "стоит")
ACTION_SOFT_MARKERS = ("имеет смысл", "лучше", "можно", "попробуем", "попробовать")
ACTION_VERBS = {
    "добавить",
    "дописать",
    "завести",
    "задеплоить",
    "замержить",
    "запросить",
    "заревьюить",
    "исправить",
    "мигрировать",
    "обновить",
    "отписать",
    "перенести",
    "переехать",
    "перекинуть",
    "подготовить",
    "померить",
    "посчитать",
    "починить",
    "проверить",
    "протестировать",
    "согласовать",
    "собрать",
    "создать",
    "спросить",
    "уточнить",
    "выкатить",
    "скинуть",
}
WEAK_ACTION_VERBS = {"подумать", "понять", "обсудить", "поговорить", "посмотреть", "разобраться"}
ABSTRACT_ACTION_PATTERNS = (
    "надо понимать",
    "надо сказать",
    "надо иметь в виду",
    "надо признать",
    "надо вообще",
    "нужно понимать",
    "нужно сказать",
    "давай поговорим",
    "давай обсудим",
    "как бы надо",
    "в целом надо",
    "по-хорошему надо",
)
MEETING_FACILITATION_PATTERNS = (
    "давайте перейдем",
    "давайте перейдём",
    "давайте переходить",
    "давайте проголосуем",
    "давайте голосовать",
    "давайте обсудим",
    "давай обсудим",
    "давайте дальше",
    "давайте побежали",
    "давайте продолжим",
    "давайте посмотрим",
    "давайте воспользуемся",
    "перейдем к следующ",
    "перейдём к следующ",
    "проголосовать",
    "голосовать",
    "зелеными кружочками",
    "зелёными кружочками",
    "давайте сфокусируемся",
    "давайте сфокусимся",
    "давай сфокусируемся",
    "давай сфокусимся",
    "следующий блок",
    "следующему блоку",
    "следующая тема",
    "ссылку на всех",
    "ссылка на всех",
    "по таймингу",
    "тайминг",
    "мемы ставим",
    "мемки",
    "анонимно",
    "ткнуть в кнопочку",
    "поднимите руку",
)
TOPIC_GENERIC_WORDS = STOP_WORDS | {
    "блок",
    "блока",
    "блоку",
    "будет",
    "больше",
    "будто",
    "была",
    "были",
    "было",
    "быть",
    "говорить",
    "говорю",
    "говорят",
    "берут",
    "будем",
    "будешь",
    "будут",
    "делаем",
    "делать",
    "думаю",
    "даже",
    "кажется",
    "какие-то",
    "какой-то",
    "кстати",
    "короче",
    "знаю",
    "знаем",
    "может",
    "можно",
    "наверное",
    "например",
    "момент",
    "моменты",
    "возможно",
    "всеми",
    "большую",
    "взять",
    "него",
    "ощущение",
    "особо",
    "очень",
    "понятно",
    "помню",
    "получается",
    "после",
    "посмотреть",
    "потом",
    "поэтому",
    "привет",
    "про",
    "раз",
    "раза",
    "разом",
    "разные",
    "тобой",
    "сильно",
    "сказать",
    "сделать",
    "слышал",
    "смотрим",
    "стало",
    "стоит",
    "ставим",
    "спасибо",
    "тебе",
    "тему",
    "тогда",
    "тоже",
    "хотим",
    "хочется",
    "чего",
    "хорошо",
    "лучше",
    "что-то",
    "какую-то",
    "конечно",
}
TOPIC_LABEL_TRANSLATIONS = {
    "alert": "alerts",
    "alerts": "alerts",
    "алерт": "alerts",
    "алерты": "alerts",
    "алертами": "alerts",
    "дашборд": "dashboards",
    "дашборды": "dashboards",
    "дежурство": "дежурство",
    "дежурства": "дежурство",
    "дьюти": "дежурство",
    "задача": "задачи",
    "задачи": "задачи",
    "задачу": "задачи",
    "команда": "команда",
    "команды": "команда",
    "метрика": "метрики",
    "метрики": "метрики",
    "обращаемость": "обращаемость",
    "планирование": "планирование",
    "ретро": "ретро",
    "спринт": "спринт",
    "спринта": "спринт",
    "срок": "сроки",
    "сроки": "сроки",
}

DECISION_EXPLICIT_MARKERS = (
    "решили",
    "договорились",
    "согласовали",
    "зафиксировали",
    "фиксируем",
    "принимаем",
    "решение такое",
    "итого",
    "вывод",
)
DECISION_DIALOGUE_MARKERS = (
    "окей, тогда",
    "тогда",
    "да, так и сделаем",
    "так и сделаем",
    "оставляем",
    "берём",
    "берем",
    "не делаем",
    "откладываем",
    "переносим",
    "выбираем",
    "идём с",
    "идем с",
    "остаемся на",
    "остаёмся на",
    "делаем так",
)
PROPOSAL_MARKERS = (
    "давай",
    "предлагаю",
    "оставим",
    "берём",
    "берем",
    "не будем",
    "пока не",
    "перенесём",
    "перенесем",
    "отложим",
    "идём по",
    "идем по",
    "сделаем так",
)
AGREEMENT_MARKERS = ("окей", "согласен", "согласна", "договорились", "так и сделаем", "подходит", "хорошо")

RISK_STRONG_MARKERS = (
    "риск",
    "опасно",
    "сломается",
    "может сломаться",
    "не успеем",
    "не взлетит",
    "заблокирует",
    "блокирует",
    "блокер",
    "узкое место",
    "отвалится",
    "деградирует",
    "потеряем",
    "не получится",
    "не хватает",
    "зависим от",
    "нет доступа",
    "нет данных",
    "сложно доказать",
    "сомнение",
    "под вопросом",
)
RISK_MEDIUM_MARKERS = ("проблема", "непонятно", "сложно", "тяжело", "дорого", "медленно", "нестабильно", "хрупко", "неочевидно")
RISK_CONSEQUENCE_MARKERS = ("если", "когда", "иначе", "может", "не сможем", "не успеем", "сломается", "заблокирует")
RISK_SOLVED_PATTERNS = ("проблема решена", "проблема будет решена", "скоро будет решена", "уже решена")

OPEN_QUESTION_STRONG_PATTERNS = (
    "как именно",
    "кто будет",
    "что делать",
    "какой вариант",
    "надо понять",
    "надо выяснить",
    "надо уточнить",
    "не ясно",
    "неясно",
    "непонятно",
    "остается вопрос",
    "остаётся вопрос",
)
QUESTION_WORDS = ("как", "кто", "когда", "что", "почему", "зачем", "сколько", "какой", "какая", "какие")
TOPIC_ONLY_QUESTION_PATTERNS = ("вопрос по", "вопрос с", "тема вопроса", "обсудим вопрос")
ANSWER_MARKERS = ("да", "нет", "потому что", "это", "мы", "я", "нужно", "давай", "тогда", "ответ", "причина")

DISCOURSE_MARKERS = (
    "теперь",
    "дальше",
    "следующее",
    "вторая тема",
    "по поводу",
    "давай про",
    "тогда про",
    "возвращаясь",
    "кстати",
    "ещё момент",
    "еще момент",
    "отдельно",
    "перейдём",
    "перейдем",
    "закрыли",
    "окей, теперь",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local extractive MurmurMark notes from transcript artifacts.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--transcript-profile",
        choices=(
            "auto",
            "current",
            "shadow_v2",
            "audit_cleanup_v1",
            "audit_cleanup_v2",
            "audit_cleanup_v3",
            "audit_cleanup_v4",
            "audit_cleanup_v5",
            "audit_cleanup_v6",
            "audit_cleanup_v7",
            "reviewed_v1",
            "agent_reviewed_v1",
            "suggested_review_v1",
            "order_repair_v1",
            "local_recall_repair_v1",
        ),
        default="auto",
        help="Transcript artifact profile to synthesize from.",
    )
    return parser.parse_args()


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def shell_path(path: Path) -> str:
    return shlex.quote(display_path(path))


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def synthesis_handoff(session: Path, out_dir: Path, verdict_payload: dict[str, Any]) -> dict[str, Any]:
    session_arg = shell_path(session)
    verdict = str(verdict_payload.get("verdict") or "")
    review_summary = verdict_payload.get("review_summary") if isinstance(verdict_payload.get("review_summary"), dict) else {}
    review_item_count = safe_int(review_summary.get("review_item_count") if review_summary else 0)
    risk_items = verdict_payload.get("risk_items") if isinstance(verdict_payload.get("risk_items"), list) else []
    needs_review = review_item_count > 0 or bool(risk_items) or verdict == "usable_with_review"
    can_export = verdict == "good" and not needs_review

    next_commands: list[dict[str, str]] = []
    if needs_review:
        next_commands.append(
            {
                "id": "review_next",
                "command": f"murmurmark review next {session_arg}",
                "reason": "review required before export or high-confidence use",
            }
        )
    next_commands.extend(
        [
            {
                "id": "open_notes_summary",
                "command": f"murmurmark notes {session_arg}",
                "reason": "read the selected extractive notes",
            },
            {
                "id": "open_transcript_summary",
                "command": f"murmurmark transcript {session_arg}",
                "reason": "inspect the selected transcript profile",
            },
            {
                "id": "refresh_session_report",
                "command": f"murmurmark report {session_arg}",
                "reason": "refresh readiness after synthesis or review changes",
            },
        ]
    )
    if can_export:
        next_commands.append(
            {
                "id": "export_markdown_bundle",
                "command": f"murmurmark export {session_arg} --format markdown --include-json",
                "reason": "export a reviewed good-quality local bundle",
            }
        )

    open_commands = [
        {
            "id": "open_quality_verdict",
            "command": f"less {shell_path(out_dir / 'quality_verdict.md')}",
            "path": display_path(out_dir / "quality_verdict.md"),
        },
        {
            "id": "open_notes",
            "command": f"less {shell_path(out_dir / 'notes.md')}",
            "path": display_path(out_dir / "notes.md"),
        },
        {
            "id": "open_review_items",
            "command": f"less {shell_path(out_dir / 'review_items.jsonl')}",
            "path": display_path(out_dir / "review_items.jsonl"),
        },
    ]
    return {
        "recommended_next": next_commands[0]["command"],
        "next_commands": next_commands,
        "open_commands": open_commands,
    }


def read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"missing file: {path}"
    except json.JSONDecodeError as error:
        return None, f"invalid json: {path}: {error}"


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def format_time(seconds: float | int | None) -> str:
    if seconds is None:
        return "??:??"
    total = max(0, int(float(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def safe_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def cleanup_report_has_material_change(summary: dict[str, Any]) -> bool:
    return (
        safe_int(summary.get("applied_patches")) > 0
        or safe_number(summary.get("segment_repaired_remote_duplicate_seconds")) > 0.0
    )


def clean_text(text: Any, limit: int = 280) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def lower_text(text: Any) -> str:
    return str(text or "").lower().replace("ё", "е")


def tokens(text: Any) -> list[str]:
    normalized: list[str] = []
    for token in TOKEN_RE.findall(str(text or "")):
        clean = token.strip(".,!?;:()[]{}«»\"'`")
        if clean:
            normalized.append(clean.replace("ё", "е").lower())
    return normalized


def content_tokens(text: Any) -> list[str]:
    return [token for token in tokens(text) if token not in STOP_WORDS and token not in FILLER_WORDS and len(token) > 2]


def utterance_id(row: dict[str, Any], index: int) -> str:
    return str(row.get("id") or f"utt_{index + 1:06d}")


def role(row: dict[str, Any]) -> str:
    return str(row.get("speaker_label") or row.get("role") or row.get("source_track") or "Unknown")


def source_profile_paths(resolved_dir: Path, requested_profile: str) -> dict[str, Path]:
    suffix = "" if requested_profile == "current" else f".{requested_profile}"
    return {
        "clean_dialogue": resolved_dir / f"clean_dialogue{suffix}.json",
        "quality_report": resolved_dir / f"quality_report{suffix}.json",
        "overlaps": resolved_dir / f"overlaps{suffix}.json",
        "repair_comparison": resolved_dir / "repair_comparison.json",
        "audit_cleanup_report": resolved_dir.parent / "audit-cleanup" / f"audit_cleanup_report{suffix}.json",
        "review_decisions_report": resolved_dir.parent / "review-decisions" / f"review_decisions_report{suffix}.json",
        "order_repair_report": resolved_dir.parent / "order-repair" / f"transcript_order_repair_report{suffix}.json",
        "local_recall_repair_report": resolved_dir.parent / "local-recall-repair" / f"local_recall_repair_report{suffix}.json",
    }


def choose_profile(resolved_dir: Path, requested_profile: str) -> tuple[str, dict[str, Path], dict[str, Any] | None, list[dict[str, Any]]]:
    risk_items: list[dict[str, Any]] = []
    repair_comparison: dict[str, Any] | None = None

    def order_repair_for(base_profile: str) -> tuple[str, dict[str, Path]] | None:
        order_paths = source_profile_paths(resolved_dir, "order_repair_v1")
        order_report, order_error = read_json(order_paths["order_repair_report"])
        if order_error is not None or not isinstance(order_report, dict):
            return None
        gates = order_report.get("gates") if isinstance(order_report.get("gates"), dict) else {}
        summary = order_report.get("summary") if isinstance(order_report.get("summary"), dict) else {}
        try:
            applied_repairs = int(summary.get("applied_repairs", 0) or 0)
        except (TypeError, ValueError):
            applied_repairs = 0
        if (
            order_paths["clean_dialogue"].exists()
            and order_paths["quality_report"].exists()
            and gates.get("passed") is True
            and applied_repairs > 0
            and order_report.get("input_profile") == base_profile
        ):
            return "order_repair_v1", order_paths
        return None

    if requested_profile == "auto":
        comparison_path = resolved_dir / "repair_comparison.json"
        comparison, error = read_json(comparison_path)
        if error is None and isinstance(comparison, dict):
            repair_comparison = comparison
        cleanup_v7_paths = source_profile_paths(resolved_dir, "audit_cleanup_v7")
        cleanup_v7_report, cleanup_v7_error = read_json(cleanup_v7_paths["audit_cleanup_report"])
        cleanup_v7_summary = cleanup_v7_report.get("summary") if isinstance(cleanup_v7_report, dict) else {}
        if (
            cleanup_v7_paths["clean_dialogue"].exists()
            and cleanup_v7_error is None
            and isinstance(cleanup_v7_report, dict)
            and isinstance(cleanup_v7_report.get("gates"), dict)
            and cleanup_v7_report["gates"].get("passed") is True
            and isinstance(cleanup_v7_summary, dict)
            and cleanup_report_has_material_change(cleanup_v7_summary)
        ):
            repaired = order_repair_for("audit_cleanup_v7")
            if repaired:
                return repaired[0], repaired[1], repair_comparison, risk_items
            return "audit_cleanup_v7", cleanup_v7_paths, repair_comparison, risk_items
        reviewed_paths = source_profile_paths(resolved_dir, "reviewed_v1")
        reviewed_report, reviewed_error = read_json(reviewed_paths["review_decisions_report"])
        if (
            reviewed_paths["clean_dialogue"].exists()
            and reviewed_error is None
            and isinstance(reviewed_report, dict)
            and isinstance(reviewed_report.get("gates"), dict)
            and reviewed_report["gates"].get("passed") is True
        ):
            repaired = order_repair_for("reviewed_v1")
            if repaired:
                return repaired[0], repaired[1], repair_comparison, risk_items
            return "reviewed_v1", reviewed_paths, repair_comparison, risk_items
        agent_paths = source_profile_paths(resolved_dir, "agent_reviewed_v1")
        agent_report, agent_error = read_json(agent_paths["review_decisions_report"])
        if (
            agent_paths["clean_dialogue"].exists()
            and agent_error is None
            and isinstance(agent_report, dict)
            and isinstance(agent_report.get("gates"), dict)
            and agent_report["gates"].get("passed") is True
        ):
            repaired = order_repair_for("agent_reviewed_v1")
            if repaired:
                return repaired[0], repaired[1], repair_comparison, risk_items
            return "agent_reviewed_v1", agent_paths, repair_comparison, risk_items
        for cleanup_profile in ("audit_cleanup_v6", "audit_cleanup_v5", "audit_cleanup_v4", "audit_cleanup_v3", "audit_cleanup_v2", "audit_cleanup_v1"):
            cleanup_paths = source_profile_paths(resolved_dir, cleanup_profile)
            cleanup_report, cleanup_error = read_json(cleanup_paths["audit_cleanup_report"])
            summary = cleanup_report.get("summary") if isinstance(cleanup_report, dict) else {}
            if (
                cleanup_profile in {"audit_cleanup_v3", "audit_cleanup_v4", "audit_cleanup_v5", "audit_cleanup_v6", "audit_cleanup_v7"}
                and (not isinstance(summary, dict) or not cleanup_report_has_material_change(summary))
            ):
                continue
            if (
                cleanup_paths["clean_dialogue"].exists()
                and cleanup_error is None
                and isinstance(cleanup_report, dict)
                and isinstance(cleanup_report.get("gates"), dict)
                and cleanup_report["gates"].get("passed") is True
            ):
                repaired = order_repair_for(cleanup_profile)
                if repaired:
                    return repaired[0], repaired[1], repair_comparison, risk_items
                return cleanup_profile, cleanup_paths, repair_comparison, risk_items
        shadow_paths = source_profile_paths(resolved_dir, "shadow_v2")
        if shadow_paths["clean_dialogue"].exists() and repair_comparison and repair_comparison.get("passed") is True:
            repaired = order_repair_for("shadow_v2")
            if repaired:
                return repaired[0], repaired[1], repair_comparison, risk_items
            return "shadow_v2", shadow_paths, repair_comparison, risk_items
        repaired = order_repair_for("current")
        if repaired:
            return repaired[0], repaired[1], repair_comparison, risk_items
        return "current", source_profile_paths(resolved_dir, "current"), repair_comparison, risk_items

    if requested_profile in {"audit_cleanup_v1", "audit_cleanup_v2", "audit_cleanup_v3", "audit_cleanup_v4", "audit_cleanup_v5", "audit_cleanup_v6", "audit_cleanup_v7"}:
        paths = source_profile_paths(resolved_dir, requested_profile)
        comparison, error = read_json(paths["repair_comparison"])
        if error is None and isinstance(comparison, dict):
            repair_comparison = comparison
        cleanup_report, cleanup_error = read_json(paths["audit_cleanup_report"])
        if cleanup_error is not None:
            risk_items.append({"type": "missing_audit_cleanup_report", "severity": "high", "reason": cleanup_error})
        elif not isinstance(cleanup_report, dict) or not isinstance(cleanup_report.get("gates"), dict) or cleanup_report["gates"].get("passed") is not True:
            risk_items.append(
                {
                    "type": "audit_cleanup_gates_failed",
                    "severity": "high",
                    "reason": "audit cleanup profile was requested but cleanup gates did not pass",
                }
            )
        return requested_profile, paths, repair_comparison, risk_items

    if requested_profile == "reviewed_v1":
        paths = source_profile_paths(resolved_dir, "reviewed_v1")
        comparison, error = read_json(paths["repair_comparison"])
        if error is None and isinstance(comparison, dict):
            repair_comparison = comparison
        review_report, review_error = read_json(paths["review_decisions_report"])
        if review_error is not None:
            risk_items.append({"type": "missing_review_decisions_report", "severity": "high", "reason": review_error})
        elif (
            not isinstance(review_report, dict)
            or not isinstance(review_report.get("gates"), dict)
            or (
                review_report["gates"].get("passed") is not True
                and not review_report_is_partial_scope_only(review_report)
            )
        ):
            risk_items.append(
                {
                    "type": "review_decisions_gates_failed",
                    "severity": "high",
                    "reason": "reviewed profile was requested but review decision gates did not pass",
                }
            )
        return "reviewed_v1", paths, repair_comparison, risk_items

    if requested_profile == "agent_reviewed_v1":
        paths = source_profile_paths(resolved_dir, "agent_reviewed_v1")
        comparison, error = read_json(paths["repair_comparison"])
        if error is None and isinstance(comparison, dict):
            repair_comparison = comparison
        review_report, review_error = read_json(paths["review_decisions_report"])
        if review_error is not None:
            risk_items.append({"type": "missing_agent_review_decisions_report", "severity": "high", "reason": review_error})
        elif (
            not isinstance(review_report, dict)
            or not isinstance(review_report.get("gates"), dict)
            or (
                review_report["gates"].get("passed") is not True
                and not review_report_is_partial_scope_only(review_report)
            )
        ):
            risk_items.append(
                {
                    "type": "agent_review_decisions_gates_failed",
                    "severity": "high",
                    "reason": "agent-reviewed profile was requested but review decision gates did not pass",
                }
            )
        return "agent_reviewed_v1", paths, repair_comparison, risk_items

    if requested_profile == "suggested_review_v1":
        paths = source_profile_paths(resolved_dir, "suggested_review_v1")
        comparison, error = read_json(paths["repair_comparison"])
        if error is None and isinstance(comparison, dict):
            repair_comparison = comparison
        review_report, review_error = read_json(paths["review_decisions_report"])
        if review_error is not None:
            risk_items.append({"type": "missing_suggested_review_report", "severity": "high", "reason": review_error})
        elif not isinstance(review_report, dict) or not isinstance(review_report.get("gates"), dict) or review_report["gates"].get("passed") is not True:
            risk_items.append(
                {
                    "type": "suggested_review_gates_failed",
                    "severity": "high",
                    "reason": "suggested review profile was requested but review decision gates did not pass",
                }
            )
        else:
            risk_items.append(
                {
                    "type": "suggested_review_candidate_profile",
                    "severity": "medium",
                    "reason": "profile was generated from suggested decisions and is for comparison only, not human-reviewed",
                }
            )
        return "suggested_review_v1", paths, repair_comparison, risk_items

    if requested_profile == "order_repair_v1":
        paths = source_profile_paths(resolved_dir, "order_repair_v1")
        comparison, error = read_json(paths["repair_comparison"])
        if error is None and isinstance(comparison, dict):
            repair_comparison = comparison
        order_report, order_error = read_json(paths["order_repair_report"])
        if order_error is not None:
            risk_items.append({"type": "missing_transcript_order_repair_report", "severity": "high", "reason": order_error})
        elif not isinstance(order_report, dict) or not isinstance(order_report.get("gates"), dict) or order_report["gates"].get("passed") is not True:
            risk_items.append(
                {
                    "type": "transcript_order_repair_gates_failed",
                    "severity": "high",
                    "reason": "order repair profile was requested but order repair gates did not pass",
                }
            )
        return "order_repair_v1", paths, repair_comparison, risk_items

    if requested_profile == "local_recall_repair_v1":
        paths = source_profile_paths(resolved_dir, "local_recall_repair_v1")
        comparison, error = read_json(paths["repair_comparison"])
        if error is None and isinstance(comparison, dict):
            repair_comparison = comparison
        repair_report, repair_error = read_json(paths["local_recall_repair_report"])
        if repair_error is not None:
            risk_items.append({"type": "missing_local_recall_repair_report", "severity": "high", "reason": repair_error})
        elif not isinstance(repair_report, dict) or not isinstance(repair_report.get("gates"), dict) or repair_report["gates"].get("passed") is not True:
            risk_items.append(
                {
                    "type": "local_recall_repair_gates_failed",
                    "severity": "high",
                    "reason": "local-recall repair profile was requested but repair gates did not pass",
                }
            )
        else:
            summary = repair_report.get("summary") if isinstance(repair_report.get("summary"), dict) else {}
            if safe_number(summary.get("applied_repairs")) > 0:
                risk_items.append(
                    {
                        "type": "inserted_local_recall_me_turns_need_review",
                        "severity": "medium",
                        "reason": "local-recall repair inserted Me turns that should be checked before external export",
                    }
                )
        return "local_recall_repair_v1", paths, repair_comparison, risk_items

    if requested_profile == "shadow_v2":
        paths = source_profile_paths(resolved_dir, "shadow_v2")
        comparison, error = read_json(paths["repair_comparison"])
        if error is None and isinstance(comparison, dict):
            repair_comparison = comparison
        if not repair_comparison or repair_comparison.get("passed") is not True:
            risk_items.append(
                {
                    "type": "shadow_profile_without_passing_comparison",
                    "severity": "high",
                    "reason": "shadow_v2 was requested but repair_comparison.json did not pass",
                }
            )
        return "shadow_v2", paths, repair_comparison, risk_items

    return "current", source_profile_paths(resolved_dir, "current"), None, risk_items


def load_inputs(selected_profile: str, paths: dict[str, Path]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    dialogue, dialogue_error = read_json(paths["clean_dialogue"])
    quality, quality_error = read_json(paths["quality_report"])
    overlaps, overlaps_error = read_json(paths["overlaps"])
    cleanup_report: dict[str, Any] | None = None
    if paths.get("audit_cleanup_report") and paths["audit_cleanup_report"].exists():
        cleanup_candidate, cleanup_error = read_json(paths["audit_cleanup_report"])
        if cleanup_error is None and isinstance(cleanup_candidate, dict):
            cleanup_report = cleanup_candidate

    risk_items: list[dict[str, Any]] = []
    if dialogue_error:
        risk_items.append({"type": "missing_clean_dialogue", "severity": "fatal", "reason": dialogue_error})
        dialogue = {"schema": None, "utterances": []}
    if quality_error:
        risk_items.append({"type": "missing_quality_report", "severity": "medium", "reason": quality_error})
        quality = {}
    if overlaps_error:
        overlaps = {"schema": None, "overlaps": []}

    if not isinstance(dialogue, dict) or dialogue.get("schema") != "murmurmark.clean_dialogue/v1":
        risk_items.append(
            {
                "type": "invalid_clean_dialogue_schema",
                "severity": "fatal",
                "reason": f"expected murmurmark.clean_dialogue/v1 for profile {selected_profile}",
            }
        )
        dialogue = {"schema": None, "utterances": []}

    utterances = dialogue.get("utterances") if isinstance(dialogue, dict) else []
    if not isinstance(utterances, list):
        risk_items.append({"type": "invalid_utterances", "severity": "fatal", "reason": "clean_dialogue.utterances is not an array"})
        utterances = []

    overlap_rows = overlaps.get("overlaps") if isinstance(overlaps, dict) else []
    if not isinstance(overlap_rows, list):
        overlap_rows = []

    for index, row in enumerate(utterances):
        if isinstance(row, dict):
            row.setdefault("id", utterance_id(row, index))

    if isinstance(quality, dict) and cleanup_report:
        gates = cleanup_report.get("gates") if isinstance(cleanup_report.get("gates"), dict) else {}
        explanation = gates.get("local_recall_explanation")
        if explanation:
            quality["local_recall_low_score_explained"] = True
            quality["local_recall_explanation"] = explanation

    return quality if isinstance(quality, dict) else {}, utterances, overlap_rows + risk_items


def metrics_from_quality(quality: dict[str, Any], utterances: list[dict[str, Any]], overlap_rows: list[dict[str, Any]]) -> dict[str, Any]:
    needs_review = int(quality.get("needs_review_count", sum(1 for row in utterances if row.get("quality", {}).get("needs_review"))))
    utterance_count = int(quality.get("utterances", len(utterances)))
    cross_gt2 = int(quality.get("cross_role_overlap_gt2_count", sum(1 for row in overlap_rows if float(row.get("duration_sec", 0) or 0) > 2.0)))
    remote_duplicate_seconds = float(quality.get("remote_duplicate_in_me_seconds", 0.0) or 0.0)
    audit_cleanup = quality.get("audit_cleanup") if isinstance(quality.get("audit_cleanup"), dict) else {}
    meeting_duration = float(quality.get("meeting_duration_sec", 0.0) or 0.0)
    if meeting_duration <= 0.0 and utterances:
        meeting_duration = max(float(row.get("end", 0.0) or 0.0) for row in utterances if isinstance(row, dict))
    metrics = {
        "utterances": utterance_count,
        "needs_review_count": needs_review,
        "needs_review_ratio": round(needs_review / utterance_count, 6) if utterance_count > 0 else None,
        "cross_role_overlap_gt2_count": cross_gt2,
        "cross_role_overlap_gt2_seconds": float(quality.get("cross_role_overlap_gt2_seconds", 0.0) or 0.0),
        "remote_duplicate_in_me_seconds": round(remote_duplicate_seconds, 3),
        "unrepaired_long_mic_crossings_count": int(quality.get("unrepaired_long_mic_crossings_count", 0) or 0),
        "golden_phrase_fail_count": int(quality.get("golden_phrase_fail_count", 0) or 0),
        "local_only_island_recall": float(quality.get("local_only_island_recall", 1.0) or 0.0),
        "local_recall_low_score_explained": bool(quality.get("local_recall_low_score_explained")),
        "meeting_duration_sec": round(meeting_duration, 3),
    }
    if quality.get("local_recall_explanation"):
        metrics["local_recall_explanation"] = quality.get("local_recall_explanation")
    human_review = quality.get("human_review") if isinstance(quality.get("human_review"), dict) else {}
    if human_review:
        metrics["review_scope_complete"] = human_review.get("review_scope_complete")
        metrics["review_scope_remaining_seconds"] = float(human_review.get("review_scope_remaining_seconds", 0.0) or 0.0)
    for key in (
        "audit_harmful_seconds_before",
        "audit_harmful_seconds_after",
        "audit_benign_seconds",
        "audit_review_seconds",
        "dropped_me_duplicate_seconds",
        "dropped_me_noise_seconds",
        "protected_intentional_repeat_count",
    ):
        if key in audit_cleanup:
            metrics[key] = audit_cleanup[key]
        elif key in quality:
            metrics[key] = quality[key]
    return metrics


def verdict_from_metrics(
    selected_profile: str,
    metrics: dict[str, Any],
    risk_items: list[dict[str, Any]],
    repair_comparison: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]]]:
    items = list(risk_items)
    utterances = int(metrics.get("utterances", 0) or 0)
    needs_ratio = metrics.get("needs_review_ratio")

    if any(item.get("severity") == "fatal" for item in items) or utterances <= 0:
        if utterances <= 0:
            items.append({"type": "empty_transcript", "severity": "fatal", "reason": "selected clean_dialogue has no utterances"})
        return "failed", items

    if selected_profile == "shadow_v2" and (not repair_comparison or repair_comparison.get("passed") is not True):
        items.append(
            {
                "type": "shadow_profile_without_passing_comparison",
                "severity": "high",
                "reason": "selected shadow transcript did not pass repair comparison gates",
            }
        )

    if int(metrics["unrepaired_long_mic_crossings_count"]) > 0:
        items.append(
            {
                "type": "unrepaired_long_mic_crossings",
                "severity": "high",
                "reason": "long mic segments still cross authoritative remote intervals",
            }
        )
    if int(metrics["golden_phrase_fail_count"]) > 0:
        items.append({"type": "golden_phrase_failures", "severity": "high", "reason": "configured golden phrase checks failed"})

    if selected_profile in {"audit_cleanup_v1", "audit_cleanup_v2", "audit_cleanup_v3", "audit_cleanup_v4", "audit_cleanup_v5", "audit_cleanup_v6", "audit_cleanup_v7", "reviewed_v1", "agent_reviewed_v1"} and "audit_harmful_seconds_after" in metrics:
        duration = max(1.0, float(metrics.get("meeting_duration_sec", 0.0) or 0.0))
        harmful = float(metrics.get("audit_harmful_seconds_after", 0.0) or 0.0)
        review = float(metrics.get("audit_review_seconds", 0.0) or 0.0)
        local_recall = float(metrics.get("local_only_island_recall", 1.0) or 1.0)
        local_recall_explained = bool(metrics.get("local_recall_low_score_explained"))
        harmful_ratio = harmful / duration
        review_ratio = review / duration
        if (local_recall < 0.70 and not local_recall_explained) or harmful > max(180.0, duration * 0.06) or review > max(900.0, duration * 0.35):
            items.append(
                {
                    "type": "audit_cleanup_group_quality",
                    "severity": "fatal",
                    "reason": "audit-informed overlap metrics failed hard thresholds",
                }
            )
            return "failed", items
        if (local_recall < 0.80 and not local_recall_explained) or harmful > max(90.0, duration * 0.03) or review > max(300.0, duration * 0.12):
            items.append(
                {
                    "type": "audit_cleanup_group_quality",
                    "severity": "high",
                    "reason": "audit-informed harmful/review overlap metrics exceed usable thresholds",
                }
            )
            return "risky", items
        if selected_profile == "reviewed_v1" and metrics.get("review_scope_complete") is False:
            items.append(
                {
                    "type": "partial_review_scope",
                    "severity": "medium",
                    "reason": "some review rows are still missing or todo",
                    "remaining_seconds": metrics.get("review_scope_remaining_seconds"),
                }
            )
            return "usable_with_review", items
        if harmful > max(30.0, duration * 0.01) or review > max(60.0, duration * 0.03):
            items.append(
                {
                    "type": "audit_cleanup_group_review",
                    "severity": "medium",
                    "reason": "audit-informed overlap metrics require review",
                }
            )
            return "usable_with_review", items
        if int(metrics["needs_review_count"]) > 0 or int(metrics["cross_role_overlap_gt2_count"]) > 0:
            items.append({"type": "remaining_review_regions", "severity": "medium", "reason": "some transcript regions still need review"})
            return "usable_with_review", items
        return "good", items

    if needs_ratio is not None and float(needs_ratio) > 0.12:
        items.append(
            {
                "type": "needs_review_ratio",
                "severity": "high",
                "reason": "more than 12% of utterances need review",
            }
        )
    if float(metrics["remote_duplicate_in_me_seconds"]) > 180.0:
        items.append(
            {
                "type": "remote_duplicate_in_me_seconds",
                "severity": "high",
                "reason": "too much remote speech may remain in Me utterances",
            }
        )

    if any(item.get("severity") == "high" for item in items):
        return "risky", items

    if int(metrics["needs_review_count"]) > 0:
        items.append({"type": "needs_review_utterances", "severity": "medium", "reason": "some utterances need review"})
    if int(metrics["cross_role_overlap_gt2_count"]) > 0:
        items.append({"type": "long_cross_role_overlaps", "severity": "medium", "reason": "some role overlaps are longer than 2 seconds"})
    if float(metrics["remote_duplicate_in_me_seconds"]) > 0.0:
        items.append({"type": "remote_duplicate_in_me_seconds", "severity": "medium", "reason": "some remote overlap remains in Me utterances"})

    if any(item.get("severity") == "medium" for item in items):
        return "usable_with_review", items
    return "good", items


def phrase_matches(text: str, phrases: tuple[str, ...]) -> list[str]:
    lowered = lower_text(text)
    return [phrase for phrase in phrases if phrase in lowered]


def facilitation_matches(text: Any) -> list[str]:
    return phrase_matches(str(text or ""), MEETING_FACILITATION_PATTERNS)


def is_meeting_facilitation(text: Any) -> bool:
    return bool(facilitation_matches(text))


def domain_terms(text: Any) -> list[str]:
    text_tokens = tokens(text)
    found = sorted({token for token in text_tokens if token in DOMAIN_TERMS})
    lowered = lower_text(text)
    for term in DOMAIN_TERMS:
        if " " in term and term in lowered:
            found.append(term)
    return sorted(set(found))


def row_quality_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    quality = row.get("quality")
    if isinstance(quality, dict):
        for key, value in quality.items():
            if value is True:
                flags.append(str(key))
            elif isinstance(value, dict) and value.get("status"):
                flags.append(f"{key}:{value.get('status')}")
    return sorted(flags)


def row_quality_review_sources(row: dict[str, Any]) -> list[dict[str, Any]]:
    quality = row.get("quality")
    if not isinstance(quality, dict):
        return []
    sources: list[dict[str, Any]] = []
    for key, value in quality.items():
        if not isinstance(value, dict):
            continue
        status = str(value.get("status") or "").strip()
        if not status:
            continue
        sources.append(
            {
                "key": key,
                "status": status,
                "profile": value.get("profile"),
                "decisions": value.get("decisions") if isinstance(value.get("decisions"), list) else [],
                "source_audit_ids": value.get("source_audit_ids") if isinstance(value.get("source_audit_ids"), list) else [],
            }
        )
    return sources


def unresolved_review_sources(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [source for source in row_quality_review_sources(row) if source.get("status") == "needs_review"]


def apply_unresolved_review_penalty(candidate: dict[str, Any], row: dict[str, Any], score: int, penalty: int = 12) -> int:
    sources = unresolved_review_sources(row)
    if not sources:
        return score
    keys = sorted({str(source.get("key")) for source in sources if source.get("key")})
    existing = candidate["features"].get("review_sources")
    merged = existing if isinstance(existing, list) else []
    seen = {
        (
            str(source.get("key")),
            str(source.get("status")),
            ",".join(str(item) for item in source.get("source_audit_ids", [])),
        )
        for source in merged
        if isinstance(source, dict)
    }
    for source in sources:
        signature = (
            str(source.get("key")),
            str(source.get("status")),
            ",".join(str(item) for item in source.get("source_audit_ids", [])),
        )
        if signature not in seen:
            merged.append(source)
            seen.add(signature)
    candidate["features"]["review_sources"] = merged
    candidate["penalties"].append(f"unresolved review source: {', '.join(keys)}")
    return score - penalty


def context_ids(utterances: list[dict[str, Any]], index: int) -> list[str]:
    ids: list[str] = []
    for neighbor in (index - 1, index + 1):
        if 0 <= neighbor < len(utterances):
            ids.append(utterance_id(utterances[neighbor], neighbor))
    return ids


def find_action_verbs(text_tokens: list[str]) -> tuple[list[str], list[str]]:
    concrete = [token for token in text_tokens if token in ACTION_VERBS]
    weak = [token for token in text_tokens if token in WEAK_ACTION_VERBS]
    return sorted(set(concrete)), sorted(set(weak))


def detect_action_object(text_tokens: list[str], verbs: list[str]) -> tuple[bool, list[str]]:
    for verb in verbs:
        if verb not in text_tokens:
            continue
        start = text_tokens.index(verb) + 1
        span = [token for token in text_tokens[start : start + 8] if token not in STOP_WORDS and token not in FILLER_WORDS]
        if len(span) >= 2:
            return True, span[:6]
        if any(token in DOMAIN_TERMS for token in span):
            return True, span[:6]
    return False, []


def substantive_terms(text: Any) -> list[str]:
    terms: list[str] = []
    for token in content_tokens(text):
        translated = TOPIC_LABEL_TRANSLATIONS.get(token, token)
        if translated in TOPIC_GENERIC_WORDS or translated in FILLER_WORDS:
            continue
        if len(translated) < 4 and translated not in DOMAIN_TERMS:
            continue
        terms.append(translated)
    terms.extend(domain_terms(text))
    return sorted(set(terms))


def topic_keywords_for_block(block: list[tuple[int, dict[str, Any]]]) -> list[str]:
    weights: dict[str, int] = {}

    def add(term: str, weight: int) -> None:
        if not term or term in TOPIC_GENERIC_WORDS or term in FILLER_WORDS:
            return
        if len(term) < 4 and term not in DOMAIN_TERMS:
            return
        weights[term] = weights.get(term, 0) + weight

    for _, row in block:
        text = str(row.get("text") or "")
        for term in domain_terms(text):
            add(TOPIC_LABEL_TRANSLATIONS.get(term, term), 8)
        for term in substantive_terms(text):
            add(TOPIC_LABEL_TRANSLATIONS.get(term, term), 2)
        if phrase_matches(text, DECISION_EXPLICIT_MARKERS):
            add("решения", 3)
        concrete_verbs, _ = find_action_verbs(tokens(text))
        if phrase_matches(text, ACTION_STRONG_MARKERS) or concrete_verbs:
            add("действия", 2)
        if phrase_matches(text, RISK_STRONG_MARKERS):
            add("риски", 3)
        if phrase_matches(text, OPEN_QUESTION_STRONG_PATTERNS):
            add("вопросы", 3)

    ranked = sorted(weights.items(), key=lambda pair: (-pair[1], pair[0]))
    return [term for term, score in ranked if score >= 3][: int(DEFAULT_RULES["selection"]["topic_keywords_per_block"])]


def has_substantive_decision_content(*texts: Any) -> bool:
    combined = " ".join(str(text or "") for text in texts)
    if domain_terms(combined):
        return True
    if len(substantive_terms(combined)) >= 2:
        return True
    if phrase_matches(combined, DECISION_EXPLICIT_MARKERS):
        return True
    if any(token in tokens(combined) for token in ("оставляем", "берем", "берём", "переносим", "откладываем", "выбираем", "раз", "неделю", "недели", "месяц")):
        return True
    return False


def candidate_base(
    candidate_id: str,
    candidate_type: str,
    row: dict[str, Any],
    index: int,
    utterances: list[dict[str, Any]],
    selected_profile: str,
    topic_block_id: str | None,
    display_text: str | None = None,
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": candidate_id,
        "type": candidate_type,
        "subtype": candidate_type,
        "status": "hidden",
        "score": 0,
        "confidence": "low",
        "display_text": clean_text(display_text if display_text is not None else row.get("text"), limit=420),
        "evidence_utterance_ids": evidence_ids or [utterance_id(row, index)],
        "context_utterance_ids": context_ids(utterances, index),
        "topic_block_id": topic_block_id,
        "time": {"start": row.get("start"), "end": row.get("end")},
        "roles": [role(row)],
        "features": {
            "markers": [],
            "verbs": [],
            "objects": [],
            "domain_terms": domain_terms(row.get("text")),
            "quality_flags": row_quality_flags(row),
            "review_sources": row_quality_review_sources(row),
        },
        "reasons": [],
        "penalties": [],
        "needs_review": True,
        "source": {"transcript_profile": selected_profile, "extractor": "deterministic_rules_v3"},
    }


def finalize_candidate(candidate: dict[str, Any], thresholds: dict[str, int]) -> dict[str, Any]:
    candidate["score"] = max(0, min(100, int(candidate["score"])))
    score = candidate["score"]
    candidate_type = candidate["type"]

    if candidate_type == "action":
        if candidate.get("subtype") in {"meeting_facilitation", "process_discussion", "weak_action"}:
            candidate["status"] = "hidden"
        elif score >= thresholds["selected_action"]:
            candidate["subtype"] = "action_item"
            candidate["status"] = "selected"
        elif score >= thresholds["candidate_action"]:
            candidate["subtype"] = "candidate_action"
            candidate["status"] = "selected"
        elif score >= thresholds["weak_action"]:
            candidate["subtype"] = "weak_action"
            candidate["status"] = "hidden"
        else:
            candidate["status"] = "hidden"
    elif candidate_type == "decision":
        if candidate.get("subtype") == "meeting_facilitation":
            candidate["status"] = "hidden"
        elif score >= thresholds["selected_decision"]:
            candidate["subtype"] = "decision"
            candidate["status"] = "selected"
        elif score >= thresholds["candidate_decision"]:
            candidate["subtype"] = "candidate_decision"
            candidate["status"] = "selected"
        elif score >= thresholds["weak_decision"]:
            candidate["subtype"] = "weak_decision"
            candidate["status"] = "hidden"
        else:
            candidate["status"] = "hidden"
    elif candidate_type == "risk":
        if score >= thresholds["selected_risk"]:
            candidate["subtype"] = "risk"
            candidate["status"] = "selected"
        elif score >= thresholds["candidate_risk"]:
            candidate["subtype"] = "candidate_risk"
            candidate["status"] = "selected"
        elif score >= thresholds["weak_risk"]:
            candidate["subtype"] = "weak_risk"
            candidate["status"] = "hidden"
        else:
            candidate["status"] = "hidden"
    elif candidate_type == "open_question":
        if score >= thresholds["selected_open_question"]:
            candidate["subtype"] = "open_question"
            candidate["status"] = "selected"
        elif score >= thresholds["candidate_open_question"]:
            candidate["subtype"] = "candidate_open_question"
            candidate["status"] = "selected"
        elif score >= thresholds["weak_open_question"]:
            candidate["subtype"] = "discussion_question"
            candidate["status"] = "hidden"
        else:
            candidate["status"] = "hidden"

    if score >= 75:
        candidate["confidence"] = "high"
    elif score >= 55:
        candidate["confidence"] = "medium"
    else:
        candidate["confidence"] = "low"
    return candidate


def score_action(row: dict[str, Any], index: int, utterances: list[dict[str, Any]], selected_profile: str, topic_block_id: str | None, candidate_number: int) -> dict[str, Any] | None:
    text = str(row.get("text") or "")
    text_tokens = tokens(text)
    strong_markers = phrase_matches(text, ACTION_STRONG_MARKERS)
    medium_markers = phrase_matches(text, ACTION_MEDIUM_MARKERS)
    soft_markers = phrase_matches(text, ACTION_SOFT_MARKERS)
    facilitation = facilitation_matches(text)
    concrete_verbs, weak_verbs = find_action_verbs(text_tokens)
    marker_present = bool(strong_markers or medium_markers or soft_markers or facilitation or concrete_verbs or weak_verbs)
    if not marker_present:
        return None

    candidate = candidate_base(f"cand_action_{candidate_number:04d}", "action", row, index, utterances, selected_profile, topic_block_id)
    features = candidate["features"]
    features["markers"] = strong_markers + medium_markers + soft_markers + facilitation
    features["verbs"] = concrete_verbs + weak_verbs

    if facilitation:
        candidate["subtype"] = "meeting_facilitation"
        candidate["score"] = 25
        candidate["reasons"].append(f"meeting facilitation pattern: {facilitation[0]}")
        candidate["penalties"].append("hidden from notes markdown")
        return finalize_candidate(candidate, DEFAULT_RULES["thresholds"])

    score = 0
    if strong_markers:
        score += 25
        candidate["reasons"].append(f"strong commitment marker: {strong_markers[0]}")
    if medium_markers:
        score += 18
        candidate["reasons"].append(f"obligation/proposal marker: {medium_markers[0]}")
    if soft_markers:
        score += 10
        candidate["reasons"].append(f"soft action marker: {soft_markers[0]}")
    if concrete_verbs:
        score += 22
        candidate["reasons"].append(f"action verb: {concrete_verbs[0]}")

    object_verbs = concrete_verbs or weak_verbs
    has_object, object_span = detect_action_object(text_tokens, object_verbs)
    if weak_verbs:
        if has_object:
            score += 10
            candidate["reasons"].append(f"weak action verb with object: {weak_verbs[0]}")
        else:
            score -= 12
            candidate["penalties"].append(f"weak action verb without object: {weak_verbs[0]}")
    if has_object:
        score += 22
        features["objects"] = object_span
        candidate["reasons"].append(f"object span: {' '.join(object_span[:4])}")
    else:
        score -= 20
        candidate["penalties"].append("no concrete object")
        if not strong_markers:
            score -= 25
            candidate["subtype"] = "process_discussion"
            candidate["penalties"].append("hidden because action lacks concrete object and owner commitment")

    if features["domain_terms"]:
        score += min(10, len(features["domain_terms"]) * 5)
        candidate["reasons"].append(f"domain terms: {', '.join(features['domain_terms'][:3])}")
    if any(token in {"я", "мы", "ты"} for token in text_tokens) or strong_markers:
        score += 15
        candidate["reasons"].append("owner hint")
    if any(token in {"потом", "после", "сначала", "завтра", "сегодня"} for token in text_tokens):
        score += 6
        candidate["reasons"].append("time/sequencing hint")
    if not row.get("quality", {}).get("needs_review"):
        score += 8
    else:
        score -= 15
        candidate["penalties"].append("utterance needs review")
    score = apply_unresolved_review_penalty(candidate, row, score)

    abstract_hits = phrase_matches(text, ABSTRACT_ACTION_PATTERNS)
    if abstract_hits:
        score -= 25
        candidate["subtype"] = "process_discussion"
        candidate["penalties"].append(f"abstract/process pattern: {abstract_hits[0]}")
    if medium_markers and not strong_markers and not concrete_verbs and not has_object:
        score -= 18
        candidate["subtype"] = "process_discussion"
        candidate["penalties"].append("proposal marker without concrete action object")
    if len(content_tokens(text)) < 3:
        score -= 10
        candidate["penalties"].append("too short")
    if len(content_tokens(text)) > 45:
        score -= 10
        candidate["penalties"].append("too long and rambling")
    if "?" in text:
        score -= 15
        candidate["penalties"].append("question-like uncertainty")
    if weak_verbs and not concrete_verbs and not abstract_hits:
        candidate["subtype"] = "weak_action"

    candidate["score"] = score
    return finalize_candidate(candidate, DEFAULT_RULES["thresholds"])


def score_explicit_decision(row: dict[str, Any], index: int, utterances: list[dict[str, Any]], selected_profile: str, topic_block_id: str | None, candidate_number: int) -> dict[str, Any] | None:
    text = str(row.get("text") or "")
    explicit = phrase_matches(text, DECISION_EXPLICIT_MARKERS)
    dialogue = phrase_matches(text, DECISION_DIALOGUE_MARKERS)
    if not explicit and not dialogue:
        return None
    if lower_text(text).strip() in {"окей", "ок", "да", "угу", "хорошо"}:
        return None

    candidate = candidate_base(f"cand_decision_{candidate_number:04d}", "decision", row, index, utterances, selected_profile, topic_block_id)
    candidate["features"]["markers"] = explicit + dialogue
    facilitation = facilitation_matches(text)
    if facilitation and not explicit:
        candidate["subtype"] = "meeting_facilitation"
        candidate["features"]["markers"].extend(facilitation)
        candidate["score"] = 25
        candidate["reasons"].append(f"meeting facilitation pattern: {facilitation[0]}")
        candidate["penalties"].append("hidden from notes markdown")
        return finalize_candidate(candidate, DEFAULT_RULES["thresholds"])
    score = 0
    if explicit:
        score += 50
        candidate["reasons"].append(f"explicit decision marker: {explicit[0]}")
    if dialogue:
        score += 20
        candidate["reasons"].append(f"dialogue decision marker: {dialogue[0]}")
    if domain_terms(text):
        score += 15
        candidate["reasons"].append("domain term")
    if len(content_tokens(text)) >= 3:
        score += 10
        candidate["reasons"].append("specific decision content")
    if any(verb in tokens(text) for verb in ("оставляем", "берем", "берём", "переносим", "откладываем", "выбираем")):
        score += 15
        candidate["reasons"].append("clear choice verb")
    if dialogue and not explicit and not has_substantive_decision_content(text):
        score -= 35
        candidate["subtype"] = "weak_decision"
        candidate["penalties"].append("dialogue marker without substantive decision content")
    if not row.get("quality", {}).get("needs_review"):
        score += 8
    else:
        score -= 15
        candidate["penalties"].append("utterance needs review")
    score = apply_unresolved_review_penalty(candidate, row, score)
    if "?" in text:
        score -= 20
        candidate["penalties"].append("question-like")
    if len(content_tokens(text)) < 2:
        score -= 20
        candidate["penalties"].append("too vague")
    candidate["score"] = score
    return finalize_candidate(candidate, DEFAULT_RULES["thresholds"])


def score_proposal_decision(
    row: dict[str, Any],
    index: int,
    utterances: list[dict[str, Any]],
    selected_profile: str,
    topic_block_id: str | None,
    candidate_number: int,
) -> dict[str, Any] | None:
    text = str(row.get("text") or "")
    proposal = phrase_matches(text, PROPOSAL_MARKERS)
    if not proposal:
        return None
    if "?" in text:
        return None
    if is_meeting_facilitation(text) or not has_substantive_decision_content(text):
        return None
    start = float(row.get("start", 0.0) or 0.0)
    for neighbor_index in range(index + 1, min(index + 4, len(utterances))):
        neighbor = utterances[neighbor_index]
        neighbor_start = float(neighbor.get("start", start) or start)
        if neighbor_start - start > 45:
            break
        agreement = phrase_matches(str(neighbor.get("text") or ""), AGREEMENT_MARKERS)
        if not agreement:
            continue
        if not has_substantive_decision_content(text, neighbor.get("text")):
            continue
        candidate = candidate_base(
            f"cand_decision_{candidate_number:04d}",
            "decision",
            row,
            index,
            utterances,
            selected_profile,
            topic_block_id,
            display_text=f"{clean_text(row.get('text'), 240)} / {clean_text(neighbor.get('text'), 160)}",
            evidence_ids=[utterance_id(row, index), utterance_id(neighbor, neighbor_index)],
        )
        candidate["subtype"] = "proposal_accepted"
        candidate["context_utterance_ids"] = context_ids(utterances, index) + context_ids(utterances, neighbor_index)
        candidate["roles"] = sorted({role(row), role(neighbor)})
        candidate["time"] = {"start": row.get("start"), "end": neighbor.get("end")}
        candidate["features"]["markers"] = proposal + agreement
        score = 35 + 25
        candidate["reasons"].append(f"proposal marker: {proposal[0]}")
        candidate["reasons"].append(f"neighbor agreement: {agreement[0]}")
        if role(row) != role(neighbor):
            score += 10
            candidate["reasons"].append("opposite roles involved")
        if neighbor_start - start <= 20:
            score += 10
            candidate["reasons"].append("agreement within 20 seconds")
        if domain_terms(text) or domain_terms(neighbor.get("text")):
            score += 10
        if row.get("quality", {}).get("needs_review") or neighbor.get("quality", {}).get("needs_review"):
            score -= 15
            candidate["penalties"].append("some evidence utterance needs review")
        score = apply_unresolved_review_penalty(candidate, row, score)
        score = apply_unresolved_review_penalty(candidate, neighbor, score)
        candidate["score"] = score
        return finalize_candidate(candidate, DEFAULT_RULES["thresholds"])
    return None


def score_risk(row: dict[str, Any], index: int, utterances: list[dict[str, Any]], selected_profile: str, topic_block_id: str | None, candidate_number: int) -> dict[str, Any] | None:
    text = str(row.get("text") or "")
    strong = phrase_matches(text, RISK_STRONG_MARKERS)
    medium = phrase_matches(text, RISK_MEDIUM_MARKERS)
    if not strong and not medium:
        return None
    candidate = candidate_base(f"cand_risk_{candidate_number:04d}", "risk", row, index, utterances, selected_profile, topic_block_id)
    candidate["features"]["markers"] = strong + medium
    score = 0
    if strong:
        score += 35
        candidate["reasons"].append(f"strong risk marker: {strong[0]}")
    if medium:
        score += 20
        candidate["reasons"].append(f"medium risk marker: {medium[0]}")
    if phrase_matches(text, RISK_CONSEQUENCE_MARKERS):
        score += 20
        candidate["reasons"].append("consequence or condition pattern")
    if domain_terms(text):
        score += 10
    if any(token in tokens(text) for token in ("может", "возможно", "скорее")):
        score += 8
    solved = phrase_matches(text, RISK_SOLVED_PATTERNS)
    if solved:
        score -= 30
        candidate["penalties"].append(f"issue described as solved: {solved[0]}")
    if "проблема" in medium and not phrase_matches(text, RISK_CONSEQUENCE_MARKERS):
        score -= 15
        candidate["penalties"].append("problem used without clear consequence")
    if not row.get("quality", {}).get("needs_review"):
        score += 8
    else:
        score -= 10
        candidate["penalties"].append("utterance needs review")
    score = apply_unresolved_review_penalty(candidate, row, score)
    candidate["score"] = score
    return finalize_candidate(candidate, DEFAULT_RULES["thresholds"])


def score_open_question(
    row: dict[str, Any],
    index: int,
    utterances: list[dict[str, Any]],
    selected_profile: str,
    topic_block_id: str | None,
    candidate_number: int,
) -> dict[str, Any] | None:
    text = str(row.get("text") or "")
    lowered = lower_text(text)
    strong = phrase_matches(text, OPEN_QUESTION_STRONG_PATTERNS)
    interrogative = any(lowered.startswith(word + " ") or f" {word} " in lowered for word in QUESTION_WORDS)
    has_question = "?" in text or bool(strong) or interrogative
    if not has_question:
        return None
    candidate = candidate_base(f"cand_question_{candidate_number:04d}", "open_question", row, index, utterances, selected_profile, topic_block_id)
    candidate["features"]["markers"] = strong + (["question_mark"] if "?" in text else [])
    score = 0
    if "?" in text or interrogative:
        score += 35
        candidate["reasons"].append("question construction")
    if strong:
        score += 25
        candidate["reasons"].append(f"unresolved marker: {strong[0]}")
    if domain_terms(text):
        score += 10
    if any(word in tokens(text) for word in ("кто", "кого", "кому", "когда")):
        score += 15
        candidate["reasons"].append("owner or timing unknown")
    topic_only = phrase_matches(text, TOPIC_ONLY_QUESTION_PATTERNS)
    if topic_only:
        score -= 25
        candidate["penalties"].append(f"topic-only question pattern: {topic_only[0]}")
    if answered_nearby(utterances, index):
        score -= 30
        candidate["penalties"].append("answered nearby")
    if not row.get("quality", {}).get("needs_review"):
        score += 8
    else:
        score -= 10
        candidate["penalties"].append("utterance needs review")
    score = apply_unresolved_review_penalty(candidate, row, score)
    candidate["score"] = score
    return finalize_candidate(candidate, DEFAULT_RULES["thresholds"])


def answered_nearby(utterances: list[dict[str, Any]], index: int) -> bool:
    source_end = float(utterances[index].get("end", utterances[index].get("start", 0.0)) or 0.0)
    for neighbor in utterances[index + 1 : index + 6]:
        start = float(neighbor.get("start", source_end) or source_end)
        if start - source_end > 90:
            break
        if phrase_matches(str(neighbor.get("text") or ""), ANSWER_MARKERS):
            return True
    return False


def row_topic_block(topic_blocks: list[dict[str, Any]], row: dict[str, Any]) -> str | None:
    start = float(row.get("start", 0.0) or 0.0)
    for block in topic_blocks:
        if float(block["start"]) <= start <= float(block["end"]):
            return str(block["id"])
    return None


def extract_candidates(utterances: list[dict[str, Any]], selected_profile: str, topic_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    counters = {"action": 0, "decision": 0, "risk": 0, "question": 0}
    for index, row in enumerate(utterances):
        if not isinstance(row, dict):
            continue
        topic_block_id = row_topic_block(topic_blocks, row)

        counters["action"] += 1
        action = score_action(row, index, utterances, selected_profile, topic_block_id, counters["action"])
        if action:
            candidates.append(action)

        counters["decision"] += 1
        explicit_decision = score_explicit_decision(row, index, utterances, selected_profile, topic_block_id, counters["decision"])
        if explicit_decision:
            candidates.append(explicit_decision)
        proposal_decision = score_proposal_decision(row, index, utterances, selected_profile, topic_block_id, counters["decision"])
        if proposal_decision:
            candidates.append(proposal_decision)

        counters["risk"] += 1
        risk = score_risk(row, index, utterances, selected_profile, topic_block_id, counters["risk"])
        if risk:
            candidates.append(risk)

        counters["question"] += 1
        question = score_open_question(row, index, utterances, selected_profile, topic_block_id, counters["question"])
        if question:
            candidates.append(question)

    candidates.sort(key=lambda item: (float(item["time"].get("start") or 0.0), item["type"], -int(item["score"])))
    return candidates


def salience_score(row: dict[str, Any]) -> tuple[int, list[str], list[str]]:
    text = str(row.get("text") or "")
    words = content_tokens(text)
    dterms = domain_terms(text)
    score = min(len(words), 20) * 2 + len(dterms) * 5
    reasons: list[str] = []
    penalties: list[str] = []
    if dterms:
        reasons.append(f"domain_terms:{','.join(dterms[:3])}")
    if phrase_matches(text, ACTION_MEDIUM_MARKERS + DECISION_EXPLICIT_MARKERS + RISK_STRONG_MARKERS + OPEN_QUESTION_STRONG_PATTERNS):
        score += 8
        reasons.append("marker_bonus")
    if not row.get("quality", {}).get("needs_review"):
        score += 8
    else:
        score -= 12
        penalties.append("needs_review")
    sources = unresolved_review_sources(row)
    if sources:
        score -= 10
        penalties.append("unresolved_review_source")
    if is_filler_utterance(text):
        score -= 35
        penalties.append("filler")
    facilitation = facilitation_matches(text)
    if facilitation:
        score -= 30
        penalties.append(f"meeting_facilitation:{facilitation[0]}")
    if len(words) < 3:
        score -= 15
        penalties.append("too_short")
    if len(words) > 45:
        score -= 8
        penalties.append("too_long")
    return max(0, int(score)), reasons, penalties


def is_filler_utterance(text: str) -> bool:
    words = content_tokens(text)
    text_lower = lower_text(text).strip(" .,!?:;")
    if text_lower in FILLER_WORDS:
        return True
    return len(words) <= 1 and len(tokens(text)) <= 3


def discourse_boundary(row: dict[str, Any]) -> str | None:
    text = lower_text(row.get("text")).strip()
    if not text:
        return None
    for marker in DISCOURSE_MARKERS:
        if text.startswith(marker):
            return f"topic_marker:{marker}"
    if text.startswith("так ") and len(content_tokens(text)) >= 4:
        return "topic_marker:так"
    if text.startswith("ладно") and len(content_tokens(text)) >= 4:
        return "topic_marker:ладно"
    return None


def build_topic_blocks(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not utterances:
        return []

    config = DEFAULT_RULES["outline"]
    raw_blocks: list[list[tuple[int, dict[str, Any]]]] = []
    current: list[tuple[int, dict[str, Any]]] = []
    block_start = float(utterances[0].get("start", 0.0) or 0.0)
    previous_end = block_start
    boundary_reasons: list[list[str]] = [[]]

    for index, row in enumerate(utterances):
        start = float(row.get("start", previous_end) or previous_end)
        gap = start - previous_end
        span = start - block_start
        reasons: list[str] = []
        if gap >= config["strong_pause_boundary_sec"]:
            reasons.append(f"strong_pause_{int(gap)}s")
        elif gap >= config["pause_boundary_sec"]:
            reasons.append(f"pause_{int(gap)}s")
        marker = discourse_boundary(row)
        if marker and span >= config["target_block_sec"] / 2:
            reasons.append(marker)
        should_split = bool(current) and span >= config["min_block_sec"] and bool(reasons)
        if current and span >= config["max_block_sec"]:
            should_split = True
            reasons.append("max_block_duration")
        if should_split:
            raw_blocks.append(current)
            boundary_reasons.append(reasons)
            current = []
            block_start = start
        current.append((index, row))
        previous_end = float(row.get("end", start) or start)

    if current:
        raw_blocks.append(current)

    topic_blocks: list[dict[str, Any]] = []
    for block_index, block in enumerate(raw_blocks, start=1):
        first_index, first = block[0]
        last_index, last = block[-1]
        scored: list[tuple[int, int, dict[str, Any], list[str], list[str]]] = []
        for item_index, item in block:
            score, reasons, penalties = salience_score(item)
            if score >= config["min_salience_score"]:
                scored.append((score, item_index, item, reasons, penalties))
        if not scored:
            best_index, best_item = max(block, key=lambda pair: salience_score(pair[1])[0])
            best_score, best_reasons, best_penalties = salience_score(best_item)
            scored = [(best_score, best_index, best_item, best_reasons, best_penalties)]
        scored.sort(key=lambda item: (-item[0], float(item[2].get("start", 0.0) or 0.0)))
        representatives = scored[: int(DEFAULT_RULES["selection"]["representative_utterances_per_block"])]
        representative_ids = {utterance_id(item, item_index) for _, item_index, item, _, _ in representatives}
        if len({role(item) for _, _, item, _, _ in representatives}) == 1:
            present_role = role(representatives[0][2])
            for score, item_index, item, reasons, penalties in scored[int(DEFAULT_RULES["selection"]["representative_utterances_per_block"]) :]:
                if role(item) != present_role and score >= config["min_salience_score"]:
                    representatives.append((score, item_index, item, reasons, penalties))
                    representative_ids.add(utterance_id(item, item_index))
                    break
        representatives.sort(key=lambda item: float(item[2].get("start", 0.0) or 0.0))

        keywords = topic_keywords_for_block(block)

        salience_scores = {
            utterance_id(item, item_index): score
            for score, item_index, item, _, _ in representatives
        }
        topic_blocks.append(
            {
                "id": f"topic_{block_index:04d}",
                "start": first.get("start"),
                "end": last.get("end"),
                "boundary_reasons": boundary_reasons[block_index - 1] if block_index - 1 < len(boundary_reasons) else [],
                "keywords": keywords,
                "utterance_ids": [utterance_id(first, first_index), utterance_id(last, last_index)],
                "utterance_count": len(block),
                "representative_utterance_ids": [utterance_id(item, item_index) for _, item_index, item, _, _ in representatives],
                "representatives": [
                    {
                        "utterance_id": utterance_id(item, item_index),
                        "role": role(item),
                        "start": item.get("start"),
                        "end": item.get("end"),
                        "text": clean_text(item.get("text"), limit=220),
                        "salience_score": score,
                    }
                    for score, item_index, item, _, _ in representatives
                ],
                "salience_scores": salience_scores,
                "quality": {
                    "needs_review_count": sum(1 for _, row in block if row.get("quality", {}).get("needs_review")),
                },
            }
        )
    return topic_blocks


def select_items(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    selected: dict[str, list[dict[str, Any]]] = {
        "decisions": [],
        "actions": [],
        "risks": [],
        "open_questions": [],
    }
    mapping = {
        "decision": ("decisions", "max_decisions"),
        "action": ("actions", "max_actions"),
        "risk": ("risks", "max_risks"),
        "open_question": ("open_questions", "max_open_questions"),
    }
    for candidate_type, (selected_key, limit_key) in mapping.items():
        rows = [item for item in candidates if item["type"] == candidate_type and item["status"] == "selected"]
        rows.sort(key=lambda item: (-int(item["score"]), float(item["time"].get("start") or 0.0)))
        selected[selected_key] = rows[: int(DEFAULT_RULES["selection"][limit_key])]
        for rank, item in enumerate(selected[selected_key], start=1):
            item["rank"] = rank
    return selected


def build_evidence_notes(
    *,
    session: Path,
    selected_profile: str,
    inputs: dict[str, str],
    utterances: list[dict[str, Any]],
    review_items: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    topic_blocks = build_topic_blocks(utterances)
    candidates = extract_candidates(utterances, selected_profile, topic_blocks)
    selected = select_items(candidates)
    selected["outline_blocks"] = topic_blocks[: int(DEFAULT_RULES["selection"].get("max_outline_blocks", 8))]
    selected_counts = {key: len(value) for key, value in selected.items()}

    hidden_counts: dict[str, int] = {}
    for candidate in candidates:
        if candidate["status"] != "selected":
            key = str(candidate["subtype"])
            hidden_counts[key] = hidden_counts.get(key, 0) + 1
    review_summary = review_items_summary(review_items)

    evidence = {
        "schema": "murmurmark.evidence_notes/v2",
        "session_id": session.name,
        "source": {
            "transcript_profile": selected_profile,
            "clean_dialogue_path": inputs.get("clean_dialogue"),
            "quality_report_path": inputs.get("quality_report"),
            "overlaps_path": inputs.get("overlaps"),
        },
        "generator": {
            "name": "synthesize-simple-extractive",
            "version": GENERATOR_VERSION,
            "mode": "deterministic",
            "config": "default_v3",
        },
        "topic_blocks": topic_blocks,
        "candidates": candidates,
        "selected": selected,
        "review": {
            "items": review_items,
            "summary": {
                **review_summary,
                "hidden_candidate_counts": dict(sorted(hidden_counts.items())),
            },
        },
        "metrics": {
            **metrics,
            "topic_block_count": len(topic_blocks),
            "candidate_count": len(candidates),
            "selected_counts": selected_counts,
            "hidden_candidate_counts": dict(sorted(hidden_counts.items())),
            "review_item_count": review_summary["review_item_count"],
            "review_item_seconds": review_summary["review_item_seconds"],
            "review_items_by_type": review_summary["by_type"],
            "review_items_by_severity": review_summary["by_severity"],
        },
        "rules": DEFAULT_RULES,
        "outline": selected["outline_blocks"],
        "potential_decisions": selected["decisions"],
        "potential_actions": selected["actions"],
        "risks_and_open_questions": selected["risks"] + selected["open_questions"],
    }
    return evidence


def build_review_items(
    utterances: list[dict[str, Any]],
    overlaps: list[dict[str, Any]],
    risk_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(utterances):
        quality = row.get("quality") if isinstance(row, dict) else {}
        if isinstance(quality, dict) and quality.get("needs_review"):
            detailed_sources = [source for source in row_quality_review_sources(row) if source.get("status") == "needs_review"]
            if detailed_sources:
                for source in detailed_sources:
                    rows.append(
                        {
                            "type": f"utterance_{source['key']}",
                            "severity": "medium",
                            "start": row.get("start"),
                            "end": row.get("end"),
                            "utterance_ids": [utterance_id(row, index)],
                            "reason": f"{source['key']} status needs_review",
                            "source_audit_ids": source.get("source_audit_ids", []),
                            "decisions": source.get("decisions", []),
                            "text": clean_text(row.get("text"), limit=360),
                        }
                    )
            else:
                rows.append(
                    {
                        "type": "utterance_needs_review",
                        "severity": "medium",
                        "start": row.get("start"),
                        "end": row.get("end"),
                        "utterance_ids": [utterance_id(row, index)],
                        "reason": "utterance quality.needs_review is true",
                        "text": clean_text(row.get("text"), limit=360),
                    }
                )

    for overlap in overlaps:
        if not isinstance(overlap, dict) or "duration_sec" not in overlap:
            continue
        severity = "medium" if float(overlap.get("duration_sec", 0.0) or 0.0) > 2.0 else "low"
        rows.append(
            {
                "type": "cross_role_overlap",
                "severity": severity,
                "start": overlap.get("start"),
                "end": overlap.get("end"),
                "utterance_ids": [
                    value
                    for value in (
                        overlap.get("left_utterance_id"),
                        overlap.get("right_utterance_id"),
                        overlap.get("left_id"),
                        overlap.get("right_id"),
                    )
                    if value
                ],
                "reason": overlap.get("type", "overlap"),
                "text": clean_text(overlap.get("text") or overlap.get("left_text") or overlap.get("right_text"), limit=360),
            }
        )

    for item in risk_items:
        rows.append(
            {
                "type": item.get("type", "verdict_risk"),
                "severity": item.get("severity", "medium"),
                "start": item.get("start"),
                "end": item.get("end"),
                "utterance_ids": item.get("utterance_ids", []),
                "reason": item.get("reason", ""),
                "text": clean_text(item.get("text"), limit=360),
            }
        )
    return rows


def review_items_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, dict[str, Any]] = {}
    by_severity: dict[str, dict[str, Any]] = {}
    type_counts: Counter[str] = Counter()
    total_seconds = 0.0

    for item in items:
        item_type = str(item.get("type") or "unknown")
        severity = str(item.get("severity") or "medium")
        start = safe_number(item.get("start"))
        end = safe_number(item.get("end"))
        seconds = max(0.0, end - start)
        total_seconds += seconds
        type_counts[item_type] += 1
        type_bucket = by_type.setdefault(item_type, {"count": 0, "seconds": 0.0})
        type_bucket["count"] += 1
        type_bucket["seconds"] += seconds
        severity_bucket = by_severity.setdefault(severity, {"count": 0, "seconds": 0.0})
        severity_bucket["count"] += 1
        severity_bucket["seconds"] += seconds

    for bucket in list(by_type.values()) + list(by_severity.values()):
        bucket["seconds"] = round(float(bucket["seconds"]), 3)

    return {
        "review_item_count": len(items),
        "review_item_seconds": round(total_seconds, 3),
        "by_type": dict(sorted(by_type.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "top_types": [
            {"type": item_type, "count": count}
            for item_type, count in type_counts.most_common(5)
        ],
    }


def write_quality_markdown(path: Path, verdict_payload: dict[str, Any]) -> None:
    lines = [
        "# Quality Verdict",
        "",
        f"Verdict: `{verdict_payload['verdict']}`",
        f"Transcript profile: `{verdict_payload['selected_transcript_profile']}`",
        "",
        "## Metrics",
        "",
    ]
    for key, value in verdict_payload["metrics"].items():
        lines.append(f"- `{key}`: `{value}`")
    review_summary = verdict_payload.get("review_summary") if isinstance(verdict_payload.get("review_summary"), dict) else {}
    if review_summary:
        lines.extend(["", "## Review Items", ""])
        lines.append(f"- `review_item_count`: `{review_summary.get('review_item_count')}`")
        lines.append(f"- `review_item_seconds`: `{review_summary.get('review_item_seconds')}`")
        by_type = review_summary.get("by_type") if isinstance(review_summary.get("by_type"), dict) else {}
        if by_type:
            lines.append("- by type:")
            for item_type, bucket in sorted(by_type.items()):
                if isinstance(bucket, dict):
                    lines.append(f"  - `{item_type}`: `{bucket.get('count')}` / `{bucket.get('seconds')}` sec")
    lines.extend(["", "## Risk Items", ""])
    if verdict_payload["risk_items"]:
        for item in verdict_payload["risk_items"]:
            lines.append(f"- `{item.get('severity', 'medium')}` `{item.get('type', 'risk')}`: {item.get('reason', '')}")
    else:
        lines.append("- none")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def candidate_line(item: dict[str, Any]) -> str:
    ids = ", ".join(f"`{value}`" for value in item.get("evidence_utterance_ids", []))
    reasons = "; ".join(item.get("reasons", [])[:2])
    subtype = item.get("subtype", item.get("type"))
    return (
        f"- `needs_review` `{subtype}` score `{item.get('score')}` "
        f"{format_time(item.get('time', {}).get('start'))}-{format_time(item.get('time', {}).get('end'))} "
        f"{ids} {', '.join(item.get('roles', []))}: {item.get('display_text', '')}"
        + (f"\n  - Reason: {reasons}" if reasons else "")
    )


def write_notes_markdown(path: Path, verdict: dict[str, Any], evidence: dict[str, Any]) -> None:
    selected = evidence.get("selected", {})
    lines = [
        "# Extractive Notes",
        "",
        f"Verdict: `{verdict['verdict']}`  ",
        f"Transcript profile: `{verdict['selected_transcript_profile']}`",
        "",
        "These notes are extractive. Treat potential decisions and actions as review candidates until confirmed.",
        "",
        "## Conversation Outline",
        "",
    ]
    for block in selected.get("outline_blocks", []):
        start = format_time(block.get("start"))
        end = format_time(block.get("end"))
        keywords = ", ".join(block.get("keywords", [])[:4]) or "discussion block"
        ids = block.get("utterance_ids", [])
        id_span = f"`{ids[0]}`..`{ids[-1]}`" if ids else "`unknown`"
        lines.append(f"### {start}-{end}: {keywords}")
        lines.append(f"- Utterances {id_span} ({block.get('utterance_count', 0)} turns)")
        for sample in block.get("representatives", []):
            lines.append(f"  - `{sample['utterance_id']}` {sample['role']}: {sample['text']}")
        lines.append("")
    if not selected.get("outline_blocks"):
        lines.append("- no utterances")

    sections = (
        ("Potential Decisions", "decisions"),
        ("Potential Actions", "actions"),
        ("Risks", "risks"),
        ("Open Questions", "open_questions"),
    )
    for title, key in sections:
        lines.extend(["", f"## {title}", ""])
        rows = selected.get(key, [])
        if not rows:
            lines.append("- none detected")
            continue
        for item in rows:
            lines.append(candidate_line(item))
    lines.extend(["", "## Hidden / Weak Candidates", "", "Full scored list: `evidence_notes.json`."])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def empty_evidence(session: Path, requested_profile: str, risk_items: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    review_items = build_review_items([], [], risk_items)
    review_summary = review_items_summary(review_items)
    return {
        "schema": "murmurmark.evidence_notes/v2",
        "session_id": session.name,
        "source": {"transcript_profile": requested_profile},
        "generator": {"name": "synthesize-simple-extractive", "version": GENERATOR_VERSION, "mode": "deterministic", "config": "default_v3"},
        "topic_blocks": [],
        "candidates": [],
        "selected": {"outline_blocks": [], "decisions": [], "actions": [], "risks": [], "open_questions": []},
        "review": {"items": review_items, "summary": review_summary},
        "metrics": {
            **metrics,
            "topic_block_count": 0,
            "candidate_count": 0,
            "selected_counts": {},
            "hidden_candidate_counts": {},
            "review_item_count": review_summary["review_item_count"],
            "review_item_seconds": review_summary["review_item_seconds"],
            "review_items_by_type": review_summary["by_type"],
            "review_items_by_severity": review_summary["by_severity"],
        },
        "rules": DEFAULT_RULES,
        "outline": [],
        "potential_decisions": [],
        "potential_actions": [],
        "risks_and_open_questions": [],
    }


def write_failed_outputs(out_dir: Path, session: Path, requested_profile: str, risk_items: list[dict[str, Any]]) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = {
        "utterances": 0,
        "needs_review_count": 0,
        "needs_review_ratio": None,
        "cross_role_overlap_gt2_count": 0,
        "cross_role_overlap_gt2_seconds": 0.0,
        "remote_duplicate_in_me_seconds": 0.0,
        "unrepaired_long_mic_crossings_count": 0,
        "golden_phrase_fail_count": 0,
    }
    evidence = empty_evidence(session, requested_profile, risk_items, metrics)
    review_summary = review_items_summary(evidence["review"]["items"])
    payload = {
        "schema": "murmurmark.quality_verdict/v1",
        "verdict": "failed",
        "selected_transcript_profile": requested_profile,
        "inputs": {},
        "metrics": metrics,
        "review_summary": review_summary,
        "risk_items": risk_items,
    }
    handoff = synthesis_handoff(session, out_dir, payload)
    payload.update(handoff)
    write_json(out_dir / "quality_verdict.json", payload)
    write_quality_markdown(out_dir / "quality_verdict.md", payload)
    write_json(out_dir / "evidence_notes.json", evidence)
    write_notes_markdown(out_dir / "notes.md", payload, evidence)
    write_jsonl(out_dir / "review_items.jsonl", evidence["review"]["items"])
    write_json(
        out_dir / "synthesis_manifest.json",
        {
            "schema": "murmurmark.synthesis_manifest/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "session": str(session),
            "mode": "extractive",
            "generator": {"name": "synthesize-simple-extractive", "version": GENERATOR_VERSION, "config": "default_v3"},
            "rules": DEFAULT_RULES,
            "recommended_next": handoff["recommended_next"],
            "next_commands": handoff["next_commands"],
            "open_commands": handoff["open_commands"],
            "outputs": {
                "quality_verdict": "quality_verdict.json",
                "quality_verdict_markdown": "quality_verdict.md",
                "notes_markdown": "notes.md",
                "evidence_notes": "evidence_notes.json",
                "review_items": "review_items.jsonl",
            },
        },
    )
    print("verdict: failed")
    print(f"selected_transcript_profile: {requested_profile}")
    print(f"quality_verdict: {out_dir / 'quality_verdict.json'}")
    print(f"notes: {out_dir / 'notes.md'}")
    return 0


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    resolved_dir = session / "derived" / "transcript-simple" / "whisper-cpp" / "resolved"
    out_dir = session / "derived" / "synthesis-simple" / "extractive"
    out_dir.mkdir(parents=True, exist_ok=True)

    selected_profile, paths, repair_comparison, selection_risks = choose_profile(resolved_dir, args.transcript_profile)
    quality, utterances, overlaps_and_input_risks = load_inputs(selected_profile, paths)

    input_risks = [item for item in overlaps_and_input_risks if "duration_sec" not in item]
    overlap_rows = [item for item in overlaps_and_input_risks if "duration_sec" in item]
    metrics = metrics_from_quality(quality, utterances, overlap_rows)
    verdict, risk_items = verdict_from_metrics(selected_profile, metrics, selection_risks + input_risks, repair_comparison)

    inputs = {
        "clean_dialogue": rel(paths["clean_dialogue"], session),
        "quality_report": rel(paths["quality_report"], session),
        "overlaps": rel(paths["overlaps"], session),
    }
    if repair_comparison is not None or paths["repair_comparison"].exists():
        inputs["repair_comparison"] = rel(paths["repair_comparison"], session)
    if paths.get("audit_cleanup_report") and paths["audit_cleanup_report"].exists():
        inputs["audit_cleanup_report"] = rel(paths["audit_cleanup_report"], session)
    if paths.get("review_decisions_report") and paths["review_decisions_report"].exists():
        inputs["review_decisions_report"] = rel(paths["review_decisions_report"], session)
    if paths.get("order_repair_report") and paths["order_repair_report"].exists():
        inputs["order_repair_report"] = rel(paths["order_repair_report"], session)

    verdict_payload = {
        "schema": "murmurmark.quality_verdict/v1",
        "verdict": verdict,
        "selected_transcript_profile": selected_profile,
        "requested_transcript_profile": args.transcript_profile,
        "inputs": inputs,
        "metrics": metrics,
        "risk_items": risk_items,
    }

    if verdict == "failed":
        return write_failed_outputs(out_dir, session, selected_profile, risk_items)

    review_items = build_review_items(utterances, overlap_rows, risk_items)
    review_summary = review_items_summary(review_items)
    verdict_payload["review_summary"] = review_summary
    handoff = synthesis_handoff(session, out_dir, verdict_payload)
    verdict_payload.update(handoff)
    evidence = build_evidence_notes(
        session=session,
        selected_profile=selected_profile,
        inputs=inputs,
        utterances=utterances,
        review_items=review_items,
        metrics=metrics,
    )

    write_json(out_dir / "quality_verdict.json", verdict_payload)
    write_quality_markdown(out_dir / "quality_verdict.md", verdict_payload)
    write_json(out_dir / "evidence_notes.json", evidence)
    write_notes_markdown(out_dir / "notes.md", verdict_payload, evidence)
    write_jsonl(out_dir / "review_items.jsonl", review_items)
    profile_aliases: dict[str, str] = {}
    if selected_profile in {
        "shadow_v2",
        "audit_cleanup_v1",
        "audit_cleanup_v2",
        "audit_cleanup_v3",
        "audit_cleanup_v4",
        "audit_cleanup_v5",
        "audit_cleanup_v6",
        "audit_cleanup_v7",
        "reviewed_v1",
        "agent_reviewed_v1",
        "suggested_review_v1",
        "order_repair_v1",
        "local_recall_repair_v1",
    }:
        profile_suffix = selected_profile
        profile_aliases = {
            "quality_verdict": f"quality_verdict.{profile_suffix}.json",
            "quality_verdict_markdown": f"quality_verdict.{profile_suffix}.md",
            "notes_markdown": f"notes.{profile_suffix}.md",
            "evidence_notes": f"evidence_notes.{profile_suffix}.json",
            "review_items": f"review_items.{profile_suffix}.jsonl",
        }
        write_json(out_dir / profile_aliases["quality_verdict"], verdict_payload)
        write_quality_markdown(out_dir / profile_aliases["quality_verdict_markdown"], verdict_payload)
        write_json(out_dir / profile_aliases["evidence_notes"], evidence)
        write_notes_markdown(out_dir / profile_aliases["notes_markdown"], verdict_payload, evidence)
        write_jsonl(out_dir / profile_aliases["review_items"], review_items)
    write_json(
        out_dir / "synthesis_manifest.json",
        {
            "schema": "murmurmark.synthesis_manifest/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "session": str(session),
            "mode": "extractive",
            "generator": {"name": "synthesize-simple-extractive", "version": GENERATOR_VERSION, "config": "default_v3"},
            "selected_transcript_profile": selected_profile,
            "requested_transcript_profile": args.transcript_profile,
            "inputs": inputs,
            "rules": DEFAULT_RULES,
            "recommended_next": handoff["recommended_next"],
            "next_commands": handoff["next_commands"],
            "open_commands": handoff["open_commands"],
            "outputs": {
                "quality_verdict": "quality_verdict.json",
                "quality_verdict_markdown": "quality_verdict.md",
                "notes_markdown": "notes.md",
                "evidence_notes": "evidence_notes.json",
                "review_items": "review_items.jsonl",
                **{f"profile_{key}": value for key, value in profile_aliases.items()},
            },
        },
    )

    print(f"verdict: {verdict}")
    print(f"selected_transcript_profile: {selected_profile}")
    print(f"quality_verdict: {out_dir / 'quality_verdict.json'}")
    print(f"notes: {out_dir / 'notes.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
