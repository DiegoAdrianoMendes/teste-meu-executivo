"""Contratos de tarefa, colaboração, revisão e histórico."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.task import (
    TaskContextType,
    TaskEventType,
    TaskOrigin,
    TaskParticipantRole,
    TaskParticipantState,
    TaskPriority,
    TaskReviewState,
    TaskStatus,
)
from app.schemas.base import CamelCaseSchema, WriteSchema


def _aware_datetime(value: datetime | None) -> datetime | None:
    if value is not None and value.utcoffset() is None:
        raise ValueError("Prazos devem incluir timezone explícito.")
    return value


class TaskCommand(StrEnum):
    start = "start"
    block = "block"
    unblock = "unblock"
    submit_for_review = "submit-for-review"
    approve = "approve"
    request_changes = "request-changes"
    complete = "complete"
    reopen = "reopen"
    cancel = "cancel"


class TaskListScope(StrEnum):
    mine = "mine"
    assigned_by_me = "assigned_by_me"
    reviews = "reviews"
    team = "team"
    overdue = "overdue"
    blocked = "blocked"
    completed = "completed"
    # Escopo reservado a uma sessão administrativa ativa e vinculada a um
    # único workspace. Identidades tenant não podem usá-lo para ampliar sua
    # visibilidade além das tarefas das quais participam.
    workspace = "workspace"


class TaskParticipantWrite(WriteSchema):
    user_id: UUID
    role: TaskParticipantRole
    due_at: datetime | None = None
    required_for_completion: bool = True

    @field_validator("due_at")
    @classmethod
    def validate_due_at(cls, value: datetime | None) -> datetime | None:
        return _aware_datetime(value)

    @model_validator(mode="after")
    def completion_policy_only_applies_to_assignees(self) -> "TaskParticipantWrite":
        if self.role in {TaskParticipantRole.owner, TaskParticipantRole.assignee}:
            if (
                self.role == TaskParticipantRole.owner
                and "required_for_completion" not in self.model_fields_set
            ):
                # Owners may execute the task, but ownership alone must not
                # silently become a completion blocker for legacy/simple tasks.
                self.required_for_completion = False
        else:
            if "required_for_completion" in self.model_fields_set and self.required_for_completion:
                raise ValueError("Somente owner ou executor pode ser obrigatório para conclusão.")
            self.required_for_completion = False
        return self


class TaskParticipantRead(CamelCaseSchema):
    id: UUID
    membership_id: UUID
    user_id: UUID
    role: TaskParticipantRole
    due_at: datetime | None = None
    state: TaskParticipantState
    required_for_completion: bool
    label_snapshot: str
    active: bool
    created_at: datetime
    updated_at: datetime


class TaskReviewRoundRead(CamelCaseSchema):
    id: UUID
    round_number: int
    reviewer_user_id: UUID
    state: TaskReviewState
    due_at: datetime | None = None
    submitted_at: datetime
    submitted_by_user_id: UUID | None = None
    resolved_at: datetime | None = None
    resolved_by_user_id: UUID | None = None
    decision_comment: str | None = None


class TaskCreate(WriteSchema):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    # Compatibilidade: quando participants não é enviado, assigned_to vira owner.
    assigned_to: UUID | None = None
    owner_id: UUID | None = None
    participants: list[TaskParticipantWrite] | None = Field(default=None, max_length=200)
    due_date: str | None = Field(default=None, max_length=20)
    due_at: datetime | None = None
    review_due_at: datetime | None = None
    review_required: bool = False
    priority: TaskPriority | None = None
    is_ai_generated: bool = False
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=180)

    @field_validator("due_at", "review_due_at")
    @classmethod
    def validate_aware_deadlines(cls, value: datetime | None) -> datetime | None:
        return _aware_datetime(value)

    @model_validator(mode="after")
    def validate_review_deadlines(self) -> "TaskCreate":
        if self.participants is not None:
            owners = [
                item for item in self.participants if item.role == TaskParticipantRole.owner
            ]
            reviewers = [
                item
                for item in self.participants
                if item.role == TaskParticipantRole.reviewer
            ]
            if len(owners) != 1:
                raise ValueError("A tarefa deve possuir exatamente um owner ativo.")
            if len(reviewers) > 1:
                raise ValueError("A tarefa pode possuir no máximo um reviewer ativo.")
            requested_owner = self.owner_id or self.assigned_to
            if requested_owner is not None and owners[0].user_id != requested_owner:
                raise ValueError("ownerId/assignedTo diverge do owner informado em participants.")
        reviewer = next(
            (item for item in self.participants or [] if item.role == TaskParticipantRole.reviewer),
            None,
        )
        if self.review_required and reviewer is None:
            raise ValueError("Tarefa com revisão exige reviewer.")
        if self.review_due_at is not None and not (self.review_required or reviewer is not None):
            raise ValueError("Prazo de revisão exige reviewer ativo.")
        if self.due_at is not None:
            deadlines = [self.review_due_at]
            deadlines.extend(item.due_at for item in self.participants or [])
            if any(
                item is not None and item.astimezone(UTC) > self.due_at.astimezone(UTC)
                for item in deadlines
            ):
                raise ValueError("Prazo individual ou de revisão não pode superar o prazo geral.")
        return self


class TaskUpdate(WriteSchema):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    # Aceito no contrato por compatibilidade; mudanças de status usam comandos.
    status: TaskStatus | None = None
    assigned_to: UUID | None = None
    due_date: str | None = Field(default=None, max_length=20)
    due_at: datetime | None = None
    review_due_at: datetime | None = None
    review_required: bool | None = None
    priority: TaskPriority | None = None

    @field_validator("due_at", "review_due_at")
    @classmethod
    def validate_aware_deadlines(cls, value: datetime | None) -> datetime | None:
        return _aware_datetime(value)

    @model_validator(mode="after")
    def validate_review_update(self) -> "TaskUpdate":
        if self.review_required is False and self.review_due_at is not None:
            raise ValueError("Não informe prazo de revisão ao desativar a revisão.")
        if (
            self.due_at is not None
            and self.review_due_at is not None
            and self.review_due_at.astimezone(UTC) > self.due_at.astimezone(UTC)
        ):
            raise ValueError("Prazo de revisão não pode superar o prazo geral.")
        return self


class TaskParticipantsReplace(WriteSchema):
    expected_version: int = Field(ge=1)
    participants: list[TaskParticipantWrite] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def validate_roles(self) -> "TaskParticipantsReplace":
        owners = [item for item in self.participants if item.role == TaskParticipantRole.owner]
        reviewers = [
            item for item in self.participants if item.role == TaskParticipantRole.reviewer
        ]
        if len(owners) != 1:
            raise ValueError("A tarefa deve possuir exatamente um owner ativo.")
        if len(reviewers) > 1:
            raise ValueError("A tarefa pode possuir no máximo um reviewer ativo.")
        return self


class TaskParticipantStateUpdate(WriteSchema):
    expected_version: int = Field(ge=1)
    state: TaskParticipantState
    reason: str = Field(min_length=8, max_length=500)


class TaskCommandRequest(WriteSchema):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=8, max_length=500)
    comment: str | None = Field(default=None, min_length=1, max_length=10000)


class TaskCommentCreate(WriteSchema):
    expected_version: int = Field(ge=1)
    comment: str = Field(min_length=1, max_length=10000)
    document_ids: list[UUID] = Field(default_factory=list, max_length=20)
    mentioned_user_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def reject_duplicate_collaboration_targets(self) -> "TaskCommentCreate":
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("Um documento não pode ser anexado duas vezes ao mesmo comentário.")
        if len(set(self.mentioned_user_ids)) != len(self.mentioned_user_ids):
            raise ValueError("Um usuário não pode ser mencionado duas vezes no mesmo comentário.")
        return self


class TaskLabelWrite(WriteSchema):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class TaskLabelsReplace(WriteSchema):
    expected_version: int = Field(ge=1)
    labels: list[TaskLabelWrite] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def reject_duplicate_labels(self) -> "TaskLabelsReplace":
        normalized = [" ".join(item.name.split()).casefold() for item in self.labels]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Etiquetas duplicadas não são permitidas.")
        return self


class TaskDependencyCreate(WriteSchema):
    expected_version: int = Field(ge=1)
    predecessor_task_id: UUID | None = None
    predecessor_ref: str | None = Field(default=None, min_length=1, max_length=1024)
    reason: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def require_one_predecessor_reference(self) -> "TaskDependencyCreate":
        if (self.predecessor_task_id is None) == (self.predecessor_ref is None):
            raise ValueError("Informe exatamente uma referência de tarefa predecessora.")
        return self


class TaskDependencyRemove(WriteSchema):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=500)


class TaskSubtaskCreate(TaskCreate):
    expected_parent_version: int = Field(ge=1)


class TaskLabelRead(CamelCaseSchema):
    id: UUID
    name: str
    color: str


class TaskLabelOptionRead(TaskLabelRead):
    """Etiqueta persistente com referência opaca para filtros operacionais."""

    handle: str


class TaskDependencyRead(CamelCaseSchema):
    id: UUID
    predecessor_task_id: UUID
    successor_task_id: UUID
    created_at: datetime


class TaskDependencyCandidateRead(CamelCaseSchema):
    handle: str
    label: str
    status: TaskStatus
    due_at: datetime | None = None


class TaskAttachmentRead(CamelCaseSchema):
    id: UUID
    task_event_id: UUID
    document_id: UUID
    name_snapshot: str
    mime_type_snapshot: str | None = None
    created_at: datetime


class TaskMentionRead(CamelCaseSchema):
    id: UUID
    task_event_id: UUID
    mentioned_user_id: UUID
    label_snapshot: str
    created_at: datetime


class TaskChecklistCreate(WriteSchema):
    expected_task_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=240)


class TaskChecklistItemCreate(WriteSchema):
    expected_task_version: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=500)


class TaskChecklistItemStateUpdate(WriteSchema):
    expected_task_version: int = Field(ge=1)
    expected_item_version: int = Field(ge=1)
    completed: bool


class TaskChecklistArchive(WriteSchema):
    expected_task_version: int = Field(ge=1)
    expected_checklist_version: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=500)


class TaskChecklistItemRead(CamelCaseSchema):
    id: UUID
    label: str
    position: int
    completed_at: datetime | None = None
    completed_by_user_id: UUID | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class TaskChecklistRead(CamelCaseSchema):
    id: UUID
    task_id: UUID
    title: str
    position: int
    version: int
    items: list[TaskChecklistItemRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TaskEventRead(CamelCaseSchema):
    id: UUID
    task_id: UUID
    actor_user_id: UUID | None = None
    actor_label_snapshot: str
    actor_role_snapshot: str
    actor_type: str
    event_type: TaskEventType
    before_data: dict | None = None
    after_data: dict | None = None
    changed_paths: list[str]
    comment: str | None = None
    reason: str
    reason_source: str
    correlation_id: str
    session_type: str
    administrative_session_id: UUID | None = None
    support_grant_id: UUID | None = None
    resource_version_before: int | None = None
    resource_version_after: int | None = None
    occurred_at: datetime


class TaskRead(CamelCaseSchema):
    """Compatível com o Task legado e aditivo para colaboração."""

    id: UUID
    workspace_id: UUID
    context_type: TaskContextType
    origin: TaskOrigin
    matter_id: UUID | None = None
    client_id: UUID | None = None
    contract_id: UUID | None = None
    origin_resource_id: UUID | None = None
    origin_label: str | None = None
    parent_task_id: UUID | None = None
    title: str
    description: str | None = None
    status: TaskStatus
    priority: TaskPriority | None = None
    assigned_to: UUID | None = None
    due_date: str | None = None
    due_at: datetime | None = None
    legacy_due_text: str | None = None
    review_due_at: datetime | None = None
    review_required: bool = False
    review_state: TaskReviewState = TaskReviewState.not_required
    review_round: int = 0
    version: int = 1
    created_by_user_id: UUID | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_by_user_id: UUID | None = None
    cancellation_reason: str | None = None
    archived_at: datetime | None = None
    archived_by_user_id: UUID | None = None
    archive_reason: str | None = None
    participants: list[TaskParticipantRead] = Field(default_factory=list)
    review_rounds: list[TaskReviewRoundRead] = Field(default_factory=list)
    is_ai_generated: bool
    created_at: datetime
    source_exception_id: UUID | None = None


class TaskSummaryRead(TaskRead):
    matter_label: str | None = None
    client_label: str | None = None
    my_roles: list[TaskParticipantRole] = Field(default_factory=list)


class TaskPage(CamelCaseSchema):
    items: list[TaskSummaryRead]
    next_cursor: str | None = None
    has_more: bool


class TaskMemberOptionRead(CamelCaseSchema):
    id: UUID
    handle: str
    label: str
    role: str


class TaskMemberOptionPage(CamelCaseSchema):
    items: list[TaskMemberOptionRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class TaskMatterOptionRead(CamelCaseSchema):
    id: UUID
    handle: str
    label: str
    client_label: str


class TaskEventPage(CamelCaseSchema):
    items: list[TaskEventRead]
    total: int


class TaskLegalResourcesRead(CamelCaseSchema):
    labels: list[TaskLabelRead] = Field(default_factory=list)
    subtasks: list[TaskRead] = Field(default_factory=list)
    dependencies: list[TaskDependencyRead] = Field(default_factory=list)
    attachments: list[TaskAttachmentRead] = Field(default_factory=list)
    mentions: list[TaskMentionRead] = Field(default_factory=list)
