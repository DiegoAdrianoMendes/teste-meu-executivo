# KLM Hub - Plano executado e entrega local para aprovação

> **Documento historico.** A nova meta local vigente esta em
> [`KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md`](./KLM_HUB_META_E_PLANO_SAAS_LOCAL_V5_2026-07-19.md).
> O status de entrega abaixo descreve apenas a rodada anterior e nao conclui a
> meta SaaS V5.

> **Plano tecnico indicado naquela rodada:** [KLM Hub - Plano Adaptation-First V2](./KLM_HUB_PLANO_ADAPTATION_FIRST_V2.md). Nenhuma funcao upstream seria removida e nenhuma integracao seria implementada sem aprovacao previa.

- **Status:** `LOCAL_APPROVAL_READY_ADAPTATION_FIRST_R10_QA_TOTAL`
- **Data da evidência:** 17 de julho de 2026
- **Escopo:** SPA, backend, sidecars OSS, runtime de agentes API-first e simulador de desenvolvimento executados somente no computador local.
- **Decisão pendente:** aprovação do proprietário para iniciar, em fase separada, conectores reais e preparação da AWS.

## 1. Contrato desta fase

A ordem aprovada foi cumprida literalmente:

1. Frontend SPA completo e responsivo.
2. Backend local, persistência, contratos e dados de demonstração.
3. Agentes ligados à UI e ao backend local, depois das duas etapas anteriores.
4. Bateria ampla de testes e inspeção visual.
5. Entrega local para decisão do proprietário.

Até uma aprovação explícita, permanecem proibidos:

- chamadas a provedores externos;
- envio real de WhatsApp, e-mail, áudio, contrato, procuração ou cobrança;
- publicação ou alteração de campanhas;
- uso de credenciais reais;
- criação de recursos ou deploy na AWS;
- qualquer mudança online ou em produção.

## 2. Plano executado

| Etapa | Estado | Evidência principal |
|---|---|---|
| Frontend SPA | Concluída | 24 rotas operacionais, temas claro/escuro, desktop e mobile |
| Backend local | Concluída no escopo local | SQLite principal, PostgreSQL temporário, API autenticada, auditoria, RLS e idempotência concorrente |
| Integração SPA/API | Concluída | Verificador reproduzível com 120 checks, autenticação, CORS, OpenAPI e efeitos externos bloqueados |
| OSS adaptation-first | Concluída no escopo local | Chatwoot, Rocket.Chat, ChatterMate e BottleCRM completos, autenticados e saudáveis em sidecars; sessões locais assinadas e bridges reconciliáveis sem alterar os checkouts |
| Agentes | Concluída no escopo local | 20 agentes ligados ao gateway API-first; Orquestrador coordena o sistema, Hermes é a ponte exclusiva com o proprietário, e o simulador determinístico permanece restrito a desenvolvimento/teste |
| Testes | Concluída | 1.116 testes aprovados nas suítes principais e 11 provas PostgreSQL adicionais, além de 120 checks HTTP, runtime dos sidecars, migrations, build, typecheck e contrato estrito |
| Provedores e AWS | Não iniciada por decisão do proprietário | Todos aparecem como `offline`; nenhuma credencial foi solicitada ou salva |

## 3. Produto disponível na SPA

O primeiro acesso já é a operação real da plataforma, sem landing page intermediária:

- visão executiva com KPIs, funil, aquisição, receita, decisões e qualidade;
- conversa comercial no padrão WhatsApp, autoria por agente, takeover humano, metadados do caso e conversa interna da IA;
- omnichannel unificado para WhatsApp, e-mail, formulário, Instagram, áudio e mensagens internas;
- Caso 360 com documentos, RAG local, subtema, objeto, timeline, reunião e projeção econômica;
- subtemas de negativa de plano de saúde: medicamento de alto custo e nome do medicamento, tratamento, home care, cirurgia, exame e OPME;
- projeções de honorários iniciais, finais, êxito e cenários com ou sem valoração econômica da obrigação de fazer, sempre sob revisão humana;
- funil comercial, rastreamento, qualidade dos leads, CAC, canais, oportunidades e remarketing;
- comentários de Instagram/Facebook e automações comerciais por keyword, resposta pública ou privada, gate de follow, fila/etapa e IA, com transporte Meta ainda offline;
- moeda em formato brasileiro completo, com centavos e sem abreviações `K`/`M` nas superfícies financeiras;
- flows visuais com execução local em tempo real, histórico persistido e zero dispatch externo;
- agenda, contratos, procurações, petições, financeiro, cobrança, aprovações e trilha de atividade;
- controle de agentes, estados, runs, permissões, custos, tokens e conversa interna;
- gestão de usuários dentro de Configurações, com criação de conta, senha inicial, papel, suspensão, reativação e permissões individuais;
- ocultação e edição independente dos dados sensíveis dos leads por usuário, aplicadas no backend e sinalizadas na interface;
- TI Agent nativo para bugs e melhorias locais;
- áreas plug and play em blueprint, sem ativação prematura de novos domínios;
- modo claro e escuro persistente e layout sem overflow em 393 px e 1440 px.

### 3.1 Ferramentas de Conversas

