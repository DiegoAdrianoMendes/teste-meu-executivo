# KLM Hub - QA Omnichannel V7

**Data:** 21 de julho de 2026  
**Escopo desta rodada:** compositor de conteudo avancado em Conversas  
**Ambiente:** exclusivamente local  
**Gate externo:** fechado

## Resultado

O compositor nativo de mensagens ricas foi validado como uma acao contextual da
tela de Conversas. Ele abre em modal, preserva a thread e o caso selecionados,
nao usa iframe e nao muda a rota do SPA.

Capacidades verificadas:

- arquivo existente ou novo documento ligado ao card;
- modelo oficial;
- botoes interativos;
- lista interativa;
- WhatsApp Flow;
- previa antes da persistencia;
- validacao de capacidade por rota;
- persistencia local como rascunho quando o gate externo esta fechado;
- vinculacao de documentos ao card e ao contexto recuperavel pelo RAG.

## Contrato de backend

O backend rejeita payloads malformados antes de persistir ou tentar entregar:

- metadados acima de 64 KiB;
- mais de 10 anexos;
- nota interna com conteudo rico;
- template sem identificador ou idioma;
- interativo sem corpo ou opcoes validas;
- mais de uma midia em uma mensagem WhatsApp;
- referencia privada sem resolucao de upload no provedor;
- capacidade nao habilitada para a rota escolhida.

Arquivos centrais:

- `app/schemas/klm_operations.py`;
- `app/services/klm_operations_service.py`;
- `app/services/whatsapp/cloud_payload.py`;
- `frontend/src/klm/components/ConversationRichMessageDialog.tsx`;
- `frontend/src/klm/pages/ConversationsPage.tsx`;
- `frontend/src/klm/api.ts`.

## Evidencia automatizada

Comandos executados e resultados:

```text
frontend/src/test/conversations-tools.test.tsx        33 passed
tests/services/test_whatsapp_cloud_payload.py
tests/api/test_omnichannel_v6.py                      26 passed
frontend typecheck                                    passed
eslint focado                                         passed
ruff F e E501 focado                                  passed
python py_compile focado                              passed
```

Os testes cobrem preparo do comando rico, gate local, validacao de tipos,
capabilities, referencias privadas de midia e renderizacao Cloud API.

## Evidencia no navegador

Validacao feita no navegador integrado, sobre uma conversa WhatsApp Cloud API
real do seed local:

- URL antes, durante e depois do modal permaneceu a mesma;
- um unico `dialog` e zero `iframe`;
- zero overflow horizontal no desktop;
- tabs Botões, Lista e WhatsApp Flow operaveis;
- botoes e opcoes exibidos na previa;
- viewport `390 x 844` sem overflow horizontal;
- modal mobile com rolagem interna de `702 px` e rodape de acao estavel;
- campos inferiores, previa e avisos permaneceram acessiveis apos a rolagem;
- zero erro de console durante a passagem;
- fechamento restaurou a tela de Conversas sem perder contexto.

## Limites honestos desta fase

- Nenhum envio foi feito a Meta, Google, TikTok ou outro provedor.
- A rota nativa de upload esta implementada e validada contra um cliente Meta
  controlado em teste. A chamada real ao provedor depende das credenciais que
  serao entregues apenas na fase de APIs externas.
- O catalogo remoto de templates aprovados sera sincronizado somente na fase de
  APIs externas.
- `provider_accepted`, `sent`, `delivered` e `read` continuam proibidos sem
  receipt real compativel.

Esses limites sao gates deliberados, nao simulacoes de sucesso.

## Rodada complementar - conversa contextual e tracking

O workspace de Conversas recebeu uma segunda passagem de hardening para manter
as ferramentas no contexto do atendimento:

- `Operar`, `CRM`, `Fluxo`, `Pedir docs`, `Agendar`, `Proposta`, `Disparos` e
  `Audio` permanecem juntos no compositor;
- no desktop, as oito acoes ocupam uma unica faixa sem overflow;
- a altura do workspace usa o espaco efetivo abaixo dos cabecalhos, mantendo o
  compositor completo dentro do viewport e o historico com rolagem propria;
- Operar, CRM, Fluxo, Agendar, Disparos e Audio abriram laminas ou dialogs
  contextuais em passagem real no navegador;
- o agendamento de mensagem abriu seu formulario contextual;
- em todos esses cliques, a URL com `matter` e `thread` permaneceu inalterada;
- `Pedir docs` e `Proposta` foram validados por testes de persistencia e
  vinculacao ao caso;
- navegacoes internas da lamina, como Caso 360 para Rastreamento, continuam
  incorporadas ao workspace pela rota contextual, sem iframe;
- nenhum erro de console foi observado. Permanecem apenas os avisos de flags
  futuras do React Router 7, sem impacto funcional nesta versao.

Medicao visual no viewport `1544 x 994`:

