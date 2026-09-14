"""Base de conhecimento verificada — monitoramento DJEN, contagem de prazo e
andamento processual (DataJud).

Condensado de `~/.claude/agents/01-monitor-dje-djen.md`,
`~/.claude/agents/02-lembrete-prazo.md` e
`~/.claude/agents/03-andamento-processual.md` (dossiês jurídicos verificados
do escritório). Fonte de verdade única para os agentes nativos de
monitoramento processual da KLM — os agentes NUNCA devem inventar regra de
contagem de prazo, feriado ou canal de consulta que não esteja aqui; itens
novos exigem atualizar primeiro o dossiê de origem em `~/.claude/agents/`.

Este módulo não faz nenhuma chamada de rede: é apenas texto embutido no
`SYSTEM_PROMPT` dos agentes nativos de monitoramento de prazo/andamento
(stubs de fase posterior). Nenhuma consulta real a DJEN/DataJud é feita a
partir daqui — em produção, toda chamada de rede a esses serviços passa por
`external_delivery_allowed` OU pela flag própria do módulo, fechada por
padrão (contrato fail-closed do sistema).
"""

PROCESSUAL_KNOWLEDGE_VERSION = "processual-monitoramento-knowledge-2026-07"

PROCESSUAL_KNOWLEDGE: str = """\
DOSSIÊ PROCESSUAL — MONITORAMENTO DJEN, PRAZO E ANDAMENTO (fonte primária, não inferir além disto).

1. DJEN — REGIME LEGAL E CANAL DE CONSULTA
- Lei 11.419/2006 art. 4: diário oficial eletrônico tem natureza de publicação \
oficial.
- Lei 11.419/2006 art. 5 § 1: intimação se considera realizada no dia da \
consulta eletrônica; se não consultada em 10 dias corridos, opera ciência \
ficta no 11º dia.
- Resolução CNJ 455/2022: criação do DJEN como diário único nacional.
- Resolução CNJ 569/2024: transição obrigatória — a partir de 16/09/2024 \
todas as intimações do Poder Judiciário vão para o DJEN, salvo exceções \
residuais de STF/STJ (portais próprios).
- Lei 14.195/2021 art. 17: comunicações eletrônicas substituem publicação em \
diário oficial.
- Consulta pública oficial: portal **comunica.pje.jus.br** (interface web, \
busca por OAB, CPF/CNPJ, número de processo ou palavra-chave) e a API \
pública correspondente **comunicaapi.pje.jus.br/api/v1/comunicacao** (JSON, \
mesmos filtros: `numeroOab`, `ufOab`, `dataDisponibilizacaoInicio`, \
`dataDisponibilizacaoFim`). Rotina recomendada: consulta em comunica.pje.jus.br \
1x/dia (ou via API, em script agendado), nunca com intervalo maior — ciência \
ficta de 10 dias corridos não é "tempo extra", é janela de risco.
- Integradores de mercado (Projuris, Astrea, ADVBOX, Legal One) também \
espelham o DJEN; quando o cliente já usa um, o agente classifica o que ele \
coletou em vez de duplicar a consulta.
- Diferenciar sempre: intimação (gera prazo) × publicação de despacho de \
mero expediente (não gera prazo).

2. DATAJUD — ANDAMENTO PROCESSUAL (CNJ, API PÚBLICA)
- Base legal: Resolução CNJ 331/2020 (DataJud, base nacional de dados do \
Judiciário) e Resolução CNJ 185/2013 (PJe).
- Endpoint público: `https://api-publica.datajud.cnj.jus.br/api_publica_<sigla_tribunal>/_search` \
(sigla por tribunal: tjsp, tjmg, trf3, trt2, stj, tst etc.). Autenticação por \
API key gratuita, obtida após cadastro em datajud-wiki.cnj.jus.br/api-publica/.
- Numeração única CNJ (formato NNNNNNN-DD.AAAA.J.TR.OOOO): NNNNNNN = \
sequencial no órgão; DD = dígito verificador; AAAA = ano de autuação; J = \
justiça (1 STF, 2 CNJ, 3 STJ, 4 Federal, 5 Trabalho, 6 Eleitoral, 7 Militar, \
8 Estadual, 9 Militar UF); TR = tribunal; OOOO = órgão de origem.
- Outros canais oficiais por tribunal: e-SAJ (TJSP/TJMG, consulta pública por \
número ou nome da parte), Projudi (TJPR/TJBA, via certificado/login), eproc \
(TRF4/TRT4, consulta pública por número), PJe-JT (Justiça do Trabalho, \
pje.tst.jus.br + TRTs regionais), STF/STJ Push (notificação por e-mail de \
andamento).
- Segredo de justiça: consulta pública (DataJud/e-SAJ/comunica.pje.jus.br) \
não retorna nada — exige certificado digital e cadastro como advogado da \
parte no sistema do tribunal.
- Processo físico residual: pode não constar no DataJud até ser digitalizado \
— consultar diretamente no cartório.

3. STATUS PROCESSUAL — TAXONOMIA
Inicial protocolada → aguardando despacho.
Aguardando citação → petição despachada, AR/oficial pendente.
Aguardando contestação → réu citado, prazo correndo (15 dias úteis, CPC 335).
Em audiência → designada ou em curso.
Aguardando sentença → concluso para sentença.
Com sentença → prolatada, verificar prazo de recurso.
Em recurso → apelação/AI/RE/REsp pendente de julgamento.
Em execução → cumprimento de sentença (CPC 523 e seguintes).
Suspenso → por convenção, sobrestamento ou morte (CPC 313).
Baixado → trânsito em julgado e arquivamento.

GARGALOS (regra: mais de 30 dias corridos sem movimento = revisar; mais de \
60 dias em conclusão = agir):
- Parado em conclusão > 60 dias: emitir certidão de decurso de prazo e \
cobrar (CPC 226).
- Parado em citação > 30 dias: pedir desentranhamento e nova diligência.
- Suspenso por sobrestamento: verificar tema repetitivo e prazo de retorno.
- Cumprimento parado > 30 dias: peticionar pesquisa Bacenjud/Renajud/Infojud.

4. CONTAGEM DE PRAZO — REGRA-MESTRE (dias úteis × corridos)
- Cível, Família, Empresarial, Trabalhista (pós Reforma 2017) e Tributário: \
dias ÚTEIS (**CPC 219**). JEC (Lei 9.099) também em dias úteis a partir de 2017.
- Penal (CPP): dias CORRIDOS (CPP arts. 798-800).
- Eleitoral (CE 184): dias CORRIDOS.
- CLT art. 775 (pós Lei 13.467/2017): prazo trabalhista também em dias úteis.

5. INÍCIO DA CONTAGEM
- Regra geral (**CPC 224** § 2): início no 1º dia útil seguinte à intimação \
eletrônica ou pessoal. Se a publicação cair em dia útil, a contagem começa \
no dia útil imediatamente seguinte; se cair em sábado, domingo ou feriado, \
começa no próximo dia útil.
- CPC 231: a data da intimação é a data da consulta no portal, ou o 11º dia \
da disponibilização (ciência ficta, Lei 11.419/2006 art. 5 § 1) — o que \
ocorrer primeiro.
- CPC 1.003 § 5: recursos contam da intimação pessoal/eletrônica pela mesma \
regra.

6. PRAZOS EM DOBRO
- CPC 183: Fazenda Pública (União, Estados, Municípios, autarquias e \
fundações) — prazo em dobro. Verificar quem é a PARTE, não o advogado.
- CPC 186: Defensoria Pública — prazo em dobro.
- CPC 229: litisconsortes com procuradores diferentes — prazo em dobro no \
processo físico residual; **revogado no PJe pela Súmula 641 do STJ** — \
confirmar caso a caso qual sistema o processo tramita.

7. PRAZOS PRINCIPAIS (tabela de referência — CPC e leis especiais)
Contestação cível .............. 15 dias úteis ...... CPC 335
Réplica ......................... 15 dias úteis ...... CPC 350
Manifestação sobre laudo ........ 15 dias úteis ...... CPC 477
Apelação ........................ 15 dias úteis ...... CPC 1.003 § 5
Agravo de instrumento ........... 15 dias úteis ...... CPC 1.003 § 5
Embargos de declaração .......... 5 dias úteis ....... CPC 1.023
Recurso especial/extraordinário . 15 dias úteis ...... CPC 1.003 § 5
Embargos à execução ............. 15 dias úteis ...... CPC 915
Cumprimento de sentença .......... 15 dias úteis ...... CPC 525
Contestação trabalhista ......... na audiência ....... CLT 847
Recurso ordinário trabalhista .... 8 dias úteis (pós-2017) .. CLT 895
Resposta à acusação criminal ..... 10 dias corridos ... CPP 396
Apelação criminal ................ 5 dias corridos .... CPP 593

8. RECESSO, SUSPENSÃO E FERIADOS
- CPC 220: recesso de 20/12 a 20/01 — prazos suspensos, não se conta nenhum \
dia do período; se a publicação cair no recesso, o início da contagem é \
21/01 (ou o próximo dia útil).
- Feriado forense: cada tribunal define o seu calendário — conferir sempre o \
calendário oficial local antes de calcular; Corpus Christi não é feriado \
nacional, mas é forense em muitos TJs.
- Feriados nacionais fixos por lei (Lei 662/1949 + Lei 6.802/1980 + Lei \
10.607/2002 + Lei 14.759/2023):
  01/01 Confraternização Universal; Carnaval (segunda, terça e quarta de \
  cinzas até meio-dia — feriado nacional desde a Lei 14.759/2023); \
  Sexta-feira Santa (móvel); Páscoa (domingo, não conta); 21/04 Tiradentes; \
  01/05 Dia do Trabalho; 07/09 Independência; 12/10 Nossa Senhora Aparecida; \
  02/11 Finados; 15/11 República; 20/11 Consciência Negra (Lei 14.759/2023); \
  25/12 Natal.
- Erro de publicação (ato sem que o advogado correto tenha sido intimado): \
pedir certidão e devolução de prazo (CPC 224 § 1, justa causa).

9. CLASSIFICAÇÃO DE URGÊNCIA POR TIPO DE ATO
- CRÍTICA (prazo curto, protocolar): contestação 15 dias úteis (CPC 335), \
embargos à execução 15 dias (CPC 915), apelação 15 dias (CPC 1.003 § 5), \
agravo de instrumento 15 dias (CPC 1.003 § 5), embargos de declaração 5 dias \
(CPC 1.023).
- ALTA (10-30 dias): recurso especial/extraordinário 15 dias (CPC 1.003 § \
5), réplica 15 dias (CPC 350), manifestação sobre laudo 15 dias (CPC 477).
- MÉDIA (5-10 dias): ciência de despacho sem prazo gerador imediato mas que \
exige acompanhamento.
- BAIXA: decisão sem ônus, ata sem prazo, expediente cartorário.
Regra de lembrete antecipado: prazo crítico lançado na agenda com aviso a \
D-7 dias úteis antes da data fatal, no mínimo.

10. RÉGUA DE LEMBRETES E MEMÓRIA DE CÁLCULO — REGRA FIXA
- Toda régua de prazo segue D-7 / D-3 / D-1 / D-0, exportável em .ics \
(VCALENDAR/VEVENT por marco) para importação em Google Calendar, Outlook ou \
Apple Calendar. D-0 isolado nunca é suficiente — sem D-7 e D-3 o advogado não \
tem tempo de elaborar peça complexa.
- **Regra crítica e não negociável**: todo cálculo de prazo produzido por \
agente sai acompanhado de **memória de cálculo** auditável, passo a passo — \
data da publicação/intimação, regra de início da contagem (CPC 224/231), \
tipo de contagem (dias úteis × corridos), cada feriado e dia de recesso \
excluído, aplicação ou não de prazo em dobro (CPC 183/186/229), e a data \
fatal resultante com a lei que a sustenta em cada etapa. Cálculo sem essa \
memória de cálculo não é entregável.
- Nenhum prazo calculado por agente se torna prazo fatal lançado no sistema \
sem antes ser **conferível por humano** — o advogado responsável revisa a \
memória de cálculo e confirma antes de o prazo virar compromisso oficial da \
carteira. Agente sozinho não fecha data fatal.

11. ANTI-PADRÕES (qualquer um destes é falha grave)
- Tratar a ciência ficta de 10 dias como "tempo extra" — é janela de risco; \
estabilizar leitura em até 24h da disponibilização.
- Calcular em dias corridos quando o rito exige dias úteis, ou vice-versa.
- Esquecer o recesso de fim de ano (CPC 220) ou o Carnaval como feriado \
nacional (Lei 14.759/2023).
- Ignorar prazo em dobro de Fazenda Pública/Defensoria, ou aplicar o dobro \
de litisconsorte (CPC 229) sem checar se o processo tramita no PJe (onde a \
regra foi revogada pela Súmula 641 do STJ).
- Não diferenciar movimento processual relevante de movimento cartorário \
irrelevante, ou achar que "sem movimento" significa "nada está \
acontecendo" — pode estar concluso e exigir cobrança do cliente.
- Deixar publicação sem leitura acumular — a primeira que passar em branco \
é risco de erro disciplinar (EAOAB art. 32).
- Entregar prazo fatal sem memória de cálculo, ou sem passar por conferência \
humana antes de virar compromisso no sistema.

FONTES:
- https://comunica.pje.jus.br/
- https://comunicaapi.pje.jus.br/api/v1/comunicacao
- https://api-publica.datajud.cnj.jus.br/
- https://datajud-wiki.cnj.jus.br/api-publica/
- https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11419.htm
- https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm
- https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/l14759.htm
- https://atos.cnj.jus.br/atos/detalhar/4629 (Resolução CNJ 455/2022)
- https://atos.cnj.jus.br/atos/detalhar/5870 (Resolução CNJ 569/2024)
- https://atos.cnj.jus.br/atos/detalhar/3316 (Resolução CNJ 331/2020)
"""

PROCESSUAL_KNOWLEDGE_SOURCES: tuple[str, ...] = (
    "https://comunica.pje.jus.br/",
    "https://comunicaapi.pje.jus.br/api/v1/comunicacao",
    "https://api-publica.datajud.cnj.jus.br/",
    "https://datajud-wiki.cnj.jus.br/api-publica/",
    "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11419.htm",
    "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm",
    "https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/l14759.htm",
    "https://atos.cnj.jus.br/atos/detalhar/4629",
    "https://atos.cnj.jus.br/atos/detalhar/5870",
    "https://atos.cnj.jus.br/atos/detalhar/3316",
)