As ferramentas do atendimento não são botões cenográficos. No modo local aprovado para esta fase:

| Ação | Efeito comprovado | Limite atual |
|---|---|---|
| Responder e nota interna | `POST /api/v1/klm/omnichannel/threads/{thread_id}/messages` e atualização do histórico | persiste como rascunho local; não entrega no canal externo |
| Assumir atendimento | `PATCH /api/v1/klm/omnichannel/threads/{thread_id}` | altera o responsável local; não inicia sessão em provedor |
| Pedir documentos | `POST /api/v1/klm/follow-ups`, com política `lead_never_unanswered` | agenda o follow-up local; não envia WhatsApp ou e-mail |
| Agendar | `POST /api/v1/meetings` e abertura de `/app/agenda?meeting={id}` | cria reunião online local; convite, Calendar e Meet permanecem offline |
| Proposta | `PATCH /api/v1/klm/funnel/{matter_id}/stage`, geração da prévia `/proposta/{matter_id}` e rascunho da mensagem | a prévia é interna; não é link público, não é enviada nem assinada |
| CRM e RAG | abre o Caso 360 do atendimento com painel contextual | opera sobre o banco e documentos locais |
| Procuração, petição e áudio | abre o módulo e o registro exatos do atendimento | ZapSign, Google Docs e ElevenLabs permanecem offline |

Quando a sessão local expira, a SPA recupera a sessão de loopback e repete a requisição. Se os dados conectados realmente não estiverem disponíveis, a interface identifica o modo de demonstração, oferece reconexão e bloqueia ações que exigem caso ou thread real.

### 3.2 Comentários sociais e automações

`/app/comentarios` é a superfície local inspirada no fluxo de comentários do
ManyChat, mas incorporada ao CRM jurídico. Ela separa Instagram e Facebook e
permite configurar:

- gatilho por comentário, primeira mensagem, story/mention ou entrada da Page;
- keyword opcional e vínculo com mensagem previamente aprovada;
- resposta pública, resposta privada/DM e gate de following;
- IA generativa com tom e objetivo controlados;
- fila de destino, etapa comercial e escalada para humano;
- leitura dos comentários ingeridos, origem da postagem e elegibilidade para DM.
- abertura do comentário diretamente no modo `Atendimento`, preservando a conversa selecionada.

Nesta entrega, regras, biblioteca, comentários e CRM persistem apenas no backend
local. Webhook, leitura e envio pela Meta Graph API não estão conectados. A tela
exibe `Meta API aguardando conexão`, e nenhum comentário ou DM real pode entrar
ou sair antes da aprovação e das credenciais futuras.

### 3.3 Agenda e Google Agenda

`/app/agenda` é uma agenda operacional própria, e não mais uma fila genérica. A
SPA oferece visões de mês, semana e dia, agenda do dia, busca, filtros por status
e responsável, reuniões e follow-ups na mesma linha do tempo e suporte a
horários simultâneos. A criação, edição, conclusão, cancelamento e exclusão de
reuniões usam o CRUD real em `/api/v1/meetings`. O vínculo vindo de Conversas por
`?meeting={id}` abre diretamente a reunião correspondente.

Cada compromisso pode guardar link de Google Meet ou Microsoft Teams. Ao abrir
o agendamento, o operador vê o acesso à sala; no hover, recebe dados essenciais
do card, contexto do lead, pendências, resumo gerado localmente e insights para
conduzir a reunião. A agenda também entrega briefing consolidado do dia.

No-show é informado manualmente na reunião e alimenta taxa própria, comparação
com o período anterior e indicadores de crescimento da agenda. A lista completa
e o painel de booking em tempo real permanecem ao lado do calendário.

`/app/agenda/configuracoes` centraliza conexão futura, calendários, provedores de
videoconferência, duração, antecedência, intervalos e disponibilidade semanal. O
operador cria uma página vertical de agendamento por dentro do card ou da agenda,
com vínculo opcional ao caso. Um booking sem card nasce explicitamente órfão e
pode ser associado depois a qualquer card existente, sem recriar a reunião.

No mobile, o mês cabe integralmente em 393 px e usa indicadores compactos por
dia; a lista abaixo do calendário mantém os detalhes legíveis. Em desktop, o
painel lateral mostra a agenda do dia, a legenda e o estado da conexão Google.

O estado atual é deliberadamente `Google desconectado`. A interface explica o
futuro OAuth, Google Meet, calendário por usuário, reconciliação bidirecional e
auditoria, mas não chama `accounts.google.com` nem `googleapis.com`. A conexão e
a sincronização só serão habilitadas depois da aprovação online e do cadastro do
Client ID, Client Secret, URI de retorno e calendários autorizados. Testes E2E
provam que abrir o painel Google no modo local produz zero requisições externas.

## 4. Backend local

O runtime de aprovação usa:

