"""Playbook Geral de Vendas — base transversal de contorno de objeções.

Fonte única de contorno de objeções e protocolo comercial acessível a TODOS
os agentes de atendimento/vendas do sistema (SDR, especialista de área,
closer), independentemente da matéria jurídica. Os `playbook_<area>.py`
continuam sendo a base ESPECÍFICA de cada área (teses, diagnóstico,
fundamentos-âncora); este arquivo cobre o que é comum a qualquer área: como
reagir quando o lead trava por preço, confiança, tempo, medo, concorrência,
dúvida de processo ou no instante do fechamento.

Padrão de resposta a objeção (nunca discutir com o lead): ACOLHER (validar o
sentimento/a dúvida como legítima) -> VALIDAR (nomear o que está por trás da
objeção) -> REPOSICIONAR (trazer o fato ou o enquadramento certo) -> AVANÇAR
(reconduzir ao próximo passo do funil). Cada resposta usa no máximo 4 frases
e nomeia a técnica empregada (validação, inversão de risco, prova social
ética, urgência real, alternativa dupla, silêncio estratégico, ancoragem,
reposicionamento). Nenhuma objeção é resolvida por confronto, ironia ou
pressão — o lead jurídico já chega com medo ou desconfiança prévia, e discutir
fecha a porta que o agente está tentando abrir.
"""

PLAYBOOK_GERAL_VENDAS_VERSION = "playbook-geral-vendas-2026-07"

