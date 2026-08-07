# Current Goal

Status: current

Updated: 2026-08-07

MurmurMark exists to produce the most reliable local meeting transcript that available evidence can
support. The transcript must preserve words, chronology and roles, distinguish remote participants
by voice inside a session, and expose uncertainty. Notes, summaries, retrieval and work-system
updates are optional derivatives and do not hold the critical path.

Roadmap status and dependencies live in `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Coverage v3

OpsKarta nearest goal: Remote Speaker Coverage v3: сократить крупнейший frozen residual `unknown_remote_speaker` в Transcript Perfection Corpus v1 — 797.773 секунды и 1219 слов на шести сессиях — не меняя selected words, `Me`, роли, порядок и обычный aggregate transcript; построить карту причин unknown, проверить сначала bounded улучшения существующего локального Resemblyzer-профиля, а более тяжёлый локальный backend допускать только как pinned offline-кандидат без неявного скачивания; разрешать speaker attribution лишь при независимом enrollment и достаточных similarity/margin evidence, сохраняя conflicting overlap и слабую речь как unknown; продвинуть изолированный профиль только при снижении unknown seconds и words минимум на 25%, attributed-only B-cubed F1 и pairwise precision не ниже 0.95, полном word/timestamp conservation, прохождении 1x1/group/boundary/fallback gates и отсутствии регрессий Transcript Perfection Corpus; иначе выпустить воспроизводимый DO_NOT_PROMOTE с точным evidence ceiling; default transcript, capture, Echo Guard, ASR, retention, export, local mic diarization и optional derivatives не менять; добавить tests/report, актуализировать README, contracts, runbook, current goal, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Transcript Perfection Corpus v1 established one deterministic baseline over 12 frozen sources. It
keeps correctness, coverage, uncertainty and review burden separate and does not publish a synthetic
aggregate score. The largest measured actionable residual is remote speech whose words are present
but whose speaker is still `unknown`:

- `797.773s` of `9857.660s` remote speech;
- 1219 of 18212 selected remote words;
- six frozen sessions, including 1x1 and group calls;
- current attributable speech ratio `0.919071`.

This is larger than the remaining chronology (`62.690s`), ambiguous Me audio (`196.280s`) and
missing-Me (`21.120s`) queues under the frozen ranking. It also maps directly to the product mission:
the words already exist, but the transcript cannot always say which remote voice spoke them.

## Objective

Increase supported remote-speaker coverage without buying coverage through incorrect labels. The
result must preserve the current high-precision attributed subset and exact aggregate fallback.

The goal may end in either:

- `PROMOTE_REMOTE_SPEAKER_COVERAGE_V3` when usefulness and safety gates pass; or
- a scientifically complete `DO_NOT_PROMOTE` that identifies which unknown regions cannot be
  resolved with the available local evidence and models.

## Required Work

1. Freeze the 1219-word / `797.773s` unknown queue with session, utterance, word, frame and source
   hashes. Do not store private transcript text or names in tracked files.
2. Classify causes: missing seed enrollment, short speech, boundary dilution, internal change,
   overlap, low signal, rare speaker, cluster conflict or missing token/audio alignment.
3. Establish per-cause ceilings and the safest candidate order. Start with existing Resemblyzer
   evidence: adaptive windows, bounded unknown-only clustering and stricter secondary enrollment.
4. Keep every candidate isolated. A local pyannote/Sortformer-class backend may be evaluated only
   after its model, license, hashes, runtime and offline installation are explicit.
5. Attribute a word only when audio evidence, session-local enrollment, nearest-speaker similarity
   and margin agree. Never infer a human name or cross-session identity from voice.
6. Replay the six-session speaker corpus, private reference, five internal-boundary cases and the
   complete Transcript Perfection scorecard.
7. Publish one decision, testing snapshot and next residual ranking; update all active planning
   documents, then commit and push.

## Acceptance Gates

- unknown remote speech and unknown remote words each fall by at least `25%` relative to the frozen
  v2 baseline, or the result is `DO_NOT_PROMOTE`;
- attributed-only B-cubed F1 `>= 0.95` and pairwise precision `>= 0.95`;
- selected word loss/duplication is zero and turn text reconstructs selected text byte for byte;
- word timestamps remain monotonic and bounded by source utterances;
- 1x1 dominance, expected group speaker ranges and 5/5 internal-boundary cases pass;
- conflicting overlap and weak evidence remain explicit unknown;
- raw audio, selected dialogue, `Me`, plain transcript, notes and export inputs are unchanged;
- stale or missing model/evidence yields exact aggregate fallback;
- Transcript Perfection Corpus source integrity and every existing source gate remain green;
- replay with identical inputs is deterministic and offline.

## Safety Boundary

- no change to capture, Echo Guard, primary ASR or selected transcript text;
- no forced label solely to satisfy coverage;
- no voice-derived human names or cross-session identity;
- no cloud service, external write or implicit model download;
- no local mic multi-speaker diarization in this goal;
- no promotion of notes, summaries or work proposals.

## Previous Goal Result

Transcript Perfection Corpus v1 completed with `BASELINE_ESTABLISHED`:

- 12/12 frozen source artifacts verified by byte count, SHA-256 and schema;
- eight transcript dimensions reported explicitly;
- lexical correctness remains honest `not_measured`; word conservation is not treated as WER;
- no aggregate quality score or invalid sum across unlike corpus scopes;
- stale input test produces `INVALID_INPUTS`;
- repeated generation is byte-identical;
- ranked residuals: remote speaker `797.773s`, chronology `62.690s`, ambiguous Me audio
  `196.280s`, missing Me `21.120s`.

## After This Goal

1. Re-run Transcript Perfection Corpus v1 and take the new highest-ranked release blocker.
2. Repeat one isolated residual closure at a time until the speaker-resolved default gate is clear.
3. Promote the speaker-resolved transcript as the normal CLI read surface only after those gates.
4. Open Local Mic Multi-Speaker Diarization only after a real labeled multi-person local scenario.

Raw CAF and batch output remain authoritative. Live Shadow remains advisory and cannot select or
publish a speaker-resolved transcript.
