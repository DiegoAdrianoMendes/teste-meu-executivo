#!/usr/bin/env python3
"""Run the deterministic, offline SaaS V5 P5 conversation simulation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import socket
import sys
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.context_compiler import (  # noqa: E402
    CONTEXT_COMPILER_VERSION,
    ContextCompiler,
    ContextEvidence,
    ContextMessage,
    ContextProvenance,
    ContextSummary,
)
from app.ai.errors import ContextScopeError, ModelAPIDisabledError  # noqa: E402
from app.ai.models import (  # noqa: E402
    MediaKind,
    ModelAttribution,
    ModelMediaPart,
    ModelRequest,
    TaskClass,
)
from app.ai.policy import ModelAPIGate, RequestPolicy  # noqa: E402
from app.schemas.messaging_cadence import MessagingCadenceSettingsRead  # noqa: E402
from app.schemas.playbook import PlaybookContent  # noqa: E402
from app.services.messaging_cadence_service import MessagingCadenceService  # noqa: E402
from app.services.playbook_defaults import default_playbook_content  # noqa: E402

SCRIPT_VERSION = "klm-saas-v5-p5-conversation-simulator-v1"
SCHEMA_VERSION = "klm.saas-v5.p5.conversation-simulation.v1"
DEFAULT_SEED = "klm-saas-v5-p5-conversations-2026-07-20"
DEFAULT_OUTPUT = ROOT / ".local/qa-evidence/saas-v5-p5/conversation-simulation.json"
MINIMUM_CONVERSATIONS = 1_000
CONTEXT_TOKEN_BUDGET = 1_800
RECENT_WINDOW_SIZE = 4
WORKSPACE_COUNT = 4

SUBTHEMES = (
    "medicamento_alto_custo",
    "tratamento",
    "home_care",
    "cirurgia",
    "exame",
)
DOCUMENT_STATES = ("present", "missing")
AUTONOMY_MODES = (
    "human_only",
    "ai_triage_human_meeting",
    "ai_close_guarded",
    "ai_autonomous_supervised",
)
REQUIRED_OBJECTIONS = frozenset(
    {"price", "no_entry", "documents", "guarantee", "audio", "silence"}
)
TERMINAL_CONTACT_STATES = frozenset({"opt_out", "blocked"})


class SimulationValidationError(RuntimeError):
    """Raised when a mandatory P5 simulation gate is not satisfied."""


class OfflineNetworkAttemptError(SimulationValidationError):
    """Raised when simulated code attempts to open a network connection."""


@dataclass(frozen=True, slots=True)
class LifecycleProfile:
    key: str
    media_kind: str
    contact_state: str
    no_show: bool
    closing_requested: bool
    billing_requested: bool


LIFECYCLE_PROFILES = (
    LifecycleProfile("active_follow_up", "none", "active", False, False, False),
    LifecycleProfile("active_no_show_audio", "audio", "active", True, False, False),
    LifecycleProfile("active_close_image", "image", "active", False, True, True),
    LifecycleProfile("opt_out_after_objection", "none", "opt_out", False, False, False),
    LifecycleProfile("blocked_after_proposal", "image", "blocked", False, True, True),
)


@dataclass(frozen=True, slots=True)
class SimulationContracts:
    playbooks: Mapping[str, PlaybookContent]
    cadence: MessagingCadenceSettingsRead
    request_policy: RequestPolicy


class OfflineNetworkGuard:
    """Block Python socket egress while the simulation executes."""

    def __init__(self) -> None:
        self.attempts = 0
        self._patchers: list[Any] = []

    def _blocked(self, *_args: object, **_kwargs: object) -> None:
        self.attempts += 1
        raise OfflineNetworkAttemptError("Network access is forbidden in the P5 simulator.")

    def __enter__(self) -> OfflineNetworkGuard:
        self._patchers = [
            patch.object(socket.socket, "connect", side_effect=self._blocked),
            patch.object(socket.socket, "connect_ex", side_effect=self._blocked),
            patch.object(socket, "create_connection", side_effect=self._blocked),
        ]
        for patcher in self._patchers:
            patcher.start()
        return self

    def __exit__(self, *_args: object) -> None:
        for patcher in reversed(self._patchers):
            patcher.stop()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: Any) -> str:
    payload = value if isinstance(value, str) else _canonical_json(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_int(seed: str, *parts: object) -> int:
    material = "\x1f".join((seed, *(str(part) for part in parts)))
    return int(_digest(material), 16)


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _contract_value(values: Sequence[str], required_folded_value: str) -> str:
    expected = required_folded_value.strip().lower()
    for value in values:
        if _fold(value).strip().lower() == expected:
            return value
    raise SimulationValidationError(
        f"Required playbook contract value is missing: {required_folded_value}."
    )


def build_contracts() -> SimulationContracts:
    """Load and validate the real local playbook, policy, and cadence contracts."""
    playbooks = {mode: default_playbook_content(mode) for mode in AUTONOMY_MODES}
    for mode, playbook in playbooks.items():
        if playbook.policy.autonomy_mode != mode:
            raise SimulationValidationError(f"Autonomy contract drifted for {mode}.")
        if tuple(playbook.legal_triage.subthemes[: len(SUBTHEMES)]) != SUBTHEMES:
            raise SimulationValidationError("Mandatory health subthemes drifted from the playbook.")

    objection_ids = {item.id for item in playbooks[AUTONOMY_MODES[0]].objections}
    missing_objections = REQUIRED_OBJECTIONS - objection_ids
    if missing_objections:
        raise SimulationValidationError(
            f"Mandatory objections are absent: {sorted(missing_objections)}."
        )

    cadence = MessagingCadenceSettingsRead()
    request_policy = RequestPolicy(
        max_input_tokens=4_096,
        max_output_tokens=512,
        max_cost_per_call_usd=Decimal("0"),
        default_workspace_daily_budget_usd=Decimal("0"),
        workspace_daily_budgets_usd={},
        agent_call_caps_usd={},
    )
    return SimulationContracts(
        playbooks=playbooks,
        cadence=cadence,
        request_policy=request_policy,
    )


def _model_api_gate_is_closed() -> bool:
    try:
        ModelAPIGate(
            model_apis_enabled=False,
            online_approved=False,
            local_fake_enabled=False,
        ).require_open()
    except ModelAPIDisabledError:
        return True
    raise SimulationValidationError("The external model API gate unexpectedly opened.")


def _provenance(
    workspace_id: str,
    matter_id: str,
    source_type: str,
    source_id: str,
) -> ContextProvenance:
    return ContextProvenance(
        workspace_id=workspace_id,
        matter_id=matter_id,
        source_type=source_type,
        source_id=source_id,
        source_version=SCRIPT_VERSION,
    )


def _media_parts(media_kind: str, journey_id: str) -> tuple[ModelMediaPart, ...]:
    if media_kind == "none":
        return ()
    if media_kind == "audio":
        kind = MediaKind.AUDIO
        mime_type = "audio/ogg"
        suffix = "ogg"
    elif media_kind == "image":
        kind = MediaKind.IMAGE
        mime_type = "image/png"
        suffix = "png"
    else:
        raise SimulationValidationError(f"Unsupported synthetic media kind: {media_kind}.")
    payload = f"SYNTHETIC_ONLY:{journey_id}:{media_kind}".encode()
    return (
        ModelMediaPart(
            kind=kind,
            mime_type=mime_type,
            data=payload,
            filename=f"{journey_id}.{suffix}",
        ),
    )


def _context_sources(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources = [payload["card"]["provenance"]]
    summary = payload.get("summary")
    if summary is not None:
        sources.append(summary["provenance"])
    sources.extend(item["provenance"] for item in payload["recent_messages"])
    sources.extend(item["provenance"] for item in payload["evidence"])
    return sources


def _compile_context(
    *,
    compiler: ContextCompiler,
    journey_id: str,
    workspace_id: str,
    matter_id: str,
    subtheme: str,
    document_state: str,
    objection_id: str,
    autonomy_mode: str,
    profile: LifecycleProfile,
    playbook: PlaybookContent,
    sequence: int,
) -> tuple[Any, tuple[str, ...]]:
    base_time = datetime(2030, 1, 1, tzinfo=UTC) + timedelta(minutes=sequence * 10)
    message_facts = (
        f"SYNTHETIC_ONLY inbound subtheme={subtheme}",
        f"SYNTHETIC_ONLY triage documents={document_state}",
        f"SYNTHETIC_ONLY objection={objection_id}",
        f"SYNTHETIC_ONLY media={profile.media_kind}",
        f"SYNTHETIC_ONLY no_show={str(profile.no_show).lower()}",
        f"SYNTHETIC_ONLY contact_state={profile.contact_state}",
    )
    recent_messages = tuple(
        ContextMessage(
            role="user" if index % 2 == 0 else "assistant",
            content=content,
            occurred_at=base_time + timedelta(seconds=index),
            provenance=_provenance(
                workspace_id,
                matter_id,
                "synthetic_conversation_message",
                f"{journey_id}-message-{index + 1}",
            ),
        )
        for index, content in enumerate(message_facts)
    )
    summary = ContextSummary(
        content=(
            "SYNTHETIC_ONLY bounded summary "
            f"subtheme={subtheme} documents={document_state} autonomy={autonomy_mode}"
        ),
        provenance=_provenance(
            workspace_id,
            matter_id,
            "synthetic_rolling_summary",
            f"{journey_id}-summary",
        ),
    )
    evidence = (
        ContextEvidence(
            title="Synthetic playbook contract",
            content=(
                "SYNTHETIC_ONLY playbook policy "
                f"minimum_fee_brl={playbook.commercial.minimum_initial_fee_brl:.2f} "
                f"success_only={str(playbook.commercial.success_only_allowed).lower()}"
            ),
            rank=100,
            provenance=_provenance(
                workspace_id,
                matter_id,
                "playbook_contract",
                f"saas-v5-default-{autonomy_mode}",
            ),
        ),
        ContextEvidence(
            title="Synthetic document state",
            content=f"SYNTHETIC_ONLY document_state={document_state} subtheme={subtheme}",
            rank=80,
            provenance=_provenance(
                workspace_id,
                matter_id,
                "synthetic_document_state",
                f"{journey_id}-documents",
            ),
        ),
    )
    card = {
        "id": matter_id,
        "practice_area": "synthetic_health_law",
        "demand_type": subtheme,
        "micro_status": profile.key,
        "priority": "simulation",
        "urgency": "synthetic_only",
        "documents_state": document_state,
        "objection": objection_id,
        "autonomy_mode": autonomy_mode,
        "simulation_only": True,
    }
    compiled = compiler.compile(
        workspace_id=workspace_id,
        matter_id=matter_id,
        card=card,
        recent_messages=recent_messages,
        summary=summary,
        evidence=evidence,
        token_budget=CONTEXT_TOKEN_BUDGET,
        card_provenance=_provenance(
            workspace_id,
            matter_id,
            "synthetic_matter_card",
            matter_id,
        ),
    )
    payload = compiled.payload
    sources = _context_sources(payload)
    if not sources:
        raise SimulationValidationError(f"Compiled context has no provenance for {journey_id}.")
    if any(
        source["workspace_id"] != workspace_id or source["matter_id"] != matter_id
        for source in sources
    ):
        raise SimulationValidationError(f"Compiled context escaped its scope for {journey_id}.")
    if any(len(str(source.get("content_sha256", ""))) != 64 for source in sources):
        raise SimulationValidationError(f"Compiled context lacks source digests for {journey_id}.")
    source_refs = tuple(
        f"{source['source_type']}:{source['source_id']}" for source in sources
    )
    return compiled, source_refs


def _cadence_category(
    profile: LifecycleProfile,
    document_state: str,
) -> str:
    if profile.no_show:
        return "no_show"
    if document_state == "missing":
        return "documentos"
    if profile.billing_requested:
        return "pagamento"
    if profile.closing_requested:
        return "proposta"
    return "novo_lead"


def _cadence_plan(
    *,
    seed: str,
    journey_id: str,
    profile: LifecycleProfile,
    document_state: str,
    playbook: PlaybookContent,
    settings: MessagingCadenceSettingsRead,
) -> dict[str, Any]:
    category = _cadence_category(profile, document_state)
    if profile.contact_state in TERMINAL_CONTACT_STATES:
        return {
            "category": category,
            "status": f"suppressed_{profile.contact_state}",
            "plannedMessages": 0,
            "preDelayMinObservedMs": None,
            "preDelayMaxObservedMs": None,
            "typingMinObservedMs": None,
            "typingMaxObservedMs": None,
            "boundsValidated": True,
        }

    steps = playbook.follow_up.cadences.get(category)
    if not steps:
        raise SimulationValidationError(f"Missing playbook cadence category: {category}.")
    maximum_steps = min(
        playbook.follow_up.max_messages_per_day,
        settings.max_consecutive_without_reply,
    )
    selected_steps = steps[:maximum_steps]
    plans: list[tuple[int, int]] = []
    jitter_ratio = settings.jitter_percent / 100
    typing_lower_bound = math.floor(settings.typing_min_ms * (1 - jitter_ratio))
    typing_upper_bound = math.ceil(settings.typing_max_ms * (1 + jitter_ratio))
    for step in selected_steps:
        text = f"SYNTHETIC_ONLY follow_up={step.id} intent={step.message_intent}"
        key = f"{seed}:{journey_id}:{step.id}"
        pre_delay_ms = MessagingCadenceService._deterministic_between(
            settings.pre_delay_min_ms,
            settings.pre_delay_max_ms,
            f"{key}:pre",
        )
        raw_typing_ms = int((len(text.strip()) / settings.characters_per_second) * 1000)
        typing_base = max(
            settings.typing_min_ms,
            min(settings.typing_max_ms, raw_typing_ms),
        )
        typing_duration_ms = MessagingCadenceService._with_jitter(
            typing_base,
            settings.jitter_percent,
            f"{key}:typing",
        )
        if not settings.pre_delay_min_ms <= pre_delay_ms <= settings.pre_delay_max_ms:
            raise SimulationValidationError(f"Pre-delay escaped its bounds for {journey_id}.")
        if not typing_lower_bound <= typing_duration_ms <= typing_upper_bound:
            raise SimulationValidationError(f"Typing duration escaped its bounds for {journey_id}.")
        if not 0 <= step.typing_delay_seconds <= 300:
            raise SimulationValidationError(f"Playbook typing delay is invalid for {journey_id}.")
        plans.append((pre_delay_ms, typing_duration_ms))

    return {
        "category": category,
        "status": "scheduled_local_draft",
        "plannedMessages": len(plans),
        "preDelayMinObservedMs": min(item[0] for item in plans),
        "preDelayMaxObservedMs": max(item[0] for item in plans),
        "typingMinObservedMs": min(item[1] for item in plans),
        "typingMaxObservedMs": max(item[1] for item in plans),
        "boundsValidated": True,
    }


def _commercial_plan(
    *,
    playbook: PlaybookContent,
    objection_id: str,
    objection_position: int,
    profile: LifecycleProfile,
) -> dict[str, Any]:
    minimum = playbook.commercial.minimum_initial_fee_brl
    success_option = next(
        (
            option
            for option in playbook.commercial.payment_options
            if "exito" in _fold(option).lower()
        ),
        None,
    )
    if playbook.commercial.success_only_allowed and success_option is None:
        raise SimulationValidationError("Success-only is allowed but no success option exists.")

    if not profile.billing_requested:
        return {
            "billingRequested": False,
            "billingMode": "none",
            "initialFeeBrl": None,
            "successOption": None,
            "status": "not_requested",
        }

    success_only = objection_id == "no_entry"
    initial_fee = 0.0 if success_only else minimum + ((objection_position % 4) * 1_000)
    if not success_only and initial_fee < minimum:
        raise SimulationValidationError("An upfront fee fell below the playbook minimum.")
    if success_only and not playbook.commercial.success_only_allowed:
        raise SimulationValidationError("A forbidden success-only option was generated.")
    status = (
        f"suppressed_{profile.contact_state}"
        if profile.contact_state in TERMINAL_CONTACT_STATES
        else "draft_pending_human_approval"
    )
    return {
        "billingRequested": True,
        "billingMode": "success_only" if success_only else "upfront",
        "initialFeeBrl": initial_fee,
        "successOption": success_option if success_only else None,
        "status": status,
    }


def _risk_and_handoff(
    *,
    playbook: PlaybookContent,
    document_state: str,
    objection_id: str,
    objection_attempts: int,
    objection_max_attempts: int,
    autonomy_mode: str,
    profile: LifecycleProfile,
) -> dict[str, Any]:
    hard_gates = playbook.policy.hard_gates
    risk_reasons: list[str] = []
    if document_state == "missing":
        risk_reasons.append(_contract_value(hard_gates, "incerteza documental"))
    if objection_id == "guarantee":
        risk_reasons.append(_contract_value(hard_gates, "orientacao juridica conclusiva"))
    if profile.closing_requested:
        risk_reasons.append(_contract_value(hard_gates, "assinatura"))
    if profile.billing_requested:
        risk_reasons.append(_contract_value(hard_gates, "pagamento"))

    policy_escalation = objection_attempts >= objection_max_attempts
    human_required = bool(risk_reasons) or autonomy_mode == "human_only" or policy_escalation
    handoff_required = human_required or profile.no_show
    destination: str | None = None
    if handoff_required:
        if autonomy_mode == "human_only":
            destination = "fila_humana"
        elif document_state == "missing" or objection_id == "guarantee":
            destination = "juridico"
        elif profile.no_show:
            destination = "secretaria"
        else:
            destination = "closer"
        allowed_destinations = {item.key for item in playbook.handoff.destinations}
        if destination not in allowed_destinations:
            raise SimulationValidationError(f"Invalid handoff destination: {destination}.")

    return {
        "highRisk": bool(risk_reasons),
        "riskReasons": list(dict.fromkeys(risk_reasons)),
        "policyEscalation": policy_escalation,
        "humanRequired": human_required,
        "handoffRequired": handoff_required,
        "handoffDispatcher": playbook.handoff.normal_dispatcher if handoff_required else None,
        "handoffDestination": destination,
    }


def _closing_status(profile: LifecycleProfile) -> str:
    if not profile.closing_requested:
        return "not_requested"
    if profile.contact_state in TERMINAL_CONTACT_STATES:
        return f"suppressed_{profile.contact_state}"
    return "draft_pending_human_approval"


def _build_journey(
    *,
    seed: str,
    sequence: int,
    subtheme: str,
    document_state: str,
    autonomy_mode: str,
    objection_id: str,
    objection_position: int,
    profile: LifecycleProfile,
    contracts: SimulationContracts,
    compiler: ContextCompiler,
) -> dict[str, Any]:
    journey_id = f"sim-journey-{sequence:04d}"
    combination = (
        subtheme,
        document_state,
        autonomy_mode,
        objection_id,
        profile.key,
    )
    workspace_number = 1 + (_stable_int(seed, "workspace", *combination) % WORKSPACE_COUNT)
    workspace_id = f"sim-workspace-{workspace_number:02d}"
    matter_id = f"sim-matter-{_digest([seed, *combination])[:20]}"
    playbook = contracts.playbooks[autonomy_mode]
    objection = next(item for item in playbook.objections if item.id == objection_id)
    objection_attempts = _stable_int(seed, "attempts", *combination) % (
        objection.max_attempts + 1
    )

    compiled, source_refs = _compile_context(
        compiler=compiler,
        journey_id=journey_id,
        workspace_id=workspace_id,
        matter_id=matter_id,
        subtheme=subtheme,
        document_state=document_state,
        objection_id=objection_id,
        autonomy_mode=autonomy_mode,
        profile=profile,
        playbook=playbook,
        sequence=sequence,
    )
    media_parts = _media_parts(profile.media_kind, journey_id)
    request = ModelRequest(
        messages=[compiled.as_message()],
        workspace_id=workspace_id,
        agent_name="sdr",
        task_class=TaskClass.MEDIUM,
        max_output_tokens=256,
        idempotent=True,
        idempotency_key=f"{seed}:{journey_id}:{compiled.digest}",
        contains_sensitive_data=True,
        media_parts=media_parts,
        attribution=ModelAttribution(
            operation="p5_conversation_simulation",
            matter_id=matter_id,
            context_digest=compiled.digest,
            playbook_version=f"saas-v5-default-{autonomy_mode}",
            source_refs=source_refs,
        ),
        request_id=journey_id,
    )
    estimated_request_tokens = compiled.estimated_tokens + sum(
        part.estimated_input_tokens for part in media_parts
    )
    contracts.request_policy.validate_request(request, estimated_request_tokens)

    cadence = _cadence_plan(
        seed=seed,
        journey_id=journey_id,
        profile=profile,
        document_state=document_state,
        playbook=playbook,
        settings=contracts.cadence,
    )
    commercial = _commercial_plan(
        playbook=playbook,
        objection_id=objection_id,
        objection_position=objection_position,
        profile=profile,
    )
    gates = _risk_and_handoff(
        playbook=playbook,
        document_state=document_state,
        objection_id=objection_id,
        objection_attempts=objection_attempts,
        objection_max_attempts=objection.max_attempts,
        autonomy_mode=autonomy_mode,
        profile=profile,
    )

    return {
        "id": journey_id,
        "workspaceId": workspace_id,
        "matterId": matter_id,
        "subtheme": subtheme,
        "documentState": document_state,
        "objection": objection_id,
        "objectionAttempts": objection_attempts,
        "mediaKind": profile.media_kind,
        "media": {
            "partCount": len(media_parts),
            "contentDigests": [part.content_sha256 for part in media_parts],
            "provenanceValidated": all(
                len(part.content_sha256) == 64 for part in media_parts
            ),
        },
        "noShow": profile.no_show,
        "followUpRequested": True,
        "contactState": profile.contact_state,
        "lifecycleProfile": profile.key,
        "autonomyMode": autonomy_mode,
        "stageAutonomyMode": playbook.policy.stage_modes["fechamento"],
        "closingRequested": profile.closing_requested,
        "closingStatus": _closing_status(profile),
        "collectionRequested": profile.billing_requested,
        "collectionStatus": commercial["status"],
        "cadence": cadence,
        "commercial": commercial,
        "gates": gates,
        "runtime": {
            "contextCompilerExecuted": True,
            "contextCompilerVersion": compiled.compiler_version,
            "contextDigest": compiled.digest,
            "estimatedContextTokens": compiled.estimated_tokens,
            "estimatedRequestTokens": estimated_request_tokens,
            "tokenBudget": compiled.token_budget,
            "recentItemsIncluded": compiled.recent_items_included,
            "recentItemsOmitted": compiled.recent_items_omitted,
            "sourceCount": len(source_refs),
            "sourceRefsDigest": _digest(source_refs),
            "scopeValidated": True,
            "provenanceValidated": True,
            "requestPolicyValidated": True,
        },
        "effects": {
            "externalActionsExecuted": 0,
            "networkCallsExecuted": 0,
            "messagesSent": 0,
            "chargesExecuted": 0,
        },
        "syntheticOnly": True,
    }


def run_scope_isolation_probes(compiler: ContextCompiler) -> dict[str, Any]:
    """Prove that both workspace and matter provenance mismatches fail closed."""
    workspace_id = "sim-workspace-scope-a"
    matter_id = "sim-matter-scope-a"
    timestamp = datetime(2030, 1, 1, tzinfo=UTC)

    def compile_with(provenance: ContextProvenance) -> None:
        compiler.compile(
            workspace_id=workspace_id,
            matter_id=matter_id,
            card={"id": matter_id, "simulation_only": True},
            recent_messages=(
                ContextMessage(
                    role="user",
                    content="SYNTHETIC_ONLY isolation probe",
                    occurred_at=timestamp,
                    provenance=provenance,
                ),
            ),
            token_budget=CONTEXT_TOKEN_BUDGET,
        )

    workspace_blocked = False
    try:
        compile_with(
            _provenance(
                "sim-workspace-scope-b",
                matter_id,
                "synthetic_isolation_probe",
                "workspace-cross-read",
            )
        )
    except ContextScopeError:
        workspace_blocked = True

    matter_blocked = False
    try:
        compile_with(
            _provenance(
                workspace_id,
                "sim-matter-scope-b",
                "synthetic_isolation_probe",
                "matter-cross-read",
            )
        )
    except ContextScopeError:
        matter_blocked = True

    if not workspace_blocked or not matter_blocked:
        raise SimulationValidationError("Context scope isolation did not fail closed.")
    return {
        "workspaceCrossReadBlocked": workspace_blocked,
        "matterCrossReadBlocked": matter_blocked,
        "probeCount": 2,
        "passed": True,
    }


def _dimension_counts(journeys: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(item[field]) for item in journeys).items()))


def _slice_counts(
    journeys: Sequence[Mapping[str, Any]],
    first: str,
    second: str,
) -> dict[str, int]:
    counts = Counter(f"{item[first]}|{item[second]}" for item in journeys)
    return dict(sorted(counts.items()))


def validate_journeys(
    journeys: Sequence[Mapping[str, Any]],
    contracts: SimulationContracts,
) -> dict[str, Any]:
    """Validate exhaustive combinations and every safety invariant."""
    objection_ids = tuple(item.id for item in contracts.playbooks[AUTONOMY_MODES[0]].objections)
    lifecycle_keys = tuple(profile.key for profile in LIFECYCLE_PROFILES)
    expected = set(
        product(
            SUBTHEMES,
            DOCUMENT_STATES,
            AUTONOMY_MODES,
            objection_ids,
            lifecycle_keys,
        )
    )
    observed_counter = Counter(
        (
            str(item["subtheme"]),
            str(item["documentState"]),
            str(item["autonomyMode"]),
            str(item["objection"]),
            str(item["lifecycleProfile"]),
        )
        for item in journeys
    )
    observed = set(observed_counter)
    missing = expected - observed
    unexpected = observed - expected
    duplicate_count = sum(count - 1 for count in observed_counter.values() if count > 1)

    high_risk = [item for item in journeys if item["gates"]["highRisk"]]
    terminal = [item for item in journeys if item["contactState"] in TERMINAL_CONTACT_STATES]
    active = [item for item in journeys if item["contactState"] == "active"]
    upfront = [item for item in journeys if item["commercial"]["billingMode"] == "upfront"]
    success_only = [
        item for item in journeys if item["commercial"]["billingMode"] == "success_only"
    ]
    handoffs = [item for item in journeys if item["gates"]["handoffRequired"]]
    media_values = {str(item["mediaKind"]) for item in journeys}
    no_show_values = {bool(item["noShow"]) for item in journeys}

    checks = {
        "minimumConversations": len(journeys) >= MINIMUM_CONVERSATIONS,
        "mandatoryCartesianMatrix": (
            not missing
            and not unexpected
            and duplicate_count == 0
            and len(journeys) == len(expected)
        ),
        "allSubthemes": {str(item["subtheme"]) for item in journeys} == set(SUBTHEMES),
        "documentsPresentAndMissing": {
            str(item["documentState"]) for item in journeys
        }
        == set(DOCUMENT_STATES),
        "allPlaybookObjections": {str(item["objection"]) for item in journeys}
        == set(objection_ids),
        "audioAndImage": {"audio", "image"}.issubset(media_values),
        "noShowAndShow": no_show_values == {False, True},
        "allAutonomyModes": {str(item["autonomyMode"]) for item in journeys}
        == set(AUTONOMY_MODES),
        "workspaceMatterIsolation": (
            len({str(item["workspaceId"]) for item in journeys}) == WORKSPACE_COUNT
            and len({str(item["matterId"]) for item in journeys}) == len(journeys)
            and all(item["runtime"]["scopeValidated"] for item in journeys)
        ),
        "contextCompilerEveryJourney": all(
            item["runtime"]["contextCompilerExecuted"] for item in journeys
        ),
        "contextBoundedEveryJourney": all(
            item["runtime"]["estimatedContextTokens"] <= item["runtime"]["tokenBudget"]
            and item["runtime"]["recentItemsOmitted"] >= 2
            for item in journeys
        ),
        "scopeAndProvenanceEveryJourney": all(
            item["runtime"]["scopeValidated"]
            and item["runtime"]["provenanceValidated"]
            and item["runtime"]["sourceCount"] >= 3
            for item in journeys
        ),
        "requestPolicyEveryJourney": all(
            item["runtime"]["requestPolicyValidated"] for item in journeys
        ),
        "mediaProvenanceEveryJourney": all(
            item["media"]["provenanceValidated"]
            and item["media"]["partCount"] == len(item["media"]["contentDigests"])
            and (
                (item["mediaKind"] == "none" and item["media"]["partCount"] == 0)
                or (item["mediaKind"] != "none" and item["media"]["partCount"] == 1)
            )
            for item in journeys
        ),
        "zeroExternalEffects": all(
            all(value == 0 for value in item["effects"].values()) for item in journeys
        ),
        "highRiskHumanGates": bool(high_risk)
        and all(
            item["gates"]["humanRequired"]
            and item["gates"]["handoffRequired"]
            and item["gates"]["handoffDispatcher"]
            == contracts.playbooks[str(item["autonomyMode"])].handoff.normal_dispatcher
            for item in high_risk
        ),
        "terminalContactStopsCadence": bool(terminal)
        and all(
            item["cadence"]["plannedMessages"] == 0
            and str(item["cadence"]["status"]).startswith("suppressed_")
            for item in terminal
        ),
        "activeFollowUpUsesBoundedCadence": bool(active)
        and all(
            item["cadence"]["plannedMessages"] > 0
            and item["cadence"]["boundsValidated"]
            for item in active
        ),
        "handoffsUsePlaybookDispatcher": bool(handoffs)
        and all(
            item["gates"]["handoffDispatcher"]
            == contracts.playbooks[str(item["autonomyMode"])].handoff.normal_dispatcher
            and item["gates"]["handoffDestination"]
            for item in handoffs
        ),
        "minimumUpfrontFee": bool(upfront)
        and all(
            item["commercial"]["initialFeeBrl"]
            >= contracts.playbooks[str(item["autonomyMode"])].commercial.minimum_initial_fee_brl
            for item in upfront
        ),
        "successOnlyOptions": bool(success_only)
        and all(
            contracts.playbooks[
                str(item["autonomyMode"])
            ].commercial.success_only_allowed
            and bool(item["commercial"]["successOption"])
            and item["gates"]["humanRequired"]
            for item in success_only
        ),
        "closingAndBillingAreDraftOrSuppressed": all(
            not item["closingRequested"]
            or str(item["closingStatus"]).startswith(("draft_", "suppressed_"))
            for item in journeys
        )
        and all(
            not item["commercial"]["billingRequested"]
            or str(item["commercial"]["status"]).startswith(("draft_", "suppressed_"))
            for item in journeys
        ),
        "closingAndCollectionCovered": (
            {bool(item["closingRequested"]) for item in journeys} == {False, True}
            and {bool(item["collectionRequested"]) for item in journeys} == {False, True}
            and all(
                item["collectionStatus"] == item["commercial"]["status"]
                for item in journeys
            )
        ),
        "syntheticOnly": all(item["syntheticOnly"] for item in journeys),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        details = []
        if missing:
            details.append(f"missing={len(missing)}")
        if unexpected:
            details.append(f"unexpected={len(unexpected)}")
        if duplicate_count:
            details.append(f"duplicates={duplicate_count}")
        suffix = f" ({', '.join(details)})" if details else ""
        raise SimulationValidationError(
            f"Mandatory P5 coverage failed: {', '.join(failed)}{suffix}."
        )
    return {
        "passed": True,
        "ratio": 1.0,
        "checks": checks,
        "highRiskJourneys": len(high_risk),
        "terminalContactJourneys": len(terminal),
        "handoffJourneys": len(handoffs),
        "upfrontJourneys": len(upfront),
        "successOnlyJourneys": len(success_only),
        "missingMandatoryCombinations": 0,
        "unexpectedCombinations": 0,
        "duplicateCombinations": 0,
    }


def _contract_snapshot(contracts: SimulationContracts) -> dict[str, Any]:
    first = contracts.playbooks[AUTONOMY_MODES[0]]
    return {
        "contextCompilerVersion": CONTEXT_COMPILER_VERSION,
        "contextTokenBudget": CONTEXT_TOKEN_BUDGET,
        "recentWindowSize": RECENT_WINDOW_SIZE,
        "playbook": {
            "autonomyModes": list(AUTONOMY_MODES),
            "objectionIds": [item.id for item in first.objections],
            "minimumInitialFeeBrl": first.commercial.minimum_initial_fee_brl,
            "successOnlyAllowed": first.commercial.success_only_allowed,
            "paymentOptions": first.commercial.payment_options,
            "hardGates": first.policy.hard_gates,
            "handoffDispatcher": first.handoff.normal_dispatcher,
        },
        "messagingCadence": contracts.cadence.model_dump(mode="json", by_alias=True),
        "requestPolicy": {
            "maxInputTokens": contracts.request_policy.max_input_tokens,
            "maxOutputTokens": contracts.request_policy.max_output_tokens,
            "maxMediaParts": contracts.request_policy.max_media_parts,
            "maxSingleMediaBytes": contracts.request_policy.max_single_media_bytes,
            "maxMediaBytes": contracts.request_policy.max_media_bytes,
        },
    }


def simulate(seed: str = DEFAULT_SEED) -> dict[str, Any]:
    """Generate, execute, and validate the complete deterministic P5 matrix."""
    normalized_seed = str(seed).strip()
    if not normalized_seed:
        raise SimulationValidationError("A non-empty deterministic seed is required.")

    contracts = build_contracts()
    compiler = ContextCompiler(recent_window_size=RECENT_WINDOW_SIZE)
    model_api_gate_closed = _model_api_gate_is_closed()
    objection_ids = tuple(
        item.id for item in contracts.playbooks[AUTONOMY_MODES[0]].objections
    )
    objection_positions = {value: index for index, value in enumerate(objection_ids)}
    combinations = list(
        product(
            SUBTHEMES,
            DOCUMENT_STATES,
            AUTONOMY_MODES,
            objection_ids,
            LIFECYCLE_PROFILES,
        )
    )
    combinations.sort(
        key=lambda item: _digest(
            [
                normalized_seed,
                item[0],
                item[1],
                item[2],
                item[3],
                item[4].key,
            ]
        )
    )

    with OfflineNetworkGuard() as network_guard:
        journeys = [
            _build_journey(
                seed=normalized_seed,
                sequence=index,
                subtheme=subtheme,
                document_state=document_state,
                autonomy_mode=autonomy_mode,
                objection_id=objection_id,
                objection_position=objection_positions[objection_id],
                profile=profile,
                contracts=contracts,
                compiler=compiler,
            )
            for index, (
                subtheme,
                document_state,
                autonomy_mode,
                objection_id,
                profile,
            ) in enumerate(combinations, start=1)
        ]
        isolation = run_scope_isolation_probes(compiler)
    if network_guard.attempts:
        raise SimulationValidationError(
            f"The offline network guard blocked {network_guard.attempts} attempt(s)."
        )

    coverage = validate_journeys(journeys, contracts)
    contract_snapshot = _contract_snapshot(contracts)
    matrix = {
        "dimensions": {
            "subthemes": list(SUBTHEMES),
            "documentStates": list(DOCUMENT_STATES),
            "autonomyModes": list(AUTONOMY_MODES),
            "objections": list(objection_ids),
            "lifecycleProfiles": [profile.key for profile in LIFECYCLE_PROFILES],
        },
        "expectedCombinations": len(combinations),
        "observedCombinations": len(journeys),
        "counts": {
            "subtheme": _dimension_counts(journeys, "subtheme"),
            "documentState": _dimension_counts(journeys, "documentState"),
            "autonomyMode": _dimension_counts(journeys, "autonomyMode"),
            "objection": _dimension_counts(journeys, "objection"),
            "mediaKind": _dimension_counts(journeys, "mediaKind"),
            "contactState": _dimension_counts(journeys, "contactState"),
            "collectionRequested": _dimension_counts(journeys, "collectionRequested"),
            "lifecycleProfile": _dimension_counts(journeys, "lifecycleProfile"),
        },
        "slices": {
            "subthemeByMedia": _slice_counts(journeys, "subtheme", "mediaKind"),
            "autonomyByContactState": _slice_counts(
                journeys, "autonomyMode", "contactState"
            ),
            "autonomyByClosing": _slice_counts(
                journeys, "autonomyMode", "closingRequested"
            ),
            "documentsByHandoff": {
                key: value
                for key, value in sorted(
                    Counter(
                        f"{item['documentState']}|{item['gates']['handoffRequired']}"
                        for item in journeys
                    ).items()
                )
            },
        },
    }
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "scriptVersion": SCRIPT_VERSION,
        "kind": "klm-saas-v5-p5-conversation-simulation",
        "status": "passed",
        "seed": normalized_seed,
        "conversationCount": len(journeys),
        "minimumConversationCount": MINIMUM_CONVERSATIONS,
        "matrix": matrix,
        "coverage": coverage,
        "contracts": contract_snapshot,
        "contractDigest": _digest(contract_snapshot),
        "isolation": isolation,
        "externalEffects": {
            "offlineNetworkGuard": True,
            "networkAttempts": network_guard.attempts,
            "networkCallsExecuted": 0,
            "providerCallsExecuted": 0,
            "messagesSent": 0,
            "chargesExecuted": 0,
            "modelApiGate": "blocked" if model_api_gate_closed else "open",
            "localEvidenceWrites": 1,
        },
        "data": {
            "classification": "synthetic_only",
            "realDataRecordsRead": 0,
            "realDataRecordsWritten": 0,
            "syntheticMarkersValidated": len(journeys),
        },
        "journeysDigest": _digest(journeys),
        "journeys": journeys,
    }
    payload["digest"] = _digest(payload)
    return payload


def write_evidence(path: Path, payload: Mapping[str, Any]) -> None:
    """Write deterministic evidence atomically to the local QA directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = simulate(args.seed)
        write_evidence(args.output, payload)
    except (OSError, SimulationValidationError, ValueError) as exc:
        print(f"P5_CONVERSATION_SIMULATION_FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        "P5_CONVERSATION_SIMULATION_OK: "
        f"{payload['conversationCount']} synthetic conversations; "
        f"digest={payload['digest']}; output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
