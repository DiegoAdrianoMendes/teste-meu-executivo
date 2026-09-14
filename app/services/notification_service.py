"""
Service: NotificationService
Lógica de negócio para notificações do sistema.
Suporta: listar, marcar como lida (individual e em massa), criar notificações.
Integrado com WebSocket para entrega em tempo real.
Notificações são scoped por workspace_id para isolamento multi-tenant.
"""
from collections.abc import Sequence
from typing import TYPE_CHECKING, List, Optional
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.post_commit import queue_realtime_event
from app.models.notification import Notification, NotificationType
from app.models.omnichannel_v6 import WorkspaceTeamMember
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership

if TYPE_CHECKING:
    from app.models.conversation_collaboration import ConversationMention


class NotificationService:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_for_user(
        self,
        user_id: UUID,
        workspace_id: Optional[UUID] = None,
        skip: int = 0,
        limit: int = 50,
        unread_only: bool = False,
    ) -> List[Notification]:
        """
        Retorna notificações do usuário, ordenadas por mais recentes.
        Filtra por workspace_id quando fornecido — notificações globais
        (workspace_id IS NULL) são sempre incluídas.
        """
        query = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.timestamp.desc())
            .offset(skip)
            .limit(limit)
        )
        if workspace_id:
            # Mostrar: notificações deste workspace OU notificações globais (sem workspace)
            from sqlalchemy import or_
            query = query.where(
                or_(
                    Notification.workspace_id == workspace_id,
                    Notification.workspace_id.is_(None),
                )
            )
        if unread_only:
            query = query.where(Notification.is_read == False)  # noqa: E712

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def mark_as_read(
        self,
        notification_id: UUID,
        user_id: UUID,
        *,
        workspace_id: UUID,
    ) -> bool:
        """Marca uma notificação específica como lida."""
        from sqlalchemy import or_

        result = await self.db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                or_(
                    Notification.workspace_id == workspace_id,
                    Notification.workspace_id.is_(None),
                ),
            )
        )
        notification = result.scalar_one_or_none()
        if not notification:
            return False
        notification.is_read = True
        await self.db.flush()
        return True

    async def mark_all_as_read(self, user_id: UUID, workspace_id: Optional[UUID] = None) -> int:
        """
        Marca todas as notificações não lidas do usuário como lidas.
        Quando workspace_id for fornecido, limita ao workspace atual.
        Retorna o número de notificações atualizadas.
        """
        from sqlalchemy import or_
        where_clauses = [Notification.user_id == user_id, Notification.is_read == False]  # noqa: E712
        if workspace_id:
            where_clauses.append(
                or_(
                    Notification.workspace_id == workspace_id,
                    Notification.workspace_id.is_(None),
                )
            )
        result = await self.db.execute(
            update(Notification)
            .where(*where_clauses)
            .values(is_read=True)
            .returning(Notification.id)
        )
        updated = list(result.scalars().all())
        await self.db.flush()
        logger.info("notifications_marked_all_read", user_id=str(user_id), count=len(updated))
        return len(updated)

    async def create(
        self,
        user_id: UUID,
        title: str,
        message: str,
        notification_type: NotificationType = NotificationType.info,
        matter_id: Optional[UUID] = None,
        workspace_id: Optional[UUID] = None,
    ) -> Notification:
        """
        Cria uma notificação para um usuário.
        workspace_id deve ser passado para isolar notificações por workspace.
        """
        notification = Notification(
            user_id=user_id,
            matter_id=matter_id,
            workspace_id=workspace_id,
            title=title,
            message=message,
            notification_type=notification_type,
            is_read=False,
        )
        self.db.add(notification)
        await self.db.flush()
        logger.info(
            "notification_created",
            user_id=str(user_id),
            notification_id=str(notification.id),
            workspace_id=str(workspace_id) if workspace_id else None,
            type=notification_type.value,
        )
        return notification

    async def create_for_structured_mentions(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        source_message_id: UUID,
        matter_id: UUID | None,
        actor_label: str,
        mentions: Sequence["ConversationMention"],
    ) -> list[Notification]:
        """Create one tenant-bound inbox signal per resolved recipient.

        The deterministic primary key makes replays and concurrent retries
        idempotent without requiring a new notification-table migration.
        """

        direct_user_ids = {
            mention.mentioned_user_id
            for mention in mentions
            if mention.mentioned_user_id is not None
        }
        team_ids = {
            mention.mentioned_team_id
            for mention in mentions
            if mention.mentioned_team_id is not None
        }
        recipients: dict[UUID, str] = {
            user_id: "user" for user_id in direct_user_ids
        }
        if team_ids:
            team_members = await self.db.execute(
                select(WorkspaceTeamMember.user_id)
                .join(
                    WorkspaceMembership,
                    (WorkspaceMembership.id == WorkspaceTeamMember.membership_id)
                    & (WorkspaceMembership.workspace_id == WorkspaceTeamMember.workspace_id)
                    & (WorkspaceMembership.user_id == WorkspaceTeamMember.user_id),
                )
                .join(User, User.id == WorkspaceTeamMember.user_id)
                .where(
                    WorkspaceTeamMember.workspace_id == workspace_id,
                    WorkspaceTeamMember.team_id.in_(team_ids),
                    WorkspaceMembership.status == MembershipStatus.active,
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
            for user_id in team_members.scalars().all():
                recipients.setdefault(user_id, "team")

        created: list[Notification] = []
        safe_actor_label = actor_label.strip() or "Uma pessoa da equipe"
        for user_id, mention_scope in sorted(
            recipients.items(),
            key=lambda item: str(item[0]),
        ):
            notification_id = uuid5(
                NAMESPACE_URL,
                (
                    "klm:conversation-mention:"
                    f"{workspace_id}:{thread_id}:{source_message_id}:{user_id}"
                ),
            )
            existing = await self.db.scalar(
                select(Notification).where(Notification.id == notification_id)
            )
            if existing is not None:
                if (
                    existing.workspace_id != workspace_id
                    or existing.user_id != user_id
                    or existing.matter_id != matter_id
                ):
                    raise RuntimeError("Identificador de notificação de menção em conflito.")
                created.append(existing)
                continue

            notification = Notification(
                id=notification_id,
                user_id=user_id,
                matter_id=matter_id,
                workspace_id=workspace_id,
                title="Menção em conversa",
                message=(
                    f"{safe_actor_label} mencionou você "
                    f"{'por sua equipe ' if mention_scope == 'team' else ''}"
                    "em uma conversa KLM."
                ),
                notification_type=NotificationType.info,
                is_read=False,
            )
            was_created = False
            try:
                async with self.db.begin_nested():
                    self.db.add(notification)
                    await self.db.flush()
                    was_created = True
            except IntegrityError:
                notification = await self.db.scalar(
                    select(Notification).where(Notification.id == notification_id)
                )
                if notification is None:
                    raise
            created.append(notification)
            if not was_created:
                continue
            queue_realtime_event(
                self.db,
                workspace_id=workspace_id,
                event_type="notification.mention.created",
                data={
                    "notificationId": str(notification.id),
                    "userId": str(user_id),
                    "threadId": str(thread_id),
                    "messageId": str(source_message_id),
                },
            )
            logger.info(
                "mention_notification_created",
                notification_id=str(notification.id),
                user_id=str(user_id),
                workspace_id=str(workspace_id),
                thread_id=str(thread_id),
                message_id=str(source_message_id),
            )
        return created

    async def get_unread_count(self, user_id: UUID, workspace_id: Optional[UUID] = None) -> int:
        """Retorna o número de notificações não lidas para o badge do Topbar."""
        from sqlalchemy import func, or_
        where_clauses = [Notification.user_id == user_id, Notification.is_read == False]  # noqa: E712
        if workspace_id:
            where_clauses.append(
                or_(
                    Notification.workspace_id == workspace_id,
                    Notification.workspace_id.is_(None),
                )
            )
        result = await self.db.execute(
            select(func.count(Notification.id)).where(*where_clauses)
        )
        return result.scalar_one() or 0
