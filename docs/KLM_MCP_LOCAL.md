# KLM Hub MCP local e tenant-safe

Status: implementado e testado localmente. Nenhuma API externa, canal real ou AWS e acionado.

## Objetivo

O pacote `app/mcp/` conecta agentes locais ao CRM juridico do KLM Hub via MCP
`stdio`. O workspace e o usuario atuante sao escolhidos explicitamente pelo
operador ao emitir um grant. Eles nao aparecem nos schemas das ferramentas e,
portanto, nao podem ser trocados pelo modelo durante uma chamada.

## Arquitetura

```text
Cliente MCP / agente
        |
        | JSON-RPC UTF-8, uma mensagem por linha
        v
scripts/run_klm_mcp.sh
        |
        v
app/mcp/protocol.py -> app/mcp/tools.py
        |
        | grant assinado + usuario/workspace/membership fixos
        | revalidacao de status, RBAC e permission overrides por chamada
        | set_workspace_context antes de acessar dados do tenant
        v
KLM Hub SQLAlchemy -> PostgreSQL RLS ou SQLite local
```

O servidor anuncia MCP `2025-11-25` e aceita as versoes compativeis
`2025-06-18`, `2025-03-26` e `2024-11-05`. O framing segue a especificacao
oficial de `stdio`: JSON-RPC em UTF-8, delimitado por newline, e nenhum log em
`stdout`.

## Adaptacao do BottleCRM

A referencia auditada esta preservada em `.vendor/upstreams/bottlecrm`, versao
`0.46`, sob licenca MIT. Foram incorporadas as decisoes maduras de manter o MCP
como processo separado, expor poucas ferramentas compactas, limitar paginacao,
usar annotations, validar entradas no servidor e exigir confirmacao para
escritas.

O BottleCRM usa FastMCP e PAT sobre REST. Neste escopo, criar PAT, endpoints e
dependencias globais estava expressamente fora dos arquivos autorizados. Por
isso, o KLM usa um grant local assinado e acesso ORM sob a mesma camada de RLS e
RBAC do projeto. Cada chamada revalida a identidade; suspender o membership,
desativar o usuario/workspace ou remover uma permissao tem efeito imediato.

Nenhum arquivo upstream foi alterado e nenhuma funcionalidade upstream foi
removida. O aviso de referencia e licenca esta em
`app/mcp/THIRD_PARTY_NOTICES.md`.

## Garantias de seguranca

1. `workspace_id`, `user_id` e `membership_id` existem apenas no grant emitido pelo operador.
2. Grant e chave devem ser arquivos regulares, sem symlink, do usuario atual e modo `0600`.
3. O grant tem HMAC-SHA256, prazo, UUID proprio e fingerprint do banco configurado.
4. Toda chamada abre uma sessao, fixa o contexto do workspace e revalida membership, usuario, workspace, papel e overrides de permissao.
5. Dados de identidade e saude ficam mascarados sem `crm.sensitive.read` e `lead_data.view` simultaneos.
6. Toda escrita exige `confirm=true` literal e `idempotency_key` valida.
7. Escritas usam lock local por workspace, lock transacional da linha do workspace e auditoria como recibo atomico.
8. Repetir a mesma chave devolve o resultado persistido; reutiliza-la em outra operacao retorna conflito.
9. Assignees informados pelo modelo sao aceitos apenas com membership ativo no mesmo workspace.
10. Handoff e aprovacao apenas criam itens locais pendentes. Nada e autoaprovado ou enviado externamente.

## Capacidades do grant

| Capacidade | Permissoes vivas exigidas | Efeito |
| --- | --- | --- |
| `crm.read` | `kanban.view` | Lista e consulta clientes/casos |
| `crm.sensitive.read` | `kanban.view`, `lead_data.view` | Revela identidade, contato e campos juridicos sensiveis |
| `crm.write` | `kanban.edit` | Nota e tarefa; qualificacao exige tambem `lead_data.edit` |
| `handoff.request` | `kanban.edit`, `aprovacoes.view` | Cria pedido local de handoff |
| `approval.request` | `kanban.edit`, `aprovacoes.view` | Cria item pendente de aprovacao |

As ferramentas de escrita nem sequer aparecem em `tools/list` quando a
capacidade correspondente nao foi emitida.

## Ferramentas

| Ferramenta | Tipo | Observacao |
| --- | --- | --- |
| `klm.crm.list_clients` | leitura | Maximo 50; busca nominal exige acesso sensivel |
| `klm.crm.get_client` | leitura | IDOR protegido por workspace |
| `klm.crm.list_matters` | leitura | Filtros compactos e paginados |
| `klm.crm.get_matter` | leitura | Dossie compacto; nao despeja documentos ou RAG |
| `klm.crm.qualify_and_tag_matter` | escrita | Allowlist de score, classe, prioridade, urgencia, tags e proxima acao |
| `klm.crm.create_case_note` | escrita | Nota interna na timeline |
| `klm.crm.create_task` | escrita | Tarefa local com assignee validado |
| `klm.crm.request_human_handoff` | escrita | Pedido pendente na fila humana |
| `klm.crm.request_approval` | escrita | Solicitacao pendente; nunca autoaprova |

