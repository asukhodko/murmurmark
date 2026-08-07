# Current Goal

Updated: 2026-08-07

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Speaker-Resolved Transcript Default v1

OpsKarta nearest goal: Speaker-Resolved Transcript Default v1: сделать promoted Remote Speaker Coverage v3 стандартным локальным результатом `murmurmark transcript`, meeting handoff и guarded export для совместимых сессий, сохранив authoritative selected words, текст, порядок, роли и timestamps без изменений; показывать доказанные session-local anonymous speaker IDs, а неподдержанные remote words оставлять aggregate `Colleagues`; принимать v3 только при совпадении policy, implementation, frozen corpus manifest и всех session input/artifact SHA-256, иначе автоматически и явно возвращать byte-identical aggregate transcript; сохранить явный `--rich` как совместимый диагностический путь и применять человеческие имена только из complete fingerprint-bound review; добавить selected-speaker-profile и fallback reason в status/outcome/handoff, проверить six-session corpus, 1x1/group, 5/5 boundaries, stale/missing/model fallback, deterministic replay, ordinary transcript/export и Transcript Perfection gates; capture, Echo Guard, основной ASR, local mic diarization, cross-session identity и optional derivatives не менять; актуализировать README, contracts, runbook, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Remote Speaker Coverage v3 is already promoted and attributes `93.9312%` of frozen remote speech
with B-cubed F1 `0.962171` and pairwise precision `0.961675`. The remaining evidence is honest:
unsupported words stay `unknown` and can be rendered as aggregate `Colleagues`.

Residual Evidence v4 tested the obvious bounded continuation. It recovered 124 words / `83.640s`,
but reductions of `14.5711%` words and `13.9811%` seconds missed both `20%` gates. All safety gates
passed, so v4 closed with `DO_NOT_PROMOTE`. More threshold tuning is not the shortest reliable path.

The product value now exists behind explicit `--rich`; this goal moves the proven result into the
normal user path without claiming identity where evidence is absent.

## Objective

Make the default transcript visibly speaker-resolved on eligible sessions. Keep aggregate output as
an exact fail-open fallback and make the selected profile and fallback reason machine-readable.

## Required Work

1. Define one selector over promoted v3, reviewed session-local labels and exact aggregate fallback.
2. Use it consistently in `transcript`, meeting final handoff, status/outcome and guarded export.
3. Preserve `--rich` compatibility and never infer a human name from voice.
4. Add stale policy, manifest, implementation, input, artifact and missing-runtime fallback tests.
5. Replay six frozen sessions, 1x1/group and five internal boundaries; keep Transcript Perfection
   green and ordinary selected words byte-exact.
6. Publish the decision, refresh planning, commit and push.

## Acceptance Gates

- eligible sessions select promoted v3 without an extra user flag;
- every supported remote word renders its session-local speaker; every unsupported word remains
  `Colleagues` rather than receiving a guessed identity;
- selected words, text, timestamps, `Me`, roles and order stay exact;
- stale, missing or incompatible evidence returns the exact previous aggregate transcript;
- status, outcome and handoff expose selected speaker profile and fallback reason;
- reviewed names require a complete current-session decision with matching fingerprints;
- 1x1, group, 5/5 boundaries, deterministic replay, export and Transcript Perfection gates pass.

## Safety Boundary

- no capture, Echo Guard, primary ASR, audio selection or raw retention change;
- no cross-session voice identity and no voice-derived human names;
- no local mic multi-speaker claim without a real labeled scenario;
- no cloud dependency or implicit model download;
- no new notes, summaries or work-system behavior.

## Previous Goal Result

Remote Speaker Residual Evidence v4 completed with `DO_NOT_PROMOTE`:

- recovered 124 words / `83.640207s` from 851 words / `598.239509s`;
- reductions were `14.5711%` words and `13.9811%` seconds versus `20%` gates;
- attributable speech reached an audit-only `0.947797`;
- B-cubed F1 stayed `0.962171`, pairwise precision `0.961675`;
- all conservation, existing-label, 1x1/group, 5/5 boundary, raw and fallback gates passed;
- promoted v3 and the exact aggregate fallback remain unchanged.

## After This Goal

1. Re-run Transcript Perfection Corpus and take its largest remaining transcript defect.
2. Open local mic multi-speaker diarization only after a real labeled scenario exists.
3. Revisit remote residuals only with a genuinely independent pinned speaker backend or stronger
   enrollment evidence, not lower v3 thresholds.

Raw CAF and batch output remain authoritative. Live Shadow remains advisory.
