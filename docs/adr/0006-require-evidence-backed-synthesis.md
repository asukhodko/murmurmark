# ADR-0006: Require Evidence-Backed Synthesis

Status: accepted  
Date: 2026-06-22

## Context

Meeting notes are risky when they look polished but contain unsupported claims, wrong owners or hidden uncertainty.

## Decision

Synthesis must use an evidence package and every factual output must cite utterance IDs or be marked for review.

Docs/Jira/Confluence/Git updates are generated as proposals, not applied automatically.

## Consequences

Benefits:

- less hallucinated meeting memory;
- easier human review;
- uncertain speakers do not become false owners;
- supports sensitive-team workflows.

Costs:

- more complex output schema;
- notes may be less smooth until reviewed;
- synthesis adapters must implement validation.

## Alternatives

- direct transcript-to-notes prompt;
- direct docs writes by an agent;
- summarize raw audio directly.

Rejected for v1 because they weaken auditability.

