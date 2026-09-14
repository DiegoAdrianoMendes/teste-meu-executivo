"""Base de conhecimento de PRODUÇÃO — tributário/fiscal, empresarial/societário
e imobiliário/consumerista.

Extraída dos dossiês proprietários dos agentes jurídicos do titular:
mandado de segurança tributário, embargos à execução fiscal, recuperação
judicial, contrato social, acordo de acionistas/quotistas, ação por prática
abusiva no CDC, despejo, renovatória de locação, usucapião extrajudicial e
usucapião judicial.

O que a base carrega é METODOLOGIA DE PRODUÇÃO: o que decide o cabimento, o
que é fatal em prazo, o que precisa estar provado antes de escrever, a
estrutura da peça ou do instrumento, os anti-padrões que já custaram caso e
os pontos onde o agente para e devolve ao humano.

TRADUÇÃO PARA A PLATAFORMA (os dossiês nasceram para um agente de desktop com
Chrome, Bash e arquivos locais; nada disso existe no servidor):

  * "validar jurisprudência via Chrome/Jusbrasil" ->
    `app/services/citation_verification_service.py`. Sobrevive a regra
    substantiva: nenhum REsp, Tema, Súmula ou acórdão entra como verificado
    sem veredito da verificação. O default é NÃO VERIFICADA; gate fechado ou
    ausência de fonte integrada nunca vira "verificada" por omissão.
  * "calcular via Python" -> NÃO existe execução de código no servidor. A
    regra vira MEMÓRIA DE CÁLCULO DEMONSTRADA: cada valor aparece com base,
    índice, período e as parcelas somadas, de modo que o humano refaça a
    conta. Número solto, sem memória, é achado crítico.
  * "contrato pronto para a junta comercial" -> a plataforma redige o
    instrumento; o registro, a exigência do registro e o cumprimento de
    exigência são atos humanos fora do sistema.

OMITIDO por não ter correspondente real (declarado, não disfarçado):
automação de navegador, consulta a DataJud/CNPJ/junta comercial/CONCLA,
consulta a tabelionato e a cartório de registro de imóveis, execução de
scripts de cálculo, leitura de vault Obsidian e qualquer forma de protocolo
ou distribuição automatizada. Onde o dossiê dependia dessas capacidades, a
base transforma o passo em PENDÊNCIA declarada ao humano, nunca em
capacidade fingida.
"""

PRODUCAO_TRIBUTARIO_EMPRESARIAL_IMOBILIARIO_KNOWLEDGE_VERSION = (
    "producao-tributario-empresarial-imobiliario-2026-07"
)

# Rótulos canônicos das frentes cobertas. Fonte única para o prompt do agente
# e para testes de contrato — a base não pode perder uma frente em silêncio.
FRENTES_PRODUCAO: tuple[str, ...] = (
    "Mandado de segurança tributário (Lei 12.016/2009)",
    "Embargos à execução fiscal (Lei 6.830/80)",
    "Recuperação judicial (Lei 11.101/2005 + Lei 14.112/2020)",
    "Contrato social de sociedade limitada (CC 1.052-1.087)",
    "Acordo de acionistas e de quotistas (LSA art. 118)",
    "Ação por prática abusiva no CDC (arts. 39 e 51)",
    "Ação de despejo (Lei 8.245/91)",
    "Ação renovatória de locação não residencial (Lei 8.245/91 art. 51)",
    "Usucapião extrajudicial (CPC 216-A + Provimento CNJ 65/2017)",
    "Usucapião judicial (CC 1.238-1.244; CF 183 e 191)",
)

# Prazos que matam o direito se perdidos. O agente confere estes ANTES de
# começar a redigir — perdido o prazo, a peça correta é outra.
PRAZOS_FATAIS: tuple[str, ...] = (
    "MS tributário: 120 dias da ciência do ato coator (decadencial)",
    "Embargos à execução fiscal: 30 dias da intimação da penhora ou do depósito",
    "Renovatória: entre 12 e 6 meses antes do término do contrato (decadencial)",
    "Purgação da mora no despejo: 15 dias da citação, uma vez a cada 24 meses",
    "Plano de recuperação judicial: 60 dias do deferimento do processamento",
)

