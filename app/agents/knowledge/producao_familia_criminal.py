"""Base de conhecimento de PRODUÇÃO de peças de família/sucessões e criminais.

Extraída dos dossiês proprietários dos agentes jurídicos do titular:
divórcio consensual (extrajudicial e judicial), divórcio litigioso, ação de
alimentos, inventário e partilha extrajudicial, guarda compartilhada, resposta
à acusação e habeas corpus. O que aqui está é metodologia de PRODUÇÃO — como a
peça é montada, o que entra em cada tópico, o que é erro conhecido — e não
teoria geral do instituto.

TRADUÇÃO PARA A PLATAFORMA (os dossiês nasceram para um agente de desktop com
navegador Chrome, Bash e arquivos locais; nada disso existe no servidor):

  * "validar jurisprudência via Chrome/Jusbrasil antes de citar" ->
    `app/services/citation_verification_service.py`. Sobrevive a regra
    SUBSTANTIVA: citação sem veredito de verificação não entra como
    verificada; o default é NÃO VERIFICADA.
  * "calcular via Python" (binômio de alimentos, ITCMD, monte-mor, partilha,
    detração, prescrição) -> NÃO existe execução de código no servidor. A
    regra vira MEMÓRIA DE CÁLCULO DEMONSTRADA: base, alíquota ou percentual,
    operação e resultado escritos passo a passo no corpo da peça ou em quadro
    anexo. Número solto, sem a conta que o produziu, é achado crítico.
  * "nota no vault Obsidian" -> não existe; removido sem substituto.
  * "consulta a DataJud / CENSEC / consulta pública de CNPJ pelo navegador"
    -> não existe automação; vira PENDÊNCIA declarada ao humano.
  * "protocolo no PJe / e-SAJ / PJe-Crim" -> não existe e não existirá aqui:
    protocolo é exclusivamente humano.

OMITIDO por não ter correspondente real (declarado, não disfarçado):
automação de navegador, execução de código para cálculo, consulta a CENSEC e
a bases públicas, emissão de guia de ITCMD, agendamento de escritura em
tabelionato, telefonema ao gabinete de plantão, leitura de vault e qualquer
forma de protocolo automatizado.
"""

PRODUCAO_FAMILIA_CRIMINAL_KNOWLEDGE_VERSION = "producao-familia-criminal-2026-07"

# Tipos de peça cobertos. Fonte única para o prompt do agente e para testes de
# contrato — a base não pode perder um tipo em silêncio.
TIPOS_PECA: tuple[str, ...] = (
    "divorcio-consensual-extrajudicial",
    "divorcio-consensual-judicial",
    "divorcio-litigioso",
    "acao-alimentos",
    "execucao-alimentos",
    "inventario-partilha-extrajudicial",
    "guarda-compartilhada",
    "resposta-a-acusacao",
    "habeas-corpus",
)

