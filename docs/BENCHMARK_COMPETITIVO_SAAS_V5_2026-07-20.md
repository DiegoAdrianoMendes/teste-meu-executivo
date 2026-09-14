# Benchmark competitivo KLM Hub SaaS Local V5

**Data da pesquisa:** 20 de julho de 2026  
**Escopo:** produto local, sem APIs externas e sem AWS  
**Regra de leitura:** recursos de terceiros abaixo são alegações públicas dos
fornecedores. Recursos do KLM só contam quando possuem contrato local, teste e
evidência ligada ao snapshot do código.

## 1. Método

A comparação usa páginas oficiais e repositórios oficiais consultados em
20/07/2026. Foram avaliadas jornadas, não apenas listas de funcionalidades:
aquisição, qualificação, atendimento, colaboração, CRM, agenda, proposta,
automação, execução por agentes, governança, analytics e operação jurídica.

Escala usada:

- `comprovado-local`: implementado e exercitado no KLM sem efeito externo;
- `simulado-local`: jornada funcional com adapter falso e gate de rede fechado;
- `declarado-fornecedor`: informação comercial ou documental do fornecedor;
- `não-homologado`: depende de provider, credencial, produção ou validação futura.

Este documento não afirma que o KLM é superior ao mercado. Ele identifica onde o
produto local já tem diferenciação verificável e onde concorrentes maduros ainda
definem o patamar de homologação.

## 2. Referências auditadas