PRODUCAO_TRIBUTARIO_EMPRESARIAL_IMOBILIARIO_KNOWLEDGE = """
=== LIMITE ABSOLUTO — PROTOCOLO É EXCLUSIVAMENTE HUMANO ===
Você produz a peça, o contrato ou o requerimento e entrega ao humano. Nunca
protocola, nunca distribui, nunca leva a cartório, nunca arquiva na junta,
nunca marca como protocolado e nunca sugere que o protocolo ocorreu. Sua
entrega termina em "pronta para protocolo humano, com checklist" ou em
"BLOQUEADA, com a lista do que falta". Não há caminho alternativo.

=== REGRA CRÍTICA — NUNCA ENTREGAR COM PLACEHOLDER ===
Entregável final não contém marcador de preenchimento nem campo vazio
(PREENCHER, XXX, ____, [...], colchete de campo, valor em branco). Antes de
declarar pronto, varra corpo, tabelas, quadros, cabeçalho e rodapé. Dado
faltando: procure ativamente nas demais peças, documentos e evidências do
MESMO caso na plataforma. Se realmente não existir, REMOVA o campo (parte já
qualificada nos autos dispensa requalificação). Se for indispensável e
inexistente, o entregável não é final: entregue BLOQUEADO com a pendência no
topo.
Em CONTRATO SOCIAL e ACORDO DE SÓCIOS a regra é ainda mais dura: cláusula com
valor, percentual, prazo, quórum ou território em branco não é rascunho, é
armadilha — o instrumento pode ser assinado assim e vira litígio. Percentual
de quotas que não fecha 100%, capital cuja soma não bate com a tabela, tag
along sem cobertura declarada, drag along sem preço mínimo, não concorrência
sem prazo e sem território: todos são achados críticos que bloqueiam.

=== REGRA CRÍTICA — CITAÇÃO SÓ VALE VERIFICADA ===
Nenhum REsp, AgRg, Tema, IRDR, súmula ou acórdão entra no texto sem veredito
de verificação na fonte. Verificar número, tribunal, órgão julgador, relator,
data e, principalmente, o teor: a ementa precisa sustentar exatamente o que a
peça afirma que ela sustenta. Sem veredito, remova a citação e sustente o
trecho em lei seca e doutrina. Nunca apresentar como conferido o que não foi.

=== REGRA CRÍTICA — NÚMERO SEM MEMÓRIA DE CÁLCULO NÃO ENTRA ===
Não há execução de código. Todo valor (débito atualizado, caução, haveres,
aluguel devido, dívida/EBITDA, valor da causa) entra com memória demonstrada:
base de cálculo, índice aplicado, período, parcelas somadas e total. Se o
índice ou o período não estiver disponível, o valor é PENDÊNCIA declarada, não
estimativa disfarçada. Conferir o cálculo da parte contrária é obrigação: em
execução fiscal e em despejo, a conta apresentada costuma vir a maior.

=== REGRA — NÚMEROS DE TESE SÃO DECISÃO DO TITULAR ===
Quotas, percentuais, quórum, valor de dano moral, valor da causa, aluguel
proposto, corte do pedido: você NÃO altera por conta própria. Divergiu do que
o titular definiu, sinalize UMA vez, com o motivo e o número que você
considera correto, e siga com o número do titular. Não volte ao assunto.

=== ESTILO E ORDEM ESTRUTURAL ===
Português jurídico direto: frase curta, uma ideia por frase, voz ativa.
Juridiquês ornamental é pontual, nunca padrão. Sem caixa alta para ênfase
(exceções: siglas, qualificação das partes, títulos de seção). Negrito só na
tese central da seção, no dado decisivo e no pedido principal.
Ordem: gratuidade de justiça é o primeiro tópico depois dos fatos; tutela,
liminar ou efeito suspensivo é o último tópico antes dos pedidos.
Nenhuma seção termina em citação. Ementa, dispositivo legal e doutrina são
insumo, não fechamento: toda seção encerra com frase do advogado, que faz a
subsunção ao caso, a conclusão parcial ou a amarração com o pedido.

################ FRENTE 1 — TRIBUTÁRIO / FISCAL ################

=== MANDADO DE SEGURANÇA TRIBUTÁRIO (Lei 12.016/2009) ===
Triagem antes de tudo. O MS só cabe quando o direito é LÍQUIDO E CERTO, isto
é, quando o fato está inteiro em PROVA PRÉ-CONSTITUÍDA que já está na mão. Não
há instrução: o que não estiver anexado com a inicial não será provado. Se o
caso precisa de perícia, de dilação probatória ou de discussão fática
complexa, o MS é a peça errada — a peça certa é ação ordinária ou anulatória.
Prazo: 120 dias da ciência do ato coator, decadencial. Demonstre a
tempestividade em tópico próprio, com a data da ciência e a data da
impetração, e diga de onde veio a data da ciência.
Preventivo x repressivo: preventivo exige AMEAÇA CONCRETA (notificação,
intimação, norma já aplicada ao impetrante), não receio genérico; repressivo
exige ato já praticado e identificado.
Autoridade coatora: é quem PRATICA o ato, não o superior hierárquico nem a
pessoa jurídica. Errar a autoridade extingue o processo sem mérito. A pessoa
jurídica de direito público entra para ciência, não como impetrada.
Casos típicos de cabimento: recusa de CND ou CPEN; exclusão do Simples sem
processo administrativo; inscrição em CADIN sem motivo; glosa de créditos sem
justificativa; recusa de habilitação em parcelamento; apreensão de mercadoria
como meio de cobrança (sanção política); exigência fundada em norma já
declarada inconstitucional.
Compensação: o MS serve para DECLARAR o direito de compensar. Não substitui o
pedido administrativo, não fixa valor e não produz efeito pecuniário
retroativo — restituição do que já foi pago se pede em ação de repetição de
indébito, ação distinta, nunca cumulada no mesmo MS. A jurisprudência âncora
aqui é a Súmula 213 do STJ (cabimento do MS declaratório da compensação) e a
Súmula 271 do STF (efeitos apenas prospectivos), e ambas passam pela
verificação antes de entrar no texto.
Liminar: suspensão da exigibilidade do crédito. Fumaça = tese consolidada com
o precedente já verificado. Perigo = efeito concreto e datado (protesto
marcado, CND vencendo com licitação em curso, bloqueio operacional), nunca
"prejuízo de difícil reparação" genérico.
Honorários não são devidos em MS: não peça.
Anti-padrões: autoridade coatora errada; pedido pecuniário retroativo;
cumular MS com repetição; impetrar sem a prova documental inteira; esquecer o
tópico de tempestividade dos 120 dias; pedir honorários.
Se a liminar é negada, o caminho é agravo de instrumento imediato; se o mérito
é desfavorável, apelação. Sinalize isso no plano de execução entregue ao
humano.

=== EMBARGOS À EXECUÇÃO FISCAL (Lei 6.830/80 art. 16) ===
Dois pressupostos que o agente confere ANTES de escrever, porque nenhum dos
dois se conserta depois:
  1. GARANTIA DO JUÍZO formalizada — pagamento, penhora, depósito em dinheiro,
     fiança bancária ou seguro garantia. Embargos sem garantia não são
     recebidos. Se a garantia ainda não está formalizada, o entregável é
     BLOQUEADO até que esteja, e o caminho paralelo a avaliar é a exceção de
     pré-executividade.
  2. PRAZO de 30 dias contados da INTIMAÇÃO da penhora ou do depósito, não da
     citação. Demonstre em tópico próprio: data da garantia, data da
     intimação, data dos embargos.
Quando o vício é patente e dispensa dilação (prescrição consumada, CDA
visivelmente inábil), a exceção de pré-executividade resolve sem exigir
garantia — avalie esse caminho antes de mandar o cliente garantir o juízo.
Efeito suspensivo NÃO é automático: precisa de requerimento expresso, garantia
integral, probabilidade do direito e risco de dano grave demonstrado. Sem
pedido expresso, a execução segue com os embargos correndo ao lado.
Roteiro de defesa, na ordem em que se examina:
  NULIDADE DA CDA — falta de nome ou endereço do devedor, do valor originário
  discriminado, da forma de cálculo de juros e multa, do fundamento legal do
  tributo e da penalidade, da data e do número do processo administrativo. O
  vício formal admite substituição da CDA só até a decisão de primeira
  instância, e a peça deve dizer isso para fechar a porta depois.
  DECADÊNCIA — lançamento de ofício conta do primeiro dia do exercício
  seguinte àquele em que o lançamento poderia ter sido efetuado; lançamento
  por homologação com pagamento antecipado conta do fato gerador. Cinco anos.
  PRESCRIÇÃO — cinco anos da constituição definitiva; o despacho que ordena a
  citação interrompe, com retroação à propositura.
  PRESCRIÇÃO INTERCORRENTE — a executiva parada é o achado mais rentável e o
  mais esquecido. A suspensão de um ano corre automaticamente da ciência da
  não localização do devedor ou de bens; findo esse ano, começa a correr o
  quinquênio, também automaticamente, sem necessidade de despacho. Monte a
  LINHA DO TEMPO com data de cada ato útil e mostre o vazio.
  PAGAMENTO, PARCELAMENTO OU COMPENSAÇÃO — com o comprovante anexado.
  ILEGITIMIDADE PASSIVA DO SÓCIO — mero inadimplemento não redireciona; exige
  dolo, fraude, excesso de poderes ou dissolução irregular.
  MÉRITO TRIBUTÁRIO — tese de inconstitucionalidade, base de cálculo ou
  alíquota erradas, com o precedente verificado.
Confira o cálculo da Fazenda item a item (principal, multa, juros, encargo
legal) e apresente o valor que você entende correto com memória demonstrada.
Pedido subsidiário de redução do valor é obrigatório quando você impugna
parcela e não o todo.
Anti-padrões: embargar sem garantia; perder os 30 dias; não pedir efeito
suspensivo expressamente; confundir decadência com prescrição; deixar passar
a intercorrente; aceitar o cálculo da Fazenda sem conferir; alegar vício
formal da CDA tarde demais.

################ FRENTE 2 — EMPRESARIAL / SOCIETÁRIO ################

=== RECUPERAÇÃO JUDICIAL (Lei 11.101/2005 com a Lei 14.112/2020) ===
Requisitos do art. 48, conferidos com documento antes de qualquer redação:
mais de dois anos de exercício regular da atividade; não ser falido, ou ter
as responsabilidades declaradas extintas por sentença transitada; não ter
obtido outra recuperação judicial nos últimos cinco anos; não ter sócio
controlador ou administrador condenado por crime falimentar. Falha em
qualquer um deles: o pedido é indeferido de plano e o entregável é BLOQUEADO,
não "ajustado".
O que NÃO se sujeita à recuperação, e por isso precisa de negociação separada
desde o primeiro dia: crédito com garantia de alienação fiduciária,
arrendamento mercantil, reserva de domínio e cessão fiduciária de recebíveis
(a trava bancária); adiantamento de contrato de câmbio; créditos tributários,
que seguem em cobrança própria com a via da transação e do parcelamento aberta
pela Lei 14.112/2020; FGTS. Empresa que entra em recuperação sem ter mapeado a
trava bancária descobre no meio do stay que o caixa continua sendo drenado.
Stay period: 180 dias de suspensão das execuções e da constrição patrimonial,
prorrogável uma única vez por igual período, desde que o devedor não tenha dado
causa à demora. Peça a prorrogação ANTES do vencimento, nunca depois.
Documentação do art. 51 é o gargalo real: demonstrações contábeis dos três
últimos exercícios e do período em curso; relação nominal completa de credores
com CPF/CNPJ, valor, natureza e classificação; relação de empregados; relação
de bens dos administradores e sócios controladores; extratos bancários;
certidões de protesto; relação das ações judiciais. Inicial sem esses
documentos é indeferida sumariamente.
Classes de credores: I trabalhista e por acidente do trabalho, com o excedente
a 150 salários mínimos por credor migrando para quirografário; II garantia
real, limitada ao valor do bem gravado; III quirografários; IV microempresa e
empresa de pequeno porte. Classificar credor na classe errada compromete a
assembleia inteira — a lista é conferida credor a credor, não por amostragem.
Plano: 60 dias do deferimento do processamento, prazo improrrogável. Plano
apresentado sem viabilidade real não compra tempo, apenas adia a convolação em
falência.
Assembleia e cram down: a aprovação ordinária exige, na classe trabalhista, a
maioria simples dos presentes por cabeça; nas classes de garantia real e
quirografária, maioria de créditos e de cabeças. Rejeitado o plano por uma
classe, o cram down do art. 58 permite ao juiz conceder a recuperação desde
que haja aprovação da maioria dos créditos presentes no conjunto das classes,
aprovação em pelo menos duas classes (ou, havendo apenas duas, em uma delas), e
voto favorável de mais de um terço na classe que rejeitou, sem tratamento
diferenciado entre credores da classe dissidente. O plano precisa ser desenhado
desde o começo com a aritmética do cram down em mente.
Financiamento durante a recuperação (DIP) tem prioridade de pagamento e é a
alavanca que atrai investidor — depende de autorização judicial específica.
Anti-padrões: pedir sem os documentos do art. 51; classificar credor errado;
não negociar com o credor fiduciário; esquecer o pedido de stay; confundir
recuperação judicial com extrajudicial.

=== CONTRATO SOCIAL — SOCIEDADE LIMITADA (CC 1.052-1.087) ===
O contrato social é o documento que decide como o litígio societário vai
terminar, escrito antes de o litígio existir. Produza pensando na saída, não
na constituição.
Qualificação: nome, nacionalidade, estado civil COM O REGIME DE BENS,
profissão, documento de identidade, CPF e endereço de cada sócio. O regime de
bens não é formalidade: define anuência do cônjuge e reflete na apuração de
haveres e no divórcio de sócio.
Capital e integralização — a cláusula mais subestimada. A responsabilidade do
sócio é limitada ao valor de suas quotas, MAS todos respondem solidariamente
pela integralização do capital enquanto ele não estiver integralizado. Diga no
contrato: valor total, número de quotas, valor nominal, tabela de distribuição
por sócio (quotas, valor e percentual, fechando 100% e batendo com o total),
forma de integralização (dinheiro com depósito comprovado; bens com avaliação e
responsabilidade solidária dos sócios pela exatidão do valor atribuído;
direitos creditórios com cessão formal) e prazo com cronograma de aportes.
Capital simbólico em empresa que vai buscar crédito bancário é erro comercial
com efeito jurídico.
Cessão de quotas (CC 1.057): na omissão do contrato, o sócio cede livremente a
outro sócio, e a terceiro só se não houver oposição de titulares de mais de um
quarto do capital. O contrato pode dispor diferente, e quase sempre deve:
direito de preferência com oferta por escrito, prazo de resposta e
consequência do silêncio explicitados. Contrato omisso aqui é como um estranho
entra na sociedade.
Retirada (CC 1.029) e apuração de haveres: notificação com sessenta dias de
antecedência em sociedade por prazo indeterminado. A cláusula precisa dizer a
DATA-BASE, o CRITÉRIO (balanço de determinação a valor de mercado, incluindo
intangíveis, é o padrão supletivo) e a FORMA DE PAGAMENTO. Contrato que cala
sobre critério entrega a definição ao perito e ao juiz.
Exclusão de sócio (CC 1.085): a exclusão extrajudicial por justa causa, com
deliberação da maioria do capital, só é possível se o contrato PREVIR
expressamente essa possibilidade; e exige reunião ou assembleia convocada
especialmente para isso, com ciência prévia ao acusado em tempo de comparecer
e se defender. Sem previsão contratual, a exclusão só se faz judicialmente.
Quóruns: alteração do contrato, incorporação, fusão, cisão e dissolução seguem
o quórum legal vigente; o contrato pode elevá-lo. Verifique a redação atual
antes de escrever o número, porque os quóruns da limitada foram alterados
legislativamente e citar o quórum revogado é erro que sobrevive dez anos no
arquivo da empresa.
Administração: quem administra, se isolada ou conjuntamente, poderes
expressos, VEDAÇÕES expressas (aval, fiança e endosso de favor; oneração de
imóvel; empréstimo acima de valor definido; contratação com parte relacionada)
e pró-labore. Administrador não sócio exige quórum próprio e aceitação.
Mecanismo de impasse é cláusula de contrato social em sociedade de dois sócios
com 50% cada. Sem ele, o empate paralisa a empresa e a única saída é a
dissolução judicial. Coloque buy-sell (shotgun), arbitragem ou voto de
qualidade definido, e diga qual é.
Falecimento de sócio: diga se a sociedade continua com os herdeiros, se paga
haveres ou se liquida a quota, e por qual critério. Silêncio aqui coloca
herdeiro sem vocação empresarial dentro da sociedade.
Cláusulas estratégicas a avaliar sempre, e a justificar por escrito quando
omitidas: não concorrência pós-saída (prazo e território razoáveis e
definidos), tag along, drag along, vesting, opção de compra e venda,
arbitragem.
Anti-padrões: CNAE impreciso; ausência de cláusula de retirada e de critério
de haveres; exclusão sem previsão contratual; esquecer anuência do cônjuge
quando o regime exige; não definir foro; confundir limitada com sociedade
limitada unipessoal ou com empresário individual.

=== ACORDO DE ACIONISTAS E DE QUOTISTAS (LSA art. 118) ===
O acordo vincula as partes desde a assinatura; para ser OPONÍVEL à companhia e
a terceiros precisa ser arquivado na sede (limitada) ou averbado nos livros de
registro (sociedade anônima), e as obrigações de voto e de preferência
comportam execução específica. Acordo não averbado é contrato entre sócios com
eficácia meramente obrigacional — perde exatamente quando é mais necessário. O
plano de averbação faz parte do entregável.
Conflito entre acordo e contrato social: prevalece o contrato social. Detectado
o conflito, a entrega inclui a alteração contratual correspondente, não apenas
o acordo.
Cláusulas críticas, e o que torna cada uma inútil se mal escrita:
  BLOCO DE CONTROLE E VOTO EM CONJUNTO — defina como o bloco decide
  internamente (maioria simples, com quórum qualificado para matérias
  listadas), quando se reúne e o que acontece com quem vota em desacordo. Sem
  a reunião prévia definida, o bloco não funciona.
  PREFERÊNCIA — oferta por escrito nas mesmas condições da proposta de
  terceiro, prazo certo de resposta, e efeito do silêncio. O alienante fica
  livre para vender ao terceiro nas EXATAS condições ofertadas, nunca em
  condições melhores para o comprador.
  TAG ALONG — diga a COBERTURA em número (100% para proteção integral do
  minoritário, ou percentual definido). Tag along sem cobertura declarada não
  protege ninguém.
  DRAG ALONG — diga o quórum que dispara o arrasto e o PREÇO MÍNIMO por
  quota ou ação. Drag along sem preço mínimo é autorização para o majoritário
  vender a companhia levando o minoritário por qualquer valor.
  VESTING — cliff (saída antes dele perde tudo), cronograma de aquisição,
  aceleração por evento de liquidez (single trigger) ou por desligamento sem
  causa seguido de venda em janela definida (double trigger), e as regras de
  bad leaver e good leaver com o critério de preço de recompra em cada
  hipótese. Vesting sem cliff entrega participação a quem saiu no primeiro mês.
  LOCK-UP — prazo e exceções expressas.
  IMPASSE — buy-sell shotgun (quem oferece o preço tem de estar disposto tanto
  a comprar quanto a vender por ele, o que força preço honesto), mediação com
  prazo definido, e arbitragem como saída final.
  ARBITRAGEM — câmara nomeada, número de árbitros, sede, idioma e lei
  aplicável. Cláusula arbitral vaga é cláusula patológica e devolve as partes
  ao Judiciário depois de meses.
  NÃO CONCORRÊNCIA E NÃO ALICIAMENTO — prazo, território e atividade
  definidos e proporcionais. Restrição desproporcional é reduzida ou anulada.
  ADESÃO DE NOVOS SÓCIOS — sem ela, cada rodada de investimento quebra o
  acordo.
Anexos que fazem parte do entregável: cap table, cronograma de vesting e
modelos de notificação para preferência, tag e drag.
Anti-padrões: tag along parcial vendido como proteção; drag along sem piso;
vesting sem cliff; não averbar; não concorrência abusiva; acordo em conflito
com o contrato social; esquecer a anuência do cônjuge conforme o regime.

################ FRENTE 3 — IMOBILIÁRIO / CONSUMIDOR ################

=== AÇÃO DE DESPEJO (Lei 8.245/91) ===
A primeira decisão é a HIPÓTESE, porque ela define notificação prévia, liminar
e prova:
  falta de pagamento de aluguel e encargos, a qualquer tempo;
  infração legal ou contratual;
  reparações urgentes determinadas pelo Poder Público que o locatário recusa;
  término do prazo na locação residencial ajustada por escrito por trinta meses
  ou mais, com denúncia ao final;
  denúncia vazia na residencial prorrogada por prazo indeterminado, que exige
  notificação prévia de trinta dias e não exige motivo;
  retomada motivada (uso próprio, de cônjuge, ascendente ou descendente;
  demolição ou reforma que aumente a área; extinção do contrato de trabalho na
  locação vinculada ao emprego);
  não residencial findo o prazo, com denúncia livre, ressalvada a renovatória.
Foro: o do imóvel, salvo eleição válida.
Liminar do art. 59 §1º: concedida em quinze dias para desocupação, mediante
CAUÇÃO do locador equivalente a três meses de aluguel, nas hipóteses legais
(entre elas o inadimplemento em locação sem qualquer garantia, o término da
locação não residencial e as reparações urgentes). Não confunda essa caução do
locador com a caução prestada pelo locatário como garantia locatícia. Pedido
de liminar traz o cálculo da caução com memória.
Purgação da mora (art. 62): o locatário pode, em quinze dias da citação,
depositar o débito integral — aluguéis e acessórios vencidos, multas, juros,
custas e honorários — e evitar o despejo. O direito é limitado a uma vez a
cada vinte e quatro meses. Cliente locador precisa saber disso antes de
ajuizar; cliente locatário precisa saber que essa é a janela, e que ela é a
única.
Fiador: cite-o no polo passivo. A responsabilidade do fiador se estende até a
efetiva devolução das chaves quando o contrato prorrogado assim previr, e a
exoneração exige o procedimento legal com o prazo de cento e vinte dias.
Esquecer o fiador é abrir mão do único patrimônio solvente do caso.
Pedidos que não podem faltar: aluguéis e encargos vencidos com memória de
cálculo (aluguel, IPTU, condomínio, multa contratual, juros e correção pelo
índice do contrato), aluguéis VINCENDOS até a efetiva entrega das chaves, e as
despesas de vistoria e reparos.
Anti-padrões: denúncia vazia sem a notificação prévia; esquecer o fiador; não
pedir os vincendos; confundir as duas cauções; não pedir liminar quando cabe;
ajuizar no foro errado.

=== AÇÃO RENOVATÓRIA DE LOCAÇÃO NÃO RESIDENCIAL (Lei 8.245/91 art. 51) ===
O PRAZO É O CASO. A renovatória tem de ser ajuizada no intervalo entre doze e
seis meses antes do término do contrato em vigor. O prazo é DECADENCIAL e
fatal: perdido, não há prorrogação, não há relevação, não há reabertura. Com
ele se perde o ponto comercial, porque o locador passa a poder retomar o imóvel
pela via da denúncia. Antes de qualquer análise de mérito, calcule a janela a
partir da data de término do contrato e diga em qual dia ela abre e em qual dia
ela fecha. Se a janela já fechou, o entregável não é a renovatória: é o plano
alternativo (negociação da renovação, discussão do valor em via ordinária,
plano de mudança de estabelecimento), entregue com a perda do direito
declarada com todas as letras.
Requisitos cumulativos: contrato a renovar por escrito e com prazo
determinado; prazo mínimo de cinco anos, admitida a soma dos contratos escritos
sucessivos (acessão de contratos); exploração do MESMO ramo pelo prazo mínimo
e ininterrupto de três anos. Cada um se prova com documento: a cadeia completa
de contratos, alvará de funcionamento datado, inscrições fiscais, notas
fiscais ao longo do período.
Sucessão e cessão: o direito à renovação acompanha o fundo de comércio
regularmente adquirido, somando-se o tempo do antecessor, desde que mantido o
mesmo ramo. Em sublocação total, o sublocatário tem legitimidade.
Condições propostas: prazo, aluguel a valor de mercado com avaliação anexa,
encargos e garantia. Proposta abaixo do mercado é a porta de entrada da defesa
de insuficiência de proposta — peça sempre, subsidiariamente, a fixação do
aluguel por perícia.
Defesas do locador que você precisa antecipar: insuficiência da proposta;
proposta melhor de terceiro, que exige prova robusta e faz nascer o direito de
preferência do locatário em igualdade de condições; retomada para uso próprio,
de cônjuge, ascendente ou descendente, ou para obras determinadas pelo Poder
Público ou que aumentem o valor do imóvel. Nas hipóteses de retomada, o
locatário tem direito à indenização pelos prejuízos (mudança, perda do lugar e
da clientela, investimentos não amortizados) — o pedido indenizatório
subsidiário faz parte da estratégia desde a inicial.
Shopping center tem regime próprio e admite cláusulas atípicas: leia o contrato
antes de aplicar o padrão.
Anti-padrões: perder a janela; não juntar a cadeia de contratos que soma cinco
anos; não provar o mesmo ramo por três anos; aceitar o valor do locador sem
perícia subsidiária; tratar locação residencial como se coubesse renovatória.

=== USUCAPIÃO — EXTRAJUDICIAL (CPC 216-A) X JUDICIAL ===
Modalidades e prazos, que definem toda a prova:
  extraordinária: quinze anos de posse contínua, mansa e pacífica com animus
  domini, sem justo título nem boa-fé; reduz a dez anos se houver moradia
  habitual ou obras de caráter produtivo;
  ordinária: dez anos com justo título e boa-fé; reduz a cinco anos quando o
  imóvel foi adquirido onerosamente com registro depois cancelado, havendo
  moradia ou investimento de interesse social e econômico;
  especial urbana: cinco anos, área urbana de até 250 m², moradia própria ou da
  família, sem outro imóvel, e o benefício se concede uma única vez;
  especial rural: cinco anos, área de até cinquenta hectares, tornada produtiva
  pelo trabalho próprio ou da família, com moradia, sem outro imóvel rural ou
  urbano;
  familiar: dois anos, imóvel urbano de até 250 m² dividido com ex-cônjuge ou
  ex-companheiro que abandonou o lar, sem outro imóvel.
Soma de posses (accessio possessionis): o sucessor soma o tempo do antecessor
desde que a posse tenha as mesmas qualidades. É o que salva a maioria dos casos
que parecem faltar tempo — verifique sempre antes de descartar a modalidade.
Bem público não se usucape, em nenhuma modalidade. Detectado imóvel público ou
faixa pública na área, o caminho é regularização fundiária, e o entregável diz
isso, sem tentar contornar.
Via EXTRAJUDICIAL, no registro de imóveis, quando os confrontantes e o titular
registral anuem. Insumos: ata notarial lavrada por tabelião atestando o tempo e
as características da posse; planta e memorial descritivo assinados por
profissional habilitado com ART ou RRT, pelo requerente e pelos confrontantes;
certidões negativas dos distribuidores; comprovação de IPTU ou ITR; justo
título quando a modalidade exigir; procuração com poderes específicos. A
anuência é o ponto de decisão: o silêncio do titular ou do confrontante
notificado é interpretado como CONCORDÂNCIA na sistemática atual, mas a
IMPUGNAÇÃO de qualquer interessado ou do ente público encerra a via
extrajudicial e remete a questão ao juízo competente. Por isso o entregável
extrajudicial já vem com o plano subsidiário judicial pronto.
Nada disso é produzido pela plataforma: ata notarial, planta com ART e
certidões são atos de terceiros. O agente entrega o requerimento redigido e o
PLANO DE OBTENÇÃO desses documentos, com responsável e sequência, como
pendência do humano.
Via JUDICIAL quando há impugnação, confrontante ou titular incerto, litígio de
divisas, ou parcela pública envolvida. Polo passivo e intimações que, se
faltarem, geram nulidade: titular do domínio registrado e cônjuge; herdeiros
conhecidos; confrontantes identificados e seus cônjuges; confrontantes e
interessados incertos por edital; Fazendas Pública federal, estadual e
municipal, intimadas obrigatoriamente; Ministério Público quando houver
interesse que justifique a intervenção.
Prova em juízo: robustez documental decide. IPTU ou ITR de toda a série;
faturas de serviços públicos no nome do possuidor; notas de obras e
benfeitorias; recibos do antecessor quando houver soma de posses; documentos
pessoais com o endereço do imóvel ao longo do tempo; fotografias datadas; e
testemunhas antigas, no mínimo três, de preferência vizinhos e antecessores.
Perícia com planta, memorial e ART é insumo, não detalhe.
Anti-padrões: pleitear especial urbana para quem tem outro imóvel; esquecer as
Fazendas; deixar confrontante fora da citação; não somar a posse do antecessor;
juntar planta sem ART; tentar usucapir bem público.

=== PRÁTICA ABUSIVA NO CDC (arts. 39 e 51) ===
Distinga antes de escolher a peça: vício do produto ou do serviço é defeito de
qualidade ou quantidade de um fornecimento específico; PRÁTICA ABUSIVA é
conduta do fornecedor no mercado, e CLÁUSULA ABUSIVA é o texto do contrato. A
ação por prática abusiva serve quando o problema é sistemático ou contratual,
não quando é um produto com defeito.
Práticas abusivas do art. 39: venda casada; recusa de atendimento a demanda
dentro do estoque; envio de produto ou serviço sem solicitação prévia, que
equivale a amostra grátis e não pode ser cobrado; prevalecer-se da fraqueza ou
ignorância do consumidor em razão de idade, saúde, conhecimento ou condição
social; exigir vantagem manifestamente excessiva; executar serviço sem
orçamento prévio e autorização expressa; repassar informação depreciativa;
colocar no mercado produto em desacordo com as normas técnicas; recusar a venda
a quem se dispõe a pagar à vista; elevar preço sem justa causa; aplicar índice
de reajuste diverso do legal ou contratual; deixar de estipular prazo para
cumprimento da obrigação.
Cláusulas abusivas do art. 51 são NULAS DE PLENO DIREITO. Isso muda a
estratégia: a nulidade é matéria de ordem pública, pode ser reconhecida de
ofício, não convalesce pelo tempo nem pela assinatura do consumidor, e a
nulidade da cláusula não invalida o contrato quando dele puder ser separada. O
pedido é DECLARATÓRIO de nulidade, e ele vem antes do pedido condenatório na
ordem dos pedidos.
Publicidade enganosa ou abusiva: o ônus de provar a veracidade e a correção da
informação publicitária é do fornecedor, por disposição legal expressa —
invoque isso, é mais forte do que a inversão do ônus por hipossuficiência, que
depende de decisão judicial.
Foro: domicílio do consumidor, por opção dele.
Tutela INIBITÓRIA quando a prática se repete ou ameaça repetir-se: ela não
repara, impede. Astreinte com valor, periodicidade e teto definidos é o
instrumento; sem teto, a multa vira discussão autônoma depois.
Repetição do indébito em dobro exige cobrança indevida e ausência de engano
justificável; peça-a com a memória de cálculo do que foi cobrado.
Dano moral: fundamente o abalo concreto e sinalize o valor uma única vez. O
quantum é decisão do titular.
Anti-padrões: confundir vício com prática abusiva; deixar de pedir a
declaração de nulidade quando há cláusula; esquecer a inibitória em prática
repetida; pedir dano moral genérico; ajuizar no foro do fornecedor; pedir
astreinte sem parâmetros.

=== SAÍDA PADRÃO DE TODA PRODUÇÃO ===
Toda entrega desta base contém, além do documento redigido:
  1. o enquadramento escolhido, com a alternativa descartada e o motivo;
  2. o cálculo do prazo fatal aplicável, passo a passo, com a fonte da data;
  3. a memória de cálculo de todo valor citado;
  4. a lista de documentos anexados e a lista de documentos PENDENTES, com
     responsável e o que cada um prova;
  5. o status das citações (verificada ou removida — nunca "a conferir");
  6. o plano subsidiário para a hipótese mais provável de resistência;
  7. o checklist de protocolo para o humano;
  8. a frase de status: "pronta para protocolo humano, com checklist" ou
     "BLOQUEADA, com N pendências".
"""

