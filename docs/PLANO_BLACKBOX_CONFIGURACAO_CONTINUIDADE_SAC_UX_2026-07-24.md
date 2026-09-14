# Plano de execução — Blackbox, configuração assistida, continuidade e SAC

## Meta

Concluir localmente o plano de controle do Meu Executivo para que:

1. o `/blackbox` seja o ambiente exclusivo do titular e dos funcionários do
   SaaS, com painel e fila próprios para cada função;
2. funcionários autorizados consigam configurar agentes, fluxos e a experiência
   de uma conta por uma interface assistida por IA, sem precisar programar;
3. ausências, férias, desligamentos e substituições sejam tratados por
   delegação temporária ou transferência definitiva, com escopo, etiqueta,
   aprovação e auditoria;
4. o `/app` permaneça exclusivo do escritório contratante e nunca aceite uma
   sessão do Blackbox;
5. os ajustes já marcados em Conversas e o SAC em tempo real sejam concluídos
   sem remover funcionalidades;
6. toda ação visível tenha contrato real com backend, testes e revisão
   independente;
7. nada seja publicado na AWS nesta fase.

Este plano complementa o `PLANO_MESTRE_2026-07-24.md`. Ele não substitui os
gates de fundação, segurança e Flow Studio definidos ali.

---

## 1. Decisões de arquitetura

### 1.1 Duas superfícies, duas identidades

| Superfície | Runtime local | Rota | Usuários |
| --- | --- | --- | --- |
| Aplicativo contratado | porta `4173` | `/app` | Dono e funcionários do escritório |
| Backoffice do SaaS | porta `4174` | `/blackbox` | Titular e funcionários do Meu Executivo |
| Acesso assistido | runtime Blackbox | `/blackbox/support-session` | Funcionário autorizado, com concessão temporária |

Regras obrigatórias:

- O `/blackbox` não redireciona para `/app`.
- O `/app` não contém rota, menu, import ou bundle do Blackbox.
- Identidades `platform` não recebem membership de workspace.
- Identidades `workspace` não recebem função interna do SaaS.
- Cookies, audiência dos tokens, origem e renovação são distintos.
- `/api/v1/platform/**` exige identidade `platform`.
- APIs de workspace exigem identidade `workspace` e contexto de tenant.
- Um funcionário do SaaS só consulta dados reais de uma conta pela sessão
  temporária e auditada de acesso assistido.
- Pré-visualizações comuns usam dados sintéticos. Elas não impersonam cliente.

### 1.2 Plano de controle e plano de dados

O Blackbox será o plano de controle. Ele cria templates, rascunhos, aprovações,
publicações e reversões. Os dados jurídicos, conversas e documentos continuam
no plano de dados de cada workspace.

O Blackbox não escreverá diretamente nas tabelas de um cliente. Uma publicação
gera um comando versionado, idempotente e auditado para o serviço canônico de
configuração aplicar ao workspace de destino.

### 1.3 Um motor para cada capacidade

- Agentes: evoluir o motor nativo e sua governança atual.
- Flows: reutilizar o registro de nós, revisões, validação, dry-run e runtime
  existentes.
- SAC: reutilizar o núcleo canônico `SupportTicket`.
- Disponibilidade: reutilizar perfis, exceções, aprovações e auditoria atuais.
- Copilotos: reutilizar os serviços de copiloto operacional e de flows, com um
  contrato comum de proposta, validação, aprovação e aplicação.

Não serão criados editores, tabelas ou filas paralelas.

### 1.4 Vibecoding governado

Para o suporte, a experiência será semelhante a vibecoding: a pessoa descreve
o resultado desejado e a IA prepara a configuração. Entretanto:

- não haverá execução de código arbitrário;
- a saída da IA será configuração estruturada validada por schema;
- a IA nunca publica diretamente;
- qualquer efeito externo passará por dry-run, diff e aprovação;
- campos avançados ficarão ocultos por padrão;
- o modo técnico será restrito ao titular, Produto e Engenharia com permissão;
- toda publicação terá revisão, hash, autor, aprovador e rollback.

---

## 2. Blackbox por função

### 2.1 Home e filas

