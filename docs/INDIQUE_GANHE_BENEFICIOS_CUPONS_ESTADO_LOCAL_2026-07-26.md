# Indique e Ganhe, Benefícios e Cupons - Estado Local

## Escopo entregue

- Portal do escritório em `/app/beneficios`, acessível ao owner, com link de indicação, histórico, elegibilidade e resgate de cupons.
- Studio de indicações e benefícios em `/blackbox/benefits`.
- Studio de cupons em `/blackbox/coupons`.
- Recompensa pertencente ao workspace indicador, liberada após o primeiro pagamento mais 30 dias.
- Benefício de boas-vindas configurável para o workspace indicado.
- Partner reavaliado trimestralmente por contas indicadas ativas e MRR indicado.
- Concessão manual de dias, meses, tokens, desconto ou crédito, com ator, motivo, ticket vinculado e trilha de eventos.
- Cupons com vigência, limites globais e por workspace, segmentação, aprovação, aplicação automática ou disponibilização para aplicação manual.

## Controles implementados

- Idempotência na criação, concessão, aplicação e resgate.
- Serialização transacional no PostgreSQL e bloqueio de linhas nas mutações concorrentes.
- Operação atômica para criar, aprovar e aplicar um benefício no Blackbox.
- Reconciliação obrigatória quando a assinatura mudou depois da aplicação e uma reversão automática seria insegura.
- Ledger imutável por concessão e evento.
- RLS e políticas por workspace para tabelas de benefícios, com bypass explícito somente no contexto da plataforma.
- Separação do programa SaaS de indicação do fluxo jurídico F13 existente.

## Evidências locais

- Compilação Python: concluída.
- Testes direcionados de serviço, acesso, migração e contrato RLS: 12 aprovados.
- Typecheck do frontend com Node 24.18: concluído.
- Builds de `/app` e `/blackbox`: concluídos.
- Alembic: uma única head, `8d3ee1871532`.

## Limites desta entrega

- Nenhuma publicação na AWS foi realizada.
- Nenhum gateway de cobrança externo foi ativado.
- As políticas RLS foram verificadas por contrato de migração; ainda falta o teste de integração contra uma instância PostgreSQL real antes da produção.
- O QA foi direcionado ao novo núcleo, conforme a decisão de não executar novamente a suíte integral neste momento.
- Avisos de depreciação do Pydantic V2 permanecem preexistentes e não impediram os testes.

## Gate antes de produção

1. Executar upgrade e downgrade da migração em PostgreSQL efêmero.
2. Provar isolamento entre dois workspaces e bypass da plataforma com sessões reais.
3. Conectar o evento canônico de primeiro pagamento do provedor escolhido.
4. Validar conciliação, estorno e expiração em sandbox do provedor.
5. Rodar smoke autenticado das rotas `/app/beneficios`, `/blackbox/benefits` e `/blackbox/coupons`.
