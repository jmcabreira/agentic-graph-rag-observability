# 01 — Problem statement

**Date:** 2026-11-04
**Status:** Selected

## Statement

"A liderança jurídica e de RevOps não consegue auditar qual informação embasou uma decisão
porque não há trilha ligando resposta → fatos → documentos originais, o que cria risco jurídico
quando um cliente contesta uma resposta dada em público."

## Why this one

PS-1 (trust) and PS-4 (continuous evaluation) are also relevant and will be addressed as part
of the solution, but PS-5 anchors to the highest-stakes business consequence: legal exposure
from public answers to customers (Orion, Zendesk #48233).

## Evidence

- Sam Patel's public Zendesk reply on 2026-09-29 cited the 2023 rule — traceable by Orion's legal team
- Tomás Herrera requested legal confirmation before approving (slack/cs-orion/2026-09-30.json)
- `src/observability/__init__.py` is empty — no trail exists today

## Success signal

Every answer produced by Ask Orbisa has a trace in Langfuse linking:
response → tool calls → facts retrieved → source documents (path + date)
