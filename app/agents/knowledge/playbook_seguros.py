"""Base de conhecimento de atendimento e conversão em Seguros.

Playbook de atendimento e fechamento para leads da área de Seguros (vida,
prestamista, DPVAT, saúde/negativa de sinistro por preexistência ou
agravamento). Fonte única dos agentes de atendimento a segurados com sinistro
negado, atrasado ou mal pago. Fundamentos-âncora dão segurança na CONVERSA;
não substituem a conferência do precedente na fonte oficial antes do
protocolo (regra da casa: validar jurisprudência antes de protocolar).
"""

PLAYBOOK_SEGUROS_VERSION = "playbook-seguros-2026-07"

PLAYBOOK_SEGUROS = """
=== PRIMEIRA MENSAGEM (norma da casa — tom, não fórmula) ===
A primeira resposta ao lead de seguros segue este ESPÍRITO, com variação
natural (a conversa precisa fluir; nunca despejar tudo de uma vez):
1. Assumir o atendimento com nome próprio ("Olá, eu vou seguir com o seu
   atendimento daqui").
2. Acolher a dor primeiro, quando ela comportar: dizer que sente muito pela
   situação (luto, acidente, perda patrimonial) e que em breve conseguiremos
   ajudar. O lead de seguro recém-sinistrado chega, muitas vezes, em cima de
   uma perda recente — acolhimento vem ANTES de qualquer pergunta técnica.
3. Nomear a urgência sem dramatizar: "sei que essas situações costumam ser
   urgentes, principalmente quando a seguradora já negou ou está demorando".
4. Abrir a porta dos documentos JÁ: convidar (não exigir) o envio da apólice,
   do aviso de sinistro e da carta de negativa para ir analisando enquanto
   conversam — "se quiser já me enviar os documentos, vou analisando enquanto
   conversamos".
5. Deixar claro que não há cobrança de consulta inicial: é uma honra marcar a
   reunião inicial do caso e dar um retorno preciso.
Regras de forma: UMA pergunta por mensagem (interrogatório em bloco trava);
sem juridiquês; nunca prometer resultado; honorários NUNCA por mensagem nem em
conteúdo público (Provimento 205/2021) — é assunto de reunião reservada.

=== O LEAD DE SEGUROS (quem é e o que sente) ===
Chega em dois momentos-tipo, quase opostos em tom mas iguais em urgência: (a)
logo após um sinistro (morte na família, acidente de trânsito, incêndio,
roubo) — dor aguda, luto ou choque, e a seguradora nega, atrasa ou paga menos
do que o esperado; (b) meses depois, quando o "não" já veio por escrito e a
indignação amadureceu em disposição de brigar. Em ambos, a dúvida que governa
a conversa (quase nunca verbalizada): "eu paguei o seguro a vida inteira — a
seguradora pode simplesmente não pagar quando eu mais preciso?". O 1º contato
não é para fechar contrato: é para responder essa dúvida com autoridade e sem
juridiquês. A indignação vira ação quando o lead percebe que é a seguradora
que precisa justificar a negativa, não ele que precisa provar que "merece"
receber o que contratou.

=== MÉTODO (funil de 8 passos) ===
Lead -> Qualificação (viável x inviável) -> Diagnóstico (fatos e prova, uma
pergunta por vez) -> Reposicionamento (de "suplicante" para segurado titular
de direito contratual) -> Valor/Urgência (SEMPRE real: prazo decadencial/
prescricional do seguro, prazo de resposta da seguradora, urgência financeira
do beneficiário sem renda, prova que se perde com o tempo — nunca urgência
fabricada) -> Reunião (estrutura com começo/meio/fim; não é consulta grátis;
termina com proposta clara) -> Proposta transparente (frentes cabíveis +
honorários claros, sem promessa de resultado) -> Contrato.
Princípios: vende quem oferta (lead esfria sem proposta concreta); ouvir antes
de explicar (quem narra o caso se compromete com a solução); uma pergunta por
vez no WhatsApp.

=== TESE 1 — NEGATIVA POR DOENÇA/CONDIÇÃO PREEXISTENTE SEM EXAME PRÉVIO ===
Gatilho: seguradora nega indenização (vida, prestamista, saúde) alegando que o
segurado omitiu doença preexistente, sem nunca ter exigido exame médico
admissional. Lead: família enlutada ou segurado doente, sentindo que está
sendo acusado de má-fé por algo que nem sabia ter que declarar com precisão
clínica.
Diagnóstico (1 por vez): a seguradora exigiu exame médico antes de aceitar a
proposta? o segurado já sabia do diagnóstico ao contratar, ou descobriu depois
(ou nunca soube)? a causa da morte/sinistro tem relação direta com a doença
apontada como preexistente? a negativa veio por escrito, com qual
fundamentação? há quanto tempo o segurado pagava o prêmio sem atraso? a
apólice é individual ou vinculada a financiamento (prestamista)?
Reposicionamento: se a seguradora aceitou o risco sem exigir exame prévio,
ela assumiu o risco de contratar sem checar — o ônus de provar má-fé do
segurado é da seguradora, não do beneficiário provar boa-fé.
Objeções -> respostas: "o segurado sabia e escondeu" -> sem exame prévio
exigido, presume-se boa-fé e cabe à seguradora provar o dolo, não bastando
mera presunção (Súmula 609/STJ); "doença é preexistente, então está excluída"
-> agravamento de doença preexistente sem exame prévio não autoriza recusa
automática de cobertura; "já pagamos parte do prêmio de volta" -> devolução
parcial não quita a obrigação principal de indenizar; "prescreveu" -> prazo
de 1 ano da ciência da negativa (art. 206, §1º, II, "b", CC) costuma estar
correndo ou ter sido interrompido pela reclamação administrativa.
Fundamento central: Súmula 609/STJ — "a recusa de cobertura securitária, sob
a alegação de doença preexistente, é ilícita se não demonstrada a má-fé do
segurado por ocasião da contratação do seguro de vida ou saúde, salvo prévio
exame médico pela seguradora".
Honorários: obrigação de indenizar (entrada+êxito) + dano moral quando houver
recusa abusiva prolongada + sucumbência.

=== TESE 2 — SEGURO DPVAT (invalidez permanente / morte / DAT) ===
Gatilho: acidente de trânsito com sequela permanente, morte da vítima, ou
recusa/atraso do pagamento do DPVAT (hoje operado por consórcio de
seguradoras/Seguradora Líder e, a partir da reestruturação legal, por outro
arranjo). Lead: vítima com sequela ou família enlutada, muitas vezes sem saber
que o DPVAT existe independentemente de culpa no acidente.
Diagnóstico: houve boletim de ocorrência ou registro do acidente? há laudo do
IML ou perícia médica atestando o grau da invalidez? o pagamento administrativo
foi negado, foi pago a menor, ou simplesmente nunca foi requerido? há nexo
causal direto entre o acidente e a sequela apontada? o valor recebido (se
houve) veio com tabela de proporcionalidade da invalidez?
Reposicionamento: o DPVAT independe de culpa e é devido pelo simples fato do
acidente de trânsito com vítima; o valor pago a menor pode ser complementado
judicialmente quando a perícia administrativa subestimou o grau da lesão.
Objeções -> respostas: "já recebi o DPVAT" -> pagamento administrativo não
impede ação de complementação se o grau de invalidez foi mal aferido; "não
tenho boletim de ocorrência" -> BO não é requisito absoluto, mas fortalece a
prova; cabe reunir prova alternativa (atendimento hospitalar, laudo, testemunha);
"já passou muito tempo" -> verificar prazo prescricional trienal (art. 206,
§3º, IX, CC) contado do evento ou do pagamento administrativo a menor.
Honorários: ação de cobrança/complementação (entrada+êxito) + sucumbência.

=== TESE 3 — SEGURO PRESTAMISTA (financiamento/consórcio) NEGADO ===
Gatilho: mutuário falece ou fica inválido durante financiamento (imóvel,
veículo, crédito consignado) com seguro prestamista contratado junto ao banco;
seguradora nega quitação alegando doença preexistente, atividade de risco não
declarada, ou suicídio nos primeiros 2 anos. Família segue recebendo cobrança
do banco/financeira mesmo com o seguro contratado para cobrir exatamente essa
hipótese.
Diagnóstico: o seguro foi contratado junto com o financiamento (venda casada
frequentemente não informada com clareza)? qual a causa da morte/invalidez? a
negativa citou doença preexistente, atividade de risco, ou período de carência
para suicídio? o banco segue cobrando parcelas apesar do sinistro? há cópia da
apólice ou só o contrato de financiamento menciona o seguro?
Reposicionamento: o prestamista existe para proteger a família da dívida
justamente no momento de fragilidade; a negativa devolve à família o peso que
o seguro foi contratado para tirar.
Objeções -> respostas: "doença preexistente" -> mesma lógica da Súmula
609/STJ, se não houve exame prévio; "suicídio nos 2 primeiros anos" -> a
cobertura é devida se decorridos os 2 anos de vigência (art. 798, CC), e
mesmo dentro do prazo a prova de premeditação é da seguradora, não se
presumindo pelo mero ato; "não achei a apólice" -> banco e seguradora são
obrigados a fornecer via; solicitação formal + Procon/SUSEP como reforço;
"banco continua cobrando" -> pedido de tutela para suspender a cobrança
durante a discussão do sinistro.
Honorários: obrigação de quitar o saldo devedor (entrada+êxito sobre o valor
quitado) + dano moral pela negativa/cobrança indevida + sucumbência.

=== TESE 4 — ATRASO NO PAGAMENTO DA INDENIZAÇÃO (sem negativa formal) ===
Gatilho: seguradora não nega, mas também não paga — sinistro "em análise" por
meses, pedidos repetidos de documentos adicionais, silêncio administrativo.
Lead: segurado ou beneficiário cansado de "protocolos" sem resposta, com
necessidade financeira concreta (funeral, reforma, substituição do bem
sinistrado).
Diagnóstico: quando o sinistro foi avisado (data do protocolo)? quantos
pedidos de documento adicional já foram feitos, e todos foram atendidos? há
prazo regulatório da SUSEP para resposta da seguradora e ele já foi
ultrapassado? há urgência financeira concreta (contas, dívidas represadas)?
existe correspondência escrita (e-mail, protocolo) documentando os pedidos?
Reposicionamento: mora da seguradora em analisar não é neutra: gera direito a
juros, correção e, quando abusiva/reiterada, dano moral autônomo.
Objeções -> respostas: "ainda estão analisando" -> prazo regulatório de
resposta não é indefinido; ultrapassado o prazo, cabe cobrança judicial da
obrigação e dos consectários da mora; "preciso mandar mais um documento" ->
verificar se o pedido é razoável ou passa a configurar exigência protelatória
repetitiva.
Honorários: cobrança da indenização + consectários da mora (entrada+êxito) +
dano moral quando a demora for abusiva + sucumbência.

=== TESE 5 — SEGURO DE VIDA/SAÚDE: NEGATIVA POR "AGRAVAMENTO" NÃO COMUNICADO ===
Gatilho: seguradora recusa pagamento alegando que o segurado passou a exercer
atividade de risco, mudou de profissão, ou teve piora de saúde e "não
comunicou" a mudança durante a vigência, tratando isso como quebra do dever de
informar.
Lead: sente que está sendo penalizado por viver a vida (mudar de emprego,
adoecer) e não por ter agido de má-fé.
Diagnóstico: o contrato previa expressamente dever de comunicar essa mudança
específica, ou é interpretação da seguradora? a mudança de fato teve relação
causal com o sinistro, ou é genérica? o segurado seguiu pagando o prêmio sem
qualquer aviso da seguradora sobre mudança de risco? há cláusula de
agravamento redigida de forma clara (dever de informação prévia e clara do
segurador, art. 54, CDC quando aplicável)?
Reposicionamento: agravamento de risco só autoriza recusa se houver nexo causal
com o sinistro e se o dever de comunicar estava expresso e claro; cláusula
genérica ou de interpretação duvidosa se resolve a favor do segurado
(interpretação contra o estipulante/contrato de adesão).
Objeções -> respostas: "mudou de profissão e não avisou" -> analisar se a nova
atividade tem relação com a causa do sinistro; sem nexo causal, a omissão é
irrelevante para a indenização; "cláusula prevê perda de direito automática" ->
perda de direito por agravamento de risco depende de prova de nexo causal e
de má-fé, não é automática.
Honorários: obrigação de indenizar (entrada+êxito) + dano moral quando cabível
+ sucumbência.

=== REUNIÃO DE FECHAMENTO (4 fases + fase zero) ===
0. Reposicionar a narrativa (de quem "pede favor" à seguradora para quem
   exerce direito contratual já pago).
1. Diagnóstico (perguntas da tese; ouvir mais que falar).
2. Dimensionar valor e urgência REAIS (nunca ancoragem de ROI/"invista X para
   receber Y" — não faz sentido técnico e fere a ética).
3. Proposta transparente: frentes cabíveis + estrutura de honorários, sem
   promessa de resultado; onde há divergência de fato (nexo causal, prova de
   má-fé), dizer isso com franqueza. A confiança vem da franqueza, não da
   euforia.
4. Conduzir à decisão: urgência real como razão de não adiar (prazo
   prescricional, dívida do prestamista correndo, necessidade financeira); se
   pedir tempo, respeitar e combinar retorno em prazo definido. Fechar não é
   pressionar; é não deixar a conversa morrer por inércia.

=== HONORÁRIOS (modelos por frente — NUNCA por mensagem) ===
Obrigação de indenizar/quitar saldo: entrada + êxito sobre o valor recebido ou
quitado. Dano moral (quando cabível pela recusa/demora abusiva): percentual
sobre a condenação. Complementação de DPVAT: percentual sobre a diferença
apurada. Sucumbência: fonte adicional que não sai do bolso do cliente.
"Quanto custa?" antes da reunião: explicar que depende das frentes mapeadas
na documentação e que boa parte é por êxito — número só em reunião reservada
(Provimento 205/2021).

=== DOSSIÊ DOCUMENTAL POR TESE ===
Preexistência/agravamento (Tese 1 e 5): apólice completa + proposta de
contratação (para verificar se houve exame prévio) + laudo/atestado de óbito
ou relatório médico com CID + carta de negativa por escrito + comprovantes de
pagamento do prêmio.
DPVAT (Tese 2): boletim de ocorrência (se houver) + laudo do IML ou perícia
médica + comprovante de atendimento hospitalar + comprovante de pagamento
administrativo já recebido (se houver) + documentos pessoais da vítima/
beneficiários.
Prestamista (Tese 3): contrato de financiamento + apólice ou certificado de
adesão ao seguro + carta de negativa + extrato de cobrança do banco pós-
sinistro + atestado de óbito/laudo de invalidez.
Atraso (Tese 4): protocolo de aviso de sinistro com data + toda a
correspondência trocada (e-mails, prints, protocolos de pedidos de documento)
+ comprovante de necessidade financeira quando reforçar o pedido de urgência.
Não precisa de tudo para protocolar, mas dossiê completo = tutela mais
robusta.

=== FAQ DO LEAD ===
1. "Quanto custa contratar vocês?" -> Depende das frentes mapeadas na
   documentação; boa parte do trabalho é por êxito. O número exato só é dado
   em reunião reservada, nunca por mensagem (Provimento 205/2021).
2. "Quanto tempo demora um processo assim?" -> Varia por comarca, complexidade
   da prova e se há tutela de urgência; dá para estimar faixa realista na
   reunião, mas ninguém garante prazo exato.
3. "Vou ter que ir ao fórum?" -> Na maior parte dos casos não; a presença
   pessoal costuma ser exigida só em audiência específica (perícia, conciliação
   presencial), e isso é raro e combinado com antecedência.
4. "Posso perder o processo?" -> Sim, é possível; por isso a avaliação inicial
   é honesta sobre os pontos fortes e fracos do caso antes de qualquer decisão
   de entrar com a ação.
5. "A seguradora pode cancelar meu contrato se eu processar?" -> Cobrar um
   direito não é causa legítima de cancelamento; se houver risco disso, pode-se
   pedir proteção específica nesse sentido.
6. "Preciso pagar algo adiantado?" -> A estrutura de honorários é apresentada
   de forma transparente na reunião; muitos casos combinam entrada menor com
   parte relevante por êxito.
7. "Vocês garantem que eu vou receber o valor todo?" -> Não; ninguém pode
   prometer resultado de processo judicial. O trabalho é construir o caso mais
   forte possível com a prova disponível.
8. "Já faz tempo que aconteceu, ainda dá tempo?" -> Depende do prazo
   prescricional aplicável ao caso (varia por tipo de seguro e situação); por
   isso a análise do prazo é uma das primeiras coisas verificadas.
9. "Posso negociar direto com a seguradora enquanto isso?" -> Pode, mas
   qualquer proposta de acordo deve ser avaliada antes de aceitar, para não
   quitar o caso por valor menor do que o devido.
10. "E se eu não tiver todos os documentos?" -> Não precisa ter tudo para
    começar; o que existir já ajuda a avaliar o caso, e o restante pode ser
    buscado (segunda via com a seguradora, hospital, órgãos públicos) ao longo
    do processo.
11. "Vocês cobram para analisar meu caso?" -> Não; a reunião inicial de
    avaliação do caso não tem custo. O que é cobrado, e explicado com
    transparência, é o trabalho de fato caso decida contratar.
12. "Como funciona o atendimento depois que eu contrato?" -> Você recebe um
    ponto de contato responsável pelo caso e é avisado nos marcos relevantes
    (protocolo, decisões, prazos de resposta); dúvidas pontuais têm canal
    direto, sem precisar reabrir tudo a cada mensagem.
13. "Vou conseguir acompanhar o andamento do processo?" -> Sim; o andamento é
    público na maior parte dos casos e o escritório também informa sobre
    decisões e próximos passos relevantes, sem precisar você mesmo consultar
    o sistema do tribunal.
14. "Como eu envio os documentos para vocês?" -> Pelo canal do atendimento
    mesmo (WhatsApp ou e-mail, conforme combinado), com organização simples;
    não precisa protocolo formal para enviar prova nem se preocupar com
    formatação.
15. "Existe prazo para eu processar a seguradora?" -> Sim, existe prazo
    prescricional que varia conforme o tipo de seguro e a situação (recusa,
    atraso, cobrança indevida); por isso vale procurar avaliação assim que a
    negativa ou o problema aparecer, sem deixar o tempo passar.
16. "Por que contratar um especialista em seguros e não um advogado
    generalista?" -> Litígio contra seguradora tem particularidades técnicas
    (regulação SUSEP, cláusulas de carência e exclusão, prazos próprios) que
    pesam na estratégia; atuação focada nessas causas tende a reconhecer
    padrões que passam despercebidos em análise genérica.
17. "Se eu esperar mais um pouco antes de agir, faz diferença?" -> Pode fazer;
    prazo prescricional corre, prova se perde (testemunhas, documentos,
    memória de fatos) e, em caso de dívida vinculada ao seguro (prestamista),
    a cobrança e os encargos seguem avançando enquanto nada é feito.
18. "Existe algum risco de eu piorar minha situação processando?" -> Buscar um
    direito contratual não é, por si, motivo de retaliação legítima; quando
    existe indício real de risco, isso é mapeado e tratado na estratégia
    desde o início.
19. "Vocês recebem só se eu ganhar?" -> A estrutura de honorários costuma
    combinar uma parte fixa menor com uma parte relevante vinculada ao
    resultado; os termos exatos, sempre dentro do que a regra da advocacia
    permite, são fechados na reunião reservada.
20. "O que acontece se eu perder o processo?" -> Além de não obter o valor
    pleiteado, pode haver condenação em custas e honorários da parte
    contrária conforme a sucumbência; por isso a avaliação inicial de
    viabilidade é honesta antes de qualquer decisão de entrar com a ação.
21. "Dá para recorrer se a decisão for contra mim?" -> Sim, existe via
    recursal cabível conforme a decisão e a instância; ela é avaliada caso a
    caso, sempre pesando custo, prazo e chance real de reversão.
22. "Eu tenho direito à gratuidade de justiça?" -> Depende da sua condição
    econômica declarada e comprovável; quando cabível, o pedido evita o
    pagamento adiantado de custas processuais, e isso é avaliado já na
    reunião inicial.
23. "Preciso assinar procuração ou algo assim para começar?" -> Sim, a
    procuração formaliza a representação e costuma ser assinada
    eletronicamente, sem precisar de reconhecimento de firma em cartório na
    maioria dos casos.
24. "A seguradora pode simplesmente ignorar a ação?" -> Não; uma vez citada,
    a seguradora tem prazo para responder, e a ausência de defesa gera
    consequências processuais (revelia) que costumam favorecer quem
    processou.
25. "Meu caso é pequeno demais para valer a pena?" -> Isso é avaliado na
    reunião, olhando o valor em disputa frente ao tempo e custo estimado;
    seguimos com transparência sobre se compensa ou não antes de qualquer
    contratação.
26. "Posso trocar de seguradora enquanto o processo corre?" -> Em geral sim,
    a contratação de novo seguro é decisão sua e independente do processo em
    curso contra a seguradora anterior.
27. "O que conta como prova de que a seguradora agiu errado?" -> A carta de
    negativa por escrito, protocolos de atendimento, e-mails e prints de
    conversa costumam ser as provas mais fortes; quanto mais documentado o
    histórico, mais robusto o caso.
28. "Vocês atendem mesmo eu não morando na cidade do escritório?" -> Sim, a
    maior parte do atendimento e do processo é conduzida remotamente, com
    comunicação por telefone, WhatsApp e videochamada quando necessário.
29. "Por que agir agora e não esperar eu me organizar melhor?" -> Quanto mais
    cedo o caso é avaliado, mais tempo sobra para reunir prova, preservar
    prazo e, se houver urgência financeira real, buscar tutela que antecipe
    parte do resultado.
30. "Se eu já aceitei um valor menor da seguradora, ainda dá para fazer
    algo?" -> Depende de como e o que foi aceito (quitação total ou parcial,
    vício de consentimento); vale trazer o que foi assinado para uma
    avaliação específica antes de descartar a possibilidade.

=== GOVERNANÇA DO AGENTE ===
- Urgência sempre real (prazo prescricional, dívida do prestamista correndo,
  necessidade financeira concreta), nunca fabricada; expectativa honesta
  quando houver divergência de fato ou de prova; zero promessa de resultado;
  honorários e valores nunca em mensagem/conteúdo público (Provimento
  205/2021).
- Fundamento-âncora serve à conversa; antes do protocolo, todo precedente é
  conferido na fonte oficial (fluxo da casa: agente da matéria + revisão).
- Caso de luto recente, sofrimento agudo, ou urgência financeira grave
  (funeral sem recurso, cobrança judicial iminente do prestamista): acolher,
  coletar o essencial e ESCALAR a humano imediatamente; o agente nunca segura
  sozinho um caso de risco.
- Envio real de mensagem é ação externa: gates + template/janela do canal.
"""

PLAYBOOK_SEGUROS_SOURCES = [
    "Súmula 609/STJ (recusa por doença preexistente sem exame prévio)",
    "Lei 9.656/98 (planos e seguros saúde, por analogia às frentes de saúde)",
    "Código Civil, art. 798 (seguro de vida — período de carência de suicídio)",
    "Código Civil, art. 206, §1º, II, 'b' e §3º, IX (prazos prescricionais)",
    "Código de Defesa do Consumidor, art. 54 (contrato de adesão)",
    "Legislação do seguro DPVAT (indenização por acidente de trânsito)",
    "Normas SUSEP sobre prazos de resposta e regulação do sinistro",
    "Provimento 205/2021 OAB (publicidade)",
]
