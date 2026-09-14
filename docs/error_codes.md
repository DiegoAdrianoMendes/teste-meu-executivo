# KLM Legal Hub — Códigos de Erro da API

Todos os erros da API retornam JSON no formato:

```json
{
  "detail": {
    "code": "DOMAIN-001",
    "message": "Descrição legível do erro."
  }
}
```

---

## Autenticação (AUTH)

| Código   | HTTP | Descrição                                          | Causa                                              |
|----------|------|----------------------------------------------------|----------------------------------------------------|
| AUTH-001 | 401  | Token de autenticação não fornecido                | Header `Authorization` ausente                     |
| AUTH-002 | 401  | Token inválido ou expirado                         | JWT malformado, assinatura inválida ou expirado     |
| AUTH-003 | 401  | Email ou senha incorretos                          | Credenciais de login inválidas                     |
| AUTH-004 | 403  | Usuário inativo                                    | Conta desativada pelo administrador                |

---

## Usuários (USR)

| Código   | HTTP | Descrição                                          | Causa                                              |
|----------|------|----------------------------------------------------|----------------------------------------------------|
| USR-001  | 404  | Usuário não encontrado                             | ID inexistente ou usuário deletado                 |
| USR-002  | 409  | Email já cadastrado no sistema                     | Tentativa de criar/atualizar com email duplicado   |
| USR-003  | 403  | Permissão insuficiente                             | Operação requer role `admin`                       |
| USR-004  | 400  | Não é possível desativar o próprio usuário         | Admin tentou desativar a si mesmo                  |
| USR-005  | 403  | Usuário inativo                                    | Conta desativada (alias de AUTH-004)               |

---

## Casos / Matters (MTR)

| Código   | HTTP | Descrição                                          | Causa                                              |
|----------|------|----------------------------------------------------|----------------------------------------------------|
| MTR-001  | 404  | Caso não encontrado                                | ID inexistente ou caso arquivado                   |
| MTR-002  | 422  | Campo 'order' (int) é obrigatório                  | Payload de reordenação inválido                    |
| MTR-003  | 422  | Nota não pode ser vazia                            | Payload de nota inválido                           |

---

## Tarefas (TSK)

| Código   | HTTP | Descrição                                          | Causa                                              |
|----------|------|----------------------------------------------------|----------------------------------------------------|
| TSK-001  | 404  | Tarefa não encontrada                              | ID inexistente                                     |

---

## Aprovações (APR)

| Código   | HTTP | Descrição                                          | Causa                                              |
|----------|------|----------------------------------------------------|----------------------------------------------------|
| APR-001  | 404  | Aprovação não encontrada                           | ID inexistente                                     |
| APR-002  | 409  | Aprovação já resolvida                             | Tentativa de resolver aprovação já processada      |

---

## Documentos (DOC)

| Código   | HTTP | Descrição                                          | Causa                                              |
|----------|------|----------------------------------------------------|----------------------------------------------------|
| DOC-001  | 404  | Documento não encontrado                           | ID inexistente                                     |

---

## Notificações (NTF)

| Código   | HTTP | Descrição                                          | Causa                                              |
|----------|------|----------------------------------------------------|----------------------------------------------------|
| NTF-001  | 404  | Notificação não encontrada                         | ID inexistente                                     |

---

## Como tratar no frontend

```typescript
import { ApiError, apiFetch } from '@/services/http';

try {
  const data = await apiFetch('/api/v1/recurso');
} catch (error) {
  if (error instanceof ApiError) {
    const detail = error.detail as { code?: string } | undefined;
    const code = detail?.code ?? 'UNKNOWN';
    // Trate o código de domínio. O cliente central renova a sessão usando o
    // cookie HttpOnly e mantém o access token fora do localStorage.
    console.error(code);
  }
}
```
