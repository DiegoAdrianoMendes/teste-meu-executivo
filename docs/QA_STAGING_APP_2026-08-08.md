# Relatório de QA autenticado — Staging App

**Data:** 08/08/2026  
**Ambiente:** `https://staging-app.meuexecutivo.com`  
**API:** `https://staging-api.meuexecutivo.com`  
**Perfil testado:** titular do escritório piloto Kleist Advocacia  
**Estratégia:** regressão autenticada, não destrutiva e sem efeitos externos

## 1. Veredito

**APROVADO COM RESSALVAS para testes internos.**

O shell autenticado, as rotas não relacionadas a mensagens, as principais abas, formulários em modo abrir/cancelar, sessão, estados vazios e breakpoints testados funcionaram. Não houve erro ou warning capturado no console ao final da rodada.

O ambiente ainda não deve ser considerado pronto para aceite final sem tratar os achados P1/P2 deste relatório, principalmente a latência do Financeiro Executivo e as falhas de semântica/acessibilidade.

## 2. Exclusões determinadas pelo titular

Não foram abertos nem exercitados:

- Conversas;
- Grupos;
- Disparos e listas;
- Automações ou ações capazes de enviar/receber mensagens;
- envio de mensagem-teste;
- campanhas, cobranças externas, publicação, webhooks ou qualquer outro efeito externo.

**Nenhuma mensagem foi enviada.**

## 3. Evidência geral

- Login concluído pelo titular no Chrome.
- Sessão permaneceu autenticada após navegação e recarga direta de `/app/configuracoes`.
- API respondeu `status=ok`, `environment=staging`, versão `0.1.0`.
- 26 rotas seguras carregaram sem redirecionamento para login e sem erro visível.
- 42 trocas de abas/visões foram exercitadas.
- 3 formulários foram abertos e cancelados, sem persistência: demanda, processo e lead.
- 48 combinações responsivas foram verificadas em 360, 393, 768, 834, 1024 e 1194 px.
- Nenhuma combinação apresentou overflow horizontal global.
- Console final: zero erros e zero warnings capturados.

## 4. Cobertura de rotas

| Módulo | Resultado | Observação |
|---|---|---|
| Início | Passou | KPIs, agenda, alertas, funil e relatórios renderizados |
| Agenda | Passou | Mês, semana e dia; dados locais; Google corretamente indicado como desconectado |
| Tarefas | Passou com ressalva | Abas, filtros e visões renderizam; sem dados no piloto |
| Publicações e Controladoria | Passou | Painel diário, prazos sugeridos/confirmados e distribuição exibidos |
| Aprovações | Passou | Fila e recursos carregados |
| CRM | Passou | Kanban/tabela e formulário de lead abrir/cancelar |
| Contratos | Passou | Fila e operação local carregadas |
| Demandas | Passou | Formulário abrir/cancelar |
| Processos | Passou | Abas e formulário abrir/cancelar |
| Petições | Passou | Fila e operação local carregadas |
| Conhecimento | Passou | Busca/listagem e leitura de artigo |
| Áreas e times | Passou | Catálogo e detalhe de área |
| Financeiro | Passou com ressalva | Abas, DRE, forecast, valuation, centros, tributação e exportações renderizam |
| KPIs e métricas | Passou com ressalva | Cinco famílias de visão carregam |
| Rastreamento | Passou | Nove abas analíticas carregam, sem execução externa |
| Equipe interna | Passou | Listagem e indicadores do titular carregam |
| Tráfego pago | Passou em leitura | Nenhuma campanha criada |
| Indique e ganhe | Passou | Quatro abas carregam; nenhum link/cupom criado |
| Migração | Passou com ressalva | Wizard renderiza; nenhuma importação iniciada |
| Meus Executivos | Passou com ressalva | Sala e reports renderizam; nenhuma conversa iniciada |
| Agentes | Passou com ressalva | Equipe, hierarquia, aprovações, permissões, autonomia e motor carregam |
| Flows | Passou em leitura | Página carregada; nenhum fluxo criado/publicado |
| Integrações | Passou em leitura | Visão geral, conexões, assistente e mensuração carregam; nenhuma ação externa |
| TI Agent | Passou | Nova solicitação, fila, diagnóstico e acessos carregam; nada enviado |
| Configurações | Passou em leitura | Página e sessão persistem após reload; nenhuma configuração salva |
| Atividade | Passou | Auditoria/processos carregados |

## 5. Financeiro Executivo 360

Comprovado na interface:

- KPIs de recebido, a receber, vencido, inadimplência, MRR e resultado;
- forecast de caixa;
- DRE gerencial dimensional em caixa/competência e mensal/anual;
- histórico de cenários e valuations;
- exportação PDF, XLSX e DOCX;
- centro de custo, cliente, contrato, caso, equipe, responsável e lançamento;
- orçamento por centro de custo;
- perfil tributário;
- simulação da transição 2026–2033;
- exigência textual de revisão humana/contador.

O workspace piloto não possui lançamentos, centros, cenários, valuations ou perfil tributário; por isso não foi possível provar drill-down com dados reais nem a consistência dos totais entre dimensões.

## 6. Responsividade

Páginas representativas testadas em seis larguras:

- Início;
- Agenda;
- Tarefas;
- CRM;
- Financeiro;
- KPIs;
- Agentes;
- Configurações.