- FastAPI em `http://127.0.0.1:8010`;
- SQLite em `.local/klm-hub.db`;
- sessão local emitida somente para origem loopback/privada autorizada;
- CORS restrito à SPA local;
- seed idempotente para casos, mensagens, funil, métricas, flows e aprovações;
- API versionada em `/api/v1`;
- OpenAPI com 281 paths, 356 operações e nenhum `operationId` duplicado;
- uma única cabeça Alembic: `94e5f6a7b8c9`;
- verificador local versionado em `scripts/verify_klm_local.py`;
- migrations aplicadas em PostgreSQL 16 descartável, revertidas integralmente até `base` e reaplicadas até o head;
- `alembic check` com zero operações pendentes entre metadata ORM e schema migrado;
- rollback até `base` comprovado com zero enums residuais e apenas a tabela vazia de controle do Alembic;
- instância PostgreSQL descartável removida e serviço auxiliar do Compose encerrado após a validação.

Os gates locais são cumulativos. Uma entrega externa só pode existir quando houver, ao mesmo tempo, aprovação online, ações externas habilitadas e runtime fora do modo local. Nesta entrega:

```text
mode=local_simulation
localOnly=true
onlineApproved=false
externalActionsEnabled=false
awsEnabled=false
providers=offline
```

`providers=offline` inclui o gateway de modelos e todos os canais. Mensagens de saída viram rascunhos e intents bloqueadas. Aprovar uma intent na interface local não executa o provedor.

### 4.1 Usuários, permissões e privacidade comercial

O proprietário acessa `Configurações > Usuários e permissões` dentro da própria SPA do KLM Hub. Nessa área é possível:

- criar um usuário com nome, e-mail, telefone, papel e senha inicial de no mínimo 10 caracteres;
- suspender ou reativar o acesso com efeito imediato no backend;
- conceder e bloquear permissões específicas sem alterar o papel-base;
- decidir, por usuário, se os dados sensíveis dos leads ficam ocultos, somente para leitura ou editáveis;
- revisar permissões efetivas, exceções individuais e status de cada integrante.

O papel `operator` nasce sem acesso aos dados sensíveis. Owner, administrador do workspace e gestor jurídico recebem o acesso pelo papel-base, mas o administrador pode bloqueá-lo individualmente. O owner preserva controle integral e não pode ser rebaixado por outro administrador; a última conta owner ativa também não pode ser removida ou suspensa.

A proteção não depende da interface. Para usuários restritos, a API mascara nome, CPF, telefone, e-mail, empregador, cargo, identificadores sociais, metadados de contato e referências sensíveis em clientes, casos, funil, WhatsApp, omnichannel, Instagram, Messenger, documentos, RAG, SDR, agenda, financeiro, flows, campanhas, notificações, payloads de agentes, auditoria e aprovações. URLs de documentos e reuniões também deixam de ser retornadas. Busca e ordenação não funcionam como oráculo para descobrir nomes ocultos, e editar dados do lead exige `lead_data:edit` além da permissão operacional. Os eventos de criação e alteração são auditados sem registrar senha.

## 5. Arquitetura dos agentes

- Estratégia de lançamento: APIs de modelos por gateway próprio, sem inferência offline automática.
- Providers implementados: OpenAI Responses, Anthropic Messages, Gemini Interactions e Vertex Gemini.
- Desenvolvimento/testes: `local_simulator` com `deterministic-health-v1`, tokens e custo zero.
- Produção futura: catálogo versionado de rotas/modelos/preços, orçamento, allowlists, circuit breaker e reconciliação de cobrança incerta.
- Gates atuais: `KLM_MODEL_APIS_ENABLED=false`, `KLM_ONLINE_APPROVED=false` e canais externos desligados.

| Agente | Responsabilidade local | Gate principal |
|---|---|---|
| Orquestrador | Coordena agentes, subagentes, contexto, flows e retorno operacional | Nunca solicita o humano nem executa ação externa |
| Hermes | Ponte executiva com o dono; apresenta a exceção já conferida e registra a decisão | Só aceita delegação da Secretaria; não orquestra nem decide |
| SDR Saúde | Triagem, subtema, objeto, checklist e follow-up | Não promete resultado nem decide tese |
| Jurídico Saúde | Hipóteses, riscos, lacunas e evidências | Conclusões decisivas exigem revisão humana |
| CRM de precisão | Qualificação, tags, etapa, tarefas e dados | Só grava fatos sustentados por evidência |
| Closer | Proposta e primeira reunião online | Não assina nem fixa honorários finais |
| Secretaria | Confirmação, lembrete, remarcação e no-show | Encaminha exceções ao Hermes |
| Paralegal | Dados de contrato e procuração | Apenas prepara rascunhos locais |
| Petição | Rascunho com fontes vinculadas ao caso | Nunca protocola; devolve ao Orquestrador e, se necessário, segue por Secretaria e Hermes |
| Andamento | Comunicação pós-contrato | Só usa evento previamente validado |
| Marketing | Gaps, criativos, canais e experimentos | Não publica nem altera orçamento |
| Track | UTMs, pixels, atribuição, CAC, ROAS e alertas | Não edita produção sem evidência |
| Recuperação | Oferta aprovada de baixo ticket | Só usa oferta, preço e público aprovados |
| Cobrança | Vencimentos, lembretes e exceções | Não altera parcela nem envia cobrança |

Propriedades verificadas:

