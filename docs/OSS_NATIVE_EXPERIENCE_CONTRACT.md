# Contrato de experiência OSS nativa no KLM Hub

## Decisão

As 135 famílias comunitárias classificadas de Chatwoot, Rocket.Chat,
ChatterMate e BottleCRM devem ser operáveis por uma rota do próprio KLM Hub.
Os sidecars continuam como motores e fontes auxiliares, mas deixam de ser a
experiência primária. Abrir o produto completo é uma ferramenta secundária de
diagnóstico e administração.

## Critério obrigatório

Uma capacidade só recebe `nativeExperience=true` quando atende, em conjunto:

1. rota estável `/app/recursos/{provider}/{capability}` sem iframe;
2. snapshot autenticado e isolado por workspace;
3. conteúdo específico da capacidade, sem painel vazio ou contador genérico;
4. ações adequadas ao domínio, com formulário e confirmação quando necessário;
5. escrita no motor local ou persistência KLM explícita e auditável; capacidades
   sob gate comprovam somente configuração, validação e `dry-run`;
6. idempotência, optimistic version e rejeição de replay divergente;
7. estados de loading, vazio, erro, indisponível, sem permissão e gate externo;
8. remoção de segredos e dados sensíveis do payload de resposta e da auditoria;
9. prova unitária/integração e smoke da rota no desktop e no mobile;
10. fonte e limite visíveis: `KLM`, `sidecar`, `sincronizado`, `somente local` ou
    `execução externa bloqueada`.

Deep link, healthcheck, string de rota, presença no checkout, leitura parcial ou
abertura autenticada do motor não satisfazem esse contrato.

## Modos nativos

- `sidecar_round_trip`: leitura e escrita reconciliadas com o motor;
- `sidecar_command`: comando real no motor, com leitura posterior comprovada;
- `klm_canonical`: KLM é a fonte de verdade e projeta no motor;
- `klm_equivalent`: implementação própria para capacidade não comunitária ou
  sem API utilizável, sem copiar código proprietário;
- `gated_configuration`: configuração, validação e dry-run nativos, com o efeito
  externo bloqueado até credenciais e aprovação;
- `responsive_substitution`: experiência responsiva do KLM substitui um cliente
  móvel upstream que não será distribuído.

## Gates e licenças

Um bloqueio de licença impede usar ou copiar o módulo upstream, mas não impede
criar um equivalente KLM independente. Um gate externo não impede a experiência
nativa de configuração, preview, fila, auditoria e dry-run; impede somente o
efeito de rede.

Nenhuma tela pode apresentar `enviado`, `entregue`, `sincronizado` ou
`operacional` sem confirmação correspondente. Aceitação local, fila e intenção
devem possuir estados distintos.

## Segurança

- Todas as consultas e mutações incluem `workspace_id` no backend.
- Um servidor/organização sidecar não pode ficar ativo em dois workspaces.
- Ações usam `Idempotency-Key` e hash canônico do payload.
- Configuração rejeita chaves, tokens, senhas, cookies e segredos brutos.
- Execução externa continua condicionada aos gates cumulativos do KLM.
- Logs e auditoria recebem somente metadados sanitizados e limitados; credenciais,
  cookies e marcadores censurados são removidos antes da persistência ou exibição.

## Prova atual

O registro V4 só marca uma capacidade como verificada quando
`app/data/oss_native_verification.json` corresponde, por hash, ao catálogo, aos
adapters, à SPA e aos testes obrigatórios. Alteração em qualquer evidência faz a
verificação falhar fechada. O manifesto 1.2 inclui inventário e snapshot de
conteúdo determinístico, portanto a prova continua reproduzível mesmo com
arquivos ainda não consolidados em Git. A rodada atual comprova:

- 135 experiências e 135 rotas nativas;
- 121 capacidades com contrato local validado por leitura nativa e dry-run;
- 12 capacidades de configuração com efeito externo bloqueado;
- 2 substituições responsivas;
- 511 ocorrências de ações, agrupadas em 360 comandos técnicos;
- zero deep link primário;
- 511 marcadores explícitos de execução externa em dry-run e zero execução
  externa reportada pela aplicação; nenhuma observação independente de sockets
  integra esta prova.

Esses números são invariantes de teste, não estimativas de interface. Uma
capacidade pode estar nativa e, ao mesmo tempo, corretamente limitada a
configuração ou simulação enquanto o gate externo estiver fechado.

O laudo reproduzível da rodada final está em
`docs/QA_OSS_NATIVE_2026-07-18.md`: 1.538 testes backend, 176 testes unitários de
frontend e 205 testes E2E foram aprovados, com 511 ações encontradas nas 135
rotas e latência p95 de navegação de 2.436 ms. O smoke pós-manifesto repetiu a
matriz em estado verificado com p95 de 2.263 ms e zero efeito externo reportado
pela aplicação.

## Gate de conclusão

A meta termina somente quando o registro informar `unclassified=0`,
`nativeExperienceVerified=135`, `primaryDeepLink=0`, `nativeRoute=135`, os
adapters declararem exatamente as mesmas 135 chaves e as matrizes backend, E2E e
runtime provarem cada rota e cada ação anunciada, incluindo o motivo visível de
toda ação bloqueada.
