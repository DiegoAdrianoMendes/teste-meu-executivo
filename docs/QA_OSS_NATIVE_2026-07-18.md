# QA da experiência OSS nativa - 18 e 19/07/2026

## Resultado

**Aprovado para validação local do proprietário.** As capacidades comunitárias
aprovadas de Chatwoot, Rocket.Chat, ChatterMate e BottleCRM foram incorporadas
como experiências do próprio KLM Hub. Os sidecars permanecem preservados como
motores auxiliares e superfícies administrativas; não são a experiência
primária do usuário.

Esta aprovação é exclusivamente local. Nenhuma credencial externa, entrega real
de WhatsApp ou e-mail, sincronização Google/Teams, ZapSign, Asaas, ElevenLabs,
pixel de anúncios ou preparação de AWS foi ativada. O contador de efeitos
externos executados permaneceu em zero segundo os 511 marcadores explícitos de
`dry-run` reportados pela aplicação. Esta rodada não realizou observação
independente de sockets ou captura de tráfego de processo.

## Matriz comprovada

| Medida | Resultado |
|---|---:|
| Capacidades e rotas nativas | 135 / 135 |
| Chatwoot | 39 |
| Rocket.Chat | 33 |
| ChatterMate | 27 |
| BottleCRM | 36 |
| Capacidades com contrato local validado | 121 |
| Configurações com efeito externo bloqueado | 12 |
| Substituições responsivas | 2 |
| Deep links como experiência primária | 0 |
| Ocorrências de ações expostas na SPA | 511 |
| Comandos técnicos únicos | 360 |
| Efeitos externos executados | 0 |

Distribuição por modo: 90 `sidecar_command`, 18 `sidecar_round_trip`, 6
`klm_canonical`, 7 `klm_equivalent`, 12 `gated_configuration` e 2
`responsive_substitution`. Um recurso nativo pode continuar limitado a
configuração ou simulação enquanto seu gate externo estiver fechado.

## Hardening final

- comandos OSS com criação atômica, replay idempotente e reconciliação de
  registros abandonados em `received` após falha ou reinício;
- intents de rastreamento e ações externas com idempotência semântica: a mesma
  chave com payload diferente é rejeitada, não reaproveitada;
- lançamento dos motores administrativos restrito a proprietário/admin com
  `integrations.manage`, ticket ligado a ator, workspace, papel e provider, com
  revalidação antes do consumo do nonce;
- autoria de áudio interno ligada ao operador autenticado, com proveniência
  persistida e exibida pela SPA; a ordinal da solicitação é derivada do histórico
  persistido, não de campos enviados pelo cliente; mídia reproduzível aceita
  somente download privado do próprio KLM com ticket válido, nunca URL HTTPS
  arbitrária fornecida em metadados;
- escopos legados `own` e `team` falham fechados; somente concessões explícitas
  `workspace` ou `all` autorizam uma ação, no frontend e no backend;
- paridade de RBAC no frontend e no backend, inclusive rastreamento, relatórios,
  áudio, omnichannel, flows, equipe, processos e motores administrativos;
- dados do card, documentos, conversas, contratos, agenda e projeções mantidos
  no contexto do workspace e do caso, com RLS comprovada em PostgreSQL;
- viewport mobile medido por `document.documentElement.clientWidth`, sem permitir
  que `window.innerWidth` inflado esconda overflow de layout;
- correção de largura intrínseca em Rastreamento e Configurações da Agenda; as
  tabelas largas agora rolam apenas em seus próprios contêineres;
- autenticação de prontidão do Rocket.Chat com orçamento compatível com troca de
  sessão e reconciliação rápida no SPA quando um motor estiver degradado;
- estado de indisponibilidade continua fail-closed: um motor realmente parado
  permanece indisponível e não é presumido como ativo; snapshots degradados
  expiram em 1 segundo para não prender a SPA em um falso estado antigo;
- Agenda sincroniza o estado do WebSocket depois de registrar os listeners,
  eliminando a corrida de carregamento frio que deixava o badge preso em
  `Reconectando` mesmo com o canal conectado; a lista de reuniões também se
  reconcilia a cada 60 segundos com WebSocket saudável ou 15 segundos em modo
  degradado, e follow-ups são reconciliados a cada 30 segundos;
- a CSP efetivamente servida pelo gateway ChatterMate permite apenas o próprio
  host e WebSockets loopback; o verificador estático e o probe do header vivo
  rejeitam origens Firebase, Google, Shopify, CDN ou outros provedores;
