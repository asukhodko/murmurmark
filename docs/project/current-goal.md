# Current Goal

Updated: 2026-08-08

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Controlled Remote Speaker Truth Lab v1

OpsKarta nearest goal: Controlled Remote Speaker Truth Lab v1: создать полностью локальный детерминированный exact-scripted multi-speaker remote corpus с отдельными source stems, canonical mixtures, word/speaker/timestamp truth, SHA-256 и session-disjoint train/dev/hard splits; покрыть минимум четыре anonymous voices, 1x1 и group turns, внутрисегментные speaker changes, short turns, silence, overlap, rare speaker и open-set speaker без enrollment; запускать promoted Coverage v3 и candidate constrained/open-set WavLM topology только как audit, не считать machine agreement truth и не использовать real-session residual для настройки; завершить LAB_READY только при полном word conservation, direct truth coverage, deterministic replay, held-out B-cubed F1 и pairwise precision >=0.98, open-set false attribution ==0 и boundary recall 100%, иначе DO_NOT_ADVANCE; не менять selected transcript, Coverage v3, raw CAF, primary ASR или Echo Guard; хранить generated speech в private ignored artifacts, tracked outputs ограничить aggregate metrics, portable paths и hashes; добавить CLI, тесты и corpus report, обновить README, contracts, runbook, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Independent WavLM recovered 53 residual words / `23.357s`, but direct truth covers none of them.
Remote Speaker Residual Reference Corpus v1 produced a correct blind queue of 278 items and closed
`REFERENCE_INSUFFICIENT`: a real-meeting promotion decision cannot be manufactured autonomously.

The next useful autonomous step is an exact local laboratory. It can reveal whether constrained and
open-set attribution is technically viable before asking for real blind review, while keeping the
real-session residual untouched.

## Objective

Generate known multi-speaker remote sources and mixtures, freeze exact speaker/word/time truth, and
evaluate the existing and candidate diarization topology on disjoint held-out material. End with
`LAB_READY` or reproducible `DO_NOT_ADVANCE`.

## Required Work

1. Pin local source voices/renderers and fingerprint every stem and mixture.
2. Build train/dev/hard scenarios with at least four anonymous speakers, short turns, internal
   changes, overlap, silence, rare speaker and open-set speaker.
3. Store exact script, speaker, word and boundary truth privately; publish only aggregate hashes and
   metrics.
4. Run Coverage v3 and candidate constrained/open-set WavLM without learning from held-out data.
5. Measure word conservation, direct attribution, B-cubed, pairwise precision, boundary recall and
   open-set false attribution.
6. Add CLI build/status/replay, deterministic tests and a corpus report.
7. Keep real-session promotion blocked regardless of a synthetic `LAB_READY` result.

## Acceptance Gates

- source stems reconstruct each canonical mixture within the declared tolerance;
- every reference word has one stable ID, speaker, start and end;
- train/dev/hard speakers and source hashes obey the frozen split contract;
- all held-out words are conserved exactly;
- held-out B-cubed F1 and pairwise precision are at least 0.98;
- all scripted speaker boundaries are recovered;
- no open-set word is forced into an enrolled speaker;
- replay is byte-stable and public artifacts contain no speech, names or absolute paths;
- any failed gate produces `DO_NOT_ADVANCE`, never a weaker production threshold.

## Safety Boundary

- synthetic lab evidence cannot promote or edit a real transcript;
- no capture, Echo Guard, primary ASR, Coverage v3 or raw CAF changes;
- no cloud speech service, voice-derived name or cross-session identity;
- no tuning on the frozen six-session residual;
- no notes, summaries, UI or external writes.

## Previous Goal Result

Remote Speaker Residual Reference Corpus v1 completed `REFERENCE_INSUFFICIENT`:

- 851 words / `598.239509s` frozen across six sessions;
- 278 blind review items and 28 session-local exemplars;
- all 53 WavLM proposals / `23.356997s` sealed separately;
- 0 reviewed items and 0 directly referenced proposal words;
- structural, privacy, replay and no-mutation gates pass;
- Coverage v3 remains the exact fallback.

## After This Goal

1. `LAB_READY` permits a bounded constrained/open-set candidate on synthetic held-out truth only.
2. `DO_NOT_ADVANCE` closes the current topology and preserves Coverage v3.
3. Real-session promotion still requires blind human or exact scripted truth from the private
   residual queue.
4. Real lexical correctness and local mic multi-speaker diarization remain separate prerequisites.

Raw CAF and batch output remain authoritative. Live Shadow remains advisory.