```text
acoes contextuais: 8/8 visiveis
topo da faixa:       907.5 px
base da faixa:       939.5 px
overflow horizontal: nenhum
rota preservada:     sim
```

Evidencia atualizada desta rodada:

```text
backend integrado de tracking, operacoes e migracoes     102 passed
frontend contextual e ferramentas de conversa             44 passed
frontend conversations-tools                               36 passed
frontend typecheck                                         passed
eslint focado                                              passed
ruff F/E9/I focado                                         passed
git diff --check                                           passed
```

O tracking detalhado, a deduplicacao comercial e os contratos de feedback aos
provedores estao documentados em `docs/TRACKING_CORE_V2_2026-07-21.md`.

## Hardening do upload oficial de midia

O upload para a Cloud API passou a usar uma maquina de estados duravel e
idempotente antes de qualquer chamada externa. O contrato agora garante:

- chave `Idempotency-Key` explicita ou chave deterministica derivada do payload;
- hash do arquivo, tamanho, MIME, extensao e hash do nome antes do despacho;
- estados `prepared`, `dispatching`, `provider_accepted`, `completed`, `failed`
  e `delivery_uncertain` persistidos por workspace e conexao;
- checkpoint anterior ao provedor e checkpoint separado apos aceite;
- reexecucao de intent concluida ou aceita sem segundo upload;
- bloqueio de repeticao quando o resultado do provedor for incerto;
- `intentId` devolvido em falhas reconciliaveis e rejeicoes conhecidas;
- prova imutavel do ID de midia e resposta autenticada do provedor;
- isolamento RLS para intents e receipts, inclusive sob o papel da aplicacao;
- classificacao de falha de credencial separada de rejeicao de payload;
- limite de proxy em `105m`, acima do teto documental de `100 MiB` do backend.

O MIME declarado pelo navegador nao e aceito sozinho. PDF, JPEG, PNG, OOXML,
Office legado, texto, audio e video passam por validacao estrutural compativel
com o tipo e a extensao. Arquivos forjados, corrompidos, com cauda anexada,
compressao suspeita ou caminho inseguro sao rejeitados antes do provedor.

Evidencia focada desta camada:

```text
upload, idempotencia, reconciliacao e conteudo de midia       52 passed
ruff F/E9/I focado                                             passed
```

Nenhum desses testes efetuou entrega externa. O cliente de provedor foi
substituido por doubles controlados que exercitam aceite, rejeicao, timeout,
concorrencia, falha de checkpoint e recuperacao.

## Evidencia final vinculada ao release local

A rodada final e vinculada a um unico snapshot de 1.339 arquivos. O digest
canonico, as cinco provas formais e o inventario completo ficam registrados em
`app/data/oss_native_verification.json`, evitando duplicar no texto um valor que
deve ser conferido diretamente no manifesto assinado pelo conteudo.

```text
frontend unitario, typecheck, lint e build           391/391 testes
Playwright de producao                               297/297 testes
  desktop, mobile e tablet                          cobertura integral
backend PostgreSQL com RLS                         2.420/2.420 testes
testes ignorados                                              0
runtime HTTP                                         119 checks
experiencias nativas verificadas                            135
familias contratuais verificadas                            121
acoes expostas e validadas                                  511
execucoes de efeitos externos                                  0
locks de upstream                                        5/5
prova de operacao sem sidecars                           7/7
criterios do gate de arquitetura                       12/12
```

O smoke pos-finalizacao reabriu as 135 experiencias e confirmou 511 acoes com
`p95=2.409 ms`. Essa e uma medicao do ambiente local de QA, nao um SLA de
producao.

### Regra de contexto da caixa de entrada

Os oito comandos `Operar`, `CRM`, `Fluxo`, `Pedir docs`, `Agendar`, `Proposta`,
`Disparos` e `Audio` ficam na propria tela de Conversas. Cada comando abre uma
lamina, dialog ou painel contextual vinculado ao `matter` e ao `thread`; nenhum
deles substitui a rota principal, usa iframe ou perde o caso selecionado.

Essa regra foi comprovada por teste unitario e E2E nos tres viewports. O Caso
360 tambem expôs dossie, arquivos, downloads, agenda e ferramentas contextuais
sem retirar o atendente da conversa. Documentos, reunioes e proposta passaram
pelo contrato de API local do caso selecionado.

### Estado servido pela API

`GET /api/v1/capabilities` retornou `verification.state=verified`, cinco provas
e 135 capacidades verificadas. A fronteira publica usa somente os dominios
nativos `communications`, `collaboration`, `automation` e `crm`; identificadores
de implementacao importados permanecem internos.

APIs externas, entrega real em provedores e AWS continuam deliberadamente
desconectadas. Sao gates da proxima fase e nao foram declarados como validados.

## Tempo sem resposta nos cards e KPIs operacionais