- nenhum agente possui permissão para envio externo;
- somente Hermes cria a solicitação de intervenção humana, após delegação verificável da Secretaria;
- Petição, Cobrança e demais especialistas devolvem a exceção ao Orquestrador; pedidos humanos seguem obrigatoriamente por Secretaria e Hermes;
- a conversa entre agentes é privada, atribuída, persistida e visível ao proprietário;
- runs são idempotentes, auditáveis e isolados por workspace e caso;
- o CRM não inventa campos e mantém pendência jurídica quando falta evidência;
- o fluxo de triagem local executa SDR, Jurídico, CRM, Secretaria e Hermes;
- toda simulação retorna `externalActionsExecuted=0`.
- uma chamada paga é persistida antes dos efeitos locais; falha incerta não repete cobrança com a mesma chave;
- cobrança incerta pode ser conciliada por administrador como cobrada ou não cobrada; a reserva de orçamento é liquidada/liberada, os KPIs são atualizados uma vez e a evidência fica opaca na auditoria;
- saída de agente não entra como evidência primária do RAG e nunca executa ferramenta diretamente.

## 6. Evidências de qualidade

| Verificação | Resultado final |
|---|---|
| Suítes principais | 1.116 testes aprovados: 820 backend + 96 frontend + 200 E2E; 15 skips condicionais nas execuções portáteis/mobile |
| Total com PostgreSQL real | 1.127 aprovações automatizadas ao incluir 11 provas RLS/concorrência executadas no PostgreSQL descartável |
| Backend Pytest | 820 passaram e 11 foram ignorados no perfil portátil; os 11 cenários condicionais passaram no PostgreSQL real |
| Frontend Vitest | 96/96 passaram em 11 arquivos |
| Playwright SPA | 200 passaram e 4 duplicações mobile foram ignoradas intencionalmente, em 24 rotas, 393 px e 1440 px |
| Verificador HTTP SQLite | 120/120 checks passaram em `127.0.0.1:8010` |
| PostgreSQL RLS real | 11/11 testes focados passaram depois do ciclo integral de migrations; RLS, concorrência de e-mail e concorrência de agenda validadas |
| Sidecars OSS | os quatro perfis passaram verificação estática, processos e endpoints; Chatwoot, Rocket.Chat, ChatterMate e BottleCRM estão `ready`, autenticados e `connected_local` |
| Concorrência real | 32/32 respostas `201`: mensagem, tracking, intent e agent run, 8 simultâneas por operação, um ID por chave |
| Console e HTTP no browser | 0 erros de console, 0 exceções e 0 respostas 5xx nas 24 rotas |
| Overflow responsivo | 0 px além do viewport em 393 px e 1440 px |
| Inspeção visual | Funil, Flows, Conversas, Comentários sociais e Rastreamento revisados em capturas desktop/mobile; cabeçalho mobile possui prova geométrica sem sobreposição |
| TypeScript | `tsc --noEmit` sem erros |
| Contrato UI/backend | 10/10 interfaces, 0 erros e 0 avisos com OpenAPI atual |
| Lint frontend | ESLint: 0 erros e 8 avisos legados de Fast Refresh |
| Build Vite | concluído sem erro |
| OpenAPI | 281 paths, 356 operações, 0 IDs duplicados |
| Alembic | 1 head (`94e5f6a7b8c9`); banco descartável migrado de `base` a `head` e 11/11 provas PostgreSQL aprovadas |
| Formatação Git | `git diff --check` sem ocorrências |

Avisos não bloqueantes conhecidos:

- 8 avisos de Fast Refresh em componentes compartilhados preexistentes;
- 2 avisos de flags futuras do React Router apenas no ambiente de teste;
- 136 avisos não bloqueantes concentrados em dependências e defaults legados do backend;
- base local do Browserslist desatualizada; não foi atualizada porque esta fase proíbe acesso online.
- o baseline Ruff de módulos legados e migrations geradas permanece fora do zero; nenhum autofix amplo foi aplicado.

### 6.1 Hardening aplicado até a rodada R10