Cada função terá uma home própria dentro do shell Blackbox:

| Função | Home principal | Dados permitidos |
| --- | --- | --- |
| Titular | Visão executiva | Toda a plataforma e controles raiz |
| Administração | Operação da plataforma | Módulos delegados pelo titular |
| SDR | Comercial | Leads e oportunidades do próprio SaaS |
| Gestão comercial | Gestão comercial | Equipe, metas e distribuição comercial |
| Onboarding | Implantações | Contas em ativação e configuração aprovada |
| Customer Success | Carteira | Saúde, adoção, retenção e expansão |
| Suporte | SAC | Tickets, conversa pública e triagem |
| Suporte técnico | Fila técnica | Tickets técnicos, erros e integrações |
| Financeiro | Financeiro | Assinaturas, cobrança, MRR e ARR |
| Marketing | Conteúdo e aquisição | Site, conteúdo e campanhas do SaaS |
| Comunidade | Comunicação | Avisos e relacionamento |
| Produto | Produto e operações | Feedback, versões e evolução |
| Compliance | Risco e auditoria | Acessos, eventos e evidências |
| Engenharia/TI | Operação técnica | Incidentes, logs, releases e infraestrutura |

Uma função sem permissão não verá o módulo e receberá `403` no backend se tentar
abri-lo diretamente. O titular poderá conceder ou negar permissões adicionais
sem transformar o funcionário em administrador raiz.

### 2.2 Novos módulos de autorização

Adicionar ao catálogo de RBAC:

- `workspace_configuration`
- `agent_templates`
- `flow_templates`
- `experience_customization`
- `configuration_deployments`
- `staff_reassignment`

Ações:

- `view`
- `propose`
- `test`
- `approve`
- `deploy`
- `rollback`
- `manage`
- `export`, quando aplicável

Permissões iniciais:

- Titular: todas.
- Administrador: todas as delegadas.
- Onboarding: visualizar, propor e testar; publicar somente se concedido.
- Suporte: visualizar e propor; sem publicação por padrão.
- Suporte técnico: visualizar, propor e testar; sem publicação por padrão.
- Produto: gerir templates e testar; aprovação/publicação conforme concessão.
- Engenharia: testar, diagnosticar e fazer rollback técnico conforme concessão.
- Demais funções: sem acesso até concessão explícita.

---

## 3. Studio de configuração assistida

### 3.1 Navegação

Criar no Blackbox a área **Configuração de contas**, com:

- Visão geral.
- Agentes.
- Fluxos.
- Experiência da conta.
- Publicações.
- Histórico e reversões.

A seleção da conta será obrigatória e permanecerá visível. Trocar de conta
descarta ou salva o rascunho, nunca reaproveita silenciosamente a configuração
de outro workspace.

### 3.2 Jornada principal para suporte não técnico

1. Escolher a conta.
2. Selecionar “Agente”, “Fluxo” ou “Experiência”.
3. Partir de um template ou escolher “Descrever o que preciso”.
4. Conversar com o copiloto em linguagem natural.
5. A IA faz perguntas curtas sobre objetivo, público, área, produto, canal,
   autonomia e resultado esperado.
6. A IA monta um rascunho estruturado.
7. A tela mostra lado a lado:
   - o que existe hoje;
   - o que será alterado;
   - quem será afetado;
   - riscos e bloqueios;
   - consumo estimado de tokens e canais;
   - integrações ou aprovações necessárias.
8. Executar validação e simulação com dados sintéticos.
9. Corrigir erros com um clique ou pedir à IA para corrigir.
10. Enviar para aprovação.
11. Publicar agora, agendar ou cancelar.
12. Acompanhar aplicação por progresso e reverter se necessário.

### 3.3 Regras de UX

- Linguagem simples e em português.
- Ajuda contextual em todos os campos.
- Modelos prontos por objetivo.
- Modo guiado como padrão e modo avançado opcional.
- Nenhum JSON exposto no modo guiado.
- Preview desktop e mobile.
- Rascunho persistido e recuperável.
- Diff em linguagem humana e diff técnico para funções autorizadas.
- Botões claros: `Testar`, `Pedir aprovação`, `Publicar`, `Agendar`, `Reverter`.
- Zero botão sem backend real.
- Conflito de edição mostra as duas versões e não sobrescreve silenciosamente.
- Publicação parcial ou falha permanece visível, com retry idempotente.

