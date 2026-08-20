# Remote Speaker Boundary and Minority-Voice Segmentation v1

Статус: завершено `KEEP_COVERAGE_V3` 2026-08-20.

## Назначение

Контракт проверяет, можно ли до назначения личности разделить remote-речь по сменам голоса и лучше
сохранить короткие реплики редких участников. Это изолированный shadow-слой. Он не меняет
`remote_speaker_coverage_v3`, выбранные транскрибации, основной ASR, Echo Guard или raw CAF.

## Инварианты

- Алгоритм границ не читает текст, speaker identity, имена или terminal truth.
- Каждое входное слово, его порядок и исходные временные границы сохраняются точно.
- Неустойчивый или одиночный раздел остаётся `unknown`; forced identity запрещён.
- Candidate, policy, implementation и terminal inputs фиксируются SHA-256 до чтения terminal truth.
- Публичные артефакты не содержат session IDs, имён, речи или абсолютных путей.
- Отсутствующий или повреждённый вход не меняет production и завершает проверку ошибкой.

## Candidate

`spectral_word_boundary_change_v1` использует только локальные признаки:

- паузы и word timestamps;
- speech-band spectral cosine между соседними словами;
- локальную устойчивость соседних спектральных окон;
- energy VAD и провал энергии на границе;
- utterance boundary;
- локальные Resemblyzer embeddings для анонимной кластеризации выделенных интервалов.

Порог границы и параметры кластеризации выбираются только на controlled `dev`. Реальная terminal
сессия до freeze используется только как неразмеченный вход для materialization и проверки
устойчивости, её reference content не читается.

## Артефакты

Публичные:

```text
sessions/_reports/remote-speaker-boundary-minority-v1/
  freeze_manifest.json
  report.json
  report.md
  artifact_manifest.json
  replay_report.json
```

Item-level features, segments, partitions, source fingerprints и terminal evaluations находятся в
`private/`. Отслеживаемый manifest:
`docs/testing/remote-speaker-boundary-minority-v1-manifest.json`.

## Решение

Допустим ровно один исход:

- `PROMOTE_SEGMENTATION`: все controlled-hard, operational, stability, conservation и safety gates
  пройдены, а реальная boundary truth проверена человеком;
- `KEEP_COVERAGE_V3`: candidate измерен, но не проходит обязательные ворота;
- `EVIDENCE_BOUND`: метрики проходят, но доверия к реальному reference недостаточно.

Получен `KEEP_COVERAGE_V3`. Реальная boundary precision составила `0.044688`, speaker-count ratio
`0.5`, minority-speaker recall `0.017161`, а минимальный timing-shift partition ARI `0.289387`.
Продвижение и production mutation запрещены.