- validação de pertencimento entre cliente, caso, thread, follow-up e evento de aquisição;
- bloqueio de responsáveis humanos e técnicos fora do workspace ativo;
- takeover humano atribui o operador atual e a devolução à IA limpa a atribuição;
- idempotência protegida por savepoint contra corrida de `SELECT → INSERT`;
- corrida de agent run retorna o run vencedor sem executar novamente nem duplicar mensagem interna;
- serialização do run deduplicado carrega relações assíncronas antes da resposta;
- schemas migrados para Pydantic v2 sem os 21 avisos anteriores;
- typecheck legado corrigido sem relaxar o compilador;
- validador de contrato deixou de aceitar falsos positivos quando o arquivo OpenAPI estava ausente;
- executor e conexão SQLite de testes são encerrados explicitamente no teardown.
- criação de usuário e membership ocorre em uma única transação, sem conta órfã em caso de falha;
- concessões e bloqueios individuais compõem permissões efetivas no backend, sem confiar no papel armazenado no token;
- suspensão e mudanças de permissão passam a valer sem exigir novo login;
- dados sensíveis são mascarados antes da serialização da API, inclusive em conversas e canais sociais;
- owner, último owner ativo, escopo do workspace e escalada de privilégio receberam bloqueios explícitos;
- a gestão de equipe foi incorporada à SPA atual do KLM Hub, com UX própria para desktop e mobile;
- o bootstrap local passou a carregar a sessão antes do contexto da aplicação, eliminando o primeiro frame sem permissões;
- a migration de permissões foi provada em ciclo `upgrade → downgrade → upgrade` com colunas `JSONB` em PostgreSQL 16.14;
- documentos, chunks do RAG, payloads de agente, SDR, agenda, financeiro, flows, campanhas, notificações e auditoria foram incluídos no mesmo bloqueio de dados sensíveis;
- o teste adversarial injeta marcadores de nome, CPF, telefone, e-mail, URL S3, link de reunião e referência financeira e exige ausência total nas respostas restritas;
- as rotas operacionais do KLM Hub agora validam permissões efetivas no backend para dashboard, relatórios, agentes, áreas, follow-up, tracking, TI e integrações;
- a criação de lançamento financeiro com lembretes deixou de acionar lazy-load assíncrono inválido e passou a substituir lembretes por operação SQL explícita.
- a sessão local expirada passou a se recuperar automaticamente e a repetir a requisição original sem cair silenciosamente em dados cenográficos;
- Conversas passou a distinguir banco local, carregamento e demonstração, com reconexão explícita e bloqueio de ações sem thread ou caso real;
- documentos, reunião, proposta, CRM, RAG, áudio, procuração e petição agora preservam o contexto do atendimento ao persistir ou abrir o módulo de destino;
- a prévia local de proposta mostra escopo e projeção do caso sem simular publicação, envio ou assinatura externa;
- o fluxo Conversas → Proposta → Agenda recebeu prova E2E concorrente em desktop e mobile usando casos distintos.
- o funil usa drag and drop persistido, sensor de teclado e controle mobile, com versão otimista do quadro;
- o editor de flows usa React Flow, drag and drop, revisões imutáveis, dry-run, snapshot do run e conflito `409`;
- Conversas ativa, pausa, retoma e consulta flows do catálogo sem enviar ao canal externo;
- proposta em Conversas recarrega a versão do funil e repete uma única vez após conflito otimista;
- a primeira reunião do caso é obrigatoriamente online e a URL é validada sem credenciais embutidas;
- reuniões validam intervalo, conflito e responsável ativo do workspace; atualização não contorna a regra da primeira reunião online;
- excluir uma reunião também revalida a primeira reunião remanescente e não permite transformar o primeiro atendimento em presencial por efeito colateral;
- o gateway API-first aplica schemas fechados, allowlists, orçamento, circuit breaker, telemetria sem conteúdo e reconciliação de cobrança incerta;
- o executor local de flows percorre somente edges alcançáveis, avalia branches, respeita limite de passos, pausa em `human_pause` e retoma pelo snapshot/revisão fixados;
- webhooks Chatwoot exigem vínculo mestre exato e globalmente único entre `account_id` e workspace; URL e segredo global não escolhem outro tenant;
- webhooks, event bus e workers de agentes/flows adotam contexto RLS explícito, e o scheduler abre uma sessão por workspace;
- refresh tokens são consumidos atomicamente, não aceitam replay e deixam de funcionar quando usuário ou membership são suspensos; reset de senha é transacional, de uso único e revoga a família completa de access/refresh tokens e tickets WebSocket;
- login e reset de senha usam lock da conta para fechar a corrida com a senha antiga; conexões WebSocket locais do usuário são encerradas imediatamente;
- nós de agente em Flows usam checkpoint preso à revisão, assentamento terminal idempotente e token de continuação; sucesso avança uma vez, falha fica revisável e override humano reconciliado não duplica o passo;
- receipts Chatwoot usam claim atômico, lease de cinco minutos, fencing e retry seguro após expiração ou falha;
- todos os sinks de rede conhecidos falham antes de criar cliente HTTP quando os canais estão fechados: Meta oficial e aliases, QR gateway, ZapSign, Slack, Google/Meta Ads, mídia WhatsApp, e-mail, transcrição, campanhas, S3 e Cost Explorer;
- APIs de modelos e canais externos possuem gates independentes; habilitar raciocínio por API jamais habilita entrega, assinatura, anúncios, cobrança ou AWS;
- o launcher de aprovação remove chaves de modelo do ambiente e força modelos/canais/AWS desligados; dois restarts consecutivos foram aprovados antes do Playwright final;
- falha ao salvar uma mensagem otimista remove a falsa mensagem e restaura o rascunho do atendente;
- o launcher local separa stdout/stderr em `.local/logs`, usa porta estrita e exibe diagnóstico antes de encerrar jobs malsucedidos;
- os quatro produtos foram fixados e executados como sidecars isolados; ChatterMate e BottleCRM deixaram de ser apenas referências e agora expõem suas interfaces e runtimes completos localmente;
- logs usam referências HMAC opacas e tipo de exceção, sem telefone, e-mail, CPF, mensagem, prompt, resposta, token ou traceback bruto.
- todo avanço assíncrono de flow persiste uma intenção antes do broker; início, retomada, espera e continuação são recuperados periodicamente após queda entre commit e publicação;
- a primeira decisão humana sobre um `AgentRun` é terminal e imutável, com retry idêntico idempotente, conflito explícito e unicidade garantida por migration;
- tickets WebSocket são consumidos atomicamente no primeiro uso; replay, suspensão e reset de sessão são recusados antes do `accept`;
- webhooks OpenClaw, ZapSign, Stripe e genéricos falham fechados no gate externo, exigem credencial/assinatura e possuem proteção de replay ou idempotência atômica quando o provider fornece ID;
- atribuição de conversa, regras automáticas, card relacionado e flow referenciado são validados no workspace antes de qualquer mutação;
- cancelar, excluir, reordenar ou reativar reuniões revalida a primeira reunião efetiva e ignora registros cancelados;
- o launcher espera a remoção real dos jobs do `launchctl`; dois reinícios consecutivos e a abertura da SPA/API foram aprovados depois da correção;
- `GET /api/v1/klm/runtime` expõe separadamente infraestrutura local, APIs de modelos, canais externos, AWS e aprovação online.
- o launcher local sobrescreve os dois DSNs de banco e não herda um PostgreSQL de staging/produção do shell;
- cancelamento de flow e mensagem agendada bloqueia efeitos externos em despacho, confirmados ou incertos, sem apagar a evidência necessária à reconciliação;
- a reserva diária de orçamento do agente é persistida antes da chamada cobrável; Redis e memória mantêm tombstones idempotentes para liquidação/liberação e rejeitam decisão conflitante;
- eventos realtime e alertas operacionais só são publicados depois do commit; rollback descarta o evento e falha de publicação não transforma uma transação já confirmada em erro de domínio;
- mensagens omnichannel validam o payload integral em replay, serializam a thread, preservam preview cronológico e não perdem incremento de não lidas; o caminho SQLite concorrente não dispara lazy-load após rollback de savepoint;
- o scheduler usa ordenação determinística e `SKIP LOCKED`, sem deixar backlog pausado ou aguardando humano ocupar o lote de mensagens elegíveis;
- ZapSign usa segredo de webhook por workspace, e o pipeline inbound do WhatsApp mantém checkpoints duráveis para RAG, SDR, automação e escalada, com estado explícito de reconciliação quando o efeito pode ter ocorrido;
- a API pública exige `Idempotency-Key` nas mutações, receipt transacional e rate limiter fail-closed; Stripe usa receipt global por evento, ordenação por `created` e rejeita o mesmo `event_id` com conteúdo divergente;
- o reconciliador SQLite adiciona de forma idempotente as colunas de lease, Stripe e pipeline inbound em bancos locais já existentes, preservando dados e índices;
- Comentários sociais expõe a operação local de Instagram/Facebook e o editor de automações por keyword/follow/IA; a UI informa que a Meta API aguarda conexão;
- valores monetários usam `R$ 1.000.000,00`, sempre com duas casas decimais, sem notação compacta.
- agenda registra no-show manual, comparativos, links Meet/Teams, hover contextual, briefing de IA, disponibilidade, booking vertical e associação posterior de reuniões órfãs;
- Chatwoot sincroniza somente threads externas e mensagens de cliente, verifica o banco por leitura autenticada e reconcilia sem duplicar;
- BottleCRM usa lock de sincronização, vínculo único de organização, commit atômico e recuperação de registros externos apagados ou parcialmente criados; duas execuções consecutivas mantêm as mesmas entidades;
- Rocket.Chat usa dez canais e salas por caso, sincroniza mensagens internas nos dois sentidos e rejeita o mesmo evento com payload divergente;
- as quatro interfaces completas abrem com bilhete HMAC curto, preso ao workspace local e consumido em ledger durável com lock entre processos; replay após concorrência ou restart é recusado;
- a reconciliação Chatwoot possui lock de processo e de arquivo para impedir criação concorrente de contatos, conversas ou mensagens;
- o BottleCRM interrompe a projeção antes de qualquer efeito externo quando uma demanda ativa referencia cliente excluído, retornando erro de integridade em vez de `500`;
- o E2E de abertura completa comprova cookies de sessão ou token de retomada específico de cada produto, e o teste de teclado valida o anúncio efetivo `aria-live` após a movimentação;
- a suíte Playwright usa um worker porque os projetos desktop/mobile alteram e restauram o mesmo workspace SQLite; o teste do funil descobre o próximo estágio pelo contrato, sem depender de um card fixo;
- comentários sociais abrem a thread escolhida no inbox visível, expõem o modo `Atendimento` também em mobile e limpam a seleção incompatível ao trocar Instagram/Facebook;
- a ingestão de e-mail usa receipt transacional por workspace e `messageId`, rejeita payload divergente, materializa anexos/RAG uma única vez sob concorrência real e recupera mensagens legadas sem segundo card;
- o inbound WhatsApp usa lease renovável com geração de fencing; worker vencido não grava checkpoint e envio automático incerto fica em `reconciliation_required` sem repetição cega;
- eventos Stripe reconhecidos sem assinatura local retornam falha temporária para reentrega; empates de timestamp preservam o estado mais restritivo e cancelamento não regride para ativo;
- revogação automática de token da API pública é confirmada antes da resposta de rejeição e sobrevive ao rollback do request;
- `public_api_mutation_receipts`, `public_api_request_logs` e `klm_email_ingest_receipts` entraram no inventário RLS direto, totalizando 55 tabelas auditadas;
- registros com marcadores exclusivos de QA foram removidos do SQLite em transação com chaves estrangeiras ativas e `integrity_check=ok`.
- Conversas ganhou Caso 360 completo no drawer lateral, incluindo arquivos, proposta/contrato, tratamento, honorários, atividade interna e links contextuais para a SPA;
- a agenda aberta pela conversa carrega disponibilidade, duração, fuso, sala on-line e vínculo ao card antes de confirmar;
- a cadência de mensagem usa atraso humano configurável, indicador `digitando`, variação, pausa entre trechos e limite de envio sem acionar transporte externo;
- status OSS e contexto ChatterMate usam cache curto, single-flight, cópias defensivas e invalidação pós-escrita, sem compartilhar snapshots entre workspaces;
- o login ChatterMate é reutilizado por chave de origem/usuário/hash de credencial e sobrevive ao cancelamento do consumidor sem cancelar o login dos demais;
- uma geração monotônica impede que um contexto ChatterMate invalidado durante o carregamento volte ao cache depois da escrita concorrente;
- a rodada final do gateway ChatterMate registrou zero respostas `499`, e o E2E completo caiu de cerca de 5,8 para 4,8 minutos;
- o catálogo CMED de 10/06/2026, ciclos de tratamento e política interna de honorários foram incorporados ao card com revisão humana obrigatória;
- o laudo reproduzível da rodada está em `docs/QA_TOTAL_2026-07-17.md`.

