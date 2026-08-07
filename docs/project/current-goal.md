# Current Goal

Status: current

Updated: 2026-08-07

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Plain transcript, extractive notes and guarded export stay authoritative. Optional model-assisted
views may advance only through frozen local evidence gates and must fail open to exact source
artifacts.

Roadmap status and dependencies live in `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Reviewed Meeting Artifacts v1

OpsKarta nearest goal: Reviewed Meeting Artifacts v1: превратить exact decisions, actions, risks и open questions из current Reviewed Speaker-Aware Meeting Memory v1, используя promoted Evidence-Only Local Note Selection v1 только при валидном handoff, в короткую fingerprint-bound очередь confirmed/rejected/unresolved; создать deterministic review template, apply и isolated artifact bundle, копируя text, speaker provenance и evidence utterance IDs byte-for-byte и никогда не считая unresolved подтверждённым; при missing/stale/malformed selector fail open к deterministic exact source catalog; заморозить тот же six-session corpus и проверить review burden, evidence integrity, deterministic replay, stale-input rejection и ordinary-output non-regression; default transcript/notes/export, capture, Echo Guard, ASR, retention, cloud/external writes, cross-session identity и UI не менять; добавить tests/report, согласовать README, contracts, runbook, current goal, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Reviewed Speaker-Aware Meeting Memory v1 already provides 726 exact statements on 6/6 frozen
sessions. Free-text local synthesis was rejected because 69/142 generated claims failed independent
support checks. Evidence-Only Local Note Selection v1 removed text authorship and passed its narrow
optional gates: 47 review-marked candidates became 28, every displayed claim remained exact source
text and model-authored published claims stayed at zero.

The remaining product gap is explicit truth status. A candidate that looks useful is still not a
confirmed decision, commitment, risk or open question. MurmurMark needs a short review queue and a
durable artifact bundle before search or work proposals can safely consume meeting memory.

## Objective

Create a deterministic session-local review loop for exact meeting-artifact candidates. A user or
agent can mark each row `confirmed`, `rejected` or `unresolved`; materialization preserves the source
statement and provenance exactly and exposes only confirmed items as confirmed artifacts.

## Required Work

1. Build a stable candidate catalog from verified speaker-aware memory. Use the promoted ID selector
   only when its handoff and fingerprints are current; otherwise use the deterministic exact source.
2. Generate a compact fingerprint-bound review template for decisions, actions, risks and open
   questions. Keep summary rows outside the confirmation contract.
3. Validate complete and partial answer files, allowed states, row identity, source fingerprint and
   immutable evidence membership. Unknown or stale decisions must fail open.
4. Materialize an isolated bundle containing confirmed, rejected and unresolved rows plus a concise
   reviewed Markdown view. Copy text, speaker provenance and utterance IDs byte-for-byte.
5. Never present `unresolved` as an accepted fact or obligation. Rejected rows remain audit evidence
   and cannot enter confirmed notes.
6. Freeze the same six-session corpus and measure queue size, confirmation coverage, deterministic
   replay, stale-input rejection and ordinary-output non-regression.
7. Add fixture/corpus tests, contract and runbook documentation, then commit and push a clean tree.

## Acceptance Gates

- every review row maps to one current source statement and exact evidence provenance;
- only `confirmed`, `rejected` and `unresolved` are accepted states;
- selected-model output is optional input, never a source of wording or evidence;
- missing or stale selection falls back to the deterministic exact source catalog;
- stale, duplicate, unknown or malformed decisions cannot publish a current bundle;
- confirmed Markdown contains only confirmed rows and exact source text;
- repeated runs with the same inputs are deterministic and interruption-safe;
- default transcript, notes, export and all earlier handoffs remain byte-identical;
- the six-session report records bounded review burden and a clear promotion decision.

## Safety Boundary

- no generated or edited factual wording;
- no automatic confirmation from model rank, score or confidence;
- no cloud request, model pull, external write or cross-session participant identity;
- no capture, Echo Guard, ASR, transcript selection, default notes/export, retention or UI change;
- no claim that the optional selector's vacuous high-confidence retention proves future safety.

## Completed Checkpoint

Evidence-Only Local Note Selection v1 is frozen with
`PROMOTE_OPTIONAL_EVIDENCE_SELECTION`. It passed 6/6 sessions, selected 56 of 189 source statements,
reduced 47 review-marked candidates to 28, covered all available categories and 80% of available
speaker occurrences, and published zero model-authored claims. Its high-confidence source
population was empty, so that retention result is explicitly limited.

## After This Goal

1. Local Evidence Retrieval v1 indexes exact utterances and confirmed artifacts with fingerprint
   invalidation and retention-aware deletion.
2. Reviewed Work Proposals v1 materializes confirmed artifacts into local proposal bundles with
   evidence and diffs.
3. External systems remain explicit reviewed destinations; UI remains optional and does not hold
   the CLI path.

Raw CAF and batch output remain authoritative. Live Shadow is capture-safe but advisory, and its
promotion remains blocked by measured quality and runtime evidence.
