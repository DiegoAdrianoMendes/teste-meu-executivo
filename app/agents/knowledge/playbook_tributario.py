"""Base de conhecimento de atendimento e conversão em Direito Tributário.

Mesma anatomia e profundidade do guia de vendas de saúde
(`direito_saude_vendas.py`, padrão-ouro da casa), adaptada às teses
tributárias de maior recorrência em escritório de porte médio: isenção de
Imposto de Renda por moléstia grave, recuperação de créditos tributários e
defesa em execução fiscal. Fonte única dos agentes de atendimento a leads
tributários. Fundamentos-âncora dão segurança na CONVERSA; não substituem a
conferência do precedente na fonte oficial antes do protocolo (regra da casa:
validar jurisprudência antes de protocolar).
"""

PLAYBOOK_TRIBUTARIO_VERSION = "playbook-tributario-2026-07"

PLAYBOOK_TRIBUTARIO = """
=== PRIMEIRA MENSAGEM (norma da casa — tom, não fórmula) ===
A primeira resposta ao lead tributário segue este ESPÍRITO, com variação
natural (a conversa precisa fluir; nunca despejar tudo de uma vez):
1. Assumir o atendimento com nome próprio ("Olá, eu vou seguir com o seu
   atendimento daqui").
2. Acolher quando a dor comportar: no lead-aposentado/doente, dizer que sente
   muito pela situação de saúde antes de qualquer pergunta tributária; no
   lead-empresário, o acolhimento é mais sóbrio (frustração com cobrança
   indevida ou aperto de caixa), mas ainda assim reconhecer o incômodo antes
   de entrar em técnica.
3. Nomear a urgência sem dramatizar quando ela existir de fato: prazo de
   restituição correndo, execução fiscal com risco de penhora, prescrição se
   aproximando — "sei que isso pode estar pesando no seu bolso" ou "sei que
   prazo de Receita/execução preocupa".
4. Abrir a porta dos documentos JÁ: convidar (não exigir) o envio de
   declarações de IR, laudo médico, CDA, extrato do processo executivo —
   "se quiser já me enviar os documentos, vou analisando enquanto
   conversamos".
5. Deixar claro que não há cobrança de consulta inicial: é uma honra marcar a
   reunião inicial do caso e dar um retorno preciso.
Regras de forma: UMA pergunta por mensagem (interrogatório em bloco trava);
sem juridiquês; nunca prometer resultado (nem valor de restituição, nem
êxito na execução); honorários NUNCA por mensagem nem em conteúdo público
(Provimento 205/2021) — é assunto de reunião reservada.

=== O LEAD TRIBUTÁRIO (quem é e o que sente) ===
Dois perfis dominam. (1) Aposentado ou servidor com doença grave: descobre
por acaso (contador, associação, notícia) que pode não pagar Imposto de
Renda sobre a aposentadoria; sente indignação retroativa ("pagando isso
todo mês sem precisar?") e insegurança médico-burocrática (não sabe se sua
doença "conta"). (2) Empresário ou pessoa física com passivo tributário:
chega com uma citação de execução fiscal na mão (medo de penhora, bloqueio
de conta, nome sujo) ou com a sensação difusa de estar pagando tributo a
mais (PIS/COFINS, ICMS na base de cálculo) sem saber quantificar. A dúvida
que governa a conversa (quase nunca verbalizada): "isso que a Receita/o
Fisco está cobrando de mim está mesmo certo, ou eu tenho algo a reaver ou a
me defender?". O 1º contato não é para fechar contrato: é para responder
essa dúvida com autoridade e sem juridiquês. A virada de postura acontece
quando o lead entende que o ônus de provar a cobrança está com o Fisco, não
com ele.

=== MÉTODO (funil de 8 passos) ===
Lead -> Qualificação (viável x inviável: existe o direito material, ou o
tributo é mesmo devido?) -> Diagnóstico (fatos e prova, uma pergunta por
vez) -> Reposicionamento (de contribuinte resignado para titular de
direito/defesa legítima) -> Valor/Urgência (SEMPRE real: prazo decadencial
de 5 anos para restituir, prazo prescricional da execução, citação já
recebida, prazo para embargos/exceção — nunca urgência fabricada) ->
Reunião (estrutura com começo/meio/fim; não é consulta grátis; termina com
proposta clara) -> Proposta transparente (frentes cabíveis + honorários
claros, sem promessa de valor de êxito) -> Contrato.
Princípios: vende quem oferta (lead esfria sem proposta concreta); ouvir
antes de explicar (quem narra o caso se compromete com a solução); uma
pergunta por vez no WhatsApp; em tributário, número (valor de restituição,
valor da dívida) é sempre estimativa até o cálculo formal — nunca prometer
o número exato antes de rodar a planilha.

=== TESE 1 — ISENÇÃO DE IR POR MOLÉSTIA GRAVE ===
Gatilho: aposentado, pensionista ou servidor inativo com diagnóstico de
doença grave listada em lei que descobre (por conta própria, associação de
classe ou indicação médica) que pode deixar de pagar IR sobre os
proventos. Lead: pessoa idosa ou com saúde fragilizada, muitas vezes já
cansada de burocracia; a dor é dupla — a doença e a sensação de estar
pagando algo que não devia.
Diagnóstico (1 por vez): a renda é de aposentadoria/pensão/reforma (não
salário de quem ainda trabalha)? qual o diagnóstico exato e desde quando?
tem laudo médico (particular ou de perícia oficial do INSS/serviço público)
com CID? o pagamento do IR é retido na fonte (INSS, órgão público) ou
recolhido por conta própria? há quanto tempo recebe o benefício com o
diagnóstico já existente? já tentou pedir a isenção administrativamente à
Receita ou ao órgão pagador?
Reposicionamento: a isenção é DIREITO do aposentado/pensionista com doença
grave, não benefício discricionário da Receita; a retenção continuada é
que precisa se justificar.
Objeções -> respostas: "só vale para quem se aposentou por causa da
doença" -> não: a Súmula 598/STJ afasta essa exigência, a isenção alcança
quem foi diagnosticado depois de já aposentado; "preciso de laudo do
INSS/perícia oficial" -> a própria Súmula 598/STJ dispensa laudo oficial
quando a doença grave está comprovada por outros meios (laudos e exames
particulares idôneos); "minha doença não está na lista" -> conferir a lista
taxativa do art. 6º, XIV, da Lei 7.713/1988 (ex.: neoplasia maligna,
cardiopatia grave, doença de Parkinson, esclerose múltipla, nefropatia
grave, hepatopatia grave, cegueira, hanseníase, AIDS, entre outras) — fora
da lista, o caminho jurídico muda e isso é dito com franqueza; "recebo
salário, não aposentadoria" -> a isenção do art. 6º, XIV, é para proventos
de aposentadoria/reforma/pensão, não para quem ainda está em atividade —
alinhar expectativa desde já.
Fundamentos-âncora: Lei 7.713/1988, art. 6º, XIV; Súmula 598/STJ. Prazo:
restituição administrativa e ação de repetição de indébito observam o
prazo de 5 anos (CTN, art. 168); pedidos futuros (mês a mês, enquanto durar
a doença) seguem daqui para frente.
Honorários: ação declaratória de isenção + repetição do indébito dos
últimos 5 anos (percentual sobre o valor restituído) + acompanhamento da
não incidência futura.

=== TESE 2 — RECUPERAÇÃO DE CRÉDITOS TRIBUTÁRIOS ===
Gatilho: empresário que desconfia de estar pagando tributo a mais (base de
cálculo inchada, bitributação, recolhimento indevido) ou que já identificou
crédito não aproveitado; muitas vezes vem de um contador que "ouviu falar"
de uma tese, sem saber quantificar nem viabilizar.
Diagnóstico: qual regime tributário (Simples, Lucro Presumido, Lucro
Real)? quais tributos federais/estaduais/municipais recolhe com
regularidade? tem notas fiscais e apurações dos últimos 5 anos
organizadas? já fez algum pedido administrativo de restituição ou
compensação? o contador já apontou alguma discrepância específica (ex.:
ICMS destacado na nota compondo a base de PIS/COFINS)? há débitos em
aberto que poderiam ser compensados com o crédito a recuperar?
Reposicionamento: recuperar crédito não é "vantagem contábil", é corrigir
um recolhimento que nunca deveria ter sido feito daquele jeito — o dinheiro
já é do contribuinte.
Objeções -> respostas: "a Receita nunca vai devolver" -> a via judicial
existe exatamente para isso, com trânsito em julgado que vincula o Fisco;
"não vale a pena o custo do processo" -> por isso o dimensionamento vem
antes da proposta, com estimativa de valor e frentes de honorários por
êxito; "isso não vai me colocar na mira da fiscalização" -> pleitear
crédito que a lei reconhece não é fato gerador de fiscalização adicional;
"quanto tempo demora?" -> tributário tende a ser mais lento que outras
áreas (Fazenda recorre bastante), isso é dito com franqueza desde a
reunião.
Fundamentos-âncora: CTN, arts. 165 e 168 (repetição de indébito, prazo
quinquenal); CTN, art. 170 (compensação); Tema 69/STF (RE 574.706 — exclusão
do ICMS da base de cálculo do PIS/COFINS, "tese do século", paradigma da
possibilidade de expurgar tributo da base de outro tributo). Onde a
discussão específica do cliente for tese nova, correlata ou ainda em
discussão nos tribunais (ex.: outras exclusões de base por analogia),
isso é sinalizado como PONTO A CONFIRMAR na fonte oficial antes de
qualquer promessa ao lead — nunca citar número de tema/recurso sem
certeza.
Honorários: ação de repetição de indébito/compensação (percentual sobre o
valor recuperado ou compensado) + consultoria de regularização futura
(honorário fixo) + eventual mandado de segurança preventivo.

=== TESE 3 — DEFESA EM EXECUÇÃO FISCAL ===
Gatilho: citação em execução fiscal (federal, estadual ou municipal),
penhora ou bloqueio (BacenJud/SisbaJud) já efetivado, ou notificação de
inclusão de sócio no polo passivo. Lead: medo imediato e concreto (conta
bloqueada, nome sujo, patrimônio em risco); às vezes é sócio ou
ex-sócio surpreendido por cobrança de dívida da empresa.
Diagnóstico (1 por vez): já foi citado ou apenas notificado
administrativamente? tem a Certidão de Dívida Ativa (CDA) em mãos? qual o
tributo, o período e o valor originário? já houve penhora, bloqueio ou
qualquer constrição? a cobrança é contra a empresa, contra você
pessoalmente, ou tentam redirecionar para você como sócio? há quanto tempo
o débito é discutido (para checar prescrição)? a empresa está ativa,
encerrada irregularmente ou em processo formal de baixa?
Reposicionamento: execução fiscal não é sentença definitiva de que a
dívida existe do jeito que está cobrada — a CDA goza de presunção relativa,
que se derruba com prova e técnica; o contribuinte não está desarmado.
Objeções -> respostas: "já fui citado, não dá mais para discutir" ->
discutir prescrição, nulidade da CDA e excesso de execução é possível a
qualquer momento processual adequado (exceção de pré-executividade para
matéria de ordem pública, sem dilação probatória — Súmula 393/STJ;
embargos à execução quando há garantia do juízo e matéria que exige prova);
"vão bloquear minha conta pessoal" -> isso só é legítimo se houver base
para responsabilizar o sócio (art. 135 do CTN); mero inadimplemento da
empresa NÃO gera responsabilidade pessoal (Súmula 430/STJ); dissolução
irregular da empresa autoriza presunção de responsabilidade do sócio-gerente
(Súmula 435/STJ) — e essa presunção admite defesa; "isso é muito antigo,
ainda pode cobrar?" -> checar prescrição (CTN, art. 174) e, em execução já
em curso e parada por muito tempo sem localização de bens, a prescrição
intercorrente (Lei 6.830/1980, art. 40) — tema consolidado em recurso
repetitivo do STJ sobre o rito de suspensão/arquivamento e o prazo de um
ano antes de correr a prescrição; o número exato do repetitivo é conferido
na fonte antes do protocolo; "não tenho como pagar" -> parcelamento
administrativo é caminho paralelo à defesa técnica, e os dois podem ser
avaliados juntos na reunião.
Fundamentos-âncora: CTN, art. 174 (prescrição do crédito tributário); Lei
6.830/1980 ("Lei de Execução Fiscal"), art. 2º, §5º e art. 40 (requisitos
da CDA; suspensão/arquivamento e prescrição intercorrente); Súmula
393/STJ (exceção de pré-executividade cabe para matérias de ordem pública
cognoscíveis de ofício, sem necessidade de prova); Súmula 430/STJ
(inadimplemento não gera, por si só, responsabilidade do sócio-gerente);
Súmula 435/STJ (dissolução irregular presume responsabilidade do
sócio-gerente); CTN, art. 135 (responsabilidade de terceiros). O prazo e o
rito exatos da prescrição intercorrente variam conforme a lei local e o
ente exequente (federal/estadual/municipal); conferir o precedente
específico na fonte antes de peticionar.
Honorários: exceção de pré-executividade ou embargos à execução (fixo ou
percentual sobre o valor da dívida discutida/excluída) + êxito sobre valor
extinto ou reduzido + acompanhamento de eventual parcelamento.

=== REUNIÃO DE FECHAMENTO (4 fases + fase zero) ===
0. Reposicionar a narrativa (de contribuinte resignado/assustado para
   titular de direito ou parte com defesa legítima).
1. Diagnóstico (perguntas da tese; ouvir mais que falar).
2. Dimensionar valor e urgência REAIS (prazo decadencial/prescricional,
   citação já recebida, bloqueio em curso — nunca ancoragem de ROI/"invista
   X para receber Y", e nunca prometer o valor exato de restituição ou de
   redução de dívida antes do cálculo formal).
3. Proposta transparente: frentes cabíveis + estrutura de honorários, sem
   promessa de resultado; onde a tese for nova ou divergente (ex.:
   discussões de crédito ainda não pacificadas), dizer e registrar por
   escrito. A confiança vem da franqueza, não da euforia.
4. Conduzir à decisão: urgência real como razão de não adiar (prazo
   corre); se pedir tempo, respeitar e combinar retorno em prazo definido
   (o caso esfria rápido e reaquece com follow-up no prazo combinado).
   Fechar não é pressionar; é não deixar a conversa morrer por inércia.

=== HONORÁRIOS (modelos por frente — NUNCA por mensagem) ===
Isenção de IR: entrada + percentual sobre o valor restituído (repetição de
indébito) + honorário de acompanhamento da isenção futura. Recuperação de
crédito: percentual sobre o valor recuperado/compensado, normalmente por
êxito, dado o prazo mais longo da via judicial tributária. Defesa em
execução fiscal: fixo pela peça técnica (exceção/embargos) + percentual de
êxito sobre o valor excluído, reduzido ou extinto da dívida. "Quanto
custa?" antes da reunião: explicar que depende das frentes mapeadas na
documentação e que parte relevante costuma ser por êxito — número só em
reunião reservada (Provimento 205/2021).

=== DOSSIÊ DOCUMENTAL (pedir cedo, sem exigir tudo) ===
Isenção de IR: laudo médico com CID (oficial ou particular idôneo),
comprovante de aposentadoria/pensão, declarações de IR dos últimos 5 anos,
informe de rendimentos com a retenção. Recuperação de crédito: apurações e
guias dos tributos discutidos, notas fiscais representativas, contrato
social/regime tributário, eventual protocolo administrativo prévio.
Execução fiscal: citação/mandado, cópia integral da CDA, comprovante de
qualquer penhora/bloqueio, contrato social e atas (se houver discussão de
responsabilidade de sócio), histórico de tentativas de localização de bens
(para prescrição intercorrente). Não precisa de tudo para protocolar, mas
dossiê completo = defesa/pedido mais robusto.

=== FAQ DO LEAD (perguntas reais, respostas honestas) ===
1. "Quanto custa?" -> Depende da frente e costuma ter componente de êxito;
   número exato só sai na reunião reservada, depois de ver a documentação
   (Provimento 205/2021 veda valor em conteúdo público).
2. "Quanto tempo demora?" -> Tributário tende a ser mais lento que outras
   áreas, porque a Fazenda Pública recorre com frequência; isso é dito
   sem rodeio na reunião, com prazo estimado por instância.
3. "Vou ter que ir ao fórum?" -> Normalmente não: a atuação é majoritariamente
   escrita (petições, provas documentais); presença pessoal costuma ser
   dispensável, salvo situação excepcional que será avisada com
   antecedência.
4. "Posso perder?" -> Sim, é possível; nenhuma tese tributária tem resultado
   garantido, e nas teses ainda divergentes (algumas discussões de crédito)
   isso é reforçado na reunião antes de contratar.
5. "Se eu processar, corro o risco de ser fiscalizado?" -> Pleitear direito
   reconhecido em lei ou em precedente não é fato gerador de fiscalização
   adicional; a preocupação é compreensível, mas não corresponde à prática.
6. "Minha conta pode ser bloqueada de novo?" -> Se já houve bloqueio, o
   objetivo imediato é discutir a validade da cobrança e, se cabível, pedir
   a liberação; se ainda não houve, agir cedo reduz esse risco.
7. "Posso parcelar em vez de discutir?" -> As duas coisas podem ser
   avaliadas juntas; parcelar não impede, a depender do caso, de discutir
   parte do valor.
8. "E se eu não tiver todos os documentos?" -> Começa-se com o que existe;
   o dossiê completo fortalece o caso, mas a ausência de um documento não
   impede a análise inicial.
9. "Isso vale para qualquer imposto/tributo?" -> Não; cada tese tem
   requisitos próprios (tipo de renda, tipo de tributo, ente cobrador) e
   isso é verificado no diagnóstico antes de qualquer promessa.
10. "Por que contratar advogado e não resolver direto com a Receita/o
    contador?" -> A via administrativa é possível e às vezes é o primeiro
    passo; a atuação jurídica entra quando é preciso discutir o mérito,
    ganhar segurança jurídica (trânsito em julgado) ou reagir a uma
    execução já em curso.
11. "Qual o prazo para eu pedir a restituição do IR pago indevidamente?" ->
    5 anos (CTN, art. 168); passado esse prazo, o valor daquele período
    específico já não é mais recuperável, mas a isenção segue valendo para
    frente — por isso quanto antes o pedido é formalizado, mais meses
    entram na conta.
12. "Se eu perder, tenho que pagar a dívida da Fazenda (honorários de
    sucumbência)?" -> Existe esse risco, como em qualquer ação; ele é
    explicado com números e cenário na reunião, não escondido.
13. "Existe gratuidade de justiça em processo tributário?" -> Sim, quando a
    situação econômica do lead comprova a hipossuficiência (custas e
    honorários periciais podem ser dispensados); isso é avaliado caso a
    caso na análise documental.
14. "Cabe recurso se eu perder na primeira instância?" -> Sim, apelação e,
    dependendo da matéria, recursos aos tribunais superiores; cada etapa
    tem prazo próprio e é avaliada conforme o resultado da decisão.
15. "Como funciona o atendimento no dia a dia, é tudo por WhatsApp?" -> O
    diagnóstico inicial e o envio de documentos costumam ser por WhatsApp;
    a reunião de abertura do caso e decisões importantes são feitas com
    mais formalidade, por chamada ou presencialmente, conforme sua
    preferência.
16. "Depois que eu contrato, como acompanho o andamento?" -> Você recebe
    atualização a cada movimentação relevante do processo, não fica
    dependendo de perguntar; prazos e próximos passos são informados com
    antecedência.
17. "Preciso ir pessoalmente ao escritório para assinar o contrato?" ->
    Normalmente não; assinatura eletrônica e envio digital de documentos
    resolvem a maior parte dos casos, presença física só quando o caso
    exigir.
18. "O que acontece se eu não fizer nada e deixar como está?" -> No IR por
    moléstia grave, a retenção indevida continua mês a mês e o prazo de 5
    anos para reaver o passado corre contra você; na execução fiscal, o
    processo segue seu curso e pode chegar à penhora sem defesa
    apresentada.
19. "Por que buscar um escritório especializado em tributário e não um
    advogado generalista?" -> A área tem regras técnicas próprias (prazos
    específicos, teses consolidadas nos tribunais superiores, praxe da
    Fazenda) que mudam com frequência; quem atua com foco na matéria
    acompanha essas mudanças de perto, o que ajuda a evitar apostas em
    teses já superadas.
20. "Por que agir agora e não esperar 'ver como fica' a situação?" -> Na
    isenção de IR, cada mês que passa sem o pedido é um mês a menos de
    restituição possível dentro dos 5 anos; na execução fiscal, esperar
    reduz o tempo disponível para reagir antes de uma penhora.
21. "Vocês cobram para analisar meus documentos antes de eu decidir?" ->
    Não; a análise inicial da documentação e a reunião de abertura do caso
    não têm custo, é parte de dar um retorno preciso antes de qualquer
    contratação.
22. "Como funcionam os honorários por êxito, sem eu me comprometer com um
    valor fixo alto?" -> Em boa parte das frentes tributárias, parte
    relevante do honorário é atrelada ao resultado (valor restituído,
    recuperado ou excluído da dívida); a estrutura exata é apresentada e
    combinada na reunião reservada, nunca por mensagem.
23. "E se durante o processo eu tiver outro problema com a Receita, o
    escritório já está a par do meu caso?" -> Sim; o histórico documental e
    o acompanhamento ficam registrados no seu caso, o que agiliza qualquer
    nova frente relacionada.
24. "Meu contador pode participar do processo junto com o advogado?" ->
    Pode e costuma ajudar; contador e advogado atuam em frentes
    complementares (apuração e técnica processual), o alinhamento entre os
    dois fortalece o caso.
25. "Existe algum custo de cartório ou taxa que eu preciso pagar durante o
    processo?" -> Pode haver custas processuais e taxas específicas
    (perícia, por exemplo); quando existirem, isso é informado com
    antecedência, e a gratuidade de justiça pode dispensar boa parte
    delas quando cabível.
26. "Quanto tempo eu tenho depois de receber uma citação de execução fiscal
    para agir?" -> O prazo processual para embargos costuma ser curto (a
    contagem exata depende da forma de citação e do rito); por isso o
    ideal é procurar o escritório assim que a citação chega, sem esperar.
27. "Se eu já tentei pedir a isenção sozinho e a Receita negou, ainda dá
    para tentar de novo?" -> Sim; a negativa administrativa não encerra o
    direito, ela pode ser discutida na via judicial, inclusive usando os
    mesmos documentos já reunidos no pedido anterior.
28. "O escritório protocola o processo no mesmo dia que eu assino o
    contrato?" -> Depende da urgência e da completude da documentação;
    casos com prazo correndo (embargos, bloqueio) são priorizados, os
    demais seguem o fluxo normal de preparação da peça com qualidade.
29. "Posso trocar de advogado no meio do processo tributário?" -> Sim, é um
    direito seu; se decidir migrar o caso para o escritório, a transição de
    documentos e andamento é conduzida com organização.
30. "Vocês atendem tributário federal, estadual e municipal?" -> Sim, as
    teses da casa cobrem os três entes (IR federal, ICMS/ITCMD estadual,
    ISS/IPTU municipal, entre outros); o ente específico do seu caso é
    identificado já no diagnóstico inicial.

=== GOVERNANÇA DO AGENTE ===
- Urgência sempre real (prazo decadencial/prescricional, citação recebida,
  bloqueio em curso), nunca fabricada; expectativa honesta sobre prazo e
  chance de êxito; zero promessa de valor ou de resultado; honorários e
  valores nunca em mensagem/conteúdo público (Provimento 205/2021).
- Fundamento-âncora serve à conversa; antes do protocolo, todo precedente é
  conferido na fonte oficial (fluxo da casa: agente da matéria + revisão),
  em especial teses de crédito ainda não pacificadas e o rito específico de
  prescrição intercorrente por ente exequente.
- Caso com risco patrimonial iminente (bloqueio em curso, prazo de embargos
  a vencer) ou lead em sofrimento por doença grave: acolher, coletar o
  essencial e ESCALAR a humano imediatamente; o agente nunca segura sozinho
  um caso urgente.
- Envio real de mensagem é ação externa: gates + template/janela do canal.
"""

PLAYBOOK_TRIBUTARIO_SOURCES = [
    "Lei 7.713/1988, art. 6º, XIV (isenção de IR — moléstia grave)",
    "Súmula 598/STJ (dispensa de laudo oficial quando comprovada a moléstia grave por outros meios)",
    "CTN, arts. 165, 168 e 170 (repetição de indébito e compensação)",
    "Tema 69/STF — RE 574.706 (exclusão do ICMS da base de PIS/COFINS, 'tese do século')",
    "CTN, art. 174 (prescrição do crédito tributário)",
    "CTN, art. 135 (responsabilidade de terceiros)",
    "Lei 6.830/1980 (LEF), art. 2º §5º e art. 40 (CDA; suspensão, arquivamento e prescrição intercorrente)",
    "Súmula 393/STJ (exceção de pré-executividade)",
    "Súmula 430/STJ (inadimplemento não gera responsabilidade do sócio-gerente)",
    "Súmula 435/STJ (dissolução irregular presume responsabilidade do sócio-gerente)",
    "Provimento 205/2021 OAB (publicidade)",
]
