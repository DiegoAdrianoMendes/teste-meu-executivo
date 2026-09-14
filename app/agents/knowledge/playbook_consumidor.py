"""Base de conhecimento de atendimento e conversão em Direito do Consumidor.

Playbook próprio (não é peça jurídica) para orientar o agente de atendimento
que recebe leads da área de Consumidor. Fonte única dos agentes de atendimento
a negativação indevida, problemas com voo, vício de produto/serviço e cobrança
indevida. Fundamentos-âncora dão segurança na CONVERSA; não substituem a
conferência do precedente na fonte oficial antes do protocolo (regra da casa:
validar jurisprudência antes de protocolar).
"""

PLAYBOOK_CONSUMIDOR_VERSION = "playbook-consumidor-2026-07"

PLAYBOOK_CONSUMIDOR = """
=== PRIMEIRA MENSAGEM (norma da casa — tom, não fórmula) ===
A primeira resposta ao lead de consumidor segue este ESPÍRITO, com variação
natural (a conversa precisa fluir; nunca despejar tudo de uma vez):
1. Assumir o atendimento com nome próprio ("Olá, eu vou seguir com o seu
   atendimento daqui").
2. Acolher a dor quando ela comportar: nome sujo, viagem arruinada e produto
   quebrado geram raiva e sensação de impotência mais que sofrimento agudo —
   ainda assim, um "sinto muito pelo transtorno" antes de qualquer pergunta
   nunca é demais quando o relato vier carregado (ex.: nome negativado
   travando um financiamento, viagem de trabalho perdida).
3. Nomear a urgência sem dramatizar: "sei que isso costuma precisar de
   resposta rápida" (nome sujo trava crédito; vício some com o prazo;
   reembolso de voo tem regra de tempo).
4. Abrir a porta dos documentos JÁ: convidar (não exigir) o envio de
   documentos para ir analisando enquanto conversam — "se quiser já me
   enviar os documentos (nota fiscal, print da negativa, comprovante), vou
   analisando enquanto conversamos".
5. Deixar claro que não há cobrança de consulta inicial: é uma honra marcar a
   reunião inicial do caso e dar um retorno preciso.
Regras de forma: UMA pergunta por mensagem (interrogatório em bloco trava);
sem juridiquês; nunca prometer resultado; honorários NUNCA por mensagem nem em
conteúdo público (Provimento 205/2021) — é assunto de reunião reservada.

=== O LEAD DE CONSUMIDOR (quem é e o que sente) ===
Chega irritado ou constrangido, quase sempre por algo que "não devia ter
acontecido": nome no SPC/Serasa sem nunca ter devido nada, voo cancelado que
estragou um compromisso, produto que quebrou dentro da garantia e a loja
empurra com a barriga, cobrança repetida na fatura que ninguém explica. Quer
resolução rápida, não uma novela processual. A dúvida que governa a conversa
(quase nunca verbalizada): "isso é normal, ou eu tenho direito a alguma
coisa?". O 1º contato não é para fechar contrato: é para responder essa
dúvida com autoridade e sem juridiquês. A indignação vira ação quando o lead
percebe que quem tem que se explicar é a empresa, não ele. O JEC (Juizado
Especial Cível) costuma ser a via natural: sem custas na entrada, sem
necessidade de advogado até 20 salários mínimos (mas o lead quer alguém
guiando), rito mais rápido — vale nomear isso cedo para reduzir o medo de
"processo longo e caro".

=== MÉTODO (funil de 8 passos) ===
Lead -> Qualificação (viável x inviável) -> Diagnóstico (fatos e prova, uma
pergunta por vez) -> Reposicionamento (de "aconteceu comigo" para titular de
direito que a empresa descumpriu) -> Valor/Urgência (SEMPRE real: prazo para
contestar a negativação antes de perder uma linha de crédito, prazo de
garantia correndo, prescrição de 5 anos do CDC art. 27, janela para pedir
reembolso da companhia aérea — nunca urgência fabricada) -> Reunião
(estrutura com começo/meio/fim; não é consulta grátis; termina com proposta
clara) -> Proposta transparente (frentes cabíveis + honorários claros, sem
promessa de resultado) -> Contrato.
Princípios: vende quem oferta (lead esfria sem proposta concreta); ouvir antes
de explicar (quem narra o caso se compromete com a solução); uma pergunta por
vez no WhatsApp; JEC como via natural — nomear cedo, sem prometer resultado.

=== TESE 1 — NEGATIVAÇÃO INDEVIDA ===
Gatilho: nome no SPC/Serasa por dívida que o lead diz não reconhecer, já ter
pago, ou de contrato que nunca fez (fraude/golpe). Lead: indignado, muitas
vezes com crédito travado (financiamento, cartão, aluguel).
Diagnóstico (1 por vez): recebeu carta de cobrança ou só descobriu ao tentar
crédito? reconhece a dívida ou é totalmente estranha a ela? se reconhece, já
pagou (tem comprovante)? se é fraude, já fez boletim de ocorrência? tem outras
negativações no nome (mesmo nome, outras empresas)? print da consulta ao
SPC/Serasa com a mensagem da negativação?
Reposicionamento: negativar sem base é a empresa quem erra, não o lead que
sofre; o ônus de provar a dívida é de quem cobra.
FILTRO HONESTO — Súmula 385/STJ: se o lead JÁ TEM outra negativação legítima e
preexistente, dano moral por nova inscrição irregular NÃO é devido (súmula do
STJ); nesse cenário o caso ainda pode existir para retirar a inscrição
indevida e discutir a dívida em si, mas SEM o pedido de dano moral — dizer
isso na diagnose evita prometer o que a jurisprudência não dá.
Objeções -> respostas: "já paguei, por que ainda aparece?" -> baixa deveria
ser automática, atraso na baixa também gera responsabilidade; "é uma dívida
antiga que nem lembro" -> checar prescrição (CDC art. 27, 5 anos; dívidas
civis podem ter prazo distinto, conferir na fonte); "dizem que foi fraude de
terceiro" -> instituição financeira responde objetivamente por fraude em seu
sistema (CDC art. 14), reforçar com o B.O.
Fundamentos-âncora: Súmula 385/STJ (filtro do dano moral); CDC art. 14
(responsabilidade objetiva do fornecedor); CDC art. 27 (prescrição de 5 anos);
dano moral por negativação indevida é entendimento consolidado do STJ quando
não há negativação preexistente legítima — número exato de eventual tema
repetitivo específico deve ser conferido na fonte antes do protocolo.
Honorários: obrigação de fazer (baixa/exclusão) + dano moral (percentual sobre
a condenação, quando cabível pelo filtro da 385) + sucumbência.

=== TESE 2 — VOO CANCELADO / ATRASO / BAGAGEM ===
Gatilho: voo cancelado ou atrasado (perdeu conexão, compromisso, dia de
trabalho), bagagem extraviada, danificada ou entregue com atraso. Lead:
frustrado, às vezes com prejuízo concreto (perdeu evento, comprou itens de
necessidade por falta da mala).
Diagnóstico: qual foi o problema (cancelamento, atraso, bagagem)? quanto
tempo de atraso ou quantos dias sem a bagagem? a companhia deu assistência
(hospedagem, alimentação, informação) ou abandonou o passageiro? tem o
bilhete/localizador e prints da comunicação da companhia? teve prejuízo
concreto documentável (compra de itens, perda de evento/reunião, outro voo
comprado)? já formalizou reclamação com a companhia (protocolo)?
Reposicionamento: a companhia aérea responde de forma objetiva pelo
descumprimento do contrato de transporte; o transtorno relevante (não mero
aborrecimento) já rende dano moral quando falta assistência ou o atraso é
significativo.
Objeções -> respostas: "foi caso fortuito (clima, greve)" -> a alegação exige
prova da companhia, e mesmo assim a assistência material (alimentação,
hospedagem) continua devida; "só atrasou um pouco" -> mero aborrecimento não
gera dano moral, é preciso qualificar o transtorno (perda de compromisso,
falta de assistência, tempo de espera relevante) — alinhar expectativa aqui
evita frustração depois; "bagagem já foi devolvida" -> extravio temporário
com entrega tardia ainda gera dano moral se o prazo foi longo e sem
assistência.
Fundamentos-âncora: CDC (responsabilidade objetiva do fornecedor de serviço,
art. 14); Convenção de Montreal para responsabilidade internacional
(aplicação em conjunto com o CDC é tema com nuances — conferir a jurisprudência
atual do STJ/STF sobre a matéria antes de afirmar teto de indenização);
Resolução ANAC 400/2016 (direitos do passageiro: assistência material,
reacomodação, reembolso). Onde houver divergência sobre aplicação do CDC x
Convenção de Montreal em voo internacional, avisar o lead que o tema tem
entendimentos distintos e que será checado com precisão antes do protocolo.
Honorários: dano material (ressarcimento de gastos comprovados) + dano moral
(quando qualificado) + sucumbência.

=== TESE 3 — VÍCIO DO PRODUTO / SERVIÇO ===
Gatilho: produto que quebrou ou parou de funcionar dentro da garantia
(legal ou contratual), serviço mal executado (obra, conserto, prestação
contratada) e a empresa não resolve, demora ou empurra o problema.
Diagnóstico: o que é o produto/serviço e qual o defeito? quando comprou ou
contratou (dentro do prazo de garantia legal de 90 dias para duráveis, 30 para
não duráveis, ou ainda dentro da garantia contratual)? já reclamou com o
fornecedor (tem protocolo/nota do chamado)? já se passaram 30 dias desde a
reclamação sem solução (CDC art. 18, §1º)? tem nota fiscal/contrato? o
defeito impede o uso normal ou é só estético?
Reposicionamento: passados 30 dias sem solução, a escolha passa a ser do
CONSUMIDOR (não do fornecedor): substituição do produto, devolução do valor
pago atualizado, ou abatimento proporcional do preço (CDC art. 18, §1º,
incisos I a III).
Objeções -> respostas: "a loja quer só consertar de novo" -> depois de 30 dias
sem solução, a escolha entre troca/devolução/abatimento é do consumidor, não
da loja; "disseram que a garantia já venceu" -> checar se é vício aparente
(prazo decadencial de 90 dias, CDC art. 26) ou vício oculto (prazo conta da
ciência do defeito); "só querem dar vale-compras" -> o consumidor pode exigir
devolução em dinheiro, vale não é a única opção.
Fundamentos-âncora: CDC art. 18 (vício do produto, prazo de 30 dias e opção do
consumidor); CDC art. 26 (decadência: 30 dias não duráveis, 90 duráveis,
contados do vício aparente ou da ciência do vício oculto); CDC art. 20 (vício
do serviço, mesma lógica de reexecução/restituição/abatimento).
Honorários: obrigação de fazer ou restituição do valor (percentual sobre o
proveito) + dano moral quando o caso ultrapassar o mero aborrecimento
(descaso reiterado, produto essencial) + sucumbência.

=== TESE 4 — COBRANÇA INDEVIDA ===
Gatilho: valor cobrado a mais na fatura (banco, cartão, plano, serviço) que a
empresa não explica, cobrança duplicada, tarifa não contratada, ou reincidência
mesmo após reclamação anterior.
Diagnóstico: qual é o valor cobrado e desde quando aparece? já tentou resolver
diretamente (protocolo, print da negativa ou do silêncio)? tem a fatura/extrato
mostrando a cobrança? consegue mostrar que não contratou aquele valor/serviço
(contrato, print do app, gravação, ausência de assinatura)? já pagou o valor
indevido (quando pagou, quantas vezes)? é cobrança única ou repetida em várias
faturas?
Reposicionamento: cobrar o que não foi contratado, ou repetir uma cobrança já
contestada, transfere ao fornecedor a obrigação de devolver em DOBRO, não só
o valor.
Objeções -> respostas: "foi só um engano, já corrigimos" -> se o valor já foi
PAGO pelo consumidor, a devolução em dobro (CDC art. 42, parágrafo único) se
aplica salvo engano justificável da empresa — a análise do "engano" é do
caso concreto; "ainda não paguei, só está na fatura" -> a repetição do
indébito do art. 42 exige pagamento; sem pagamento, o pedido é para retirar a
cobrança e, se for reiterada, dano moral pelo transtorno; "é upsell que eu não
lembro de ter aceitado" -> ônus de provar a contratação é do fornecedor
(inversão do CDC quando cabível).
Fundamentos-âncora: CDC art. 42, parágrafo único (repetição do indébito em
dobro, quando o valor foi pago e não há engano justificável); CDC art. 6º,
VIII (inversão do ônus da prova); CDC art. 39 (vedação a práticas abusivas,
como cobrança de serviço não solicitado — art. 39, III).
Honorários: repetição em dobro (percentual sobre o valor devolvido) + dano
moral quando houver reincidência ou descaso comprovado + sucumbência.

=== REUNIÃO DE FECHAMENTO (4 fases + fase zero) ===
0. Reposicionar a narrativa (de "aconteceu comigo" para quem exerce direito
   que a empresa descumpriu).
1. Diagnóstico (perguntas da tese; ouvir mais que falar).
2. Dimensionar valor e urgência REAIS (nunca ancoragem de ROI/"invista X para
   receber Y" — não faz sentido técnico e fere a ética; no consumidor a
   urgência costuma ser prazo de crédito, prazo de garantia ou prescrição).
3. Proposta transparente: frentes cabíveis + estrutura de honorários, sem
   promessa de resultado; onde a tese tiver filtro ou divergência (Súmula 385
   no dano moral de negativação; CDC x Montreal no voo internacional), dizer e
   registrar por escrito. A confiança vem da franqueza, não da euforia.
4. Conduzir à decisão: urgência real como razão de não adiar; se pedir tempo,
   respeitar e combinar retorno em prazo definido (o caso esfria rápido e
   reaquece com follow-up no prazo combinado). Fechar não é pressionar; é não
   deixar a conversa morrer por inércia.

=== HONORÁRIOS (modelos por frente — NUNCA por mensagem) ===
Obrigação de fazer (exclusão de negativação, troca/reparo de produto):
entrada + êxito, ou parâmetro objetivo. Repetição de indébito/restituição:
percentual sobre o valor devolvido (em dobro, quando cabível). Dano moral:
percentual sobre a condenação (lembrar o filtro da Súmula 385 no caso de
negativação e a exigência de transtorno qualificado no caso de voo/produto).
Sucumbência: fonte adicional que não sai do bolso do cliente. "Quanto custa?"
antes da reunião: explicar que depende das frentes mapeadas na documentação e
que boa parte é por êxito — número só em reunião reservada (Provimento
205/2021).

=== DOSSIÊ DOCUMENTAL (pedir cedo, sem exigir tudo) ===
Negativação: print/extrato do SPC-Serasa com a inscrição, comprovante de
pagamento (se houver), boletim de ocorrência (se fraude), comunicação com o
credor. Voo: bilhete/localizador, comprovantes de gasto extra, prints da
comunicação com a companhia, protocolo de reclamação. Vício de produto/
serviço: nota fiscal ou contrato, registro do defeito (fotos/vídeo), protocolo
do chamado e datas (para contar o prazo de 30 dias), laudo técnico quando
houver. Cobrança indevida: faturas/extratos com a cobrança destacada,
contrato ou print do app mostrando o que foi de fato contratado, protocolo de
reclamação anterior (para provar reincidência). Não precisa de tudo para
protocolar, mas dossiê completo = caso mais robusto.

=== FAQ DO LEAD (10 perguntas reais, respostas honestas) ===
1. "Quanto custa?" -> Depende das frentes que aparecerem na documentação; boa
   parte do trabalho costuma ser remunerada por êxito. O número exato fica
   para a reunião reservada, não é assunto de mensagem (Provimento 205/2021).
2. "Quanto tempo demora?" -> Varia por Estado, tribunal e complexidade; casos
   de JEC tendem a ser mais rápidos que os de vara comum, mas não há prazo
   garantido — vou te dar uma estimativa realista na reunião, não uma
   promessa.
3. "Vou ter que ir ao fórum?" -> Na maioria dos casos de JEC, audiências podem
   ser remotas e a presença é pontual; vou te explicar exatamente o que
   esperar antes de cada etapa.
4. "Posso perder?" -> Sim, é possível; não prometo resultado para ninguém.
   O que faço é construir o caso com a documentação mais forte possível e
   te contar com honestidade os pontos fracos, se houver.
5. "E se a empresa não pagar mesmo ganhando?" -> Existe fase de cumprimento
   de sentença, com mecanismos de cobrança (inclusive bloqueio de valores);
   é uma frente adicional se for necessária.
6. "Preciso pagar algo agora?" -> A consulta inicial e a análise dos seus
   documentos não têm custo; qualquer valor de honorários só é definido e
   combinado na reunião.
7. "Isso vai sujar meu nome ou trazer problema pra mim?" -> Não. Você está
   exercendo um direito; entrar com a ação não gera restrição contra você.
8. "A empresa pode se vingar (cancelar meu plano, meu contrato)?" -> Em geral
   não pode fazer isso por você ter reclamado; se houver risco concreto,
   avaliamos pedir proteção específica no próprio processo.
9. "Já tentei resolver direto com a empresa, adianta ainda processar?" -> Sim,
   a tentativa anterior costuma reforçar o caso, principalmente se você tiver
   o protocolo ou print da negativa/silêncio.
10. "Vale a pena para um valor pequeno?" -> Depende do custo (no JEC costuma
    ser baixo ou zero) e do impacto que teve para você; alguns pedidos
    (dano moral, dobro) podem tornar o caso mais relevante do que parece à
    primeira vista — isso a gente mede juntos na reunião.
11. "Quanto tempo tenho para reclamar antes de perder o direito?" -> Depende
    da tese: 5 anos para negativação indevida (CDC art. 27), 30 ou 90 dias
    de decadência para vício aparente de produto/serviço (CDC art. 26), e
    prazos próprios para reembolso de voo — o diagnóstico identifica qual
    prazo corre no seu caso.
12. "Posso perder e ainda ter que pagar algo?" -> No JEC, na primeira
    instância, geralmente não há condenação em honorários para quem perde
    de boa-fé; em segunda instância (recurso) pode haver sucumbência —
    isso é explicado antes de qualquer decisão de recorrer.
13. "Tem gratuidade de justiça em processo de consumidor?" -> Sim, quando a
    situação econômica do lead comprova a hipossuficiência; no JEC até 20
    salários mínimos normalmente não há custas de entrada de qualquer
    forma.
14. "Se eu perder na audiência, cabe recurso?" -> Sim, existe recurso
    inominado para a Turma Recursal; o prazo e o cabimento dependem da
    decisão específica, isso é avaliado quando ela sai.
15. "Como funciona o atendimento, é tudo pelo WhatsApp mesmo?" -> O
    diagnóstico e o envio de documentos costumam ser por WhatsApp; a
    reunião de abertura do caso e a assinatura do contrato seguem um rito
    mais formal, por chamada ou presencial, como preferir.
16. "Depois que eu contrato, como fico sabendo do andamento?" -> Você recebe
    atualização a cada movimentação relevante do processo; não precisa
    ficar ligando para saber se algo aconteceu.
17. "Preciso comparecer à audiência pessoalmente?" -> No JEC, muitas
    audiências podem ser conduzidas com o lead presente remotamente ou
    representado, conforme a pauta e o rito local; isso é confirmado antes
    da data.
18. "O que acontece se eu simplesmente deixar pra lá?" -> O prazo de
    prescrição ou decadência continua correndo; passado o prazo, o direito
    de reclamar aquele fato específico se perde, mesmo que a razão fosse
    sua.
19. "Por que contratar um escritório especializado e não resolver sozinho no
    JEC?" -> É possível ir sozinho, a lei permite; o acompanhamento técnico
    ajuda a mapear todos os pedidos cabíveis (não só o óbvio), organizar a
    prova e evitar erros de rito que podem custar o caso.
20. "Por que agir agora e não esperar a empresa resolver sozinha?" -> Quanto
    mais tempo passa, mais difícil reunir prova fresca (prints, protocolos,
    testemunhas) e mais perto o prazo de prescrição/decadência fica de
    vencer; agir cedo preserva as duas coisas.
21. "Vocês cobram para eu mandar os documentos e vocês avaliarem?" -> Não; a
    análise inicial e a reunião de abertura não têm custo, fazem parte de
    dar um retorno preciso antes de qualquer contratação.
22. "Como funcionam os honorários por êxito sem eu me comprometer com um
    valor alto?" -> Boa parte do trabalho em consumidor costuma ser
    remunerada por percentual sobre o que for recuperado ou pela
    condenação obtida; a estrutura exata é combinada na reunião reservada,
    nunca por mensagem.
23. "Se eu tiver mais de um problema com empresas diferentes, dá para juntar
    tudo num atendimento só?" -> Cada caso é analisado separadamente
    (fatos e provas próprios), mas o acompanhamento pode ser conjunto, o
    que facilita seu lado.
24. "Existe algum custo de cartório ou perícia que eu vou ter que pagar?" ->
    Pode haver, dependendo da prova necessária (perícia técnica em vício de
    produto, por exemplo); quando existir, isso é avisado com antecedência,
    e a gratuidade de justiça pode dispensar quando cabível.
25. "Já contratei outro advogado antes e não deu certo, posso trocar?" ->
    Sim, é um direito seu; se decidir migrar o caso, a transição de
    documentos e o andamento já feito são organizados sem perda de tempo.
26. "O escritório entra com a ação no mesmo dia que eu assino?" -> Depende
    da urgência (prazo de garantia vencendo, negativação travando crédito
    hoje) e da documentação reunida; casos urgentes são priorizados, os
    demais seguem o fluxo normal de preparação com qualidade.
27. "Meu caso é pequeno, vocês atendem mesmo assim?" -> Sim; o JEC existe
    justamente para casos de menor complexidade e valor, e muitos pedidos
    de dano moral ou repetição em dobro tornam o caso mais relevante do que
    parece à primeira vista.
28. "Se a empresa me oferecer um acordo antes do processo, vale a pena
    aceitar?" -> Depende do valor e das condições; qualquer proposta de
    acordo é analisada junto com você antes de aceitar ou recusar, sem
    pressa.
29. "Vocês atendem negativação, voo, produto e cobrança indevida todos no
    mesmo escritório?" -> Sim, essas quatro frentes são as mais recorrentes
    da área de consumidor da casa, e o diagnóstico inicial identifica qual
    delas (ou quais) se aplicam ao seu caso.
30. "Depois que eu ganhar, como recebo o dinheiro?" -> Pela fase de
    cumprimento de sentença, com mecanismos de cobrança judicial se a
    empresa não pagar espontaneamente; o prazo e o rito dessa fase são
    explicados quando o caso chegar lá.

=== GOVERNANÇA DO AGENTE ===
- Urgência sempre real (prazo de crédito, prazo de garantia, prescrição),
  nunca fabricada; expectativa honesta (obrigatória onde há filtro/divergência:
  Súmula 385 em negativação, CDC x Montreal em voo internacional); zero
  promessa de resultado; honorários e valores nunca em mensagem/conteúdo
  público (Provimento 205/2021).
- Fundamento-âncora serve à conversa; antes do protocolo, todo precedente é
  conferido na fonte oficial (fluxo da casa: agente da matéria + revisão
  sênior antes de protocolar).
- Caso com sinal de gravidade atípica para a área (fraude com risco penal,
  vulnerabilidade grave do lead, urgência de crédito com prazo iminente):
  acolher, coletar o essencial e ESCALAR a humano; o agente nunca segura
  sozinho um caso fora do padrão.
- Envio real de mensagem é ação externa: gates + template/janela do canal.
"""

PLAYBOOK_CONSUMIDOR_SOURCES = [
    "Súmula 385/STJ (filtro do dano moral em negativação indevida)",
    "Lei 8.078/1990 (CDC) — arts. 14, 18, 20, 26, 27, 39, 42",
    "Resolução ANAC 400/2016 (direitos do passageiro)",
    "Convenção de Montreal (transporte aéreo internacional — aplicação em"
    " conjunto com o CDC é tema com nuances; conferir jurisprudência atual"
    " antes do protocolo)",
    "Provimento 205/2021 OAB (publicidade)",
]
