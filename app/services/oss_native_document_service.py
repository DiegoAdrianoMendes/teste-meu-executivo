"""Rich native document facade over the four reviewed OSS adapters."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oss_bridge import REDACTED_SECRET, sanitize_oss_json
from app.models.oss_native import (
    OSSNativeCommand,
    OSSNativeCommandStatus,
    OSSNativeExecutionMode,
)
from app.schemas.oss_native import (
    OSSNativeCommandCreate,
    OSSNativeCommandOperation,
    OSSNativeConfigPatch,
    OSSNativeDocumentActionWrite,
)
from app.services.oss_capability_registry import native_public_text, oss_capability_registry
from app.services.oss_native_state_service import (
    OSSNativeCommandConflictError,
    OSSNativeDispatcherRegistry,
    OSSNativeExternalEffectReportedError,
    OSSNativeStateService,
    OSSNativeVersionConflictError,
    _reject_positive_external_effect_markers,
)

_NATIVE_DOMAINS = {
    "chatwoot": ("communications", "Comunicações"),
    "rocketchat": ("collaboration", "Colaboração interna"),
    "chattermate": ("automation", "Agentes e automações"),
    "bottlecrm": ("crm", "CRM jurídico"),
}

_ADAPTERS = {
    "chatwoot": (
        "app.services.oss_native_chatwoot_service",
        "ChatwootNativeService",
    ),
    "rocketchat": (
        "app.services.oss_native_rocketchat_service",
        "RocketChatNativeService",
    ),
    "chattermate": (
        "app.services.oss_native_chattermate_service",
        "ChatterMateNativeService",
    ),
    "bottlecrm": (
        "app.services.oss_native_bottlecrm_service",
        "BottleCRMNativeService",
    ),
}

_READ_COMMANDS = {
    "refresh",
    "sync",
    "probe",
    "query",
    "search",
    "list",
    "preview",
    "validate",
    "dry_run",
    "health_check",
    "calculate",
    "export_preview",
}
_FORCE_MUTATING_ACTIONS = {
    ("chattermate", "agents-rbac", "sync"),
}
_DANGER_PREFIXES = ("delete", "remove", "disable", "archive", "revoke", "merge")
PermissionKey = tuple[str, str]

_CATEGORY_PERMISSIONS: dict[str, tuple[PermissionKey, PermissionKey]] = {
    "administracao": (("configuracoes", "view"), ("configuracoes", "edit")),
    "agentes": (("agents", "view"), ("agents", "manage")),
    "analytics": (("relatorios", "view"), ("relatorios", "export")),
    "atendimento": (("mensagens", "view"), ("mensagens", "send_message")),
    "automacao": (("automations", "view"), ("automations", "manage")),
    "canais": (("mensagens", "view"), ("mensagens", "send_message")),
    "colaboracao": (("mensagens", "view"), ("mensagens", "send_message")),
    "comercial": (("finance", "view"), ("finance", "manage")),
    "conteudo": (("kanban", "view"), ("kanban", "edit")),
    "crm": (("lead_data", "view"), ("lead_data", "edit")),
    "dados": (("lead_data", "view"), ("lead_data", "edit")),
    "documentos": (("kanban", "view"), ("kanban", "edit")),
    "experiencia": (("configuracoes", "view"), ("configuracoes", "edit")),
    "financeiro": (("finance", "view"), ("finance", "manage")),
    "governanca": (("audit", "view"), ("admin", "manage")),
    "integracoes": (("integrations", "view"), ("integrations", "manage")),
    "licenca": (("admin", "view"), ("admin", "manage")),
    "marketing": (("automations", "view"), ("automations", "manage")),
    "mobile": (("configuracoes", "view"), ("configuracoes", "edit")),
    "observabilidade": (("agents", "view"), ("agents", "manage")),
    "operacao": (("kanban", "view"), ("kanban", "edit")),
    "personalizacao": (("branding", "view"), ("branding", "manage")),
    "plataforma": (("integrations", "view"), ("integrations", "manage")),
    "produtividade": (("kanban", "view"), ("kanban", "edit")),
    "qualidade": (("agents", "view"), ("agents", "manage")),
    "rag": (("agents", "view"), ("agents", "manage")),
    "seguranca": (("security", "view"), ("users", "manage")),
    "workflow": (("automations", "view"), ("automations", "manage")),
}
_COMMAND_VERBS = {
    "acknowledge": "Reconhecer",
    "activate": "Ativar",
    "add": "Adicionar",
    "apply": "Aplicar",
    "approve": "Aprovar",
    "archive": "Arquivar",
    "assign": "Atribuir",
    "associate": "Associar",
    "attach": "Anexar",
    "begin": "Iniciar",
    "bulk": "Operação em lote:",
    "calculate": "Calcular",
    "cancel": "Cancelar",
    "change": "Alterar",
    "clear": "Limpar",
    "close": "Encerrar",
    "clone": "Duplicar",
    "commit": "Confirmar",
    "complete": "Concluir",
    "configure": "Configurar",
    "connect": "Conectar",
    "convert": "Converter",
    "create": "Criar",
    "deactivate": "Desativar",
    "delete": "Excluir",
    "detach": "Desanexar",
    "disable": "Desativar",
    "dispatch": "Disparar",
    "dissociate": "Desassociar",
    "dry": "Simular",
    "edit": "Editar",
    "enable": "Ativar",
    "end": "Encerrar",
    "evaluate": "Avaliar",
    "export": "Exportar",
    "generate": "Gerar",
    "grant": "Conceder",
    "import": "Importar",
    "ingest": "Ingerir",
    "invalidate": "Invalidar",
    "invite": "Convidar",
    "kick": "Remover",
    "link": "Vincular",
    "load": "Carregar",
    "mark": "Marcar",
    "merge": "Mesclar",
    "move": "Mover",
    "open": "Abrir",
    "pause": "Pausar",
    "persist": "Registrar",
    "pin": "Fixar",
    "playground": "Testar no playground",
    "preview": "Pré-visualizar",
    "prepare": "Preparar",
    "probe": "Verificar",
    "process": "Processar",
    "promote": "Promover",
    "publish": "Publicar",
    "query": "Consultar",
    "react": "Reagir",
    "read": "Ler",
    "reassign": "Reatribuir",
    "record": "Registrar",
    "regenerate": "Gerar nova",
    "register": "Registrar",
    "reject": "Rejeitar",
    "refresh": "Atualizar dados",
    "remove": "Remover",
    "render": "Renderizar",
    "reorder": "Reordenar",
    "replace": "Substituir",
    "reply": "Responder",
    "request": "Solicitar",
    "reset": "Redefinir",
    "resolve": "Resolver",
    "restore": "Restaurar",
    "revoke": "Revogar",
    "rotate": "Renovar",
    "run": "Executar",
    "save": "Salvar",
    "schedule": "Agendar",
    "search": "Buscar",
    "select": "Selecionar",
    "send": "Enviar",
    "set": "Definir",
    "snooze": "Adiar",
    "star": "Favoritar",
    "start": "Iniciar",
    "stop": "Parar",
    "subscribe": "Inscrever",
    "sync": "Sincronizar",
    "takeover": "Assumir atendimento",
    "test": "Testar",
    "transfer": "Transferir",
    "unlink": "Desvincular",
    "unmerge": "Desfazer mesclagem",
    "unpin": "Desafixar",
    "unpublish": "Despublicar",
    "unstar": "Desfavoritar",
    "unwatch": "Parar de acompanhar",
    "update": "Atualizar",
    "upload": "Enviar",
    "upsert": "Salvar",
    "validate": "Validar",
    "verify": "Verificar",
    "watch": "Acompanhar",
}

_LABEL_OVERRIDES = {
    "assignment_policy": "Política de atribuição",
    "canned_response": "Resposta pronta",
    "contact_note": "Nota do contato",
    "custom_attribute": "Atributo personalizado",
    "direct_message": "Mensagem direta",
    "dry-run": "Simular",
    "external_message": "Mensagem externa",
    "help_center": "Central de ajuda",
    "integration_event": "Evento de integração",
    "lead_capture": "Captura de lead",
    "local_message": "Mensagem local",
    "mobile_preferences": "Preferências móveis",
    "native_state": "Estado nativo",
    "nativestate": "Estado nativo",
    "notification_rule": "Regra de notificação",
    "oauth_app": "Aplicativo OAuth",
    "organization_settings": "Configurações da organização",
    "personal_access_token": "Token de acesso pessoal",
    "private_note": "Nota interna",
    "record_count": "Quantidade de registros",
    "recordcount": "Quantidade de registros",
    "records": "Registros",
    "sections": "Seções",
    "thread_id": "ID da conversa",
    "threadid": "ID da conversa",
    "update_assignment_policy": "Atualizar política de atribuição",
    "update_canned_response": "Atualizar resposta pronta",
    "update_contact_note": "Atualizar nota do contato",
    "update_custom_attribute": "Atualizar atributo personalizado",
    "update_organization_settings": "Atualizar configurações da organização",
    "upsert_integration_config": "Salvar configuração da integração",
    "view": "Visão",
    "workspace_id": "ID do ambiente",
    "workspaceid": "ID do ambiente",
}

_WORD_LABELS = {
    "account": "conta",
    "agent": "agente",
    "alert": "alerta",
    "all": "todas",
    "app": "aplicativo",
    "assignment": "atribuição",
    "attribute": "atributo",
    "automation": "automação",
    "billable": "faturável",
    "binding": "vínculo",
    "board": "quadro",
    "bot": "bot",
    "calendar": "calendário",
    "capacity": "capacidade",
    "case": "caso",
    "cases": "casos",
    "channel": "canal",
    "chat": "conversa",
    "children": "dependentes",
    "columns": "colunas",
    "company": "empresa",
    "config": "configuração",
    "contact": "contato",
    "contract": "contrato",
    "conversation": "conversa",
    "customer": "cliente",
    "custom": "personalizado",
    "dashboard": "painel",
    "discussion": "discussão",
    "dm": "conversa direta",
    "document": "documento",
    "documents": "documentos",
    "draft": "rascunho",
    "duplicate": "duplicidade",
    "emoji": "emoji",
    "escalation": "escalonamento",
    "estimate": "estimativa",
    "event": "evento",
    "external": "externa",
    "file": "arquivo",
    "from": "de",
    "goal": "meta",
    "graph": "grafo",
    "group": "grupo",
    "handoff": "transferência humana",
    "help": "ajuda",
    "import": "importação",
    "inbox": "caixa de entrada",
    "integration": "integração",
    "invoice": "fatura",
    "key": "chave",
    "klm": "KLM",
    "label": "etiqueta",
    "lead": "lead",
    "local": "local",
    "manifest": "manifesto",
    "media": "mídia",
    "member": "membro",
    "members": "membros",
    "mention": "menção",
    "message": "mensagem",
    "metric": "métrica",
    "mobile": "móvel",
    "native": "nativo",
    "node": "etapa",
    "notification": "notificação",
    "opportunity": "oportunidade",
    "order": "pedido",
    "organization": "organização",
    "owner": "responsável",
    "parent": "pai",
    "permission": "permissão",
    "person": "pessoa",
    "personal": "pessoal",
    "pipeline": "funil",
    "policy": "política",
    "preferences": "preferências",
    "preview": "prévia",
    "private": "interna",
    "probability": "probabilidade",
    "product": "produto",
    "profile": "perfil",
    "proposal": "proposta",
    "rating": "avaliação",
    "recurrence": "recorrência",
    "report": "relatório",
    "requirement": "requisito",
    "revision": "revisão",
    "role": "perfil de acesso",
    "room": "sala",
    "routing": "roteamento",
    "rule": "regra",
    "run": "",
    "segment": "segmento",
    "session": "sessão",
    "solution": "solução",
    "tag": "etiqueta",
    "task": "tarefa",
    "team": "equipe",
    "template": "modelo",
    "text": "texto",
    "thread": "conversa",
    "ticket": "chamado",
    "time": "tempo",
    "timer": "cronômetro",
    "to": "para",
    "tool": "ferramenta",
    "user": "usuário",
    "webhook": "webhook",
    "with": "com",
    "workflow": "fluxo",
    "workspace": "ambiente",
}

# The adapters keep upstream field names stable. These dictionaries translate the
# operational vocabulary at the document boundary without changing technical IDs.
_LABEL_OVERRIDES.update(
    {
        "account_id": "ID da conta",
        "actions": "Ações disponíveis",
        "api_settings": "Configurações da API",
        "attribute_display_name": "Nome de exibição do atributo",
        "attribute_display_type": "Tipo de exibição do atributo",
        "avatar_url": "Foto",
        "available_name": "Nome disponível",
        "browser_name": "Navegador",
        "browser_version": "Versão do navegador",
        "cache": "Cache local",
        "cache_keys": "Chaves de cache",
        "canonical": "Dados consolidados no KLM",
        "canonical_records": "Registros no KLM",
        "channel_id": "ID do canal",
        "channel_type": "Tipo de canal",
        "collection": "Coleção técnica",
        "confirmed": "Confirmado",
        "conflicts": "Conflitos de sincronização",
        "created_at": "Criado em",
        "date_of_joining": "Data de entrada",
        "device_name": "Dispositivo",
        "display_name": "Nome de exibição",
        "external_gate": "Controle de execução externa",
        "greeting_enabled": "Saudação ativa",
        "greeting_message": "Mensagem de saudação",
        "has_marketing_access": "Acesso ao marketing",
        "has_sales_access": "Acesso comercial",
        "integration_connections": "Integrações configuradas",
        "inviter_id": "ID de quem convidou",
        "ip_address": "Endereço IP",
        "is_active": "Ativo",
        "last_login": "Último acesso",
        "last_remote_digest": "Assinatura da última sincronização",
        "message_signature": "Assinatura da mensagem",
        "native_records": "Registros locais",
        "platform_name": "Sistema operacional",
        "platform_version": "Versão do sistema operacional",
        "pubsub_token": "Token de notificações",
        "ratings_count": "Avaliações por nota",
        "remote_resources": "Recursos no motor",
        "room_id": "ID da sala",
        "roomid": "ID da sala",
        "runtime_risk": "Compatibilidade do motor",
        "source_of_truth": "Fonte oficial dos dados",
        "tenant_scoped": "Isolado por ambiente",
        "token_name": "Nome do token",
        "total_count": "Total",
        "total_sent_messages_count": "Pesquisas enviadas",
        "uid": "Identificador do usuário",
        "ui_settings": "Preferências da interface",
        "updated_at": "Atualizado em",
        "upstream": "Motor de origem",
        "username": "Nome de usuário",
        "working_hours_enabled": "Horário de atendimento ativo",
        "bulk_delete": "Excluir em lote",
        "bulk_status": "Atualizar status em lote",
        "bulk_update": "Atualizar em lote",
        "assign_policy_inbox": "Atribuir política à caixa de entrada",
        "availability_status": "Status de disponibilidade",
        "compatibility_status": "Status de compatibilidade",
        "http_status": "Status HTTP",
        "link_parent": "Vincular caso principal",
        "merge_duplicate": "Mesclar registros duplicados",
        "playground": "Testar em ambiente isolado",
        "profile_pic": "Foto",
        "register_mcp_scope": "Registrar escopo MCP",
        "save_metric_view": "Salvar visualização da métrica",
        "unmerge_case": "Desfazer mesclagem do caso",
        "user_details": "Dados do usuário",
    }
)

_WORD_LABELS.update(
    {
        "access": "acesso",
        "accounts": "contas",
        "action": "ação",
        "actions": "ações",
        "active": "ativo",
        "address": "endereço",
        "agents": "agentes",
        "ai": "IA",
        "allowed": "permitido",
        "analytics": "análises",
        "api": "API",
        "approval": "aprovação",
        "approvals": "aprovações",
        "attachments": "anexos",
        "audit": "auditoria",
        "available": "disponível",
        "blocked": "bloqueado",
        "body": "conteúdo",
        "campaigns": "campanhas",
        "cancelled": "cancelado",
        "candidate": "candidato",
        "candidates": "candidatos",
        "canonical": "consolidado no KLM",
        "capability": "capacidade",
        "capture": "captura",
        "catalog": "catálogo",
        "categories": "categorias",
        "category": "categoria",
        "center": "central",
        "changes": "alterações",
        "channels": "canais",
        "chats": "conversas",
        "client": "cliente",
        "clients": "clientes",
        "closed": "fechado",
        "code": "código",
        "collection": "coleção",
        "collections": "coleções",
        "command": "comando",
        "commands": "comandos",
        "companies": "empresas",
        "configuration": "configuração",
        "configured": "configurado",
        "confirmation": "confirmação",
        "conflict": "conflito",
        "conflicts": "conflitos",
        "connections": "conexões",
        "contacts": "contatos",
        "content": "conteúdo",
        "context": "contexto",
        "controls": "controles",
        "count": "quantidade",
        "counts": "quantidades",
        "created": "criado",
        "csat": "satisfação",
        "current": "atual",
        "customers": "clientes",
        "data": "dados",
        "date": "data",
        "deleted": "excluído",
        "description": "descrição",
        "detail": "detalhe",
        "details": "detalhes",
        "digest": "assinatura",
        "direction": "direção",
        "effect": "efeito",
        "email": "e-mail",
        "enabled": "ativo",
        "engine": "motor",
        "entries": "registros",
        "error": "erro",
        "errors": "erros",
        "events": "eventos",
        "executed": "executado",
        "execution": "execução",
        "expected": "esperado",
        "exposed": "disponível",
        "fields": "campos",
        "filename": "nome do arquivo",
        "files": "arquivos",
        "filter": "filtro",
        "finance": "financeiro",
        "format": "formato",
        "forms": "formulários",
        "gate": "controle",
        "gated": "bloqueado",
        "general": "geral",
        "generated": "gerado",
        "greeting": "saudação",
        "health": "saúde do motor",
        "healthy": "saudável",
        "hidden": "oculto",
        "history": "histórico",
        "hours": "horário",
        "http": "HTTP",
        "id": "ID",
        "identity": "identidade",
        "inboxes": "caixas de entrada",
        "included": "incluído",
        "installation": "instalação",
        "integrations": "integrações",
        "intent": "objetivo",
        "issues": "problemas",
        "items": "itens",
        "json": "JSON",
        "kind": "tipo",
        "knowledge": "conhecimento",
        "kpis": "indicadores",
        "labels": "etiquetas",
        "last": "último",
        "limit": "limite",
        "linked": "vinculado",
        "links": "links",
        "login": "acesso",
        "management": "gestão",
        "matter": "caso",
        "matters": "casos",
        "maximum": "máximo",
        "meetings": "reuniões",
        "memberships": "vínculos",
        "messages": "mensagens",
        "metadata": "metadados",
        "method": "método",
        "minutes": "minutos",
        "mode": "modo",
        "model": "modelo",
        "modules": "módulos",
        "motor": "motor",
        "name": "nome",
        "network": "rede",
        "nodes": "etapas",
        "note": "nota",
        "notes": "notas",
        "notifications": "notificações",
        "number": "número",
        "offline": "offline",
        "operations": "operações",
        "output": "resultado",
        "overview": "visão geral",
        "page": "página",
        "payments": "pagamentos",
        "payload": "dados da operação",
        "people": "pessoas",
        "permissions": "permissões",
        "phone": "telefone",
        "pinned": "fixado",
        "plan": "plano",
        "portal": "portal",
        "presence": "presença",
        "previous": "anterior",
        "properties": "propriedades",
        "provider": "motor",
        "providers": "motores",
        "push": "envio",
        "quality": "qualidade",
        "queue": "fila",
        "raw": "dados técnicos",
        "reason": "motivo",
        "recent": "recente",
        "redirect": "redirecionamento",
        "refreshed": "atualizado",
        "remote": "remoto",
        "removed": "removido",
        "reports": "relatórios",
        "requested": "solicitado",
        "required": "obrigatório",
        "requirements": "requisitos",
        "resolved": "resolvido",
        "resources": "recursos",
        "response": "resposta",
        "responses": "respostas",
        "result": "resultado",
        "risk": "risco",
        "roles": "perfis de acesso",
        "rooms": "salas",
        "route": "rota",
        "rows": "linhas",
        "runtime": "motor",
        "safe": "seguro",
        "schema": "estrutura",
        "scope": "escopo",
        "scoped": "isolado",
        "searchable": "pesquisável",
        "seconds": "segundos",
        "secrets": "credenciais",
        "security": "segurança",
        "server": "servidor",
        "service": "serviço",
        "sessions": "sessões",
        "settings": "configurações",
        "setup": "configuração",
        "size": "tamanho",
        "sla": "SLA",
        "snapshot": "retrato atual",
        "sort": "ordenação",
        "source": "origem",
        "state": "estado",
        "stats": "indicadores",
        "steps": "etapas",
        "stored": "armazenado",
        "success": "sucesso",
        "summary": "resumo",
        "synced": "sincronizado",
        "system": "sistema",
        "tabs": "abas",
        "tags": "etiquetas",
        "tasks": "tarefas",
        "teams": "equipes",
        "tenant": "workspace",
        "threads": "conversas",
        "threshold": "limite",
        "timestamp": "data e hora",
        "title": "título",
        "token": "token",
        "tokens": "tokens",
        "tools": "ferramentas",
        "total": "total",
        "type": "tipo",
        "unread": "não lido",
        "updated": "atualizado",
        "upstream": "motor de origem",
        "uri": "URI",
        "url": "URL",
        "username": "usuário",
        "users": "usuários",
        "valid": "válido",
        "validated": "validado",
        "value": "valor",
        "values": "valores",
        "verified": "verificado",
        "version": "versão",
        "views": "visões",
        "waiting": "aguardando",
        "warning": "alerta",
        "webhooks": "webhooks",
        "whatsapp": "WhatsApp",
        "widgets": "indicadores",
        "working": "atendimento",
        "article": "artigo",
        "attributes": "atributos",
        "availability": "disponibilidade",
        "cache": "cache",
        "canned": "pronta",
        "conversations": "conversas",
        "direct": "direta",
        "entry": "registro",
        "execute": "executar",
        "export": "exportação",
        "field": "campo",
        "macro": "macro",
        "mailbox": "caixa de e-mail",
        "mcp": "MCP",
        "mfa": "MFA",
        "oauth": "OAuth",
        "rag": "RAG",
        "record": "registro",
        "reopen": "reabertura",
        "secret": "credencial",
        "stages": "etapas",
        "status": "status",
        "view": "visão",
        "widget": "componente",
    }
)

_COMMAND_VERBS.update({"execute": "Executar"})

_NOUN_PHRASES = {
    "agent_bot": "bot de atendimento",
    "agent_command": "comando do agente",
    "aging_config": "configuração de envelhecimento das oportunidades",
    "all_notifications": "todas as notificações",
    "all_read": "tudo como lido",
    "app_manifest": "manifesto do aplicativo",
    "app_status": "status do aplicativo",
    "assignment_policy": "política de atribuição",
    "automation_rule": "regra de automação",
    "bot_secret": "credencial do bot",
    "canned_response": "resposta pronta",
    "case_status": "status do caso",
    "contact_attributes": "atributos do contato",
    "contact_note": "nota do contato",
    "custom_attribute": "atributo personalizado",
    "custom_field": "campo personalizado",
    "custom_view": "visão personalizada",
    "dashboard_app": "aplicativo do painel",
    "direct_message": "mensagem direta",
    "emoji_upload": "envio de emoji",
    "escalation_dry_run": "simulação de escalonamento",
    "escalation_policy": "política de escalonamento",
    "external_app": "aplicativo externo",
    "external_channel": "canal externo",
    "external_inbox": "caixa de entrada externa",
    "external_media": "mídia externa",
    "external_message": "mensagem externa",
    "from_group": "do grupo",
    "help_center": "central de ajuda",
    "inbox_members": "membros da caixa de entrada",
    "integration_config": "configuração da integração",
    "integration_event": "evento de integração",
    "klm_person": "pessoa no KLM",
    "lead_capture": "captura de lead",
    "local_config": "configuração local",
    "local_message": "mensagem local",
    "local_policy": "política local",
    "metric_view": "visão da métrica",
    "mobile_preferences": "preferências do aplicativo móvel",
    "native_draft": "rascunho local",
    "notification_read": "notificação como lida",
    "notification_rule": "regra de notificação",
    "notification_unread": "notificação como não lida",
    "oauth_app": "aplicativo OAuth",
    "oauth_provider": "provedor OAuth",
    "order_status": "status do pedido",
    "order_to_invoice": "pedido em fatura",
    "organization_settings": "configurações da organização",
    "personal_access_token": "token de acesso pessoal",
    "policy_inbox": "política da caixa de entrada",
    "portal_link": "link do portal",
    "private_note": "nota interna",
    "reopen_policy": "política de reabertura",
    "required_attributes": "atributos obrigatórios",
    "read": "como lido",
    "room_member": "membro da sala",
    "room_read": "sala como lida",
    "routing_rule": "regra de roteamento",
    "run_bot": "bot",
    "run_dashboard_app": "aplicativo do painel",
    "run_event": "evento",
    "run_export": "exportação",
    "run_import": "importação",
    "run_integration": "integração",
    "run_node": "etapa",
    "run_upload": "envio de arquivo",
    "run_webhook": "webhook",
    "session_cache": "cache da sessão",
    "team_member": "membro da equipe",
    "team_members": "membros da equipe",
    "time_entry": "registro de tempo",
    "to_group": "ao grupo",
    "to_proposal": "em proposta",
    "to_rag": "para a base de conhecimento",
    "upload": "envio de arquivo",
    "mcp_scope": "escopo MCP",
    "with_children": "com dependentes",
}


class OSSNativeCapabilityNotFoundError(LookupError):
    """Raised when a provider/capability pair is outside the reviewed 135 keys."""


class OSSNativeExternalGateError(PermissionError):
    """Raised when a native UI request attempts a real external effect."""


OSSNativeExternalEffectContractError = OSSNativeExternalEffectReportedError


class OSSNativeExecutionError(RuntimeError):
    """Raised after a local native command fails and its safe audit is persisted."""

    def __init__(self, command_id: UUID, message: str) -> None:
        super().__init__(message)
        self.command_id = command_id


@lru_cache(maxsize=1)
def _capabilities() -> dict[tuple[str, str], dict[str, Any]]:
    return {(row["provider"], row["key"]): row for row in oss_capability_registry()["capabilities"]}


def native_capability(provider: str, capability: str) -> dict[str, Any]:
    row = _capabilities().get((provider, capability))
    if row is None:
        raise OSSNativeCapabilityNotFoundError(
            f"Capacidade nativa não catalogada: {provider}.{capability}."
        )
    return row


def required_capability_read_permissions(
    provider: str, capability_key: str
) -> frozenset[PermissionKey]:
    capability = native_capability(provider, capability_key)
    domain_read, _ = _CATEGORY_PERMISSIONS.get(
        capability["category"],
        (("integrations", "view"), ("integrations", "manage")),
    )
    return frozenset({("integrations", "view"), domain_read})


def required_capability_manage_permissions(
    provider: str,
    capability_key: str,
) -> frozenset[PermissionKey]:
    capability = native_capability(provider, capability_key)
    _, domain_manage = _CATEGORY_PERMISSIONS.get(
        capability["category"],
        (("integrations", "view"), ("integrations", "manage")),
    )
    return frozenset({("integrations", "manage"), domain_manage})


def _required_action_permissions(
    row: dict[str, Any],
    capability: dict[str, Any],
    *,
    mutating: bool,
) -> frozenset[PermissionKey]:
    domain_read, domain_manage = _CATEGORY_PERMISSIONS.get(
        capability["category"],
        (("integrations", "view"), ("integrations", "manage")),
    )
    required: set[PermissionKey] = {
        ("integrations", "manage") if mutating else ("integrations", "view"),
        domain_manage if mutating else domain_read,
    }
    declared = row.get("permission")
    if isinstance(declared, dict):
        module = str(declared.get("module") or "").strip()
        action = str(declared.get("action") or "").strip()
        if module and action:
            required.add((module, action))
    if row.get("authority") == "workspace_admin":
        required.add(("users", "manage"))
    return frozenset(required)


def _permissions_allow(
    required: frozenset[PermissionKey],
    permission_keys: frozenset[PermissionKey] | None,
) -> bool:
    return permission_keys is None or required.issubset(permission_keys)


def _humanize(value: str) -> str:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
    override = _LABEL_OVERRIDES.get(normalized)
    if override:
        return override
    words = [word for word in re.split(r"[._:-]+", normalized) if word]
    if not words:
        return "Ação"
    command_verb = _COMMAND_VERBS.get(words[0])
    first = command_verb or _WORD_LABELS.get(words[0], words[0])
    if first == first.lower():
        first = first.capitalize()
    tail_key = "_".join(words[1:])
    tail = _NOUN_PHRASES.get(tail_key) if command_verb else None
    if tail is None:
        tail = " ".join(
            translated
            for word in words[1:]
            if (translated := _WORD_LABELS.get(word, word.replace("_", " ")))
        )
    return f"{first} {tail}".strip()


def _format_for_key(key: str) -> str | None:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()
    tokens = {token for token in re.split(r"[^a-z0-9]+", normalized) if token}
    if tokens & {"amount", "price", "value", "revenue", "cost"}:
        return "currency"
    if normalized.endswith("_at") or tokens & {"datetime", "timestamp"}:
        return "datetime"
    if normalized.endswith("_date") or normalized == "date":
        return "date"
    if tokens & {"status", "state", "health"}:
        return "status"
    if tokens & {"percent", "percentage", "rate", "ratio"}:
        return "percent"
    if normalized.startswith(("is_", "has_")) or normalized in {"active", "enabled", "ready"}:
        return "boolean"
    return None


def _columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys: list[str] = []
    for row in rows[:25]:
        for key in row:
            if key not in keys:
                keys.append(key)
            if len(keys) >= 16:
                break
    return [
        {
            "key": key,
            "label": _humanize(key),
            **({"format": value_format} if (value_format := _format_for_key(key)) else {}),
            "hiddenOnMobile": index >= 4,
        }
        for index, key in enumerate(keys)
    ]


def _without_redacted_fields(value: Any) -> Any:
    """Remove credential placeholders from user-facing native documents."""

    if isinstance(value, dict):
        return {
            key: _without_redacted_fields(child)
            for key, child in value.items()
            if child != REDACTED_SECRET
        }
    if isinstance(value, list):
        return [
            _without_redacted_fields(child)
            for child in value
            if child != REDACTED_SECRET
        ]
    return value


def _section(
    key: str,
    value: Any,
    index: int,
    *,
    title_override: str | None = None,
) -> dict[str, Any]:
    section_id = re.sub(r"[^a-z0-9_-]+", "-", key.lower()).strip("-") or f"data-{index}"
    title = title_override or _humanize(key)
    if isinstance(value, list):
        object_rows = [row for row in value if isinstance(row, dict)]
        if len(object_rows) == len(value):
            columns = _columns(object_rows)
            if (
                key.endswith("sections")
                and object_rows
                and all("key" in row and "label" in row for row in object_rows)
            ):
                columns = [
                    {"key": "label", "label": "Recurso", "hiddenOnMobile": False},
                    {
                        "key": "key",
                        "label": "Identificador técnico",
                        "hiddenOnMobile": True,
                    },
                ]
            return {
                "id": section_id,
                "type": "table",
                "title": title,
                "columns": columns,
                "rows": object_rows,
                "searchable": True,
                "pageSize": 12,
            }
        return {
            "id": section_id,
            "type": "structured",
            "title": title,
            "data": value,
        }
    if isinstance(value, dict):
        collection_key = next(
            (
                candidate
                for candidate in ("records", "rows", "items", "results", "entries")
                if isinstance(value.get(candidate), list)
            ),
            None,
        )
        if collection_key:
            rows = value[collection_key]
            object_rows = [row for row in rows if isinstance(row, dict)]
            if len(object_rows) == len(rows):
                return {
                    "id": section_id,
                    "type": "table",
                    "title": title,
                    "description": f"Fonte: {collection_key}",
                    "columns": _columns(object_rows),
                    "rows": object_rows,
                    "searchable": True,
                    "pageSize": 12,
                }
        if value and all(not isinstance(item, dict | list) for item in value.values()):
            return {
                "id": section_id,
                "type": "details",
                "title": title,
                "data": value,
                "fields": [
                    {
                        "key": field,
                        "label": _humanize(field),
                        **(
                            {"format": value_format}
                            if (value_format := _format_for_key(field))
                            else {}
                        ),
                    }
                    for field in value
                ],
            }
    return {
        "id": section_id,
        "type": "structured",
        "title": title,
        "data": value,
    }


_NESTED_SECTION_TITLES = {
    ("canonical", "counts"): "Dados consolidados no KLM",
    ("content", "items"): "Registros",
    ("content", "records"): "Registros",
    ("data", "items"): "Registros",
    ("data", "records"): "Registros",
    ("view", "sections"): "Recursos disponíveis",
    ("view", "summary"): "Resumo operacional",
}


def _expanded_sections(key: str, value: Any, index: int) -> list[dict[str, Any]]:
    if key not in {"canonical", "content", "data", "view"} or not isinstance(value, dict):
        return [_section(key, value, index)]

    if key == "canonical":
        flattened: dict[str, Any] = {}
        nested_sections: list[dict[str, Any]] = []
        for nested_key, nested_value in value.items():
            if not isinstance(nested_value, dict | list):
                flattened[nested_key] = nested_value
            elif isinstance(nested_value, dict) and all(
                not isinstance(item, dict | list) for item in nested_value.values()
            ):
                flattened.update(nested_value)
            else:
                nested_sections.append(
                    _section(
                        f"{key}_{nested_key}",
                        nested_value,
                        index + len(nested_sections),
                        title_override=_humanize(nested_key),
                    )
                )
        if flattened:
            nested_sections.insert(
                0,
                _section(
                    "canonical_summary",
                    flattened,
                    index,
                    title_override="Dados consolidados no KLM",
                ),
            )
        return nested_sections or [_section(key, value, index)]

    sections: list[dict[str, Any]] = []
    scalar_fields: dict[str, Any] = {}
    for nested_key, nested_value in value.items():
        if not isinstance(nested_value, dict | list):
            scalar_fields[nested_key] = nested_value
            continue
        sections.append(
            _section(
                f"{key}_{nested_key}",
                nested_value,
                index + len(sections),
                title_override=_NESTED_SECTION_TITLES.get(
                    (key, nested_key),
                    _humanize(nested_key),
                ),
            )
        )
    if scalar_fields:
        sections.insert(
            0,
            _section(
                f"{key}_summary",
                scalar_fields,
                index,
                title_override=_humanize(key),
            ),
        )
    return sections or [_section(key, value, index)]


def _sections(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(raw.get("tabs"), list):
        return []
    preferred = [
        key
        for key in ("records", "sections", "content", "data", "view", "canonical", "nativeState")
        if key in raw
    ]
    sections: list[dict[str, Any]] = []
    for index, key in enumerate(preferred):
        sections.extend(_expanded_sections(key, raw[key], index * 4))
        if len(sections) >= 16:
            sections = sections[:16]
            break
    return sections or [
        {
            "id": "empty",
            "type": "notice",
            "title": "Nenhum registro",
            "tone": "neutral",
            "body": "A capacidade está disponível, mas ainda não possui registros neste workspace.",
        }
    ]


def _diagnostic_sections(raw: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostic_keys = ("runtimeRisk", "gate", "cache", "upstream", "collection")
    sections: list[dict[str, Any]] = []
    for index, key in enumerate(diagnostic_keys):
        value = raw.get(key)
        if value in (None, "", [], {}):
            continue
        sections.append(
            _section(
                f"diagnostic_{key}",
                value,
                index,
                title_override=_humanize(key),
            )
        )
    return sections


def _document_tabs(raw: dict[str, Any]) -> list[dict[str, Any]]:
    tabs = [
        {
            "id": "overview",
            "label": "Visão geral",
            "sections": _sections(raw),
        }
    ]
    diagnostic_sections = _diagnostic_sections(raw)
    if diagnostic_sections:
        tabs.append(
            {
                "id": "diagnostics",
                "label": "Diagnóstico do motor",
                "sections": diagnostic_sections,
            }
        )
    return tabs


def _record_count(raw: dict[str, Any]) -> int:
    for key in ("records", "items", "rows"):
        if isinstance(raw.get(key), list):
            return len(raw[key])
    for container_key in ("content", "data", "nativeState"):
        container = raw.get(container_key)
        if isinstance(container, dict):
            for key in ("records", "items", "rows"):
                if isinstance(container.get(key), list):
                    return len(container[key])
            for key in ("total", "count", "recordCount"):
                if isinstance(container.get(key), int):
                    return container[key]
    return 0


def _kpis(raw: dict[str, Any], action_count: int) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    widgets = raw.get("widgets")
    if isinstance(widgets, list):
        for index, widget in enumerate(widgets[:4]):
            if not isinstance(widget, dict) or "value" not in widget:
                continue
            metrics.append(
                {
                    "id": str(widget.get("id") or f"metric-{index}"),
                    "label": str(widget.get("label") or "Indicador"),
                    "value": widget.get("value"),
                    "trend": "neutral",
                }
            )
    view = raw.get("view")
    summary = view.get("summary") if isinstance(view, dict) else None
    if isinstance(summary, dict):
        for key, value in summary.items():
            if isinstance(value, int | float) and len(metrics) < 4:
                metrics.append(
                    {
                        "id": str(key),
                        "label": _humanize(str(key)),
                        "value": value,
                        "format": "integer" if isinstance(value, int) else "number",
                        "trend": "neutral",
                    }
                )
    if not metrics:
        metrics.append(
            {
                "id": "records",
                "label": "Registros visíveis",
                "value": _record_count(raw),
                "format": "integer",
                "trend": "neutral",
            }
        )
    metrics.append(
        {
            "id": "actions",
            "label": "Ações nativas",
            "value": action_count,
            "format": "integer",
            "trend": "neutral",
        }
    )
    return metrics[:4]


def _field_for(name: str) -> dict[str, Any]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    field_type = "text"
    if normalized in {
        "actions",
        "aliases",
        "attribute_keys",
        "changes",
        "conditions",
        "custom_attributes",
        "data",
        "graph",
        "manifest",
        "metadata",
        "payload",
        "permissions",
        "policy",
        "user_ids",
    } or normalized.endswith(("_payload", "_config", "_metadata", "_graph")):
        field_type = "json"
    elif normalized in {"body", "content", "description", "message", "notes", "text"}:
        field_type = "textarea"
    elif normalized == "email" or normalized.endswith("_email"):
        field_type = "email"
    elif (
        compact
        in {
            "accesstoken",
            "apikey",
            "authorization",
            "backupcode",
            "clientsecret",
            "credential",
            "onetimecode",
            "otpcode",
            "password",
            "privatekey",
            "secret",
            "sessiontoken",
            "token",
            "verificationcode",
            "webhooksecret",
            "webhooktoken",
        }
        or compact.endswith("password")
    ):
        field_type = "password"
    elif "url" in normalized or "uri" in normalized or "endpoint" in normalized:
        field_type = "url"
    elif normalized in {"byte_size", "capacity", "size"} or any(
        part in normalized for part in ("count", "days", "limit", "position", "priority")
    ):
        field_type = "integer"
    elif normalized.startswith(("is_", "has_")) or normalized in {
        "active",
        "confirm",
        "confirm_local_only",
        "confirmed",
        "enabled",
        "private",
    }:
        field_type = "switch"
    elif normalized.endswith("_at") or "datetime" in normalized:
        field_type = "datetime"
    elif normalized.endswith("date"):
        field_type = "date"
    return {
        "name": name,
        "label": _humanize(name),
        "type": field_type,
        "required": True,
    }


def _fields_for_required(required: list[Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for raw_name in required:
        name = str(raw_name).strip()
        alternatives = [item.strip() for item in name.split("|") if item.strip()]
        if len(alternatives) <= 1:
            fields.append(_field_for(name))
            continue

        alternative_labels = " ou ".join(_humanize(item) for item in alternatives)
        for alternative in alternatives:
            field = _field_for(alternative)
            field.update(
                {
                    "required": False,
                    "oneOfGroup": name,
                    "description": f"Preencha ao menos um: {alternative_labels}.",
                }
            )
            fields.append(field)
    return fields


def _has_action_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | dict):
        return bool(value)
    return True


def _validate_action_values(action: dict[str, Any], values: dict[str, Any]) -> None:
    """Validate the live adapter action schema before any command/config is stored."""

    fields = action.get("fields") if isinstance(action.get("fields"), list) else []
    one_of_groups: dict[str, list[str]] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        value = values.get(name)
        if field.get("required") is True and not _has_action_value(value):
            raise ValueError(f"O campo {_humanize(name)} é obrigatório.")
        group = str(field.get("oneOfGroup") or "").strip()
        if group:
            one_of_groups.setdefault(group, []).append(name)
        if not _has_action_value(value):
            continue

        field_type = str(field.get("type") or "text")
        if field_type in {"text", "textarea", "password", "email", "url", "date", "datetime"}:
            if not isinstance(value, str):
                raise ValueError(f"O campo {_humanize(name)} deve ser texto.")
            candidate = value.strip()
            if (
                field_type == "email"
                and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate) is None
            ):
                raise ValueError(f"O campo {_humanize(name)} deve conter um e-mail válido.")
            if field_type == "url" and re.match(r"^(https?://|/)", candidate) is None:
                raise ValueError(f"O campo {_humanize(name)} deve conter uma URL HTTP(S) ou local.")
            if field_type == "date":
                try:
                    datetime.fromisoformat(candidate).date()
                except ValueError as error:
                    raise ValueError(
                        f"O campo {_humanize(name)} deve conter uma data válida."
                    ) from error
            if field_type == "datetime":
                try:
                    datetime.fromisoformat(candidate.replace("Z", "+00:00"))
                except ValueError as error:
                    raise ValueError(
                        f"O campo {_humanize(name)} deve conter data e hora válidas."
                    ) from error
        elif field_type == "integer":
            if isinstance(value, bool):
                raise ValueError(f"O campo {_humanize(name)} deve ser um número inteiro.")
            try:
                parsed = int(value)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"O campo {_humanize(name)} deve ser um número inteiro."
                ) from error
            if isinstance(value, float) and value != parsed:
                raise ValueError(f"O campo {_humanize(name)} deve ser um número inteiro.")
        elif field_type == "switch" and not isinstance(value, bool):
            raise ValueError(f"O campo {_humanize(name)} deve ser verdadeiro ou falso.")
        elif field_type == "json" and not isinstance(value, dict | list):
            raise ValueError(f"O campo {_humanize(name)} deve conter JSON estruturado.")

    for group, names in one_of_groups.items():
        if not any(_has_action_value(values.get(name)) for name in names):
            labels = " ou ".join(_humanize(name) for name in names)
            raise ValueError(f"Preencha ao menos um campo de {labels} ({group}).")


def _transient_action_values(
    action: dict[str, Any],
    request: OSSNativeDocumentActionWrite,
) -> dict[str, str]:
    if len(request.transient_secrets) > 8:
        raise ValueError("A ação excede o limite de 8 credenciais transitórias.")
    fields = action.get("fields") if isinstance(action.get("fields"), list) else []
    password_fields = {
        str(field.get("name") or "").strip().lower(): str(field.get("name") or "").strip()
        for field in fields
        if isinstance(field, dict)
        and str(field.get("type") or "").strip().lower() == "password"
        and str(field.get("name") or "").strip()
    }
    transient: dict[str, str] = {}
    for raw_name, protected in request.transient_secrets.items():
        canonical_name = password_fields.get(str(raw_name).lower())
        if canonical_name is None:
            raise ValueError(
                f"O campo transitório {_humanize(str(raw_name))} não é um campo "
                "de senha desta ação."
            )
        secret = protected.get_secret_value()
        if not 1 <= len(secret) <= 1024:
            raise ValueError(
                f"A credencial transitória de {_humanize(canonical_name)} possui tamanho inválido."
            )
        transient[canonical_name] = secret
    return transient


def _reject_transient_secret_echo(value: Any, secrets: set[str]) -> None:
    if not secrets:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_transient_secret_echo(key, secrets)
            _reject_transient_secret_echo(child, secrets)
    elif isinstance(value, list | tuple):
        for child in value:
            _reject_transient_secret_echo(child, secrets)
    elif isinstance(value, str) and any(secret and secret in value for secret in secrets):
        raise ValueError("O adapter tentou devolver uma credencial transitória.")


def _reject_transient_secret_exception(error: BaseException, secrets: set[str]) -> None:
    """Replace adapter errors that contain a transient credential with a safe error."""

    try:
        _reject_transient_secret_echo(str(error), secrets)
        _reject_transient_secret_echo(error.args, secrets)
    except ValueError:
        raise ValueError(
            "O adapter rejeitou a ação, mas a mensagem insegura foi suprimida."
        ) from None


def _local_only_result(value: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Reject positive external-effect evidence instead of rewriting adapter output."""
    del source
    _reject_positive_external_effect_markers(value)
    safe = _persistence_safe_result(value)
    safe.setdefault("externalNetworkExecuted", False)
    return safe