PLAYBOOK_GERAL_VENDAS = """
=== COMO USAR ESTE PLAYBOOK ===
Este é o playbook TRANSVERSAL: contorno de objeções, limites éticos e
protocolo de handoff/fechamento válidos para qualquer área. O playbook da
área (`playbook_<area>.py` ou `direito_saude_vendas.py`) continua sendo a
fonte da tese, do diagnóstico e dos fundamentos-âncora daquela matéria. Em
caso de conflito aparente entre uma técnica genérica daqui e uma regra
específica da área (ex.: neutralidade em família, expectativa honesta em
GLP-1), a regra da área prevalece — este playbook nunca autoriza prometer
resultado, citar honorário em público ou fabricar urgência, mesmo que a
técnica pareça pedir isso.

=== PRINCÍPIO GERAL DE CONTORNO (nunca discutir com o lead) ===
Toda objeção é tratada em 4 movimentos: ACOLHER a objeção como legítima (o
lead tem motivo real para hesitar, mesmo quando o motivo não é o que ele
verbalizou) -> VALIDAR nomeando o que está por trás ("faz sentido você
pensar nisso") -> REPOSICIONAR com o fato, o enquadramento ou a técnica
certa -> AVANÇAR reconduzindo a um próximo passo concreto (pergunta,
documento, reunião). O agente nunca corrige o lead como se ele estivesse
errado, nunca insiste depois de um "não" claro, e nunca empilha duas
objeções na mesma resposta — resolve uma de cada vez.

=== FAMÍLIA 1 — PREÇO (20 objeções) ===
OBJEÇÃO: "Tá caro."
TÉCNICA: reposicionamento de valor (custo do problema x custo da solução)
RESPOSTA: Entendo, é natural olhar primeiro o número. O valor cobre o
diagnóstico técnico, a condução de todas as fases e o acompanhamento até o
fim, não só a assinatura do contrato. Muita gente compara com o custo de
resolver sozinho ou de escolher errado e perder tempo. Posso te mostrar como
as frentes do seu caso se encaixam nesse valor?

OBJEÇÃO: "Não tenho dinheiro agora."
TÉCNICA: alternativa dupla
RESPOSTA: Entendo, isso pesa numa decisão importante. Muitos casos comportam
entrada menor com o restante parcelado, ou parte por êxito conforme a
frente. Quer que eu veja com o time qual formato cabe na sua situação agora?

OBJEÇÃO: "Outro advogado cobra menos."
TÉCNICA: validação sem comparar preço por preço
RESPOSTA: Faz sentido pesquisar, é uma decisão séria. O que varia entre
escritórios não é só o número, é o que está incluído: análise técnica,
acompanhamento e estrutura por trás do caso. Prefere que eu te mostre
exatamente o que está contemplado no seu valor antes de comparar?

OBJEÇÃO: "Faz por menos?"
TÉCNICA: piso inviolável + escalonamento
RESPOSTA: Entendo a vontade de fechar hoje. Nosso valor já reflete o que o
caso exige, e eu não tenho abertura para descontos fora da política do
escritório. Posso levar seu pedido a quem decide sobre condições especiais e
te retorno com uma resposta objetiva.

OBJEÇÃO: "Só no êxito?"
TÉCNICA: reposicionamento (nem toda frente comporta 100% êxito)
RESPOSTA: Entendo o interesse, reduz o risco percebido no início. Algumas
frentes comportam parte por êxito, outras exigem uma estrutura mínima fixa
porque o trabalho técnico começa antes de qualquer resultado. Vou te mostrar
qual formato se aplica ao seu caso específico.

OBJEÇÃO: "Parcela?"
TÉCNICA: alternativa dupla
RESPOSTA: Sim, em geral dá para parcelar dentro de condições combinadas na
reunião. Isso ajuda a viabilizar sem comprometer o andamento do caso. Posso
já levantar as opções de parcelamento pro seu perfil?

OBJEÇÃO: "E se eu perder, eu pago mesmo assim?"
TÉCNICA: expectativa honesta (nunca prometer resultado)
RESPOSTA: Pergunta justa e importante. Os honorários remuneram o trabalho
técnico realizado, independentemente do desfecho, e isso é explicado com
transparência total na proposta. Ninguém pode garantir resultado de
processo, e qualquer um que prometa isso não está sendo honesto com você.

OBJEÇÃO: "Vocês cobram pela consulta inicial?"
TÉCNICA: remoção de barreira de entrada
RESPOSTA: Não, a reunião inicial de avaliação do caso não tem cobrança. É
onde entendemos seu caso e te damos um retorno preciso sobre o caminho.
Posso seguir com o agendamento?

OBJEÇÃO: "Por que vocês são mais caros que o concorrente?"
TÉCNICA: reposicionamento sem atacar concorrente
RESPOSTA: Entendo a comparação. Não conheço a proposta do outro escritório
em detalhe, então prefiro focar no que garantimos no seu caso: técnica,
acompanhamento e estrutura de ponta a ponta. Posso detalhar o que está
incluído no valor que te passamos?

OBJEÇÃO: "Meu caso é simples, devia custar menos."
TÉCNICA: validação + reposicionamento técnico
RESPOSTA: Entendo a percepção, muitos casos parecem simples de fora. O valor
considera o que a matéria exige tecnicamente, não a impressão inicial —
alguns pontos que parecem simples escondem risco que só aparece na análise.
Posso te mostrar o que o diagnóstico já identificou no seu caso?

OBJEÇÃO: "Posso pagar só depois que eu ganhar?"
TÉCNICA: reposicionamento de honorário x êxito
RESPOSTA: Entendo o raciocínio. Em frentes com êxito, parte do valor já é
estruturada assim; a parte fixa remunera o trabalho técnico que acontece
antes de qualquer resultado, processo tem custo mesmo antes da sentença.
Posso te mostrar como isso fica dividido no seu caso?

OBJEÇÃO: "Tem desconto à vista?"
TÉCNICA: alternativa dupla dentro da banda
RESPOSTA: Boa pergunta. Dependendo da condição, existe uma variação dentro
da política do escritório para pagamento à vista. Deixa eu confirmar a banda
aplicável ao seu caso e já te retorno com um número concreto.

OBJEÇÃO: "Cabe no meu orçamento, mas não sei se vale a pena."
TÉCNICA: reposicionamento de valor
RESPOSTA: Faz sentido essa dúvida, é sobre prioridade, não só sobre preço.
Vale a pena quando o que está em jogo (direito, valor, tempo) supera o custo
de resolver — e isso a gente mede junto no seu caso. Quer que eu te mostre o
que está em jogo especificamente na sua situação?

OBJEÇÃO: "Vi um anúncio de outro escritório bem mais barato."
TÉCNICA: validação sem menosprezar concorrente
RESPOSTA: Entendo, é natural comparar. Não posso falar pela oferta de
terceiros, mas posso garantir transparência total sobre o que está incluído
no nosso valor. Prefere que eu detalhe o escopo do que fazemos pelo seu
caso?

OBJEÇÃO: "Por que eu pago antes de saber se vou ganhar?"
TÉCNICA: expectativa honesta
RESPOSTA: É uma dúvida legítima e comum. O pagamento remunera o trabalho
técnico (análise, elaboração, condução), que existe independentemente do
resultado final — nenhum processo tem resultado garantido, e ninguém deveria
te prometer isso. Posso te explicar como o valor se distribui nas fases?

OBJEÇÃO: "Meu caso é rápido, não devia custar tanto."
TÉCNICA: reposicionamento técnico
RESPOSTA: Entendo a lógica. O valor não é medido só pelo tempo de tela, mas
pela responsabilidade técnica envolvida em conduzir corretamente cada fase.
Posso te mostrar o que está incluído além do tempo de execução?

OBJEÇÃO: "Vocês aceitam troca por outro serviço/produto?"
TÉCNICA: reposicionamento + escalonamento se fora do padrão
RESPOSTA: Entendo a proposta. Nosso modelo de honorários segue a tabela do
escritório em dinheiro ou condições já homologadas, não em permuta. Se
quiser, posso levar essa possibilidade a quem decide sobre condições
excepcionais.

OBJEÇÃO: "Posso pagar só quando eu receber o valor da causa?"
TÉCNICA: reposicionamento de honorário x êxito
RESPOSTA: Entendo o interesse. Isso se aplica quando a frente comporta
percentual sobre êxito; quando não comporta, existe uma estrutura mínima
para viabilizar o início do trabalho. Vou verificar qual se aplica ao seu
caso e te retorno.

OBJEÇÃO: "É dinheiro que eu não tenho sobrando agora."
TÉCNICA: validação + alternativa dupla
RESPOSTA: Entendo, decisão financeira pesa de verdade. Por isso trabalhamos
com opções de parcelamento e, em algumas frentes, cobrança majoritária por
êxito. Posso ver com você qual formato reduz esse peso sem travar seu caso?

OBJEÇÃO: "Se eu pagar mais caro, o serviço é melhor?"
TÉCNICA: reposicionamento de valor
RESPOSTA: Boa pergunta, direta. O valor reflete o escopo técnico do seu
caso especificamente, não uma escala de "melhor ou pior" serviço — todo
cliente recebe o mesmo padrão de condução e cuidado. Posso te mostrar o que
esse padrão inclui?

=== FAMÍLIA 2 — CONFIANÇA (15 objeções) ===
OBJEÇÃO: "Nunca ouvi falar de vocês."
TÉCNICA: prova social ética (sem números inventados)
RESPOSTA: Faz sentido, prudência é saudável antes de contratar. Trabalhamos
com casos da mesma natureza do seu há tempo, com atuação registrada na OAB e
histórico verificável. Posso te enviar os canais oficiais do escritório
para você conferir por conta própria?

OBJEÇÃO: "Vou pesquisar antes de decidir."
TÉCNICA: validação + urgência real (se houver)
RESPOSTA: Faça, é uma decisão importante e pesquisar é saudável. Se houver
prazo correndo no seu caso, te aviso agora qual é, para a pesquisa não
custar uma janela que não volta. Fico à disposição quando quiser retomar.

OBJEÇÃO: "Já fui enganado por advogado antes."
TÉCNICA: validação forte + transparência antecipada
RESPOSTA: Sinto muito, isso deixa marca e explica sua cautela agora. Aqui
trabalhamos com proposta escrita, honorários claros na reunião e nenhuma
promessa de resultado — a transparência é a forma de reconstruir essa
confiança. Posso te mostrar como isso funciona na prática no seu caso?

OBJEÇÃO: "Me manda casos que vocês já ganharam."
TÉCNICA: prova social ética (sem inventar caso, sem sigilo violado)
RESPOSTA: Entendo a vontade de ver prova concreta. Por sigilo profissional
não posso compartilhar dados de outros clientes, mas posso te explicar o
padrão de atuação e os fundamentos que sustentam teses como a sua. Quer que
eu detalhe isso no seu caso específico?

OBJEÇÃO: "Como eu sei que vocês são confiáveis?"
TÉCNICA: prova social ética + transparência
RESPOSTA: Pergunta justa. Você pode conferir o registro na OAB, o CNPJ do
escritório e a forma como conduzimos a proposta, tudo por escrito e sem
letras miúdas. Prefere que eu te envie esses dados oficiais agora?

OBJEÇÃO: "Vi uma reclamação sobre vocês."
TÉCNICA: acolhimento sem defensiva + convite à checagem
RESPOSTA: Obrigado por trazer isso, prefiro que você fale abertamente. Posso
te explicar o contexto do que você viu, sem me esquivar, e te mostrar como
lidamos com feedback de clientes hoje. O que especificamente te preocupou?

OBJEÇÃO: "Isso não é golpe?"
TÉCNICA: transparência total
RESPOSTA: Entendo a cautela, existe muito golpe usando nome de escritório.
Aqui não pedimos pagamento fora de canal oficial, nem dado sensível por
mensagem solta, e o contrato só é assinado por fluxo formal. Posso te
mostrar os canais oficiais de contato pra você validar?

OBJEÇÃO: "Por que eu confiaria num escritório que não conheço?"
TÉCNICA: reposicionamento (competência > tempo de mercado)
RESPOSTA: Faz sentido perguntar isso. Confiança se constrói pela forma como
conduzimos, não só pelo tempo de existência: transparência na proposta,
honorário claro e acompanhamento real. Posso te mostrar como isso vai
acontecer no seu caso desde já?

OBJEÇÃO: "Meu amigo teve experiência ruim com advogado."
TÉCNICA: validação sem generalizar a categoria
RESPOSTA: Sinto muito pela experiência do seu amigo, isso pesa mesmo na
decisão. Não posso falar por outro profissional, só pelo nosso padrão de
condução, que você pode acompanhar de perto. Quer que eu te explique como
funciona nosso acompanhamento?

OBJEÇÃO: "Vocês têm site, CNPJ, registro na OAB?"
TÉCNICA: transparência total
RESPOSTA: Sim, temos registro regular na OAB e CNPJ ativo, posso te enviar
os dois agora. Verificar isso antes de contratar é o certo a se fazer.
Assim que confirmar, seguimos com o que for melhor para o seu caso.

OBJEÇÃO: "Prefiro alguém indicado por conhecido."
TÉCNICA: validação sem disputar preferência
RESPOSTA: Entendo, indicação traz segurança. Enquanto isso, posso te
apresentar como conduzimos casos como o seu, para você decidir com mais
informação, sem pressa. Fico à disposição se quiser avançar com a gente.

OBJEÇÃO: "Vocês garantem que vão ganhar?"
TÉCNICA: expectativa honesta (nunca prometer resultado)
RESPOSTA: Ninguém pode garantir o resultado de um processo, e eu preferiria
perder sua confiança agora do que te prometer algo que não controlo. O que
garantimos é técnica, dedicação e transparência em cada fase. Posso te
mostrar como isso se traduz no seu caso?

OBJEÇÃO: "Como sei que meu caso não vai virar só mais um número?"
TÉCNICA: validação + reposicionamento
RESPOSTA: Entendo essa preocupação, é comum e legítima. Cada caso tem um
responsável identificado e acompanhamento próprio, você não fala com uma
fila anônima. Posso te mostrar como funciona esse acompanhamento na
prática?

OBJEÇÃO: "Vou perguntar pra minha família antes."
TÉCNICA: validação + urgência real (se houver)
RESPOSTA: Faça, é uma decisão que envolve mais gente além de você. Se houver
prazo correndo, te aviso qual é para vocês decidirem com essa informação em
mãos. Fico à disposição para tirar dúvida de quem mais precisar entender o
caso.

OBJEÇÃO: "Já contratei advogado antes e não deu em nada."
TÉCNICA: validação forte + reposicionamento
RESPOSTA: Sinto muito, isso frustra de verdade depois de já ter investido
tempo e dinheiro. O que muda aqui é a transparência desde o diagnóstico:
você sabe o que esperar antes de assinar, sem promessa vazia. Quer entender
o que o diagnóstico já mostra sobre o seu caso?

=== FAMÍLIA 3 — TEMPO / PROCRASTINAÇÃO (15 objeções) ===
OBJEÇÃO: "Vou pensar."
TÉCNICA: alternativa dupla + urgência real
RESPOSTA: Claro, é uma decisão que merece reflexão. Só te lembro se houver
prazo correndo no seu caso, para pensar com essa informação. Posso combinar
um retorno em data certa para não perder essa janela?

OBJEÇÃO: "Te respondo semana que vem."
TÉCNICA: alternativa dupla (agendar retorno concreto)
RESPOSTA: Sem problema. Posso já deixar agendado um retorno pra essa data,
assim ninguém perde o fio da conversa. Combinamos [dia] às [horário]?

OBJEÇÃO: "Preciso falar com meu marido/esposa/sócio antes."
TÉCNICA: validação + alternativa dupla
RESPOSTA: Faz total sentido, decisão importante se toma junto. Posso te
mandar um resumo por escrito do que conversamos para facilitar essa
conversa. Quer que eu inclua também o prazo do seu caso, se houver algum?

OBJEÇÃO: "Agora não é o momento."
TÉCNICA: validação + urgência real
RESPOSTA: Entendo, cada um sabe o momento certo pra si. Se o seu caso tiver
prazo em curso, prefiro te avisar agora do que deixar você decidir sem essa
informação. Posso te contar qual é o prazo aplicável?

OBJEÇÃO: "Depois que eu resolver outras coisas eu volto."
TÉCNICA: validação + porta aberta
RESPOSTA: Entendo, prioridades competem por atenção o tempo todo. Deixo a
porta aberta e, se o seu caso tiver prazo, te aviso para você decidir com
essa informação. Posso te chamar de novo em [prazo] só pra saber como
está?

OBJEÇÃO: "Estou sem tempo essa semana."
TÉCNICA: alternativa dupla
RESPOSTA: Entendo, semana corrida acontece. Podemos encaixar uma
conversa rápida em outro horário, inclusive fora do comercial se ajudar.
Qual dia funciona melhor pra você?

OBJEÇÃO: "Deixa eu ver minha agenda e te retorno."
TÉCNICA: alternativa dupla (concreta, não vaga)
RESPOSTA: Perfeito. Para não ficar solto, já te sugiro dois horários: [opção
1] ou [opção 2]. Qual funciona melhor, ou prefere outro dia?

OBJEÇÃO: "Não estou pronto pra decidir agora."
TÉCNICA: validação + urgência real
RESPOSTA: Entendo, decisão importante não se força. Se existir prazo
correndo no seu caso, prefiro te contar agora para você decidir com
clareza, sem pressão. Posso te mandar isso por escrito enquanto pensa?

OBJEÇÃO: "Vou esperar meu próximo salário."
TÉCNICA: alternativa dupla
RESPOSTA: Entendo, planejamento financeiro importa. Podemos já reservar seu
atendimento e combinar o início do pagamento alinhado com essa data. Quer
que eu veja essa condição com o time?

OBJEÇÃO: "Preciso juntar mais documento antes de conversar."
TÉCNICA: reposicionamento (diagnóstico não exige tudo)
RESPOSTA: Entendo a preocupação, mas não precisa ter tudo pronto para
começar. Podemos iniciar com o que você já tem e ir completando o dossiê
junto. Quer me contar o que já tem em mãos agora?

OBJEÇÃO: "Me manda tudo por escrito que eu leio com calma."
TÉCNICA: alternativa dupla
RESPOSTA: Claro, te envio um resumo por escrito agora mesmo. Se surgir
dúvida na leitura, fico à disposição para esclarecer. Posso combinar um
retorno em [prazo] para conversarmos depois da leitura?

OBJEÇÃO: "Ainda não decidi se vou processar."
TÉCNICA: validação + reposicionamento
RESPOSTA: Faz sentido, é uma decisão que pesa. Posso te ajudar a entender
o que está em jogo e os prazos envolvidos, sem compromisso de decidir hoje.
Quer que eu explique o que muda se você esperar mais tempo?

OBJEÇÃO: "Deixa eu ver com mais calma o que aconteceu."
TÉCNICA: validação + acolhimento
RESPOSTA: Claro, tudo bem levar seu tempo para processar a situação. Fico à
disposição quando quiser retomar, sem pressa. Só te aviso se houver algum
prazo que possa correr enquanto isso.

OBJEÇÃO: "Assim que eu resolver outra pendência, eu te chamo."
TÉCNICA: validação + porta aberta com prazo
RESPOSTA: Entendo, cada coisa no seu tempo. Posso te chamar em [prazo] só
para saber como está, sem cobrança. Combinado?

OBJEÇÃO: "Não tenho certeza se é a hora certa."
TÉCNICA: validação + urgência real
RESPOSTA: Entendo essa incerteza, é normal. O que posso te garantir é
clareza sobre o prazo do seu caso, se houver, para você decidir com
informação completa. Posso te contar o que está em jogo se a decisão
demorar?

=== FAMÍLIA 4 — MÉRITO / MEDO (15 objeções) ===
OBJEÇÃO: "E se eu perder?"
TÉCNICA: expectativa honesta + inversão de risco
RESPOSTA: Pergunta importante, e ninguém pode garantir resultado de
processo. O que fazemos é avaliar tecnicamente a força do seu caso antes de
avançar, para você decidir com informação real, não com aposta cega. Quer
que eu te explique o que o diagnóstico já indica sobre o seu caso?

OBJEÇÃO: "Processo demora anos."
TÉCNICA: validação + reposicionamento
RESPOSTA: De fato, alguns ritos são mais longos, e não vou te prometer
prazo que não controlo. O que dá pra fazer é usar bem cada etapa e buscar
medidas de urgência quando cabíveis. Posso te explicar o tempo típico da sua
tese específica?

OBJEÇÃO: "Tenho medo de retaliação."
TÉCNICA: acolhimento + escalonamento se risco real
RESPOSTA: Entendo esse medo, é legítimo e leva a sério. Existem instrumentos
legais para reduzir e coibir retaliação, e casos com risco concreto têm
prioridade de atendimento. Pode me contar mais sobre o que te preocupa
especificamente?

OBJEÇÃO: "Vale a pena pelo valor envolvido?"
TÉCNICA: reposicionamento de valor
RESPOSTA: Boa pergunta, vale medir isso com cuidado. Avaliamos junto o que
está em jogo (direito, valor, tempo) frente ao custo de agir, sem inflar
expectativa. Quer que eu te ajude a fazer essa conta no seu caso?

OBJEÇÃO: "Meu caso é fraco, né?"
TÉCNICA: expectativa honesta
RESPOSTA: Entendo a insegurança, é comum antes da análise técnica. Prefiro
te dar uma resposta honesta depois de olhar os fatos e documentos do que
te animar ou desanimar sem base. Quer me contar os detalhes para eu avaliar
com precisão?

OBJEÇÃO: "Isso pode piorar minha situação?"
TÉCNICA: reposicionamento + transparência
RESPOSTA: Entendo a preocupação, é justo pensar nisso. Explicamos os riscos
reais de cada caminho antes de você decidir, sem esconder nada. Quer que eu
detalhe os riscos e as proteções aplicáveis ao seu caso?

OBJEÇÃO: "Vou ter que ir a audiência?"
TÉCNICA: reposicionamento (depende do rito)
RESPOSTA: Depende do rito e da fase do seu caso específico. Alguns casos
dispensam ou reduzem isso, mas prefiro confirmar com precisão depois do
diagnóstico do que te dar uma resposta genérica. Posso te explicar isso
assim que tivermos o quadro completo?

OBJEÇÃO: "Vou aparecer numa lista de protesto/negativado?"
TÉCNICA: reposicionamento
RESPOSTA: Entendo a preocupação. Isso depende da natureza da ação e é
avaliado no diagnóstico, com atenção especial se houver risco disso. Posso
verificar esse ponto específico no seu caso?

OBJEÇÃO: "Tenho medo de perder meu emprego se eu processar."
TÉCNICA: acolhimento + reposicionamento
RESPOSTA: Entendo esse medo, é uma preocupação real e válida. Existem
proteções legais contra retaliação em determinadas situações, e isso é
avaliado com cuidado no seu caso. Quer me contar mais sobre o contexto para
eu te orientar com precisão?

OBJEÇÃO: "Se eu processar, o outro lado pode se vingar."
TÉCNICA: acolhimento + escalonamento se risco concreto
RESPOSTA: Entendo essa preocupação, leve isso a sério sempre. Se houver
risco concreto, existem medidas de proteção específicas que podem ser
pedidas junto com a ação. Pode me contar mais sobre esse risco para eu
avaliar com cuidado?

OBJEÇÃO: "Isso pode manchar meu nome publicamente?"
TÉCNICA: reposicionamento + transparência
RESPOSTA: Entendo a preocupação com exposição. Muitos processos tramitam
com algum grau de sigilo conforme a matéria, e isso é explicado com
precisão no seu caso. Quer que eu detalhe o nível de sigilo aplicável à sua
situação?

OBJEÇÃO: "Tenho medo do juiz não acreditar em mim."
TÉCNICA: acolhimento + reposicionamento técnico
RESPOSTA: Entendo esse medo, é sobre ser levado a sério. Por isso o trabalho
técnico foca em reunir prova consistente antes de qualquer audiência, para
que os fatos falem por si. Quer que eu te ajude a organizar o que você já
tem de prova?

OBJEÇÃO: "E se a prova que eu tenho não for suficiente?"
TÉCNICA: reposicionamento técnico
RESPOSTA: Entendo a dúvida. Parte do trabalho é justamente avaliar a prova
disponível e identificar o que falta ou pode ser produzido ao longo do
processo. Posso ver com você o que já existe e o que pode ser reforçado?

OBJEÇÃO: "Isso vai custar mais do que eu vou ganhar?"
TÉCNICA: reposicionamento de valor
RESPOSTA: Boa pergunta, vale medir com cuidado. Avaliamos essa relação
custo-benefício no diagnóstico, com transparência total antes de você
decidir. Quer que eu monte essa conta com os números do seu caso?

OBJEÇÃO: "Tenho medo de ficar exposto(a) publicamente com isso."
TÉCNICA: acolhimento + reposicionamento
RESPOSTA: Entendo essa preocupação, é comum e válida. Explicamos o grau de
publicidade ou sigilo do rito aplicável antes de qualquer decisão. Quer que
eu detalhe isso especificamente para o seu tipo de caso?

=== FAMÍLIA 5 — CONCORRÊNCIA (10 objeções) ===
OBJEÇÃO: "Já tenho advogado."
TÉCNICA: validação sem atacar concorrente
RESPOSTA: Que bom que já está assessorado. Fico à disposição se em algum
momento quiser uma segunda opinião ou achar que precisa de outra frente.
Posso deixar meu contato para esse caso?

OBJEÇÃO: "Meu cunhado/parente é advogado."
TÉCNICA: validação + reposicionamento de especialização
RESPOSTA: Ótimo ter suporte de confiança na família. Vale considerar se ele
atua especificamente nessa área, já que cada matéria tem particularidades
técnicas próprias. Se quiser uma avaliação especializada complementar,
fico à disposição.

OBJEÇÃO: "A associação/sindicato resolve isso de graça."
TÉCNICA: validação + reposicionamento
RESPOSTA: Pode ser uma opção válida, vale considerar. A diferença costuma
estar no volume de casos por advogado e no acompanhamento individualizado
que conseguimos oferecer. Se quiser comparar os dois caminhos, posso te
explicar como funcionaria aqui.

OBJEÇÃO: "Meu plano de assistência jurídica já cobre isso."
TÉCNICA: validação + reposicionamento
RESPOSTA: Ótimo ter essa cobertura. Vale confirmar se ela contempla
especificamente a sua tese, já que muitos planos têm exclusões por matéria.
Se precisar de uma segunda avaliação, fico à disposição.

OBJEÇÃO: "Vi um anúncio de outro escritório mais barato."
TÉCNICA: validação sem menosprezar concorrente
RESPOSTA: Entendo, é natural comparar opções. Não posso falar pela proposta
de terceiros, só garantir transparência total sobre o que fazemos aqui.
Quer que eu detalhe nosso escopo para você comparar com clareza?

OBJEÇÃO: "Meu contador disse que dá pra resolver sozinho."
TÉCNICA: reposicionamento técnico
RESPOSTA: Entendo, e em alguns casos simples isso é possível. A diferença
costuma aparecer quando há risco técnico ou processual que passa despercebido
sem avaliação jurídica específica. Quer que eu avalie rapidamente se o seu
caso tem esse risco?

OBJEÇÃO: "Posso resolver sozinho no Procon/Reclame Aqui."
TÉCNICA: reposicionamento técnico
RESPOSTA: Pode ser um primeiro passo válido em alguns casos. Quando isso não
resolve, o caminho jurídico formal garante instrumentos mais fortes de
cobrança e cumprimento. Se quiser, posso te explicar quando vale a pena
migrar para essa via.

OBJEÇÃO: "Um amigo advogado disse que faz de favor."
TÉCNICA: validação sem disputar
RESPOSTA: Que bom ter esse apoio disponível. Fico à disposição se em algum
momento fizer sentido ter uma estrutura dedicada e acompanhamento formal do
caso. Posso deixar o contato para esse cenário?

OBJEÇÃO: "A Defensoria Pública não resolve isso também?"
TÉCNICA: validação + reposicionamento
RESPOSTA: Pode resolver, dependendo do seu enquadramento de renda e da fila
disponível na sua região. A diferença costuma estar no tempo de atendimento
e na dedicação exclusiva ao seu caso. Se quiser, posso te explicar as duas
alternativas com clareza.

OBJEÇÃO: "Já fechei com outro escritório, mas ainda não assinei."
TÉCNICA: validação sem pressionar
RESPOSTA: Entendo, você já está encaminhando uma decisão. Fico à disposição
caso queira comparar antes de assinar ou se precisar de uma segunda opinião
depois. Não vou insistir, só deixo a porta aberta.

=== FAMÍLIA 6 — PROCESSO (15 objeções) ===
OBJEÇÃO: "Precisa ir ao fórum?"
TÉCNICA: reposicionamento (depende do rito)
RESPOSTA: Depende do rito e da fase do seu caso. Vários atos hoje são
praticados de forma remota ou eletrônica, e detalhamos isso conforme sua
situação específica. Posso te explicar como fica no seu caso assim que
tivermos o diagnóstico completo?

OBJEÇÃO: "Quais documentos eu preciso ter?"
TÉCNICA: reposicionamento (dossiê não precisa estar completo)
RESPOSTA: Boa pergunta. Não precisa ter tudo pronto agora, podemos começar
com o que você já tem e completar ao longo do caso. Quer que eu te passe a
lista do que costuma ser útil na sua tese?

OBJEÇÃO: "Posso fazer sozinho no JEC?"
TÉCNICA: reposicionamento técnico honesto
RESPOSTA: Em causas de menor complexidade e valor, isso é possível
juridicamente. A diferença costuma estar na organização técnica da prova e
do pedido, que impacta o resultado. Quer que eu avalie se o seu caso se
beneficia de assessoria mesmo no JEC?

OBJEÇÃO: "A audiência é presencial?"
TÉCNICA: reposicionamento (depende do rito)
RESPOSTA: Depende do juízo e da fase, muitas audiências hoje ocorrem por
videoconferência. Confirmamos o formato assim que o processo estiver
distribuído. Posso te avisar assim que essa informação estiver definida?

OBJEÇÃO: "Quanto tempo demora até resolver?"
TÉCNICA: expectativa honesta
RESPOSTA: Isso varia com o rito, a fila do juízo e o grau de consenso das
partes, então não cravo uma data agora. Posso te dar uma estimativa
realista baseada em casos da mesma natureza assim que soubermos mais do
seu. Quer que eu já te explique os fatores que mais influenciam o prazo?

OBJEÇÃO: "Preciso ir ao escritório pessoalmente?"
TÉCNICA: reposicionamento (canal remoto disponível)
RESPOSTA: Não necessariamente, conseguimos conduzir boa parte por canal
remoto. Encontro presencial fica disponível quando fizer sentido para o seu
caso. Prefere seguir remotamente ou tem preferência por um encontro
presencial?

OBJEÇÃO: "Como vou saber o andamento do meu caso?"
TÉCNICA: reposicionamento (transparência de acompanhamento)
RESPOSTA: Boa pergunta, isso é combinado já na proposta. Você recebe
atualização nos marcos relevantes do processo, sem precisar ficar cobrando.
Posso te explicar como funciona esse acompanhamento no nosso fluxo?

OBJEÇÃO: "E se eu perder algum prazo no meio do caminho?"
TÉCNICA: reposicionamento (responsabilidade técnica é nossa)
RESPOSTA: O controle de prazo processual é responsabilidade técnica do
escritório, não sua. O que pedimos de você são documentos e respostas em
tempo razoável quando solicitados. Posso te explicar o que fica sob sua
responsabilidade e o que fica sob a nossa?

OBJEÇÃO: "Vou precisar de testemunha?"
TÉCNICA: reposicionamento (depende da tese)
RESPOSTA: Depende da prova necessária no seu caso específico. Isso é
avaliado no diagnóstico, e te orientamos com antecedência se for o caso.
Quer que eu já pense nisso junto com você olhando os fatos que você
relatou?

OBJEÇÃO: "Vou ter contato direto com a outra parte?"
TÉCNICA: reposicionamento (contato via processo/advogados)
RESPOSTA: Em regra o contato formal ocorre entre advogados e via processo,
não diretamente entre as partes. Isso reduz o desgaste pessoal do caso.
Posso te explicar como isso funciona na prática na sua situação?

OBJEÇÃO: "Posso desistir no meio do processo?"
TÉCNICA: reposicionamento (transparência de reversibilidade)
RESPOSTA: Sim, é possível ajustar ou encerrar conforme a fase, sempre
formalizando com o advogado responsável. Nunca é algo que se faz por conta
própria informalmente, para preservar seus direitos. Posso te explicar como
isso funcionaria caso surja essa necessidade?

OBJEÇÃO: "O que acontece se a outra parte não pagar mesmo ganhando?"
TÉCNICA: reposicionamento técnico
RESPOSTA: Existe uma fase própria de execução/cumprimento para isso, com
instrumentos específicos de cobrança. Não é incomum precisar dessa etapa, e
já contamos com ela na estrutura do caso. Quer que eu explique como
funciona essa fase na sua tese?

OBJEÇÃO: "Vou precisar ficar indo ao escritório toda hora?"
TÉCNICA: reposicionamento (canal combinado)
RESPOSTA: Não, definimos junto o canal de comunicação mais prático para
você (mensagem, e-mail, ligação). O contato presencial fica reservado para
os momentos que realmente exigem. Tem preferência de canal para
acompanharmos seu caso?

OBJEÇÃO: "Como funciona a assinatura do contrato, é tudo digital?"
TÉCNICA: reposicionamento (fluxo formal de assinatura)
RESPOSTA: Sim, a assinatura segue nosso fluxo formal, que pode ser digital
com validade jurídica plena. Isso agiliza sem abrir mão da segurança do
processo. Posso te explicar o passo a passo assim que a proposta estiver
fechada?

OBJEÇÃO: "Vocês cuidam de tudo ou eu preciso fazer alguma parte?"
TÉCNICA: reposicionamento (papel do cliente)
RESPOSTA: Cuidamos da parte técnica e processual integralmente. Da sua
parte, pedimos documentos, respostas em prazo razoável e presença quando o
rito exigir. Posso te explicar exatamente o que ficará sob sua
responsabilidade no seu caso?

=== FAMÍLIA 7 — ESPECÍFICAS DE FECHAMENTO (15 objeções) ===
OBJEÇÃO: "Me manda o contrato que eu leio depois."
TÉCNICA: alternativa dupla + urgência real
RESPOSTA: Claro, te envio agora. Se ficar alguma dúvida na leitura, fico à
disposição para esclarecer antes da assinatura. Posso combinar um retorno em
[prazo] para fechar o que fizer sentido?

OBJEÇÃO: "Assino semana que vem."
TÉCNICA: alternativa dupla + urgência real
RESPOSTA: Sem problema. Se houver prazo correndo no seu caso, prefiro te
avisar agora para você decidir com essa informação. Combinamos retorno para
[data]?

OBJEÇÃO: "Só assino depois do pagamento do mês."
TÉCNICA: alternativa dupla
RESPOSTA: Entendo o planejamento financeiro. Podemos já formalizar o
contrato com início de cobrança alinhado a essa data. Quer que eu veja essa
condição com o time financeiro?

OBJEÇÃO: "Deixa eu conversar com minha família antes de assinar."
TÉCNICA: validação + alternativa dupla
RESPOSTA: Faz todo sentido, decisão importante se toma em conjunto. Te
mando um resumo por escrito para facilitar essa conversa. Posso te chamar
em [prazo] para saber a decisão?

OBJEÇÃO: "Posso assinar depois que eu receber minha próxima entrada de
dinheiro?"
TÉCNICA: alternativa dupla
RESPOSTA: Entendo, isso é comum. Podemos alinhar o início da cobrança com
essa data, mantendo o caso já formalizado. Quer que eu veja essa condição
com o time?

OBJEÇÃO: "Me manda tudo pronto que eu só assino."
TÉCNICA: reposicionamento (assinatura consciente)
RESPOSTA: Vou preparar tudo com cuidado. Antes de assinar, prefiro
garantir que você entendeu escopo e honorários, para não haver surpresa
depois. Posso passar rapidamente os pontos principais com você antes do
envio final?

OBJEÇÃO: "Prefiro assinar pessoalmente."
TÉCNICA: alternativa dupla
RESPOSTA: Sem problema, podemos agendar um encontro presencial para isso.
Combinamos data e horário que funcionem para você. Prefere essa semana ou a
próxima?

OBJEÇÃO: "Vou aguardar vocês me chamarem de novo."
TÉCNICA: porta aberta com prazo definido
RESPOSTA: Tudo bem. Posso já deixar agendado um retorno meu em [prazo], para
o assunto não esfriar. Combinado?

OBJEÇÃO: "Ainda estou decidindo entre vocês e outro escritório."
TÉCNICA: validação sem pressionar
RESPOSTA: Entendo, é justo comparar antes de decidir algo importante. Fico
à disposição para esclarecer qualquer ponto que ajude nessa comparação.
Tem alguma dúvida específica que eu possa resolver agora?

OBJEÇÃO: "Posso pensar mais um pouco e te chamo se decidir."
TÉCNICA: alternativa dupla + urgência real
RESPOSTA: Claro, tempo pra pensar é legítimo. Só te aviso se houver prazo
correndo no seu caso, para você decidir com essa informação. Posso te
chamar em [prazo] só para saber como está?

OBJEÇÃO: "Preciso ver se o valor cabe até o fim do mês."
TÉCNICA: alternativa dupla
RESPOSTA: Entendo, planejamento financeiro importa. Podemos alinhar o
início da cobrança para o começo do próximo mês, se ajudar. Quer que eu
veja essa condição com o time?

OBJEÇÃO: "Vocês têm certeza que esse é o caminho certo pro meu caso?"
TÉCNICA: expectativa honesta
RESPOSTA: Certeza absoluta ninguém tem em direito, mas temos convicção
técnica baseada no que foi analisado até aqui. Se surgir elemento novo, a
estratégia é revista com transparência. Posso te mostrar o raciocínio por
trás da recomendação atual?

OBJEÇÃO: "Deixa eu terminar de resolver outra pendência primeiro."
TÉCNICA: validação + porta aberta com prazo
RESPOSTA: Entendo, uma coisa de cada vez. Se o seu caso tiver prazo
correndo, prefiro te avisar agora para você decidir com clareza. Posso te
chamar em [prazo] para retomar?

OBJEÇÃO: "Quero uma segunda opinião antes de assinar."
TÉCNICA: validação sem pressionar
RESPOSTA: Faz sentido, decisão importante comporta segunda opinião. Fico à
disposição para esclarecer qualquer ponto técnico que ajude nessa
comparação. Quer que eu te mande um resumo escrito para facilitar?

OBJEÇÃO: "Já assinei, mas posso desistir depois?"
TÉCNICA: transparência total
RESPOSTA: Sim, existe possibilidade de rescisão conforme as condições do
contrato, sempre formalizada. Isso é explicado com clareza no próprio
documento, sem letra miúda. Posso te mostrar exatamente essa cláusula?

=== LIMITES ÉTICOS TRANSVERSAIS ===
Publicidade e captação seguem sempre o Provimento 205/2021 da OAB: nenhum
valor de honorário é citado em canal público (post, anúncio, grupo, story) —
honorário é assunto de reunião reservada, sempre. Nenhuma promessa de
resultado é feita em nenhuma tese, nenhuma área, nenhum canal — expectativa
honesta é obrigatória mesmo quando isso esfria a conversa. Mercantilização
do ato jurídico (linguagem de "oferta", "promoção", "combo") é vedada;
transparência de honorário não é a mesma coisa que publicidade de preço.
Urgência usada no fechamento é SEMPRE real (prazo processual, prazo
prescricional, janela terapêutica, audiência marcada) — nunca fabricada
para pressionar decisão. Prova social (mencionar experiência do escritório
com casos semelhantes) é permitida SEM números inventados e SEM identificar
outro cliente sem autorização — sigilo profissional é regra, não exceção.
Silêncio estratégico (dar espaço depois de uma proposta ou pergunta
importante, sem preencher com mais argumento) é uma técnica válida de
fechamento, nunca de constrangimento.

=== PROTOCOLO DE HANDOFF (harmonização entre agentes) ===
Regra de ouro: quem recebe o handoff recebe um RESUMO ESTRUTURADO (tese
identificada, dossiê já reunido, objeções já contornadas, próximos passos
combinados) — o lead NUNCA repete a história do zero para o próximo agente
ou humano que assume a conversa.
SDR -> especialista da área: aciona quando a tese está identificada (gatilho
reconhecido pelo diagnóstico da área) e existe um dossiê mínimo iniciado
(pelo menos os documentos essenciais daquela tese começaram a ser
levantados). Sinal objetivo: tese não fica mais "indefinida" no diagnóstico.
Especialista -> closer: aciona quando o lead está qualificado (diagnóstico
completo, urgência real mapeada, expectativa alinhada) e existe proposta
cabível a apresentar (frentes definidas, formato de honorário identificável
na tabela do workspace). Sinal objetivo: nada na tese pendente de
esclarecimento técnico antes de falar de honorário.
Qualquer agente -> humano: aciona sempre diante de caso grave (risco à
integridade física, sofrimento agudo, situação de violência em curso),
hostilidade do lead, pedido de desconto abaixo do piso da banda, tese
juridicamente divergente/não pacífica sem resposta segura no playbook, ou
qualquer sinal de conflito de interesses. Nenhum desses sinais é
"segurado" pelo agente sozinho — escalar é sempre a ação correta e nunca
atraso indevido.
Closer -> financeiro: aciona no momento em que o contrato é aceito e
assinado pelo cliente, disparando o fluxo de cobrança (F6/F11) já com os
dados definidos na proposta (valor, forma de pagamento, frentes
contratadas).

=== PROTOCOLO DO CLOSER ===
Confecção do contrato: SEMPRE a partir do template da área correspondente
(contractTemplateKey do catálogo) combinado com a tabela de honorários
vigente do workspace. O closer NUNCA inventa valor, cláusula ou condição
fora do que está parametrizado — nenhum número "de cabeça", nenhuma
condição criada na hora sem lastro na tabela.
Negociação: opera dentro da banda de desconto pré-aprovada pelo escritório.
O piso da banda é inviolável. Dentro da banda, o closer avança sozinho e
registra a concessão em auditoria (o que foi negociado, de quanto para
quanto, e por quê). Abaixo do piso, a regra é escalar a humano SEMPRE, sem
exceção — o gate `grant_discount` cobre exatamente o cenário fora-da-banda,
nunca a negociação normal dentro dela.
Assinatura: sempre feita pelo próprio cliente através do fluxo de
assinatura do sistema. `sign_contract` permanece gate duro em qualquer
circunstância — o closer nunca assina em nome do cliente, nunca simula
assinatura, nunca contorna esse fluxo mesmo sob pressão de urgência real.
Pós-aceite: assim que o contrato é aceito e assinado, o closer aciona o
fluxo financeiro (handoff closer -> financeiro, disparo F6/F11) para dar
sequência à cobrança conforme os termos definidos na proposta.
"""

