"""
Schemas Pydantic para User/Agent.
Compatíveis com a interface Agent e AuthUser do frontend TypeScript.

Mapeamento de campos (backend → frontend):
  agent_type → type    (frontend: Agent.type)
  avatar_url → avatar  (frontend: Agent.avatar)
  is_active  → isActive
  access_token → accessToken  (frontend: TokenResponse.accessToken)

Códigos de erro relacionados:
  AUTH-001  Token ausente ou inválido
  AUTH-002  Token expirado
  AUTH-003  Credenciais incorretas
  USR-001   Usuário não encontrado
  USR-002   Email já cadastrado
  USR-003   Permissão insuficiente
  USR-004   Não é possível desativar o próprio usuário
  USR-005   Usuário inativo
"""
from typing import Literal, Optional
from uuid import UUID

from pydantic import AliasChoices, Field

from app.core.password_policy import PasswordValue
from app.models.user import AgentType, UserRole
from app.schemas.base import CamelCaseSchema, WriteSchema
from app.schemas.email import CanonicalEmail
from app.schemas.workspace import SessionContextRead


class UserCreate(WriteSchema):
    name: str
    email: Optional[CanonicalEmail] = None
    phone: Optional[str] = None
    password: Optional[PasswordValue] = None
    agent_type: AgentType = AgentType.human
    role: UserRole = UserRole.lawyer
    initials: str = ""
    color: str = "#6366f1"
    avatar_url: Optional[str] = None


class UserUpdate(WriteSchema):
    """Atualização parcial de dados do usuário."""
    name: Optional[str] = None
    email: Optional[CanonicalEmail] = None
    phone: Optional[str] = None
    password: Optional[PasswordValue] = None
    role: Optional[UserRole] = None
    initials: Optional[str] = None
    color: Optional[str] = None
    avatar_url: Optional[str] = None
    is_active: Optional[bool] = None


class UserRead(CamelCaseSchema):
    """
    Compatível com a interface AuthUser do frontend:
      id, name, role, initials, email

    E com a interface Agent:
      id, name, type, avatar, initials, role, color, isActive

    Campos com alias explícito (divergem do padrão camelCase automático):
      agent_type → 'type'   (frontend usa Agent.type)
      avatar_url → 'avatar' (frontend usa Agent.avatar)
    """
    id: UUID
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    # Frontend: Agent.type — alias explícito pois 'agentType' ≠ 'type'
    type: Optional[AgentType] = Field(
        default=None,
        validation_alias=AliasChoices("type", "agent_type", "agentType"),
    )
    role: str
    initials: str
    color: str
    # Frontend: Agent.avatar — alias explícito pois 'avatarUrl' ≠ 'avatar'
    avatar: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("avatar", "avatar_url", "avatarUrl"),
    )
    is_active: bool
    is_internal_staff: bool = False
    is_platform_admin: bool = False
    is_platform_root: bool = False
    platform_role_package: Optional[str] = None
    platform_access_active: bool = False
    platform_permissions: list[dict[str, str]] = Field(default_factory=list)
    requires_password_change: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "requires_password_change",
            "requiresPasswordChange",
            "password_change_required",
        ),
    )


class AgentSummary(CamelCaseSchema):
    """Versão compacta para exibição no frontend (ex: avatar no card)."""
    id: UUID
    name: str
    initials: str
    color: str
    type: Optional[AgentType] = Field(
        default=None,
        validation_alias=AliasChoices("type", "agent_type", "agentType"),
    )
    role: str


class TokenResponse(CamelCaseSchema):
    """
    Resposta do login.
    O frontend espera camelCase: accessToken, tokenType, user.
    O campo 'user' popula o estado currentUser do AppContext.
    """
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    user: UserRead
    session: SessionContextRead


class LoginRequest(WriteSchema):
    email: CanonicalEmail
    password: str
    audience: Literal["workspace", "platform"] = "workspace"


class RegisterRequest(WriteSchema):
    name: str
    email: CanonicalEmail
    workspace_name: str
    password: PasswordValue
    plan: str = "pro"
