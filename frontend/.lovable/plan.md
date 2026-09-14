

# Painel Administrativo `/adm` — Somente UI

## Visão Geral

Criar uma rota `/adm` com painel de administração completo para gerenciar usuários (humanos e bots) com sistema granular de permissões. Tudo mock, seguindo a arquitetura frontend-only existente.

## Estrutura

```text
/adm
├── Tabs ou sidebar interno
│   ├── Usuários Humanos
│   ├── Agentes IA (Bots)
│   └── Permissões & Papéis
```

## Modelo de Dados

### Novos types (`src/types/index.ts`)

- `AdminRole` — tipo com id, name, description, permissions[]
- `Permission` — granular: { module, action, scope }
  - **module**: dashboard, kanban, lista, mensagens, relatorios, aprovacoes, configuracoes, admin
  - **action**: view, create, edit, delete, approve, export, manage_ai, send_message
  - **scope**: all, own, team
- `AdminUser` — extends Agent com: email, isActive, lastLogin, roles[], directPermissions[]
- Adicionar `'admin'` ao `ModuleId`

### Mock data (`src/mocks/data.ts`)

- 3 roles mock: Administrador (tudo), Advogado (operacional), Estagiário (limitado)
- Usuários mock baseados nos agents existentes + 1-2 extras
- Matriz de permissões pré-preenchida

## Páginas e Componentes

### `src/pages/Admin.tsx` — Layout principal

- Tabs: "Usuários", "Agentes IA", "Papéis & Permissões"
- Header com título e stats (total users, active bots, roles)

### Tab Usuários Humanos

- Tabela com: avatar, nome, email, papel, status (ativo/inativo), último login
- Botão "Novo Usuário" → Dialog/modal com formulário
- Ações por linha: editar, ativar/desativar, ver permissões
- Ao clicar em editar → painel lateral ou dialog com:
  - Dados básicos (nome, email, papel)
  - Toggle ativo/inativo
  - Seletor de papel (dropdown)
  - **Permissões diretas** — grid de checkboxes: módulo × ação × escopo

### Tab Agentes IA

- Cards ou tabela dos bots com: nome, tipo de função, status, modelo IA, limites
- Editar bot → dialog com:
  - Nome, descrição, função
  - Módulos que pode acessar (checkboxes)
  - Ações permitidas (toggle por ação)
  - Limites: max ações/dia, requer aprovação humana (switch)

### Tab Papéis & Permissões

- Lista de roles com nome, descrição, nº de usuários vinculados
- Ao clicar em um role → **Matriz de permissões completa**
  - Linhas: módulos
  - Colunas: ações (ver, criar, editar, deletar, aprovar, exportar)
  - Células: checkbox + seletor de escopo (todos, próprios, equipe)
- Botão "Novo Papel" → dialog com nome + descrição + matriz

## Componentes Novos

- `src/pages/Admin.tsx` — página principal
- `src/components/admin/UserManagement.tsx` — tab usuários
- `src/components/admin/BotManagement.tsx` — tab bots
- `src/components/admin/RolesPermissions.tsx` — tab papéis
- `src/components/admin/PermissionMatrix.tsx` — grid checkbox módulo × ação
- `src/components/admin/UserEditDialog.tsx` — dialog edição usuário
- `src/components/admin/BotEditDialog.tsx` — dialog edição bot

## Arquivos Modificados

- `src/types/index.ts` — novos types de admin
- `src/mocks/data.ts` — mock users, roles, permissions
- `src/App.tsx` — rota `/adm` protegida
- `src/components/layout/AppShell.tsx` — módulo admin
- `src/components/layout/AppSidebar.tsx` — item "Admin" no menu
- `src/store/app-context.tsx` — state e actions para admin CRUD (mock)

## Detalhes Técnicos

- Todas as operações são mock (dispatch local, sem backend)
- Responsivo: no mobile, tabela vira cards, matriz de permissões usa scroll horizontal
- Dialogs usam componentes shadcn existentes (Dialog, Form, Switch, Checkbox, Select, Tabs)
- Permissões são granulares até o nível módulo × ação × escopo