Resultado: 48/48 renderizaram com `<main>`, sem tela presa em loading e sem overflow horizontal global.

## 7. Achados

### P1 — Financeiro Executivo demora para concluir consultas iniciais

Na primeira abertura da aba **Visão Financeira**, exportações, forecast, DRE, indicadores, lookups e orçamento permaneceram em `Carregando/Calculando/Buscando` depois de 3,5 segundos. Todos terminaram após aproximadamente mais 8 segundos.

**Impacto:** sensação de travamento no módulo central e risco de cliques repetidos.  
**Recomendação:** medir p95 por endpoint, carregar blocos de forma independente, exibir qualidade/tempo da fonte e evitar que todos os lookups iniciem simultaneamente.

### P1 — Login de staging exibe “Simulação local ativa”

A tela de autenticação de staging apresenta o rótulo **Simulação local ativa**, embora `/health` reporte `environment=staging`.

**Impacto:** diagnóstico operacional enganoso e dúvida sobre qual runtime está ativo.  
**Recomendação:** derivar o rótulo da mesma fonte canônica do `/health` ou removê-lo fora do ambiente local.

### P2 — Estrutura semântica duplicada

- `/app/tarefas` e `/app/executivos` renderizam dois elementos `<main>`;
- Agenda, Tarefas, CRM, Conhecimento, Áreas, Rastreamento, Tráfego pago, Benefícios, Migração, Executivos, Agentes, Integrações e TI possuem mais de um `<h1>`.

**Impacto:** navegação ruim por leitor de tela e estrutura de página ambígua.  
**Recomendação:** um único landmark `<main>` e um único `<h1>` por rota; títulos internos devem iniciar em `<h2>`.

### P2 — Controles sem nome acessível

- Agentes: 137 checkboxes visíveis sem `aria-label`, `title` ou `<label for>` detectável;
- Migração: 7 radios sem nome acessível próprio;
- KPIs: 1 checkbox sem nome acessível.

**Impacto:** funções não operáveis/compreensíveis por tecnologia assistiva.  
**Recomendação:** associar cada controle ao rótulo de negócio e validar com axe e teclado.

### P3 — Estados de tarefa da Agenda misturam português e inglês

O Kanban da Agenda mostra `Done` e `Blocked`, enquanto os demais estados estão em português.

**Recomendação:** apresentar `Concluída` e `Bloqueada`, mantendo enums internos fora da interface.

### P3 — Estado visual de modo em Tarefas não expõe seleção

Os botões Lista, Quadro e Calendário mudam a visão, mas não expõem `aria-pressed` ou estado equivalente.

**Recomendação:** usar tabs ou `aria-pressed` para anunciar a visão ativa.

## 8. Limites da evidência

- Workspace piloto praticamente vazio: números zero foram consultados, mas não foi possível reconciliar dados complexos.
- Nenhuma mutação definitiva foi executada.
- Nenhuma exportação foi solicitada, pois criaria job persistido.
- Nenhum fluxo externo foi exercitado.
- RBAC foi verificado apenas como titular do escritório; não houve matriz multiusuário.
- Não houve teste de mensagens por determinação expressa do titular.

## 9. Próxima rodada recomendada

1. Corrigir os dois P1.
2. Corrigir landmarks, headings e nomes acessíveis.
3. Criar massa sintética isolada no workspace piloto para finanças, tarefas, clientes, contratos e casos.
4. Repetir reconciliação financeira e drill-down até lançamento.
5. Executar matriz de perfis com contas de teste distintas.
6. Manter mensageria excluída até autorização específica.

## 10. Correção e recertificação local

**Meta da correção:** zerar os achados P1/P2/P3 deste relatório sem abrir ou
exercitar Conversas, Grupos, Disparos ou qualquer fluxo de envio/recebimento.

Resultado implementado:

- o rótulo do runtime agora distingue ambiente local, homologação e produção;
- o Financeiro não dispara os seis lookups de alta cardinalidade enquanto não
  houver busca ou valor selecionado, e as consultas estáveis usam cache curto;
- as rotas afetadas mantêm um único `main` e um único `h1` canônico;
- checkboxes e radios apontados pelo scanner receberam nomes acessíveis;
- os modos Lista, Quadro e Calendário expõem `aria-pressed`;
- `Done` e `Blocked` passaram a `Concluídas` e `Bloqueadas` na interface.

Evidência local em Node 24.18.0:

| Gate | Resultado |
|---|---:|
| Testes unitários/integrados afetados | 172/172 |
| Regressão E2E desktop/mobile/tablet | 48/48 |
| Financeiro sintético: 55 registros, drill-down e XLSX | 1/1 |
| Matriz de papéis e Blackbox | incluída nos 172 testes |
| ESLint | zero erros; 30 warnings preexistentes |
| Typecheck real | aprovado |
| Build App + verificação de bundle | aprovado |

O primeiro ensaio da exportação financeira confirmou que o job assíncrono não
termina sem worker. Depois de iniciar API e worker locais reais contra a mesma
base isolada, o cenário passou em 8,3 segundos e removeu a massa sintética ao
final. Nenhuma integração ou entrega externa foi habilitada.

Limite remanescente: a melhora do tempo frio em **staging** deve ser medida
novamente depois da publicação desta correção. O gate local comprova a remoção
do fan-out vazio e o funcionamento completo, mas não substitui o p95 observado
na infraestrutura AWS.