### 6.2 Security Closure R10

- WebSocket usa ticket de 60 segundos e uso único no subprotocolo, valida origem, usuário, workspace e membership ativos e transmite somente dentro do workspace;
- o ticket WebSocket não aparece na URL nem nos access logs, e suspensão ou mudança de permissão encerra a conexão ativa;
- tokens da API pública exigem escopos explícitos, herdam as permissões atuais do criador e são revogados quando o criador perde acesso ou é suspenso;
- `lead_data:view` e `lead_data:edit` foram separados; busca, ordenação e cache da SPA também respeitam revogação ao vivo;
- documentos privados persistem somente a chave do objeto e exigem autorização para gerar URL temporária de download;
- o modo local usa armazenamento privado em `.local/object-storage`, com ticket curto, limite de 25 MB, validação de tipo, proteção contra path traversal e `Cache-Control: private, no-store`;
- a futura ativação AWS falha no startup se o bucket privado não bloquear ACLs e políticas públicas, mantendo mídia pública em bucket separado;
- falha de pré-assinatura S3 retorna `503`; nenhuma URL fictícia ou pública é usada como fallback;
- o access token fica apenas em memória/sessionStorage e o refresh token em cookie `HttpOnly`; rotação consome o JTI uma única vez, logout revoga refresh e corrida tardia não restaura sessão encerrada;
- backend e frontend aplicam CSP, `nosniff`, `DENY`, Referrer Policy, Permissions Policy e `no-store`;
- o rollback integral remove os 16 enums da migration inicial, e a linha Alembic converge no head único `94e5f6a7b8c9` após os vínculos únicos dos motores OSS, agenda e catálogo CMED;
- execução real do backend sobre PostgreSQL validou RLS, papel runtime e isolamento de 55 tabelas.

