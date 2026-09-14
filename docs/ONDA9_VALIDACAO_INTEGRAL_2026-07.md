# Onda 9 — Validação Integral (§19-20)

Data: 2026-07-27
Escopo: consolidação do veredito dos gates auditados na validação integral da Onda 9 (§19 IA fail-closed, §20 zero protocolo acessível a agentes / zero vazamento entre workspaces). Sem alteração de código — auditoria de leitura + execução de testes existentes.

## Tabela de gates

| Gate | Veredito | Evidência | Buraco |
|---|---|---|---|
| §20 "zero protocolo acessível a agentes" | **PROVADO** | `agent_tool_executors.py:91-98` só 6 tools read-only; `klm_agent_service.py:2749-52` intersecta `agent.tools ∩ registry`; `mcp/tools.py` só 9 tools fixas; `protocol_folder_service.py:764-790` `register_protocol_receipt` é DB-only pós-fato com `user_id` humano; build só empacota; endpoint só 4 rotas. Teste `test_no_agent_route_exists_to_execute_protocol` PASSOU agora. Sem cliente PJe/eSAJ de escrita. | Nenhum. Ressalva cosmética: docstring de `protocol_folder_service.py:13-14` cita caminho de teste inexistente (`tests/api/test_protocol_folder.py`); teste real está em `tests/services/test_protocol_folder_service.py`. |
| `protocol_folder_service.py` `build_protocol_folder`: if/elif/else state gate | **PROVADO** | `protocolavel` só é atribuído 1x (`protocol_folder_service.py:643`), só se `pending_items` vazio (assinatura/procuração/custas/hash também bloqueiam). Checks: aprovado, sem placeholder (checklist + regex), citação "verificada" (provider fail-closed), zero achado crítico, hash vs build anterior. `pytest -k protocol_folder/citation/legal_editor/review` = 110 passed, cobrindo cada gate. | Nenhum no gate testado. `hash_alterado` compara ao build anterior (não "desde aprovação" per se; imutabilidade vem do append-only, não testada aqui). Citação só fica "verificada" com provider real; hoje fail-closed por padrão (documentado, não é buraco). |
| §20 zero vazamento entre workspaces — tabelas NOVAS do plano | **PROVADO** | pytest: 10/10 passed (verifica `Base.metadata` real, `workspace_tables == protected \| exempt`). AST: 205 tabelas com `workspace_id`, 0 fora de `rls.py`. As 20 tabelas do plano (`legal_demands`, `legal_exceptions`, `agent_autonomy_policies`, `migration_*`, `case_*`, `citation_verifications`, `document_asset*`, `protocol_folders`, `legal_batch*`, `knowledge_feedback`) estão em `RLS_TENANT_TABLES` com migrações `ENABLE`/`FORCE RLS` + `POLICY`. | Verificação em código + SQLite; não rodou contra Postgres real (exige banco vivo, fora do escopo read-only) — aplicação efetiva das migrações em produção não foi confirmada. |
| §19/§13 IA: gate fechado nunca fabrica (citação/agente/contrato) | **PROVADO** | `pytest -k "citation or autonomy or agent_governance or contract_billing"` (excl. postgresql): 97 passed, 0 failed. `citation_verification_service.py:264-317` força `nao_verificada` com gate fechado. `klm_agent_service.py:4506-4509` declara `SIMULATOR_RUNTIME` quando gate fechado. `contract_billing_service.py`: extração sempre draft; materialização só pós-aprovação humana. | Nenhum no escopo auditado. Ressalva: testes usam fakes injetados; `ModelAPIDisabledError` do gateway real confirmado por leitura de código (`app/ai/policy.py:23-30`), não por teste fim-a-fim dentro deste `-k`. |

## Bloqueadores de lançamento

Nenhum BURACO real foi encontrado nos 4 gates auditados — todos os 4 vereditos são PROVADO sem buraco funcional. Não há bloqueador de lançamento decorrente desta validação.

Ressalvas registradas (não bloqueiam, mas ficam pendentes de follow-up):

