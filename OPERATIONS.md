# Guia de Operação — Helix + Langfuse Observability

## 1. Subir o stack

```bash
# Langfuse (banco, cache, UI)
docker compose -f infra/docker-compose.yml up -d

# Verificar se tudo subiu
docker ps --format "table {{.Names}}\t{{.Status}}"
```

> **Atenção:** após um cold start, Neo4j e ClickHouse levam ~2 min para estabilizar.
> Espere o uso de CPU baixar antes de rodar experimentos:
> ```bash
> docker stats --no-stream --format "{{.Name}}\t{{.CPUPerc}}" | grep -E "neo4j|clickhouse"
> ```
> Pronto quando ambos estiverem abaixo de 10%.

---

## 2. Verificar conectividade

```bash
# Neo4j (banco de grafos do Helix)
nc -z 127.0.0.1 7687 && echo "Neo4j UP" || echo "Neo4j DOWN"

# Helix MCP server (ferramentas do agente)
nc -z 127.0.0.1 8765 && echo "MCP UP"   || echo "MCP DOWN"

# Langfuse UI
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/public/health
# deve retornar 200
```

Se Neo4j ou MCP estiverem DOWN:
```bash
docker start orbisa-neo4j-1 orbisa-helix-mcp-1
```

---

## 3. Testar o agente manualmente

```bash
uv run python -c "
import asyncio
from clients.agent.runner import ask

async def main():
    run = await ask(question='Quem aprova reembolsos na Orbisa?', as_of='2026-10-15')
    print('Resposta:', run.answer[:300])
    print('Fontes:', run.source_paths)
    print('Custo:', run.cost_usd)

asyncio.run(main())
"
```

Se a resposta for *"Helix tools aren't available"*, o MCP não está acessível. Veja seção 2.

---

## 4. Carregar o dataset (primeira vez ou para resetar)

```bash
uv run python -m observability.datasets.load_truth
```

Cria/atualiza o dataset `orbisa-truth` com 22 perguntas no Langfuse.
No UI: **Datasets → orbisa-truth**.

---

## 5. Calibrar o juiz LLM (antes de confiar nos experimentos)

```bash
# Rápido: 3 questões (~30s, ~$0.05)
uv run python -m observability.evaluators.calibrate --ids Q01,Q02,Q03

# Completo: todas as 22 questões (~5 min, ~$0.50)
uv run python -m observability.evaluators.calibrate
```

Esperado: `✓ Judge calibration passed.` com agreement ≥ 80%.
Se falhar, revisar o system prompt em `src/observability/evaluators/judge.py`.

---

## 6. Rodar um experimento

```bash
# Baseline completo (22 questões, ~15-20 min, ~$2.15)
uv run python -m observability.experiments.run \
  --name prod-baseline-full \
  --prompt-label production \
  --max-concurrency 2

# Subconjunto rápido para validar (3 questões, ~3 min, ~$0.30)
uv run python -m observability.experiments.run \
  --name prod-smoke \
  --prompt-label production \
  --ids Q01,Q02,Q03 \
  --max-concurrency 1

# Com versão alternativa do prompt
uv run python -m observability.experiments.run \
  --name staging-v1 \
  --prompt-label staging \
  --max-concurrency 2
```

**Flags disponíveis:**

| Flag | Padrão | Descrição |
|---|---|---|
| `--name` | `production-<timestamp>` | Nome do run no Langfuse |
| `--prompt-label` | `production` | Label do prompt no Langfuse |
| `--ids` | todas | Questões a incluir (ex: `Q01,Q05,Q13`) |
| `--max-concurrency` | `3` | Perguntas em paralelo |
| `--model` | do `.env` | Override do modelo Claude |

---

## 7. Ver resultados no Langfuse UI

Acesse `http://localhost:3000`

| O que ver | Onde ir |
|---|---|
| Resultado dos experimentos | **Experiments** (menu lateral) |
| Perguntas individuais com notas | **Experiments → <nome do run>** |
| Traces de cada resposta do agente | **Tracing** → filtrar por tag `experiment` |
| Dataset com gabarito | **Datasets → orbisa-truth** |
| Versões do prompt do sistema | **Prompts → ask-orbisa-system-label** |

---

## 8. Comparar dois prompts

1. No Langfuse UI, vá em **Prompts → ask-orbisa-system-label**
2. Duplique a versão `production` e edite → salve com label `staging`
3. Rode o experimento staging:
   ```bash
   uv run python -m observability.experiments.run \
     --name staging-v1 \
     --prompt-label staging \
     --max-concurrency 2
   ```
4. Em **Experiments**, selecione os dois runs e compare lado a lado

**Métricas para decidir qual prompt é melhor:**

| Métrica | Significado |
|---|---|
| `mean_correctness` | % de respostas corretas (principal) |
| `mean_source_recall` | % de fontes esperadas citadas |
| `correctness_<categoria>` | Acurácia por tipo de pergunta |
| `mean_cost_usd` | Custo médio por pergunta |
| `p95_latency_ms` | Latência no percentil 95 |

> O agente é não-determinístico. Rode o mesmo prompt duas vezes e espere ±5–10% de variação antes de concluir que uma versão é melhor.

---

## 9. Baseline de referência (prod-baseline-full)

Resultado registrado em 2026-11-05 com `--prompt-label production`:

| Métrica | Valor |
|---|---|
| Acurácia geral | **100%** (22/22) |
| Source recall | **87.1%** |
| Custo / pergunta | $0.097 |
| Latência p95 | 87s |
| Categorias | alias, anchor, conflict, false_premise, lookup, multi_source, temporal — todas 100% |

> Source recall máximo alcançável sem ingerir novas fontes: **~83%**
> (5 questões referenciam zendesk, gong e hubspot — não disponíveis no Helix)

---

## 10. Problemas comuns

### Agente responde "Helix tools aren't available"
```bash
docker start orbisa-neo4j-1 orbisa-helix-mcp-1
# aguardar ~15s e testar novamente
```

### `uv run` não carrega variáveis de ambiente
O `.env` é carregado automaticamente via `python-dotenv` no módulo `observability.tracing`.
Se ainda falhar, carregue manualmente:
```bash
set -a && source .env && set +a
```

### Experiment trava com `ReadTimeout`
O Langfuse pode estar lento logo após subir os containers. Aguarde Neo4j e ClickHouse estabilizarem (ver seção 1).

### Dataset run não aparece no UI (Experiments vazio)
O PostgreSQL pode ter estado em recovery mode durante o experimento.
Verifique e reexecute:
```bash
docker exec orbisa-postgres-1 psql -U postgres -c "SELECT pg_is_in_recovery();"
# deve retornar "f" (false) antes de rodar o experimento
```
