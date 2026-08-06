# Current Goal

Status: current

Updated: 2026-08-06

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Raw CAF and batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the guarded
production audio profile. Evidence Handoff v2 remains the only input to guarded export.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Anonymous Rich Transcript Handoff v1

OpsKarta nearest goal: Anonymous Rich Transcript Handoff v1: превратить promoted Remote Speaker Evidence Map v1 в versioned optional rich transcript и CLI/read surface, сохраняя selected dialogue byte-identical; публиковать session-local anonymous speaker IDs только при текущих input/model/output fingerprints и passing per-session/corpus gates, оставлять abstain как aggregate Colleagues, не присваивать имена и не менять обычный Markdown, notes, verdict, Evidence Handoff v2 или guarded export; доказать referential integrity, stale/fail-open/replay и corpus no-regression; завершить PROMOTE или воспроизводимым DO_NOT_PROMOTE, добавить тесты, актуализировать документацию, roadmap и OpsKarta, закоммитить и отправить изменения.

## Why This Is Next

Remote Speaker Evidence Map v1 completed with `PROMOTE_AUDIT_ONLY`. Six frozen real sessions
produced `14` stable anonymous clusters and attributed `4490.170s` of remote speech while retaining
`4420.800s` as explicit aggregate fallback. On `66` attributed private-reference rows, ARI was
`0.865804` and B-cubed F1 was `0.913884`.

The evidence is useful but still hidden under a research directory. The next bounded step is to
make it a stable optional artifact that normal CLI consumers can find and verify. This does not
make speaker labels authoritative and does not require names.

## Objective

Publish a deterministic rich transcript handoff that binds an unchanged selected dialogue to a
current passing anonymous speaker map. Expose it through an explicit CLI read path while preserving
the existing plain transcript and guarded export behavior.

## Required Work

1. Define a versioned rich-handoff contract that references the selected dialogue, anonymous map,
   attribution rows, corpus decision, model and parameter fingerprints.
2. Materialize the handoff transactionally only when every source fingerprint is current and the
   per-session map plus frozen corpus permit optional publication.
3. Keep the original utterance array logically exact. Add speaker evidence by utterance ID rather
   than rewriting text, role, order or timestamps.
4. Add `murmurmark transcript SESSION --rich` and `--path-only` behavior for the optional artifact.
   Missing or stale rich evidence must explain the fallback and leave ordinary transcript reads
   untouched.
5. Keep anonymous IDs session-local. Do not accept names, voice identity across meetings or
   implicit identity inference in this stage.
6. Prove referential integrity, stale-input rejection, missing-model fail-open, deterministic
   replay, interrupted publication recovery and selected-output non-regression.
7. Freeze a real corpus decision. `PROMOTE` may expose the optional rich CLI artifact only; notes,
   Evidence Handoff v2 and guarded export require later goals.

## Acceptance Gates

- every rich attribution references an existing selected utterance ID exactly once;
- the selected utterance list, text, role, order and timestamps are unchanged;
- source dialogue, map, model, parameters and corpus decision have verified SHA-256 lineage;
- stale, missing, weak or non-promoted evidence yields an explicit unavailable/fallback result;
- repeated runs over unchanged inputs are byte-identical after excluding runtime-only telemetry;
- plain `murmurmark transcript`, notes, verdict, Evidence Handoff v2 and guarded export are
  byte-identical before and after rich publication;
- no names or cross-session identity links appear in tracked or generated rich artifacts;
- raw CAF and all existing selected profiles remain unchanged;
- the frozen corpus ends in reproducible `PROMOTE` or `DO_NOT_PROMOTE`.

## Safety Boundary

- no changes to capture, Echo Guard, primary ASR or selected transcript profiles;
- no speaker naming, roster inference or identity matching from voice;
- no automatic notes/synthesis changes and no export promotion;
- no cloud audio and no UI work;
- no fallback that silently presents aggregate `Colleagues` as a named or distinct person.

## Completed Predecessor

Remote Speaker Evidence Map v1 completed on 2026-08-06 with `PROMOTE_AUDIT_ONLY`. The frozen corpus
passed six-session count, integrity, boundary and chunk/replay gates. Optional evidence covers
`50.3892%` of remote speech; unsupported speech remains aggregate. See
`docs/testing/2026-08-06-remote-speaker-evidence-map-v1.md`.