Limites futuros explícitos: em produção multiprocesso, Redis é dependência operacional para fan-out e marcadores duráveis de revogação WebSocket; a conexão também revalida membership e sessão persistida antes de cada broadcast. Alertas operacionais externos são hoje pós-commit e best-effort; antes de habilitar e-mail ou WhatsApp reais, a fase online deverá adicionar outbox persistente com retry/reconciliação ou registrar aceitação humana desse risco. O gate local fechado impede perda ou envio externo nesta entrega.

Dívida técnica observada, fora do escopo desta correção: o baseline Ruff do repositório completo ainda contém erros históricos de estilo em módulos legados e migrations geradas. Nenhum autofix massivo foi aplicado para evitar churn e alterações comportamentais fora desta rodada.

## 7. Como abrir localmente

Inicialização persistente recomendada no macOS:

```bash
cd /Users/kleistr.monteiro/Documents/Playground/klm-legal-hub-backend-real
./scripts/start_klm_local.sh
```

O comando mantém SPA e API ativas entre tarefas locais. Para encerrar:

```bash
./scripts/stop_klm_local.sh
```

Inicialização manual do backend:

```bash
cd /Users/kleistr.monteiro/Documents/Playground/klm-legal-hub-backend-real
python3 scripts/run_local.py --port 8010
```

Verificação HTTP local, em outro terminal:

```bash
cd /Users/kleistr.monteiro/Documents/Playground/klm-legal-hub-backend-real
python3 scripts/verify_klm_local.py --base-url http://127.0.0.1:8010
```

Frontend, em outro terminal:

```bash
cd /Users/kleistr.monteiro/Documents/Playground/klm-legal-hub-backend-real/frontend
npm run dev -- --host 127.0.0.1 --port 4173
```

Suítes do frontend:

