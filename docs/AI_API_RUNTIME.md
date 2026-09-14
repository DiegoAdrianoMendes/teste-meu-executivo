# Runtime de Modelos API-first

Status: runtime integrado aos agentes do KLM Hub e validado localmente, sem
credenciais nem chamadas reais nesta fase.

## Decisão

- O lançamento usa APIs de modelos; inferência local não é selecionada automaticamente.
- Ollama, Gemma e os providers legados permanecem no código para uso futuro explícito.
- O simulador determinístico continua restrito a testes e desenvolvimento.
- Uma chamada de modelo nunca concede permissão para enviar WhatsApp, e-mail, contrato ou outra ação externa.
- O modelo produz somente JSON tipado. Ferramentas, CRM, mensagens, aprovações e
  conectores são executados por serviços locais separados e com suas próprias
  permissões.

## Bloqueios

Uma chamada somente sai quando `KLM_MODEL_APIS_ENABLED=true` e
`KLM_ONLINE_APPROVED=true`. Os dois valores começam em `false`. Os gates de canal
continuam independentes em `KLM_EXTERNAL_ACTIONS_ENABLED`.

Antes de rede, o gateway exige:

1. `workspace_id` e nome do agente;
2. rota no catálogo versionado;
3. provider/modelo e base URL em allowlists exatas;
4. credencial configurada;
5. preço conhecido no catálogo versionado;
6. estimativa dentro dos tetos por chamada, agente e workspace/dia;
7. circuito do provider fechado.

O catálogo padrão não contém rotas, modelos ou preços. Portanto, mesmo que os
gates sejam ligados por engano, ele falha antes de qualquer request.

O launcher de aprovação `scripts/run_local.py` é deliberadamente mais restritivo:
ele força os gates de modelos, canais e AWS para `false` e remove chaves de modelos
herdadas do processo. O futuro canário com APIs usará um perfil separado e
revisável; não se transforma o launcher de aprovação em um atalho online.

O registry e `GET /api/v1/klm/runtime` expõem os estados separadamente:

- `modelApisEnabled`: o gateway pode consultar um modelo;
- `channelDeliveryEnabled`: os canais externos podem entregar algo;
- `permissions.sendExternal`: o papel do agente tem permissão de envio.

No pacote local atual, os três controles de entrega permanecem fechados. Abrir o
gateway de modelos não altera nenhum deles.

Os serviços de canal também aplicam o gate no sink, imediatamente antes de criar
um cliente de rede. Isso inclui WhatsApp oficial e QR, ZapSign, Slack, e-mail,
Google Ads, Meta Ads, download de mídia, campanhas, S3 e Cost Explorer. Assim, um
endpoint, worker, alias ou Flow não consegue contornar o controle central.

## Providers

- OpenAI: Responses API, `store=false`, Structured Outputs.
- Anthropic: Messages API, system prompt no campo próprio, `output_config.format`.
  Aceita mídia (imagem e PDF) como blocos base64 `image`/`document` na última
  mensagem de usuário.
- Gemini API: Interactions API, `store=false`, `background=false`, structured output.
  Aceita mídia (imagem e PDF) como blocos `inline_data` base64.
- Vertex Gemini: adapter preservado para autenticação Google Cloud futura.
- Maritaca (Sabiá): API OpenAI-compatível (`https://chat.maritaca.ai/api`,
  `/chat/completions`), chave via `MARITACA_API_KEY` ou BYOK por workspace.
  Modelos `sabiazinho-3` (barato, classe SIMPLE/PT) e `sabia-3`. Somente texto.

### Capabilities de mídia por adapter

| Adapter | Imagem | PDF | Áudio |
| --- | --- | --- | --- |
| Anthropic | sim | sim | não |
| Gemini API | sim | sim | não |
| Vertex Gemini | sim | não | sim |
| Maritaca | não | não | não |
| Fake (simulador) | sim | sim | sim |

O roteador por capability bloqueia, antes de credencial e de rede, qualquer
rota cujo adapter não declare suporte à mídia da requisição, e cai para a
próxima rota do catálogo que suporte.

### Roteamento SIMPLE → Maritaca (opcional)

`KLM_AI_SIMPLE_PREFER_MARITACA=true` reordena as rotas da classe `simple`
colocando as rotas do provider `maritaca` primeiro (ordem relativa das demais
preservada). O default é `false` e mantém a ordem do catálogo intocada.

### Preços Maritaca (BRL → USD para o catálogo)

O catálogo de preços (`KLM_AI_PRICE_CATALOG_JSON`) aceita somente USD. A
Maritaca publica preços em BRL por 1M de tokens; a conversão usada abaixo é a
taxa conservadora de R$ 5,00/US$ (superestima o custo em USD quando o câmbio
real está acima disso). Revalidar câmbio e tabela antes de publicar catálogo.

| Modelo | BRL entrada/1M | BRL saída/1M | USD entrada/1M | USD saída/1M |
| --- | --- | --- | --- | --- |
| `sabiazinho-3` | R$ 1,00 | R$ 3,00 | 0.20 | 0.60 |
| `sabia-3` | R$ 5,00 | R$ 10,00 | 1.00 | 2.00 |

Exemplo de entradas do catálogo:

