from __future__ import annotations

import hashlib

import pytest

from app.ai.context_compiler import (
    CONTEXT_COMPILER_VERSION,
    CONTEXT_SCHEMA_VERSION,
    ContextCompiler,
    ContextEvidence,
    ContextMessage,
    ContextProvenance,
    ContextSummary,
)
from app.ai.errors import (
    ContextBudgetExceededError,
    ContextCompilationError,
    ContextScopeError,
)

WORKSPACE_ID = "workspace-a"
MATTER_ID = "matter-a"


def provenance(source_type: str, source_id: str, **overrides) -> ContextProvenance:
    values = {
        "workspace_id": WORKSPACE_ID,
        "matter_id": MATTER_ID,
        "source_type": source_type,
        "source_id": source_id,
    }
    values.update(overrides)
    return ContextProvenance(**values)


def message(index: int) -> ContextMessage:
    return ContextMessage(
        role="user" if index % 2 else "assistant",
        content=f"message-{index}",
        occurred_at=f"2026-07-{index + 1:02d}T12:00:00-03:00",
        provenance=provenance("conversation", f"message-{index}"),
    )


def test_compilation_is_canonical_versioned_and_recent_window_is_bounded():
    compiler = ContextCompiler(recent_window_size=2)
    messages = [message(index) for index in range(4)]
    evidence = [
        ContextEvidence(
            content="lower-ranked evidence",
            title="B",
            rank=1,
            provenance=provenance("document", "doc-b"),
        ),
        ContextEvidence(
            content="higher-ranked evidence",
            title="A",
            rank=10,
            provenance=provenance("document", "doc-a"),
        ),
    ]

    first = compiler.compile(
        workspace_id=WORKSPACE_ID,
        matter_id=MATTER_ID,
        card={"priority": "high", "id": MATTER_ID},
        recent_messages=messages,
        summary="rolling summary",
        evidence=evidence,
    )
    second = compiler.compile(
        workspace_id=WORKSPACE_ID,
        matter_id=MATTER_ID,
        card={"id": MATTER_ID, "priority": "high"},
        recent_messages=list(reversed(messages)),
        summary="rolling summary",
        evidence=list(reversed(evidence)),
    )

    assert first.content == second.content
    assert first.digest == second.digest
    assert first.digest == hashlib.sha256(first.content.encode()).hexdigest()
    assert first.compiler_version == CONTEXT_COMPILER_VERSION
    assert first.schema_version == CONTEXT_SCHEMA_VERSION
    assert first.estimated_tokens <= first.token_budget
    assert first.recent_items_included == 2
    assert first.recent_items_omitted == 2
    assert first.truncated is True

    payload = first.payload
    assert payload["scope"] == {
        "matter_id": MATTER_ID,
        "workspace_id": WORKSPACE_ID,
    }
    assert [item["provenance"]["source_id"] for item in payload["recent_messages"]] == [
        "message-2",
        "message-3",
    ]
    assert [item["provenance"]["source_id"] for item in payload["evidence"]] == [
        "doc-a",
        "doc-b",
    ]
    assert payload["selection"]["recent_window_limit"] == 2
    assert first.as_message().content == first.content


@pytest.mark.parametrize(
    ("workspace_id", "matter_id"),
    [("", MATTER_ID), (WORKSPACE_ID, "")],
)
def test_workspace_and_matter_are_mandatory(workspace_id, matter_id):
    with pytest.raises(ContextCompilationError, match="required"):
        ContextCompiler().compile(
            workspace_id=workspace_id,
            matter_id=matter_id,
            card={},
        )


@pytest.mark.parametrize("section", ["card", "summary", "message", "evidence"])
def test_every_provenance_record_must_match_workspace_and_matter(section):
    wrong_scope = provenance("document", "foreign", workspace_id="workspace-b")
    kwargs = {
        "workspace_id": WORKSPACE_ID,
        "matter_id": MATTER_ID,
        "card": {},
    }
    if section == "card":
        kwargs["card_provenance"] = wrong_scope
    elif section == "summary":
        kwargs["summary"] = ContextSummary("summary", wrong_scope)
    elif section == "message":
        kwargs["recent_messages"] = [
            ContextMessage("user", "message", "2026-07-19T00:00:00Z", wrong_scope)
        ]
    else:
        kwargs["evidence"] = [ContextEvidence("evidence", wrong_scope)]

    with pytest.raises(ContextScopeError, match="outside"):
        ContextCompiler().compile(**kwargs)


def test_provenance_digest_is_verified_and_preserved_for_included_evidence():
    content = "source evidence"
    digest = hashlib.sha256(content.encode()).hexdigest()
    evidence = ContextEvidence(
        content=content,
        provenance=provenance("document", "doc-1", content_sha256=digest),
    )

    compiled = ContextCompiler().compile(
        workspace_id=WORKSPACE_ID,
        matter_id=MATTER_ID,
        card={},
        evidence=[evidence],
    )

    assert compiled.payload["evidence"][0]["provenance"]["content_sha256"] == digest

    invalid = ContextEvidence(
        content=content,
        provenance=provenance("document", "doc-1", content_sha256="0" * 64),
    )
    with pytest.raises(ContextCompilationError, match="digest mismatch"):
        ContextCompiler().compile(
            workspace_id=WORKSPACE_ID,
            matter_id=MATTER_ID,
            card={},
            evidence=[invalid],
        )


def test_budget_truncation_is_deterministic_and_never_exceeds_limit():
    compiler = ContextCompiler(recent_window_size=3)
    kwargs = {
        "workspace_id": WORKSPACE_ID,
        "matter_id": MATTER_ID,
        "card": {
            "id": MATTER_ID,
            "demand_type": "labor",
            "notes": "card " * 100,
        },
        "recent_messages": [message(index) for index in range(5)],
        "summary": "summary " * 80,
        "evidence": [
            ContextEvidence(
                content="evidence " * 80,
                rank=5,
                provenance=provenance("document", "doc-1"),
            )
        ],
        "token_budget": 700,
    }

    first = compiler.compile(**kwargs)
    second = compiler.compile(**kwargs)

    assert first.content == second.content
    assert first.estimated_tokens <= 700
    assert first.truncated is True
    assert first.payload["selection"]["content_truncations"] > 0


def test_budget_smaller_than_mandatory_envelope_fails_closed():
    with pytest.raises(ContextBudgetExceededError, match="mandatory scoped context"):
        ContextCompiler().compile(
            workspace_id=WORKSPACE_ID,
            matter_id=MATTER_ID,
            card={},
            token_budget=1,
        )


def test_non_string_card_keys_fail_with_a_scoped_compilation_error():
    with pytest.raises(ContextCompilationError, match="non-string object key"):
        ContextCompiler().compile(
            workspace_id=WORKSPACE_ID,
            matter_id=MATTER_ID,
            card={"valid": True, 1: "invalid"},
        )
