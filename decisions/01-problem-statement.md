# 01 — Problem statement

**Date:** 2026-11-04
**Status:** Final

## Statement

"A liderança jurídica e de RevOps não consegue auditar qual informação embasou uma decisão
porque não há trilha ligando resposta → fatos → documentos originais, o que cria risco jurídico
quando um cliente contesta uma resposta dada em público."

## Primary user

**Tomás Herrera, Head of RevOps** — aprova reembolsos, recebe escaladas e precisou de
confirmação jurídica antes de agir no caso Orion (slack/cs-orion/2026-09-30.json).

## Scope

All 22 ground-truth questions across all 7 categories:
`anchor`, `temporal`, `conflict`, `multi_source`, `alias`, `false_premise`, `lookup`

## Success criteria

| Criterion | Target | How measured |
|---|---|---|
| Every answer has a complete trail (fact + source + date) | 100% of executions | Langfuse trace has spans with `source_path` and `valid_at` |
| Correctness on 22 benchmark questions | ≥ 90% (≥ 20 / 22) | LLM judge compares agent answer with `expected_answer` in `questions.jsonl` |
| Latency per question | ≤ 120s (p95) | `duration_ms` recorded in Langfuse trace |
| Cost per question | ≤ USD 0.25 | `cost_usd` recorded in Langfuse trace |
| System works without Langfuse (graceful degradation) | Zero additional errors | Shut down Langfuse, run Q01 — agent answers normally |

## Constraints

1. Self-hosted — Langfuse runs on our machines, no data leaves
2. Agent keeps working if Langfuse is down
3. Do not modify Helix — instrument only, do not rewrite
4. Do not upgrade dependencies

## Out of scope

1. Real-time integration with the 7 source systems — exports only
2. Custom UI for Tomás — he uses Langfuse UI directly
3. Automated alerts via email or Slack — covered in prompt 12
4. Evaluation of questions outside the 22-question benchmark — online eval in prompt 11
5. Production deploy (GCP / on-premise) — local only for now

## Open risks

- LLM judge cost: running all 22 questions with a judge adds ~USD 0.30 per full run
- `invalid_at = 2026-09-08` on Orion clause 7.3 may be a data error — affects temporal questions