1. **Docstring desatualizada** em `protocol_folder_service.py:13-14` — referencia `tests/api/test_protocol_folder.py`, que não existe; o teste real está em `tests/services/test_protocol_folder_service.py`. Correção trivial de comentário, sem risco.
2. **RLS não validado contra Postgres real** — a prova das 20 tabelas novas foi feita em código + SQLite (AST + metadata). A aplicação efetiva das migrações `ENABLE`/`FORCE ROW LEVEL SECURITY` + `POLICY` em um banco Postgres vivo não foi confirmada nesta rodada (fora do escopo read-only). Recomenda-se rodar a suíte de RLS contra um Postgres de staging antes do go-live que dependa dessas tabelas.
3. **Cobertura fim-a-fim do gateway de IA real** — o comportamento fail-closed do `ModelAPIDisabledError` foi confirmado por leitura de `app/ai/policy.py:23-30`, não por um teste de integração que dispare o gateway real dentro do filtro `-k` usado. Os 97 testes que passaram usam fakes/mocks injetados. Recomenda-se um teste de integração leve que force o gate fechado contra o gateway real (mesmo que mockando só a chamada de rede).

Nenhum desses três itens é um "buraco" no sentido do critério da Onda 9 (nenhum permite fabricação de dado, protocolo por agente, ou vazamento entre workspaces); são lacunas de cobertura/documentação, não falhas de gate.

## Critérios §20 atendidos vs pendentes

**Atendidos (provados nesta rodada):**
- Zero rota/tool que permita a um agente executar protocolo (peticionamento) — confirmado por inspeção de todas as superfícies de execução de agente (tool executors, registry intersection, MCP tools, endpoints) e por teste dedicado passando.
- `build_protocol_folder` é gate de estado único, atribuído uma vez, condicionado a todos os pré-requisitos (aprovação humana, ausência de placeholder, citação verificada, zero achado crítico, hash de integridade, itens pendentes de assinatura/procuração/custas) — coberto por 110 testes.
- Isolamento por workspace nas 20 tabelas novas do plano — RLS declarado e migrado no código-fonte, verificado por AST + testes de metadata (100% das 205 tabelas do schema têm `workspace_id` e estão dentro de `rls.py`).

**Pendentes (fora do escopo desta rodada, não classificados como buraco):**
- Confirmação da aplicação efetiva das migrações RLS em um Postgres real de staging/produção (item de ressalva 2 acima).
- Teste de integração fim-a-fim do gateway de IA real sob gate fechado, complementando os fakes usados nos 97 testes (item de ressalva 3 acima).
- Correção da docstring desatualizada em `protocol_folder_service.py` (item de ressalva 1 acima).

## Veredito final honesto

Os 4 gates auditados na Onda 9 (§19-20) foram **PROVADOS**, sem buraco funcional. Não há evidência de que um agente possa protocolar, de que o sistema fabrique citação/conteúdo com o gate de IA fechado, ou de vazamento de dados entre workspaces nas tabelas novas do plano. As três ressalvas listadas são lacunas de cobertura de teste em ambiente real (Postgres vivo, gateway de IA real) e uma docstring desatualizada — não invalidam o veredito PROVADO, mas devem ser fechadas antes de depender publicamente dessas garantias em produção.

**O upsell jurídico está pronto para anúncio?** Do ponto de vista dos gates §19-20 auditados aqui: sim, condicionalmente. A engenharia de segurança e integridade (zero protocolo por agente, zero fabricação sob gate fechado, isolamento de workspace) está provada em código e testes. Antes de anunciar publicamente o upsell jurídico como pronto para uso em produção, recomenda-se fechar a ressalva 2 (validação RLS contra Postgres real) por ser a única com risco de dado cruzado entre clientes se a migração não tiver sido aplicada corretamente no ambiente vivo — as ressalvas 1 e 3 não bloqueiam o anúncio.

**Lembrete (emenda 3):** o CORE do produto já pode lançar independentemente deste veredito — esta validação cobre especificamente o upsell jurídico (protocolo, IA de citação/agente/contrato, tabelas jurídicas novas) e não é pré-requisito para o lançamento do CORE.
