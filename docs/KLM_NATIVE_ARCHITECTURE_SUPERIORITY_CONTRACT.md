# Contrato de superioridade da arquitetura nativa KLM

## Objetivo

O KLM não considera uma capacidade superior porque ela possui mais telas ou
porque o código de um projeto de referência foi reescrito. A liberação exige
prova reproduzível de que a implementação nativa preserva o comportamento
necessário ao produto jurídico e aplica controles mais fortes que o melhor
baseline encontrado nos projetos pinados em `docs/oss-upstreams.lock.json`.

Este contrato compara arquitetura e controles. Paridade ou superioridade de
comportamento de uma função individual só pode ser declarada quando a função
atingir `native_verified`. Uma função `native_shadow` permanece consultável em
`dry-run`, mas não pode executar mutações nem sustentar alegação de paridade.

## Requisitos obrigatórios

1. **Fronteira KLM:** rotas, OpenAPI, DTOs e bundle de produção usam somente
   conceitos do KLM. Identidade, rota, topologia e payload dos projetos de
   referência não atravessam a fronteira pública.
2. **Contratos estritos:** respostas públicas usam DTOs explícitos com campos
   extras proibidos. Entradas mutáveis têm limite, validação estrutural e
   rejeitam material secreto persistente.
3. **Isolamento classificado:** toda tabela com `workspace_id` está protegida
   por RLS ou consta de um registro fechado de exceções de bootstrap/control
   plane, com razão e controle alternativo testado. Não existem tabelas sem
   classificação.
4. **RLS transacional:** o contexto de workspace é local à transação, não pode
   ser trocado na mesma sessão e falha fechado quando ausente. O papel de
   runtime não pode ser superusuário, `BYPASSRLS`, owner ou membro de papel que
   contorne as políticas.
5. **Integridade entre tenants:** referências executadas pelo núcleo
   `native_primary` usam chave estrangeira composta por identidade e workspace
   sempre que ambos os lados materializam `workspace_id`. Relações ainda
   classificadas como `native_shadow` não podem mutar dados até receberem a
   mesma proteção ou um controle transacional equivalente comprovado.
6. **Concorrência:** mutações em registros existentes exigem versão esperada;
   conflitos não sobrescrevem estado. Aprovações e leases usam lock ou compare
   and swap no banco, não apenas leitura seguida de escrita.
7. **Idempotência por intenção:** a mesma chave e o mesmo payload retornam o
   resultado anterior; a mesma chave com payload divergente falha. Controles de
   requisição nunca são persistidos como dados do domínio.
8. **Veto humano exato:** ações destrutivas ou sensíveis exigem aprovação do
   mesmo workspace, ainda pendente quando aplicável, vinculada ao digest da
   intenção e consumível uma única vez.
9. **Evidência imutável:** auditorias, eventos de função e versões jurídicas são
   append-only no ORM e no banco. `UPDATE`, `DELETE` e `TRUNCATE` falham; o
   conteúdo jurídico possui digest canônico versionado.
10. **Efeitos duráveis:** estado autoritativo é confirmado antes de realtime.
    Trabalho assíncrono necessário usa outbox persistida e recuperável; falha de
    socket ou broker não reverte nem falsifica o commit do domínio.
11. **Dependência externa explícita:** operação nativa primária não chama motor
    externo de forma implícita. Efeito de rede permanece bloqueado até gate,
    credencial e aprovação específicos, com estado local, enfileirado, enviado,
    confirmado e falho representados separadamente.
12. **Prova vinculada ao código:** censo, manifests, SBOM, testes PostgreSQL,
    testes frontend, E2E, carga e revisão independente são vinculados por hash à
    árvore avaliada. Mudança posterior invalida a prova.

## Baseline comparativo

O comparador utiliza somente commits e escopos de licença pinados. Recursos
proprietários excluídos não são copiados nem tratados como baseline disponível.
Os pontos fortes encontrados nos projetos de referência, como RLS do CRM,
locks de aprovação, filas, realtime e UX operacional, tornam-se piso, não teto.

Exemplos de elevação exigida pelo KLM:

- contexto RLS de sessão com limpeza posterior vira contexto transacional e
  sessão incapaz de trocar de tenant;
- idempotência pontual vira contrato uniforme por chave e digest;
- aprovação com row lock ganha vínculo exato de intenção e consumo único;
- log de auditoria mutável vira evidência append-only no banco;
- DTO acoplado ao motor vira fronteira KLM estrita e neutra;
- callback em memória vira outbox durável quando o efeito precisa sobreviver ao
  processo.

## Gate de liberação

A liberação local falha quando qualquer requisito obrigatório não possui teste
executável ou quando existe função/tabela/rota sem classificação. O relatório
deve separar, sem arredondamento:

- unidades bloqueadas por licença;
- unidades em `native_shadow`;
- unidades em `native_primary` com arquitetura comprovada;
- unidades em `native_verified` com equivalência comportamental independente;
- capacidades expostas ao usuário e capacidades apenas internas.

Nenhum número de testes, sozinho, autoriza a expressão "100% superior". A
alegação permitida é exatamente a coberta pelo gate e pelo nível de maturidade
registrado para cada capacidade.

O scorecard usa exatamente estes doze controles, nesta ordem:

1. `provider_neutral_native_boundary`;
2. `strict_typed_contracts`;
3. `classified_tenant_isolation`;
4. `transactional_rls_role_hardening`;
5. `tenant_reference_integrity`;
6. `optimistic_and_locked_concurrency`;
7. `intent_idempotency`;
8. `exact_single_use_human_veto`;
9. `immutable_evidence`;
10. `durable_post_commit_effects`;
11. `explicit_external_dependency_gates`;
12. `source_bound_release_verification`.

Os onze primeiros formam o `structuralPreflightPassed`. Isso não autoriza uma
alegação de superioridade. `architectureGatePassed` só pode ser verdadeiro
quando o décimo segundo controle também confirma, no mesmo digest da árvore,
PostgreSQL sem skips, frontend unitário, E2E, runtime local e locks/licenças dos
cinco comparadores. O próprio verificador, o P5, os routers, `app/main.py`, Vite
e seus testes integram o inventário de hash. Os relatórios gerados ficam fora
do inventário para evitar autorreferência circular, mas carregam e verificam o
digest integral da fonte.
