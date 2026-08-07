# Product Vision

MurmurMark turns sensitive meetings into reliable local transcripts and reviewable work artifacts
without relying on the meeting platform's cloud recording.

## Mission

MurmurMark exists to turn important work calls into local, reliable and useful artifacts:
transcript, notes, decisions, actions and risks. It should preserve privacy, source evidence and user
control instead of asking the user to trust a cloud recorder, a meeting bot or unsupported generated
summaries.

The practical mission is stricter than "produce some text". For a complete recording, MurmurMark
should process unattended and return a truthful outcome: ready for notes, review first, or blocked
with an explicit reason. A risky transcript must stay visibly risky.

Short version:

```text
Local-first meeting transcription for sensitive work.
```

## Current Product North Star

One command should turn a complete meeting into a truthful transcript and a short set of confirmed
work artifacts. Every displayed decision, action, risk or question must retain exact source text,
speaker provenance and utterance evidence. The same local evidence should then be searchable and
usable for reviewed work proposals without hidden external writes.

Audio quality remains a hard invariant. For speaker playback, ASR input should preserve every
confirmed local word while carrying no recognizable authoritative remote; nearby people and
unexplained energy remain explicit. Speaker-Preserving Neural Echo v2.17 is the current safe
production plateau. Stronger local separators reached a reproducible presence/absence limit, so the
audio track reopens only with new independent Target-Me presence evidence.

The product is for situations where a user needs memory and follow-up from a call, but cannot safely
create a shared cloud recording: 1 on 1 meetings, retrospectives, incident reviews, architecture
reviews, planning sessions, and internal discussions with sensitive context.

## Product Promise

MurmurMark records only what is needed, keeps it local by default, produces a transcript with a
quality verdict, creates evidence-backed notes, and deletes raw audio under an explicit policy.

The product should feel boring in the best way: clear permissions, visible health, resumable
processing, local files, no surprise network activity, no hidden recording routes, no magical claims
about speaker identity.

## Development Principle

Development converges from evidence, not from adding output profiles. Each quality step freezes its
input, changes one bounded error class, and either passes corpus-wide promotion gates or records a
reproducible evidence limit. Failed promotion never weakens the selected transcript. Proven missing
local speech now has a bounded completion profile and an executable text-review lane. The product
path hides safe pipeline mechanics behind `murmurmark meeting`, while retaining checkpoints,
review evidence and honest failure.

Speech-quality work is audio-first. Remote audio leaking into the microphone must be removed before
the primary ASR while genuine local speech remains intact. Transcript cleanup is a safety net and
an audit layer; it cannot count as successful Echo suppression or hide a weak audio candidate.
Classical and end-to-end neural trials established that aggressive remote suppression can delete
short near-end speech during overlap. The promoted personalized hybrid now uses controlled
Target-Me enrollment, remote evidence, bounded attenuation and direct whisper.cpp gates; every
unsupported or unsafe session returns to exact `local_fir`. Reference-conditioned three-stem
research showed that exact remix alone cannot identify another nearby speaker. The follow-up
speaker-disjoint corpus and paired enrollment controls proved that speaker identity is present in
the data, but the small scratch-trained separator still missed immutable dev waveform-quality and
absent-speaker gates. Both experiments stopped before hard-test and left production unchanged.

Evidence Notes And Export v2 now binds the selected transcript, verdict, review evidence, notes and
export into one verifiable handoff without depending on profile-specific filenames. Release-quality
CLI packages that boundary into a supported, deterministic and upgrade-safe local release. Reliable
Final Handoff v1 now makes cached/resumed completion bounded and actionable. Authoritative
Incremental ASR and exact remote production are complete, but remote-only work does not shorten the
parallel mic critical path. Causal Canonical Mic ASR v1 then measured the exact post-Echo boundary
and closed with `DO_NOT_PROMOTE`: current local-FIR and Speaker-Preserving selection depend on the
complete session, and `0/147` candidate mic windows matched final canonical PCM.

Remote Speaker Evidence Map v1, Anonymous Rich Transcript Handoff v1 and explicit reviewed naming
now provide promoted optional speaker views over authoritative remote audio. The bounded Pre-ASR
Target-Me frontier is closed after SepFormer failed reliable presence/absence separation before dev;
production v2.17 remains the exact plateau. Reviewed Speaker-Aware Meeting Memory v1 now connects
explicit session-local labels to evidence-backed notes and export through a promoted opt-in
handoff. A pinned local free-text model then failed qualification: independent evidence checks
rejected too many authored claims, so no generated-text mode was exposed. The bounded ID-only
alternative passed as an explicit optional view: it selects only known statements and keeps exact
source evidence. The current step adds explicit `confirmed`, `rejected` or `unresolved` status to
meeting artifacts, followed by local evidence retrieval and reviewed proposal bundles. A stale or
missing selector keeps the deterministic exact source. Voice similarity never assigns a person's
identity; cross-meeting matching and external writes remain separate gates.

## Primary User

A technical person on macOS who participates in meetings and wants:

- a reliable local record of their own microphone and the remote meeting audio;
- a speaker-aware transcript;
- meeting notes tied to transcript evidence;
- controlled integration with Obsidian, Markdown, issue trackers or docs repositories;
- strong privacy defaults.

## Core Jobs

- Record a meeting locally without changing Teams/Zoom/Meet audio settings.
- Keep the user's microphone separate from remote participants.
- Detect empty or broken recordings before it is too late.
- Convert the session into a structured transcript.
- Identify uncertain speaker/text regions instead of hiding uncertainty.
- Produce notes, decisions, action items and risks with citations.
- Delete raw audio after successful processing when policy says so.

## Non-Goals for v1

- No general-purpose podcast recorder.
- No cloud meeting bot.
- No automatic publishing to Confluence/Jira/Git without review.
- No always-on ambient recorder.
- No promise that remote participants are named correctly without confidence or review.
- No raw audio upload to external APIs by default.
- No virtual microphone or virtual speaker as the default architecture.

## Product Language

Use `MurmurMark` for the product and UI. Use `murmurmark` for repository, CLI, package names and machine identifiers.

Short description:

```text
Local-first macOS meeting transcription and notes pipeline.
```

Long description:

```text
MurmurMark records your microphone and the selected meeting application's audio into separate local
tracks, builds a speaker-aware transcript with a quality verdict, and turns it into evidence-backed
meeting notes under an explicit privacy and retention policy.
```