- o manifesto 1.2 é preso a inventário e snapshot de conteúdo determinístico.
  A validação criptográfica usa cache fail-closed por assinatura de arquivos:
  qualquer alteração de fonte, manifesto, prova, inventário ou snapshot força
  nova validação completa.

## Evidência executada

| Verificação | Resultado |
|---|---|
| Backend completo | 1.538 aprovados, 11 pulados, 0 falhas |
| Frontend unitário | 176 aprovados, 0 falhas |
| TypeScript | aprovado |
| ESLint | 0 erros; 9 avisos de Fast Refresh |
| Build de produção | aprovado; 3.695 módulos transformados |
| Playwright desktop/mobile | 205 aprovados, 0 falhas |
| Matriz exaustiva das 135 rotas | 511 ações; p95 de 2.436 ms |
| Smoke pós-manifesto | 3 aprovados em 3,5 min; 135/121/511; p95 de 2.263 ms |
| Cenário mobile de status repetido | 10 de 10 aprovados |
| Verificador local autenticado | 124 checks; concorrência 8; 0 efeitos externos reportados |
| Contratos HTTP nativos | 135 documentos; 121 capacidades; 511 ações e 511 marcadores de dry-run |
| PostgreSQL real com RLS | 11 aprovados; migração até `99d0e1f2a3b4` |
| Runtime dos sidecars | 4 perfis e endpoints saudáveis |
| Integridade dos upstreams | 5 de 5 locks exatos |
| Ruff e `git diff --check` | aprovados |

As provas estão em `app/data/oss_native_proofs/`, com saídas brutas compactadas
em `app/data/oss_native_proofs/raw/`. O manifesto
`app/data/oss_native_verification.json` referencia cada prova por SHA-256.

Todos os artefatos finais compartilham o digest
`d2e5af57d5f9726ae8be841ce63e882663d9a9af7335cfe68b93d4e568be1c7d`,
calculado sobre 906 arquivos da superfície verificável. A API comprovou executar
esse mesmo digest antes da finalização. Como o worktree contém conteúdo ainda
não consolidado em Git, a prova não depende apenas do `gitHead`: o manifesto
1.2 inclui `sourceBinding.mode=content_snapshot`, inventário por arquivo e o
snapshot determinístico `source-snapshot.tar.gz` (SHA-256
`87c6bb96c22849aef7b3609555732cfe59635b1e14e8dcda411937a3120039de`).
O validador reconstrói o tar, confere membros, tamanhos e hashes e compara as
cinco suítes com a mesma fonte. Qualquer mudança coberta invalida a verificação
até nova rodada.

## Inspeção visual

Foram inspecionadas 14 renderizações Chromium de desktop e mobile, incluindo
Início, Conversas, Ferramentas, Flows, Agenda, Rastreamento, Configurações da
Agenda e Áudio. Não foram observados conteúdo em branco, sobreposição estrutural,
texto cortado ou overflow global depois das correções.

O controlador de navegador integrado ao Codex apresentou incompatibilidade do
próprio plugin (`Cannot redefine property: process`). A inspeção foi concluída
com o Chromium local do Playwright, usando as mesmas URLs e viewports. A suíte
automatizada permaneceu independente desse fallback e passou os 205 cenários.

## Avisos não bloqueantes

- 144 avisos de depreciação de `datetime.utcnow()` em SQLAlchemy,
  `python-jose` e um serviço legado;
- 9 avisos históricos de Fast Refresh em arquivos que misturam componentes e
  exports auxiliares;
- avisos de flags futuras do React Router e base Browserslist desatualizada;
- o runtime Rocket.Chat comunitário local é `6.13.1`, enquanto a fonte auditada
  está fixada em `8.6.1`; a compatibilidade local foi testada, mas a diferença
  deve ser resolvida ou formalmente aceita antes de produção.

## Gate de produção

Este laudo comprova o comportamento local, o isolamento testado e a experiência
nativa. A declaração de zero efeito externo está limitada à instrumentação e aos
marcadores de dry-run da própria aplicação; não equivale a observação
independente de rede. Não comprova envio real, contas externas, carga
distribuída, recuperação de desastre nem implantação em AWS.

O próximo gate permanece condicionado à aprovação do proprietário, ao
fornecimento de credenciais de sandbox, à validação individual das integrações
externas e a uma nova rodada de segurança, carga e recuperação antes de qualquer
efeito de produção.
