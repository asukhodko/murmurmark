# Current Goal

Status: current

Updated: 2026-08-07

MurmurMark exists to produce the most reliable local meeting transcript that the available
evidence can support. The transcript must preserve words, chronology and roles, distinguish remote
participants by voice inside a session, and expose uncertainty. Notes, summaries, retrieval and
work-system updates are optional derivatives and do not hold the critical path.

Roadmap status and dependencies live in `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Diarization v2

OpsKarta nearest goal: Remote Speaker Diarization v2: заменить audit-only utterance-level clustering полноценной локальной word/frame-level diarization authoritative remote audio; заморозить speaker-labelled reference и 1x1/group controls, сравнить pinned local backends и выбрать воспроизводимый профиль; обнаруживать internal speaker changes и привязывать каждое remote word к session-local anonymous speaker либо explicit unknown, сохраняя selected text, chronology и timestamps без потерь и не выводя имя по голосу; добиться на frozen corpus attributable remote speech >= 0.85, attributed-only B-cubed F1 >= 0.90, pairwise precision >= 0.90, корректных speaker-count/overlap/boundary gates и zero word loss/duplication; при missing/stale/model failure fail open к exact aggregate Colleagues; продвигать speaker-resolved read surface только после corpus-wide PROMOTE, иначе зафиксировать воспроизводимый DO_NOT_PROMOTE; default transcript, Me role, capture, Echo Guard, ASR text, retention, cloud/external writes и cross-session identity не менять; отдельно зафиксировать future mic multi-speaker path; добавить tests/report, согласовать README, contracts, runbook, current goal, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Remote Speaker Evidence Map v1 proved that the authoritative remote track contains useful speaker
identity evidence. On six frozen sessions it reached attributed-only B-cubed F1 `0.913884`, but
assigned only `629/1235` remote utterances and `50.3892%` of remote speech. It also cannot split an
utterance when the speaker changes inside its current ASR boundary.

That is enough evidence to stop treating diarization as a distant idea, but not enough coverage to
call the transcript speaker-resolved. Improving notes before closing this gap would optimize a
derivative while the primary artifact still collapses multiple people into `Colleagues`.

## Objective

Build and qualify an isolated remote-diarization profile over the unchanged authoritative remote
audio and selected ASR words. It should produce stable session-local anonymous speakers, explicit
unknown regions and evidence for every boundary. Promotion must improve speaker attribution without
changing recognized words, their order or the ordinary fallback transcript.

Operationally, an ideal transcript means: every retained word has the correct text, time and role;
every supported speaker attribution is correct; and every unsupported decision is visibly unknown.
It does not mean hiding uncertainty to make the Markdown look complete.

## Required Work

1. Freeze the six-session speaker corpus, hashes, current v1 outputs and representative 1x1/group,
   overlap and internal-speaker-change references. Record where labels are authoritative versus
   incomplete.
2. Add a word/frame-level diarization adapter over authoritative remote audio. Pin models, runtime,
   configuration and offline cache. Keep the current Resemblyzer map as a baseline.
3. Reconcile diarization turns with immutable selected ASR words. Split speaker spans at supported
   boundaries, represent overlap explicitly and assign weak regions to `unknown`.
4. Publish an isolated `remote_speaker_diarization_v2` evidence profile and a speaker-resolved read
   surface. Do not mutate plain transcript or selected ASR text during qualification.
5. Measure coverage, B-cubed and pairwise metrics, speaker-count error, boundary error, overlap
   behavior, word conservation, chronology and deterministic replay per session and corpus-wide.
6. Fail open to exact aggregate `Colleagues` when evidence, models, fingerprints or runtime are
   absent, stale or incompatible.
7. Add fixture and corpus regressions, contract and runbook documentation, then record an explicit
   `PROMOTE` or `DO_NOT_PROMOTE` decision and finish with a clean commit and push.

## Acceptance Gates

- attributable remote speech ratio is at least `0.85` on the frozen corpus;
- attributed-only B-cubed F1 is at least `0.90` and pairwise precision at least `0.90`;
- every single-remote control resolves to one dominant remote speaker; group speaker-count and
  boundary gates are stated before evaluation and pass on the frozen references;
- speaker changes inside one ASR utterance can be represented without losing or duplicating words;
- selected words, chronology and timestamps remain conserved, except for documented bounded
  timestamp refinement that passes exact order gates;
- overlap and weak evidence remain explicit; no word is force-assigned merely to maximize coverage;
- speaker IDs are session-local and anonymous; display names require explicit session review;
- missing, stale or failed diarization returns the exact aggregate transcript;
- ordinary transcript, `Me` attribution, notes, export and raw CAF remain byte-identical;
- repeated offline runs with the same inputs are deterministic.

## Safety Boundary

- no cross-session identity matching and no identity inferred from voice;
- no transcription rewrite, generated wording or factual correction in the diarization stage;
- no change to capture, Echo Guard, primary ASR, local-role selection or raw audio;
- no cloud model, implicit download or external publication;
- no mandatory notes, summaries, search, work proposals or UI work;
- no assumption that all mic speech is always one person: that remains a separate future profile.

## After This Goal

1. **Transcript Perfection Corpus v1** expands the frozen benchmark across wording, chronology,
   `Me`/remote roles, speaker boundaries, overlap, office noise and acoustic modes, then drives the
   remaining largest measured error class to `PROMOTE` or a documented limit.
2. **Local Mic Multi-Speaker Diarization v1** becomes executable only after a real multi-person local
   scenario and labelled corpus exist. It must distinguish Target-Me, other local speakers and
   unknown without weakening current Target-Me protection.
3. Summaries, retrieval and work-system proposals remain optional derivatives after transcript
   convergence. They may consume only versioned transcript evidence and cannot redefine the mission.

Raw CAF and batch output remain authoritative. Live Shadow remains advisory and cannot select or
publish a speaker-resolved transcript.
