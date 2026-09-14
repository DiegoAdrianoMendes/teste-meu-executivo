# Revisão como Usuário Leigo + Onboarding + Plano da Aba Conhecimento

Data: 23/07/2026. Método: navegação real clicando no sistema (127.0.0.1:4173) como advogado não-técnico, com o backend no código integrado do dia. Complementa REVISAO_UI_UX_2026-07-23.md (backlog de polish) e o memo de investidor.

## 1. Revisão como usuário (achados do clique)

**O que impressiona de cara:** dossiê Caso 360 dentro da conversa; wizard da Central de Conexão com instruções completas passo a passo (renderização impecável, checklist persistente); Rastreamento com 6 KPIs, abas e "Recomendação de verba"; white-label por workspace funcionando (marca/tema trocam de verdade).

**Achados (por gravidade para um leigo):**
1. **Honestidade dos números de demonstração**: o Rastreamento mostra "Investimento R$ 678 · ROAS 12,39x · Vendas atribuídas 3 de 3" vindos do seed SEM o selo de demonstração (o banner só cobre dados de fixture do gestor de anúncios, não o seed de aquisição). Um leigo acredita que é real; um comprador desconfia de tudo depois. Estender o selo a TODA origem sintética.
2. **Métricas que se contradizem aos olhos do leigo**: "CAC cliente — (0 clientes)" ao lado de "Vendas atribuídas 3 de 3". Explicável tecnicamente, inaceitável comercialmente; reconciliar definições ou explicar inline.
3. **Dois botões "Personalizar" lado a lado** com ícones diferentes no Rastreamento, sem diferenciação de rótulo.
4. **Títulos truncados no header** ("Central de Conex…"): dar mais espaço ao título ou tooltip.
5. **Scroll por teclado (End/PgDn) não funciona** nas páginas de conteúdo (scroll container interno): acessibilidade e conforto.
6. Confirmações anteriores do passeio: zero erros de console em todas as páginas; dark mode consistente; mobile com bottom bar correta; busca com autocomplete funciona.
7. **Ambiente**: o predev do frontend agora exige Node ≥24.18.0 (máquina tem 24.14.1): instalar a versão do .nvmrc (pendência de ambiente; dev server foi iniciado direto pelo vite nesta sessão).

## 2. Revisão do onboarding

Hoje o primeiro contato é: login local automático → Home densa (20+ chamadas) sem "comece por aqui". O wizard de conexão existe e é excelente, mas nada aponta para ele. Convergindo com o memo YC e a revisão de UI:

1. **Aha script de 3 passos na Home** (novo usuário/workspace sem conexão): card fixo no topo: (1) Conectar WhatsApp → Central de Conexão; (2) Enviar mensagem-teste → inbox; (3) Ver a demonstração de atribuição (dados rotulados) → Rastreamento. Barra de progresso do workspace; some quando os 3 completam.
2. **Wizard**: adicionar tempo estimado por passo ("~10 min", "Business Verification: 1-3 dias úteis da Meta"), e celebração no fim (mensagem-teste real na tela).
3. **Onboarding assistido como produto** (venda white-glove): o wizard vira roteiro do implantador; agendar implantação na tela de boas-vindas.
4. **Primeiro login por papel** (usa a Home adaptativa do backlog de UI): secretária não deve cair em ROAS.

## 3. Plano da Aba Conhecimento (Central de Ajuda interna)

**Objetivo**: usuários não-técnicos se auto-servirem com passo a passo ilustrado, sem sair do sistema.

**Arquitetura:**
- Rota `/app/conhecimento` ("Conhecimento", ícone BookOpen, grupo Execução) + acesso contextual: ícone "?" no header de cada página abrindo o artigo daquela tela em drawer lateral (não navega para fora do contexto).
- Conteúdo como dados versionados no repo: `frontend/src/content/knowledge/*.md(x)` com frontmatter {slug, título, categoria, papéis, páginas relacionadas, updatedAt}; render com o pipeline de markdown existente do blog. Sem CMS nesta fase; o Super Admin edita via repo/painel futuro.
- **Links internos**: sintaxe `[[rota:/app/conexoes|Central de Conexão]]` renderizada como Link do router (deep link para a tela certa, e onde possível com âncora/aba, ex.: `/app/rastreamento?tab=feedback`).
- **Prints**: mesma política do wizard (nunca placeholder): screenshots reais capturados na conexão piloto e nas telas locais, armazenados em `frontend/public/knowledge/`, com moldura padrão e legenda; enquanto não existirem, o artigo publica só texto + callouts.
- Busca dentro da aba (client-side pelos frontmatters/corpo) e integração futura com o Cmd+K do backlog de UI.
- Filtro por papel: artigo declara `papéis:` e a listagem prioriza os do papel do usuário (nada oculto, só ordenado).
- Telemetria mínima: "este artigo ajudou? 👍👎" gravado localmente por artigo (alimenta o Super Admin).

**Catálogo inicial (16 artigos, ordem de escrita):**
Conexão: 1) Conectar WhatsApp (modo manual) — espelho do wizard com prints; 2) Entender Business Verification da Meta; 3) Erros comuns de conexão (display name, número preso, PIN 133005); 4) Conectar Instagram e Página (quando F3/F4 abrirem).
Operação diária: 5) Atender conversas e usar o Caso 360; 6) Criar template e entender aprovação/categorias (marketing custa ~7× utility); 7) Fazer um disparo seguro (opt-in, supressão, por que a campanha pausa sozinha); 8) Agenda e follow-ups; 9) Funil: mover casos e o que cada etapa significa.
Crescimento: 10) Ligar o rastreamento de anúncios (CTWA) e ler o ROI; 11) Recuperar histórico de anúncios (backfill); 12) Entender CPL/CAC/ROAS em linguagem simples; 13) Aquisição paga: criar campanha com aprovação.
Gestão: 14) Papéis e permissões da equipe; 15) Planos, cotas de IA e o que acontece ao estourar; 16) Segurança e privacidade dos dados do escritório.

**Execução**: sprint próprio na onda de polish (página + drawer contextual + 6 primeiros artigos escritos; restante conforme telas estabilizam). Prints entram na conexão piloto.
