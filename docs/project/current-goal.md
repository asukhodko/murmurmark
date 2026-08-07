# Current Goal

Status: current

Updated: 2026-08-07

MurmurMark exists to produce the most reliable local meeting transcript that the available
evidence can support. The transcript must preserve words, chronology and roles, distinguish remote
participants by voice inside a session, and expose uncertainty. Notes, summaries, retrieval and
work-system updates are optional derivatives and do not hold the critical path.

Roadmap status and dependencies live in `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Transcript Perfection Corpus v1

OpsKarta nearest goal: Transcript Perfection Corpus v1: собрать единый локальный frozen benchmark над существующими real-session и synthetic references для recognized words, chronology, Me/remote roles, remote speaker turns, overlap, missing Me, remote leakage и acoustic modes; формально определить operational transcript perfection как корректный supported result плюс explicit unknown, не позволяя улучшать score простым abstention; добавить одну воспроизводимую corpus-команду, versioned manifest и no-regression gates, которые сохраняют отдельные safety thresholds, private references и raw CAF; измерить текущий production baseline, ранжировать residual classes по пользовательскому вреду, длительности, частоте и доказательности и выбрать один крупнейший исправимый класс как следующую цель; default transcript, capture, Echo Guard, ASR, promoted remote diarization, retention, cloud/external writes и optional synthesis не менять; добавить tests/report, согласовать README, contracts, runbook, current goal, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

The pipeline already has many strong but separate gates: selected words, transcript order, local
recall, remote leakage, Target-Me preservation, acoustic mode, remote speaker precision and
speaker-boundary conservation. Remote Speaker Diarization v2 is now promoted with `91.9071%`
attributable remote speech, B-cubed F1 `0.960690`, pairwise precision `0.959564` and exact selected
word conservation.

The remaining risk is fragmentation of evidence. A new session can reveal a defect, but there is no
single scorecard showing whether it is the largest user-visible gap, whether fixing it regresses a
different layer, or how close the whole product is to its mission. The next useful step is therefore
measurement convergence rather than another local heuristic.

## Objective

Build one deterministic transcript-quality benchmark and CLI report over existing frozen corpora.
It must preserve the meaning and thresholds of each source gate while presenting one operational
answer:

- what is already correct and supported;
- what remains explicit unknown or review;
- which residual class causes the most user harm;
- which exact next command or engineering goal follows;
- whether a candidate change regresses any promoted capability.

The corpus establishes a baseline and selection mechanism. It does not need to make the current
transcript perfect in the same goal.

## Required Work

1. Inventory current frozen references and manifests for words, chronology, roles, local recall,
   remote-forbidden leakage, acoustic modes and remote speakers. Record authoritative, partial and
   synthetic coverage explicitly.
2. Define a versioned `transcript_perfection/v1` contract. Separate correctness, coverage,
   uncertainty and review burden so abstention cannot masquerade as quality.
3. Add one local corpus command and report that reuses current artifacts where possible, verifies
   input lineage and emits per-session, per-dimension and aggregate results.
4. Freeze a portable manifest with session IDs and SHA-256 only. Private reference text, names and
   raw audio stay ignored and local.
5. Preserve every existing hard safety gate. A missing reference is `not_measured`, not a pass; a
   stale artifact is an explicit failure or bounded skip according to the source contract.
6. Rank residual classes by severity, affected seconds, frequency, confidence and repairability.
   Choose one next goal from that ranking and record why alternatives are lower priority.
7. Add synthetic and real-corpus regressions, update documentation and OpsKarta, then commit and push
   a reproducible baseline decision.

## Acceptance Gates

- one command rebuilds or verifies the benchmark and produces JSON plus a concise Markdown report;
- every promoted source gate is represented without weakening its original threshold;
- words, order, roles, local recall, remote leakage, remote speakers, overlap and acoustic modes have
  explicit measured or `not_measured` status;
- correctness and coverage are reported separately; unknown/review burden remains visible;
- no missing or stale input is silently counted as passing;
- the tracked manifest contains no transcript text, human names or machine-specific absolute paths;
- repeated offline runs with the same inputs are deterministic;
- raw CAF, selected transcripts and existing promoted artifacts remain unchanged;
- the report identifies one largest actionable residual class and the next complete engineering
  goal, with evidence for the choice.

## Safety Boundary

- no new capture, Echo Guard, ASR, diarization or repair algorithm in this goal;
- no loosening of existing corpus thresholds to make the aggregate report green;
- no cloud model, implicit download, external publication or identity inference;
- no mandatory notes, summaries, retrieval, work proposals or UI work;
- no claim of perfection where a dimension lacks reference coverage.

## Previous Goal Result

Remote Speaker Diarization v2 completed with `PROMOTE`:

- frozen sessions: `6`;
- attributable remote speech ratio: `0.919071`;
- attributed-only B-cubed F1: `0.960690`;
- attributed-only pairwise precision: `0.959564`;
- frozen internal-boundary cases: `5/5`;
- selected-word loss or duplication: `0`;
- 1x1 and group speaker-count gates: passed;
- stale/model/input failures: exact aggregate fallback;
- promoted read command: `murmurmark transcript SESSION --rich` after
  `murmurmark audit remote-diarization SESSION`.

The remaining `8.0929%` of remote speech stays explicit unknown. A rare fourth voice in the private
reference lacked enough enrollment and was not forced into a known speaker.

## After This Goal

1. Execute the largest measured, safely repairable residual class selected by the perfection report.
2. Repeat the unified corpus gate after every transcript-quality change.
3. Start Local Mic Multi-Speaker Diarization v1 only after a real multi-person local scenario and
   labelled corpus exist.
4. Keep summaries, retrieval and work-system proposals optional until transcript convergence.

Raw CAF and batch output remain authoritative. Live Shadow remains advisory and cannot select or
publish a speaker-resolved transcript.
