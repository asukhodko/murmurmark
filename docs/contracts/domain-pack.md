# Domain Pack Contract

Domain packs give ASR, correction and synthesis controlled knowledge about a team, project or topic.

They are local files. They may contain sensitive names and should follow the same storage policy as transcripts.

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
