# ADR-009: Call NVIDIA directly for scheduled Nemotron paper cycles

## Status

Accepted

## Date

2026-07-24

## Context

The paper LLM runtime is pinned to `nvidia/nemotron-3-ultra-550b-a55b`.
Calling that model through OpenRouter made the cycle dependent on intermediary
credits and provider parameter routing, despite the NVIDIA credential already
being configured in the Mephul Agent Client vault.

The user requires a usable, observable paper bot. The native strategy account
and the isolated LLM paper accounts must remain separate; this decision does
not authorize real trading.

## Decision

The launchd-scheduled `paper_autonomous` cycle calls NVIDIA's OpenAI-compatible
`/v1/chat/completions` endpoint directly. It receives the API key only from
the macOS Keychain service `0rum-nvidia`, in memory for one cycle. The key is
not stored in repository configuration, journals, status files or dashboard
payloads.

The direct adapter requests JSON mode with model thinking disabled and supplies
the exact JSON contract in the system instruction, then validates the returned
object against the existing local schemas. This keeps the paper journal limited
to auditable decisions rather than private reasoning.
It uses the explicitly pinned Ultra model and does not fall back to another
model or provider. OpenRouter remains a supported explicit CLI provider for
controlled experiments.

## Alternatives considered

### Keep OpenRouter for the scheduled cycle

Rejected. Its available credits and strict route parameter support previously
prevented the scheduled paper cycle from producing a decision.

### Copy the key into a YAML or launchd plist

Rejected. Those files are easy to inspect, commit or log. The Keychain keeps
the credential outside the repository and the scheduled process reads it only
when needed.

### Use free-form model text

Rejected. JSON mode plus local schema validation keeps invalid decisions from
reaching even the isolated paper simulator.

## Consequences

- Scheduled paper cycles are no longer constrained by OpenRouter credits.
- NVIDIA availability and quota are visible as accurately labelled runtime
  errors in the paper dashboard.
- The existing decision, validation, fill and learning journals are unchanged.
- A direct NVIDIA request still never reaches a real broker.
