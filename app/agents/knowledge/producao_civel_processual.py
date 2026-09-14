"""Base de conhecimento da PRODUÇÃO de peças cíveis e processuais (padrão MV).

Extraída dos dossiês proprietários dos agentes de produção do titular:
petição inicial cível, contestação cível, escolha e redação de recurso,
apelação cível, agravo de instrumento, ação de cobrança e monitória,
cumprimento de sentença e impugnação ao cumprimento de sentença.

Esta base é de PRODUÇÃO: como se redige a peça. Não é base de venda
(`direito_saude_vendas.py`, `playbook_*`) nem de revisão pré-protocolo
(`revisao_senior_protocolo.py`). O fluxo natural é produção -> revisão
sênior -> protocolo humano.

TRADUÇÃO PARA A PLATAFORMA (os dossiês nasceram para um agente de desktop
com Chrome, Bash e arquivos locais; nada disso existe no servidor):

  * "validar jurisprudência via Chrome/Jusbrasil antes de citar" ->
    `app/services/citation_verification_service.py`. Sobrevive apenas a
    regra substantiva: nenhuma citação entra na peça como verificada sem
    veredito de verificação; sem veredito, ou sai do texto, ou aparece
    marcada como NÃO VERIFICADA para o revisor humano.
  * "calcular via Python/Bash" -> NÃO existe execução de código no agente.
    A regra vira: todo valor (valor da causa, crédito atualizado, preparo,
    excesso de execução) vem de cálculo auditável com memória demonstrada
    parcela a parcela, com índice, marco inicial e data de atualização
    explícitos. Número solto, sem memória, é defeito da peça.
  * "gravar em /tmp e exportar" -> a minuta é entregue como conteúdo
    estruturado da plataforma; a formatação MV e o .docx são do serviço de
    documento, não deste agente.

OMITIDO por não ter correspondente real na plataforma (declarado, não
disfarçado): navegação em Chrome, consulta a DataJud/CNPJ/BACEN/SISBAJUD,
execução de scripts Python, leitura do vault Obsidian, protocolo eletrônico
automatizado em PJe/e-SAJ/Projudi e emissão ou pagamento de guias de custas.
O que a plataforma não faz, o agente declara como pendência do humano em vez
de simular.
"""

PRODUCAO_CIVEL_PROCESSUAL_KNOWLEDGE_VERSION = "producao-civel-processual-2026-07"

# Tipos de peça cobertos por esta base. Fonte única para o prompt do agente e
# para testes de contrato — a base não pode perder um tipo em silêncio.
TIPOS_DE_PECA: tuple[str, ...] = (
    "peticao_inicial_civel",
    "contestacao_civel",
    "escolha_de_recurso",
    "apelacao_civel",
    "agravo_de_instrumento",
    "acao_de_cobranca_e_monitoria",
    "cumprimento_de_sentenca",
    "impugnacao_ao_cumprimento_de_sentenca",
)

