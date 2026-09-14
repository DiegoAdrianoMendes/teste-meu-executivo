from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db import platform_control
from app.db.platform_control import (
    CLEAR_PLATFORM_CONTROL_CONTEXT_SQL,
    PLATFORM_ROOT_SCOPE,
    PlatformControlContextError,
    _set_commercial_portability_rebind_context,
    _set_commercial_portability_restore_rebind_context,
    clear_platform_control_context,
    set_platform_control_context,
)


class _Session:
    def __init__(self, dialect: str) -> None:
        self._bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self.calls: list[dict[str, str]] = []

    def get_bind(self):
        return self._bind

    async def execute(self, statement, params=None):
        self.calls.append(params if params is not None else {"statement": statement})


@pytest.mark.asyncio
async def test_platform_control_context_is_transaction_attributable() -> None:
    session = _Session("postgresql")
    actor_id = uuid4()

    await set_platform_control_context(
        session,
        actor_user_id=actor_id,
        module="commercial_contracts",
        action="manage",
    )

    assert session.calls == [
        {
            "scope": "commercial_contracts",
            "actor_user_id": str(actor_id),
            "action": "commercial_contracts:manage",
        }
    ]


@pytest.mark.asyncio
async def test_platform_root_context_is_explicit_not_a_permission_alias() -> None:
    session = _Session("postgresql")

    await set_platform_control_context(
        session,
        actor_user_id=uuid4(),
        module="plans",
        action="publish",
        is_platform_root=True,
    )

    assert session.calls[0]["scope"] == PLATFORM_ROOT_SCOPE
    assert session.calls[0]["action"] == "plans:publish"


@pytest.mark.asyncio
async def test_platform_control_context_can_be_cleared_without_touching_tenant_context() -> None:
    session = _Session("postgresql")

    await clear_platform_control_context(session)

    assert session.calls == [{"statement": CLEAR_PLATFORM_CONTROL_CONTEXT_SQL}]


@pytest.mark.asyncio
async def test_platform_control_context_clear_is_noop_outside_postgresql() -> None:
    session = _Session("sqlite")

    await clear_platform_control_context(session)

    assert session.calls == []


@pytest.mark.asyncio
async def test_platform_control_context_rejects_unbounded_setting_names() -> None:
    session = _Session("postgresql")

    with pytest.raises(PlatformControlContextError):
        await set_platform_control_context(
            session,
            actor_user_id=uuid4(),
            module="plans'; select true; --",
            action="manage",
        )

    assert session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("is_platform_root", (False, True))
@pytest.mark.parametrize("reserved_action", ("rebind", "restore_rebind"))
async def test_generic_platform_context_rejects_reserved_portability_namespace(
    is_platform_root: bool,
    reserved_action: str,
) -> None:
    session = _Session("postgresql")

    with pytest.raises(
        PlatformControlContextError,
        match="Contexto interno de portabilidade",
    ):
        await set_platform_control_context(
            session,
            actor_user_id=uuid4(),
            module="commercial_portability",
            action=reserved_action,
            is_platform_root=is_platform_root,
        )

    assert session.calls == []
    assert "_set_commercial_portability_rebind_context" not in platform_control.__all__
    assert (
        "_set_commercial_portability_restore_rebind_context"
        not in platform_control.__all__
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("is_platform_root", (False, True))
@pytest.mark.parametrize(
    ("setter", "expected_action"),
    (
        (
            _set_commercial_portability_rebind_context,
            "commercial_portability:rebind",
        ),
        (
            _set_commercial_portability_restore_rebind_context,
            "commercial_portability:restore_rebind",
        ),
    ),
)
async def test_internal_portability_setter_uses_bounded_scope_even_for_root(
    is_platform_root: bool,
    setter,
    expected_action: str,
) -> None:
    session = _Session("postgresql")
    actor_id = uuid4()

    await setter(
        session,
        actor_user_id=actor_id,
        is_platform_root=is_platform_root,
    )

    assert session.calls == [
        {
            "scope": "commercial_portability",
            "actor_user_id": str(actor_id),
            "action": expected_action,
        }
    ]


def test_internal_portability_setter_has_one_reviewed_application_caller() -> None:
    app_root = Path(__file__).resolve().parents[2] / "app"
    private_setters = {
        "_set_commercial_portability_rebind_context",
        "_set_commercial_portability_restore_rebind_context",
    }
    callers: dict[str, list[str]] = {name: [] for name in private_setters}
    for path in app_root.rglob("*.py"):
        if path.name == "platform_control.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_aliases = {
            alias.asname or alias.name: alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "app.db.platform_control"
            for alias in node.names
            if alias.name in private_setters
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called_setter: str | None = None
            if isinstance(node.func, ast.Name):
                if node.func.id in private_setters:
                    called_setter = node.func.id
                else:
                    called_setter = imported_aliases.get(node.func.id)
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in private_setters
            ):
                called_setter = node.func.attr
            if called_setter is not None:
                callers[called_setter].append(
                    str(path.relative_to(app_root.parent))
                )

    assert callers == {
        "_set_commercial_portability_rebind_context": [
            "app/services/commercial_legacy_bridge_service.py"
        ],
        "_set_commercial_portability_restore_rebind_context": [
            "app/services/commercial_legacy_bridge_service.py"
        ],
    }
