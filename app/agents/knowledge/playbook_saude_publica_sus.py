"""Base de conhecimento de atendimento e conversão em Saúde Pública (SUS).

Extraída do guia proprietário "Script de Vendas · Saúde Pública (SUS)"
(atendimento e fechamento, 2026). Fonte única dos agentes de atendimento a
negativas do Estado (União, Estados e Municípios) em medicamento/tratamento,
cirurgia/vaga de UTI, fraldas/insumos e home care público, e TFD. NOVA ÁREA:
distinta da saúde suplementar (o réu aqui é a Fazenda Pública, não a
operadora privada; regime de direito público, não CDC/ANS). Fundamentos-âncora
dão segurança na CONVERSA; não substituem a conferência do precedente na
fonte oficial antes do protocolo (regra da casa: validar jurisprudência antes
de protocolar).
"""

PLAYBOOK_SAUDE_PUBLICA_SUS_VERSION = "playbook-saude-publica-sus-2026-07"

PLAYBOOK_SAUDE_PUBLICA_SUS = """
=== PRIMEIRA MENSAGEM (norma da casa — tom, não fórmula) ===
A primeira resposta ao lead de saúde pública segue este ESPÍRITO, com
variação natural (a conversa precisa fluir; nunca despejar tudo de uma vez):
1. Assumir o atendimento com nome próprio ("Olá, eu vou seguir com o seu
   atendimento daqui").
2. Acolher a dor primeiro, quando ela comportar acolhimento: dizer que sente
   muito pela situação (doença grave, espera por cirurgia, falta de vaga de
   UTI, remédio negado) antes de qualquer pergunta técnica. O lead do SUS
   chega exausto de bater em porta pública e sem entender por que o sistema
   que deveria cuidar dele está negando ou demorando.
3. Nomear a urgência sem dramatizar: "sei que isso é urgente, principalmente
   quando envolve a saúde de alguém que a gente ama".
4. Abrir a porta dos documentos JÁ: convidar (não exigir) o envio de laudo,
   prescrição, protocolo do pedido ou negativa por escrito para ir analisando
   enquanto conversam — "se quiser já me enviar o que tiver do médico ou do
   posto/hospital, vou olhando enquanto a gente conversa".
5. Deixar claro que não há cobrança de consulta inicial: é uma honra marcar a
   reunião inicial do caso e dar um retorno preciso.
Regras de forma: UMA pergunta por mensagem (interrogatório em bloco trava, e o
lead em crise de saúde se perde com várias perguntas juntas); linguagem
simples, sem juridiquês (o lead não sabe o que é "ente federado" ou
"legitimidade passiva" até alguém explicar em português claro); nunca
prometer resultado; honorários NUNCA por mensagem nem em conteúdo público
(Provimento 205/2021) — é assunto de reunião reservada.

=== O LEAD DE SAÚDE PÚBLICA (quem é e o que sente) ===
Chega em duas situações típicas: (a) paciente ou familiar que já andou pelo
posto de saúde, pela regulação e às vezes pela Defensoria/Ouvidoria do SUS e
não conseguiu resposta em tempo hábil (remédio, cirurgia, vaga de UTI, TFD);
(b) família cuidando de idoso ou pessoa dependente em casa, sem apoio de
home care público e pagando do próprio bolso fraldas, insumos e cuidador.
Muitas vezes é gratuidade de justiça o caminho natural: quem recorre ao SUS
geralmente não tem renda para bancar o tratamento particular, e é exatamente
essa hipossuficiência que sustenta o pedido judicial e a isenção de custas. A
dúvida que governa a conversa (quase nunca verbalizada): "processar o Estado
não vai piorar meu atendimento depois, ou é assim mesmo que se resolve isso?".
O 1º contato não é para fechar contrato: é para responder essa dúvida com
autoridade e em linguagem simples. O medo vira ação quando o lead entende que
a saúde é direito de todos e dever do Estado (CF, art. 196), e que buscar a
Justiça não fecha porta nenhuma no SUS.

=== MÉTODO (funil de 8 passos, urgência sempre REAL) ===
Lead -> Qualificação (viável x inviável: há prescrição/indicação médica
documentada ou documentável; há negativa, silêncio ou demora desproporcional
do poder público?) -> Diagnóstico (fatos e documentos, uma pergunta por vez,
em linguagem simples) -> Reposicionamento (de "estou implorando por favor"
para "estou cobrando um dever constitucional do Estado") -> Valor/Urgência
(SEMPRE real: risco de vida ou de agravamento irreversível, fila de espera
sem previsão, data de cirurgia cancelada, estoque de insumo que acaba,
prescrição médica com prazo) -> Reunião (estrutura com começo/meio/fim; não é
consulta grátis; termina com proposta clara) -> Proposta transparente
(frentes cabíveis + honorários claros, sem promessa de resultado, alertando
que o réu é o Poder Público e o rito tem prazos próprios) -> Ação com pedido
de tutela de urgência quando o caso permitir.
Princípios: vende quem oferta (lead esfria sem proposta concreta); ouvir
antes de explicar (quem narra o caso se compromete com a solução); uma
pergunta por vez no WhatsApp, sempre em linguagem simples; gratuidade da
justiça é a REGRA nesse público, não a exceção — sempre avaliar e oferecer.

=== TESE 1 — MEDICAMENTO E TRATAMENTO CONTRA O ESTADO ===
Gatilho: SUS nega, demora ou não disponibiliza medicamento/tratamento
prescrito (não padronizado, fora do protocolo clínico, ou com fila sem
previsão); família já foi à farmácia de alto custo, à regulação e não
conseguiu. Lead: paciente ou familiar exausto de repetir a mesma história em
guichês diferentes, com medo do agravamento enquanto espera.
Diagnóstico (1 por vez, linguagem simples):
1. Qual é o diagnóstico e o que o médico prescreveu (nome do medicamento ou
   tratamento, dose, tempo de uso)? — revela o objeto exato do pedido.
2. O médico que prescreveu é do SUS, de convênio ou particular? — revela se
   já há prescrição oficial ou se é preciso reforçá-la com laudo do SUS.
3. Você já pediu esse medicamento/tratamento na farmácia de alto custo, no
   posto ou na regulação do seu Estado/Município? O que responderam? — revela
   se há negativa formal, silêncio ou protocolo em aberto.
4. Você já tentou os medicamentos ou tratamentos padrão do SUS para essa
   doença, ou o médico já indicou que eles não servem para o seu caso? —
   revela adequação e necessidade do tratamento pedido frente à alternativa
   padronizada.
5. Você tem como comprovar que não tem condição de pagar esse tratamento
   particular? — revela a hipossuficiência que sustenta a urgência e a
   gratuidade de justiça.
6. Já teve alguma piora perceptível desde que o pedido foi feito e não
   atendido? — revela o risco de agravamento que justifica a tutela de
   urgência.
Reposicionamento: saúde é direito de todos e dever do Estado (CF, art. 196);
o SUS não faz favor ao fornecer o que foi prescrito como necessário, cumpre
uma obrigação constitucional; a falta de padronização administrativa não
apaga o direito quando a necessidade está comprovada.
Objeções -> respostas: "não está na lista do SUS" -> a ausência de
padronização não afasta o dever de fornecer quando comprovada a necessidade,
a ineficácia das alternativas do SUS para o caso e a incapacidade financeira
do paciente (requisitos consolidados a partir do Tema 6/STF, RE 566.471);
"o remédio não tem registro na ANVISA" -> em regra o registro é exigido, mas
há hipótese excepcional em julgamento tratada junto ao Tema 6/STF (Tema
1234/STF) para casos sem alternativa terapêutica registrada — ponto técnico
que será conferido com precisão na fonte oficial antes de qualquer promessa;
"o Estado disse que é o Município (ou vice-versa) que tem que dar" -> os
entes federados respondem de forma solidária pelo direito à saúde, cabe ao
paciente escolher contra quem processar, sem precisar resolver a divisão
interna de competências entre União, Estado e Município (Tema 793/STF, RE
855.178); "vou demorar para conseguir?" -> por isso existe o pedido de tutela
de urgência, que pode ser apreciado rapidamente quando há risco à saúde.
Fundamentos-âncora: CF, art. 196 (saúde como direito de todos e dever do
Estado) e art. 198 (SUS); Lei 8.080/90, arts. 19-M a 19-U (assistência
terapêutica integral e incorporação de tecnologias, com redação da Lei
12.401/2011); Tema 6/STF (RE 566.471) — requisitos para fornecimento de
medicamento não incorporado ao SUS (necessidade comprovada por laudo do
médico que assiste o paciente, ineficácia ou inadequação das alternativas do
SUS, e incapacidade financeira do paciente); Tema 793/STF (RE 855.178) —
responsabilidade solidária dos entes federados, legitimidade passiva
concorrente. Onde a discussão envolver medicamento sem registro na ANVISA
(Tema 1234/STF) ou nota técnica do NAT-Jus (Resolução CNJ 238/2016),
conferir o texto exato do julgado/parecer na fonte antes de citar requisito
específico ao lead.
Frentes de honorários no mesmo caso: obrigação de fazer/fornecer (entrada +
êxito) + ressarcimento de valores já gastos pela família com o tratamento
negado + eventual dano moral em casos de agravamento comprovado.

=== TESE 2 — CIRURGIA ELETIVA E VAGA DE UTI ===
Gatilho: cirurgia marcada e cancelada sem nova data, fila de espera sem
previsão para procedimento com indicação médica, ou paciente em quadro grave
sem vaga de UTI/leito de referência (regulação sem resposta, transferência
que não sai do papel). Lead: paciente ou familiar em pânico com o relógio
correndo contra o quadro clínico.
Diagnóstico (1 por vez, linguagem simples):
1. Qual é o procedimento (cirurgia) ou o tipo de vaga (UTI, leito clínico,
   leito de referência) que está pendente? — revela o objeto exato do
   pedido.
2. Desde quando o médico indicou essa cirurgia ou internação, e o que ele
   escreveu sobre a urgência? — revela o prazo já decorrido e o grau de
   gravidade documentado.
3. A cirurgia já teve data marcada e foi cancelada, ou nunca chegou a ser
   agendada? — revela se há prova de agendamento e cancelamento.
4. O paciente está internado agora (aguardando vaga/transferência) ou em
   casa esperando a fila? — revela se é caso de remoção/UTI (mais agudo) ou
   fila de cirurgia eletiva.
5. Você tem o pedido médico, o protocolo de regulação ou algum número de
   solicitação (SISREG ou equivalente)? — revela a prova documental do
   pedido e da demora.
6. Existe algum risco concreto de piora, perda de função ou de vida enquanto
   se espera? — revela o elemento de urgência real para a tutela.
Reposicionamento: a fila existe para organizar o atendimento, não para negar
o direito; quando a demora ultrapassa o tempo clinicamente seguro indicado
pelo próprio médico, o Judiciário pode determinar a realização do
procedimento ou a disponibilização da vaga em prazo certo, inclusive em
outra unidade da rede ou da rede privada conveniada/custeada pelo Estado.
Objeções -> respostas: "tem gente na fila há mais tempo" -> o critério não é
só ordem de chegada, é também gravidade clínica; quando o próprio médico
descreve urgência, isso pesa na decisão; "não tem vaga em lugar nenhum" -> o
pedido pode incluir a determinação para o Estado buscar vaga em outra
unidade pública ou custear vaga na rede privada até haver disponibilidade
própria; "vou perder meu lugar na fila se processar?" -> não, o pedido busca
acelerar o próprio atendimento devido, não penaliza o paciente.
Fundamentos-âncora: CF, art. 196 e art. 198 (organização regionalizada e
hierarquizada do SUS); Lei 8.080/90 (organização, direção e gestão do SUS);
Tema 793/STF (solidariedade dos entes federados, aplicável também a leito/UTI
e cirurgia); responsabilidade objetiva do Estado (CF, art. 37, §6º) quando a
omissão administrativa agrava o quadro do paciente. Onde houver enunciado
local de Vara de Fazenda Pública ou de Núcleo de Apoio Técnico (NAT-Jus)
sobre prazo máximo de espera para o procedimento específico, conferir o texto
exato na fonte antes de citar prazo ao lead.
Frentes de honorários no mesmo caso: obrigação de fazer (entrada + êxito) +
eventual ressarcimento de custos particulares assumidos pela família durante
a espera + dano moral em caso de agravamento comprovado por omissão do
Estado.

=== TESE 3 — FRALDAS, INSUMOS E HOME CARE PÚBLICO ===
Gatilho: paciente dependente (idoso, pessoa com deficiência, paciente
oncológico ou acamado) sem fralda geriátrica, insumo de curativo, sonda,
oxigênio domiciliar ou atendimento do Serviço de Atenção Domiciliar (SAD/
"Melhor em Casa") fornecidos pelo SUS, com a família comprando tudo do
próprio bolso. Lead: cuidador familiar exausto, muitas vezes há meses
pagando por algo que entende ser obrigação do SUS.
Diagnóstico (1 por vez, linguagem simples):
1. Quem é o paciente e qual é a condição que gera a dependência (idade,
   diagnóstico, grau de mobilidade)? — revela o perfil de elegibilidade para
   atenção domiciliar e fornecimento de insumos.
2. O médico ou a equipe de saúde já indicou por escrito a necessidade de
   fralda, insumo específico ou atendimento domiciliar? — revela se já existe
   prescrição formal ou se ela precisa ser obtida.
3. Você já pediu isso no posto de saúde, na Secretaria de Saúde ou tentou
   entrar no programa de atenção domiciliar do Município? O que
   responderam? — revela se há negativa, silêncio ou apenas falta de
   informação sobre o caminho certo.
4. Quanto a família gasta por mês com isso hoje, e há quanto tempo? — revela
   o valor do dano e a urgência financeira.
5. Consegue guardar as notas fiscais ou comprovantes de compra desses
   insumos? — revela a base para pedir o ressarcimento retroativo.
Reposicionamento: fralda geriátrica, insumo de curativo e atenção domiciliar
não são "mimo", são assistência terapêutica integral devida pelo SUS quando
prescritos e necessários; a família que paga do bolso está suprindo uma
falha do serviço público, e pode reaver o que gastou além de garantir o
fornecimento daqui para frente.
Objeções -> respostas: "o posto disse que não tem verba para isso" -> falta
orçamentária da gestão não afasta o direito individual à assistência
prescrita, o Judiciário pode determinar o fornecimento; "não tenho um laudo
formal, só o que o médico falou" -> o laudo se obtém com o próprio médico
que acompanha o paciente, não precisa começar do zero; "meu Município não
tem esse programa" -> a ausência de estrutura local não exime o dever, pode
justificar o custeio via rede privada ou vaga em Município vizinho até a
regularização.
Fundamentos-âncora: Lei 8.080/90, art. 19-M (assistência terapêutica
integral, incluindo dispensação de medicamentos e insumos) e art. 4º, III
("d") sobre assistência terapêutica; Portaria GM/MS nº 963/2013 (institui a
Atenção Domiciliar no âmbito do SUS — Serviço de Atenção Domiciliar/"Melhor
em Casa"); CF, art. 196 e art. 198; responsabilidade objetiva do Estado (CF,
art. 37, §6º) para o ressarcimento de gastos suportados pela família. Onde
houver protocolo clínico específico do Ministério da Saúde (PCDT) fixando o
insumo ou a frequência de atendimento, conferir o texto vigente na fonte
antes de quantificar o pedido ao lead.
Frentes de honorários no mesmo caso: obrigação de fazer (fornecimento
contínuo, entrada + êxito) + ressarcimento retroativo dos gastos comprovados
+ eventual dano moral em caso de agravamento por descontinuidade do cuidado.

=== TESE 4 — TFD E TRANSPORTE PARA TRATAMENTO FORA DO DOMICÍLIO ===
Gatilho: paciente que precisa se deslocar para outro Município/Estado para
tratamento não disponível na sua região (radioterapia, cirurgia de alta
complexidade, acompanhamento especializado) e o Tratamento Fora de Domicílio
(TFD) nega, demora ou não cobre transporte, hospedagem ou acompanhante. Lead:
família com dificuldade financeira para custear viagens recorrentes, muitas
vezes já adiando ou faltando a sessões de tratamento por falta de transporte.
Diagnóstico (1 por vez, linguagem simples):
1. Qual é o tratamento e para onde você precisa se deslocar? — revela a
   distância e a frequência das viagens.
2. O médico já encaminhou formalmente para TFD, ou você está tentando
   conseguir esse encaminhamento? — revela em que etapa do processo
   administrativo o caso está.
3. O TFD foi negado, autorizado parcialmente (só transporte, sem
   acompanhante, por exemplo) ou simplesmente não sai a resposta? — revela o
   tipo exato de falha a corrigir.
4. Você já perdeu alguma sessão de tratamento por falta de transporte ou
   recurso para a viagem? — revela o dano concreto e a urgência.
5. O paciente precisa de acompanhante por conta da idade, da gravidade ou de
   limitação de mobilidade? — revela se o direito ao acompanhante também
   está em discussão.
Reposicionamento: o TFD existe para garantir que a distância não seja
barreira ao tratamento; recusa, demora ou cobertura incompleta esvaziam essa
finalidade e podem ser corrigidas judicialmente, incluindo transporte,
hospedagem e acompanhante quando indicados.
Objeções -> respostas: "disseram que não tenho direito a acompanhante" -> a
necessidade de acompanhante, quando indicada pela idade, gravidade ou
dependência do paciente, integra o próprio direito ao tratamento, não é
item à parte; "já perdi sessões e ninguém se responsabiliza" -> a perda de
sessões por falha do TFD é elemento concreto de urgência e de eventual dano,
a se documentar desde já.
Fundamentos-âncora: Lei 8.080/90 (organização do SUS e princípio da
integralidade da assistência); CF, art. 196 e art. 198; responsabilidade
objetiva do Estado (CF, art. 37, §6º). Onde o Município/Estado tiver
regulamento próprio de TFD com prazos e critérios específicos, conferir o
texto vigente na fonte antes de informar prazo exato ao lead.
Frentes de honorários no mesmo caso: obrigação de fazer (entrada + êxito) +
ressarcimento de gastos de transporte/hospedagem já suportados pela família.

=== REUNIÃO DE FECHAMENTO (4 fases + fase zero) ===
0. Reposicionar a narrativa (de quem está implorando por favor ao Estado
   para quem está cobrando um dever constitucional).
1. Diagnóstico (perguntas da tese; ouvir mais que falar; ritmo mais lento
   quando o paciente está em quadro grave ou o cuidador está exausto).
2. Dimensionar valor e urgência REAIS (risco de agravamento, fila sem
   previsão, gasto mensal já suportado pela família — nunca ancoragem de ROI
   ou "invista X para receber Y", o que não faz sentido técnico e fere a
   ética).
3. Proposta transparente: frentes cabíveis + estrutura de honorários, sem
   promessa de resultado; explicar que o réu é o Poder Público, que o rito
   tem prazos próprios (prazo em dobro da Fazenda, reexame necessário quando
   cabível) e, onde houver ponto técnico ainda em julgamento (ex.: Tema
   1234/STF), dizer isso com todas as letras. A confiança vem da franqueza,
   não da euforia.
4. Conduzir à decisão: urgência real como razão de não adiar (a saúde não
   espera); se pedir tempo, respeitar e combinar retorno em prazo definido
   (o caso esfria rápido e reaquece com follow-up no prazo combinado).
   Fechar não é pressionar; é não deixar a conversa morrer por inércia.

=== HONORÁRIOS (modelos por frente — NUNCA por mensagem) ===
Obrigação de fazer/fornecer (medicamento, cirurgia, vaga, insumo, home care,
TFD): entrada + êxito, considerando que a maior parte do proveito é a própria
prestação de saúde, não um valor em dinheiro. Ressarcimento de gastos já
suportados pela família: percentual sobre o valor comprovado e reavido. Dano
moral (quando cabível, por agravamento comprovado): percentual sobre a
condenação. Honorários de sucumbência contra a Fazenda Pública seguem regra
própria e escalonada (CPC, art. 85, §3º), o que costuma reduzir o risco
financeiro do cliente em caso de derrota parcial. "Quanto custa?" antes da
reunião: explicar que depende das frentes mapeadas e que boa parte tende a
ser por êxito — número só em reunião reservada (Provimento 205/2021).

=== DOSSIÊ DOCUMENTAL (pedir cedo, sem exigir tudo) ===
Laudo/relatório do médico assistente com CID, prescrição detalhada e, quando
houver, indicação expressa de urgência ou risco de agravamento — prova
central de quase toda tese; documento de identidade e CPF do paciente (ou de
quem representa, se incapaz); comprovante de residência e de renda/ausência
de renda (sustenta a gratuidade de justiça); protocolo do pedido administrativo
e negativa por escrito, ou prova do silêncio (número de protocolo, prints,
e-mails); notas fiscais/recibos de gastos já suportados pela família
(medicamento, insumo, transporte, hospedagem); exames e evolução clínica
recente. Não precisa de tudo para protocolar, mas dossiê completo = tutela
mais robusta e mais rápida.

=== FAQ DO LEAD (perguntas reais, respostas honestas) ===
1. "Quanto custa?" -> Depende das frentes mapeadas no caso e costuma envolver
   parte por êxito; o número exato só se define na reunião reservada, nunca
   por mensagem (Provimento 205/2021).
2. "Vou ter que pagar custas do processo?" -> Na maioria dos casos de saúde
   pública o paciente é hipossuficiente e tem direito à gratuidade de
   justiça, que dispensa o adiantamento de custas; isso se confirma caso a
   caso.
3. "Quanto tempo demora?" -> Quando há risco real à saúde, pede-se tutela de
   urgência, que pode ser apreciada em poucos dias; o processo até a decisão
   final costuma levar mais tempo, sem prazo fechado possível de prometer.
4. "Vou ter que ir ao fórum?" -> Na maioria dos casos não é preciso
   comparecer com frequência; boa parte do processo tramita com base em
   documentos e decisões escritas.
5. "Posso perder?" -> Sim, é possível; por isso o trabalho começa avaliando
   se há prescrição/indicação médica clara e prova da negativa ou demora do
   Estado, antes de qualquer promessa.
6. "Processar o Estado vai piorar meu atendimento depois, ou vão me tratar
   pior no posto?" -> Buscar a Justiça não retira nem prejudica o direito do
   paciente a outros atendimentos do SUS; são situações independentes.
7. "Preciso de advogado, ou dá para pedir pela Defensoria Pública?" -> É
   possível buscar a Defensoria Pública quando elegível; o acompanhamento
   particular ajuda a instruir o pedido com mais agilidade e a já preparar a
   tutela de urgência, cada família decide o caminho que faz sentido para
   ela.
8. "O remédio/tratamento que eu preciso não está na lista do SUS, ainda
   assim dá para pedir?" -> Sim, a ausência na lista oficial não encerra o
   direito quando há necessidade comprovada e ausência de alternativa
   adequada já disponível no SUS; isso se avalia no diagnóstico do caso.
9. "Depois que eu ganhar, isso é para sempre ou preciso processar de novo se
   precisar continuar o tratamento?" -> Depende do tipo de decisão e da
   duração do tratamento prescrito; isso se explica com clareza assim que o
   pedido é definido, sem prometer definitividade automática.
10. "E se eu não tiver todos os documentos agora?" -> Não precisa ter tudo
    para começar; dá para ir reunindo aos poucos enquanto o caso avança, e o
    laudo médico é o documento mais importante para priorizar.
11. "Como funciona o atendimento, é tudo remoto?" -> A reunião inicial e o
    envio de documentos podem ser feitos remotamente; presença física fica
    reservada a eventual audiência, o que costuma ser exceção nesse tipo de
    processo.
12. "Quanto tempo até a primeira reunião?" -> O atendimento inicial é ágil,
    normalmente marcado em poucos dias, especialmente quando há risco de
    agravamento clínico que justifique tutela de urgência imediata.
13. "Como acompanho o andamento do processo?" -> Você recebe atualização nos
    marcos relevantes (protocolo, decisão da tutela, cumprimento pelo
    Estado); não precisa acompanhar sozinho os autos.
14. "Como envio o laudo médico e os demais documentos?" -> Pode enviar foto
    ou PDF pelo mesmo canal do atendimento assim que tiver em mãos; o laudo
    com CID e prescrição é o documento mais urgente de priorizar.
15. "Existe prazo para eu entrar com a ação depois da negativa?" -> Não há
    prazo curto que extinga o direito ao tratamento em si, mas quanto mais
    tempo sem tutela de urgência, maior o risco de agravamento sem
    cobertura; por isso a urgência real da área é clínica, não processual.
16. "Se eu perder, posso recorrer?" -> Sim, cabe recurso contra decisão
    desfavorável (apelação e, quando cabível, recursos superiores);
    recorrer não garante reforma, mas é direito exercido quando há
    fundamento técnico.
17. "Por que contratar um especialista em saúde pública, e não um advogado
    generalista?" -> O réu é a Fazenda Pública, com prazos e regime
    processuais próprios (prazo em dobro, reexame necessário), e a tutela de
    urgência exige laudo bem estruturado; rotina com esse tipo de caso reduz
    o risco de indeferimento por falha técnica evitável.
18. "O que eu perco esperando para agir?" -> Quando há indicação de urgência
    ou risco de agravamento, cada semana sem o tratamento pode significar
    piora clínica que a Justiça não consegue reverter depois; a urgência
    aqui é a saúde, não uma conveniência processual.
19. "Como funcionam os honorários por êxito nessa área?" -> Parte relevante
    costuma ser atrelada ao resultado (deferimento da tutela, valor de
    eventual indenização), dividindo o risco entre cliente e escritório; a
    estrutura exata é apresentada na reunião reservada, nunca por mensagem
    (Provimento 205/2021).
20. "Se o Estado descumprir a decisão, o que acontece?" -> Cabe pedir
    medidas de cumprimento forçado, incluindo multa diária (astreintes) e,
    em casos graves, outras medidas coercitivas previstas em lei; o
    descumprimento reforça o caso, não o enfraquece.
21. "Posso pedir o tratamento contra um Estado ou Município diferente de
    onde moro?" -> Em regra a ação é ajuizada no domicílio do paciente ou
    onde está a unidade responsável pelo atendimento; a legitimidade
    (União, Estado, Município) depende de qual ente é responsável pelo
    fornecimento específico.
22. "O tratamento que eu preciso já está sendo julgado nos tribunais
    superiores, isso muda alguma coisa?" -> Pode mudar; quando há tema
    afetado em repercussão geral ou repetitivo, os critérios da decisão
    final tendem a se aplicar aos casos futuros — isso é sinalizado ao
    cliente e conferido na fonte oficial antes do protocolo, sem citar
    número de tema de memória.
23. "Posso pedir também ressarcimento do que já paguei do próprio bolso?" ->
    Sim, é possível pleitear o ressarcimento de gastos comprovados enquanto
    o Estado não forneceu o tratamento devido, desde que haja prova (notas
    fiscais, recibos) do valor gasto.
24. "Se eu contratar e depois quiser trocar de advogado, posso?" -> Sim, é
    direito do cliente substabelecer ou revogar procuração a qualquer
    momento; isso é tratado com transparência desde a primeira conversa.
25. "Preciso ter o laudo mais recente ou serve um antigo?" -> O laudo mais
    atual, com CID, prescrição detalhada e indicação de urgência quando
    houver, tem mais força; laudo antigo pode ser usado para instruir o
    caso, mas a equipe orienta se é necessário atualizá-lo.
26. "O que acontece se o pedido administrativo ainda não tiver resposta?" ->
    O silêncio administrativo prolongado também pode fundamentar o pedido
    judicial, especialmente diante de urgência clínica; a prova do
    protocolo já é suficiente para começar a avaliação.
27. "A decisão judicial obriga o SUS a fornecer para sempre?" -> Depende do
    tipo de decisão e da natureza do tratamento (contínuo ou por tempo
    determinado); isso é explicado com clareza assim que o pedido é
    formulado, sem prometer definitividade automática.
28. "Vou perder o acesso normal ao SUS se eu processar?" -> Não; buscar a
    Justiça para garantir um direito específico não retira nem prejudica o
    acesso do paciente a outros atendimentos do sistema público.
29. "Existe algum risco de eu ter que devolver o valor se perder depois de
    já estar recebendo o tratamento por tutela de urgência?" -> Em regra,
    valores gastos pelo Estado por decisão de urgência posteriormente
    revertida seguem tratamento próprio da execução contra a Fazenda; o
    paciente, salvo má-fé, não é normalmente quem arca com essa devolução —
    isso é esclarecido no caso concreto.
30. "Se eu não fizer nada agora, o que realmente muda?" -> A negativa ou o
    silêncio do Estado continua, o quadro clínico segue sem o tratamento
    indicado e a demora reduz a margem para reverter eventual agravamento;
    a urgência real da área é o próprio tempo perdido sem a saúde do
    paciente resolvida.

=== GOVERNANÇA DO AGENTE ===
- Urgência sempre real (risco de vida, agravamento, fila sem previsão, gasto
  já suportado pela família), nunca fabricada; expectativa honesta
  (obrigatória nos pontos técnicos ainda em julgamento, como o Tema 1234/STF
  sobre medicamento sem registro na ANVISA); zero promessa de resultado;
  honorários e valores nunca em mensagem/conteúdo público (Provimento
  205/2021).
- Fundamento-âncora serve à conversa; antes do protocolo, todo precedente é
  conferido na fonte oficial (fluxo da casa: agente da matéria + revisão).
  Onde não houver certeza do número exato de um tema/súmula, citar o
  instituto sem número e sinalizar a conferência.
- Réu é a Fazenda Pública (União, Estado ou Município): distinto da saúde
  suplementar; regime de direito público, prazos próprios (prazo em dobro da
  Fazenda, reexame necessário quando cabível), gratuidade de justiça como
  regra a oferecer, não a exceção.
- Paciente em quadro clinicamente grave, com risco de vida ou de agravamento
  iminente: acolher com "sinto muito" quando a dor comportar, ir mais
  devagar, uma pergunta por vez, coletar o essencial e ESCALAR a humano
  imediatamente; o agente nunca segura sozinho um caso de risco.
- Envio real de mensagem é ação externa: gates + template/janela do canal.
"""