def _raw_actions(raw: dict[str, Any]) -> list[dict[str, Any]]:
    actions = raw.get("actions")
    if isinstance(actions, list):
        return [row if isinstance(row, dict) else {"command": row} for row in actions]
    commands = raw.get("availableCommands")
    if isinstance(commands, list):
        return [{"command": command} for command in commands]
    return []


def _actions(
    raw: dict[str, Any],
    capability: dict[str, Any],
    *,
    permission_keys: frozenset[PermissionKey] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in _raw_actions(raw):
        command = str(row.get("command") or row.get("id") or row.get("key") or "").strip()
        if not command or any(action["id"] == command for action in result):
            continue
        command_head = re.split(r"[._:-]+", command.lower(), maxsplit=1)[0]
        default_mutating = command not in _READ_COMMANDS and command_head not in _READ_COMMANDS
        force_mutating = (
            str(capability.get("provider") or ""),
            str(capability.get("key") or ""),
            command,
        ) in _FORCE_MUTATING_ACTIONS
        mutating = force_mutating or bool(
            row.get("mutates", row.get("mutating", default_mutating))
        )
        required_permissions = _required_action_permissions(
            row,
            capability,
            mutating=mutating,
        )
        permission_allowed = _permissions_allow(required_permissions, permission_keys)
        gated = capability["nativeMode"] == "gated_configuration" or bool(
            row.get("externalDispatch")
        )
        if row.get("networkAllowed") is False and capability["nativeMode"] == "gated_configuration":
            gated = True
        required = row.get("required") if isinstance(row.get("required"), list) else []
        fields = _fields_for_required(required)
        if mutating and not fields:
            fields = [
                {
                    "name": "payload",
                    "label": "Dados da operação",
                    "type": "json",
                    "defaultValue": {},
                    "required": False,
                    "description": "Campos específicos aceitos pelo motor local para esta ação.",
                }
            ]
        destructive = command.startswith(_DANGER_PREFIXES) or bool(row.get("destructive"))
        result.append(
            {
                "id": command,
                "label": _humanize(command),
                "description": (
                    "A execução real fora do ambiente local permanece bloqueada."
                    if gated
                    else "Executa a operação no KLM ou no motor local vinculado."
                ),
                "intent": (
                    "danger"
                    if destructive
                    else "primary"
                    if command in {"create", "save", "sync", "refresh"}
                    else "secondary"
                ),
                "method": "POST",
                "execution": "external" if gated else "local",
                "fields": fields,
                "permission": {
                    "allowed": permission_allowed,
                    "reason": (
                        row.get("reasonCode")
                        if permission_allowed
                        else "Seu perfil não possui todas as permissões exigidas por esta ação."
                    ),
                    "required": [
                        {"module": module, "action": action}
                        for module, action in sorted(required_permissions)
                    ],
                },
                "gate": (
                    {
                        "state": "gated",
                        "reason": "Credenciais e aprovação externa ainda não foram liberadas.",
                        "allowDryRun": True,
                        "allowConfigure": True,
                    }
                    if gated
                    else {"state": "open"}
                ),
                "supportsDryRun": gated or command in {"preview", "validate", "dry_run"},
                "confirmation": {
                    "title": f"Confirmar: {_humanize(command)}?",
                    "body": "A ação será registrada na auditoria deste workspace.",
                    "confirmLabel": "Confirmar operação",
                },
            }
        )
    return result


_UNHEALTHY_STATES = {
    "down",
    "error",
    "failed",
    "failing",
    "offline",
    "runtime-unavailable",
    "stopped",
    "unavailable",
    "unhealthy",
}
_HEALTH_DESCENT_SKIP_KEYS = {
    "audit",
    "audittrail",
    "commands",
    "items",
    "records",
    "rows",
    "tombstones",
}


def _has_nested_health_problem(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized_key in {"error", "errors"} and child not in (None, False, "", [], {}):
                return True
            if normalized_key in {"availability", "state", "status"}:
                if str(child or "").strip().lower() in _UNHEALTHY_STATES:
                    return True
            if normalized_key not in _HEALTH_DESCENT_SKIP_KEYS and _has_nested_health_problem(
                child
            ):
                return True
        return False
    if isinstance(value, list | tuple):
        return any(_has_nested_health_problem(child) for child in value)
    return False


def _health(raw: dict[str, Any], capability: dict[str, Any]) -> dict[str, Any]:
    health = raw.get("health") if isinstance(raw.get("health"), dict) else {}
    content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
    reported_state = str(
        health.get("state")
        or health.get("status")
        or raw.get("status")
        or content.get("status")
        or ""
    ).lower()
    if capability["nativeMode"] == "gated_configuration":
        state = "gated"
    elif reported_state in _UNHEALTHY_STATES:
        state = "offline"
    elif _has_nested_health_problem(raw):
        state = "degraded"
    elif reported_state in {"healthy", "ready", "ok", "synced", "not_applicable"}:
        state = "healthy"
    elif reported_state in {"degraded", "syncing"}:
        state = reported_state
    else:
        state = "degraded"
    return {
        "state": state,
        "label": {
            "healthy": "Disponível localmente",
            "degraded": "Disponível com ressalvas",
            "syncing": "Sincronizando",
            "offline": "Módulo nativo indisponível",
            "gated": "Execução externa bloqueada",
        }[state],
        "message": capability["reason"],
        "lastSyncAt": health.get("lastSyncAt") or health.get("last_synced_at"),
    }


def _version(raw: dict[str, Any], fallback: int) -> str | int:
    candidates = [raw.get("version"), raw.get("revision")]
    for container_key in ("nativeState", "content", "data", "view"):
        container = raw.get(container_key)
        if isinstance(container, dict):
            candidates.extend(
                [container.get("version"), container.get("revision"), container.get("stateVersion")]
            )
    return next(
        (candidate for candidate in candidates if isinstance(candidate, str | int)),
        fallback,
    )


def _persistence_safe_result(value: dict[str, Any]) -> dict[str, Any]:
    """Sanitize adapter output and omit credential fields before durable audit storage."""

    sanitized = sanitize_oss_json(value)

    def visit(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items() if child != REDACTED_SECRET}
        if isinstance(item, list):
            return [visit(child) for child in item]
        return item

    result = visit(sanitized)
    if not isinstance(result, dict):
        raise TypeError("O resultado nativo persistível deve ser um objeto JSON.")
    return result


class OSSNativeDocumentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _adapter_class(provider: str):
        binding = _ADAPTERS.get(provider)
        if binding is None:
            raise OSSNativeCapabilityNotFoundError(f"Provider nativo desconhecido: {provider}.")
        module_name, class_name = binding
        return getattr(importlib.import_module(module_name), class_name)

    def _adapter(self, provider: str):
        # The KLM event-sourced adapter is the primary runtime.  The four
        # upstream adapters remain preserved behind _adapter_class for import,
        # comparison, rollback, and configuration validation only.
        from app.services.oss_native_local_adapter import OSSNativeLocalPrimaryAdapter

        return OSSNativeLocalPrimaryAdapter(self.db, provider)

    async def _persist_configured_action(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        provider: str,
        capability_key: str,
        action_id: str,
        public_values: dict[str, Any],
        capability: dict[str, Any],
        command_id: UUID,
        command_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Finish a successful configure command without dispatching it again."""

        state_service = OSSNativeStateService(self.db)
        config_version: int | None = None
        for attempt in range(2):
            latest_state = await state_service.get_snapshot(
                workspace_id,
                provider,
                capability_key,
            )
            current_configuration = dict(latest_state.state.configuration)
            configuration_is_current = (
                current_configuration.get(f"action:{action_id}") == public_values
                and current_configuration.get("nativeMode") == capability["nativeMode"]
                and current_configuration.get("externalExecutionState")
                == capability["externalExecutionState"]
                and latest_state.state.execution_mode
                == OSSNativeExecutionMode.local_native
            )
            if configuration_is_current:
                config_version = latest_state.state.config_version
                break
            try:
                configured = await state_service.update_config(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    provider=provider,
                    capability=capability_key,
                    data=OSSNativeConfigPatch(
                        expected_version=latest_state.state.config_version,
                        configuration={
                            **current_configuration,
                            f"action:{action_id}": public_values,
                            "nativeMode": capability["nativeMode"],
                            "externalExecutionState": capability[
                                "externalExecutionState"
                            ],
                        },
                        execution_mode=OSSNativeExecutionMode.local_native,
                    ),
                )
                config_version = configured.state.config_version
                break
            except OSSNativeVersionConflictError:
                if attempt == 1:
                    raise

        if config_version is None:
            raise RuntimeError("A configuração local não pôde ser reconciliada.")

        configured_result = {
            **command_result,
            "persistedLocally": True,
            "configVersion": config_version,
        }
        persisted_command = await self.db.get(OSSNativeCommand, command_id)
        if persisted_command is None:
            raise RuntimeError("O comando configurado não foi encontrado para reconciliação.")
        if persisted_command.command_result != configured_result:
            persisted_command.command_result = configured_result
            await self.db.commit()
        return configured_result

    async def document(
        self,
        workspace_id: UUID,
        provider: str,
        capability_key: str,
        query: dict[str, Any] | None = None,
        *,
        permission_keys: frozenset[PermissionKey] | None = None,
    ) -> dict[str, Any]:
        capability = native_capability(provider, capability_key)
        public_capability = {
            **capability,
            "label": native_public_text(capability["label"]),
            "description": native_public_text(capability["description"]),
            "reason": native_public_text(capability["reason"]),
        }
        raw = await self._adapter(provider).snapshot(
            workspace_id,
            capability_key,
            dict(query or {}),
        )
        if not isinstance(raw, dict):
            raise TypeError("O adapter nativo deve retornar um objeto JSON.")
        raw = _without_redacted_fields(sanitize_oss_json(raw))
        native_state = await OSSNativeStateService(self.db).get_snapshot(
            workspace_id,
            provider,
            capability_key,
        )
        actions = _actions(raw, public_capability, permission_keys=permission_keys)
        audit = [
            {
                "id": str(event.id),
                "title": _humanize(event.action.value),
                "description": event.summary,
                "timestamp": event.occurred_at.isoformat(),
                "actor": str(event.actor_user_id) if event.actor_user_id else "Sistema KLM",
                "status": event.action.value,
                "metadata": event.metadata,
            }
            for event in native_state.audit_trail
        ]
        source_mode = capability["nativeMode"]
        system_of_record = (
            "provider"
            if source_mode == "sidecar_command"
            else "mixed"
            if source_mode == "sidecar_round_trip"
            else "klm"
        )
        raw_tabs = raw.get("tabs") if isinstance(raw.get("tabs"), list) else None
        domain_id, domain_label = _NATIVE_DOMAINS[provider]
        native_route = f"/app/capacidades/{domain_id}/{capability_key}"
        return {
            "schemaVersion": "2.0",
            "provider": {"id": domain_id, "label": domain_label},
            "capability": {
                "id": capability_key,
                "label": public_capability["label"],
                "description": public_capability["description"],
                "category": public_capability["category"],
            },
            "source": {
                "label": f"Núcleo nativo de {domain_label}",
                "detail": public_capability["reason"],
                "systemOfRecord": system_of_record,
                "fetchedAt": datetime.now(UTC).isoformat(),
            },
            "health": _health(raw, public_capability),
            "access": {"state": "allowed"},
            "version": _version(raw, native_state.state.config_version),
            "updatedAt": datetime.now(UTC).isoformat(),
            "kpis": _kpis(raw, len(actions)),
            "tabs": raw_tabs or _document_tabs(raw),
            "actions": actions,
            "audit": audit,
            "motor": {
                "label": "Abrir no KLM",
                "url": native_route,
                "available": True,
            },
            "native": {
                "mode": source_mode,
                "externalExecutionState": capability["externalExecutionState"],
                "roundTrip": capability["roundTrip"],
                "diagnosticsAvailable": bool(_diagnostic_sections(raw)),
            },
        }

    async def execute(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        provider: str,
        capability_key: str,
        request: OSSNativeDocumentActionWrite,
        permission_keys: frozenset[PermissionKey] | None = None,
    ) -> dict[str, Any]:
        capability = native_capability(provider, capability_key)
        public_values = dict(request.values)
        request_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "actionId": request.action_id,
                    "values": public_values,
                    "transientSecretFields": sorted(request.transient_secrets),
                    "mode": request.mode,
                    "version": request.version,
                    "context": request.context,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        existing = await self.db.scalar(
            select(OSSNativeCommand).where(
                OSSNativeCommand.workspace_id == workspace_id,
                OSSNativeCommand.provider == provider,
                OSSNativeCommand.capability == capability_key,
                OSSNativeCommand.idempotency_key == request.idempotency_key,
            )
        )
        if existing is not None:
            if existing.command_payload.get("requestFingerprint") != request_fingerprint:
                raise OSSNativeCommandConflictError(
                    "A chave de idempotência já pertence a outra ação nativa."
                )
            stored_permissions = existing.command_payload.get("requiredPermissions")
            if isinstance(stored_permissions, list):
                required_permissions = frozenset(
                    (str(row.get("module") or ""), str(row.get("action") or ""))
                    for row in stored_permissions
                    if isinstance(row, dict) and row.get("module") and row.get("action")
                )
            else:
                required_permissions = frozenset()
            if not required_permissions:
                command_head = re.split(r"[._:-]+", request.action_id.lower(), maxsplit=1)[0]
                mutating = (
                    provider,
                    capability_key,
                    request.action_id,
                ) in _FORCE_MUTATING_ACTIONS or (
                    request.action_id not in _READ_COMMANDS
                    and command_head not in _READ_COMMANDS
                )
                required_permissions = _required_action_permissions(
                    {}, capability, mutating=mutating
                )
            if not _permissions_allow(required_permissions, permission_keys):
                raise PermissionError("Seu perfil não possui permissão para esta ação nativa.")
            existing_result = dict(existing.command_result)
            if (
                request.mode == "configure"
                and existing.status == OSSNativeCommandStatus.succeeded
                and not existing_result.get("persistedLocally")
            ):
                existing_result = await self._persist_configured_action(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    provider=provider,
                    capability_key=capability_key,
                    action_id=request.action_id,
                    public_values=public_values,
                    capability=capability,
                    command_id=existing.id,
                    command_result=existing_result,
                )
            return {
                "status": existing.status.value,
                "message": (
                    "A operação já havia sido registrada; nenhum efeito ou snapshot "
                    "foi repetido. A atualização visual será reconciliada."
                ),
                "version": existing.state_version,
                "document": None,
                "result": existing_result,
                "idempotentReplay": True,
                "refreshPending": True,
            }

        adapter = self._adapter(provider)
        raw = await adapter.snapshot(workspace_id, capability_key, dict(request.context))
        if not isinstance(raw, dict):
            raise TypeError("O adapter nativo deve retornar um objeto JSON.")
        raw = _without_redacted_fields(sanitize_oss_json(raw))
        actions = {
            action["id"]: action
            for action in _actions(raw, capability, permission_keys=permission_keys)
        }
        action = actions.get(request.action_id)
        if action is None:
            raise ValueError("A ação não pertence à capacidade selecionada.")
        if not action["permission"]["allowed"]:
            raise PermissionError("Seu perfil não possui permissão para esta ação nativa.")
        if action["execution"] == "external" and request.mode == "execute":
            raise OSSNativeExternalGateError(
                "A execução externa permanece bloqueada; use simulação ou configuração."
            )
        transient_values = _transient_action_values(action, request)
        _validate_action_values(action, {**public_values, **transient_values})

        state_service = OSSNativeStateService(self.db)
        state_snapshot = await state_service.get_snapshot(
            workspace_id,
            provider,
            capability_key,
        )
        current_document_version = _version(raw, state_snapshot.state.config_version)
        if str(request.version) != str(current_document_version):
            raise OSSNativeVersionConflictError(state_snapshot.state.config_version)
        current_config = dict(state_snapshot.state.configuration)
        config_version = state_snapshot.state.config_version

        nested_payload = public_values.get("payload")
        payload = (
            dict(nested_payload) if isinstance(nested_payload, dict) else dict(public_values)
        )
        payload.setdefault("expectedVersion", request.version)
        payload.setdefault("operationId", request.idempotency_key)
        payload.setdefault("idempotencyKey", request.idempotency_key)
        payload["requestFingerprint"] = request_fingerprint
        payload["requiredPermissions"] = list(action["permission"]["required"])
        for key, value in request.context.items():
            payload.setdefault(key, value)
        payload["actionMode"] = request.mode
        payload["externalNetworkAllowed"] = False
        adapter_payload = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "requestFingerprint",
                "requiredPermissions",
                "actionMode",
                "externalNetworkAllowed",
            }
        }
        adapter_payload.update(transient_values)
        validator = getattr(adapter, "validate", None)
        if not callable(validator):
            raise TypeError(f"O adapter {provider} não implementa validação nativa.")
        try:
            try:
                validation = await validator(
                    workspace_id,
                    actor_user_id,
                    capability_key,
                    request.action_id,
                    adapter_payload,
                    mode=request.mode,
                )
            except Exception as error:
                _reject_transient_secret_exception(error, set(transient_values.values()))
                raise
            if not isinstance(validation, dict):
                raise TypeError("A validação do adapter nativo deve retornar um objeto JSON.")
            validated_result = _local_only_result(validation, source=f"{provider}.validate")
            _reject_transient_secret_echo(validation, set(transient_values.values()))
        except OSSNativeExternalEffectReportedError as error:
            command = await state_service.record_preflight_reconciliation_required(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                provider=provider,
                capability=capability_key,
                data=OSSNativeCommandCreate(
                    command_type=request.action_id,
                    idempotency_key=request.idempotency_key,
                    expected_version=config_version,
                    payload=payload,
                ),
                marker_path=error.marker_path,
                operation=OSSNativeCommandOperation(request.mode),
            )
            raise OSSNativeExecutionError(
                command.id,
                command.error_message or "A validação exige reconciliação humana.",
            ) from error
        if (
            request.mode == "execute"
            and state_snapshot.state.execution_mode != OSSNativeExecutionMode.local_native
        ):
            configured = await state_service.update_config(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                provider=provider,
                capability=capability_key,
                data=OSSNativeConfigPatch(
                    expected_version=config_version,
                    configuration={
                        **current_config,
                        "nativeMode": capability["nativeMode"],
                        "externalExecutionState": capability["externalExecutionState"],
                    },
                    execution_mode=OSSNativeExecutionMode.local_native,
                ),
            )
            config_version = configured.state.config_version

        async def dispatch(dispatch_context) -> dict[str, Any]:
            if request.mode == "configure":
                return {
                    "status": "configured",
                    "validated": True,
                    "validation": validated_result,
                    "persistedLocally": False,
                    "externalNetworkExecuted": False,
                }
            if request.mode == "dry-run":
                return {
                    "status": "dry_run",
                    "validated": True,
                    "command": request.action_id,
                    "candidateFieldCount": len(adapter_payload),
                    "validation": validated_result,
                    "externalNetworkExecuted": False,
                }
            try:
                result = await adapter.execute(
                    workspace_id,
                    actor_user_id,
                    capability_key,
                    request.action_id,
                    adapter_payload,
                    command_id=dispatch_context.command_id,
                )
            except Exception as error:
                _reject_transient_secret_exception(error, set(transient_values.values()))
                raise
            if not isinstance(result, dict):
                raise TypeError("O adapter nativo deve retornar um objeto JSON.")
            safe_result = _local_only_result(result, source=f"{provider}.execute")
            _reject_transient_secret_echo(result, set(transient_values.values()))
            return safe_result

        dispatchers = OSSNativeDispatcherRegistry()
        dispatchers.register(provider, dispatch)
        command = await OSSNativeStateService(
            self.db,
            dispatchers=dispatchers,
        ).execute_command(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            provider=provider,
            capability=capability_key,
            data=OSSNativeCommandCreate(
                command_type=request.action_id,
                idempotency_key=request.idempotency_key,
                expected_version=config_version,
                payload=payload,
            ),
            operation=OSSNativeCommandOperation(request.mode),
        )
        if command.status in {
            OSSNativeCommandStatus.failed,
            OSSNativeCommandStatus.reconciliation_required,
        }:
            raise OSSNativeExecutionError(
                command.id,
                command.error_message or "A operação local falhou.",
            )
        if command.status == OSSNativeCommandStatus.blocked:
            raise OSSNativeExternalGateError(
                command.error_message or "O gate do servidor bloqueou a operação."
            )
        if request.mode == "configure":
            configured_result = await self._persist_configured_action(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                provider=provider,
                capability_key=capability_key,
                action_id=request.action_id,
                public_values=public_values,
                capability=capability,
                command_id=command.id,
                command_result=command.result,
            )
            command.result.clear()
            command.result.update(configured_result)
        refresh_pending = False
        try:
            document = await self.document(
                workspace_id,
                provider,
                capability_key,
                {**request.context, "refresh": True},
                permission_keys=permission_keys,
            )
        except Exception:
            await self.db.rollback()
            document = None
            refresh_pending = True
        base_message = (
            "Simulação registrada sem efeito externo."
            if request.mode == "dry-run"
            else "Configuração local registrada."
            if request.mode == "configure"
            else "Operação nativa concluída."
        )
        return {
            "status": command.status.value,
            "message": (
                f"{base_message} A atualização visual será reconciliada."
                if refresh_pending
                else base_message
            ),
            "version": document["version"] if document else command.state_version,
            "document": document,
            "result": command.result,
            "idempotentReplay": command.deduplicated,
            "refreshPending": refresh_pending,
        }

    async def update_configuration(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        provider: str,
        capability_key: str,
        data: OSSNativeConfigPatch,
    ):
        """Persist configuration through the KLM-native contract only."""

        native_capability(provider, capability_key)
        configuration = dict(data.configuration) if data.configuration is not None else None
        if configuration is not None:
            _reject_positive_external_effect_markers(configuration)
        return await OSSNativeStateService(self.db).update_config(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            provider=provider,
            capability=capability_key,
            data=data,
        )


__all__ = [
    "OSSNativeCapabilityNotFoundError",
    "OSSNativeDocumentService",
    "OSSNativeExecutionError",
    "OSSNativeExternalEffectContractError",
    "OSSNativeExternalGateError",
    "native_capability",
    "required_capability_manage_permissions",
    "required_capability_read_permissions",
]
