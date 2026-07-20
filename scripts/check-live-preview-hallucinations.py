#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/live-pipeline-shadow.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_live_pipeline_shadow", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load live pipeline shadow")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    compound = "Продолжение следует... Редактор субтитров А.Семкин Корректор А.Егорова"
    assert module.is_hallucination(compound)
    assert module.strip_hallucination_fragments(compound) == ""
    repeated = "Продолжение следует... Продолжение следует"
    assert module.is_hallucination(repeated)
    assert module.strip_hallucination_fragments(repeated) == ""
    assert module.strip_hallucination_fragments(
        "Да, все, вот она переместилась. Редактор субтитров А.Семкин Корректор А.Егорова"
    ) == "Да, все, вот она переместилась"
    assert module.strip_hallucination_fragments(
        "Продолжение следует... Обсудим план дальше"
    ) == "Обсудим план дальше"
    assert module.strip_hallucination_fragments(
        "Обсудили план. Субтитры создавал DimaTorzok"
    ) == "Обсудили план"
    assert module.strip_hallucination_fragments("Спасибо") == "Спасибо"
    assert module.strip_hallucination_fragments("Это реальная рабочая реплика.") == "Это реальная рабочая реплика"
    assert not module.is_hallucination("Продолжение встречи обсудим завтра")
    with tempfile.TemporaryDirectory(prefix="murmurmark-live-preview-") as raw_dir:
        output_dir = Path(raw_dir)
        module.write_preview(
            output_dir,
            [
                {
                    "index": 1,
                    "start_sec": 0.0,
                    "end_sec": 30.0,
                    "mic": {"text": ""},
                    "remote": {"text": compound},
                },
                {
                    "index": 2,
                    "start_sec": 30.0,
                    "end_sec": 60.0,
                    "mic": {"text": ""},
                    "remote": {
                        "text": "Да, все, вот она переместилась. Редактор субтитров А.Семкин Корректор А.Егорова"
                    },
                },
                {
                    "index": 3,
                    "start_sec": 60.0,
                    "end_sec": 90.0,
                    "mic": {"text": repeated},
                    "remote": {"text": ""},
                },
            ],
            commit_delay_sec=10.0,
        )
        preview = (output_dir / "transcript.preview.md").read_text(encoding="utf-8")
        assert "Редактор субтитров" not in preview
        assert "Продолжение следует" not in preview
        assert "Да, все, вот она переместилась" in preview
    print("live preview hallucination checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
