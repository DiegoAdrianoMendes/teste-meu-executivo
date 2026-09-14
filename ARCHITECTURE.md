# Documento de Arquitetura Mestre - KLM Legal Hub

## 1. Visão Geral do Sistema
O KLM Legal Hub é uma plataforma SaaS projetada para gestão jurídica inteligente, combinando um CRM/Kanban tradicional com automação via IA (OpenClaw) e comunicação omnichannel (WhatsApp Oficial).

**Stack Tecnológico:**
- **Frontend:** React (SPA), TypeScript, TailwindCSS, Vite, Node.js 24.18.0 LTS (hospedado no Lovable/AWS quando aprovado).
- **Backend:** Python 3.12, FastAPI, SQLAlchemy (Async), PostgreSQL (hospedado na AWS ECS/RDS quando a implantação for aprovada).
- **Integrações:** Meta Cloud API (WhatsApp Business), OpenClaw (Agentes de IA), AWS S3 (Documentos).

## 2. Mapa de Entidades e Relacionamentos (Database Schema)
O sistema foi desenhado com uma arquitetura relacional robusta. A entidade central é o **Matter** (Caso/Demanda).

- **User (Agentes Humanos e IA):** Representa advogados, estagiários e agentes de IA.
- **Client:** O cliente final. Relaciona-se com múltiplos *Matters*.
- **Matter (Caso):** O coração do sistema. Possui status de Kanban (`column_id`, `micro_status`), prioridade, urgência e notas de estratégia.
  - *Relacionamentos do Matter:*
    - `tasks`: Tarefas associadas ao caso.
    - `documents`: Documentos anexados (armazenados no S3).
    - `timeline_events`: Histórico de auditoria e eventos do caso.
    - `approval_items`: Fila de aprovações (ex: aprovar petição).
    - `conversations`: Notas internas e discussões.
- **WhatsAppContact & WhatsAppMessage:**
  - `WhatsAppContact`: Vinculado opcionalmente a um `Matter`. Representa a sessão de chat com um número de telefone.
  - `WhatsAppMessage`: Histórico de mensagens (inbound/outbound), status de leitura e entrega.
- **Notification:** Alertas para usuários humanos (ex: "Assumir atendimento no WhatsApp").

## 3. Fluxos de Dados Principais

### 3.1. Fluxo de Atendimento Omnichannel (WhatsApp + IA + Humano)
1. **Inbound:** Cliente envia mensagem no WhatsApp.
2. **Webhook:** Meta dispara POST para `/api/v1/whatsapp/webhook`.
3. **Processamento:** Backend salva a mensagem em `whatsapp_messages` e atualiza `whatsapp_contacts`.
4. **Roteamento (IA vs Humano):**
   - Se o contato estiver em `ai_mode = active`, o backend aciona o OpenClaw via webhook/API interna para gerar a resposta.
   - Se a IA identificar necessidade humana (ou o cliente pedir), o `ai_mode` muda para `paused`, e uma `Notification` é gerada para o `User` responsável.
5. **Outbound:** IA ou Humano (via frontend) envia mensagem POST `/api/v1/whatsapp/messages`, que chama a Meta API e salva no banco.

### 3.2. Fluxo de Gestão de Casos (Kanban)
1. Frontend consome `GET /api/v1/matters` e renderiza as colunas.
2. Drag-and-drop dispara `PUT /api/v1/matters/{id}/move`.
3. Backend atualiza `column_id` e gera um `TimelineEvent` de auditoria.

## 4. Lacunas Arquiteturais Identificadas (Gaps)

Após análise profunda do código atual (Frontend e Backend), identifiquei as seguintes desconexões que precisam ser resolvidas para que o sistema funcione de forma fluida com as IAs:

1. **Desconexão de IDs (Frontend Mocks vs Backend Real):**
   - O frontend ainda possui muitos arquivos em `src/mocks/data.ts` (ex: IDs de colunas, agentes, matters). Quando a IA (OpenClaw) tenta atualizar um caso no backend, o frontend pode não refletir corretamente se estiver usando IDs mockados.
   - **Solução:** O frontend deve ser 100% dinâmico, consumindo `/api/v1/users` para listar agentes e `/api/v1/matters` para o Kanban, eliminando o arquivo de mocks.