```json
{
  "maritaca:sabiazinho-3": {"input_per_million_usd": "0.20", "output_per_million_usd": "0.60"},
  "maritaca:sabia-3": {"input_per_million_usd": "1.00", "output_per_million_usd": "2.00"}
}
```

Os adapters não fazem retry por conta própria. Falha depois do início de uma
geração interrompe a cadeia para evitar cobrança dupla. Retry/fallback exige
idempotência explícita, chave de idempotência e suporte declarado pelo adapter;
os adapters gerativos atuais declaram esse suporte como `false`.

## Custos e dados

- Preços não estão hardcoded. `KLM_AI_PRICE_CATALOG_JSON` registra versão, vigência e valores.
- Custo desconhecido bloqueia a chamada.
- O orçamento usa reserva pelo pior caso configurado e liquidação pelo usage retornado.
- Em produção o ledger exige Redis; o ledger em memória é apenas local/teste.
- A reserva e os metadados de rota/request são persistidos no `AgentRun` imediatamente depois da reserva e antes da chamada cobrável.
- Liquidação e liberação mantêm um tombstone durante a janela do ledger: repetir a mesma resolução é idempotente e uma resolução incompatível é recusada.
- Telemetria guarda ids, provider/modelo, tokens, custo, latência e versões de catálogo; não guarda prompt, resposta ou chave.
- Cache de resposta começa desligado e nunca aceita solicitações marcadas como sensíveis.
- JSON estruturado é validado novamente localmente com JSON Schema.
- A dependência `jsonschema` foi adicionada somente para essa validação local independente.

## Integração com agentes

`KlmAgentService` seleciona a classe de tarefa por papel e envia ao gateway:

- identidade do workspace e do agente para orçamento e auditoria;
- instruções de sistema por papel;
- metadados do caso e evidências recuperadas do RAG, sempre delimitadas como
  conteúdo não confiável;
- schema Draft 2020-12 fechado, com `additionalProperties=false`;
- chave de idempotência persistida antes da possível chamada cobrável.

O resultado normalizado salva provider, modelo, tokens, custo realizado, IDs de
request e versões dos catálogos no run e na trilha de auditoria. Um resultado do
modelo é persistido antes de CRM, mensagem interna ou aprovação, de modo que uma
falha local posterior não repita a chamada paga.

Falhas após o início do request usam o seguinte contrato:

- não há fallback para outro provider nem repetição automática;
- custo estimado não é contabilizado como custo realizado;
- provider, modelo, request IDs, estimativa e versões dos catálogos ficam em
  `billingReconciliation`;
- cobrança incerta recebe `reconciliation_required` e a mesma chave retorna o
  run durável, sem nova chamada.

Um administrador com `agents:manage` encerra essa pendência em
`POST /api/v1/klm/agents/runs/{run_id}/billing-reconciliation`, informando a
decisão `charged` ou `not_charged`, referência de evidência e, quando houver
cobrança, o custo confirmado. A operação:

- bloqueia o run no banco e é idempotente para a mesma decisão;
- rejeita decisão conflitante e request ID divergente;
- liquida ou libera a reserva diária no ledger;
- atualiza o custo do run e o agregado de KPIs exatamente uma vez;
- persiste somente uma referência HMAC opaca da evidência e cria evento de
  auditoria humano.

Se a reserva já expirou ou não existe no ledger, o run é conciliado com estado
`expired_or_missing` e o caso fica explicitamente rastreável; o sistema não
recria nem cobra uma reserva retroativamente.

Se a transação local falhar depois de o ledger já ter sido resolvido, a
reconciliação consulta o tombstone e conclui sem liquidar ou liberar uma segunda
vez. Uma queda entre reserva e provider deixa metadados suficientes para o
worker marcar a execução como recuperável ou `reconciliation_required`, sem
repetição automática de uma chamada possivelmente cobrada.

O RAG prioriza documentos e mensagens de origem verificável. Saídas anteriores
de agentes continuam auditadas, mas não retornam como evidência primária para
novas conclusões.

## Modos

| Cenário | Modelos | Infraestrutura | Entrega externa |
| --- | --- | --- | --- |
| testes e desenvolvimento local | simulador determinístico | SQLite/local | bloqueada |
| validação local com APIs aprovada | APIs de modelos | local | bloqueada |
| produção futura | APIs de modelos | PostgreSQL/Redis/AWS aprovados | gate próprio |

`KLM_LOCAL_MODELS_ENABLED=false` permanece como padrão de lançamento. Providers
locais legados não entram no roteamento sem uma decisão futura explícita.

## Ativação futura

Somente depois da aprovação do pacote local:

1. cadastrar chaves por secret manager, sem colocá-las em banco, frontend ou Git;
2. registrar allowlist exata de modelos e base URLs;
3. publicar catálogos versionados de rotas e preços;
4. configurar orçamento por workspace e teto por agente;
5. usar Redis para reserva de orçamento em produção;
6. executar um canário com canais externos ainda bloqueados;
7. liberar cada conector em uma aprovação separada.

## Referências oficiais

- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Claude Messages API](https://platform.claude.com/docs/en/api/messages/create)
- [Claude token counting](https://platform.claude.com/docs/en/api/messages/count_tokens)
- [Gemini Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview)
- [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output)
- [Maritaca API (OpenAI-compatível)](https://docs.maritaca.ai/pt/introducao)