### 3.4 Agent Studio

Campos guiados:

- nome e objetivo;
- função e posição na hierarquia;
- área, subárea, produto e público;
- tom, formalidade e estilo do escritório;
- playbooks, FAQ, documentos e fontes autorizadas;
- canais em que pode atuar;
- ferramentas que pode usar;
- ações permitidas e proibidas;
- condições de handoff;
- quando o orquestrador deve chamar um atendente humano;
- autonomia para fechar sozinho ou obrigação de reunião;
- gates jurídicos, financeiros e reputacionais;
- modelo/tier, limite de tokens, latência e horário;
- cenários de teste e respostas esperadas.

Proteções:

- agentes canônicos protegidos não terão hierarquia ou guardrails afrouxados;
- agentes personalizados serão filhos de um nó permitido;
- profundidade e delegação continuam limitadas;
- Hermes não participa do fluxo de conversas;
- Hermes continua sendo a ponte informativa e operacional com o dono do
  escritório, sem assumir decisões;
- somente o orquestrador coordena agentes da operação e chama o atendente
  humano;
- publicar exige validação da governança agentic e das áreas liberadas no plano.

### 3.5 Flow Studio no Blackbox

O Blackbox usará o mesmo registro de nós e o mesmo runtime do `/app`.

Capacidades:

- criar template global ou configuração específica de uma conta;
- pedir um fluxo ao copiloto por texto, imagem ou documento;
- criar e alterar o grafo pelo canvas;
- validar portas, schemas, caminhos inalcançáveis, ciclos e efeitos externos;
- simular conversa, lead, comentário, documento, agendamento e pagamento;
- testar janelas e limitações dos canais;
- comparar revisões;
- publicar, agendar, pausar e reverter;
- acompanhar execução e erros sem abrir dados privados desnecessários.

A IA pode preparar o grafo, sugerir melhorias e enfileirar alterações, mas a
publicação permanece humana.

### 3.6 Experience Studio

Configurações:

- marca, logo, cores e tipografia;
- tom e terminologia do escritório;
- áreas, subáreas e produtos habilitados;
- formulários e onboarding;
- modelos de proposta, contrato e documentos;
- campos e ações rápidas de Conversas;
- labels, etapas, motivos de ganho/perda e prioridades;
- widgets e KPIs da home;
- notificações, horários e SLAs;
- suporte e canais de contato;
- políticas de reunião, fechamento e handoff;
- experiência desktop e mobile.

O preview usa dados sintéticos. Uma alteração de experiência não pode liberar
área, canal, integração ou recurso fora do plano da conta.

### 3.7 Objetos canônicos

Criar ou consolidar:

- `PlatformConfigurationTemplate`
- `PlatformConfigurationRevision`
- `WorkspaceConfigurationDraft`
- `ConfigurationValidationReport`
- `ConfigurationApproval`
- `ConfigurationDeployment`
- `ConfigurationDeploymentEvent`

Todo deployment terá:

- workspace de destino;
- tipo e revisão da configuração;
- hash do conteúdo;
- autor e aprovador;
- motivo e ticket relacionado;
- estado;
- idempotency key;
- horário planejado, aplicado ou revertido;
- referência à publicação anterior para rollback.

---

## 4. Continuidade de funcionários

### 4.1 Duas operações distintas

**Delegação temporária**

- mantém o responsável original;
- adiciona uma cobertura com início e fim;
- encaminha filas e tarefas elegíveis ao substituto;
- etiqueta os itens como `Cobertura de {nome} até {data}`;
- termina automaticamente;
- restaura a roteabilidade normal ao final.

**Transferência definitiva**

- altera o responsável operacional atual;
- preserva autor, criador, assinante e titularidade probatória;
- etiqueta como `Transferido de {nome} em {data}`;
- mantém o histórico imutável;
- não reativa nem compartilha credenciais do funcionário anterior.

### 4.2 Wizard de continuidade

Disponível em:

- `/app/configuracoes/equipe/continuidade`
- `/blackbox/team/continuity`

Etapas:

1. Funcionário de origem.
2. Motivo: doença, férias, licença, desligamento ou outro.
3. Temporária ou definitiva.
4. Substituto.
5. Período, quando temporária.
6. Escopo: tudo elegível ou seleção por equipe, área, fila e tipo de objeto.
7. Prévia com quantidade de objetos, conflitos, itens não transferíveis e
   capacidade do substituto.
8. Aprovação.
9. Execução com progresso, falhas e retry.
10. Relatório e auditoria.

### 4.3 Escopo no `/app`

Elegíveis:

- conversas e filas;
- leads, cards e casos;
- tarefas e follow-ups;
- reuniões e responsabilidades de agenda;
- aprovações pendentes;
- carteira e equipe;
- propriedade operacional de flows e rascunhos.

Não são reescritos:

- autoria de mensagens e notas;
- evidências, documentos e assinaturas;
- trilhas jurídicas;
- credenciais, MFA, tokens e conexões pessoais.

### 4.4 Escopo no `/blackbox`

Elegíveis:

- tickets e filas de suporte;
- oportunidades comerciais;
- contas de onboarding e CS;
- tarefas, aprovações e follow-ups;
- incidentes, erros e investigações;
- conteúdos e publicações atribuídas;
- rascunhos de configuração.

A transferência de um item do Blackbox nunca concede acesso ao workspace do
cliente. Acesso assistido continua sendo outra autorização.

### 4.5 Permissões e segurança

- No `/app`: owner/admin ou gestor da área com
  `staff_reassignment:manage` e escopo da equipe.
- No `/blackbox`: titular ou gestor da área com
  `staff_reassignment:manage`.
- Transferência definitiva e desligamento exigem autenticação reforçada.
- Operações acima de limite configurável exigem maker-checker.
- Desligamento revoga sessões antes da transferência.
- O substituto não pode estar inativo, afastado ou fora da equipe permitida.
- Toda leitura, simulação, aprovação e mudança é auditada.

### 4.6 Modelo e processamento

Objetos:

- `ReassignmentPlan`
- `ReassignmentPlanItem`
- `DelegationOverlay`
- `AssignmentEvent`

O executor será assíncrono, por adaptadores de domínio, idempotente e
retomável. Não haverá uma transação gigante. Falhas parciais ficam visíveis e
podem ser repetidas sem duplicar etiquetas ou eventos.

---

## 5. Conversas — ajustes locais obrigatórios

O menu lateral esquerdo permanece intocado.

### 5.1 Lista de atendimentos

Cada card exibirá somente:

- foto real do canal, priorizando WhatsApp quando disponível;
- nome;
- área do direito;
- subnicho;
- produto;
- potencial de zero a cinco estrelas;
- sino verde, amarelo ou vermelho.

O card não exibirá tempo decorrido nem última mensagem. O tempo completo será
mostrado apenas ao abrir a conversa.

### 5.2 Indicadores superiores

- Remover `Dados persistidos` da interface do cliente.
- Evitar indicador duplicado dentro da lista.
- `Respostas atrasadas`, `Cliente aguardando escritório` e `Follow-ups` serão
  botões com dropdown contendo os itens correspondentes.
- Cada item do dropdown abre a conversa sem perder o contexto.

### 5.3 Conversa como elemento principal

- Compactar cabeçalho e controles.
- Garantir que nome e produto tenham prioridade tipográfica e não desapareçam.
- Ampliar a timeline e remover espaços vazios sem função.
- Manter ações contextuais em popup ou lâmina, sem trocar de página.
- Manter assistente de IA dentro do composer.
- `@pessoa` ou `@agente` transforma a composição em nota interna e cria a
  atribuição ou tarefa correspondente, conforme permissão.

### 5.4 Edição e operação

- Caso 360 terá edição por duplo clique onde permitido.
- Campos usam opções conhecidas e aceitam personalizado quando a política
  permitir.
- Toda edição registra auditoria e usa controle otimista de versão.
- Follow-up será clicável e editável.
- Fora da janela de atendimento do canal, preparar envio abrirá confirmação
  explicando a regra aplicável e o uso de template; nada será enviado
  silenciosamente.

