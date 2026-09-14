# Controle local de uso de IA em tokens

Status: implementado e validado localmente em 22/07/2026.

Este documento registra o gate obrigatório anterior à conexão de APIs de modelos. Ele não autoriza provedores externos, ações externas ou AWS.

## Regra operacional

1. Toda chamada de modelo passa por `app/ai/gateway.py`.
2. A franquia mensal tipada do workspace é resolvida antes da chamada de rede.
3. Ausência de franquia não significa uso ilimitado: provedores reais ficam bloqueados.
4. Uma reserva idempotente é criada antes da chamada e liquidada com o consumo real depois da resposta.
5. Cada chamada faturável produz um recibo imutável, identificado por workspace e `request_id`.
6. Repetição idêntica do mesmo recibo é um no-op; repetição divergente é rejeitada.
7. Falha de contabilização após início da chamada não aciona fallback faturável e permanece marcada para reconciliação.
8. Fallback estruturado não repete a chamada ao provedor.
9. Cache confirmado registra zero token faturável e preserva a origem da decisão.

## Dados persistidos

- `platform_model_usage_ledger`: recibo append-only por chamada.
- `platform_token_usage`: agregado diário por modelo/provedor.
- `platform_token_usage_by_task`: agregado diário por classe de tarefa.
- `workspace_monthly_entitlement_usage`: consumo e reservas do mês UTC.
- `entitlement_usage_reservations`: permissão idempotente adquirida antes do uso.

Os recibos guardam identificadores operacionais, tokens, custo interno, versões dos catálogos de preço e rota e identificador do provedor. Não guardam prompt, resposta, chave ou documento do cliente.

## Visibilidade

- Dono ou administrador do workspace: uso, franquia, saldo e divisão por tarefa, sempre em tokens e somente da própria conta.
- Super Admin do SaaS: mesma telemetria por workspace e custo monetário interno.
- Demais usuários: sem acesso ao painel de orçamento.
- Pacotes extras de tokens: preço comercial pode aparecer no catálogo, mas a liberação exige suporte autorizado.

## Estados da franquia

- `configured`: franquia tipada do plano, aplicável a provedores reais.
- `local_legacy`: limite local de simulação; não autoriza provedor real.
- `missing`: configuração ausente; fail-closed.
- `not_applicable`: visão global do Super Admin.

Nenhum desses estados usa o rótulo "ilimitado" para uma configuração ausente.

## Gates preservados

- `KLM_AI_USAGE_ACCOUNTING_ENABLED=true` é obrigatório para habilitar APIs de modelo.
- `KLM_MODEL_APIS_ENABLED=false` continua sendo o padrão de execução externa bloqueada.
- `KLM_ONLINE_APPROVED=false`, `KLM_EXTERNAL_ACTIONS_ENABLED=false` e `KLM_AWS_ENABLED=false` permanecem fechados.
- O provedor fake local pode executar sem assinatura para testes determinísticos; provedores reais não podem.

## Evidência local

- `55` testes backend focados passaram: gateway, exclusividade, contabilização, API, migração, configuração e compatibilidade.
- `10` testes frontend focados passaram: painel e serviços de custo.
- `compileall` backend passou.
- `tsc --noEmit` frontend passou.

## Pendências antes de APIs reais

1. Rodar o mesmo contrato contra credenciais de sandbox de cada provedor, sem dados reais de clientes.
2. Confirmar o mapeamento de `provider_request_id` e tokens cobrados na resposta oficial de cada API.
3. Validar reconciliação de timeout em que o provedor processa a chamada, mas a resposta não chega.
4. Aprovar catálogo inicial de rotas, preços e franquias dos planos.
5. Executar teste de carga e concorrência no PostgreSQL de produção candidata.

Essas pendências não impedem o desenvolvimento local; impedem a abertura do gate externo.
