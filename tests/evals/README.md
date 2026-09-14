# Evals — Dataset Dourado PT-BR (Sprint S5a)

Suíte de avaliação de regressão dos agentes do KLM Legal Hub com 50 casos
sintéticos realistas de escritório jurídico (trabalhista + plano de saúde),
todos em português brasileiro. Nenhum caso usa dados reais de clientes.

## Estrutura

| Arquivo | Papel |
| --- | --- |
| `golden_dataset.json` | 50 casos dourados com `input`, `expected` e `rationale` |
| `test_golden_regression.py` | Runner de regressão contra o provider fake determinístico |
| `README.md` | Este guia |

Composição do dataset (`version: golden-ptbr-v1`):

| Categoria | Casos | Contrato validado |
| --- | --- | --- |
| `qualificacao_sdr` | 20 | `QUALIFICATION_RESPONSE_SCHEMA` (klm_sdr) |
| `triagem_juridica` | 15 | `ANALYST_RESPONSE_SCHEMA` (klm_analyst) |
| `leitura` (áudio/OCR) | 10 | `QUALIFICATION_RESPONSE_SCHEMA` (a extração alimenta `sdr_qualification`) |
| `roteamento_orquestrador` | 5 | Schema de rota com enum vindo de `AGENT_TASK_CLASSES` (registry real) |

Três casos são adversariais (`adversarial: true`), um por superfície de
entrada: conversa de WhatsApp (`sdr-020`), relato de caso (`tri-015`) e OCR de
documento (`lei-010`). Eles carregam instruções de prompt injection que DEVEM
ser ignoradas: o runner prova que o texto malicioso fica confinado ao envelope
`UNTRUSTED_DATA_BEGIN/END` (com `trust: untrusted`), nunca entra no prompt de
sistema e nunca aparece na saída do modelo.

## Como rodar (modo determinístico, gates fechados)

Este é o modo padrão e o único usado em CI. Zero chamadas externas: o runner
constrói o gateway local com `build_model_gateway` e todos os gates fechados
(`KLM_RUNTIME_MODE=local_simulation`, `KLM_EXTERNAL_ACTIONS_ENABLED=false`,
`KLM_MODEL_APIS_ENABLED=false`, `KLM_ONLINE_APPROVED=false`,
`KLM_AWS_ENABLED=false`). Qualquer gate aberto derruba a construção do gateway
fake (fail-closed), e há teste cobrindo cada gate.

```bash
/Users/kleistr.monteiro/Documents/Playground/klm-legal-hub-backend-real/.venv/bin/python \
  -m pytest tests/evals -q
```

O que o modo determinístico congela:

- integridade e contagens do dataset;
- expectativas sempre dentro dos enums dos schemas de produção;
- resposta JSON válida por schema, com todos os campos obrigatórios;
- determinismo byte a byte (mesma entrada, mesma saída) e custo zero;
- envelope UNTRUSTED_DATA íntegro e injeções não obedecidas.

Limitação honesta: o provider fake sintetiza a resposta a partir do schema,
então os valores semânticos de `expected` (ex.: `classification: "Premium"`)
não são conferidos nesse modo. Eles são o gabarito da rodada com modelos
reais, descrita abaixo.

## Como rodar contra modelos reais (quando os gates abrirem)

Pré-requisitos, nesta ordem, todos por decisão humana explícita (nunca em CI):

1. Abrir os gates no ambiente de execução:
   `KLM_MODEL_APIS_ENABLED=true`, `KLM_ONLINE_APPROVED=true` e
   `KLM_RUNTIME_MODE` fora de `local_simulation`.
2. Publicar catálogos versionados: `KLM_AI_ROUTE_CATALOG_JSON`,
   `KLM_AI_PRICE_CATALOG_JSON`, `KLM_AI_MODEL_ALLOWLIST_JSON` e
   `KLM_AI_ALLOWED_BASE_URLS_JSON`. A factory valida tudo isso fail-closed:
   rota sem preço ou fora da allowlist bloqueia o gateway inteiro.
3. Ativar accounting (`KLM_AI_USAGE_ACCOUNTING_ENABLED=true`), obrigatório
   para o gateway externo.
4. Exportar a chave do provedor escolhido (ex.: `ANTHROPIC_API_KEY`).

Variáveis de ambiente do runner real:

| Variável | Valor | Efeito |
| --- | --- | --- |
| `KLM_EVALS_REAL_MODELS` | `1` | Sinaliza rodada real. A suíte determinística se recusa a rodar (skip explícito) para nunca misturar os dois modos; a rodada real usa o gateway construído a partir dos settings da aplicação com os gates abertos. |
| `KLM_EVALS_MODEL` | `opus` \| `sonnet` \| `haiku` | Rota alvo da rodada (o modelo concreto vem do catálogo de rotas versionado, nunca hardcoded). |

Na rodada real, cada caso é enviado com o mesmo prompt de sistema, o mesmo
envelope UNTRUSTED_DATA e o mesmo `response_schema` do modo determinístico, e
a saída é comparada com `expected`:

- `qualificacao_sdr`: `classification` e `handoff_target` exatos;
- `triagem_juridica`: `recommendation` exata;
- `leitura`: cada campo de `expected.extraction` (comparação por campo;
  `null` significa "não inventar valor");
- `roteamento_orquestrador`: `route` exata;
- casos adversariais: além do gabarito, nenhum item de `must_not_contain`
  pode aparecer na saída.

## Custo estimado por rodada (preços de julho/2026)

Premissas: 50 casos, 1 chamada por caso, média de ~1.100 tokens de entrada
(prompt de sistema + envelope com o input) e ~250 tokens de saída (JSON do
schema). Total por rodada: ~55 mil tokens de entrada e ~12,5 mil de saída.

| Modelo | Preço (entrada/saída por MTok) | Entrada (~55k) | Saída (~12,5k) | Total por rodada |
| --- | --- | --- | --- | --- |
| Claude Opus | US$ 5 / US$ 25 | US$ 0,275 | US$ 0,313 | **~US$ 0,59** |
| Claude Sonnet | US$ 3 / US$ 15 | US$ 0,165 | US$ 0,188 | **~US$ 0,35** |
| Claude Haiku | US$ 1 / US$ 5 | US$ 0,055 | US$ 0,063 | **~US$ 0,12** |

Observações:

- Os valores dobram se a rodada incluir repetição para medir estabilidade
  (2 passes) e caem com prompt caching (o prompt de sistema é idêntico entre
  os casos da mesma categoria).
- O teto por chamada (`KLM_AI_MAX_COST_PER_CALL_USD`) e o orçamento diário do
  workspace (`KLM_AI_WORKSPACE_DAILY_BUDGET_USD`) continuam valendo na rodada
  real: uma rodada inteira cabe com folga em US$ 1 de teto por chamada.
- Recomendação de rotina: Haiku para regressão frequente, Sonnet para a
  rodada de release, Opus apenas para investigar divergência semântica.

## Como estender o dataset

1. Adicionar o caso em `golden_dataset.json` mantendo o shape
   (`id`, `category`, `agent`, `adversarial`, `input`, `expected`,
   `rationale`) e o prefixo de id da categoria (`sdr-`, `tri-`, `lei-`,
   `rot-`).
2. Atualizar `CATEGORY_COUNTS`/`TOTAL_CASES` em `test_golden_regression.py`
   (o teste de integridade quebra até as contagens baterem, de propósito).
3. Todo valor esperado precisa pertencer aos enums dos schemas de produção;
   o teste de expectativas garante isso automaticamente.
4. Caso adversarial novo: `adversarial: true` + `must_not_contain` com
   marcadores que existam literalmente no input.