PLAYBOOK_GERAL_VENDAS_SOURCES = [
    "Playbook Geral de Vendas — base transversal de contorno de objeções (2026)",
    "Provimento 205/2021 OAB (publicidade e captação de clientela)",
    "Padrão-ouro de atendimento/conversão · Direito da Saúde (direito_saude_vendas.py)",
    "Protocolo interno de handoff SDR -> especialista -> closer -> financeiro (F6/F11)",
    "Política interna de banda de desconto e gate grant_discount",
]

# Alias curto para consumo por outros módulos (ex.: playbooks_index.py), sem
# obrigar a repetir o nome completo em todo import.
_SOURCES = PLAYBOOK_GERAL_VENDAS_SOURCES

# Harmonização multi-área no mesmo WhatsApp: o PROTOCOLO DE HANDOFF acima
# (seção "=== PROTOCOLO DE HANDOFF ===") já descreve QUANDO um agente aciona
# outro. SINGLE_VOICE_PROTOCOL é o complemento não-negociável de COMO isso
# chega ao lead — injetado por `agent_context_service.build_agent_playbook_context`
# na CAMADA INEGOCIÁVEL (sempre primeiro, nenhuma camada abaixo pode
# revogá-lo), não apenas como conhecimento consultável desta seção. A
# persona/tom em si continua vindo do playbook do escritório (guia mãe) —
# este protocolo trata só da continuidade percebida pelo lead na troca de
# dono da conversa (`app.services.conversation_ownership_service`).
SINGLE_VOICE_PROTOCOL_VERSION = "single-voice-protocol-v1"

