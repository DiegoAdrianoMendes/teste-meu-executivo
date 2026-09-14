"""Base de conhecimento de PRODUÇÃO de peças trabalhistas e previdenciárias.

Extraída dos dossiês proprietários do titular: reclamação trabalhista inicial
(pedido líquido pós-Reforma), defesa do empregador na Justiça do Trabalho,
cálculo de verbas rescisórias/TRCT, aposentadoria por tempo de contribuição
pós-EC 103/2019, BPC-LOAS e benefício por incapacidade (concessão,
restabelecimento e recurso).

TRADUÇÃO PARA A PLATAFORMA (os dossiês nasceram para um agente de desktop com
Chrome, Bash e Python local; nada disso existe no servidor):

  * "calcular via Python" -> NÃO há execução de código. O que sobrevive é a
    regra SUBSTANTIVA: todo valor sai com memória de cálculo demonstrada e
    auditável (base, alíquota/divisor, período, resultado), nunca número solto.
    Se a conta não pode ser demonstrada linha a linha, o número não é afirmado:
    vira pendência de apuração dirigida ao humano.
  * "validar jurisprudência via Chrome/Jusbrasil" ->
    `app/services/citation_verification_service.py`. Citação sem veredito de
    verificação não entra como verificada; o default é NÃO VERIFICADA.
  * "salvar memória em /tmp/*.csv", "gerar GRRF/DARF/GPS", "eSocial S-2299",
    "consultar CNIS/DataJud", "vault Obsidian", "navegador" -> não existem na
    plataforma. Omitidos, sem substituto inventado: quando o dossiê dependia
    deles, a instrução vira pedido explícito de documento ao humano.

OMITIDO por não ter correspondente real (declarado, não disfarçado): execução
de Python e geração de planilha; emissão de guias (GRRF, GPS, DARF) e eSocial;
consulta automatizada a CNIS, CadÚnico, extrato do FGTS, DataJud ou portais do
INSS; automação de navegador; leitura de vault de notas. Valores de salário
mínimo, teto do INSS, tabelas de pontos e índices de correção NÃO são fixados
aqui: são dados datados, devem vir do caso ou de fonte conferida no momento da
produção — a base ensina a estrutura do cálculo, não decora números que
envelhecem.
"""

PRODUCAO_TRABALHISTA_PREVIDENCIARIO_KNOWLEDGE_VERSION = (
    "producao-trabalhista-previdenciario-2026-07"
)

# Motivos de rescisão reconhecidos pela base. Fonte única para o prompt do
# agente e para testes de contrato: a tabela verba x motivo não pode perder um
# motivo em silêncio (é onde nascem os erros de multa de FGTS).
MOTIVOS_RESCISAO: tuple[str, ...] = (
    "dispensa sem justa causa",
    "dispensa por justa causa",
    "pedido de demissão",
    "acordo CLT 484-A",
    "término de contrato por prazo determinado",
    "aposentadoria",
    "falecimento do empregado",
    "rescisão indireta (CLT 483)",
)

# Regras de acesso à aposentadoria pós-EC 103/2019 que o agente é obrigado a
# comparar antes de recomendar qualquer data.
REGRAS_APOSENTADORIA: tuple[str, ...] = (
    "direito adquirido até 12/11/2019",
    "transição por pedágio de 50% (EC 103, art. 17)",
    "transição por pedágio de 100% (EC 103, art. 20)",
    "transição por pontos (EC 103, art. 15)",
    "transição por idade progressiva (EC 103, art. 16)",
    "regra permanente (EC 103, art. 19)",
)

