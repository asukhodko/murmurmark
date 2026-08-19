# Domain Pack Contract

Domain packs give ASR, correction and synthesis controlled knowledge about a team, project or topic.

They are local files. They may contain sensitive names and should follow the same storage policy as transcripts.

## Implementation Status

Domain packs are currently a local contract and an operator-maintained knowledge source. The
production whisper.cpp bridge does not consume `glossary.yaml`, and `murmurmark.config.json` keeps
`prompt_file: null` by default. A prompt can be supplied manually for diagnostics, but the normal
`--max-context 0` path does not use it effectively.

A bounded real-audio A/B established two limits: a broad static backend glossary did not improve the
target interval, while a compact topic-specific context materially improved the intended terms when
ASR context was enabled. That result does not qualify either prompt for production. The planned
Session-Scoped Lexical Context compiler must select a small relevant subset, freeze its provenance
and pass human-reviewed multi-session no-regression gates before it can affect selected text.

## Layout

```text
domain-packs/
  example-domain/
    domain.md
    glossary.yaml
    participants.yaml
    projects.yaml
    correction_rules.yaml
    prompt_templates/
      vibevoice.txt
      qwen3_asr.txt
      correction_llm.txt
```

## `domain.md`

Human-readable domain briefing:

```text
Команда занимается backend/platform engineering.
Типичные темы: reliability, incident review, deployment pipeline,
latency, SLO/SLA/SLI, Kubernetes, PostgreSQL.

Речь обычно русская, но названия технологий, аббревиатуры и сервисы часто
произносятся или пишутся на английском.
Названия технологий не переводить.
Аббревиатуры сохранять латиницей.
```

## `glossary.yaml`

```yaml
schema: murmurmark.glossary/v1
domain: example-domain
language_profile:
  - ru
  - en

terms:
  - canonical: SLO
    type: abbreviation
    spoken_forms:
      - эс эл оу
      - сло
      - s l o
    aliases:
      - Service Level Objective
    common_misrecognitions:
      - slow
      - слоу
      - сло
    correction_policy: prefer_canonical
    examples:
      - SLO по latency
      - нарушили SLO
```

## `participants.yaml`

```yaml
schema: murmurmark.participants/v1

participants:
  - id: me
    display_name: Local User
    role: meeting participant
    track_hint: mic

  - id: teammate_a
    display_name: Teammate A
    aliases:
      - teammate
    role: backend engineer
    voiceprint:
      status: not_enrolled
```

## Compiler Rules

The context compiler should not pass the full domain pack to every model.

It should produce:

- short ASR context;
- hotwords;
- correction context;
- synthesis context;
- redacted variants when policy requires it.

The compiler must prefer precision over volume. Too much domain context can create false corrections.
Unknown or stale meeting context must fail open to the current prompt-free ASR path.
