# Current Goal

Updated: 2026-08-08

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Residual Reference Corpus v1

OpsKarta nearest goal: Remote Speaker Residual Reference Corpus v1: создать private blind candidate-targeted reference corpus для frozen six-session Coverage v3 residual (`851` words / `598.240s`) и всех `53` WavLM proposals (`23.357s`); заморозить word IDs, timestamps, bounded audio clips, session-local speaker exemplars и SHA-256; скрыть machine prediction до решения и принимать truth только из explicit human-reviewed либо exact scripted evidence, никогда из согласия моделей; поддержать outcomes `remote_speaker_XX`, `unknown_speaker`, `mixed`, `unusable` с provenance и конфликтами; считать evidence ready только при прямом покрытии всех 53 proposals и не менее 20 attributable recovered words с precision `>=0.98`, иначе завершить воспроизводимым `REFERENCE_INSUFFICIENT`; не менять selected transcript, Coverage v3, raw CAF, primary ASR или Echo Guard; держать речь и имена в private ignored artifacts, а tracked outputs ограничить агрегатами, portable paths и hashes; добавить CLI, тесты и deterministic replay, обновить README, contracts, runbook, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Independent Remote Speaker Evidence v1 квалифицировал локальный WavLM backend, но закрылся
`DO_NOT_PROMOTE`: восстановлено 53 из 851 unknown words и `23.357s` из `598.240s`, то есть только
`6.2280%` и `3.9043%`. Все conservation gates прошли, однако ни одно новое решение в существующей
reference session не имеет прямой эталонной метки.

Дальнейшее изменение clustering topology или порогов без candidate-targeted truth будет оптимизацией
по согласию моделей. Сначала нужен воспроизводимый способ получить и проверить прямые метки.

## Objective

Материализовать слепой private review corpus, который связывает каждый residual word и каждое WavLM
proposal с неизменяемым remote audio, session-local anonymous speaker exemplars и явным решением.
Закрыть этап результатом `REFERENCE_READY` или точным `REFERENCE_INSUFFICIENT`.

## Required Work

1. Заморозить шесть Coverage v3 sessions, 851 residual words, 53 WavLM proposals и их lineage.
2. Нарезать bounded remote-only clips и отдельные trusted speaker exemplars без изменения raw CAF.
3. Создать blind grading format: prediction хранится отдельно и не показывается до решения.
4. Добавить CLI build/status/grade/replay и валидацию допустимых outcomes.
5. Считать coverage и precision только по явным trusted labels; `unknown`, `mixed` и `unusable` не
   превращать в speaker assignment.
6. Писать private rows под ignored `sessions/_reports/`; tracked manifest не содержит речь, имена и
   абсолютные пути.
7. Интегрировать reference readiness в Transcript Perfection как отдельный измеряемый blocker.

## Acceptance Gates

- 6/6 frozen sessions и все 851 residual words присутствуют ровно один раз;
- все 53 independent-WavLM proposals имеют review rows и immutable provenance;
- каждый клип bounded, remote-only и fingerprint-bound к Coverage v3 word IDs;
- blind answer не содержит скрытого candidate label до явного reveal/evaluation;
- truth grades ограничены `human_reviewed` и `exact_scripted`;
- ready требует direct reference всех 53 proposals, минимум 20 attributable words и precision
  `>=0.98`;
- incomplete or conflicting review даёт `REFERENCE_INSUFFICIENT`, а не forced attribution;
- replay byte-stable; selected transcript, v3 outputs and raw CAF hashes unchanged;
- public artifacts contain only counts, grades, hashes and portable paths.

## Safety Boundary

- no capture, Echo Guard, primary ASR, timeline or selected-transcript changes;
- no cloud inference, voice-derived names or cross-session identity;
- no promotion from Resemblyzer/WavLM agreement;
- no automatic speaker assignment from an unfinished review pack;
- no optional notes, summaries, UI or external writes.

## Previous Goal Result

Independent Remote Speaker Evidence v1 completed with `DO_NOT_PROMOTE`:

- pinned WavLM XVector backend, offline runtime and deterministic dev/held-out split;
- 53 words / `23.357s` recovered; 798 words / `574.883s` remain unknown;
- B-cubed F1 `0.962171`, pairwise precision `0.961675`, 5/5 boundaries;
- direct candidate reference coverage `0/5` in the existing reference session;
- all words, timestamps, roles, `Me`, v2/v3 labels, raw inputs and fallback preserved.

## After This Goal

1. If `REFERENCE_READY`, evaluate a constrained/open-set WavLM diarization profile against direct
   candidate truth.
2. If `REFERENCE_INSUFFICIENT`, keep Coverage v3 and expose the exact remaining review requirement;
   do not tune thresholds or clustering from machine agreement.
3. Real-meeting lexical correctness and local mic multi-speaker diarization remain separate evidence
   prerequisites.

Raw CAF and batch output remain authoritative. Live Shadow remains advisory.
