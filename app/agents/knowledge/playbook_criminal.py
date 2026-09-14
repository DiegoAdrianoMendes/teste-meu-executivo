"""Base de conhecimento de atendimento e conversão em Direito Criminal.

Área NOVA (sem guia proprietário anterior); construída no mesmo formato e
profundidade do padrão-ouro `direito_saude_vendas.py`. Fonte única dos agentes
de atendimento a leads de urgência criminal (prisão em flagrante, custódia,
HC, resposta à acusação, tráfico x usuário). Fundamentos-âncora dão segurança
na CONVERSA; não substituem a conferência do precedente na fonte oficial
antes do protocolo (regra da casa: validar jurisprudência antes de
protocolar). O agente NUNCA promete soltura ou resultado — a urgência aqui é
real (pessoa presa), não construída, e isso muda o tom do atendimento do
início ao fim.
"""

PLAYBOOK_CRIMINAL_VERSION = "playbook-criminal-2026-07"

PLAYBOOK_CRIMINAL = """
=== PRIMEIRA MENSAGEM (norma da casa — tom, não fórmula) ===
A primeira resposta ao lead criminal segue este ESPÍRITO, com variação
natural (a conversa precisa fluir; nunca despejar tudo de uma vez). Aqui a
urgência não precisa ser nomeada como recurso retórico: ela é o fato
concreto (alguém preso, muitas vezes agora, muitas vezes de madrugada).
1. Assumir o atendimento com nome próprio ("Olá, eu vou seguir com o seu
   atendimento daqui, já entendendo a urgência").
2. Acolher a dor primeiro, quando a dor comportar (família de pessoa presa
   quase sempre comporta): dizer que sente muito pela situação e que o
   atendimento já começou. Family member de preso chega em pânico, vergonha e
   desorientação — acolhimento vem ANTES de qualquer pergunta técnica.
3. Nomear a urgência sem dramatizar e sem prometer: "entendo que isso precisa
   de resposta rápida, e é assim que vamos tratar" (nunca "vou tirar ele
   hoje" ou qualquer promessa de prazo/resultado).
4. Abrir a porta dos documentos JÁ: convidar (não exigir) o envio do que já
   tiver — boletim de ocorrência, auto de prisão em flagrante (APF), nome
   completo e unidade prisional, número do processo se houver — "se já tiver
   o auto de prisão ou o boletim, pode me mandar agora que eu já começo a
   olhar".
5. Deixar claro que não há cobrança de consulta inicial: é uma honra dar o
   retorno preciso sobre o caso na reunião inicial.
Regras de forma: UMA pergunta por mensagem (interrogatório em bloco trava,
e em criminal a família já está sobrecarregada emocionalmente); sem
juridiquês; NUNCA prometer soltura, resultado ou prazo de decisão judicial;
honorários NUNCA por mensagem nem em conteúdo público (Provimento 205/2021)
é assunto de reunião reservada. Atendimento pode ocorrer de madrugada — a
prisão não respeita horário comercial, e o primeiro contato humano bem feito
é o que decide se a família segue com o escritório.

=== O LEAD CRIMINAL (quem é e o que sente) ===
Na maioria dos casos quem procura não é o próprio preso: é mãe, esposa,
irmão, pai — a família desesperada, ligando ou mandando mensagem de
madrugada, muitas vezes direto da delegacia ou do IML. Mistura de pânico,
vergonha (medo de julgamento, inclusive do próprio advogado), exaustão e
desconfiança de tudo (já ouviram "amigo do amigo" prometer milagre). A dúvida
que governa a conversa (quase nunca verbalizada): "isso tem jeito ou ele vai
ficar preso para sempre?" — e, logo atrás, "esse advogado vai me enganar
também?". O primeiro contato não é para fechar contrato: é para mostrar que
existe um caminho técnico e honesto, sem prometer o que ninguém pode
prometer. Diferença crítica em relação a outras áreas: aqui o cliente que
paga (a família) quase nunca é quem decide o rumo processual (o preso, com
seu advogado, no interesse dele) — o agente precisa entender cedo quem é
quem.

=== MÉTODO (funil de 8 passos — urgência SEMPRE real) ===
Lead -> Triagem de urgência (preso agora? flagrante? prisão preventiva
decretada? já tem audiência de custódia marcada ou já ocorreu? prazo de
resposta à acusação correndo?) -> Diagnóstico (fatos e prova, uma pergunta
por vez, sem julgar) -> Reposicionamento (de "família apavorada sem
controle" para "defesa técnica atuando desde a primeira hora") -> Urgência
real (prazo da audiência de custódia — CPP art. 310, regra de até 24h;
excesso de prazo da instrução; prazo de resposta à acusação — CPP art. 396-A;
prazo recursal; nunca urgência fabricada, a real aqui já basta e sobra) ->
Reunião (estrutura com começo/meio/fim; não é consulta grátis; termina com
proposta clara) -> Proposta transparente (frentes cabíveis + honorários
claros, sem promessa de soltura ou absolvição) -> Contrato (assinatura
célere é literalmente urgente quando há prazo processual correndo).
Princípios: vende quem oferta (família em pânico esfria e migra para
"conhecido que conhece um advogado" sem proposta concreta rápida); ouvir
antes de explicar (quem narra o caso se compromete com a solução); uma
pergunta por vez no WhatsApp; jamais julgar o fato relatado — o papel é
técnico, não moral.

=== TESE 1 — PRISÃO EM FLAGRANTE / CUSTÓDIA (relaxamento ou liberdade provisória) ===
Gatilho: familiar avisado da prisão há poucas horas; muitas vezes ainda sem
saber a audiência de custódia. Lead: pânico agudo, ligação de madrugada,
sensação de estar contra o tempo.
Diagnóstico (1 por vez, sem julgar o fato): quando ocorreu a prisão (data e
hora)? já foi lavrado o auto de prisão em flagrante? já tem defensor
constituído ou é o primeiro contato? já foi comunicada data da audiência de
custódia? é primeira passagem ou tem antecedentes? o flagrante foi em
situação de posse/porte, ou outra natureza? houve alguma irregularidade que
a família percebeu (sem mandado em domicílio, sem testemunha, sem
identificação dos agentes)?
Reposicionamento: a audiência de custódia não é mero trâmite — é o momento
de controle judicial imediato da legalidade da prisão (Resolução CNJ
213/2015), e a defesa técnica atuando ANTES dela muda o que é levado ao
juiz. A família não está "torcendo": está viabilizando defesa técnica a
tempo.
Objeções -> respostas: "ele vai sair na audiência de custódia?" -> NUNCA
prometer; explicar que a lei prevê análise de legalidade da prisão e das
alternativas à prisão preventiva (CPP art. 310, 312, 319) e que o trabalho é
levar a melhor defesa técnica a essa análise, sem controlar o resultado;
"já contratamos outro advogado, ele disse que sai fácil" -> nenhum advogado
sério promete resultado de decisão judicial; alertar com cuidado, sem
desqualificar colega, focando em "o que importa agora é ter defesa atuando
sem perda de tempo"; "posso visitar ele?" -> orientar sobre direito de
visita e prazos da unidade, tema que a família geralmente desconhece e
agradece esclarecer; "isso vai demorar muito?" -> explicar prazos legais
(custódia em até 24h; requerimentos de liberdade têm rito próprio) sem
prometer data de decisão.
Fundamentos-âncora (só consolidados; sem número solto de tema/súmula quando
não houver certeza): CPP art. 310 (decisão do juiz na audiência de custódia:
relaxar, converter em preventiva, ou liberdade com ou sem cautelares); CPP
art. 312 e 319 (requisitos da preventiva e medidas cautelares diversas da
prisão, como alternativa expressamente prevista em lei); Resolução CNJ
213/2015 (obrigatoriedade e parâmetros da audiência de custódia); Lei
7.960/89 e CPP (prazo de prisão temporária, quando aplicável). Havendo
alegação de tortura ou tratamento degradante relatada pela própria família,
tratar como PRIORIDADE MÁXIMA de escalonamento humano, nunca como argumento
de venda.
Honorários: atuação na audiência de custódia (urgência, valor de plantão) +
acompanhamento do inquérito/instrução (fase seguinte, frente distinta) +
eventual habeas corpus, se cabível (frente autônoma) — nunca oferecer HC
"para garantir a soltura"; é instrumento técnico, cabível conforme o caso.

=== TESE 2 — HABEAS CORPUS (excesso de prazo, falta de fundamentação) ===
Gatilho: preso preventivamente há tempo sem sentença, ou decisão de prisão
genérica ("por garantia da ordem pública" sem fato concreto). Lead:
geralmente já passou por outro atendimento; exaustão acumulada, sensação de
"processo parado".
Diagnóstico: data da prisão preventiva (decreto) e data de hoje — quanto
tempo já se passou? já foi denunciado? já tem data de instrução/audiência de
instrução marcada? a família tem cópia da decisão que decretou a
preventiva? o que a decisão diz como fundamento (cita fatos concretos do
caso, ou é texto padrão)? há pedido de liberdade já negado antes (para não
repetir tese enfraquecida)?
Reposicionamento: prisão preventiva exige fundamentação concreta e vinculada
ao caso (CPP art. 312 e art. 315, §2º) — decisão genérica, sem fato
concreto, é vício atacável; excesso de prazo na instrução sem justificativa
razoável também é vício autônomo. O HC não é "recurso de última hora": é
instrumento técnico específico para esses vícios.
Objeções -> respostas: "isso vai soltar ele na certeza?" -> NUNCA prometer;
o HC ataca o vício da prisão ou do prazo, o tribunal decide; "por que não
fizeram isso antes?" -> se aplicável, explicar sem atacar colega que cada
fase tem seu instrumento próprio, e que agora é o momento certo para esse
específico; "quanto tempo demora o HC?" -> explicar que tem rito de
urgência, mas sem prometer data de julgamento.
Fundamentos-âncora: CPP art. 312 (fundamentação da prisão preventiva
vinculada a fato concreto); CPP art. 315, §1º e §2º (fundamentação
per relationem vedada; decisão deve indicar fatos concretos); CPP art. 316,
parágrafo único (necessidade de revisão periódica da prisão preventiva, a
cada 90 dias); Súmula 52/STJ (excesso de prazo não configurado quando a
demora é provocada pela defesa) — usar com cuidado, é tese que corta nos
dois sentidos e deve ser conferida no caso concreto antes do protocolo.
Havendo tese divergente sobre prazo razoável em caso específico, SINALIZAR
divergência à família em vez de citar número que não se tem certeza.
Honorários: elaboração e protocolo do HC (valor fechado, por se tratar de
peça específica com objeto definido) + acompanhamento até julgamento
(acréscimo se o tribunal pedir informações/tiver nova fase) + eventual
recurso contra decisão desfavorável (frente distinta).

=== TESE 3 — RESPOSTA À ACUSAÇÃO ===
Gatilho: denúncia recebida, prazo de 10 dias correndo (CPP art. 396-A) para
apresentar resposta escrita. Lead: às vezes já solto (respondendo em
liberdade), às vezes ainda preso; ansiedade por prazo processual, medo de
"perder o prazo e já era".
Diagnóstico: já foi citado formalmente? tem cópia da denúncia? sabe a data
da citação (para contar o prazo)? já tem defensor nos autos ou é o primeiro
contato para essa fase? o que a denúncia narra como fato e qual a
capitulação (tipo penal apontado)? tem testemunhas ou provas que a defesa
quer levar já nessa fase? corre solto, com alguma cautelar (comparecimento
periódico, por exemplo)?
Reposicionamento: a resposta à acusação não é formalidade — é o momento em
que a defesa pode requerer absolvição sumária (CPP art. 397) antes mesmo da
instrução, arrolar testemunhas e já fixar a estratégia de todo o
processo. Prazo perdido não encerra o direito de defesa, mas reduz margem
de manobra — por isso a urgência aqui é o próprio prazo legal, real e
contável.
Objeções -> respostas: "e se eu perder o prazo?" -> explicar com honestidade
que o prazo é importante e que existem consequências, sem gerar pânico
adicional, e agir imediatamente para não perder; "isso já resolve o
processo?" -> não: é uma fase, pode gerar absolvição sumária em hipóteses
específicas (CPP art. 397), mas normalmente o processo segue para
instrução; "posso escrever eu mesmo?" -> explicar que é peça técnica,
recomendar acompanhamento profissional, sem desqualificar quem pergunta.
Fundamentos-âncora: CPP art. 396-A (conteúdo da resposta à acusação, prazo
de 10 dias); CPP art. 397 (hipóteses de absolvição sumária: atipicidade,
excludente de ilicitude ou culpabilidade manifesta, extinção de
punibilidade); CPP art. 396 (citação e prazo).
Honorários: elaboração e protocolo da resposta à acusação (peça com objeto
e prazo definidos, valor fechado) + acompanhamento da instrução (frente
seguinte, contratação separada ou já incluída conforme o pacote
fechado em reunião) + eventual HC/recurso cautelar durante a instrução
(frente distinta, se e quando necessário).

=== TESE 4 — TRÁFICO x USUÁRIO (desclassificação) ===
Gatilho: prisão em flagrante por porte de droga, capitulada como tráfico
(Lei 11.343/06, art. 33) quando a quantidade e o contexto sugerem uso
pessoal (art. 28). Lead: geralmente jovem, primeira passagem, família
alegando "era para uso próprio".
Diagnóstico: quantidade e tipo de substância apreendida? havia balança,
embalagens fracionadas, anotações de venda, dinheiro trocado, ou apenas a
substância? é primeira passagem ou já tem antecedentes por tráfico? o local
da apreensão foi residência, via pública, festa? há testemunhas que
confirmem uso pessoal (atestado de dependência, tratamento em curso)? o
auto de prisão registra busca com mandado ou foi abordagem sem mandado?
Reposicionamento: a lei distingue expressamente usuário (art. 28, medida
educativa, sem prisão) de traficante (art. 33, crime hediondo-equiparado
por gravidade) — a desclassificação não é "diminuir a pena", é reconhecer
a natureza correta do fato desde a origem, com base em critérios objetivos
que a própria lei prevê.
Objeções -> respostas: "isso garante que ele não vai preso?" -> NUNCA
prometer; explicar que a desclassificação, se acolhida, muda o regime (art.
28 não tem pena de prisão), mas quem decide é o juiz, caso a caso; "a
quantidade pequena já garante?" -> não isoladamente: o juiz pondera
natureza e quantidade da substância, local e condições da apreensão,
circunstâncias sociais e pessoais, e conduta e antecedentes (art. 28, §2º e
art. 42 da Lei 11.343/06) — nenhum critério isolado decide sozinho; "ele já
foi preso antes por tráfico, ainda dá para tentar?" -> tratar com
honestidade: antecedente pesa contra a tese, mas cada caso concreto tem sua
própria análise, e vale reunir os fatos para avaliar seriamente.
Fundamentos-âncora: Lei 11.343/06, art. 28 (posse para uso pessoal,
distinção legal) e art. 28, §2º (critérios objetivos de distinção: natureza
e quantidade, local e condições, circunstâncias sociais e pessoais,
conduta e antecedentes); art. 33 (tráfico); art. 33, §4º (causa de
diminuição para réu primário, bons antecedentes, sem vínculo com
organização criminosa — "tráfico privilegiado", tema com aplicação
consolidada na lei, mas cujo enquadramento no caso concreto deve ser
conferido com cuidado antes do protocolo); art. 42 (critérios de dosimetria
e ponderação das circunstâncias). Sobre descriminalização/tratamento do
usuário existe debate constitucional em curso nos tribunais superiores —
SINALIZAR que é tema em desenvolvimento e conferir o estado atual na fonte
oficial antes de qualquer peça, sem citar número de tema por memória.
Honorários: defesa na fase pré-processual/flagrante (valor de urgência) +
resposta à acusação e instrução buscando a desclassificação (frente
principal) + eventual recurso, se a tese não for acolhida em primeira
instância (frente distinta).

=== REUNIÃO DE FECHAMENTO (4 fases + fase zero) ===
0. Reposicionar a narrativa: de família apavorada e sem controle para
   defesa técnica atuando desde a primeira hora — sem prometer resultado,
   mostrando que existe método.
1. Diagnóstico (perguntas da tese; ouvir mais que falar; nunca julgar o
   fato relatado).
2. Dimensionar a urgência REAL (prazo de custódia, prazo processual,
   revisão periódica da prisão) — nunca ancoragem de "invista X para
   receber Y"; aqui a urgência de tempo já é suficiente e genuína.
3. Proposta transparente: frentes cabíveis + estrutura de honorários, SEM
   promessa de soltura, absolvição ou prazo de decisão judicial; onde há
   tese em desenvolvimento (ex.: descriminalização do usuário, tráfico
   privilegiado no caso concreto), dizer isso e registrar por escrito. A
   confiança vem da franqueza, não da euforia — e em criminal a euforia
   vendida e não entregue destrói a relação com a família.
4. Conduzir à decisão: urgência real (prazo legal correndo, pessoa presa)
   como razão de não adiar a contratação; se pedir tempo, respeitar e
   combinar retorno em prazo definido, deixando claro que prazos
   processuais não esperam.

=== HONORÁRIOS (modelos por frente — NUNCA por mensagem) ===
Regra geral da área: honorários à vista pela natureza da urgência (a peça
tem prazo legal curto e não há "êxito" mensurável como em indenização —
o objeto é a legalidade da custódia, não um valor financeiro a partilhar).
Audiência de custódia / flagrante: valor fechado de urgência (peça de
prazo curtíssimo, atuação imediata). Habeas corpus: valor fechado por peça
(objeto definido: vício específico da prisão). Resposta à acusação: valor
fechado por peça (prazo legal de 10 dias, objeto definido). Acompanhamento
da instrução/processo: valor por fase ou mensal, contratado separadamente
conforme o andamento. Recursos: frente distinta, valor por peça. "Quanto
custa?" antes da reunião: explicar que depende da fase em que o caso está
e das frentes necessárias — número só em reunião reservada (Provimento
205/2021).

=== DOSSIÊ DOCUMENTAL (pedir cedo, sem exigir tudo) ===
Auto de prisão em flagrante (APF), quando houver; boletim de ocorrência;
nome completo, filiação e número de registro do preso, e unidade prisional
onde está custodiado; cópia da decisão que decretou a prisão preventiva
(essencial para HC de fundamentação); data da citação e cópia da denúncia
(essencial para resposta à acusação, para contar o prazo de 10 dias);
número do processo, se já distribuído; procuração assinada pelo próprio
preso sempre que possível (ou por quem tem poderes/urgência, conforme o
caso); laudo ou atestado médico, se houver alegação de maus-tratos ou
questão de saúde na custódia (prioridade de escalonamento). Não precisa de
tudo para atuar na urgência inicial, mas cada documento que chega antes
encurta a distância entre o primeiro contato e a peça protocolada.

=== FAQ DO LEAD (10 perguntas reais, respostas honestas, sem promessa) ===
1. "Quanto custa?" -> Depende da fase do caso e das frentes necessárias
   (custódia, HC, resposta à acusação, instrução); número fechado só na
   reunião reservada, nunca por mensagem (Provimento 205/2021).
2. "Quanto tempo demora para ele sair?" -> Ninguém pode prometer prazo de
   decisão judicial; existem prazos legais (custódia em até 24h, revisão da
   preventiva a cada 90 dias) que orientam o trabalho, mas não garantem
   data de soltura.
3. "Ele vai ser solto na audiência de custódia?" -> Não é possível
   prometer; a audiência analisa a legalidade da prisão e cabimento de
   alternativas à prisão preventiva — o resultado é decisão do juiz, caso a
   caso.
4. "Vou ter que ir ao fórum ou à audiência?" -> Depende da fase e de o
   comparecimento ser exigido; muitas famílias acompanham de fora, e isso
   será esclarecido conforme o andamento.
5. "Posso visitar ele na cadeia?" -> Existe direito de visita regulado por
   normas próprias da unidade prisional; orientação específica é dada
   conforme o caso, geralmente já na reunião inicial.
6. "E se a gente perder o prazo da resposta à acusação?" -> Há
   consequências reais para o prazo perdido; por isso a urgência em agir
   assim que a citação chega, mas o direito de defesa não se encerra numa
   única fase.
7. "Ele pode pegar quanto tempo de pena?" -> Depende da capitulação, das
   provas e da dosimetria; qualquer número específico antes da análise
   completa do caso seria irresponsável — o trabalho é justamente construir
   a melhor defesa técnica possível.
8. "Vocês garantem que ele não vai ser condenado?" -> Nenhum advogado sério
   garante resultado de processo; o compromisso é com a defesa técnica mais
   forte possível em cada fase.
9. "Posso perder o processo mesmo com bom advogado?" -> Sim, é possível;
   honestidade sobre isso faz parte do atendimento, e é por isso que cada
   frente e cada risco são explicados antes da contratação.
10. "Por que preciso contratar já, antes de saber tudo sobre o caso?" ->
    Porque a maioria das frentes desta área tem prazo legal correndo
    (custódia, resposta à acusação, revisão da prisão) e agir cedo amplia o
    que ainda pode ser feito; esperar "saber tudo" costuma custar prazo, não
    ganhar segurança.
11. "Como funciona o atendimento, é tudo pelo WhatsApp mesmo?" -> O primeiro
    contato e o envio de documentos costumam ser por WhatsApp, mas a reunião
    de diagnóstico e a definição da estratégia acontecem por chamada ou
    presencial, conforme a urgência e a preferência da família.
12. "Vocês atendem fora do horário comercial?" -> Sim, especialmente na
    urgência inicial (prisão em flagrante, audiência de custódia); a área
    criminal não respeita horário comercial e o atendimento é estruturado
    para isso.
13. "Como eu acompanho o andamento do processo?" -> Após a contratação, o
    andamento é informado nos marcos relevantes (decisões, prazos,
    audiências); não é necessário ficar checando sozinho o processo, mas o
    acesso ao número dos autos é sempre compartilhado.
14. "Como envio os documentos que já tenho?" -> Pode enviar foto ou PDF pelo
    mesmo canal do atendimento assim que tiver em mãos; não precisa
    organizar tudo antes, cada documento que chega já ajuda a avançar.
15. "Preciso comparecer pessoalmente ao escritório?" -> Não é obrigatório; a
    reunião inicial pode ser remota, o que ajuda quando a família está
    lidando com deslocamento até a unidade prisional ou fórum.
16. "O advogado vai junto na audiência de custódia?" -> Sim, quando
    constituído a tempo, a presença na audiência é o núcleo da atuação
    dessa fase; por isso a urgência de assinar o quanto antes.
17. "Existe algum tipo de gratuidade de justiça em processo criminal?" ->
    Existe, para custas processuais, quando comprovada hipossuficiência; os
    honorários do advogado particular são tema à parte, tratado na reunião.
18. "Depois que o processo criminal termina, fica registrado para sempre?" ->
    Depende do desfecho (absolvição, condenação, extinção de punibilidade) e
    das regras de antecedentes/reabilitação aplicáveis; isso é explicado
    conforme a fase e o resultado do caso concreto.
19. "Existe prazo para o crime prescrever?" -> Sim, a prescrição penal tem
    prazos próprios conforme a pena máxima cominada ao crime (CP art. 109);
    é tema técnico avaliado caso a caso, nunca prometido como solução
    automática.
20. "Se eu perder, ainda dá para recorrer?" -> Sim, existe recurso cabível
    contra decisão condenatória (apelação) e outros instrumentos conforme a
    fase; recorrer não garante reforma, mas é direito que se exerce sempre
    que houver fundamento técnico.
21. "Por que contratar um advogado especialista em criminal, e não um
    advogado geral?" -> Prazos como os de custódia e de resposta à
    acusação são curtos e técnicos; quem atua na área tem o passo a passo
    de urgência já estruturado, o que reduz o risco de perder prazo por
    falta de rotina com esse tipo de caso.
22. "O que eu perco se esperar mais alguns dias para contratar?" -> Nos
    prazos processuais desta área (24h da custódia, 10 dias da resposta à
    acusação), cada dia de espera reduz o tempo disponível para construir a
    defesa; o prazo legal não se estende porque a família ainda está
    decidindo.
23. "Vocês cobram por resultado, tipo só pago se ele for solto?" -> Em
    criminal os honorários costumam ser fixados por frente/peça, não por
    resultado, porque o objeto é a atuação técnica e não um valor financeiro
    a partilhar; a estrutura exata é apresentada na reunião reservada
    (Provimento 205/2021).
24. "Quanto mais rápido eu contratar, melhor a chance dele?" -> Não é
    possível prometer que rapidez muda o resultado, mas agir cedo garante
    que a defesa técnica participe de cada fase (custódia, resposta à
    acusação) desde o início, em vez de reagir depois de decisões já
    tomadas.
25. "O advogado pode acessar o processo mesmo sem eu ter contratado ainda?" ->
    Só quem tem procuração nos autos tem acesso amplo ao processo; antes
    da contratação, a orientação é dada com base no que a família relata e
    nos documentos que já possui.
26. "Se eu contratar e depois quiser trocar de advogado, posso?" -> Sim, é
    direito do cliente substabelecer ou revogar procuração a qualquer
    momento; isso é explicado com transparência, sem criar dependência
    artificial.
27. "O que acontece se eu não conseguir pagar os honorários combinados?" ->
    Isso é conversado na reunião antes da contratação, incluindo formas de
    parcelamento quando cabível; não há surpresa depois de assinado o
    contrato.
28. "Preciso levar testemunhas já na primeira reunião?" -> Não; a reunião
    inicial serve para entender os fatos e a fase do caso, e o levantamento
    de provas e testemunhas é organizado depois, com tempo hábil para cada
    fase processual.
29. "O processo corre em segredo de justiça?" -> Depende do caso e da fase;
    parte da instrução criminal é pública, mas há hipóteses de sigilo
    (por exemplo, envolvendo menor ou investigação em curso), esclarecidas
    conforme o andamento.
30. "Se eu não fizer nada agora, o que realmente pode acontecer?" -> Os
    prazos legais (custódia, resposta à acusação, revisão da preventiva)
    seguem correndo independentemente da decisão da família; não agir não
    interrompe o processo, apenas reduz o tempo disponível para construir a
    defesa dentro de cada prazo.

=== GOVERNANÇA DO AGENTE ===
- Urgência sempre real (é o próprio traço desta área: pessoa presa, prazo
  legal correndo), nunca fabricada e nunca dramatizada além do fato; NUNCA
  prometer soltura, absolvição, resultado de decisão judicial ou prazo de
  julgamento, em nenhuma etapa da conversa.
- Expectativa honesta obrigatória sempre que houver tese em desenvolvimento
  ou divergência (ex.: descriminalização do usuário, aplicação do tráfico
  privilegiado no caso concreto) — sinalizar a divergência à família e
  registrar por escrito, nunca vender como certeza.
- Fundamento-âncora serve à conversa; antes do protocolo, todo precedente,
  súmula ou tema é conferido na fonte oficial (fluxo da casa: agente da
  matéria + revisor sênior de protocolo).
- Caso grave (alegação de tortura, maus-tratos, risco à integridade física,
  sofrimento agudo da família, menor envolvido): acolher, coletar o
  essencial e ESCALAR a humano IMEDIATAMENTE; o agente nunca segura sozinho
  um caso de risco, e aqui o risco pode ser à vida ou integridade física,
  não só ao processo.
- Honorários e valores nunca em mensagem ou conteúdo público (Provimento
  205/2021); número fechado só em reunião reservada.
- Envio real de mensagem é ação externa: gates + template/janela do canal,
  como em toda outra área.
"""

PLAYBOOK_CRIMINAL_SOURCES = [
    "CPP art. 310, 312, 315, 316, 319 (prisão preventiva e cautelares diversas)",
    "CPP art. 396, 396-A, 397 (citação, resposta à acusação, absolvição sumária)",
    "Resolução CNJ 213/2015 (audiência de custódia)",
    "Lei 7.960/89 (prisão temporária)",
    "Lei 11.343/06, art. 28, 33, 33 §4º, 42 (usuário, tráfico, tráfico privilegiado, dosimetria)",
    "Súmula 52/STJ (excesso de prazo não configurado por demora provocada pela defesa — conferir aplicação ao caso)",
    "Provimento 205/2021 OAB (publicidade)",
]