PRODUCAO_TRIBUTARIO_EMPRESARIAL_IMOBILIARIO_SOURCES = [
    "Dossiê proprietário 41 — Mandado de segurança tributário (Lei 12.016/2009)",
    "Dossiê proprietário 42 — Embargos à execução fiscal (Lei 6.830/80)",
    "Dossiê proprietário 43 — Recuperação judicial (Lei 11.101/2005 + 14.112/2020)",
    "Dossiê proprietário 44 — Contrato social de sociedade limitada (CC 1.052-1.087)",
    "Dossiê proprietário 45 — Acordo de acionistas e quotistas (LSA art. 118)",
    "Dossiê proprietário 46 — Ação por prática abusiva no CDC (arts. 39 e 51)",
    "Dossiê proprietário 47 — Ação de despejo (Lei 8.245/91)",
    "Dossiê proprietário 48 — Ação renovatória de locação não residencial (art. 51)",
    "Dossiê proprietário 49 — Usucapião extrajudicial (CPC 216-A; Prov. CNJ 65/2017)",
    "Dossiê proprietário 50 — Usucapião judicial (CC 1.238-1.244; CF 183 e 191)",
    "Regra da casa: nunca entregar peça final com placeholder (11/06/2026)",
    "Regra da casa: protocolo é exclusivamente humano",
    "Regra da casa: números de tese, quotas e quantum são decisão do titular",
    "Regras de redação do titular (12/06/2026): linguagem direta, sem caixa alta, "
    "negrito mínimo, gratuidade primeiro e tutela por último, seção nunca "
    "termina em citação",
    "Plataforma — app/services/citation_verification_service.py (veredito de citação)",
]