PLAYBOOK_SAUDE_PUBLICA_SUS_SOURCES = [
    "Guia proprietário: Script de Vendas · Saúde Pública (SUS) (2026)",
    "CF/1988, art. 196 e art. 198 (direito à saúde e organização do SUS)",
    "CF/1988, art. 37, §6º (responsabilidade objetiva do Estado)",
    "Lei 8.080/90 (Lei Orgânica da Saúde), arts. 19-M a 19-U (Lei 12.401/2011)",
    "Portaria GM/MS nº 963/2013 (Atenção Domiciliar no SUS — Melhor em Casa)",
    "Tema 6/STF (RE 566.471) — requisitos para fornecimento de medicamento",
    "Tema 793/STF (RE 855.178) — solidariedade dos entes federados",
    "Tema 1234/STF — medicamento sem registro na ANVISA (conferir na fonte)",
    "Resolução CNJ nº 238/2016 (NAT-Jus — apoio técnico ao Judiciário)",
    "CPC, arts. 98 a 102 (gratuidade de justiça); art. 85, §3º (honorários "
    "contra a Fazenda Pública)",
    "Lei 12.153/2009 (Juizados Especiais da Fazenda Pública Estadual e "
    "Municipal); Lei 10.259/2001 (Juizados Especiais Federais)",
    "Provimento 205/2021 OAB (publicidade)",
]
