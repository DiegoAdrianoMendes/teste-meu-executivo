"""Materializa exceção como tarefa humana — cross-reference tasks<->legal_exceptions.

Decisão do dono: uma `LegalException` deixa de só existir na Central de
Exceções — ao ser aberta, `ExceptionCenterService.open_exception` também
cria uma `Task` no MESMO sistema de to-do que o time já usa por caso
(`tasks`, Kanban/lista de tarefas do Matter). Não é uma fila paralela:
`legal_exceptions` continua sendo o registro de auditoria (nada é
removido) — a novidade é a materialização como item humano acionável.

Duas colunas novas, nulas, formando a referência cruzada pedida (a task
aponta a exceção e vice-versa), 1:1 enquanto o vínculo existir:

  * tasks.source_exception_id -> legal_exceptions.id (SET NULL) — de qual
    exceção esta tarefa nasceu.
  * legal_exceptions.linked_task_id -> tasks.id (SET NULL) — a tarefa já
    materializada para esta exceção, para o dedupe idempotente
    (`ExceptionCenterService.open_exception` só cria task na primeira
    abertura; reabrir a mesma intenção retorna a exceção existente antes
    de qualquer criação de task, então a idempotência é herdada de graça).

Índices únicos parciais (mesmo padrão de `ux_tasks_matter_idempotency`,
`uq_legal_exception_open_intent`): garantem 1 task por exceção e 1
exceção por task no nível do banco, não só na aplicação.

FK real (`create_foreign_key`) só em Postgres — mesmo padrão já usado por
`fe6f8a0b2d75` (schedule protocol) para colunas adicionadas via
`add_column`: SQLite de teste não sofre ALTER TABLE ADD CONSTRAINT bem, e
os testes de RLS/tenant já cobrem o enforcement real em Postgres.

Sem FK composta [workspace_id, id]: `tasks` não carrega `workspace_id`
próprio hoje — é escopado via `matter_id -> matters.workspace_id`, RLS por
parent policy (`PARENT_WORKSPACE_POLICIES["tasks"]`). Os dois lados desta
referência só são escritos dentro de
`ExceptionCenterService.open_exception`/`resolve`, que já validam
`matter.workspace_id == exception.workspace_id` antes de tocar qualquer
linha — não existe caminho de escrita que atravesse tenant sem passar por
ali, então uma FK composta aqui defenderia contra um caminho de escrita
que não existe.

Restrito a exceções COM `matter_id`: exceções sem vínculo de caso (ex.:
`publicacao_sem_vinculo_confiavel` nascida sem `MonitoredProcess`) não
geram task porque `tasks.matter_id` é obrigatório — tornar isso opcional
exigiria migrar `tasks` inteira de RLS por join para RLS direta
(`workspace_id` próprio), refactor de segurança maior que o escopo desta
mudança. Essas exceções continuam 100% visíveis e acionáveis na própria
Central de Exceções (nada foi removido); só não duplicam como Task ainda.

Revision ID: d7e9f1a3c5b6
Revises: c67346a8bba6
Create Date: 2026-07-27 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import FlexibleUUID

revision: str = "d7e9f1a3c5b6"
down_revision: str | Sequence[str] | None = "c67346a8bba6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASKS_TABLE = "tasks"
EXCEPTIONS_TABLE = "legal_exceptions"

TASK_SOURCE_EXCEPTION_INDEX = "ux_tasks_source_exception_id"
EXCEPTION_LINKED_TASK_INDEX = "ux_legal_exceptions_linked_task_id"

TASK_SOURCE_EXCEPTION_FK = "fk_tasks_source_exception_id"
EXCEPTION_LINKED_TASK_FK = "fk_legal_exceptions_linked_task_id"


def upgrade() -> None:
    op.add_column(
        TASKS_TABLE,
        sa.Column("source_exception_id", FlexibleUUID(), nullable=True),
    )
    op.add_column(
        EXCEPTIONS_TABLE,
        sa.Column("linked_task_id", FlexibleUUID(), nullable=True),
    )

    op.create_index(
        TASK_SOURCE_EXCEPTION_INDEX,
        TASKS_TABLE,
        ["source_exception_id"],
        unique=True,
        postgresql_where=sa.text("source_exception_id IS NOT NULL"),
        sqlite_where=sa.text("source_exception_id IS NOT NULL"),
    )
    op.create_index(
        EXCEPTION_LINKED_TASK_INDEX,
        EXCEPTIONS_TABLE,
        ["linked_task_id"],
        unique=True,
        postgresql_where=sa.text("linked_task_id IS NOT NULL"),
        sqlite_where=sa.text("linked_task_id IS NOT NULL"),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            TASK_SOURCE_EXCEPTION_FK,
            TASKS_TABLE,
            EXCEPTIONS_TABLE,
            ["source_exception_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            EXCEPTION_LINKED_TASK_FK,
            EXCEPTIONS_TABLE,
            TASKS_TABLE,
            ["linked_task_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(EXCEPTION_LINKED_TASK_FK, EXCEPTIONS_TABLE, type_="foreignkey")
        op.drop_constraint(TASK_SOURCE_EXCEPTION_FK, TASKS_TABLE, type_="foreignkey")

    op.drop_index(EXCEPTION_LINKED_TASK_INDEX, table_name=EXCEPTIONS_TABLE)
    op.drop_index(TASK_SOURCE_EXCEPTION_INDEX, table_name=TASKS_TABLE)

    op.drop_column(EXCEPTIONS_TABLE, "linked_task_id")
    op.drop_column(TASKS_TABLE, "source_exception_id")