PRODUCAO_CIVEL_PROCESSUAL_KNOWLEDGE = """
=== PRINCÍPIO MESTRE DA PRODUÇÃO ===
Escreva a peça inteira, ponta a ponta, nunca um esqueleto com lacunas. Cada
seção fundamentada, cada pedido específico e congruente, cada valor com
memória de cálculo. A minuta termina pronta para revisão sênior e para
protocolo por um humano.

=== REGRAS INVIOLÁVEIS (valem para todos os tipos de peça) ===

1. PROTOCOLO É EXCLUSIVAMENTE HUMANO. Nenhuma peça é protocolada por agente.
   A entrega termina em minuta pronta para revisão e protocolo humano, com
   checklist do que o humano precisa conferir e anexar. Não afirme que
   protocolou, que pagou custas ou que consultou sistema de tribunal.

2. NUNCA ENTREGAR COM PLACEHOLDER. Nenhuma peça declarada final pode conter
   "[PREENCHER]", "XXX", "____", "[...]", "●" ou campo vazio. Antes de
   entregar, varra corpo, tabelas, cabeçalho e rodapé.
   - Dado faltando: procure primeiro nos autos e nos documentos do caso já
     disponíveis na plataforma (outras peças do mesmo processo, procuração,
     contrato, anexos).
   - Se não existir em lugar nenhum: REMOVA o campo. Em peça nos mesmos
     autos, "já devidamente qualificada nos autos" dispensa CPF/CNPJ e
     endereço; o documento se refere pelo nome ("o contrato juntado com a
     inicial") em vez de por número não conferido.
   - Se o dado for juridicamente indispensável e inexistente, a peça NÃO é
     final: entregue marcada como BLOQUEADA, com a pendência no topo.

3. CITAÇÃO SEM VEREDITO NÃO ENTRA COMO VERIFICADA. Nenhum REsp, AgRg, HC,
   Tema, IRDR ou Súmula entra afirmado como validado sem veredito do serviço
   de verificação de citações. Valide número, órgão, relator, data e,
   sobretudo, o teor: a ementa precisa sustentar exatamente o que a peça diz
   que ela sustenta. Sem veredito, substitua por lei seca e doutrina ou
   marque como NÃO VERIFICADA para o revisor.

4. IDs DE DOCUMENTO SÓ SE CONFERIDOS. Nunca cite ID do PJe, fls., doc. nº ou
   anexo nº de memória ou por dedução. Sem fonte conferida, refira o
   documento pelo nome e registre a pendência no topo do entregável.

5. NENHUMA SEÇÃO TERMINA EM CITAÇÃO. Ementa, doutrina ou dispositivo
   transcrito nunca fecha um tópico; o fechamento é sempre uma frase do
   advogado aplicando aquilo ao caso.

6. ESTILO DO TITULAR. Português jurídico direto, frase curta, uma ideia por
   frase. Juridiquês ornamental ("data venia", "destarte", "outrossim", "in
   casu", "vergastada") com incidência baixa, nunca como padrão. Sem caixa
   alta para dar ênfase (exceção: siglas e títulos de seção). Negrito apenas
   na tese central de cada seção, na data/valor/prazo decisivo e no pedido
   principal. Sem travessão.

7. ORDEM ESTRUTURAL. Gratuidade de justiça, quando requerida, é o primeiro
   tópico logo após os fatos, antes de qualquer tópico de mérito. Tutela
   (urgência, evidência, cautelar, recursal, liminar) é sempre o último
   tópico antes dos pedidos. Não intercalar tutela no meio do mérito nem
   deixar gratuidade para o fim.

8. VALOR SEMPRE COM MEMÓRIA. Valor da causa, crédito atualizado, preparo,
   excesso de execução: cada um vem de cálculo auditável, parcela a parcela,
   com índice aplicado, marco inicial, marco final e fundamento do índice.
   Número solto é defeito. O agente não executa código; ele demonstra a
   conta e identifica o que depende de índice oficial a ser confirmado pelo
   humano.

=== A. PETIÇÃO INICIAL CÍVEL (rito comum) ===

REQUISITOS OBRIGATÓRIOS (CPC 319): juízo a que é dirigida; qualificação
completa de autor e réu (nomes, estado civil, existência de união estável,
profissão, CPF/CNPJ, e-mail, domicílio e residência); fato e fundamentos
jurídicos do pedido; pedido com suas especificações; valor da causa; provas
com que se pretende demonstrar a verdade dos fatos; opção do autor pela
realização ou não de audiência de conciliação (CPC 319 VII c/c 334 § 5º).
Documentos indispensáveis à propositura vão junto (CPC 320). Emenda em 15
dias antes do indeferimento (CPC 321); pedido genérico ou incompatível gera
inépcia (CPC 330 § 1º).

ESTRUTURA: endereçamento; qualificação; fatos em ordem cronológica com
remissão a documentos numerados; gratuidade (se pedida, aqui); direito, uma
tese por tópico; tutela provisória (último tópico antes dos pedidos);
pedidos; valor da causa; provas; local, data e assinatura.

O QUE NÃO PODE FALTAR: pedido de citação do réu com endereço, sob pena de
revelia (CPC 344); pedido específico e determinado, jamais "o que for de
direito"; requerimento de produção de provas nomeadas; condenação em custas,
despesas e honorários sucumbenciais (CPC 85, 10% a 20%); valor da causa
conforme CPC 292; procuração; documentos essenciais (CPC 320).

VALOR DA CAUSA (CPC 292): cobrança = principal + juros vencidos + multa;
dano material ou moral = valor pretendido; anulação = valor do contrato ou
título; despejo = doze meses de aluguel; alimentos = doze prestações
pretendidas; reintegração de posse = valor do bem; declaração de
inexigibilidade = valor do título contestado.

TUTELA PROVISÓRIA: urgência (CPC 300) exige probabilidade do direito e
perigo de dano ou risco ao resultado útil do processo, ambos quantificados e
ancorados em documento, nunca genéricos; evidência (CPC 311) dispensa
urgência e se apoia em prova documental somada a tese firmada em julgamento
de casos repetitivos, ou contrato de depósito, ou prova documental
suficiente sem defesa capaz de gerar dúvida. Obrigação de fazer pede
astreinte proporcional (CPC 537), com valor diário e teto.

JUROS E CORREÇÃO: contratual segue o contrato, com mora de 1% ao mês (CC
406) ou Selic, e correção pelo índice pactuado; extracontratual (CC 186 e
927) tem juros desde o evento danoso (Súmula 54 STJ); dano moral corrige
desde o arbitramento (Súmula 362 STJ) e conta juros desde o evento ou desde
a citação, se contratual.

PRAZOS E CUSTAS: prazo processual em dias úteis (CPC 219), contado do
primeiro dia útil seguinte à intimação (CPC 224 § 2º); custas iniciais
variam por tribunal (ordem de 1% a 2% do valor da causa) e são recolhidas
pelo humano; gratuidade (CPC 98 e Lei 1.060/50) dispensa, com declaração de
hipossuficiência, e pessoa jurídica pode obtê-la se demonstrar a
insuficiência (Súmula 481 STJ).

ERROS QUE MATAM A PEÇA: pedido genérico (inépcia); esquecer o pedido de
citação; documento essencial não juntado (CPC 320); valor da causa
incompatível; liminar pedida sem demonstrar urgência concreta; foro errado
(CPC 42-53); procuração ausente; endereço do réu desatualizado, que empurra
para citação por edital e atrasa o feito.

ENTREGÁVEL FINAL: peça redigida ponta a ponta; memória do valor da causa;
lista numerada dos documentos a anexar; checklist de protocolo para o
humano; pendências declaradas no topo, se houver.

=== B. CONTESTAÇÃO CÍVEL ===

PRAZO: 15 dias úteis (CPC 335), contados conforme o inciso aplicável
(audiência de conciliação frustrada, protocolo do pedido de cancelamento da
audiência, ou juntada do mandado). Fazenda Pública em dobro (CPC 183);
litisconsortes com procuradores diferentes de escritórios distintos em autos
físicos, em dobro (CPC 229). Perder o prazo gera revelia (CPC 344), que
presume verdadeiros os FATOS alegados, não os efeitos jurídicos pretendidos.

DEFESA CONCENTRADA: toda matéria de defesa vem em peça única (CPC 336),
inclusive a reconvenção (CPC 343), que não é mais peça autônoma, tem pedido
próprio, valor próprio e custas próprias.

PRELIMINARES (CPC 337), apenas as cabíveis: inexistência ou nulidade da
citação; incompetência absoluta ou relativa; incorreção do valor da causa;
inépcia da inicial; perempção; litispendência; coisa julgada; conexão;
incapacidade da parte, defeito de representação ou falta de autorização;
convenção de arbitragem; ausência de legitimidade ou de interesse
processual; falta de caução ou outra prestação que a lei exige como
preliminar; indevida concessão do benefício da gratuidade. Prescrição e
decadência (CPC 487 II) são conhecíveis de ofício, mas alegue sempre.

IMPUGNAÇÃO ESPECÍFICA (CPC 341) — o núcleo da peça: fato não impugnado
especificamente presume-se verdadeiro. Por isso a contestação impugna item a
item da inicial, enumerando cada fato narrado pelo autor e respondendo a
cada um, ainda que para confirmar o fato e devolvê-lo ao contexto correto.
"Nego todos os fatos" não cumpre o art. 341 e entrega o caso. As exceções do
parágrafo único (defensor público, advogado dativo, curador especial) não
socorrem o advogado constituído.

PROVAS: documental se junta com a contestação, por concentração (CPC 434);
documento novo só se posterior ou destinado a contrapor os juntados (CPC
435). Testemunhal com rol no prazo fixado no saneamento, limite de dez
testemunhas e três por fato (CPC 357 § 6º). Perícia com quesitos e
assistente técnico indicados no momento próprio (CPC 465 § 1º).

O QUE NÃO PODE FALTAR: preliminares cabíveis; impugnação item a item;
mérito com fundamento legal; reconvenção na mesma peça, se cabível; rol e
requerimento de provas; honorários sucumbenciais (CPC 85); documentos do réu
numerados; procuração.

ERROS QUE MATAM A PEÇA: defesa genérica; reconvenção em peça separada;
esquecer prescrição; deixar documento essencial para depois; pedir perícia
sem quesitos; perder o prazo.

ENTREGÁVEL FINAL: peça redigida ponta a ponta com cada item da inicial
impugnado; lista numerada dos documentos a juntar; rol de testemunhas;
análise de necessidade de agravo se já houve tutela concedida contra o réu;
checklist para o humano.

=== C. ESCOLHA DO RECURSO (juízo de admissibilidade antes de escrever) ===

Escolher o recurso certo resolve metade do problema. Roteiro: sentença (CPC
203 § 1º) pede apelação (CPC 1.009); interlocutória do rol do CPC 1.015 pede
agravo de instrumento; interlocutória fora do rol não preclui e se suscita
em preliminar de apelação ou nas contrarrazões (CPC 1.009 § 1º); decisão
monocrática de relator pede agravo interno (CPC 1.021); acórdão ou sentença
com obscuridade, contradição, omissão ou erro material pede embargos de
declaração (CPC 1.022); acórdão contrário a lei federal ou com divergência
pede recurso especial (CPC 1.029, CF 105 III); acórdão que viola a
Constituição pede recurso extraordinário (CF 102 III), com repercussão geral
demonstrada (CPC 1.035).

PRAZOS: 15 dias úteis para apelação, agravo de instrumento, agravo interno,
recurso especial e extraordinário (CPC 1.003 § 5º); 5 dias úteis para
embargos de declaração (CPC 1.023). Embargos de declaração interrompem o
prazo dos demais recursos (CPC 1.026). Contagem em dias úteis (CPC 219), a
partir do primeiro dia útil seguinte à publicação; Fazenda Pública e
Defensoria em dobro (CPC 183 e 186).

SETE CRITÉRIOS DE ADMISSIBILIDADE, verificados antes de redigir: cabimento;
tempestividade; preparo; legitimidade e interesse (sucumbência); ausência de
fato impeditivo (renúncia, aceitação tácita, desistência); regularidade
formal (assinatura, procuração, partes corretas, endereçamento);
prequestionamento, se recurso excepcional.

PREQUESTIONAMENTO: a questão federal ou constitucional precisa ter sido
enfrentada no acórdão (Súmula 282 e 356 STF; Súmula 211 STJ). Se o acórdão
silenciou, oponha embargos de declaração para forçar; o CPC 1.025 considera
prequestionados os elementos suscitados nos embargos ainda que inadmitidos.

ERROS QUE MATAM O RECURSO: apelar de interlocutória; agravar de despacho de
mero expediente; embargar para rediscutir mérito (multa do CPC 1.026 § 2º);
recurso excepcional sem prequestionamento; esquecer porte de remessa; não
recolher preparo no ato (deserção, CPC 1.007); não pedir honorários
recursais (CPC 85 § 11).

=== D. APELAÇÃO CÍVEL ===

PEÇA DUPLA: petição de interposição dirigida ao juízo a quo (CPC 1.010) mais
razões dirigidas ao tribunal. Prazo de 15 dias úteis (30 para Fazenda, CPC
183). Preparo e porte comprovados no ato da interposição, sob pena de
deserção (CPC 1.007); insuficiência admite complementação em 5 dias e o
recolhimento em dobro só na hipótese do § 4º; gratuidade dispensa.

CONTEÚDO DAS RAZÕES (CPC 1.010): nomes e qualificação das partes; exposição
do fato e do direito; razões do pedido de reforma ou de decretação de
nulidade; pedido de nova decisão. Cada fundamento autônomo da sentença
precisa ser combatido; fundamento não atacado sustenta sozinho a decisão
(lógica da Súmula 283 STF).

EFEITOS (CPC 1.012): regra é o efeito suspensivo. Produzem efeito imediato,
independentemente do recurso, as sentenças que homologam divisão ou
demarcação, condenam a pagar alimentos, extinguem sem mérito ou julgam
improcedentes os embargos do executado, julgam procedente o pedido de
instituição de arbitragem, confirmam, concedem ou revogam tutela provisória,
e decretam interdição. Nessas hipóteses, o efeito suspensivo se requer ao
relator (CPC 1.012 §§ 3º e 4º), demonstrando probabilidade de provimento ou
risco de dano grave.

REGRAS DO TITULAR PARA RECURSO, aprendidas em caso real:
  * Só material debatido. Cláusula, item de edital, documento ou fato só
    entra se já consta da inicial, das contestações, da sentença ou de
    decisões dos autos. O que ninguém debateu não entra: não se dá munição
    à parte contrária e o recurso pode virar recurso especial, onde só o
    prequestionado serve. Confira o escopo do item citado antes de usá-lo.
  * Sem retorno à origem. Nunca pedir cassação com devolução à primeira
    instância. A ordem é reforma com julgamento imediato (CPC 1.013 § 3º),
    depois diligência no próprio tribunal (CPC 938 § 3º), e só em última
    hipótese anulação com instrução delimitada. Nulidades como decisão
    surpresa (CPC 10) e cerceamento de defesa são fundamento, não pedido de
    devolução, e ficam subsidiárias ao mérito, com hierarquização expressa.
  * Mérito primeiro. A peça abre pelo erro de mérito da sentença, de
    preferência pela contradição interna dela, nunca pela preliminar de
    nulidade.
  * Nenhuma concessão no corpo. A hipótese de o cliente dever algo aparece
    em um único tópico subsidiário final, rotulado "por estrito dever de
    eventualidade".
  * Tutela recursal em cascata, nunca tudo ou nada: efeito ativo pleno;
    depois medida com caução sobre o próprio bem ou sobre direitos
    aquisitivos, sem descapitalizar o cliente com depósito; depois vistoria
    ou perícia prévia com a medida na sequência; ao menos a neutralização
    econômica, com depósito judicial pela parte contrária. Sentença de
    improcedência não se ataca com efeito suspensivo, porque não há o que
    suspender: pede-se antecipação da tutela recursal, nas razões e por
    petição autônoma ao tribunal.
  * Havendo agravo anterior no mesmo processo, pedir distribuição por
    prevenção (CPC 930, parágrafo único).

DEVOLUÇÃO E LIMITES: a apelação devolve o que foi impugnado (CPC 1.013), e
os §§ 1º a 3º permitem ao tribunal julgar desde logo; vedada a reformatio in
pejus em recurso exclusivo de uma parte; capítulos autônomos da sentença
podem ser impugnados separadamente. Prova nova em apelação, em regra vedada
(CPC 435 e 1.014), salvo motivo de força maior.

PEDIDOS TÍPICOS: conhecimento e provimento para reformar a sentença, com a
solução concreta pedida; subsidiariamente, o que a hierarquia acima
autorizar; honorários recursais (CPC 85 § 11, majoração de 1% a 5%,
respeitados os tetos); inversão da sucumbência.

ENTREGÁVEL FINAL: petição de interposição; razões; memória do preparo e do
porte para recolhimento humano; lista de documentos; análise de risco
(cenário de provimento total, parcial e desprovimento, com exposição a
honorários recursais); checklist de protocolo.

=== E. AGRAVO DE INSTRUMENTO ===

CABIMENTO (CPC 1.015, rol): tutelas provisórias; mérito do processo;
rejeição da alegação de convenção de arbitragem; incidente de
desconsideração da personalidade jurídica; rejeição do pedido de gratuidade
ou acolhimento do pedido de sua revogação; exibição ou posse de documento ou
coisa; exclusão de litisconsorte; rejeição do pedido de limitação do
litisconsórcio; admissão ou inadmissão de intervenção de terceiros;
concessão, modificação ou revogação do efeito suspensivo aos embargos à
execução; redistribuição do ônus da prova; demais casos expressamente
previstos em lei. Fora do rol, cabe invocar a taxatividade mitigada (Tema
988 STJ), demonstrando urgência que torne inútil o exame futuro em apelação.
Interlocutórias na fase de liquidação, cumprimento de sentença, execução e
inventário são todas agraváveis (CPC 1.015, parágrafo único).

PRAZO: 15 dias úteis (CPC 1.003 § 5º), com preparo no ato.

PEÇAS OBRIGATÓRIAS (CPC 1.017 I): cópia da petição inicial, da contestação,
da petição que originou a decisão agravada, da própria decisão agravada, da
certidão da respectiva intimação ou outro documento oficial que comprove a
tempestividade, e das procurações outorgadas aos advogados de ambas as
partes. Faltando peça, o relator concede prazo de 5 dias para sanar antes de
inadmitir (CPC 1.017 § 3º). Em autos eletrônicos a juntada é dispensada (§
5º), mas a comprovação da tempestividade continua sendo do recorrente.

COMUNICAÇÃO AO JUÍZO DE ORIGEM (CPC 1.018): em 3 dias da interposição,
juntar aos autos de origem cópia da petição, comprovante da interposição e
relação dos documentos. Em autos eletrônicos há dispensa (§ 2º); em autos
físicos, a omissão, se arguida e provada pelo agravado, gera
inadmissibilidade (§ 3º).

EFEITO SUSPENSIVO E TUTELA RECURSAL (CPC 1.019 I): o relator pode atribuir
efeito suspensivo (paralisa a decisão) ou deferir em antecipação a tutela
que a decisão negou (efeito ativo). Requisitos: probabilidade do direito e
risco de dano grave, ambos concretos e quantificados.

PEDIDOS TÍPICOS: efeito suspensivo ou antecipação da tutela recursal;
intimação do agravado para responder em 15 dias (CPC 1.019 II); oitiva do
Ministério Público quando cabível (CPC 1.019 III); ao final, provimento para
reformar a decisão; honorários recursais.

ERROS QUE MATAM A PEÇA: agravar fora do rol sem fundamentar a mitigação;
faltar peça do CPC 1.017 e não sanar no prazo; omitir a comunicação do CPC
1.018 em autos físicos; não comprovar tempestividade; pedir efeito
suspensivo sem urgência demonstrada; confundir com agravo interno (CPC
1.021) ou com agravo em recurso especial e extraordinário (CPC 1.042).

ENTREGÁVEL FINAL: peça redigida; lista conferida das peças do CPC 1.017;
minuta da comunicação do CPC 1.018 com o prazo de 3 dias sinalizado ao
humano; pedido de efeito suspensivo com probabilidade e risco quantificados;
checklist.

=== F. AÇÃO DE COBRANÇA E AÇÃO MONITÓRIA ===

ESCOLHA DA VIA: crédito sem qualquer título vai por cobrança pelo rito
comum; prova escrita sem eficácia executiva vai por monitória (CPC 700), o
que inclui cheque prescrito, nota promissória sem os requisitos executivos,
contrato sem as duas testemunhas e confissão de dívida em e-mail; título
executivo extrajudicial (CPC 784) vai por execução, não por estas vias.

PRESCRIÇÃO (CC 205 e 206, verificar antes de tudo): pretensão geral, 10
anos; reparação civil, 3 anos; aluguéis, 3 anos; honorários profissionais, 5
anos; execução de cheque, 6 meses após o prazo de apresentação; monitória
fundada em cheque sem força executiva, 5 anos do dia seguinte ao da emissão
(Súmula 503 STJ); monitória fundada em nota promissória sem força executiva,
5 anos (Súmula 504 STJ); pretensão contra a Fazenda Pública, 5 anos (Decreto
20.910/32).

CÁLCULO DO CRÉDITO, com memória obrigatória: principal; juros remuneratórios
contratuais, se pactuados, com o marco inicial; juros de mora de 1% ao mês
(CC 406) ou o índice legal aplicável, contados do vencimento na obrigação
positiva e líquida com termo certo (CC 397) ou da citação nos demais casos,
e do evento danoso no ilícito extracontratual (Súmula 54 STJ); multa
contratual; correção monetária desde cada vencimento pelo índice do tribunal
ou o pactuado. A memória detalha parcela a parcela.

FORO: domicílio do réu como regra (CPC 46); em ação fundada em obrigação, o
lugar onde deve ser satisfeita (CPC 53 III "d"); foro de eleição é válido
entre empresas, mas pode ser afastado em relação de consumo (CDC 51 IV).

PEDIDOS TÍPICOS NA COBRANÇA: citação do réu; procedência com condenação no
principal corrigido desde o vencimento, juros de mora, multa contratual,
custas e honorários (CPC 85); tutela de urgência (CPC 300) de arresto ou
bloqueio quando houver risco concreto de dissipação patrimonial.

PEDIDOS TÍPICOS NA MONITÓRIA: expedição do mandado para pagamento em 15 dias
ou oferecimento de embargos monitórios (CPC 701 e 702); honorários de 5%
fixados de plano, com isenção de custas se o réu pagar no prazo (CPC 701 §
1º); constituição de pleno direito do título executivo judicial se não houver
pagamento nem embargos (CPC 701 § 2º).

PROVAS A REQUERER: documental completa desde a inicial (contrato, notas,
comprovantes de pagamento parcial, notificações, extratos); depoimento
pessoal do réu quando a controvérsia for sobre pagamento; perícia contábil
quando o valor depender de apuração de encargos.

ERROS QUE MATAM A PEÇA: ajuizar execução com documento que não é título
(correto é monitória); não demonstrar que a prescrição não se consumou;
atualizar a partir de data errada; foro de eleição contra consumidor;
esquecer os juros contratuais desde o vencimento.

ENTREGÁVEL FINAL: peça redigida (cobrança ou monitória, com a escolha
justificada); cálculo do crédito com memória parcela a parcela; documentos
numerados; checklist, incluindo a estratégia pré-processual de notificação e
protesto, que é decisão e ato do humano.

=== G. CUMPRIMENTO DE SENTENÇA ===

PRESSUPOSTOS: título judicial condenatório; obrigação líquida, certa e
exigível; se ilíquida, liquidação prévia (CPC 509-512); demonstrativo
discriminado e atualizado do débito (CPC 524), com nome e qualificação das
partes, índice de correção, taxa de juros, termos inicial e final de ambos,
periodicidade da capitalização se houver, e especificação de multas ou
penalidades.

FLUXO (CPC 523 e seguintes): requerimento com o demonstrativo; intimação do
executado na forma do CPC 513 § 2º (pelo advogado, via publicação, quando
constituído); prazo de 15 dias para pagamento voluntário; não pago,
incidência automática de multa de 10% e de honorários de 10% (CPC 523 § 1º),
com expedição de mandado de penhora e avaliação; pagamento parcial faz a
multa e os honorários incidirem sobre o restante (§ 2º); em seguida, atos de
constrição, avaliação e expropriação (CPC 875-903).

IMPENHORABILIDADE (CPC 833), a conferir antes de pedir constrição: bens
inalienáveis; móveis e utilidades domésticas de padrão médio; vestuário e
pertences de uso pessoal, salvo alto valor; vencimentos, salários e
proventos, salvo para prestação alimentícia e quanto ao que exceder cinquenta
salários mínimos mensais (§ 2º); livros, máquinas e utensílios necessários ao
exercício da profissão; seguro de vida; materiais de construção em obra;
pequena propriedade rural trabalhada pela família; recursos públicos com
destinação vinculada; poupança até quarenta salários mínimos; recursos do
fundo partidário e do FUNDEB. Bem de família é impenhorável (Lei 8.009/90),
salvo as exceções do art. 3º.

ESTRATÉGIA POR PERFIL DE DEVEDOR: pessoa jurídica ativa começa por bloqueio
de ativos financeiros; pessoa física assalariada exige a via patrimonial e a
verificação do teto de impenhorabilidade; devedor com imóvel pede pesquisa
registral e indisponibilidade; devedor sem bens conhecidos pede as pesquisas
patrimoniais disponíveis ao juízo; devedor que esvaziou a empresa abre
caminho para desconsideração da personalidade jurídica (CPC 133-137, CC 50).
Contra a Fazenda Pública não há penhora: o rito é o do CPC 534-535, com
precatório ou requisição de pequeno valor. Os pedidos de pesquisa são
formulados ao juízo; a plataforma não consulta esses sistemas.

ERROS QUE MATAM A PEÇA: apresentar valor sem demonstrativo do CPC 524;
esquecer o requerimento de multa e honorários de 10%; pedir penhora de bem
notoriamente impenhorável; iniciar cumprimento de título ilíquido sem
liquidar; cobrar a Fazenda por penhora.

ENTREGÁVEL FINAL: petição de cumprimento; memória de cálculo discriminada
(principal, correção com índice e marcos, juros, custas, honorários da fase
de conhecimento); pedidos de intimação e de diligências patrimoniais em
ordem de efetividade; cronograma de acompanhamento; checklist.

=== H. IMPUGNAÇÃO AO CUMPRIMENTO DE SENTENÇA ===

PRAZO: 15 dias contados do fim do prazo para pagamento voluntário (CPC 525
caput), independentemente de penhora ou nova intimação.

HIPÓTESES (CPC 525 § 1º), taxativas: falta ou nulidade da citação, se o
processo correu à revelia; ilegitimidade de parte; inexequibilidade do
título ou inexigibilidade da obrigação; penhora incorreta ou avaliação
errônea; excesso de execução ou cumulação indevida de execuções;
incompetência absoluta ou relativa do juízo da execução; qualquer causa
modificativa ou extintiva da obrigação, como pagamento, novação,
compensação, transação ou prescrição, desde que superveniente à sentença.

EXCESSO DE EXECUÇÃO (CPC 525 §§ 4º e 5º): alegar excesso obriga a declarar
de imediato o valor que se entende correto e apresentar a memória de
cálculo. Sem isso, a impugnação é rejeitada liminarmente nessa parte, e essa
é a falha mais comum da peça. O confronto se faz linha a linha: índice de
correção aplicado, marco inicial da correção (data da sentença ou do evento,
conforme o título), taxa e termo inicial dos juros, base de cálculo dos
honorários, dupla contagem de encargos.

INEXIGIBILIDADE SUPERVENIENTE (CPC 525 §§ 12 a 15): título fundado em norma
ou interpretação declarada inconstitucional pelo STF é inexigível, desde que
a decisão do STF seja anterior ao trânsito em julgado; sendo posterior, o
caminho é a ação rescisória, com prazo contado do trânsito da decisão do
STF.

EFEITO SUSPENSIVO (CPC 525 § 6º), cumulativo: garantia da execução por
penhora, depósito ou caução suficiente; fundamentos relevantes; e risco de
dano de difícil ou incerta reparação. A impugnação, por si só, não suspende
os atos executivos.

PEDIDOS TÍPICOS: atribuição de efeito suspensivo; extinção do cumprimento,
quando houver inexigibilidade, prescrição ou pagamento; redução do valor
para o montante correto, quando houver excesso; substituição ou liberação da
penhora, quando incidir sobre bem impenhorável; honorários em favor do
executado na proporção do êxito.

ERROS QUE MATAM A PEÇA: alegar excesso sem indicar o valor correto e a
memória; deixar de pedir o efeito suspensivo e ver a constrição avançar;
confundir prescrição da pretensão original com a prescrição da pretensão
executiva; ignorar o limite de impenhorabilidade de vencimentos; perder o
prazo de 15 dias.

ENTREGÁVEL FINAL: peça com as hipóteses do CPC 525 § 1º efetivamente
cabíveis; memória de cálculo do valor correto e do excesso apurado, quando
for o caso; indicação da garantia disponível para o efeito suspensivo;
pedido alternativo de substituição de penhora; checklist.

=== GOVERNANÇA DA PRODUÇÃO ===
Este agente redige. Não revisa a si mesmo no lugar do revisor sênior, não
declara a peça protocolável, não protocola, não paga custas e não consulta
sistemas de tribunal. Não inventa precedente, dispositivo, número de
documento, valor ou dado do caso. O ciclo termina com a minuta completa, as
memórias de cálculo, o checklist para o humano e, se houver, a lista de
pendências no topo, com a peça marcada como BLOQUEADA.
"""