```bash
npm test
npm run typecheck
npm run lint
npm run build
npm run test:e2e
```

Acessos:

- SPA: `http://127.0.0.1:4173/app`
- API: `http://127.0.0.1:8010`
- Swagger: `http://127.0.0.1:8010/docs`
- estado dos gates: `http://127.0.0.1:8010/api/v1/klm/runtime`

Não é necessário fornecer chave ou senha de provedor para esta revisão.

## 8. Roteiro de aprovação

1. Revisar a Home e os KPIs.
2. Abrir Conversas e verificar autoria, takeover humano, ferramentas e conversa interna.
3. Abrir Caso 360 e revisar documentos, subtema e projeções de honorários.
4. Abrir Funil, Rastreamento, Qualidade e Flows.
5. Em Agentes, executar a simulação do time e conferir runs, Hermes e fila de aprovação.
6. Revisar Agenda, Contratos, Financeiro, Petições e Omnichannel.
7. Conferir mobile, tema escuro, Áreas plug and play e TI Agent.
8. Em Configurações, revisar criação, suspensão e níveis oculto, leitura e edição dos dados dos leads.
9. Revogar o acesso sensível de um usuário com a SPA aberta e confirmar a limpeza imediata da tela.
10. Aprovar ou listar ajustes locais. Nenhuma integração será iniciada sem a aprovação explícita.

## 9. Acessos que serão pedidos somente depois da aprovação

| Integração futura | Dados/acessos esperados | Estado atual |
|---|---|---|
| APIs de modelos | provider(s), chaves em secret manager, modelos permitidos, catálogo de preços, limites e orçamento | gateway pronto; offline |
| WhatsApp Business Cloud / Meta | app, WABA, número, token de system user, webhook e templates | offline |
| E-mail de entrada | provedor aprovado, caixa, domínio/DNS, OAuth ou credencial de serviço e webhook | offline |
| ZapSign | token, templates de contrato/procuração e segredo de webhook | offline |
| Google Workspace | OAuth ou service account, Drive/pastas, Docs e Calendar/Meet | offline |
| ElevenLabs | API key, voz, modelo e política de uso após insistência por áudio | offline |
| Google Ads | conta, developer token, conversões e política de atribuição | offline |
| Meta Ads | business, ad account, pixel/dataset, CAPI e token | offline |
| TikTok Ads | advertiser, pixel/events API e token | offline |
| Asaas ou financeiro escolhido | conta, API key, webhook e política de cobrança | offline |
| AWS e domínio | conta, região, IAM, rede, DNS, certificados, RDS, Redis, S3, KMS, Secrets Manager, observabilidade e orçamento | não criado |

As credenciais não devem ser colocadas em chat, documentação ou Git. Depois da aprovação, elas serão recebidas por mecanismo seguro e armazenadas em secret store apropriado.

## 10. Limite exato da entrega

Esta entrega comprova o produto local, seus contratos, persistência, UX e governança de execução. Ela não afirma ainda:

- integração real com qualquer provedor;
- qualidade de modelo LLM externo;
- entrega de mensagens em canais reais;
- SSO federado ou private app de produção; nesta entrega existem somente sessões locais assinadas, curtas e restritas a loopback;
- incorporação de cada função upstream dentro da SPA KLM; as interfaces completas dos quatro produtos abrem pelos sidecars e apenas os dados jurídicos selecionados são projetados no KLM;
- execução via Temporal; a durabilidade local atual usa revisões, runs, snapshots, locks e estados persistidos no banco KLM;
- aplicação da migration em um PostgreSQL de produção; a prova realizada foi local e transitória;
- verificação ao vivo das políticas, criptografia, versionamento e bloqueio público dos buckets AWS;
- MFA, convite seguro, recuperação de conta e gestão central de sessões para o ambiente online;
- desempenho sob carga de produção;
- comportamento com Redis/Celery e provedores reais sob carga;
- infraestrutura AWS criada ou validada;
- `production_verified`.

Esses itens formam a fase seguinte e só começam depois da aprovação desta entrega local.

## 11. Registro de decisão financeira - 2026-07-22

- **Status:** PAUSADO.
- **Gatilho exato para reabrir:** **destravar upgrade de funções financeiras**.
- **Escopo preservado:** lançamentos, recebíveis, despesas, estornos, vencimentos,
  lembretes, filtros, indicadores, projeções e registros locais de integração.
- **Escopo congelado:** onboarding externo do Asaas, webhooks financeiros, cobrança
  real, boleto, Pix, cartão, split, reconciliação automática e upgrade financeiro
  nativo/avançado.
- **Rotas futuras mantidas:** Asaas direto, Pluga ou Zapier.
- **Efeito externo autorizado nesta decisão:** nenhum.
- **Credenciais solicitadas ou usadas:** nenhuma.
- **Fonte detalhada:**
  [PLANO_UPGRADE_FINANCEIRO_PAUSADO_2026-07-22.md](./PLANO_UPGRADE_FINANCEIRO_PAUSADO_2026-07-22.md).

Este registro pertence somente à memória local do repositório. Ele não foi
replicado em Obsidian, memória global ou serviço externo e não comprova conexão
financeira real.