| Referência | Sinal público relevante | Fonte oficial |
|---|---|---|
| AdvHub | atendimento com IA no WhatsApp, qualificação, contratos, agenda, funil e indicadores | [AdvHub](https://www.advhub.ai/) |
| SabioAdv | WhatsApp oficial, CRM, agenda Google/Outlook, lembretes e reunião on-line | [SabioAdv](https://sabioadv.com.br/) |
| Guimoo | múltiplos agentes e números, oferta, contrato, documentos, agenda e intervenção humana | [Guimoo](https://www.guimoo.com.br/) |
| WaLeads | CRM no WhatsApp, mensagens agendadas, funis, automação, IA e múltiplos pipelines | [WaLeads](https://waleads.com.br/) e [WaLeads Update](https://novo.waleads.com.br/update) |
| ManyChat | automação visual, comentário para DM, captura, qualificação, broadcast e follow-up | [Instagram](https://manychat.com/product/instagram) e [WhatsApp](https://manychat.com/product/whatsapp) |
| Chatwoot | inbox omnichannel, notas privadas, times, atribuição, SLA, RBAC, relatórios e self-host | [Features](https://www.chatwoot.com/features) |
| ChatterMate | editor drag-and-drop, preview, delays, execução de flows e handoff humano | [Workflow Builder](https://docs.chattermate.chat/features/workflow-builder) |
| BottleCRM | CRM multiempresa, RLS, leads, oportunidades, casos, tarefas, faturas, auditoria, tags e MCP | [Repositório](https://github.com/MicroPyramid/Django-CRM) |
| Rocket.Chat | canais públicos/privados, threads, busca e colaboração self-host | [Channels](https://docs.rocket.chat/docs/channels) e [Threads](https://docs.rocket.chat/docs/threads) |
| Clio | intake dinâmico, conflito/duplicidade, agendamento, pagamento, assinatura, casos, billing e portal | [Legal CRM](https://www.clio.com/features/legal-crm-software/) e [Features](https://www.clio.com/features/) |
| MyCase | intake, pipeline, tarefas, agenda, SMS, ROI de marketing, casos, documentos, cobrança e contabilidade | [Legal CRM](https://www.mycase.com/features/legal-crm-software/) |
| Filevine | case management e automação/IA sobre casos, tarefas, prazos e agenda | [Case Management](https://info.filevine.com/legal-case-management) e [LOIS](https://www.filevine.com/news/filevine-launches-lois-console-ai-that-runs-the-firm/) |

O Rocket.Chat 7+ também possui fronteiras comerciais para determinados cenários
air-gapped e apps privados; nenhuma capacidade de licença incerta é tratada como
copiável pelo KLM. Fonte: [Rocket.Chat 7.0](https://www.rocket.chat/blog/introducing-rocket-chat-7-0).

## 3. Matriz por jornada

| Jornada | Patamar externo observado | Estado KLM local | Leitura competitiva |
|---|---|---|---|
| WhatsApp e omnichannel | AdvHub, SabioAdv, Guimoo e Chatwoot expõem canais reais | inbox unificada, múltiplos canais/números, notas, handoff e adapters locais | experiência nativa avançada; transporte real ainda não homologado |
| Qualificação jurídica | concorrentes jurídicos promovem triagem por IA | playbook versionado, subtemas de saúde, documentos, score, jurídico interno e quatro modos de autonomia | diferenciação vertical relevante, ainda dependente de validação com modelo externo |
| Caso 360 e RAG | legal CRMs maduros centralizam matter e documentos | Card/Caso 360, conversa, documentos, timeline, proveniência e isolamento por workspace/caso | forte no desenho AI-first; produção e volume ainda não provados |
| CRM e funil | BottleCRM, Clio e MyCase têm entidades e pipelines maduros | CRM jurídico canônico, DnD, dedupe, tarefas, oportunidades, auditoria e MCP local | boa cobertura local; conflitos, trust accounting e profundidade financeira seguem como gaps |
| Flows | ManyChat e ChatterMate definem o patamar visual | editor DnD, revisão, preview, execução em tempo real, pausas e convivência com IA | paridade funcional local promissora; canais reais ainda bloqueados |
| Agenda | SabioAdv, Clio e MyCase conectam calendários e booking | calendário, disponibilidade, booking, no-show, Meet/Teams simulado, vínculo com card e briefing IA | jornada completa simulada; sincronização bidirecional real não homologada |
| Proposta e fechamento | Guimoo e legal CRMs integram oferta/assinatura/pagamento | proposta PDF em slides e vertical, honorários, timeline, CTA, versão e gates humanos | verticalização jurídica/saúde é distintiva; assinatura e pagamento permanecem simulados |
| Colaboração interna | Rocket.Chat e Chatwoot oferecem threads, times e notas | canais internos, threads de agentes, menções, notas privadas e promoção explícita ao caso | experiência KLM nativa reduz troca de contexto; escala/presença em produção não provadas |
| Agentes e governança | Filevine e fornecedores AI-first promovem automação agentic | 18 papéis de agentes, policy engine, kill switches, aprovação, telemetria e trilha por playbook | governança local é um ponto forte; fluência e precisão com modelos pagos não homologadas |
| Marketing e tracking | WaLeads/ManyChat medem origem e campanhas | eventos, funis, canais, CAC/CPL/ROAS, remarketing simulado e agente Track/Marketing | cobertura analítica ampla; atribuição com pixels e ads reais ainda não validada |
| Operação SaaS | Chatwoot/BottleCRM têm self-host e tenancy maduros | lifecycle, planos, quotas, RBAC, RLS, white-label, configuração versionada e suporte break-glass | fundação local robusta; billing externo, operação 24x7 e suporte não comprovados |

## 4. Diferenciais locais verificáveis

1. O playbook não é apenas prompt: voz, descoberta, triagem, honorários,
   objeções, cadências, handoffs, autonomia, propostas e experimentos são
   versionados e auditáveis por workspace.
2. A vertical de negativas de plano de saúde combina taxonomia, medicamentos,
   ciclos de tratamento, documentos, urgência e projeções de honorários dentro da
   mesma jornada comercial e jurídica.
3. Card/Caso 360 e conversa são as duas fontes autorizadas do contexto. Notas
   internas, arquivos e dados de outro workspace não entram silenciosamente na
   resposta ao cliente.
4. Ações externas nascem como intenção local idempotente e permanecem bloqueadas
   por policy engine e kill switch nesta fase.
5. As capacidades licenciadas dos upstreams são censadas por `FunctionUnit`, com
   origem, licença, contrato, estado e prova, em vez de serem declaradas incorporadas
   apenas porque existe um sidecar ou uma tela.
6. Custo, tokens, latência, contexto, agente, caso e versão do playbook são
   atribuíveis no gateway comum de IA.

## 5. Gaps que permanecem reais

| Prioridade | Gap | Critério futuro de fechamento |
|---|---|---|
| P0 de homologação | WhatsApp oficial, e-mail, Calendar/Teams, assinatura, pagamento, áudio, Docs/Drive e Ads não foram chamados | sandbox/provider real, webhook assinado, reconciliação, idempotência e aprovação humana |
| P0 de produção | uptime, autoscaling, backup/restore, disaster recovery, observabilidade externa e suporte 24x7 não foram provados | ensaio em ambiente de homologação antes de qualquer produção |
| P1 jurídico-operacional | conflitos, trust accounting, prazos processuais abrangentes e billing jurídico profundo ficam atrás de Clio/MyCase | PRD específico, fontes brasileiras e testes de alto risco |
| P1 IA | precisão, fluência, latência e custo de modelos externos são desconhecidos | avaliação cega por tarefa e provider, com dataset versionado |
| P1 qualidade | existe dívida histórica de lint Python; o gate atual impede regressão, mas não a elimina | reduzir progressivamente o baseline sem esconder a contagem |
| P2 mercado | não há evidência de retenção, NPS, suporte ou operação multiempresa em produção | piloto controlado e métricas reais |
| Licença | 77 unidades do EmbeddedChat permanecem bloqueadas pela licença/escopo | parecer e decisão explícita; não copiar código incerto |

## 6. Posição recomendada

O KLM deve se posicionar como **sistema operacional AI-first para aquisição,
atendimento e execução jurídica de escritórios**, começando por negativas de
plano de saúde. A tese não é "ter mais telas". É ligar marketing, conversa,
playbook, caso, documentos, agentes, proposta, agenda, financeiro e pós-venda em
uma trilha auditável, com autonomia graduável.

No estado V5 local, a afirmação defensável é: **produto amplo e integrado, pronto
para homologação controlada de APIs após o fechamento do P5**. Ainda não são
defensáveis as afirmações "melhor do mercado", "pronto para produção", "sem
falhas" ou "paridade total em canais externos".

## 7. Próxima medição

Após a homologação das APIs, repetir esta matriz usando: tempo mediano até primeira
resposta, taxa de qualificação, show, proposta, assinatura, fechamento, CAC, custo
de IA por lead/cliente, intervenção humana, erro por jornada e tempo operacional.
Somente dados reais de coorte permitirão decidir onde aumentar investimento e
onde o KLM supera alternativas na prática.
