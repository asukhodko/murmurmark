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
human identity. Remote Speaker Coverage v3 is promoted with `93.9312%` attributable remote speech,
exact selected-word and v2-label conservation. Unsupported regions and a rare voice without enough
enrollment remain explicit `unknown`. Transcript Perfection Corpus v1 remains the convergence
baseline. Residual Evidence v4 measured a safe `14.57%` word / `13.98%` second ceiling and closed
with `DO_NOT_PROMOTE`; Speaker-Resolved Transcript Default v1 is promoted. Lexical Accuracy
Reference Corpus v1 proves the exact 67-word digital subset at WER/CER `0`, but closes
`REFERENCE_INSUFFICIENT` for real meetings because no human-reviewed reference exists. Independent
WavLM evidence then recovered only 53 residual words / `23.357s` and closed `DO_NOT_PROMOTE`.
The blind residual pack covers 851 words in 278 items. A bounded direct seed now contains 33 primary
answers and 8 repeats: 8 attributed, 11 unknown, 4 mixed and 10 unusable, with consistency `7/8`.
Exact local multi-speaker truth qualified the Coverage v3 control. Blind hard-v2 rejected
word-level duration/fusion candidates; blind hard-v3 then rejected long-span segment-context fusion
with `0/20` boundaries and two open-set errors. Interval and enrollment candidates then failed their
material gates. Direct blind adjudication kept Coverage v3 after the candidate lost two correct
controls and increased fail-closed unsafe accepts from 8 to 13. Purity v2 restored control safety
but only 7/14 profiles qualified and no safe identity was added. The next stage mines homogeneous
session-local enrollment; any promotion still requires disjoint direct truth and corpus qualification.

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

1. **Transcript Perfection Corpus v1:** maintain the frozen benchmark for text,
   chronology, roles, speakers, overlap, acoustic modes and known residual defects.
2. **Remote Speaker Usability Gate Error Decomposition v1:** explain unsafe real-session identity
   accepts and misses before selecting another backend or threshold.
3. **Disjoint direct truth:** any future candidate must be frozen before a new terminal set opens;
   the now-open Truth v2 is development evidence only and cannot promote another candidate.
4. **Human-Reviewed Lexical Seed v1:** external-evidence prerequisite; do not tune ASR from machine
   agreement while it is absent.
5. **Measured residual loop:** rerank after every bounded `PROMOTE` or evidence ceiling and close the
   next highest-impact class.
6. **Local Mic Multi-Speaker Diarization v1:** a conditional branch opened only by a real
   multi-person local scenario and labelled corpus.

## Long-Term Direction

MurmurMark should become a local transcription engine that can be trusted independently of meeting
platform, participant count and ordinary acoustic variation. Different ASR, diarization and audio
backends may evolve behind stable contracts. The invariant is the same: conserve evidence, abstain
when it is insufficient and never trade a plausible-looking transcript for an unmeasured error.
