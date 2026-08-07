# Product Vision

MurmurMark turns locally captured meetings into reliable, speaker-resolved and auditable
transcripts without relying on the meeting platform's cloud recording.

## Mission

MurmurMark exists to solve the difficult part of meeting memory: reconstruct what was said, when it
was said and by whom. It must work for 1 on 1 and group calls, distinguish participants mixed into
the remote track by voice, preserve genuine local speech and expose uncertainty instead of silently
guessing.

The normal local scenario has one person behind the laptop and therefore one `Me` role. The
architecture must not make that assumption permanent: if several local people participate through
one microphone, a future qualified layer should distinguish Target-Me, other local speakers and
unknown by evidence.

Notes, summaries, decisions, action lists, search and work-system updates are useful derivatives of
a reliable transcript. They are outside the core mission and stay optional until transcript quality
has converged. MurmurMark must not compensate for a weak transcript by producing polished prose.

Short version:

```text
Local-first, speaker-resolved meeting transcription that shows its evidence and uncertainty.
```

## Current Product North Star

One command should turn a complete local recording into an authoritative transcript in which:

- recognized words and chronology are conserved;
- `Me` and remote speech are separated without deleting genuine local speech;
- remote participants receive stable anonymous IDs inside the session when voice evidence supports
  the attribution;
- overlapping or weakly supported words remain explicit `unknown` rather than being force-assigned;
- a display name appears only after explicit session-local review;
- every quality decision has reproducible local evidence and an exact fallback.

Operationally, "ideal" does not mean a document with no warning labels. It means every supported
decision is correct and every unsupported decision is visibly unresolved. Corpus gates, not visual
smoothness, determine progress.

## Product Promise

MurmurMark records durable mic and remote tracks, processes them locally, returns a transcript with
an honest quality verdict and preserves enough evidence to audit or reproduce the result. Raw audio
is deleted only under an explicit retention policy.

The product should feel boring in the best way: one lifecycle command, clear permissions, resumable
processing, local files, no surprise network activity, no hidden recording route and no invented
speaker identity.

## Development Principle

Development converges from a frozen corpus. Each quality step changes one bounded error class and
either passes corpus-wide promotion gates or records a reproducible limit. Failed promotion never
weakens the selected transcript. The next goal is chosen from the largest measured residual class,
not from whichever unusual session arrived last.

Speech-quality work remains audio-first. Recognizable remote leaking into the microphone should be
removed before primary ASR while genuine local speech remains intact. Transcript cleanup is a
safety net and audit layer, not a substitute for Echo suppression. The current personalized
Speaker-Preserving Neural Echo v2.17 is the safe production plateau; unsupported acoustic modes or
failed evidence return to exact `local_fir`.

Speaker attribution is evidence-first. Voice can establish session-local similarity, not a durable
human identity. The current Remote Speaker Evidence Map v1 has strong attributed-only quality but
only about half of remote speech is covered. Remote Speaker Diarization v2 therefore replaces
derivative note work as the active goal.

## Core User Jobs

- Start and stop one reliable local recording without supervising internal stages.
- Recover processing after interruption without repeating capture or completed ASR.
- Read the best available transcript with correct words, chronology and speaker turns.
- Tell `Me`, each supported remote speaker and unresolved speech apart.
- See why a region is uncertain and inspect the supporting audio or audit evidence.
- Export or retain the transcript without silently publishing risky or stale output.

## Optional Derivatives

These may remain available, but they do not define product success:

- extractive notes and quality summaries;
- decisions, actions, risks and open questions;
- reviewed speaker-aware meeting memory;
- local evidence retrieval;
- Markdown, Obsidian or work-system proposal bundles;
- local or controlled LLM synthesis.

They may consume only versioned transcript evidence, must preserve citations and cannot change the
authoritative transcript. No major roadmap investment goes here before transcript-quality gates are
met, unless an explicit product decision changes the priority.

## Non-Goals For The Critical Path

- inferring names or cross-session identity from voice alone;
- cloud recording or mandatory cloud ASR;
- summaries that hide transcript uncertainty;
- automatic writes to issue trackers, repositories or shared docs;
- UI or menu-bar work before the CLI transcript path is mature;
- forced speaker attribution to make coverage metrics look complete.

## Near-Term Direction

1. **Remote Speaker Diarization v2:** word/frame-level remote speaker turns, internal boundary
   splitting, explicit unknown and corpus-wide quality gates.
2. **Transcript Perfection Corpus v1:** one benchmark for text, chronology, roles, speakers, overlap,
   acoustic modes and known residual defects.
3. **Local Mic Multi-Speaker Diarization v1:** only after a real multi-person local scenario and
   labelled corpus exist.

## Long-Term Direction

MurmurMark should become a local transcription engine that can be trusted independently of meeting
platform, participant count and ordinary acoustic variation. Different ASR, diarization and audio
backends may evolve behind stable contracts. The invariant is the same: conserve evidence, abstain
when it is insufficient and never trade a plausible-looking transcript for an unmeasured error.
