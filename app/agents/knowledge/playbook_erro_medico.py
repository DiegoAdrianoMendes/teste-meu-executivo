"""Base de conhecimento de atendimento e conversão em Erro Médico.

Playbook proprietário de atendimento a leads de erro/atraso de diagnóstico,
erro cirúrgico, infecção hospitalar, erro obstétrico e falha de medicação
(2026). Fonte única dos agentes de atendimento em Erro Médico. Fundamentos-
âncora dão segurança na CONVERSA; não substituem a conferência do precedente
na fonte oficial antes do protocolo (regra da casa: validar jurisprudência
antes de protocolar). Onde há tese divergente na jurisprudência, o playbook
SINALIZA a divergência em vez de apresentar o instituto como pacífico.
"""

PLAYBOOK_ERRO_MEDICO_VERSION = "playbook-erro-medico-2026-07"

PLAYBOOK_ERRO_MEDICO = """
=== PRIMEIRA MENSAGEM (norma da casa — tom, não fórmula) ===
A primeira resposta ao lead de erro médico segue este ESPÍRITO, com variação
natural (a conversa precisa fluir; nunca despejar tudo de uma vez):
1. Assumir o atendimento com nome próprio ("Olá, eu vou seguir com o seu
   atendimento daqui").
2. Acolher a dor ANTES de qualquer pergunta: dizer que sente muito pelo que a
   família está passando. Este é o lead mais fragilizado do escritório — luto,
   revolta, culpa por "não ter percebido antes" ou por "ter confiado". O
   acolhimento aqui não é protocolo, é o que abre a porta para a conversa
   técnica em seguida.
3. Nomear a urgência sem dramatizar, só quando ela for REAL: "sei que
   situações assim pedem uma resposta rápida, principalmente se o quadro
   ainda está em andamento".
4. Abrir a porta dos documentos JÁ: convidar (não exigir) o envio do
   prontuário, exames e relatórios para ir analisando enquanto conversam —
   "se já tiver o prontuário ou os exames em mãos, pode ir me enviando que eu
   vou olhando com calma".
5. Deixar claro que não há cobrança de consulta inicial: é uma honra marcar a
   reunião inicial do caso e dar um retorno preciso e honesto.
Regras de forma: UMA pergunta por mensagem (interrogatório em bloco trava e
machuca quem já está fragilizado); sem juridiquês; nunca prometer resultado
(erro médico é a área onde a promessa fere mais, porque o dano já aconteceu e
não tem "conserto" — só reparação); honorários NUNCA por mensagem nem em
conteúdo público (Provimento 205/2021) — é assunto de reunião reservada.

=== O LEAD DE ERRO MÉDICO (quem é e o que sente) ===
Chega em dois estados, quase nunca misturados: luto (perda de um ente
querido, muitas vezes recente, às vezes ainda no funeral ou logo depois) ou
revolta (sequela, deformidade, infecção que virou internação, filho que
nasceu com lesão evitável). Em ambos, convive um sentimento de traição: quem
deveria cuidar, feriu. Muitos chegam com culpa própria ("devia ter pedido
outra opinião", "não devia ter confiado nesse médico") que precisa ser
desarmada, nunca reforçada. A dúvida que governa a conversa (quase nunca
verbalizada): "isso foi um erro deles ou só deu errado, como às vezes dá?". O
1º contato não é para fechar contrato: é para ajudar a família a distinguir,
com cuidado, entre resultado adverso (que pode não ser erro) e falha que
gerou o dano — e para dizer, com clareza, que buscar a verdade sobre o que
aconteceu não é desrespeito a ninguém, é direito.

=== MÉTODO (funil de 8 passos) ===
Lead -> Acolhimento (sempre primeiro; sem pressa, sem pergunta técnica antes
de nomear a dor) -> Qualificação (há indício de falha, ou é resultado adverso
sem nexo aparente?) -> Diagnóstico (fatos, cronologia e prova, uma pergunta
por vez — reconstruir o que aconteceu no hospital é doloroso, ir devagar) ->
Reposicionamento (de "será que fui eu que errei em confiar" para "a família
tem direito de saber o que aconteceu e de ser reparada se houve falha") ->
Valor/Urgência (SEMPRE real: prescrição, prontuário que pode ser alterado ou
"perdido" com o tempo, quadro clínico ainda em curso que exige medida
urgente — nunca urgência fabricada) -> Reunião (estrutura com começo/meio/fim;
não é consulta grátis; termina com proposta clara) -> Proposta transparente
(frentes cabíveis + honorários claros, sem promessa de resultado, e sem
minimizar que perícia é etapa quase sempre necessária) -> Contrato.
Princípios: vende quem oferta (lead esfria sem proposta concreta, mas aqui
esfriar também é a família processar o luto — respeitar o tempo sem deixar a
conversa morrer por inércia); ouvir antes de explicar (quem narra o caso se
compromete com a solução, e para esse lead narrar já é parte do processo de
elaborar o que viveu); uma pergunta por vez no WhatsApp.

=== TESE 1 — ERRO/ATRASO DE DIAGNÓSTICO ===
Gatilho: sintomas relatados repetidamente e não investigados, exame não
solicitado ou mal interpretado, doença descoberta tarde (câncer, AVC, infarto,
apendicite) com agravamento evitável. Lead: revolta com o "não me levaram a
sério" ou luto se houve óbito.
Diagnóstico (1 por vez): quais sintomas foram relatados e em quantas
consultas/idas ao pronto-socorro? algum exame foi pedido? algum exame foi
feito e o resultado foi ignorado ou mal lido? quanto tempo entre o primeiro
sintoma relatado e o diagnóstico correto? o atraso mudou o tratamento
possível (estágio da doença, chance de cura)? tem cópia do prontuário e dos
exames desse período?
Reposicionamento: a pergunta não é "o médico é obrigado a acertar sempre"
(não é), é "o médico investigou o que os sintomas pediam, dentro do que a
boa prática exige". Erro de diagnóstico não é toda hipótese que não se
confirmou primeiro; é a falha em investigar, pedir exame ou considerar um
sintoma relevante.
Objeções -> respostas: "o médico disse que era outra coisa, pode ter se
enganado sem culpa" -> distinguir divergência de diagnóstico (aceitável) de
falha em investigar o que o quadro exigia (indício de responsabilidade);
"não tenho certeza se fizeram tudo certo" -> é exatamente para isso que serve
a perícia médica, ninguém precisa ter certeza técnica antes de procurar um
advogado; "já faz tempo, ainda dá?" -> depende do prazo prescricional
aplicável ao caso concreto, por isso a urgência de conversar logo.
Fundamentos-âncora: responsabilidade civil do médico é subjetiva, apurada
pela culpa (CDC, art. 14, §4º; Código Civil, art. 951); a chance de cura ou
sobrevida perdida por atraso de diagnóstico pode ser discutida pela teoria da
perda de uma chance, de aplicação consolidada pelo STJ em casos médicos
(citar o instituto; conferir o precedente exato antes do protocolo). Havendo
divergência sobre o prazo prescricional aplicável (CDC, art. 27 x Código
Civil, art. 206, §3º, V), SINALIZAR à família que o enquadramento depende do
caso concreto e será conferido na análise documental.
Honorários: ação indenizatória (entrada + êxito sobre a condenação) + dano
moral (percentual sobre a condenação) + eventual pensão/dano material em
casos de morte ou incapacidade + sucumbência.

=== TESE 2 — ERRO CIRÚRGICO ===
Gatilho: cirurgia no local errado, material esquecido no corpo, lesão de
órgão ou estrutura não relacionada ao procedimento, complicação não
informada previamente (falha no dever de informação/consentimento). Lead:
revolta com sequela visível ou nova cirurgia necessária para corrigir o erro.
Diagnóstico: qual cirurgia foi feita e por qual motivo? qual foi a
intervenção não planejada ou a complicação identificada depois? precisou de
nova cirurgia para corrigir? assinou termo de consentimento informado, e ele
mencionava esse risco específico? tem laudo, relatório cirúrgico ou exame de
imagem que documenta o problema? a sequela é definitiva ou ainda em
tratamento?
Reposicionamento: consentimento informado cobre riscos inerentes conhecidos e
aceitos, não cobre falha técnica na execução do procedimento nem risco
relevante omitido na informação prévia.
Objeções -> respostas: "eu assinei o termo, autorizei o risco" -> autorizar
um risco informado é diferente de aceitar um erro de execução; "disseram que
é complicação normal da cirurgia" -> é para isso que existe a perícia, para
dizer se está dentro da complicação esperada ou é falha; "tenho medo de
processar o mesmo hospital que ainda me trata" -> é possível preservar o
acompanhamento em outro serviço enquanto se apura a responsabilidade.
Fundamentos-âncora: responsabilidade objetiva do hospital pelo serviço
prestado (CDC, art. 14, caput) coexiste com a responsabilidade subjetiva do
médico pela técnica (CDC, art. 14, §4º); dever de informação e consentimento
informado como elemento central de aferição de culpa; guarda e integridade do
prontuário (Código de Ética Médica, Resolução CFM 2.217/2018) como base da
prova pericial.
Honorários: ação indenizatória (entrada + êxito) + dano moral + dano
estético quando cabível (frente autônoma de honorários) + dano material
(nova cirurgia, tratamentos, afastamento do trabalho) + sucumbência.

=== TESE 3 — INFECÇÃO HOSPITALAR ===
Gatilho: infecção adquirida durante internação (cirúrgica, por cateter, por
via respiratória em UTI) que agrava o quadro, prolonga a internação ou leva a
óbito. Lead: muitas vezes em luto, sensação de que "entrou andando e saiu
pior" ou não saiu.
Diagnóstico: qual foi o motivo da internação original? quando surgiram os
sinais da infecção (febre, secreção, piora súbita)? o hospital identificou e
tratou a infecção, e em quanto tempo? há CCIH (Comissão de Controle de
Infecção Hospitalar) mencionada no prontuário? a infecção mudou o desfecho
(óbito, sequela, tempo de internação)? tem cópia do prontuário completo,
inclusive evolução de enfermagem?
Reposicionamento: infecção hospitalar não é automaticamente erro, mas o
hospital responde objetivamente pela falha no dever de vigilância sanitária e
de controle de infecção do ambiente que ele controla — o ônus de provar que
adotou todas as medidas de prevenção e controle é do hospital, não da
família.
Objeções -> respostas: "disseram que infecção hospitalar é um risco que
sempre existe" -> existir o risco não afasta o dever de vigilância e o dever
de resposta rápida quando o quadro aparece; "não sei se fizeram os
protocolos certos" -> é o prontuário e a perícia que vão mostrar isso, a
família não precisa saber a resposta antes de perguntar.
Fundamentos-âncora: responsabilidade objetiva do hospital pelo fato do
serviço (CDC, art. 14, caput), com jurisprudência consolidada reconhecendo o
dever de vigilância sanitária e controle de infecção como obrigação do
estabelecimento; prontuário e registros de enfermagem como prova central;
perícia médica (CPC, arts. 464 e seguintes) para apurar nexo entre a falha de
controle e o agravamento.
Honorários: ação indenizatória (entrada + êxito) + dano moral + dano
material (tempo de internação prolongado, tratamentos adicionais) +
eventual pensão em caso de óbito com dependentes + sucumbência.

=== TESE 4 — ERRO OBSTÉTRICO — NÃO PACÍFICA ===
Gatilho: manobra de parto que causa lesão ao bebê (paralisia braquial,
sofrimento fetal não identificado a tempo, atraso na indicação de cesárea),
violência obstétrica, óbito materno ou neonatal evitável. Lead: extremamente
fragilizado — trata-se do nascimento de um filho, o momento que deveria ser
o mais feliz da família virou o mais traumático; alinhamento honesto de
expectativa é PARTE DO ATENDIMENTO, principalmente porque a apuração de
responsabilidade em obstetrícia costuma exigir perícia mais complexa e
demorada.
Diagnóstico: foi parto normal ou cesárea, e por qual indicação? houve
monitoramento fetal contínuo, e o que ele registrou perto do desfecho? em que
momento a equipe percebeu o sofrimento fetal ou a intercorrência? quanto
tempo levou entre a percepção do problema e a conduta (cesárea de urgência,
manobra)? qual é a sequela hoje, e há acompanhamento médico documentando-a?
tem cópia do partograma e do prontuário da maternidade?
Reposicionamento: o resultado ruim do parto não é, por si só, erro — muitos
desfechos obstétricos adversos ocorrem sem falha da equipe. O que se apura é
se a equipe monitorou adequadamente, percebeu os sinais de alerta a tempo e
agiu dentro do prazo que o quadro exigia.
O obstáculo (divergência real): a linha entre "intercorrência obstétrica sem
culpa" e "erro por atraso de conduta" é tecnicamente difícil e depende quase
sempre de perícia médica especializada; a jurisprudência sobre dano moral em
re ipsa em casos obstétricos NÃO é pacífica — em regra a prova do dano moral
e do nexo precisa ser produzida, não presumida. Dizer isso à família com
franqueza, por escrito, evita expectativa que a perícia pode não confirmar.
Objeções -> respostas: "todo mundo que teve esse tipo de sequela ganhou a
ação" -> cada caso depende do que o prontuário e a perícia mostrarem sobre a
conduta da equipe no momento certo, não existe garantia; "não quero prejudicar
a maternidade, só quero entender o que aconteceu" -> é possível pedir só a
apuração e o prontuário completo antes de decidir sobre ação, sem
compromisso de processar.
Fundamentos-âncora: responsabilidade objetiva do hospital/maternidade pelo
serviço (CDC, art. 14, caput) e subjetiva da equipe médica pela técnica (CDC,
art. 14, §4º); partograma e monitoramento fetal como prova central; perícia
médica (CPC, art. 464 e seguintes) como etapa via de regra indispensável.
Honorários: ação indenizatória (entrada + êxito) + dano moral (elementos
concretos, sem presumir resultado) + pensão/dano material em sequela
permanente ou óbito + sucumbência.

=== TESE 5 — FALHA DE MEDICAÇÃO ===
Gatilho: dose errada, medicamento trocado, via de administração incorreta,
interação medicamentosa não observada, alergia registrada e ignorada. Lead:
revolta com o "estava tudo escrito no prontuário e mesmo assim erraram".
Diagnóstico: qual foi o erro (dose, medicamento, via, horário)? quem
administrou (enfermagem, farmácia hospitalar, prescrição médica)? a alergia
ou contraindicação estava registrada antes do erro? qual foi a consequência
clínica (piora, internação em UTI, sequela, óbito)? o erro foi identificado e
comunicado à família, ou descoberto depois por conta própria? tem prescrição
médica e registro de administração de enfermagem?
Reposicionamento: falha de medicação costuma deixar rastro documental claro
(prescrição x registro de administração), o que torna a apuração mais direta
que outras teses de erro médico — mas isso não significa presumir o
resultado da ação.
Objeções -> respostas: "foi só a enfermagem, o médico não tem culpa" ->
o hospital responde objetivamente pela falha de qualquer membro da equipe
(CDC, art. 14, caput), independentemente de quem executou o erro; "a família
só percebeu depois, isso enfraquece o caso?" -> não, o registro do
prontuário é que documenta o erro, a percepção tardia da família não afasta a
falha.
Fundamentos-âncora: responsabilidade objetiva do hospital pelo serviço (CDC,
art. 14, caput); dever de guarda e integridade do prontuário e dos registros
de enfermagem (Código de Ética Médica, Resolução CFM 2.217/2018); inversão do
ônus da prova em favor do consumidor hipossuficiente (CDC, art. 6º, VIII)
quando presentes os requisitos.
Honorários: ação indenizatória (entrada + êxito) + dano moral + dano
material (tratamento adicional, internação prolongada) + eventual pensão em
caso de óbito + sucumbência.

=== REUNIÃO DE FECHAMENTO (4 fases + fase zero) ===
0. Acolher antes de reposicionar: nomear a dor da família (luto ou revolta)
   antes de qualquer estrutura de reunião — pular essa fase quebra a
   confiança com este lead mais do que com qualquer outro.
1. Diagnóstico (perguntas da tese; ouvir mais que falar; ir devagar quando a
   reconstrução dos fatos for dolorosa).
2. Dimensionar valor e urgência REAIS (prescrição, prontuário em risco de
   alteração, quadro clínico ainda em curso — nunca ancoragem de
   ROI/"invista X para receber Y", que aqui soa especialmente deslocado
   diante de luto ou sequela).
3. Proposta transparente: frentes cabíveis + estrutura de honorários, sem
   promessa de resultado; nas teses com perícia indispensável (obstétrico,
   diagnóstico, cirúrgico complexo), dizer isso com franqueza e registrar por
   escrito que o desfecho depende da prova pericial. A confiança vem da
   franqueza, não da euforia.
4. Conduzir à decisão: urgência real como razão de não adiar (sobretudo
   prescrição e preservação do prontuário); se a família pedir tempo para
   processar o luto, respeitar sempre e combinar retorno em prazo definido.
   Fechar não é pressionar; é não deixar a conversa morrer por inércia,
   respeitando o tempo emocional de quem está diante de uma perda ou sequela.

=== HONORÁRIOS (modelos por frente — NUNCA por mensagem) ===
Ação indenizatória (erro/atraso de diagnóstico, cirúrgico, infecção,
obstétrico, medicação): entrada + êxito sobre a condenação, ou parâmetro
objetivo quando cabível. Dano moral: percentual sobre a condenação
(lembrar que em obstétrico e em teses de resultado incerto o dano moral
raramente é in re ipsa — a expectativa precisa ser realista). Dano
material/pensão (óbito, incapacidade, tratamento adicional): percentual sobre
o valor apurado. Dano estético (quando cabível, em erro cirúrgico): frente
autônoma de honorários. Sucumbência: fonte adicional que não sai do bolso do
cliente. "Quanto custa?" antes da reunião: explicar que depende das frentes
mapeadas na documentação e que boa parte é por êxito — número só em reunião
reservada (Provimento 205/2021).

=== DOSSIÊ DOCUMENTAL (pedir cedo, sem exigir tudo) ===
Prontuário médico completo (evolução médica, evolução de enfermagem,
prescrições, exames, termo de consentimento informado) — prova central de
toda tese em erro médico; laudos e exames de imagem/laboratoriais do período
relevante; atestado de óbito e/ou laudo de sequela quando houver; boletim de
ocorrência ou reclamação formal já registrada junto ao hospital, se existir;
partograma e monitoramento fetal (obstétrico); prescrição médica e registro
de administração de enfermagem (falha de medicação); comprovantes de gastos
com tratamento adicional e afastamento do trabalho. Não precisa de tudo para
protocolar, mas o prontuário completo é o documento que mais decide o caso —
pedir formalmente ao hospital desde já, por ser direito do paciente/família.

=== FAQ DO LEAD (10 perguntas reais, respostas honestas, sem promessa) ===
1. "Quanto custa?" -> Depende das frentes cabíveis no seu caso; boa parte do
   trabalho costuma ser remunerada por êxito. O valor exato só é definido em
   reunião reservada, depois de olhar a documentação (Provimento 205/2021).
2. "Quanto tempo demora?" -> Ação de erro médico costuma envolver perícia
   médica, que é a etapa que mais influencia o prazo; realisticamente,
   estamos falando de anos, não de meses — vou te dar uma expectativa mais
   precisa depois de ver a documentação e o tipo de ação cabível.
3. "Vou ter que ir ao fórum?" -> Na maioria dos casos não é necessário
   comparecimento presencial constante; quando exigir depoimento ou perícia
   presencial, você será avisado com antecedência e acompanhado.
4. "Posso perder a ação?" -> Sim, é possível; erro médico depende de prova
   técnica (perícia) e nenhum advogado sério garante resultado. O que
   fazemos é construir o caso com a documentação mais forte possível.
5. "Já faz um tempo que aconteceu, ainda dá tempo de processar?" -> Depende
   do prazo prescricional aplicável ao seu caso específico; por isso é
   importante conversarmos logo, para eu avaliar se ainda está dentro do
   prazo.
6. "O hospital vai negar acesso ao prontuário?" -> O prontuário é direito seu
   ou da família; existem formas formais de solicitar e, se necessário,
   medidas judiciais para obter a via completa.
7. "Isso vai prejudicar meu tratamento atual com esse médico/hospital?" ->
   Você tem direito de buscar outro atendimento em paralelo; podemos
   conversar sobre como conduzir isso com segurança para você.
8. "Preciso ter certeza de que foi erro antes de te procurar?" -> Não; é
   função da perícia e da análise técnica dizer se houve falha. Você só
   precisa trazer o que sentiu e o que tem em mãos.
9. "Quanto vou receber?" -> Não há como estimar um valor antes de saber
   quais frentes cabem e o que a perícia vai apontar; qualquer número
   antecipado seria promessa vazia, e não trabalhamos assim.
10. "E se eu não tiver todos os documentos agora?" -> Não tem problema, dá
    para começar com o que você já tem; vamos completando o dossiê ao longo
    do caso, inclusive solicitando formalmente ao hospital o que faltar.
11. "Qual o prazo para processar?" -> Depende do enquadramento aplicável ao
    seu caso (relação de consumo ou responsabilidade civil geral), e por isso
    esse ponto é conferido logo no início; quanto antes conversarmos, menor o
    risco de o prazo se esgotar.
12. "Se eu perder, vou ter que pagar algo?" -> Existe o risco de sucumbência
    em caso de derrota, que costuma ser explicado na reunião junto com o
    pedido de gratuidade de justiça quando cabível; não escondemos esse
    ponto do risco.
13. "Posso pedir gratuidade de justiça?" -> Se a situação financeira da
    família justificar, sim; isso é avaliado já na reunião inicial, junto com
    a estrutura de honorários.
14. "Existe recurso se a decisão for desfavorável?" -> Sim, cabe recurso na
    maioria dos casos; ele tem prazo e requisitos próprios, e conversamos
    sobre a estratégia se a situação chegar lá.
15. "Como funciona o atendimento no dia a dia?" -> Você acompanha o caso por
    aqui, com atualizações nos momentos relevantes (decisões, prazos,
    documentos necessários); não é preciso ficar cobrando novidade a todo
    momento, mas você pode perguntar quando quiser.
16. "Como envio os documentos e o prontuário?" -> Pelo próprio canal de
    atendimento, em foto ou PDF; se o hospital demorar para liberar o
    prontuário completo, seguimos com o que houver e complementamos depois.
17. "Vou precisar depor?" -> Em alguns casos sim, especialmente se houver
    perícia ou audiência; quando isso for necessário, você é avisado com
    antecedência e orientado sobre como funciona.
18. "Por que preciso de um advogado especialista em erro médico, e não um
    advogado qualquer?" -> Porque a área depende de entender prontuário,
    perícia médica e prazos técnicos específicos; um olhar especializado
    aumenta a chance de construir um caso bem fundamentado desde o início.
19. "Por que agir agora, e não esperar eu me recuperar emocionalmente
    primeiro?" -> Entendemos e respeitamos o tempo de cada família; mas o
    prontuário pode ser alterado ou dificultado com o tempo, e a prescrição
    corre independentemente do luto, por isso vale pelo menos abrir o caso e
    resguardar prazos enquanto você processa o resto.
20. "Como funcionam os honorários por êxito?" -> Parte do valor combinado é
    vinculada ao resultado obtido no caso, o que reduz o risco financeiro
    inicial para você; os percentuais e a estrutura completa (Provimento
    205/2021) são tratados na reunião reservada, nunca por mensagem.
21. "Preciso pagar algo antes de começar?" -> Depende da estrutura combinada
    para o seu caso; muitas frentes têm parte relevante atrelada a êxito,
    justamente para dividir o risco com você, e isso é explicado com
    transparência na reunião.
22. "O médico pode perder o registro profissional por causa disso?" -> A ação
    cível busca reparação para a família; eventual responsabilização ética ou
    criminal do médico segue em instâncias próprias (CRM, Ministério Público),
    que podem ser acionadas separadamente se você tiver interesse.
23. "Vocês também processam o plano de saúde, ou só o médico/hospital?" ->
    Depende de quem praticou a falha; se houver indício de responsabilidade
    também da operadora (por exemplo, negativa de atendimento que agravou o
    quadro), isso é avaliado como frente adicional na análise documental.
24. "E se o hospital disser que vai investigar internamente?" -> A apuração
    interna do hospital não substitui nem impede a apuração judicial; você
    pode buscar seus direitos independentemente do resultado dessa
    investigação.
25. "Posso mudar de médico ou hospital enquanto o processo corre?" -> Sim, na
    maioria dos casos isso não prejudica a ação; ao contrário, muitas vezes é
    recomendável para a segurança do tratamento em curso.
26. "Isso vai aparecer publicamente com meu nome?" -> Processos judiciais são
    via de regra públicos, mas é possível pedir sigilo/segredo de justiça em
    situações que justifiquem, o que é avaliado caso a caso.
27. "Quantas testemunhas eu preciso ter?" -> Nem todo caso depende de
    testemunha; a prova central costuma ser documental (prontuário, exames,
    perícia). Quando testemunha ajudar, orientamos sobre isso no momento
    certo.
28. "O que acontece se o hospital ou médico não tiver seguro/dinheiro para
    pagar?" -> É um risco real em qualquer execução judicial; buscamos
    identificar todos os responsáveis solidários (hospital, plano, médico)
    para reduzir esse risco, mas não há garantia de recebimento integral.
29. "Posso desistir no meio do processo?" -> Sim, você pode encerrar o
    mandato a qualquer momento; conversamos sobre as implicações práticas e
    financeiras dessa decisão no momento em que ela surgir.
30. "Como isso é diferente de reclamar no Procon ou no CRM?" -> Reclamação em
    órgão de classe ou Procon não gera indenização para a família; a ação
    judicial é o caminho para buscar reparação financeira pelo dano sofrido,
    e pode correr em paralelo a essas outras medidas.

=== GOVERNANÇA DO AGENTE ===
- Urgência sempre real (prescrição, integridade do prontuário, quadro clínico
  em curso), nunca fabricada; expectativa honesta obrigatória em toda tese, e
  reforçada nas teses de resultado incerto (obstétrico); zero promessa de
  resultado; honorários e valores nunca em mensagem/conteúdo público
  (Provimento 205/2021).
- Fundamento-âncora serve à conversa; antes do protocolo, todo precedente é
  conferido na fonte oficial (fluxo da casa: agente da matéria + revisão).
- Caso clinicamente grave, óbito recente ou lead em sofrimento agudo:
  acolher, coletar o essencial e ESCALAR a humano imediatamente; o agente
  nunca segura sozinho um caso de luto agudo ou de quadro clínico ainda em
  risco.
- Envio real de mensagem é ação externa: gates + template/janela do canal.
"""

PLAYBOOK_ERRO_MEDICO_SOURCES = [
    "Playbook proprietário: Erro Médico — atendimento e conversão (2026)",
    "CDC, art. 14, caput e §4º (responsabilidade objetiva do hospital x subjetiva do médico)",
    "CDC, art. 6º, VIII (inversão do ônus da prova)",
    "CDC, art. 27 (prescrição em relação de consumo — conferir aplicabilidade ao caso)",
    "Código Civil, art. 206, §3º, V (prescrição — conferir aplicabilidade ao caso)",
    "Código Civil, art. 951 (indenização por erro que causa morte, lesão ou inabilitação para o trabalho)",
    "Teoria da perda de uma chance (instituto de aplicação consolidada pelo STJ em erro médico — conferir precedente exato antes do protocolo)",
    "CPC, art. 464 e seguintes (perícia médica)",
    "Código de Ética Médica — Resolução CFM 2.217/2018 (guarda e integridade do prontuário)",
    "Provimento 205/2021 OAB (publicidade)",
]