---

## 6. SAC em tempo real

### 6.1 Aplicativo do cliente

- Botão flutuante `Ajuda e suporte` em todas as páginas.
- Chat em tempo real durante horário configurado.
- Fora do horário, abertura de ticket com previsão.
- `Meus chamados` com status, responsável, mensagens, anexos, reabertura e
  avaliação.
- Offline queue idempotente; só mostrar `enviado` após confirmação do backend.
- Diagnóstico técnico sanitizado, sem tokens, conteúdo jurídico ou corpos de
  requisição.

### 6.2 Blackbox

O módulo `SAC e tickets` será a mesa omnichannel dos funcionários:

- fila, lista, kanban e indicadores;
- conversa pública e nota interna;
- distribuição por suporte ou TI;
- transferência de responsável/equipe;
- SLA, severidade, conta, versão, erro e incidente;
- filtros salvos e personalizados;
- presença e disponibilidade dos atendentes;
- chat ao vivo e conversão em ticket;
- acesso assistido separado, temporário e justificado.

O antigo fluxo técnico continuará como adaptador do mesmo `SupportTicket`; não
haverá segundo CRM de suporte.

### 6.3 Tempo real

- WebSocket da plataforma separado dos canais dos workspaces.
- Eventos são sinais; após reconexão o frontend busca o estado canônico.
- Nenhum evento cruza conta, superfície ou função.
- Falha de entrega, ticket sem responsável e SLA vencido geram alerta.

---

## 7. APIs e contratos frontend-backend

### 7.1 Configuração

Endpoints de plataforma:

- templates e revisões;
- rascunhos por workspace;
- validação e simulação;
- aprovações;
- deployments, progresso e rollback;
- histórico e auditoria.

Toda mutação usa `Idempotency-Key` e controle otimista de versão.

### 7.2 Continuidade

Endpoints separados por superfície:

- criar e simular plano;
- listar itens e conflitos;
- aprovar/rejeitar;
- executar/cancelar;
- acompanhar progresso;
- retry de falhas;
- encerrar delegação;
- consultar auditoria.

### 7.3 Matriz de paridade

Antes de implementar tela, manter uma matriz:

| Ação de UI | Endpoint | Permissão | Evento | Teste |
| --- | --- | --- | --- | --- |

Nenhum botão entra na entrega sem os cinco elementos. Capacidades de backend
sem ponto de entrada intencional serão registradas como internas, e não
aparecerão como falsa promessa na UI.

---

## 8. Sequência de execução

### Fase 0 — Fundação e contratos

- Fechar a árvore concorrente e os gates do plano mestre.
- Inventariar telas, APIs e permissões existentes.
- Congelar schemas e eventos canônicos.
- Criar a matriz de paridade.

**Gate:** árvore controlada, suites atuais verdes e nenhuma duplicação de motor.

### Fase 1 — Isolamento e RBAC

- Completar módulos e ações do Blackbox.
- Testar rotas, bundles, cookies, audiência e APIs.
- Criar homes e filas por função.
- Expor Engenharia/TI e Suporte Técnico como áreas próprias.

**Gate:** nenhum token ou usuário atravessa `/app` e `/blackbox`.

### Fase 2 — Domínio de configuração

- Modelos, revisões, drafts, validações, aprovações e deployments.
- Serviço de aplicação idempotente no workspace.
- Rollback e histórico.

**Gate:** publicar, repetir e reverter uma configuração de teste sem duplicação.

### Fase 3 — Studios Blackbox

- Fundação do Studio.
- Agent Studio.
- Flow Studio.
- Experience Studio.
- Copiloto comum e modo guiado.

**Gate:** suporte não técnico cria um rascunho completo, testa e pede aprovação
sem editar JSON ou acessar dados do cliente.

### Fase 4 — Continuidade

- Modelos e executor por adaptadores.
- Wizard `/app`.
- Wizard `/blackbox`.
- Etiquetas, progresso, retry e encerramento.

**Gate:** delegação temporária e transferência definitiva provadas nas duas
superfícies, sem reescrever autoria ou credenciais.