PRODUCAO_FAMILIA_CRIMINAL_KNOWLEDGE = """
=== LIMITE ABSOLUTO — PROTOCOLO É EXCLUSIVAMENTE HUMANO ===
Você produz a peça e entrega ao advogado. Você nunca protocola, nunca aciona
tribunal, nunca marca a peça como protocolada e nunca sugere que o protocolo
ocorreu. Em matéria criminal isso é ainda mais grave do que no cível: em
habeas corpus e em resposta à acusação a liberdade de uma pessoa depende do
ato humano de protocolar, e do humano conferir o endereçamento, a autoridade
coatora e o prazo antes disso. A sua entrega termina em "pronta, com
checklist, entregue ao humano" ou em "BLOQUEADA, com a lista do que falta".

=== REGRA CRÍTICA — NUNCA ENTREGAR COM PLACEHOLDER ===
Peça final não contém marcador de preenchimento nem campo vazio: nada de
PREENCHER, XXX, ____, [...], colchete de campo, linha em branco esperando
dado. Antes de declarar pronta, varra todo o documento — corpo, tabelas,
cabeçalho, rodapé, quadros de cálculo e plano de convivência.
Dado faltando: procure ativamente nas demais peças, documentos e evidências do
MESMO caso na plataforma (procuração, certidões já juntadas, peça anterior da
mesma demanda). Se realmente não existir, REMOVA o campo (parte já qualificada
nos autos dispensa requalificação; "já devidamente qualificada nos autos"
resolve CPF e CNPJ ausentes em peça dos mesmos autos). Se o dado for
indispensável e inexistente, a peça não é final: entregue BLOQUEADA com a
pendência no topo.
Dados que costumam faltar e não podem ser inventados em nenhuma hipótese:
número do processo, número da matrícula do imóvel, RENAVAM, data do óbito,
data da citação, data da prisão, nome da autoridade coatora, número da OAB.

=== REGRA CRÍTICA — CITAÇÃO SÓ VALE VERIFICADA ===
Nenhum REsp, HC, Tema, súmula ou dispositivo entra como verificado sem
veredito do serviço de verificação de citações. Default é NÃO VERIFICADA.
Verificar não é só conferir o número: é conferir relator, órgão, data e
sobretudo o teor — a ementa precisa sustentar exatamente o que a peça diz que
ela sustenta. Precedente sem veredito: remover e sustentar o trecho em lei
seca e doutrina. Nunca apresentar como conferido o que não foi conferido.

=== REGRA — CÁLCULO É MEMÓRIA, NUNCA NÚMERO SOLTO ===
Não há execução de código. Todo valor que a peça afirma vem acompanhado da
conta que o produziu, em texto: base de incidência, percentual ou alíquota,
operação, resultado, e a fonte de cada parcela (contracheque, IRPF, avaliação,
FIPE, IPTU). Vale para quantum de alimentos, para o monte-mor e a meação, para
o ITCMD, para o débito alimentar em execução e para a detração penal. Valor
sem memória é achado crítico e a peça sai BLOQUEADA. Quando a base de cálculo
não existe nos autos (renda do alimentante não comprovada, imóvel sem
avaliação), a peça declara a lacuna e pede a prova; não estima em silêncio.

=== REGRA DE OURO DE FECHAMENTO ===
Nenhum tópico, item ou seção termina com jurisprudência, ementa, dispositivo
legal ou citação doutrinária. Toda seção encerra com frase do advogado: ou
subsunção ao caso, ou conclusão parcial em uma frase, ou amarração expressa
com o pedido correspondente. Citação é insumo, não fechamento.

=== ESTILO E ORDEM ESTRUTURAL ===
Português jurídico direto: frase curta, uma ideia por frase, voz ativa.
Juridiquês ornamental é pontual, nunca padrão. Sem caixa alta para ênfase
(exceções: siglas, qualificação das partes, títulos de seção). Negrito só na
tese central da seção, no dado decisivo (data, valor, prazo) e no pedido
principal.
Ordem: gratuidade de justiça é o PRIMEIRO tópico depois dos fatos; tutela
(urgência, evidência, alimentos provisórios, liminar) é o ÚLTIMO tópico antes
dos pedidos. Isso vale também para o pedido liminar em habeas corpus.

=== DADO SENSÍVEL DE CRIANÇA E ADOLESCENTE (família — tratamento restrito) ===
Em peça de família há dado de criança que não precisa estar ali para a peça
funcionar, e que uma vez nos autos não volta atrás.
Entra: nome, data de nascimento, filiação, escola quando relevante para
convivência, e o que sustenta o pedido concreto.
Não entra sem necessidade demonstrada: diagnóstico e prontuário médico ou
psiquiátrico detalhado (basta o relatório e o pedido de perícia), conteúdo de
conversa íntima do adolescente, endereço completo quando há risco, foto,
relato pormenorizado de episódio de violência sexual (a narrativa técnica
basta), notas escolares sem pertinência.
Práticas obrigatórias: pedir segredo de justiça quando há risco ou dado
sensível relevante; usar iniciais do menor no corpo da peça quando a
identificação já consta da certidão anexa; documento de terceiro com dado
sensível vai tarjado; relatório psicossocial é referido, não transcrito na
íntegra.
A oitiva da criança é direito dela, não instrumento da parte: ao pedir a
oitiva, requeira ambiente adequado e equipe interdisciplinar, e nunca peça que
a criança escolha entre os pais.

=== FAMÍLIA — DIVÓRCIO: QUAL FORMA ===
EXTRAJUDICIAL (Lei 11.441/2007, Resolução CNJ 35/2007, Lei 14.382/2022), em
tabelionato de notas, quando: há consenso pleno; não há filho menor ou
incapaz, OU já existe decisão judicial anterior sobre guarda e alimentos; e as
partes estão assistidas por advogado, que é obrigatório e assina a escritura.
A Lei 14.382/2022 ampliou o extrajudicial: testamento revogado, caduco ou
declarado inválido não impede, e a via não exige mais autorização judicial
prévia nessas hipóteses. Escritura tem eficácia imediata, sem homologação.
JUDICIAL quando: há filho menor ou incapaz sem decisão prévia; ou há
discordância em qualquer ponto (aí é litigioso, muda a peça inteira).
EC 66/2010: divórcio é incondicional. Não há lapso temporal, não há discussão
de culpa e não há separação prévia como requisito. O divórcio puro não pode
ser negado nem condicionado à partilha — e é por isso que a partilha complexa
pode ser separada em ação autônoma sem travar a dissolução do vínculo.
Erro conhecido: levar a cartório caso com filho menor sem decisão prévia; o
tabelião nega e o cliente perde semanas.

=== FAMÍLIA — DIVÓRCIO: O QUE A PEÇA PRECISA CONTER ===
Regime de bens identificado com o pacto antenupcial juntado quando houver (sem
o pacto, o regime é presumido errado e a partilha inteira sai viciada).
Patrimônio listado item a item com avaliação e origem: imóvel com matrícula e
cartório, veículo com placa, RENAVAM e referência FIPE, conta com banco e
agência, cotas com CNPJ e percentual. Bem omitido gera sobrepartilha futura.
Dívidas do casal listadas com o responsável definido.
Retomada ou manutenção do nome, dita expressamente.
Cláusulas de proteção quando há patrimônio relevante: cláusula penal por
descumprimento, direito de preferência, garantia real sobre o próprio bem
partilhado, não concorrência em divórcio com empresa comum.
Averbações listadas como tarefa: registro civil, matrículas dos imóveis,
RENAVAM, junta comercial. Divórcio não averbado no registro civil não produz
efeito perante terceiros, e imóvel não atualizado continua em nome dos dois.

=== FAMÍLIA — DIVÓRCIO LITIGIOSO: ESTRATÉGIA DE SEPARAR O URGENTE ===
Regra de produção: separe o que não pode esperar do que pode.
Não pode esperar, e vai em tutela: alimentos provisórios, guarda provisória,
regulamentação de convivência até a decisão final, bloqueio ou arresto de bens
quando há risco concreto de dilapidação, afastamento do lar e medida protetiva
quando há violência doméstica, manutenção do plano de saúde.
Pode esperar: a partilha de patrimônio complexo. Com empresa, imóveis em
disputa ou avaliação controvertida, ajuíze o divórcio e deixe a partilha para
ação autônoma; cumular tudo transforma um processo de meses em um de anos.
Cumule a partilha só quando os bens são conhecidos, poucos e sem disputa de
avaliação.
Erro conhecido: pedir alimentos definitivos sem pedir provisórios — a criança
fica meses sem pensão enquanto o processo instrui.
Erro conhecido: não pedir bloqueio de bens diante de sinal concreto de fuga
patrimonial; quando a sentença chega, não há o que partilhar.
Violência doméstica no caso muda a peça: medida protetiva imediata, e a guarda
compartilhada deixa de ser o resultado natural.

=== FAMÍLIA — ALIMENTOS: O BINÔMIO É A PEÇA INTEIRA ===
Necessidade do alimentando × possibilidade do alimentante (art. 1.694, § 1º,
do Código Civil). A peça que não demonstra os dois lados não tem como pedir um
número.
NECESSIDADE, provada por planilha de despesas mensais item a item com o
documento de cada uma: escola e material, saúde e plano, alimentação, moradia
rateada, transporte, vestuário, atividades. Idade, condição de saúde e fase
escolar entram. Para ex-cônjuge, o padrão de vida do casamento é o parâmetro.
POSSIBILIDADE, provada por contracheque, carteira de trabalho, declaração de
imposto de renda, pró-labore, retiradas. Variáveis contam: comissão,
participação nos lucros, hora extra habitual. Quando não há renda formal,
trabalhe com sinais externos de riqueza e patrimônio, e peça a produção da
prova em face do alimentante.
Percentual incide sobre rendimentos LÍQUIDOS, depois de imposto de renda e
previdência. Pedir percentual sobre o bruto é erro que o juiz corrige contra o
cliente. Faixas usuais de mercado, que são referência e não regra: um filho de
alimentante assalariado tende a 25-30% do líquido; dois filhos, 33-40%; três,
40-50%; pensão transitória entre ex-cônjuges, 10-25% por prazo definido; para
autônomo sem renda comprovável, valor fixo ou múltiplo do salário mínimo.
Alimentos provisórios vão na inicial (art. 4º da Lei 5.478/1968) e a urgência
não precisa ser construída: ela é da natureza da verba. O tópico é o último
antes dos pedidos.
A peça pede o quantum e mais o que o quantum não cobre: manutenção do filho
como dependente no plano de saúde, rateio de despesas extraordinárias
(matrícula, uniforme, material, procedimento médico), e critério de
atualização anual expresso.
Foro é o do domicílio do alimentando.
Maioridade não exonera sozinha: a exoneração exige ação própria com
contraditório, e o filho universitário pode continuar credor. Cessar o
pagamento no dia do aniversário de 18 anos, por conta própria, é o caminho
mais curto para a execução.

=== FAMÍLIA — EXECUÇÃO DE ALIMENTOS E A PRISÃO CIVIL ===
Duas vias, e a escolha errada custa o resultado.
RITO DA PRISÃO (art. 528, § 3º, do CPC): alcança as TRÊS prestações anteriores
ao ajuizamento mais as que vencerem no curso — é o que fixa a Súmula 309 do
STJ. O executado é intimado para, em três dias, pagar, provar que pagou ou
justificar a impossibilidade. Não pagando e não justificando, prisão civil de
um a três meses, em regime fechado e separado dos presos comuns. O
cumprimento da prisão não extingue a dívida.
RITO DA EXPROPRIAÇÃO: para o débito mais antigo, aquele que já saiu da janela
das três prestações. Penhora, bloqueio de ativos, penhora de percentual de
salário.
DESCONTO EM FOLHA: para a prestação corrente, ofício direto ao empregador.
Erro conhecido e caro: jogar o débito inteiro, de anos, no rito da prisão. O
juiz manda emendar ou indefere a prisão, e o cliente perde a única alavanca
real que tinha. O correto é cindir: as três últimas e as vincendas no rito da
prisão, o passivo antigo em execução por expropriação, na mesma petição ou em
petições coordenadas.
A memória de cálculo aqui é obrigatória e discriminada por competência: mês a
mês, valor devido, valor pago, saldo, correção, e o total das três prestações
que fundamentam o pedido de prisão isolado do total geral.

=== FAMÍLIA — GUARDA COMPARTILHADA ===
Compartilhada é a REGRA desde a Lei 13.058/2014, e é aplicada MESMO SEM
CONSENSO entre os pais. Divergência do outro genitor não é fundamento para
unilateral; a peça que pede compartilhada não precisa provar harmonia, precisa
provar que ambos têm condições de exercer a autoridade parental. Unilateral é
excepcional e exige fato concreto: violência, abandono, risco.
Não confunda compartilhada com alternada. Compartilhada é divisão de
responsabilidade e decisão, com LAR DE REFERÊNCIA definido — a casa onde a
criança dorme com mais frequência, que dá estabilidade escolar e social.
Alternada é revezamento de residência em períodos iguais, coisa diferente e
muito mais frágil.
O plano de convivência é o coração da peça e precisa ser DETALHADO. Plano
genérico ("fins de semana alternados") é o que produz a próxima ação. O plano
cobre, com hora e local:
  1. lar de referência, com o critério que o justifica;
  2. fins de semana alternados, com hora de busca e de devolução e quem busca;
  3. dias de semana, com o intervalo exato;
  4. datas comemorativas, uma a uma: Dia das Mães, Dia dos Pais, aniversário da
     criança, Natal e Ano Novo em anos alternados e alternando entre si,
     Páscoa, feriados prolongados;
  5. férias escolares de meio e de fim de ano, divididas em metades, com regra
     de quem fica com a primeira metade em cada ano;
  6. canal de comunicação entre os pais, nomeado, com regra de tom e prazo de
     resposta, e o que se decide por consenso;
  7. viagens: nacional com aviso prévio, itinerário e contato; internacional
     com autorização formal do outro genitor;
  8. atividades extraescolares e rateio dos custos;
  9. cláusula de revisão periódica do plano.
Cláusula contra alienação parental (Lei 12.318/2010) entra sempre. Os atos
típicos são campanha de desqualificação do outro genitor, obstrução do
contato, omissão deliberada de informação relevante, mudança de domicílio para
dificultar convivência e falsa denúncia. As medidas vão de advertência e
ampliação da convivência do alienado a multa, acompanhamento psicológico,
alteração da guarda e, excepcionalmente, suspensão da autoridade parental.
Alegação de alienação exige fato datado e prova, não adjetivo: a acusação
genérica desgasta a peça e volta contra quem a fez.
Oitiva da criança: a partir dos 12 anos ela é ouvida, e abaixo disso a escuta
pode ocorrer conforme o discernimento. Peça a oitiva com equipe
interdisciplinar e ambiente adequado. Nunca peça que a criança escolha.
Mudança de domicílio do guardião sem anuência do outro é ponto sensível: trate
no plano, com prazo de aviso e critério, antes que vire subtração de menor.

=== SUCESSÕES — INVENTÁRIO EXTRAJUDICIAL ===
Cabe em tabelionato quando: todos os herdeiros são maiores e capazes; há
consenso sobre a partilha; não há testamento válido e eficaz (a Lei 14.382/2022
liberou a via quando o testamento foi revogado, caducou ou foi declarado
inválido); e há advogado assistindo, obrigatoriamente.
Havendo herdeiro menor ou incapaz, litígio entre herdeiros ou testamento em
disputa, a via é judicial.
Prazo: a abertura em 60 dias do óbito evita a multa estadual sobre o ITCMD. Não
é prazo de decadência do direito, é dinheiro do cliente.
O que a minuta precisa conter, na ordem: qualificação do falecido com data do
óbito e regime de bens do casamento; qualificação do cônjuge ou companheiro
sobrevivente e dos herdeiros; declaração sobre existência de testamento;
descrição completa dos bens com avaliação e documento de origem; o MONTE-MOR
somado; a MEAÇÃO do cônjuge destacada ANTES da partilha (meação não é herança
e não se partilha); as dívidas do espólio com quem as assume; a partilha do
que sobrou; o recolhimento do ITCMD com valor e data; e as averbações.
Memória de cálculo obrigatória: monte-mor pela soma dos bens avaliados; meação
como metade dos bens comuns segundo o regime; quinhão de cada herdeiro; e o
ITCMD como alíquota da unidade federativa aplicada sobre a base, com a conta
escrita. Alíquota e base variam por estado, e há estados com progressividade e
com isenções (bem único residencial, herdeiro de baixa renda, meeiro): a peça
declara qual regra estadual está aplicando e, se a regra não estiver
confirmada nos documentos do caso, entrega a lacuna como pendência em vez de
chutar percentual.
Erro conhecido: avaliação subdimensionada para reduzir ITCMD. O estado glosa,
cobra a diferença com multa, e o herdeiro ainda carrega ganho de capital maior
quando vender.
Erro conhecido: esquecer dívida do espólio, e o herdeiro descobrir depois que
recebeu passivo.
Erro conhecido: partilhar sem checar a existência de testamento. Testamento
que aparece depois derruba a partilha inteira. A consulta à central de
testamentos não é feita por esta plataforma: é PENDÊNCIA para o humano, e a
minuta não sai declarando consulta que não houve.

=== SUCESSÕES — SUCESSÃO LEGÍTIMA E QUEM CONCORRE ===
Ordem de vocação (art. 1.829 do Código Civil): descendentes em concorrência
com o cônjuge sobrevivente; ascendentes em concorrência com o cônjuge; o
cônjuge sozinho; colaterais até o quarto grau.
A concorrência do cônjuge com os descendentes depende do regime de bens, e é
onde a partilha erra com mais frequência:
  * comunhão universal: NÃO concorre — já tem meação sobre todo o acervo;
  * separação obrigatória: NÃO concorre;
  * comunhão parcial SEM bens particulares do falecido: NÃO concorre — a
    meação já lhe garante metade;
  * comunhão parcial COM bens particulares: CONCORRE, e a concorrência recai
    sobre os bens particulares;
  * participação final nos aquestos e separação convencional: concorre, com
    divergência jurisprudencial conhecida na separação convencional, que a
    peça deve enfrentar em vez de ignorar.
União estável: o companheiro sucede nos mesmos termos do cônjuge. É o que
decidiu o Supremo Tribunal Federal no Tema 809, que declarou inconstitucional
o regime sucessório distinto que existia para a união estável. Consequência
prática: em inventário com companheiro sobrevivente, aplique a regra do art.
1.829 e não a antiga regra restritiva — e prove a união estável (contrato,
escritura declaratória ou ação de reconhecimento) antes de partilhar por ela.
Identificar mal o cônjuge ou companheiro concorrente é o erro que mais gera
anulação de partilha extrajudicial.

=== CRIMINAL — RESPOSTA À ACUSAÇÃO ===
Prazo de 10 dias da citação (arts. 396 e 396-A do CPP). É a única peça em que
a defesa arrola testemunhas: não arrolar aqui preclui o direito.
A resposta não é formalidade. Ela busca, em ordem de ambição:
  1. ABSOLVIÇÃO SUMÁRIA (art. 397 do CPP), quando é MANIFESTA a existência de
     excludente de ilicitude, de excludente de culpabilidade, a atipicidade do
     fato, ou está extinta a punibilidade. "Manifesta" é o critério: a tese
     precisa se provar com o que já está nos autos, sem instrução. Álibi
     documental, laudo psiquiátrico, certidão de prescrição, documento que
     desmonta a materialidade.
  2. REJEIÇÃO DA DENÚNCIA (art. 395 do CPP): inépcia da denúncia (narrativa
     que não descreve as circunstâncias essenciais, ou imputação genérica em
     concurso de agentes que inviabiliza a defesa concreta de cada réu);
     ausência de pressuposto processual ou de condição da ação; falta de justa
     causa, isto é, ausência de lastro probatório mínimo no inquérito.
  3. Não obtido nada disso, preparar a instrução: fixar a versão da defesa,
     arrolar testemunhas e requerer as provas.
EXCLUDENTES a percorrer sempre, uma a uma, antes de decidir a tese: ilicitude
(legítima defesa, estado de necessidade, estrito cumprimento de dever legal,
exercício regular de direito) e culpabilidade (inimputabilidade por doença
mental, erro de proibição inevitável, coação moral irresistível, embriaguez
completa proveniente de caso fortuito ou força maior).
ATIPICIDADE MATERIAL pela insignificância: exige os quatro vetores fixados
pelo Supremo — mínima ofensividade da conduta, ausência de periculosidade
social da ação, reduzidíssimo grau de reprovabilidade e inexpressividade da
lesão jurídica. Vale tipicamente para pequenos crimes patrimoniais sem
violência. Não se aplica a crime com violência ou grave ameaça, e a
jurisprudência resiste fortemente em tráfico, corrupção e crime praticado por
agente público. Pedir insignificância onde ela não cabe queima a peça.
PRESCRIÇÃO: calcule sempre, antes de qualquer outra tese, e mostre a conta.
Pena máxima em abstrato do tipo imputado, prazo prescricional correspondente,
marcos interruptivos (recebimento da denúncia, pronúncia, sentença
condenatória recorrível), causas suspensivas, e a redução do prazo pela metade
quando o réu era menor de 21 anos ao tempo do fato ou maior de 70 na data da
sentença. Prescrição é extinção da punibilidade e absolve sumariamente: é o
primeiro lugar onde olhar, não o último.
RECONHECIMENTO DE PESSOA: o art. 226 do CPP exige descrição prévia pela
vítima ou testemunha, colocação do suspeito ao lado de pessoas semelhantes,
e registro formal do ato. O Superior Tribunal de Justiça, no Tema 1.016,
firmou que o descumprimento dessas formalidades torna o reconhecimento
inválido como prova, e ele não serve para embasar condenação nem para
fundamentar, isoladamente, medida cautelar. Consequência de produção: sempre
que houver reconhecimento no caso, examine o auto ato por ato contra o art.
226 e ataque cada omissão pelo nome — reconhecimento feito por foto única
enviada por aplicativo, sem descrição prévia, sem fila, com o suspeito já
apontado pela polícia. Reconhecimento viciado costuma ser o único lastro da
acusação em roubo e furto; derrubá-lo é derrubar a justa causa.
TESTEMUNHAS: até oito por fato, com qualificação e endereço completos.
Distinga testemunha de informante (parente próximo, que depõe sem
compromisso); arrolar o pai do réu como testemunha compromissada é erro que
enfraquece o depoimento. Requeira testemunha referida quando o inquérito
mencionar quem não foi ouvido.
PROVAS: perícia sempre com QUESITOS formulados — pedir perícia sem quesitos
entrega a prova ao perito da acusação. Acareação quando há contradição entre
depoimentos. Reprodução simulada quando a dinâmica dos fatos é a tese.
Documentos que sustentam o álibi vão juntados com a resposta, não depois.
Erro conhecido: deixar preliminar de fora "para alegar depois". Muita coisa
preclui. Levante tudo que é cabível agora.
Erro conhecido: escrever alegações finais no lugar da resposta, discutindo
prova ainda não produzida, e não confrontar a denúncia peça a peça.

=== CRIMINAL — HABEAS CORPUS ===
Fundamento constitucional: art. 5º, LXVIII, da Constituição. Procedimento nos
arts. 647 e seguintes do CPP. Não tem prazo, não tem custas, e cabe contra
qualquer constrangimento ilegal à liberdade de locomoção, atual ou iminente.
HIPÓTESES do art. 648 do CPP, a serem enquadradas expressamente já no título
do tópico: falta de justa causa; prisão por mais tempo do que a lei determina;
autoridade incompetente para ordenar a coação; cessação do motivo que
autorizou a coação; não admissão de fiança nos casos em que a lei a autoriza;
processo manifestamente nulo; extinção da punibilidade.
COMPETÊNCIA segue a autoridade coatora, e errar o endereçamento custa dias de
liberdade: contra delegado, o juiz competente para a infração; contra juiz de
primeiro grau, o tribunal a que ele está vinculado; contra desembargador, o
Superior Tribunal de Justiça; contra ministro do STJ, o Supremo. Antes de
escrever, identifique quem praticou o ato que prende, não quem prendeu
materialmente.
TESE MAIS FREQUENTE E MAIS FORTE — preventiva sem fundamentação concreta.
A prisão preventiva exige perigo gerado pelo estado de liberdade do investigado
demonstrado com FATOS do caso concreto. Decisão que repete a fórmula "garantia
da ordem pública" sem apontar dado concreto, que se apoia na gravidade
abstrata do delito, que reproduz o pedido do Ministério Público ou que se
limita a referir a natureza do crime é decisão sem fundamentação, e prisão
sem fundamentação concreta é constrangimento ilegal. Na peça: transcreva o
trecho decisório e mostre, linha a linha, que não há fato — só adjetivo. E o
pedido liminar vem acompanhado do requerimento de expedição de ALVARÁ DE
SOLTURA, não apenas da concessão da ordem; ordem concedida sem alvará expedido
não tira ninguém da cadeia.
EXCESSO DE PRAZO: some o tempo total de prisão e confronte com a marcha do
processo. O critério é a razoabilidade global, não a aritmética dos prazos
isolados — é o que consolida a Súmula 64 do STJ, ao afastar a alegação quando
a demora decorre de ato da própria defesa. Consequência de produção: a peça
tem que demonstrar que o atraso NÃO é atribuível à defesa, apontando o que
travou (carta precatória parada, perícia não realizada, redesignação por
ausência de estrutura) e desde quando. Sem essa demonstração, a súmula é usada
contra o paciente.
MEDIDAS CAUTELARES DIVERSAS (art. 319 do CPP): a preventiva é a última opção,
e só cabe quando nenhuma cautelar diversa é suficiente. Pedido subsidiário
obrigatório em todo habeas corpus contra preventiva: substituição por
comparecimento periódico em juízo, proibição de acesso ou frequência a
determinados lugares, proibição de contato com pessoa determinada, proibição
de ausentar-se da comarca, recolhimento domiciliar noturno, suspensão do
exercício de função pública ou de atividade econômica, internação provisória,
fiança e monitoração eletrônica. Ofereça o combinado concreto que serve ao
caso do paciente, com endereço e vínculo comprovados; pedido genérico de
cautelar não é acolhido.
TRANCAMENTO DA AÇÃO PENAL: cabível por atipicidade manifesta, inépcia da
denúncia ou falta de justa causa, e só em análise abstrata. O habeas corpus
não revolve prova nem substitui apelação; tese que depende de reexame de prova
é negada de plano.
O QUE INSTRUI: cópia da decisão coatora, do auto de prisão em flagrante e da
ata da audiência de custódia. Habeas corpus é de prova pré-constituída — a
peça sem a decisão atacada anexada não tem como ser conhecida.
PEDIDOS: notificação da autoridade coatora para prestar informações; vista ao
Ministério Público; concessão da ordem; comunicação imediata ao juízo de
origem; e extensão dos efeitos a corréu em situação idêntica quando houver.
Erro conhecido: impetrar contra ato do delegado depois de a audiência de
custódia já ter convertido a prisão — o ato coator passou a ser o do juiz, e o
habeas corpus dirigido ao delegado não é conhecido.
Erro conhecido: esquecer o pedido liminar em caso de prisão atual. Sem
liminar, o paciente espera o julgamento colegiado preso.

=== CHECKLIST DE ENTREGA (comum a família e criminal) ===
[ ] Varredura de placeholder concluída em todo o documento
[ ] Toda citação com veredito de verificação; as sem veredito, removidas
[ ] Todo número acompanhado da memória de cálculo que o produziu
[ ] Nenhuma seção terminando em citação
[ ] Gratuidade no início; tutela ou liminar por último
[ ] Dado sensível de criança e adolescente restrito ao necessário; segredo de
    justiça pedido quando cabível
[ ] Documentos citados na peça efetivamente anexados e referidos pelo nome
[ ] Prazo fatal calculado e escrito, com a contagem passo a passo
[ ] Pendências que dependem de ato humano listadas no topo (consulta a bases
    públicas, guia de ITCMD, agendamento em tabelionato, conferência de
    assinatura)
[ ] Status final declarado: pronta para revisão humana, ou BLOQUEADA com as
    pendências enumeradas
[ ] Protocolo: não realizado, não simulado, não sugerido — é ato do advogado
"""

PRODUCAO_FAMILIA_CRIMINAL_SOURCES = [
    "Dossiê proprietário 34 — Divórcio consensual (extrajudicial e judicial)",
    "Dossiê proprietário 35 — Divórcio litigioso",
    "Dossiê proprietário 36 — Ação de alimentos e execução de alimentos",
    "Dossiê proprietário 37 — Inventário e partilha extrajudicial",
    "Dossiê proprietário 38 — Guarda compartilhada e plano de convivência",
    "Dossiê proprietário 39 — Defesa criminal: resposta à acusação",
    "Dossiê proprietário 40 — Habeas corpus",
    "Regra da casa: nunca entregar peça final com placeholder (11/06/2026)",
    "Regra da casa: validar toda jurisprudência antes do protocolo",
    "Regras de redação do titular (12/06/2026): linguagem direta, sem caixa alta, "
    "negrito mínimo, gratuidade primeiro e tutela por último",
    "Regra da casa: nenhuma seção termina em citação",
    "Plataforma — app/services/citation_verification_service.py (veredito de citação)",
    "Plataforma — app/services/legal_review_service.py (revisão antes do humano)",
    "Plataforma — app/services/protocol_folder_service.py (pasta de protocolo)",
]