A lista de Conversas agora exibe o tempo de espera dentro de cada card, sem
exigir a abertura do atendimento. O indicador diferencia explicitamente:

- cliente aguardando resposta do escritorio;
- escritorio aguardando resposta do cliente;
- SLA de resposta vencido;
- conversa respondida ou ainda sem mensagens.

Enquanto existe uma espera ativa, o relogio e atualizado a cada segundo por um
unico clock compartilhado na pagina. O mesmo estado aparece no cabecalho da
conversa selecionada. Cards com SLA vencido recebem tratamento de alerta sem
alterar ou fabricar o estado persistido pelo backend.

Os filtros da caixa de entrada incluem estado da resposta, SLA vencido, espera
minima em segundos e ordenacao pelo maior tempo sem resposta. A lamina `KPIs de
resposta` permanece na tela de Conversas e agrega os numeros por conta, equipe
e atendente atual, com media, mediana, P90, maior espera, primeira resposta e
taxa dentro do SLA. A interface informa que os agrupamentos usam a atribuicao
atual e nao fingem reconstruir transferencias historicas.

Evidencia desta rodada:

```text
backend focado de metricas de resposta                    5/5 testes
backend integrado de conversa, grupos e disponibilidade 143/143 testes
frontend completo                                        421/421 testes
frontend typecheck                                             passed
frontend build + verificacao do bundle                         passed
eslint dos arquivos alterados                                  passed
navegador integrado: cards, header e lamina KPI                 passed
overflow horizontal da lamina KPI                              nenhum
```

O endpoint local e tenant-scoped, ignora notas internas no calculo de resposta
ao cliente e exige permissao de visualizacao de mensagens. A consulta retornou
`401` sem token e dados somente dentro da sessao autenticada. Nenhuma medicao
foi enviada a provedor externo.

## Rodada V8 - compositor assistido e política de viewport

O assistente nativo passou a operar também dentro do campo de resposta da
conversa. O ícone fica no canto inferior direito do compositor e abre o
copiloto em um painel contextual sobre a própria tela. A resposta gerada
continua sendo apenas um rascunho: nada é enviado como humano antes da ação
explícita do atendente.

Comportamentos comprovados:

- o `matter` e o `thread` selecionados são preservados;
- o assistente usa apenas o atendimento atual e suas evidências autorizadas;
- a sugestão volta para o mesmo `textarea` do compositor;
- o envio continua separado da geração e exige confirmação humana;
- o agendamento aberto pelo copiloto permanece contextual;
- não há troca de rota, iframe ou navegação para outra lâmina do SPA.

### Política de altura e rolagem

A regra não é uniformizar páginas com naturezas diferentes. O shell bloqueia a
rolagem do documento, enquanto cada rota escolhe a experiência adequada:

- workspaces operacionais densos, como Conversas, Flows, Agenda e Kanban,
  terminam dentro do viewport e usam rolagem interna por painel;
- editores e páginas de conteúdo extenso, como Playbook Studio, são exceções
  deliberadas e podem rolar toda a superfície útil da rota;
- mobile e tablet podem empilhar o Flow Builder com rolagem da rota; o modo de
  três painéis simultâneos e independentes é uma regra exclusiva do desktop;
- nenhuma dessas modalidades permite rolagem residual do `body` ou overflow
  horizontal da aplicação.

No Flow Builder, biblioteca, canvas e inspetor ocupam a altura útil no desktop.
A biblioteca tem seu próprio scroll e as ferramentas nativas complementares
foram reunidas em uma seção de acesso único, sem remover capacidades.

### Evidência automatizada complementar

```text
frontend unitário focado                                  61 passed
frontend typecheck                                         passed
eslint focado                                              passed
Playwright: 27 rotas + Playbook Studio e workspaces        91 passed
Playwright: teste de três painéis em mobile/tablet          2 skipped
PostgreSQL QA descartável com RLS                          33 passed
PostgreSQL QA ignorado                                          0
```

Os dois `skipped` são condicionais explícitos: a asserção de biblioteca,
canvas e inspetor lado a lado só existe a partir do breakpoint desktop. As
mesmas rotas e o empilhamento responsivo foram executados nos dois viewports
menores.

O Playbook Studio foi exercitado separadamente em desktop, mobile e tablet. Em
todos os três, a rota longa rolou, o documento permaneceu em `scrollY=0` e o
editor continuou acessível até o conteúdo inferior.

### Hardening da evidência PostgreSQL

A limpeza do banco descartável agora desabilita triggers, remove apenas a
evidência da execução e reabilita triggers dentro da mesma transação. Qualquer
erro provoca rollback integral. O runner usa banco e container exclusivos por
execução, valida o label antes da remoção e nunca apaga um container apenas por
coincidência de nome.

As duas observações médias da revisão independente foram encerradas por esses
controles e pela repetição da suíte com `33 passed` e `0 skipped`.