### Fase 5 — Conversas

- Aplicar todos os ajustes da seção 5.
- Contract tests das ações.
- Responsividade desktop e mobile.

**Gate:** conversa é o elemento principal, cards seguem o contrato solicitado e
nenhuma ação contextual abandona a tela.

### Fase 6 — SAC

- Consolidar chat, tickets, filas e painel omnichannel.
- Fechar tempo real, offline, notificações e SLAs.
- Integrar disponibilidade e substituição dos atendentes.

**Gate:** cliente abre e acompanha; suporte responde e transfere; reconexão não
perde nem duplica mensagens.

### Fase 7 — QA e revisão independente

- Suites unitárias, integração, contrato, E2E e segurança.
- Teste visual e acessibilidade.
- Revisão independente de arquitetura, segurança e UX.
- Correção de todos os P0/P1 e reexecução integral.

**Gate:** apto para aprovação local do titular. AWS e APIs externas continuam
bloqueadas.

---

## 9. Testes obrigatórios

### 9.1 Isolamento

- token workspace em API Blackbox;
- token platform em API de workspace;
- funcionário abrindo painel de outra função;
- suporte sem concessão;
- concessão expirada e cross-workspace;
- bundle contendo import da outra superfície.

### 9.2 Configuração

- revisão imutável;
- isolamento de overlays;
- dry-run sem efeitos;
- aprovação, publicação, retry e rollback;
- bloqueio por plano, área congelada, integração ou canal ausente;
- agente tentando violar hierarquia, gate humano ou papel do Hermes;
- duas pessoas editando o mesmo draft.

### 9.3 Continuidade

- temporária não altera owner original;
- término automático;
- definitiva muda responsabilidade e preserva autoria;
- etiquetas e eventos únicos;
- retry sem duplicação;
- gestor limitado à própria área;
- substituto inativo ou afastado;
- desligamento revoga sessão antes;
- isolamento entre workspaces e superfícies.

### 9.4 Conversas

- cards, sinos, potencial, foto e ausência da última mensagem;
- dropdowns dos indicadores;
- duplo clique e autorização;
- follow-up editável;
- confirmação fora da janela do canal;
- `@` como interno;
- nenhum overflow, truncamento destrutivo ou espaço vazio;
- desktop e mobile.

### 9.5 SAC

- chat ao vivo, ticket e fallback fora do horário;
- nota interna nunca visível ao cliente;
- anexos e diagnóstico sanitizado;
- offline, timeout, duplo clique, retry e duas abas;
- WebSocket reconnect;
- IDOR e vazamento cross-tenant;
- transferência durante ausência;
- SLA e alertas.

---

## 10. Critérios de conclusão

A entrega local somente poderá ser considerada concluída quando:

- `/app` e `/blackbox` forem artefatos e superfícies efetivamente isolados;
- cada funcionário do SaaS tiver somente o painel e as filas de sua função;
- o titular controlar permissões e publicações;
- suporte não técnico conseguir propor agente, flow e experiência por linguagem
  natural, testar e pedir aprovação;
- nenhum copiloto publicar sozinho;
- toda configuração puder ser revertida;
- delegação temporária e transferência definitiva funcionarem nos dois lados;
- Conversas atender a todos os ajustes marcados;
- SAC em tempo real usar um único núcleo de tickets;
- cada ação visível possuir API, permissão, evento e teste;
- não houver botão morto, placeholder ou nome de sistema externo;
- suites locais estiverem verdes;
- revisão independente não encontrar P0 ou P1 aberto;
- nenhuma publicação na AWS ou ativação de API externa tiver ocorrido.

## 11. Riscos residuais e limites

- “Todos os dados” significa responsabilidade operacional sobre os objetos
  elegíveis. Autoria, evidência, assinatura, credencial e histórico nunca são
  transferidos.
- Preview comum não usa dados reais. Acesso real depende da concessão assistida.
- A IA reduz o trabalho técnico, mas não substitui aprovação de publicação.
- A estrutura local pode provar isolamento de aplicação e contrato. DNS, WAF,
  provedor de identidade, MFA e observabilidade de produção serão gates
  posteriores, antes do go-live.