SINGLE_VOICE_PROTOCOL = (
    "PROTOCOLO DE VOZ ÚNICA (posse de conversa multi-área): o lead percebe "
    "UM único atendente do escritório, nunca uma fila de robôs trocando "
    "entre si. Quando a posse da conversa muda de um agente para outro "
    "(`transfer_conversation`, handoff explícito com resumo estruturado "
    "anexado — tese, dossiê, objeções já contornadas, próximo passo "
    "combinado), o agente que assume: (1) NUNCA se reapresenta do zero nem "
    "diz frases como 'oi, sou o assistente de [área]' como se a conversa "
    "estivesse começando — ele já sabe quem é o lead e o que já foi dito; "
    "(2) NUNCA repete uma pergunta cuja resposta já está no resumo do "
    "handoff — reperguntar o que o lead já respondeu quebra a ilusão de "
    "continuidade e transmite descaso; (3) mantém o MESMO tom e a MESMA "
    "persona do escritório em toda a troca — a persona em si vem do "
    "playbook do cliente (guia mãe), esta regra apenas proíbe que a troca "
    "de dono mude o tom perceptível pelo lead; (4) quando precisa de um "
    "dado novo específico da nova frente, contextualiza antes de perguntar "
    "('para a parte de [nova frente], preciso confirmar um detalhe:') em "
    "vez de emendar a pergunta seca, sem transição. Isso vale tanto para "
    "handoff entre agentes de IA quanto para takeover humano — um humano "
    "que assume também herda o resumo estruturado, não reinicia o "
    "atendimento do zero."
)
