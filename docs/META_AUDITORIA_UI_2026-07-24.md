# META — Auditoria e correção de UI (2026-07-24)

**Objetivo declarado pelo titular:** auditar o UI do sistema TODO após a
grande expansão de funcionalidades, corrigir tipografia, provar o vínculo
UI↔backend e dar o veredito final de "pronto para subir".

## Diretivas

1. **Tamanho das fontes** — corrigir desproporções (em regra estão maiores do
   que deveriam). O tamanho deve seguir uma **lógica única**: escala
   tipográfica hierárquica aplicada consistentemente em todas as telas.
2. **Tipo de fonte** — ajustar onde necessário (família certa por papel:
   display/heading × corpo × dado tabular/mono).
3. **Não remover NADA do UI** — o sistema está mais completo e a usabilidade
   melhorou; a auditoria corrige apresentação, não subtrai funcionalidade.
4. **UI ↔ backend real** — verificar que cada tela/ação responde de verdade ao
   backend e vice-versa (sem botão morto, sem endpoint órfão, sem dado
   fantasma).
5. **Veredito final** — após as correções, atestar se está perfeito para
   subir (AWS), com evidência.
6. **Não quebrar o preview** — o titular acompanha em tempo real via
   `http://127.0.0.1:4173/app/` (vite preview do build + proxy API 8010).
   Fluxo: editar → `npm run build` → refresh do titular.

## Escala tipográfica canônica (a "lógica")

Base shadcn/Tailwind, tela densa de SaaS B2B:

| Papel | Classe | Uso |
|---|---|---|
| Display | `text-2xl font-semibold` | Título da página (1 por tela) |
| H2 | `text-lg font-semibold` | Título de seção/painel |
| H3 | `text-base font-medium` | Título de card/subseção |
| Corpo | `text-sm` | Texto padrão, formulários, tabelas |
| Suporte | `text-xs text-muted-foreground` | Legendas, hints, metadados |
| KPI valor | `text-2xl font-bold tabular-nums` | Número de destaque em card |
| KPI label | `text-xs uppercase tracking-wide text-muted-foreground` | Rótulo do KPI |

Regras: nenhum `text-3xl`/`text-4xl`+ fora de landing/marketing; 1 display por
tela; corpo NUNCA maior que `text-sm` em tabela/formulário; números tabulares
(`tabular-nums`) em métricas e valores monetários.

## Bugs de vínculo UI↔backend já confirmados nesta auditoria

- **P0 — Branding bloqueia o app deslogado**: `loadBrand` com `preferApi=true`
  chama `/api/v1/configurations/brand.identity/effective` (exige token) antes
  do login; 401 → tela de erro em vez do login. Corrigir para **fail-open**:
  brand padrão (Meu Executivo) quando não autenticado; brand do workspace após
  login. Nunca bloquear a aplicação por identidade visual.

## Plano de execução

1. **Fase A — Levantamento paralelo (código)**: inventário completo de
   tipografia por tela (todas as classes text-*/font-*), inventário de
   chamadas de API do frontend × rotas FastAPI (órfãos nos dois sentidos),
   inventário de botões/ações sem handler ou com handler morto.
2. **Fase B — Correções**: fix P0 branding; normalização tipográfica tela a
   tela contra a escala; fontes por papel. Sem remover nenhum elemento.
3. **Fase C — Prova viva**: build → verificação visual tela a tela no browser
   (logado, workspace dev) → cliques reais nos fluxos principais confirmando
   respostas do backend (network + estado).
4. **Fase D — Gates finais**: tsc, vitest, build, pytest dirigido, scorecard;
   relatório de veredito "pronto para subir" com pendências residuais se
   houver.

## Evidências do titular (anotações ao vivo, 2026-07-24)

1. **Dashboard**: textos saindo dos quadros, desalinhados, tamanhos
   desproporcionais à página, hierarquia de pesos quebrada.
2. **Conversas**: "salada mista". Cabeçalho = sopa de chips sem hierarquia
   (circulado). Nome da pessoa MENOR que o resto do card.
3. **Flows**: "desenho de criança". Itens da paleta de blocos quebram linha e
   colidem entre si (circulado); nós do canvas com texto de 8px.
4. Falta arquitetura/planejamento visual geral.

## Medições renderizadas (prova, 4173 em 2026-07-24)

| Elemento | Renderizado | Alvo |
|---|---|---|
| h1 título da página | 25px w600 | 24px (text-2xl) w600 |
| h2 "Central de atendimento" | **14px** w600 | 18px (text-lg) w600 |
| Pill "Dados persistidos" | **16px** w600 | 12px (text-xs) w500 |
| Pills "20 conversas"/"7 atrasadas" | 10px | 12px (text-xs) — uniformes |
| Card: nome do cliente | **11px** w600 | 14px (text-sm) w600 — âncora |
| Card: assunto ("home care") | **16px** w400 | 12px (text-xs) muted |
| Card: banner "Aguardando cliente" | 16px w600 | 12px (text-xs) w600 compacto |

Diagnóstico: não há inflação de root (html 16px); o problema é uso arbitrário
por componente (text-[8px] a text-[28px]) invertendo a hierarquia.

## ⛔ ZONA INTOCÁVEL (ordem do titular, 2026-07-24)

**O menu lateral esquerdo está PERFEITO — não alterar.** Nenhuma mudança em
AppSidebar/sidebar (classes, tamanhos, cores, espaçamento, nada). Qualquer
edição feita por engano deve ser revertida. Vale para todas as ondas.

## Correções estruturais da Fase B

- **Primitives.tsx é a fundação**: KlmSectionTitle 17px→text-lg; descrição
  11px→text-xs; KlmMetricCard label 11px→text-xs, valor 28px→text-2xl
  tabular-nums; KlmPill 10px→text-xs uniforme.
- **ConversationsPage**: cabeçalho reorganizado (título text-lg + UMA linha de
  stats em chips text-xs uniformes + descrição text-xs truncada); anatomia do
  card: nome text-sm font-semibold como âncora, assunto text-xs muted, banner
  compacto text-xs, "Potencial" text-xs.
- **FlowsPage/paleta**: item = linha flex com label truncado (line-clamp-1) +
  botão "+" shrink-0 alinhado, min-h fixa, sem sobreposição; nós do canvas:
  piso tipográfico 10px (text-2xs), nunca 8-9px.
- Flake conhecida: `case360-contextual-editing` (timeout waitFor) — estabilizar
  na Fase D.

## Critério de aceite

- Zero tela fora da escala tipográfica; zero fonte fora do papel.
- Zero elemento removido (diff de UI é só de classes/estilo, não de estrutura).
- Fluxos principais provados contra o backend com evidência (screenshot +
  status HTTP).
- Suítes verdes + build ok + preview 4173 do titular funcionando o tempo todo.