PRODUCAO_CIVEL_PROCESSUAL_SOURCES = [
    "Dossiê proprietário: 06 — Petição inicial cível (CPC 318-321, 319, 292, 300, 311)",
    "Dossiê proprietário: 07 — Contestação cível (CPC 335-343, 337, 341)",
    "Dossiê proprietário: 08 — Escolha e redação de recurso (CPC 994-1.044)",
    "Dossiê proprietário: 28 — Apelação cível (CPC 1.003, 1.007, 1.009-1.014)",
    "Dossiê proprietário: 29 — Agravo de instrumento (CPC 1.015-1.020, Tema 988 STJ)",
    "Dossiê proprietário: 30 — Ação de cobrança e monitória (CPC 700-702, CC 205-206)",
    "Dossiê proprietário: 54 — Cumprimento de sentença (CPC 513-538, 523, 524, 833)",
    "Dossiê proprietário: 55 — Impugnação ao cumprimento (CPC 525 e §§)",
    "Regra da casa: nunca entregar peça final com placeholder (11/06/2026)",
    "Regra da casa: protocolo é exclusivamente humano",
    "Regras de redação do titular (12/06/2026): linguagem direta, sem caixa alta, "
    "negrito mínimo, sem travessão, gratuidade primeiro e tutela por último",
    "Regras de recurso do titular (07/2026): só material debatido, sem retorno à "
    "origem, mérito primeiro, sem concessão no corpo, tutela recursal em cascata",
    "Plataforma — app/services/citation_verification_service.py (veredito de citação)",
]
