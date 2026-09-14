"""Base de conhecimento verificada — pós-venda e relacionamento com cliente
(régua de comunicação, tradução de juridiquês, gestão de expectativa).

Condensado de `~/.claude/agents/19-follow-up-cliente.md` (dossiê jurídico
verificado do escritório). Fonte de verdade única para os agentes nativos de
pós-venda/relacionamento da KLM — os agentes NUNCA devem inventar cadência,
KPI ou modelo de comunicação que não esteja aqui; itens novos exigem
atualizar primeiro o dossiê de origem em `~/.claude/agents/`.

Este módulo não faz nenhuma chamada de rede: é apenas texto embutido no
`SYSTEM_PROMPT` dos agentes nativos de pós-venda/follow-up (stubs de fase
posterior). Envio real de e-mail/WhatsApp ao cliente é ação externa e passa
pelos mesmos gates (`external_delivery_allowed`, aprovação humana) de todo
agente do sistema — este módulo só fornece o texto de referência.
"""

POS_VENDA_KNOWLEDGE_VERSION = "pos-venda-cliente-knowledge-2026-07"

POS_VENDA_KNOWLEDGE: str = """\
DOSSIÊ PÓS-VENDA E RELACIONAMENTO COM CLIENTE (fonte primária, não inferir além disto).

1. BASE LEGAL E DE ÉTICA
- EAOAB art. 11 e Código de Ética da OAB: manter o cliente informado do \
andamento do seu caso é dever do advogado, não cortesia.
- Técnicas de Customer Success aplicadas ao Direito: a régua de comunicação \
existe para que o cliente nunca precise perguntar "e aí, como está meu \
processo?" — o escritório fala primeiro.

2. RÉGUA DE COMUNICAÇÃO POR TIPO DE CLIENTE
CLIENTE VIP (alto valor / alto risco / referência):
  - E-mail mensal com status detalhado + próximos passos.
  - Reunião mensal, presencial ou por videoconferência.
  - WhatsApp aberto, com resposta em até 4h úteis.
  - Relatório anual customizado.
CLIENTE COMUM (caso simples / valor médio):
  - E-mail trimestral com status executivo.
  - Reunião trimestral por vídeo.
  - WhatsApp reservado para urgências, resposta em até 24h úteis.
CLIENTE BAIXA (pro bono / valor baixo / caso único):
  - E-mail semestral, ou a cada marco relevante do processo.
  - Reunião sob demanda.
  - WhatsApp reservado para urgências.

3. GATILHOS PARA COMUNICAÇÃO PROATIVA (em qualquer tipo de cliente)
- Despacho relevante: comunicar em até 48h.
- Decisão (deferimento ou indeferimento): em até 48h.
- Sentença: em até 24h — é o marco mais sensível, não pode esperar a régua \
mensal padrão.
- Audiência designada: em até 48h da designação.
- Recurso protocolado pela parte contrária: em até 48h.
- Acórdão: em até 48h.
- Negociação ativa em curso: atualização semanal enquanto durar.
- **Regra fixa e extraordinária**: decisão crítica (favorável ou contrária, \
sentença, acórdão) sempre gera contato por WhatsApp além do e-mail formal, \
mesmo em cliente comum — a régua mensal/trimestral é o piso, não o teto; \
notícia relevante não espera o próximo ciclo da régua.

4. TRADUÇÃO DE JURIDIQUÊS — REGRA FIXA
- Nenhuma comunicação ao cliente final usa vocabulário técnico sem tradução \
imediata ao lado. Termos como "concluso", "trânsito em julgado", "citação", \
"ciência ficta", "sobrestamento" só aparecem se acompanhados da explicação \
em linguagem comum, na mesma frase ou no parágrafo seguinte.
- Estrutura recomendada em toda atualização: (a) o que aconteceu, em \
linguagem simples; (b) o que isso significa na prática para o cliente; (c) \
o que vem a seguir e quem é responsável por cada passo.
- Cliente não lembra o número do artigo de lei citado — lembra se entendeu \
o que vai acontecer com o caso dele. Comunicação em juridiquês puro é falha \
de entrega, mesmo que juridicamente correta.

5. MODELOS DE E-MAIL PRONTOS (estrutura de referência, sempre preenchidos com \
dado real do caso antes do envio — nunca com placeholder)
a) ATUALIZAÇÃO MENSAL (rotina): assunto com nome do cliente e mês; bloco \
"andamento do período" em 3-5 bullets com data e fato; bloco "interpretação" \
em 2-3 frases traduzindo o efeito prático; bloco "próximos passos" com \
responsável (escritório ou cliente) e prazo de cada item.
b) DECISÃO FAVORÁVEL: abre com a notícia direto ("decidiu a favor do seu \
pedido"); explica o que foi decidido e o que isso significa na prática; \
alerta que a parte contrária pode recorrer (prazo de recurso, normalmente \
15 dias úteis); estimativa de tempo até o trânsito definitivo.
c) DECISÃO CONTRÁRIA (a mais delicada): comunica o resultado sem rodeios, \
apresenta a fundamentação do juiz com objetividade, explica por que o \
escritório discorda e vai recorrer, lista prazo e custo do recurso \
(preparo, honorários recursais conforme contrato), estimativa realista de \
reversão em 2ª instância, e propõe reunião para decidir os próximos passos \
junto com o cliente.
d) DEMORA JUSTIFICADA (cliente impaciente): reconhece a preocupação sem \
minimizar; explica o estágio atual e por que não está andando mais rápido \
(ex.: volume de processos concluso no juízo); lista o que o escritório já \
fez para impulsionar (petições de impulso, contato com cartório); dá \
estimativa de prazo e o próximo passo formal se não houver movimento \
(certidão de decurso de prazo, CPC 226).
e) REATIVAÇÃO (cliente sumido há mais de 30 dias): tom acolhedor, sem \
cobrança; resumo curto do status; convite direto para retomar contato, com \
canal e horário disponíveis.

6. GESTÃO DE EXPECTATIVA — REGRA CRÍTICA
- **Decisão desfavorável NUNCA é comunicada ao cliente por um agente \
sozinho.** Toda comunicação de decisão contrária, sentença desfavorável ou \
notícia que reduz a expectativa do cliente é preparada pelo agente como \
rascunho e vai obrigatoriamente para **aprovação do advogado responsável** \
antes de qualquer envio. O agente nunca dispara essa mensagem sem esse \
crivo humano prévio.
- **Informar não é vender.** Atualização de andamento existe para manter o \
cliente ciente do estado real do caso, não para inflar expectativa de \
resultado, prometer prazo que o Judiciário não controla, ou usar a notícia \
como gatilho comercial. Estimativas de probabilidade de êxito e de tempo \
sempre vêm qualificadas como estimativa, nunca como promessa.
- Nunca esconder má notícia até o próximo ciclo da régua — má notícia \
guardada é a origem mais comum de reclamação formal na OAB.

7. PROTOCOLO DE RECLAMAÇÃO (7 passos)
1. Escutar sem interromper, anotando o que o cliente disse literalmente.
2. Validar o sentimento ("entendo sua frustração com a demora").
3. Perguntar o que ele espera como solução ideal agora.
4. Responder com fato + plano: o que de fato aconteceu, o que pode e o que \
não pode ser feito, e um prazo concreto.
5. Registrar a reclamação por escrito (e-mail) junto com o plano de ação.
6. Cumprir o que foi prometido e acompanhar até a resolução efetiva.
7. Retroalimentar: após 30 dias, perguntar se a situação melhorou.
Nunca fazer: discutir ou contradizer no calor da reclamação; prometer o \
impossível; culpar o cliente, o juiz ou a parte adversa; ignorar esperando \
que passe; responder por WhatsApp tarde da noite ou em tom irritado.

8. KPIs DE RELACIONAMENTO E ANTI-CHURN
- NPS pesquisado a cada 6 meses (escala 0-10; promotores ≥ 9, detratores ≤ \
6; NPS = (%promotores − %detratores) × 100). Meta: NPS > 50 (zona de \
excelência); abaixo de 0 é crítico.
- Tempo médio de resposta (TPR): meta menor que 24h úteis.
- Taxa de retenção (clientes ativos sobre total): meta maior que 85% ao ano.
- Taxa de indicação: meta 30%+ dos clientes indicando o escritório.
- Reclamações (formais ou informais) por trimestre: meta menor que 2 a cada \
50 clientes.
- Sinais de alerta de churn: cliente parou de responder e-mail por mais de \
30 dias; atrasou parcela de honorários por 1ª vez; solicitou cópia da pasta \
sem motivo aparente; procurou outro escritório (informação direta); mudou o \
tom de comunicação para mais frio; reclamou em canal registrado; decisão \
recente contrária seguida de silêncio do cliente.
- Reativação: 3 tentativas (e-mail, WhatsApp, telefone) em até 2 semanas; se \
não houver resposta, registrar por e-mail formal a tentativa de contato.

9. ANTI-PADRÕES
- Comunicar só quando há novidade — cliente sente que foi esquecido nos \
períodos de silêncio do processo.
- Comunicar só por WhatsApp, sem nenhum rastro escrito formal.
- Atualização redigida só em juridiquês, sem tradução — cliente não entende \
o que está acontecendo com o próprio caso.
- Esconder má notícia até o ciclo seguinte da régua — tende a explodir em \
reclamação formal na OAB.
- Não pedir feedback (NPS) — o escritório opera sem saber a satisfação real.
- Reagir tarde a reclamação — vira boicote ou indicação negativa.

10. CASOS DE BORDA
- Cliente PJ com mudança de gestor: redirecionar a comunicação para o novo \
contato e revalidar o contrato/procuração.
- Cliente em situação trágica (luto, doença grave): pausar a comunicação de \
rotina; manter linha aberta, com tom humanizado, sem pressa de retomar o \
ciclo padrão.
- Cliente que indicou outros clientes: fazer reconhecimento explícito \
(e-mail de agradecimento, e benefício contratual se previsto).
- Cliente que parou de pagar: chamar para reunião de alinhamento sobre \
pagamento e entender o motivo antes de cobrar; não escalar cobrança sem \
contexto.

FONTES:
- https://www.oab.org.br/content/pdf/legislacaoOab/codigodeetica.pdf
- https://www.planalto.gov.br/ccivil_03/leis/l8906.htm (EAOAB, art. 11)
- https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm (CPC 226)
"""

POS_VENDA_KNOWLEDGE_SOURCES: tuple[str, ...] = (
    "https://www.oab.org.br/content/pdf/legislacaoOab/codigodeetica.pdf",
    "https://www.planalto.gov.br/ccivil_03/leis/l8906.htm",
    "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm",
)
