# Prompts para o Lovable — KLM Legal Hub

Estes prompts descrevem **apenas visual e comportamento de UI**. Toda a lógica de dados e integração com a API já está implementada no código do repositório `kleislm/klm-legal-hub`.

---

## Prompt 1 — Indicador de Handoff IA → Humano (Pilha Humana)

Na página **Mensagens**, a lista de conversas já tem um campo `aiMode` em cada conversa. Quando `aiMode === 'paused'`, significa que a IA pausou e o advogado precisa assumir a conversa.

Implemente:

1. Na lista de conversas (componente `ConversationList`), conversas com `aiMode === 'paused'` devem aparecer **sempre no topo da lista**, separadas por um divisor com o label **"Aguardando você"** em vermelho (`text-destructive`).
2. Cada conversa nessa seção deve ter um badge vermelho pulsante no lado esquerdo do avatar (use `animate-pulse` do Tailwind com `bg-destructive rounded-full w-2 h-2`).
3. No header da conversa ativa (componente `ConversationView`), quando `aiMode === 'paused'`, exibir um banner amarelo/âmbar no topo com o texto: **"A IA pausou esta conversa. Você está no controle."** e um botão **"Reativar IA"** que chama `toggleAiMode(conversationId, 'active')`.
4. No Kanban (componente `MatterCard`), cards com `needsApproval === true` devem ter uma faixa lateral esquerda vermelha de 3px (`border-l-[3px] border-destructive`) e um ícone de alerta `AlertTriangle` de 12px no canto superior direito do card.

---

## Prompt 2 — Skeleton Loaders e Estados de Carregamento

O sistema agora carrega dados reais da API. Durante o carregamento, o frontend deve exibir skeletons em vez de telas em branco.

Implemente:

1. No **Kanban** (`src/pages/Kanban.tsx`): quando `state.loading === true` ou `state.cards.length === 0 && !state.isAuthenticated`, exibir 3 colunas com 3 cards skeleton cada. Cada card skeleton deve ter a mesma altura de um `MatterCard` real, com `animate-pulse` e `bg-muted rounded-lg`.
2. Na **Lista** (`src/pages/Lista.tsx`): quando carregando, exibir 8 linhas de tabela skeleton com `animate-pulse`.
3. No **Dashboard** (`src/pages/Dashboard.tsx`): quando métricas ainda não carregaram (`state.metrics.length === 0`), exibir 4 cards de KPI skeleton com `animate-pulse`.
4. Na **página de Mensagens** (`src/pages/Mensagens.tsx`): quando `state.conversations.length === 0` e autenticado, exibir 5 itens skeleton na lista de conversas.
5. Em **todas as páginas**: o `Topbar` já tem o badge de notificações. Quando `state.notifications.length === 0` e autenticado, exibir um spinner de 12px no lugar do número.

---

## Prompt 3 — Indicador de Qual Agente (IA ou Humano) Está Atendendo

Na página **Mensagens**, cada conversa tem os campos `currentAgent` (ID do agente IA) e `responsibleHuman` (ID do advogado humano). O hook `useAgentResolver` já resolve esses IDs para `{ name, color, initials }`.

Implemente:

1. No header da conversa ativa (`ConversationView`), abaixo do nome do contato, exibir dois chips lado a lado:
   - **Chip IA**: ícone `Bot` de 10px + nome do agente IA resolvido via `getAgent(conversation.currentAgent)`. Cor de fundo: `bg-primary/10 text-primary`.
   - **Chip Humano**: ícone `User` de 10px + nome do advogado resolvido via `getAgent(conversation.responsibleHuman)`. Cor de fundo: `bg-muted text-muted-foreground`.
   - Se `aiMode === 'paused'`, o chip IA deve ter `opacity-40 line-through` e o chip Humano deve ter `bg-[hsl(var(--success))]/10 text-[hsl(var(--success))]`.
2. Na lista de conversas, ao lado do timestamp de cada item, exibir um avatar minúsculo (16px) com as iniciais do agente responsável atual (IA se `aiMode === 'active'`, humano se `aiMode === 'paused'`). Use `getAgent()` para resolver as iniciais e a cor.

---

## Prompt 4 — Notificações em Tempo Real no Topbar

O `Topbar` já tem o badge de notificações. Melhore a experiência:

1. O badge deve mostrar o número de notificações não lidas de `state.notifications.filter(n => !n.read).length`. Se for 0, ocultar o badge completamente.
2. Ao clicar no ícone de sino, abrir um `Popover` (já disponível em `@/components/ui/popover`) com as últimas 5 notificações de `state.notifications`.
3. Cada item de notificação no popover deve ter:
   - Ícone baseado no tipo: `AlertTriangle` para urgente, `CheckCircle` para aprovação, `MessageCircle` para mensagem, `Info` para informação.
   - Título em negrito e descrição em `text-muted-foreground text-xs`.
   - Timestamp relativo (ex: "há 5 min") usando `date-fns/formatDistanceToNow` (já disponível).
   - Fundo `bg-primary/5` se não lida, normal se lida.
   - Ao clicar, chamar `markNotificationRead(notification.id)` do store.
4. Rodapé do popover com botão **"Ver todas"** que navega para `/notificacoes`.

---

## Prompt 5 — Página de Configuração do WhatsApp

A página de configuração do WhatsApp (`src/pages/WhatsApp.tsx` ou similar) já existe. Melhore o fluxo de conexão:

1. A página deve ter **3 estados visuais** baseados em `whatsappStatus` (que vem de `GET /api/v1/whatsapp/status`):

   **Estado: `disconnected`**
   - Card central com ícone do WhatsApp em verde, título "Conectar WhatsApp Business" e formulário com 3 campos: "Phone Number ID", "Access Token" e "Business Account ID".
   - Botão "Conectar" que chama `POST /api/v1/whatsapp/connect` via `whatsappService.connectWhatsApp()`.
   - Abaixo do formulário, um acordeão "Como obter essas credenciais?" com instruções em 3 passos.

   **Estado: `connecting`**
   - Spinner centralizado com texto "Verificando conexão com a Meta...".

   **Estado: `connected`**
   - Card verde com ícone de check, número conectado e data de conexão.
   - Seção "Configuração do Webhook" com dois campos somente leitura (copiáveis via botão de clipboard): **URL do Webhook** e **Verify Token** (ambos retornados por `GET /api/v1/whatsapp/status`).
   - Botão "Desconectar" vermelho com confirmação via `AlertDialog`.

2. Estatísticas abaixo: total de mensagens enviadas, recebidas e conversas ativas (vindas do mesmo endpoint de status).