Nao ha ferramenta para apagar cliente/caso, mover card, enviar WhatsApp/email,
assinar documento, cobrar, executar flow ou acessar documentos RAG em massa.

## Emitir um grant

Primeiro identifique explicitamente o workspace e o usuario locais:

```bash
sqlite3 -header -column .local/klm-hub.db \
  "select w.id workspace_id, w.name workspace, u.id user_id, u.email, m.role from workspace_memberships m join workspaces w on w.id=m.workspace_id join users u on u.id=m.user_id where m.status='active';"
```

Valide sem gravar credenciais:

```bash
.venv/bin/python scripts/configure_klm_mcp.py \
  --workspace-id "$WORKSPACE_ID" \
  --user-email "$USER_EMAIL" \
  --allow-sensitive \
  --allow-write \
  --allow-handoff \
  --allow-approval \
  --dry-run
```

Emita um grant de 24 horas:

```bash
.venv/bin/python scripts/configure_klm_mcp.py \
  --workspace-id "$WORKSPACE_ID" \
  --user-email "$USER_EMAIL" \
  --allow-sensitive \
  --allow-write \
  --allow-handoff \
  --allow-approval
```

O padrao grava somente em `.local/klm-mcp/`, que ja esta ignorado pelo Git:

- `.local/klm-mcp/local-owner.grant.json`
- `.local/klm-mcp/signing.key`
- `.local/klm-mcp/state/locks/`

Use `--replace` para rotacionar conscientemente um grant existente. O prazo
maximo aceito pelo configurador local e 720 horas. Para revogar imediatamente,
inclusive em um processo MCP que ja esteja aberto:

```bash
.venv/bin/python scripts/revoke_klm_mcp.py \
  --grant-file "$KLM_MCP_GRANT_FILE" \
  --key-file "$KLM_MCP_KEY_FILE"
```

O runtime verifica o marcador de revogacao em toda chamada. Suspender o
membership ou remover suas permissoes tambem bloqueia a proxima ferramenta.

## Configurar um cliente MCP

Exemplo generico compativel com clientes que aceitam `mcpServers`:

```json
{
  "mcpServers": {
    "klm-hub": {
      "command": "/Users/kleistr.monteiro/Documents/Playground/klm-legal-hub-backend-real/scripts/run_klm_mcp.sh",
      "env": {
        "KLM_MCP_GRANT_FILE": "/Users/kleistr.monteiro/Documents/Playground/klm-legal-hub-backend-real/.local/klm-mcp/local-owner.grant.json",
        "KLM_MCP_KEY_FILE": "/Users/kleistr.monteiro/Documents/Playground/klm-legal-hub-backend-real/.local/klm-mcp/signing.key"
      }
    }
  }
}
```

O launcher fixa `DATABASE_URL` no SQLite local e mantem
`KLM_EXTERNAL_ACTIONS_ENABLED=false`, `KLM_AWS_ENABLED=false` e
`KLM_ONLINE_APPROVED=false`. Um banco alternativo exige
`KLM_MCP_DATABASE_URL`; grants nao podem ser reutilizados em outro banco por
causa da fingerprint.

## Verificacao

```bash
.venv/bin/ruff check app/mcp scripts/configure_klm_mcp.py tests/mcp
.venv/bin/python -m py_compile app/mcp/*.py scripts/configure_klm_mcp.py
zsh -n scripts/run_klm_mcp.sh
.venv/bin/pytest -q tests/mcp
```

A suite dedicada cobre assinatura e permissoes dos arquivos, expiracao,
fingerprint, symlink, negociacao MCP, framing `stdio`, schemas sem identidade,
ocultacao de dados, isolamento entre tenants, revogacao, permission downgrade,
confirmacao literal, idempotencia, colisao de chave, nota, tarefa, assignee,
handoff e aprovacao.

## Limites e proxima fase

Este e um adaptador local `stdio`, nao um servidor remoto. Nao implementa OAuth,
PAT, Streamable HTTP ou conexoes reais. Quando o escopo autorizar modelos e
endpoints compartilhados, a evolucao recomendada e adotar o desenho thin-REST
do BottleCRM: PAT por usuario, hash em repouso, revogacao, scopes, rate limit e
MCP sem acesso direto ao ORM. Ate la, o grant local e a fronteira deliberada.
