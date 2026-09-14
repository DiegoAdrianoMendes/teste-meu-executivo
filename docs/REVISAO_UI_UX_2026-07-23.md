# Revisão Sênior de UI/UX — Backlog de Upgrades (23/07/2026)

Revisão read-only do sistema inteiro (shell KLM ~27,5k linhas, PlatformDashboard 778 linhas/17 abas, RBAC com 14 pacotes de acesso). Nada foi alterado; este documento é o backlog priorizado da onda de polish visual, sob as diretrizes 7 e 8 (Super Admin enxerga tudo; UI só acrescenta).

## TOP 10 (ordem de execução recomendada)

1. **Eliminar todo vestígio "saúde"** (copy "Operação saúde", ícone Stethoscope, "jurídico-saúde", placeholders de plano de saúde) — HomePage.tsx:232, LeadPage.tsx:333, AudioPage.tsx:224, InternalPage.tsx:47-49, KlmShell.tsx:357, navigation.ts:63, PlaybooksPage.tsx:155. Advogado de outra área perde confiança na demo. [P]
2. **Piso tipográfico 11px** (hoje text-[8px]/[9px]/[10px] em massa: AgendaPage 78×, ConversationsPage 73×; ilegível para sócios e reprova contraste). [M]
3. **Gráficos reais** (Recharts): sparklines nos KlmMetricCard da Home, linha de receita atribuída no Tracking. Hoje zero charts num produto de R$ 997. [M-G]
4. **Home adaptativa por pacote de acesso**: mapa pacote→ordem de blocos reordenando painéis existentes (sócio: receita/ROI/aprovações; advogado: casos+prazos do dia; secretaria: inbox+agenda+leads quentes; marketing: aquisição/tracking). RBAC já suporta (workspace_access_packages, can(), PermissionScope own/team). Nada removido, só priorizado. [M]
5. **Copy de negócio** no lugar de jargão ("persistido no banco local", "idempotencyKey" → "Solicitação registrada"). [P]
6. **Skeletons com shimmer** no shell (hoje "Carregando..." em texto). [P]
7. **Bottom bar mobile por papel** (hoje TI/Áreas ocupam vagas de Agenda/Funil; TI some sem admin.view). [P]
8. **Platform: 17 abas → 4 domínios** (Negócio / Operação&Risco / Conteúdo&Growth / Time) + faixa de alertas acionáveis no topo (workspaces em risco, IA acima do teto, erros críticos). [M]
9. **Platform: drawer "Workspace 360"** (plano, MRR, consumo IA, erros, entrar como suporte) — o drill-down que hoje exige 4 abas. [G]
10. **Command palette Cmd+K** (busca global leads+conversas+páginas; a busca atual só filtra cards carregados do funil). [M]

## Demais achados por área

**Sistema de design**: cores hardcoded fora dos tokens (#0057d8, #031121 etc., com pares faltando no dark mode); estados vazios secos sem CTA; header com 8 elementos (consolidar pills num indicador de saúde único); microinterações (transição de página, contagem animada nos números, celebração ao ganhar caso); aria-pressed nos períodos, combobox de busca navegável, contraste da sidebar (text-white/35).

**Super Admin**: overview sem seletor de período nem delta; lista de workspaces sem busca/ordenação por risco; sem exportar CSV (reaproveitar downloadBrowserFile da Home); form "novo workspace" vira Dialog.

**Personas (menor esforço)**: expor rolePackage (já vem no session.activeRole) + mapa de blocos na Home [M]; emphasisByPackage na navegação [P]; mobileNavigation por pacote [P]; saudação contextual no header com prazos/conversas do dia [P].

**Momentos "uau" de demo (polir nesta ordem)**: (1) Caso 360 na conversa + timeline MatterFlowJourney em destaque; (2) ROI por anúncio → clique em "abrir conversa de evidência" como clímax ("este contrato veio DESTE anúncio"); (3) wizard de conexão com progresso animado e mensagem-teste real; (4) fila de decisões com aprovação em 1 clique e sino baixando ao vivo; (5) relatório executivo em PDF/HTML timbrado com a marca do workspace (workspace-brand-provider já existe) no lugar do CSV cru.
