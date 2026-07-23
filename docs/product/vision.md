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
review evidence and honest failure. Classical audio promotion has now proved that remote-only
gating removes substantial echo but can delete short near-end speech during overlap. The current
quality step tests a local remote-conditioned residual suppressor against those frozen
counterexamples without weakening the `local_fir` baseline.

Remote-speaker diarization is an independent research branch after base transcript quality closure.
It starts with anonymous speaker intervals, then an evidence-backed speaker map and
`transcript.rich.json`. It does not wait for complete Echo suppression and does not block the CLI.

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