2. **Falta de WebSockets / Server-Sent Events (SSE):**
   - Atualmente, o frontend é uma SPA que depende de *polling* ou *refresh* manual para ver novas mensagens do WhatsApp ou atualizações feitas pela IA.
   - **Solução:** Implementar WebSockets no backend (`/api/v1/ws`) e no frontend para que mensagens do WhatsApp, notificações e mudanças no Kanban apareçam em tempo real.

3. **Sincronização WhatsApp ↔ CRM (Matter):**
   - A tabela `whatsapp_contacts` tem um `matter_id`, mas a lógica de criar um *Matter* automaticamente quando um novo lead entra pelo WhatsApp ainda precisa ser refinada no webhook.
   - **Solução:** O webhook do WhatsApp deve verificar se o telefone já existe. Se não, cria um `Client`, cria um `Matter` na coluna "Entrada", e vincula o `WhatsAppContact` a esse `Matter`.

4. **Handoff IA para Humano (Pilha Humana):**
   - O frontend precisa de um indicador visual claro (ex: badge vermelho) na página de Mensagens e no Kanban mostrando quais conversas estão aguardando intervenção humana.

## 5. Plano de Melhorias Técnicas e Visuais

### Para o Backend (Próximos Passos Internos)
- [ ] Implementar WebSockets para real-time updates (WhatsApp e Notificações).
- [ ] Refinar o Webhook do WhatsApp para auto-criar `Client` e `Matter` para novos leads.
- [ ] Criar endpoint `/api/v1/whatsapp/contacts/{id}/handoff` para alternar o controle entre IA e Humano.

### Para o Frontend (A ser feito via Lovable)
- **Visual:** Otimizar a página de Mensagens para mobile (layout responsivo de chat, lista de contatos colapsável).
- **Técnico:** Remover dependência de `mocks/data.ts` e plugar todos os componentes aos serviços reais (`api.ts`, `usersService.ts`, `authService.ts`).
- **UX:** Adicionar badges de notificação em tempo real no menu lateral.

---

## 6. Prompts para o Lovable

Copie e cole os prompts abaixo no Lovable para alinhar o frontend com a nova arquitetura do backend.

### Prompt 1: Integração Real da Página de Mensagens (WhatsApp)
```text
Preciso que você atualize a página de Mensagens (WhatsApp) para consumir a API real do backend e remover os dados mockados. 
1. Use o cliente HTTP configurado em `src/lib/http.ts`.
2. Crie um serviço `whatsappService.ts` com as funções:
   - `getContacts()` -> GET `/api/v1/whatsapp/contacts`
   - `getConversation(id)` -> GET `/api/v1/whatsapp/contacts/{id}`
   - `sendMessage(contactId, text)` -> POST `/api/v1/whatsapp/messages`
   - `markAsRead(contactId)` -> POST `/api/v1/whatsapp/contacts/{id}/read`
3. Na UI, adicione um toggle/botão no header do chat para alternar o "Modo IA" (Ativo/Pausado), indicando visualmente se o bot está respondendo ou se um humano assumiu.
4. Garanta que o layout do chat seja perfeito para mobile (a lista de contatos deve sumir ao abrir um chat em telas pequenas, com um botão de voltar).
```

### Prompt 2: Limpeza de Mocks e Integração do Kanban
```text
O backend já está 100% funcional com banco de dados real. Preciso que você remova o uso de `src/mocks/data.ts` no Kanban e no Dashboard.
1. O Kanban deve carregar as colunas e os cards fazendo um GET para `/api/v1/matters`.
2. Ao mover um card, deve chamar o endpoint PUT `/api/v1/matters/{id}/move` passando o novo `columnId`.
3. A lista de responsáveis (agentes) nos filtros e nos cards deve vir de um GET para `/api/v1/users`.
4. Certifique-se de que os tipos no frontend (`src/types/index.ts`) correspondam aos dados que vêm da API (ex: IDs são strings UUID).
```

### Prompt 3: Notificações e Handoff (Pilha Humana)
```text
Precisamos implementar a "Pilha Humana" para quando a IA precisar de ajuda.
1. No menu lateral (Sidebar/Navbar), adicione um badge de contagem de notificações não lidas.
2. Crie um painel ou dropdown de Notificações que consuma GET `/api/v1/notifications`.
3. Quando uma notificação for do tipo "Intervenção Humana Necessária no WhatsApp", ao clicar nela, o usuário deve ser redirecionado para a página de Mensagens, abrindo automaticamente o chat daquele cliente específico.
```