PRODUCAO_TRABALHISTA_PREVIDENCIARIO_KNOWLEDGE = """
=== PRINCÍPIO MESTRE ===
Nestas duas matérias a peça é ganha ou perdida na conta e no documento, não no
adjetivo. Trabalhista: pedido sem valor é pedido inepto. Previdenciário:
benefício sem prova de requisito é improcedência com sucumbência. Antes de
escrever qualquer tese, saiba de onde sai cada número e qual documento o
sustenta.

=== LIMITE ABSOLUTO — PROTOCOLO É EXCLUSIVAMENTE HUMANO ===
Você nunca protocola, nunca requer no INSS, nunca peticiona em PJe ou JEF,
nunca marca a peça como protocolada e nunca sugere que o protocolo ocorreu.
Sua entrega termina em "pronta para conferência e protocolo humano, com
checklist" ou em "BLOQUEADA, com a lista do que falta".

=== REGRA DO NÚMERO — MEMÓRIA DE CÁLCULO OU NADA ===
Não existe execução de código nesta plataforma. Todo valor entregue sai com a
memória de cálculo escrita e auditável: base de incidência, divisor ou
alíquota, período (dias, avos, meses), e resultado. Quem lê tem que refazer a
conta a mão e chegar ao mesmo número.
Proibido: valor arredondado sem origem; "aproximadamente"; total sem as
parcelas; alíquota aplicada sem dizer sobre o quê incidiu; correção monetária
sem nomear o índice e o marco inicial.
Se falta insumo (contracheques, cartões de ponto, extrato do FGTS, CNIS, CCT
vigente, valor do salário mínimo do ano), o número NÃO é estimado: a verba
entra descrita com a fórmula e a pendência do documento é listada no topo. Peça
com número inventado é pior do que peça com pendência declarada.

=== REGRA CRÍTICA — NUNCA ENTREGAR COM PLACEHOLDER ===
Peça final não contém marcador de preenchimento nem campo vazio (PREENCHER,
XXX, ____, [...], colchete de campo). Antes de declarar pronta, varra o
documento inteiro, inclusive tabelas, cabeçalho e rodapé. Dado faltando:
procure ativamente nas demais peças, documentos e evidências do MESMO caso na
plataforma. Se realmente não existir, REMOVA o campo (parte já qualificada nos
autos dispensa requalificação). Se o dado for indispensável e inexistente, o
entregável não é final: entregue BLOQUEADO com a pendência no topo.

=== REGRA CRÍTICA — CITAÇÃO SÓ VALE VERIFICADA ===
Nenhuma súmula do TST, tema do STF/STJ, precedente do TNU ou dispositivo entra
como verificado sem veredito de `citation_verification_service`. O default é
NÃO VERIFICADA. Citação sem veredito é achado crítico: ou sai da peça, ou o
trecho passa a se sustentar em lei seca e no documento do caso.

=== ESTILO E ORDEM ESTRUTURAL ===
Português jurídico direto, frase curta, uma ideia por frase, voz ativa.
Juridiquês ornamental é pontual, nunca padrão. Sem caixa alta para ênfase.
Negrito só na tese central da seção, no dado decisivo (data, valor, prazo) e no
pedido principal.
Gratuidade de justiça é o primeiro tópico depois dos fatos. Tutela de urgência
é o último tópico antes dos pedidos.
Nenhuma seção termina em citação, ementa ou dispositivo: a última frase do
tópico é do advogado, aplicando a citação ao caso.

########################  PARTE I — TRABALHISTA  ########################

=== PEDIDO LÍQUIDO É REQUISITO DE ADMISSIBILIDADE (CLT 840, § 1º) ===
Depois da Lei 13.467/2017, o pedido tem que ser certo, determinado e com
indicação de valor. Pedido genérico ("horas extras a apurar em liquidação")
convida extinção sem mérito daquele item.
Como se produz na prática: cada alínea do rol de pedidos leva um valor com sua
memória de cálculo logo abaixo ou em tabela anexa referenciada. O valor da
causa é a SOMA dos pedidos líquidos — se a soma não bate com o valor da causa,
a peça está errada e a contraparte vai dizer isso na defesa.
Ressalva honesta ao cliente: o valor indicado delimita o proveito econômico e
serve de base para sucumbência recíproca; superestimar pedido custa dinheiro do
próprio reclamante.

=== PRESCRIÇÃO (CF 7º, XXIX) — DUAS CONTAS, NÃO UMA ===
Bienal: extinto o contrato, há 2 anos para ajuizar. Perdido esse prazo, morre a
pretensão inteira.
Quinquenal: ajuizada a ação, só se pede o que venceu nos 5 anos anteriores à
data do ajuizamento.
Produção: antes de listar pedidos, fixe as duas datas-limite e corte o que está
fora. Do lado do reclamante, pedir período prescrito é entregar improcedência
parcial e sucumbência. Do lado do empregador, a prescrição quinquenal é
preliminar de mérito que se alega SEMPRE e QUANTIFICADA — dizer "alega-se a
prescrição" sem apontar quais verbas e quanto some da conta é desperdiçar a
tese. FGTS segue prazo próprio, mais longo, e não se confunde com a quinquenal
das verbas salariais.

=== MÉDIA DE VARIÁVEIS ===
Horas extras habituais, comissões, gorjetas, prêmios habituais, adicionais
pagos com habitualidade integram a remuneração. A média dos últimos 12 meses
(ou do período trabalhado, se menor) entra na base de cálculo de aviso, 13º,
férias + 1/3, FGTS e verbas rescisórias.
Produção: monte a tabela mês a mês com origem em contracheque, some, divida por
12 e mostre a divisão. Média afirmada sem os 12 meses listados é número solto e
não entra.

=== REFLEXOS — A CONTA QUE OS AGENTES ESQUECEM ===
Verba salarial paga com habitualidade repercute. O encadeamento correto é:
1. Apura-se a verba principal (ex.: horas extras com o adicional devido).
2. Sobre ela incide o reflexo em DSR (repouso semanal remunerado) — e o DSR só
   entra uma vez, pela proporção de dias de repouso do mês.
3. A verba principal MAIS o DSR formam a nova base que reflete em 13º salário,
   em férias acrescidas de 1/3 e no FGTS (8% de depósito).
4. Havendo dispensa sem justa causa, o FGTS assim apurado ainda gera a multa
   rescisória.
Erro clássico e caro: refletir o DSR e depois refletir de novo o DSR dentro do
13º e das férias (bis in idem), ou esquecer o FGTS sobre os reflexos. Escreva a
cadeia na memória de cálculo para que o juiz veja que não houve duplicidade.

=== MULTAS DO ART. 477, § 8º, E DO ART. 467 ===
477, § 8º: rescisão paga fora do prazo legal gera multa equivalente a um
salário do empregado, além da correção. Depende de atraso comprovado — compare
a data do TRCT com a data do efetivo pagamento (comprovante bancário). Sem
prova do atraso, o pedido morre.
467: incide sobre a parcela RESCISÓRIA INCONTROVERSA não paga até a primeira
audiência, no percentual de 50%. Não incide sobre verba controvertida nem sobre
pedido de horas extras. Do lado do empregador, a defesa contra o art. 467 é
operacional: pagar ou depositar o incontroverso antes da audiência mata a
multa. Do lado do reclamante, pedir 467 sobre verba controvertida é pedido
natimorto.

=== EQUIPARAÇÃO SALARIAL (CLT 461) ===
Requisitos cumulativos, todos provados pelo reclamante: identidade de função e
não de cargo formal; mesmo empregador; trabalho de igual valor (mesma
produtividade e mesma perfeição técnica); diferença de tempo no exercício da
função não superior a 2 anos; diferença de tempo de serviço para o mesmo
empregador não superior a 4 anos.
Pós-Reforma, o âmbito é o MESMO ESTABELECIMENTO, não mais a mesma localidade —
é a primeira coisa que se checa, e a primeira defesa do empregador. Não cabe
equiparação quando há quadro de carreira ou plano de cargos com promoções por
critério objetivo.
Produção: nomeie o paradigma, indique período de convivência funcional, e
calcule a diferença mês a mês com reflexos. Paradigma indicado sem prova de que
fazia a mesma coisa, no mesmo estabelecimento, no mesmo período, é pedido
perdido — e do lado do empregador é onde a defesa é mais fácil de ganhar.

=== DANO MORAL TRABALHISTA E O TEMA 1.121 DO STF ===
A Reforma inseriu na CLT (art. 223-G) faixas de indenização atreladas a
múltiplos do salário do ofendido. O STF, no Tema 1.121, afastou a leitura da
tarifação como teto vinculante: os parâmetros legais orientam, mas não
aprisionam o juiz, que pode fixar valor superior de forma fundamentada diante
da gravidade concreta.
Produção: não peça valor colado na faixa legal como se fosse tabela, e não peça
número redondo sem narrativa. Descreva a conduta, a repetição, o dano e as
testemunhas que presenciaram; depois proponha o valor e explique por que a
faixa legal é insuficiente naquele caso. Do lado do empregador: atacar a
existência do fato e a ausência de prova testemunhal específica rende mais do
que discutir quantum.

=== PEJOTIZAÇÃO E O TEMA 725 DO STF ===
O Tema 725 firmou a licitude da terceirização de qualquer atividade da empresa,
inclusive a atividade-fim. Isso significa que o simples fato de o prestador
atuar no núcleo do negócio NÃO gera vínculo — argumentar assim é perder a
causa.
O que gera vínculo continua sendo o art. 3º da CLT: pessoalidade (não podia se
fazer substituir), habitualidade, onerosidade e sobretudo SUBORDINAÇÃO
(controle de jornada, ordens diretas, metas fiscalizadas, integração à
hierarquia, uso de sistemas internos como empregado).
Produção do lado do reclamante: a peça é um dossiê de prova de subordinação —
mensagens de comando, escalas, e-mails corporativos, sistema de ponto,
punições, quem substituía nas férias. Sem isso, o pedido é temerário.
Produção do lado do empregador: contrato de prestação, notas fiscais,
autonomia de horário, pluralidade de clientes do prestador, ausência de
exclusividade, faturamento por entrega.

=== DEFESA DO EMPREGADOR — IMPUGNAÇÃO ESPECÍFICA ===
Fato não impugnado especificamente é fato presumido verdadeiro. A defesa
percorre a inicial parágrafo a parágrafo: confessa o que é verdade (e ganha
credibilidade), nega com o documento que prova a negativa, ou contextualiza.
Negativa geral ("impugnam-se todos os fatos") não impugna nada.
Estrutura de produção: preliminares primeiro; depois impugnação fato a fato na
ordem da inicial; depois mérito pedido a pedido, na mesma numeração usada pelo
reclamante — assim o juiz consegue julgar lendo lado a lado. Prescrição
quinquenal arguida e quantificada. Ao final, análise de risco por pedido
(probabilidade de procedência e faixa de condenação), que é o que o cliente
empresa realmente usa para decidir acordo.

=== PASTA FUNCIONAL — A DEFESA É DOCUMENTAL ===
A prova do empregador é concentrada e tem que vir com a defesa: contrato de
trabalho e aditivos; contracheques de todo o período não prescrito; cartões de
ponto; TRCT e comprovante de pagamento; extrato do FGTS; ASOs admissional,
periódicos e demissional; laudos ambientais (LTCAT/PPRA/PGR) quando se discute
insalubridade ou periculosidade; CCT/ACT de cada ano invocado; advertências e
suspensões; política interna assinada.
Documento juntado depois da defesa é documento precluso, salvo hipótese legal
de documento novo. Ausência de controle de jornada, quando a empresa é obrigada
a mantê-lo, inverte o ônus e faz presumir verdadeira a jornada alegada na
inicial — a peça do reclamante deve dizer isso expressamente.

=== SÚMULA 338 DO TST — CARTÃO BRITÂNICO ===
Controle de ponto com horários uniformes ("britânico"), sem variação real de
minutos, não serve como prova de jornada: é imprestável e o ônus volta ao
empregador.
Produção do lado do reclamante: examine os cartões juntados e, se forem
uniformes, dedique um tópico a isso — é a via mais barata de ganhar horas
extras. Do lado do empregador: se o ponto da empresa é britânico, não construa
a defesa em cima dele; sustente a jornada por outros meios (escala, testemunha,
registro de acesso) e considere o risco no cálculo de acordo.

=== SÚMULA 80 DO TST E O TEMA 555 DO STF — EPI ===
Regra geral: o fornecimento de EPI aprovado que efetivamente NEUTRALIZE o
agente nocivo elimina o direito ao adicional de insalubridade. Neutralizar é
diferente de apenas reduzir.
Exceção decisiva: para RUÍDO, o Tema 555 do STF assentou que a declaração de
eficácia do EPI no formulário não afasta o direito, porque o protetor auricular
não elimina os efeitos da exposição.
Produção do lado do empregador: não basta juntar a nota fiscal do EPI —
demonstre a entrega mediante recibo assinado, a troca periódica, o treinamento
e a fiscalização de uso. Do lado do reclamante: ataque exatamente esses quatro
pontos, e separe o agente ruído dos demais.

=== CARTA DE PREPOSIÇÃO ===
A empresa se faz representar na audiência por preposto. Cancelada a antiga
exigência de que fosse empregado, o requisito que sobrou é substantivo: o
preposto tem que CONHECER OS FATOS, porque suas declarações obrigam a
reclamada. Carta de preposição assinada por quem tem poderes, com procuração
nos autos, vai junto com a defesa.
Produção: a carta é entregue com o pacote e o preposto recebe roteiro de
audiência com os pontos que a defesa confessou e os que negou — preposto que
contradiz a própria defesa entrega confissão.

=== VERBAS RESCISÓRIAS — TABELA POR MOTIVO ===
Sem justa causa: saldo de salário; aviso prévio (indenizado ou trabalhado);
férias vencidas + 1/3; férias proporcionais + 1/3; 13º proporcional; saque
integral do FGTS; multa rescisória de 40% do FGTS; habilitação ao
seguro-desemprego.
Justa causa: saldo de salário e férias vencidas + 1/3 apenas. Sem aviso, sem
proporcionais de férias, sem 13º proporcional, sem saque, sem multa.
Pedido de demissão: saldo; férias vencidas + 1/3; férias proporcionais + 1/3;
13º proporcional. O empregado deve o aviso ao empregador (descontável se não
cumprido). Sem saque, sem multa, sem seguro-desemprego.
Acordo do art. 484-A: metade do aviso prévio indenizado; metade da multa
rescisória — ou seja, 20% do FGTS, NÃO 40%; saque limitado a 80% do saldo; sem
seguro-desemprego; demais verbas integrais. Aplicar 40% em acordo é o erro mais
comum e mais caro desta matéria: confira sempre.
Contrato por prazo determinado que chega ao termo: saldo, férias proporcionais
+ 1/3, 13º proporcional, saque; sem aviso e sem multa.
Aposentadoria espontânea e falecimento: verbas proporcionais devidas, sem aviso
e sem multa; no falecimento o crédito vai aos dependentes habilitados.
Rescisão indireta reconhecida: equivale à dispensa sem justa causa, com todas
as verbas e a multa de 40%. O risco é assimétrico — improcedente, o pedido pode
ser lido como pedido de demissão, e o cliente perde tudo. Só se propõe com
falta grave patronal documentada.

=== AVISO PRÉVIO PROPORCIONAL (Lei 12.506/2011) ===
Trinta dias, acrescidos de 3 dias por ano completo de serviço na mesma empresa,
limitado o acréscimo a 60 dias — total máximo de 90 dias. O acréscimo beneficia
o empregado; não se usa para ampliar o aviso que o empregado deve ao pedir
demissão.
Memória de cálculo obrigatória: anos completos apurados, dias resultantes,
valor diário e produto.

=== AVISO INDENIZADO PROJETA O CONTRATO ===
O período do aviso indenizado integra o tempo de serviço para todos os efeitos.
Consequência prática que muda a conta: os avos de 13º e de férias proporcionais
são contados até o FIM da projeção, não até a data da comunicação da dispensa.
Demitido em abril com aviso indenizado que projeta até junho, o empregado tem
avos até junho. Esquecer a projeção subestima a rescisão em uma ou duas
parcelas de avos, e é achado clássico ao conferir TRCT da empresa.

=== NÃO INCIDÊNCIAS QUE MUDAM O LÍQUIDO ===
Imposto de renda não incide sobre aviso prévio indenizado, sobre férias
indenizadas (vencidas e proporcionais) com o respectivo terço, nem sobre a
multa de 40% do FGTS: são verbas indenizatórias, não remuneratórias.
Incide sobre saldo de salário, sobre 13º (tributado em separado) e sobre as
médias de variáveis.
Contribuição previdenciária não incide sobre férias indenizadas nem sobre a
multa do FGTS; incide sobre saldo e 13º. O aviso indenizado é ponto
controvertido e deve ser tratado como tal, com ressalva expressa, nunca como se
fosse pacífico.
Produção: o TRCT conferido pelo escritório sai com coluna de incidências, e
qualquer desconto indevido encontrado vira pedido de devolução.

=== CONFERÊNCIA DE TRCT (rotina de produção) ===
Confirme o motivo declarado; recalcule os avos com a projeção do aviso; confira
o aviso proporcional pelos anos completos; confira o saldo pelos dias
trabalhados no mês; verifique a multa aplicável ao motivo (40%, 20% ou zero);
some os depósitos rescisórios sobre aviso e 13º; confira as incidências de IR e
INSS; confira a data do pagamento contra o prazo legal (multa do art. 477, §
8º). Cada divergência vira uma linha "valor pago x valor devido x diferença",
com a memória ao lado.

#####################  PARTE II — PREVIDENCIÁRIO  #####################

=== DIREITO ADQUIRIDO ATÉ 12/11/2019 ===
Quem completou os requisitos da regra vigente antes da EC 103/2019 mantém o
direito de se aposentar por ela a qualquer tempo, ainda que continue
trabalhando. Nas regras então vigentes, exigiam-se 35 anos de contribuição para
o homem e 30 para a mulher, com cálculo sobre os 80% maiores salários de
contribuição e aplicação do fator previdenciário.
Produção: a PRIMEIRA verificação de qualquer análise é a existência de direito
adquirido naquela data. Recomendar regra de transição para quem já tinha
direito adquirido é erro que custa dinheiro do cliente e é irreversível depois
da concessão.

=== AS QUATRO REGRAS DE TRANSIÇÃO DA EC 103/2019 ===
Todas pressupõem filiação anterior a 13/11/2019 e todas exigem o tempo mínimo
de contribuição de 35 anos (homem) e 30 anos (mulher), salvo a regra de idade
mínima progressiva na sua própria formulação.

(1) PEDÁGIO DE 50% (art. 17). Restrita a quem, em 13/11/2019, estava a no
máximo 2 anos de completar o tempo mínimo. Exige cumprir o tempo que faltava
mais um adicional de 50% desse tempo faltante. O benefício é calculado com
aplicação do fator previdenciário, o que costuma reduzi-lo — janela estreita e
raramente a melhor opção, mas obrigatória de simular quando o segurado estava
mesmo no limiar.

(2) PEDÁGIO DE 100% (art. 20). Exige idade mínima (60 anos homem, 57 mulher),
o tempo mínimo de contribuição, e o cumprimento de um adicional igual a 100% do
tempo que faltava em 13/11/2019. Em troca, o benefício corresponde a 100% da
média de todos os salários de contribuição desde julho de 1994, sem fator
previdenciário. É, em regra, a transição de melhor renda — quando o segurado
alcança, compare sempre com as demais.

(3) PONTOS (art. 15). Soma da idade com o tempo de contribuição. A pontuação
exigida sobe um ponto por ano a partir do patamar inicial da Reforma, até os
tetos definidos na própria emenda. O tempo mínimo de contribuição continua
sendo exigido em paralelo — pontuação alta com tempo insuficiente não
aposenta.
Atenção de produção: a tabela de pontos é datada e muda todo 1º de janeiro. Não
a reproduza de memória; leia a pontuação do ano de referência em fonte
conferida e registre a fonte na memória de cálculo.

(4) IDADE MÍNIMA PROGRESSIVA (art. 16). Idade mínima que sobe seis meses por
ano até estabilizar, combinada com o tempo mínimo de contribuição. Mesma
advertência: o patamar do ano é dado datado, não se decora.

Cálculo das regras (3) e (4): 60% da média de todos os salários de contribuição
desde julho de 1994, acrescidos de 2 pontos percentuais por ano de contribuição
que exceder 20 anos (homem) ou 15 anos (mulher).

=== A MÉDIA MUDOU — E É O QUE MAIS DERRUBA RENDA ===
Depois da EC 103, a média é aritmética simples sobre TODOS os salários de
contribuição desde julho de 1994. Não se descartam mais os 20% menores. Isso
significa que contribuições baixas antigas puxam a média para baixo e que
continuar contribuindo em valor alto melhora o resultado.
Produção: ao comparar regras, apresente para cada uma o percentual aplicável,
a base e a data mais cedo de acesso — a recomendação é a combinação
data x renda, não a data mais próxima.

=== ENTREGÁVEL DE APOSENTADORIA — COMPARAÇÃO, NUNCA REGRA ÚNICA ===
Toda análise sai com o quadro comparativo das regras aplicáveis: requisitos
cumpridos e faltantes de cada uma, data mais cedo de implementação, percentual
de cálculo e efeito sobre a renda, com a memória de como se chegou lá. Só
depois vem a recomendação, com a ressalva de que a apuração definitiva do tempo
depende do CNIS, cuja obtenção é do cliente ou do advogado.
Erro a evitar: recomendar a aposentadoria disponível hoje sem mostrar quanto o
cliente ganharia esperando alguns meses por outra regra.

=== TEMPO ESPECIAL ===
A conversão de tempo especial em comum foi encerrada pela EC 103, mas o tempo
especial trabalhado ATÉ 12/11/2019 continua conversível, como direito adquirido
ao critério da lei da época. A prova é o PPP, amparado no laudo técnico
ambiental; sem PPP, o caminho é a prova documental complementar e, em juízo, a
perícia.

=== BPC-LOAS (Lei 8.742/1993, art. 20) ===
Benefício assistencial, de um salário mínimo, que NÃO exige contribuição, não
gera 13º, não gera pensão por morte e é intransferível. Requisitos:
(a) ser idoso com 65 anos ou mais, OU pessoa com deficiência com impedimento de
    longo prazo — de natureza física, mental, intelectual ou sensorial — que,
    em interação com barreiras, obstrua a participação plena na sociedade, com
    duração mínima de 2 anos;
(b) renda familiar mensal per capita inferior a 1/4 do salário mínimo;
(c) inscrição ativa no CadÚnico;
(d) não acumular com outro benefício previdenciário, salvo as exceções legais.
A avaliação da deficiência é biopsicossocial: perícia médica MAIS avaliação
social. Não é só o CID.

=== RENDA PER CAPITA DO BPC — O CRITÉRIO DE 1/4 NÃO É ABSOLUTO ===
O STF assentou que o critério de 1/4 do salário mínimo é presunção legal de
miserabilidade, mas não impede a demonstração da vulnerabilidade por outros
meios no caso concreto; a legislação posterior também admite patamar mais alto
em hipóteses específicas.
Produção: quando a renda per capita fica acima de 1/4 mas a família é
miseravelmente vulnerável, o pedido não é abandonado — pede-se expressamente a
flexibilização, provando gasto que consome a renda: medicamento de uso
contínuo com receita e nota, fralda, dieta enteral, transporte para tratamento,
cuidador. E deduza esses gastos na memória de cálculo, mostrando a renda
efetivamente disponível.
Memória de cálculo obrigatória: quem compõe o grupo familiar (com o
fundamento da composição), a renda declarada de cada membro com o documento que
a comprova, o total, o número de pessoas, a divisão e o resultado, comparado ao
limite legal do ano de referência.
Erro clássico: computar apenas a renda do requerente e ignorar o grupo
familiar, ou o inverso — incluir no grupo quem a lei não inclui.

=== BENEFÍCIO POR INCAPACIDADE — QUALIDADE DE SEGURADO, CARÊNCIA, INCAPACIDADE ===
Três requisitos, verificados nesta ordem, e a peça só nasce quando os três
estão sustentados por documento:
(1) Qualidade de segurado na data do início da incapacidade, inclusive durante
    o período de graça, que se prorroga nas hipóteses legais (desemprego
    comprovado, entre outras).
(2) Carência de 12 contribuições para o auxílio por incapacidade temporária e
    para a aposentadoria por incapacidade permanente de origem NÃO acidentária.
    Dispensada a carência quando a incapacidade decorre de acidente de qualquer
    natureza, de acidente do trabalho ou de doença grave especificada em lei.
(3) Incapacidade, temporária ou permanente, para a atividade habitual —
    provada por laudos, exames e receituários contemporâneos, com CID, e não
    por atestado genérico.
Distinga as espécies: incapacidade temporária; incapacidade permanente;
auxílio-acidente, que é indenizatório, pago por sequela que reduz a capacidade,
e não se confunde com os anteriores.

=== ALTA PROGRAMADA (DCB) — O QUE FAZER E EM QUE ORDEM ===
O INSS concede o benefício já com data de cessação fixada. Se o segurado
continua incapaz, a sequência é:
(a) pedido de prorrogação, formulado na janela que antecede a data de cessação
    — é a via mais rápida e a que preserva a continuidade do pagamento;
(b) perdida a janela ou negada a prorrogação, recurso administrativo à junta e,
    depois, à câmara de julgamento;
(c) esgotada ou inviável a via administrativa, ação de restabelecimento, com
    pedido de tutela de urgência.
Produção: a peça de restabelecimento pede a DIB na data da cessação indevida,
não na data do ajuizamento — é ali que estão os atrasados. E o pedido de tutela
se sustenta em dois pilares que devem ser escritos separadamente: probabilidade
do direito (laudos particulares contemporâneos) e perigo de dano (segurado sem
renda, com a perícia judicial designada para meses à frente).

=== PERÍCIA JUDICIAL — QUESITOS SÃO PARTE DA PEÇA ===
Em benefício por incapacidade a perícia decide a causa. Os quesitos vão com a
inicial: existe incapacidade; ela é temporária ou permanente, total ou parcial;
qual a data de início da incapacidade e em que elementos o perito se baseou
para fixá-la; a incapacidade alcança a atividade habitual descrita; há sequela
permanente redutora da capacidade; a origem é ocupacional ou comum; é possível
reabilitação profissional considerando idade, escolaridade e histórico laboral.
A data de início da incapacidade é o quesito que define quanto o cliente
recebe: não deixe o perito fixá-la sem provocação.
Em BPC, o par é outro: perícia médica para o impedimento de longo prazo e
estudo social domiciliar para a vulnerabilidade. Peça os dois.

=== VIA ADMINISTRATIVA ANTES DA JUDICIAL ===
Em regra, o interesse de agir em matéria previdenciária pressupõe prévio
requerimento administrativo indeferido, cessado ou não apreciado em prazo
razoável. Antes de redigir a inicial, confirme o número do benefício e a data e
o motivo do indeferimento. Sem esse elemento, a peça sai BLOQUEADA com a
pendência, e não com um campo vazio.

=== ATRASADOS ===
Os valores retroativos correm da data de entrada do requerimento ou da data da
cessação indevida, conforme o caso, até a implantação. A memória de cálculo
mostra: marco inicial e seu fundamento, número de competências, renda mensal
considerada, subtotal, e o critério de correção e juros nomeado. Índice não
nomeado é índice não aplicado. Se o critério vigente para o período não puder
ser conferido, o pedido é formulado com o critério legal aplicável na forma da
lei, e o valor fica para a liquidação — sem número inventado.

=== ESTRUTURA PADRÃO DA INICIAL PREVIDENCIÁRIA ===
Fatos, com a história contributiva ou clínica em ordem cronológica e remissão a
documento. Gratuidade de justiça logo em seguida. Fundamentos por requisito, na
ordem qualidade de segurado, carência, requisito específico do benefício.
Pedido de perícia e, no BPC, de estudo social. Tutela de urgência como último
tópico antes dos pedidos. Pedidos certos: concessão ou restabelecimento com DIB
determinada, atrasados, honorários. Valor da causa com a memória que o compõe.

########################  FECHAMENTO  ########################

=== ENTREGÁVEL OBRIGATÓRIO (ambas as matérias) ===
(1) A peça redigida ponta a ponta, sem placeholder.
(2) A memória de cálculo de cada valor, auditável a mão.
(3) A lista dos documentos que sustentam cada afirmação, referidos pelo nome
    conferido no caso — nunca por número de folha ou ID deduzido de memória.
(4) A lista de pendências no topo, quando houver, com o que exatamente falta e
    de quem é a providência.
(5) O checklist de conferência para o humano que vai protocolar.
(6) A frase final de status: "pronta para conferência e protocolo humano" ou
    "BLOQUEADA — N pendências".

=== ANTI-PADRÕES QUE REPROVAM O ENTREGÁVEL ===
Pedido trabalhista sem valor líquido. Verba pedida em período prescrito.
Reflexo esquecido ou contado em duplicidade. Multa de 40% aplicada a acordo do
art. 484-A. Avos calculados sem a projeção do aviso indenizado. IR descontado
sobre aviso indenizado, férias indenizadas ou multa do FGTS. Defesa que impugna
os fatos genericamente. Pasta funcional incompleta na defesa. Preposto sem
carta. Recomendação de aposentadoria sem comparar as regras. Regra de transição
oferecida a quem tem direito adquirido. Tabela de pontos citada de memória.
Renda per capita do BPC calculada só com a renda do requerente. Inicial de
incapacidade sem quesitos de perícia. Restabelecimento pedindo DIB na data do
ajuizamento em vez da cessação. Qualquer valor sem memória de cálculo. Qualquer
citação sem veredito de verificação. Qualquer seção terminando em citação.
"""

PRODUCAO_TRABALHISTA_PREVIDENCIARIO_SOURCES = [
    "Dossiê proprietário: Reclamação Trabalhista Inicial — pedido líquido pós-Reforma (MV Advocacia)",
    "Dossiê proprietário: Defesa Trabalhista do Empregador — impugnação específica e pasta funcional",
    "Dossiê proprietário: Cálculo de Verbas Rescisórias — TRCT, motivos e não incidências",
    "Dossiê proprietário: Aposentadoria por Tempo de Contribuição pós-EC 103/2019",
    "Dossiê proprietário: BPC-LOAS — Lei 8.742/1993, art. 20",
    "Dossiê proprietário: Auxílio por Incapacidade — concessão, restabelecimento e recurso",
    "Regra da casa: nunca entregar peça final com placeholder (11/06/2026)",
    "Regra da casa: validar toda jurisprudência antes do protocolo",
    "Regras de redação do titular (12/06/2026): linguagem direta, sem caixa alta, "
    "negrito mínimo, gratuidade primeiro e tutela por último",
    "Regra da casa: nenhuma seção termina em citação",
    "Plataforma — app/services/citation_verification_service.py (veredito de citação)",
]
