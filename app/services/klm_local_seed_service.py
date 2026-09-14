"""Seed idempotente usado apenas pela sessão local do KLM Hub."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from unicodedata import normalize

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.platform_rbac import PLATFORM_ROLE_PACKAGES, is_platform_root
from app.db.platform_control import (
    clear_platform_control_context,
    set_platform_control_context,
)
from app.db.tenant import set_workspace_context
from app.models.approval import ApprovalPriority, ApprovalQueueItem, ApprovalStatus
from app.models.client import Client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.finance import (
    FinanceEntry,
    FinanceEntryStatus,
    FinanceEntryType,
    FinanceReminder,
    FinanceReminderStatus,
)
from app.models.flow import Flow
from app.models.honorarium_estimate import HonorariumEstimate
from app.models.klm_operations import (
    FollowUpStatus,
    FollowUpTask,
    MessageAuthorType,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
    TechnicalRequest,
    TechnicalRequestSeverity,
    ThreadStatus,
)
from app.models.matter import (
    ClientClassification,
    HealthSubtheme,
    KanbanColumnId,
    Matter,
    MicroStatus,
    PracticeArea,
    Priority,
    Urgency,
)
from app.models.medication import MedicationReference
from app.models.meeting import Meeting, MeetingStatus
from app.models.oss_bridge import ExternalEntitySyncState, OSSBridgeProvider
from app.models.revenue_projection import RevenueProjection
from app.models.storage_object import StorageObject, StorageObjectState
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.klm_operations import (
    AcquisitionEventCreate,
    FollowUpCreate,
    OmnichannelMessageCreate,
    OmnichannelThreadCreate,
    OmnichannelThreadUpdate,
    TechnicalRequestCreate,
)
from app.schemas.medication import TreatmentEstimateUpsert
from app.schemas.oss_bridge import ExternalEntityLinkUpsert
from app.services.klm_operations_service import KlmOperationsService, _now
from app.services.knowledge_service import KnowledgeService
from app.services.local_object_storage_service import LocalObjectStorageService
from app.services.medication_pricing_service import (
    DEFAULT_LEGAL_PRICING_POLICY,
    MedicationPricingService,
)
from app.services.oss_bridge_service import OSSBridgeService
from app.services.playbook_service import PlaybookService
from app.services.s3_service import S3Service, StorageUnavailableError

LOCAL_SEED_VERSION = "2026.07.17.4"
LOCAL_PLAYBOOK_SEED_VERSION = "2026.07.22.1-agent-governance-v1.4"
LOCAL_COMMERCIAL_SEED_VERSION = "2026.07.31.2"
LOCAL_WORKSPACE_SLUG = "klm-hub-local"
LOCAL_USER_EMAIL = "local@klmadvogados.com.br"
LOCAL_PLATFORM_USER_EMAIL = "blackbox@meuexecutivo.local"
LOCAL_PLATFORM_STAFF_IDENTITIES: dict[str, dict[str, str]] = {
    "platform_admin": {
        "name": "Administração Meu Executivo",
        "email": "administracao@meuexecutivo.local",
        "initials": "AD",
        "color": "#1d4ed8",
    },
    "sales_sdr": {
        "name": "Vendas Meu Executivo",
        "email": "vendas@meuexecutivo.local",
        "initials": "VE",
        "color": "#0284c7",
    },
    "commercial_manager": {
        "name": "Gestão Comercial Meu Executivo",
        "email": "comercial@meuexecutivo.local",
        "initials": "GC",
        "color": "#0369a1",
    },
    "onboarding": {
        "name": "Onboarding Meu Executivo",
        "email": "onboarding@meuexecutivo.local",
        "initials": "ON",
        "color": "#0891b2",
    },
    "customer_success": {
        "name": "Customer Success Meu Executivo",
        "email": "cs@meuexecutivo.local",
        "initials": "CS",
        "color": "#0f766e",
    },
    "support": {
        "name": "Suporte Meu Executivo",
        "email": "suporte@meuexecutivo.local",
        "initials": "SU",
        "color": "#0d9488",
    },
    "technical_support": {
        "name": "Suporte Técnico Meu Executivo",
        "email": "suporte.ti@meuexecutivo.local",
        "initials": "ST",
        "color": "#0ea5e9",
    },
    "finance_billing": {
        "name": "Financeiro Meu Executivo",
        "email": "financeiro@meuexecutivo.local",
        "initials": "FI",
        "color": "#15803d",
    },
    "marketing_content": {
        "name": "Marketing Meu Executivo",
        "email": "marketing@meuexecutivo.local",
        "initials": "MK",
        "color": "#7c3aed",
    },
    "community_manager": {
        "name": "Comunidade Meu Executivo",
        "email": "comunidade@meuexecutivo.local",
        "initials": "CM",
        "color": "#9333ea",
    },
    "compliance_security": {
        "name": "Compliance Meu Executivo",
        "email": "compliance@meuexecutivo.local",
        "initials": "CO",
        "color": "#475569",
    },
    "product_operations": {
        "name": "Produto e Operações Meu Executivo",
        "email": "produto@meuexecutivo.local",
        "initials": "PO",
        "color": "#4f46e5",
    },
    "engineering": {
        "name": "TI Meu Executivo",
        "email": "ti@meuexecutivo.local",
        "initials": "TI",
        "color": "#2563eb",
    },
}


def _ascii_pdf_text(value: str) -> str:
    plain = normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return plain.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _local_fixture_pdf(title: str, object_name: str) -> bytes:
    lines = [
        "Meu Executivo - documento demonstrativo local",
        f"Tipo: {_ascii_pdf_text(title)}",
        f"Objeto do caso: {_ascii_pdf_text(object_name)}",
        "Conteudo ficticio para validacao de documentos e RAG.",
        "Nenhum dado deste PDF deve ser usado em uma atuacao real.",
    ]
    commands = ["BT", "/F1 15 Tf", "72 760 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -26 Td")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        ),
    ]
    payload = bytearray(b"%PDF-1.4\n%KLM\n")
    offsets = [0]
    for index, item in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(item)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


CASE_FIXTURES: list[dict[str, Any]] = [
    {
        "name": "Pedro Henrique",
        "email": "pedro.henrique@local.klm",
        "phone": "+5511980001001",
        "subtheme": HealthSubtheme.medicamento_alto_custo,
        "object": "Dupilumabe",
        "source": "Google Ads",
        "channel": "google_ads",
        "column": KanbanColumnId.novo_lead,
        "micro": MicroStatus.lead_novo,
        "score": 38,
        "doc_score": 0,
        "initial_fee": 4200.0,
        "claim_value": 96000.0,
        "urgency_hours": 72,
    },
    {
        "name": "Bianca Almeida",
        "email": "bianca.almeida@local.klm",
        "phone": "+5511980001002",
        "subtheme": HealthSubtheme.exame,
        "object": "Sequenciamento genético",
        "source": "Orgânico",
        "channel": "organic",
        "column": KanbanColumnId.novo_lead,
        "micro": MicroStatus.triagem_incompleta,
        "score": 42,
        "doc_score": 20,
        "initial_fee": 4000.0,
        "claim_value": 18000.0,
        "urgency_hours": 120,
    },
    {
        "name": "Eduardo Barbosa",
        "email": "eduardo.barbosa@local.klm",
        "phone": "+5511980001003",
        "subtheme": HealthSubtheme.tratamento,
        "object": "Terapia ABA 40h",
        "source": "Meta Ads",
        "channel": "meta_ads",
        "column": KanbanColumnId.triagem_sdr,
        "micro": MicroStatus.qualificado,
        "score": 61,
        "doc_score": 40,
        "initial_fee": 4800.0,
        "claim_value": 144000.0,
        "urgency_hours": 48,
    },
    {
        "name": "Rafael Martins",
        "email": "rafael.martins@local.klm",
        "phone": "+5511980001004",
        "subtheme": HealthSubtheme.home_care,
        "object": "Atendimento domiciliar 24h",
        "source": "Formulário",
        "channel": "site_form",
        "column": KanbanColumnId.documentos,
        "micro": MicroStatus.docs_parciais,
        "score": 68,
        "doc_score": 60,
        "initial_fee": 5200.0,
        "claim_value": 216000.0,
        "urgency_hours": 24,
    },
    {
        "name": "Ana Paula Ribeiro",
        "email": "ana.paula@local.klm",
        "phone": "+5511980001005",
        "subtheme": HealthSubtheme.medicamento_alto_custo,
        "object": "Zolgensma",
        "source": "Google Ads",
        "channel": "google_ads",
        "column": KanbanColumnId.reuniao,
        "micro": MicroStatus.reuniao_agendada,
        "score": 82,
        "doc_score": 80,
        "initial_fee": 7500.0,
        "claim_value": 6500000.0,
        "urgency_hours": 12,
        "treatment": {
            "product": "ZOLGENSMA",
            "presentations_per_cycle": 1,
            "cycle_count": 1,
            "prescription_reference": "Prescrição demonstrativa do ambiente local",
        },
    },
    {
        "name": "Cláudia Santos",
        "email": "claudia.santos@local.klm",
        "phone": "+5511980001006",
        "subtheme": HealthSubtheme.opme,
        "object": "Prótese importada",
        "source": "Instagram",
        "channel": "instagram",
        "column": KanbanColumnId.proposta,
        "micro": MicroStatus.proposta_enviada,
        "score": 78,
        "doc_score": 100,
        "initial_fee": 6200.0,
        "claim_value": 128000.0,
        "urgency_hours": 48,
    },
    {
        "name": "Marina Teixeira",
        "email": "marina.teixeira@local.klm",
        "phone": "+5511980001007",
        "subtheme": HealthSubtheme.exame,
        "object": "PET-CT",
        "source": "E-mail",
        "channel": "email",
        "column": KanbanColumnId.contrato,
        "micro": MicroStatus.contrato_enviado,
        "score": 91,
        "doc_score": 100,
        "initial_fee": 4000.0,
        "claim_value": 22000.0,
        "urgency_hours": 72,
    },
    {
        "name": "João Vieira",
        "email": "joao.vieira@local.klm",
        "phone": "+5511980001008",
        "subtheme": HealthSubtheme.cirurgia,
        "object": "Neurocirurgia",
        "source": "Indicação",
        "channel": "referral",
        "column": KanbanColumnId.ganho,
        "micro": MicroStatus.pagamento_confirmado,
        "score": 100,
        "doc_score": 100,
        "initial_fee": 8400.0,
        "claim_value": 180000.0,
        "urgency_hours": 24,
    },
]


class KlmLocalSeedService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.operations = KlmOperationsService(db)

    async def ensure(self) -> tuple[User, Workspace]:
        workspace = await self.db.scalar(
            select(Workspace).where(Workspace.slug == LOCAL_WORKSPACE_SLUG)
        )
        if not workspace:
            workspace = Workspace(
                name="Meu Executivo Local",
                slug=LOCAL_WORKSPACE_SLUG,
                legal_name="Meu Executivo",
                description="Ambiente local de aprovação do Meu Executivo.",
                status=WorkspaceStatus.active,
                setup_mode=WorkspaceSetupMode.assisted,
                is_active=True,
                onboarding_completed=False,
                settings={"runtime": "local_simulation"},
            )
            self.db.add(workspace)
            await self.db.flush()

        workspace.name = "Meu Executivo Local"
        workspace.legal_name = "Meu Executivo"
        workspace.description = "Ambiente local de aprovação do Meu Executivo."
        workspace.status = WorkspaceStatus.active
        workspace.setup_mode = WorkspaceSetupMode.assisted
        workspace.is_active = True
        workspace.onboarding_completed = False

        workspace_settings = workspace.settings or {}
        seed_initialized = (
            workspace_settings.get("klmLocalSeedVersion") == LOCAL_SEED_VERSION
        )
        playbook_seed_initialized = (
            workspace_settings.get("klmPlaybookSeedVersion")
            == LOCAL_PLAYBOOK_SEED_VERSION
        )
        commercial_seed_initialized = (
            workspace_settings.get("klmCommercialSeedVersion")
            == LOCAL_COMMERCIAL_SEED_VERSION
        )

        # Bootstrap is an explicit tenant operation even before a JWT exists.
        await set_workspace_context(self.db, workspace.id)
        if not seed_initialized:
            await MedicationPricingService(self.db).seed_catalog()

        user = await self.db.scalar(select(User).where(User.email == LOCAL_USER_EMAIL))
        if not user:
            user = User(
                name="Kleist Monteiro",
                email=LOCAL_USER_EMAIL,
                agent_type=AgentType.human,
                role=UserRole.admin,
                initials="KM",
                color="#050505",
                is_active=True,
                is_internal_staff=False,
                is_platform_admin=False,
                platform_role_package=None,
                platform_access_active=False,
                email_verified_at=datetime.now(UTC),
            )
            self.db.add(user)
            await self.db.flush()
        else:
            if user.email_verified_at is None:
                user.email_verified_at = datetime.now(UTC)
            if (
                user.is_internal_staff
                or user.is_platform_admin
                or user.platform_access_active
                or user.platform_role_package is not None
            ):
                user.auth_epoch += 1
            user.is_internal_staff = False
            user.is_platform_admin = False
            user.platform_role_package = None
            user.platform_access_active = False
            user.platform_permission_grants = []
            user.platform_permission_denials = []

        membership = await self.db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace.id,
                WorkspaceMembership.user_id == user.id,
            )
        )
        if not membership:
            self.db.add(
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role=WorkspaceRole.workspace_owner,
                    status=MembershipStatus.active,
                    is_default=True,
                    access_package="office_owner",
                )
            )
            await self.db.flush()

        if not commercial_seed_initialized:
            platform_owner = await self._ensure_local_platform_owner()
            if not is_platform_root(platform_owner):
                raise RuntimeError(
                    "O catálogo local só pode ser provisionado pelo platform_owner."
                )
            await set_platform_control_context(
                self.db,
                actor_user_id=platform_owner.id,
                module="commercial_contracts",
                action="manage",
                is_platform_root=True,
            )
            try:
                # A savepoint deixa a transação externa utilizável mesmo quando a
                # convergência comercial falha. Assim o contexto root pode ser
                # limpo antes de propagar o erro, sem executar SQL numa transação
                # PostgreSQL abortada.
                async with self.db.begin_nested():
                    await self._ensure_local_commercial_contract(
                        workspace,
                        platform_owner,
                    )
            finally:
                # Não deixe a autoridade root vazar para o restante do seed tenant
                # nem para uma sessão reutilizada após falha capturada pelo caller.
                await clear_platform_control_context(self.db)
        if not seed_initialized:
            await self.operations.ensure_default_areas(workspace.id)
        if not playbook_seed_initialized:
            await PlaybookService(self.db).ensure_health_baseline(
                workspace_id=workspace.id,
                actor_user_id=user.id,
            )
        if not seed_initialized:
            await self._seed_cases(workspace, user)
            await self._seed_flow(workspace)
            await self._seed_technical_request(workspace, user)
        from app.services.omnichannel_v6_bootstrap_service import (
            OMNICHANNEL_LOCAL_SEED_VERSION,
            OmnichannelV6BootstrapService,
        )

        omnichannel_seed_initialized = (
            workspace_settings.get("klmOmnichannelSeedVersion")
            == OMNICHANNEL_LOCAL_SEED_VERSION
        )
        if not omnichannel_seed_initialized:
            await OmnichannelV6BootstrapService(self.db).ensure_workspace(
                workspace.id,
                user.id,
                include_demo_events=True,
            )
        workspace.settings = {
            **(workspace.settings or {}),
            "runtime": "local_simulation",
            "klmLocalSeedVersion": LOCAL_SEED_VERSION,
            "klmPlaybookSeedVersion": LOCAL_PLAYBOOK_SEED_VERSION,
            "klmCommercialSeedVersion": LOCAL_COMMERCIAL_SEED_VERSION,
            "klmOmnichannelSeedVersion": OMNICHANNEL_LOCAL_SEED_VERSION,
            "legalPricingPolicy": deepcopy(
                (workspace.settings or {}).get("legalPricingPolicy") or DEFAULT_LEGAL_PRICING_POLICY
            ),
            "externalActionsEnabled": False,
            "awsEnabled": False,
        }
        workspace.updated_at = _now()
        if settings.KLM_CHATWOOT_ACCOUNT_ID.strip():
            account_id = settings.KLM_CHATWOOT_ACCOUNT_ID.strip()
            await OSSBridgeService(self.db).upsert_link(
                workspace.id,
                ExternalEntityLinkUpsert(
                    provider=OSSBridgeProvider.chatwoot,
                    klm_entity_type="workspace",
                    klm_entity_id=workspace.id,
                    external_account_id=account_id,
                    external_entity_type="account",
                    external_entity_id=account_id,
                    sync_state=ExternalEntitySyncState.synced,
                    last_synced_at=_now(),
                    metadata={"managedBy": "klm-local-bootstrap"},
                ),
            )
        await self.db.flush()
        return user, workspace

    async def _ensure_local_commercial_contract(
        self,
        workspace: Workspace,
        actor_user: User,
    ) -> None:
        """Provision the canonical local-only Core + Legal contract once.

        The effective capability manifest deliberately fails closed without a
        canonical contract.  Local verification therefore needs a real
        contract, not a legacy subscription fallback.  Re-running the seed
        converges its dedicated local account to Evolution + Jurídico 360 while
        preserving commercial exceptions through the canonical revision path.
        """

        from app.models.commercial_catalog import (
            CommercialContractLineStatus,
            WorkspaceCommercialContract,
        )
        from app.models.entitlement import EntitlementKey
        from app.schemas.commercial_catalog import (
            WorkspaceContractCreateRequest,
            WorkspaceContractLineWrite,
            WorkspaceContractRevisionRequest,
        )
        from app.services.commercial_catalog_service import CommercialCatalogService
        from app.services.commercial_legacy_bridge_service import CanonicalContractCommand

        if not is_platform_root(actor_user):
            raise RuntimeError(
                "Contrato comercial local exige ator platform_owner real."
            )
        existing_contract_id = await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace.id
            )
        )
        catalog_service = CommercialCatalogService(self.db)
        catalog, _ = await catalog_service.bootstrap_default_catalog(
            actor_user_id=actor_user.id,
            idempotency_key=f"klm-local-commercial-catalog:{LOCAL_COMMERCIAL_SEED_VERSION}",
        )
        offers = {item.sku: item for item in catalog}
        evolution = offers["crm_evolution"]
        legal = offers["legal_360"]

        def legal_limit(key: EntitlementKey, quantity: int):
            return legal.snapshot.entitlements[key].model_copy(
                update={
                    "enabled": True,
                    "limit_quantity": quantity,
                }
            )

        default_legal_line = WorkspaceContractLineWrite(
            sku="legal_360",
            policy_version_id=legal.id,
            contracted_price_cents=89_900,
            entitlement_overrides={
                EntitlementKey.users: legal_limit(EntitlementKey.users, 25),
                EntitlementKey.legal_areas: legal_limit(EntitlementKey.legal_areas, 20),
                EntitlementKey.storage_bytes: legal_limit(
                    EntitlementKey.storage_bytes, 25 * 1024**3
                ),
                EntitlementKey.monitored_processes: legal_limit(
                    EntitlementKey.monitored_processes, 100
                ),
                EntitlementKey.legal_documents: legal_limit(
                    EntitlementKey.legal_documents, 20_000
                ),
                EntitlementKey.protocol_folders: legal_limit(
                    EntitlementKey.protocol_folders, 1_000
                ),
            },
        )
        command = CanonicalContractCommand(self.db, control_plane=True)
        if existing_contract_id is None:
            await command.create_contract(
                payload=WorkspaceContractCreateRequest(
                    workspace_id=workspace.id,
                    lines=[
                        WorkspaceContractLineWrite(
                            sku="crm_evolution",
                            policy_version_id=evolution.id,
                        ),
                        default_legal_line,
                    ],
                    reason="Contrato combinado local para validar Core e Jurídico 360.",
                ),
                actor_user_id=actor_user.id,
                idempotency_key=f"klm-local-commercial-contract:{workspace.id}",
            )
            return

        existing = await catalog_service.get_contract(existing_contract_id)
        instant = _now()

        def effective_at(value: datetime) -> datetime:
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

        current_lines = [
            line
            for line in existing.lines
            if line.status is CommercialContractLineStatus.active
            and effective_at(line.effective_from) <= instant
            and (
                line.effective_until is None
                or effective_at(line.effective_until) > instant
            )
        ]
        current_core = next(
            (line for line in current_lines if line.offer_family.value == "crm"),
            None,
        )
        current_legal = next(
            (line for line in current_lines if line.offer_family.value == "legal"),
            None,
        )
        if current_core is not None and current_core.sku == "crm_evolution" and current_legal:
            await command.reconcile_projection(
                workspace_id=workspace.id,
                actor_user_id=actor_user.id,
                idempotency_key=(
                    f"klm-local-commercial-projection:"
                    f"{LOCAL_COMMERCIAL_SEED_VERSION}:{workspace.id}"
                ),
            )
            return

        target_legal = (
            WorkspaceContractLineWrite(
                sku=current_legal.sku,
                policy_version_id=current_legal.policy_version_id,
                quantity=current_legal.quantity,
                contracted_price_cents=current_legal.contracted_price_cents,
                billing_interval=current_legal.billing_interval,
                entitlement_overrides=current_legal.entitlement_overrides,
                effective_from=current_legal.effective_from,
                effective_until=current_legal.effective_until,
            )
            if current_legal is not None
            else default_legal_line
        )
        await command.revise_contract(
            contract_id=existing.id,
            expected_version=existing.version,
            payload=WorkspaceContractRevisionRequest(
                lines=[
                    WorkspaceContractLineWrite(
                        sku="crm_evolution",
                        policy_version_id=evolution.id,
                    ),
                    target_legal,
                ],
                reason="Convergir conta local para Evolution e Jurídico 360.",
            ),
            actor_user_id=actor_user.id,
            idempotency_key=(
                f"klm-local-commercial-converge:"
                f"{LOCAL_COMMERCIAL_SEED_VERSION}:{workspace.id}"
            ),
        )

    async def _ensure_local_platform_owner(self) -> User:
        """Materialize the attributable local root without entering a workspace."""

        platform_user = await self.db.scalar(
            select(User).where(User.email == LOCAL_PLATFORM_USER_EMAIL)
        )
        if not platform_user:
            platform_user = User(
                name="Administrador Meu Executivo",
                email=LOCAL_PLATFORM_USER_EMAIL,
                agent_type=AgentType.human,
                role=UserRole.admin,
                initials="ME",
                color="#0067ff",
                is_active=True,
                is_internal_staff=True,
                is_platform_admin=True,
                platform_role_package="platform_owner",
                platform_access_active=True,
                platform_permission_grants=[],
                platform_permission_denials=[],
                email_verified_at=datetime.now(UTC),
            )
            self.db.add(platform_user)
            await self.db.flush()
        else:
            authority_changed = not is_platform_root(platform_user)
            if platform_user.email_verified_at is None:
                platform_user.email_verified_at = datetime.now(UTC)
            platform_user.name = "Administrador Meu Executivo"
            platform_user.agent_type = AgentType.human
            platform_user.role = UserRole.admin
            platform_user.initials = "ME"
            platform_user.color = "#0067ff"
            platform_user.is_active = True
            platform_user.deleted_at = None
            platform_user.is_internal_staff = True
            platform_user.is_platform_admin = True
            platform_user.platform_role_package = "platform_owner"
            platform_user.platform_access_active = True
            platform_user.platform_permission_grants = []
            platform_user.platform_permission_denials = []
            if authority_changed:
                platform_user.auth_epoch += 1

        membership = await self.db.scalar(
            select(WorkspaceMembership.id).where(
                WorkspaceMembership.user_id == platform_user.id,
            )
        )
        if membership is not None:
            raise RuntimeError(
                "A identidade local da Blackbox não pode possuir membership de workspace."
            )
        if not is_platform_root(platform_user):
            raise RuntimeError(
                "A identidade local da Blackbox não satisfaz o predicado root."
            )
        await self.db.flush()
        return platform_user

    async def ensure_platform_identity(self) -> User:
        """Criar a identidade local exclusiva da Blackbox, sem membership de cliente."""
        _workspace_user, _workspace = await self.ensure()
        platform_user = await self._ensure_local_platform_owner()
        for role_package in LOCAL_PLATFORM_STAFF_IDENTITIES:
            await self._ensure_local_platform_staff(role_package)
        await self.db.flush()
        return platform_user

    async def ensure_platform_staff_identity(self, role_package: str) -> User:
        """Criar uma identidade funcional local, sempre isolada dos workspaces."""
        if role_package not in LOCAL_PLATFORM_STAFF_IDENTITIES:
            raise LookupError("Função interna local não encontrada.")
        await self.ensure_platform_identity()
        return await self._ensure_local_platform_staff(role_package)

    async def _ensure_local_platform_staff(self, role_package: str) -> User:
        fixture = LOCAL_PLATFORM_STAFF_IDENTITIES.get(role_package)
        package = PLATFORM_ROLE_PACKAGES.get(role_package)
        if fixture is None or package is None or not package.assignable:
            raise LookupError("Função interna local não encontrada.")

        staff_user = await self.db.scalar(
            select(User).where(User.email == fixture["email"])
        )
        if staff_user is None:
            staff_user = User(
                name=fixture["name"],
                email=fixture["email"],
                agent_type=AgentType.human,
                role=UserRole.admin,
                initials=fixture["initials"],
                color=fixture["color"],
                is_active=True,
                is_internal_staff=True,
                is_platform_admin=False,
                platform_role_package=role_package,
                platform_access_active=True,
                platform_permission_grants=[],
                platform_permission_denials=[],
                email_verified_at=datetime.now(UTC),
            )
            self.db.add(staff_user)
            await self.db.flush()
        else:
            staff_user.name = fixture["name"]
            staff_user.initials = fixture["initials"]
            staff_user.color = fixture["color"]
            staff_user.agent_type = AgentType.human
            staff_user.role = UserRole.admin
            staff_user.is_active = True
            staff_user.is_internal_staff = True
            staff_user.is_platform_admin = False
            staff_user.platform_role_package = role_package
            staff_user.platform_access_active = True
            staff_user.platform_permission_grants = []
            staff_user.platform_permission_denials = []
            if staff_user.email_verified_at is None:
                staff_user.email_verified_at = datetime.now(UTC)

        membership = await self.db.scalar(
            select(WorkspaceMembership.id).where(
                WorkspaceMembership.user_id == staff_user.id,
            )
        )
        if membership is not None:
            raise RuntimeError(
                "A identidade funcional da Blackbox não pode possuir membership de workspace."
            )
        await self.db.flush()
        return staff_user

    async def _seed_cases(self, workspace: Workspace, user: User) -> None:
        for index, fixture in enumerate(CASE_FIXTURES):
            client = await self._upsert_client(workspace, fixture)
            matter = await self._upsert_matter(workspace, user, client, fixture)
            await self._upsert_financial_projection(matter, fixture)
            await self._seed_treatment_estimate(workspace, user, matter, fixture)
            await self._seed_documents(workspace, user, matter, fixture)
            thread = await self._upsert_thread(workspace, user, client, matter, fixture)
            await self._seed_messages(workspace, user, thread, fixture, index)
            await self._seed_follow_up(workspace, user, matter, thread, fixture)
            await self._seed_tracking(workspace, user, client, matter, fixture, index)
            await self._seed_meeting(workspace, user, matter, fixture, index)
            await self._seed_finance(workspace, matter, fixture, index)
            await self._seed_approval(user, matter, fixture)

    async def _upsert_client(self, workspace: Workspace, fixture: dict[str, Any]) -> Client:
        client = await self.db.scalar(
            select(Client).where(
                Client.workspace_id == workspace.id,
                Client.email == fixture["email"],
                Client.deleted_at.is_(None),
            )
        )
        if not client:
            client = Client(
                workspace_id=workspace.id,
                name=fixture["name"],
                email=fixture["email"],
                phone=fixture["phone"],
                notes="Registro demonstrativo local; não representa cliente real.",
            )
            self.db.add(client)
            await self.db.flush()
        return client

    async def _upsert_matter(
        self,
        workspace: Workspace,
        user: User,
        client: Client,
        fixture: dict[str, Any],
    ) -> Matter:
        matter = await self.db.scalar(
            select(Matter).where(
                Matter.workspace_id == workspace.id,
                Matter.client_id == client.id,
                Matter.health_object_name == fixture["object"],
                Matter.deleted_at.is_(None),
            )
        )
        values = {
            "practice_area": PracticeArea.saude_suplementar,
            "demand_type": f"Negativa de cobertura: {fixture['object']}",
            "lead_source": fixture["source"],
            "claim_value": fixture["claim_value"],
            "health_subtheme": fixture["subtheme"],
            "health_object_name": fixture["object"],
            "health_insurer": "Operadora Saúde Local",
            "health_denial_reason": "Negativa contratual recebida e pendente de revisão jurídica.",
            "medical_urgency_hours": fixture["urgency_hours"],
            "economic_value_annual": fixture["claim_value"],
            "lead_score": fixture["score"],
            "column_id": fixture["column"],
            "micro_status": fixture["micro"],
            "priority": Priority.alta if fixture["urgency_hours"] <= 24 else Priority.media,
            "urgency": Urgency.urgente if fixture["urgency_hours"] <= 24 else Urgency.normal,
            "classification": (
                ClientClassification.Premium
                if fixture["score"] >= 80
                else ClientClassification.Forte
                if fixture["score"] >= 60
                else ClientClassification.Bom
            ),
            "doc_score": fixture["doc_score"],
            "production_score": min(100, fixture["score"]),
            "next_action": "Continuar o fluxo local de saúde e revisar documentos.",
            "responsible_human_id": user.id,
        }
        if not matter:
            matter = Matter(
                workspace_id=workspace.id,
                client_id=client.id,
                **values,
            )
            self.db.add(matter)
        else:
            for key, value in values.items():
                setattr(matter, key, value)
            matter.updated_at = _now()
        await self.db.flush()
        return matter

    async def _upsert_financial_projection(self, matter: Matter, fixture: dict[str, Any]) -> None:
        estimate = await self.db.scalar(
            select(HonorariumEstimate).where(HonorariumEstimate.matter_id == matter.id)
        )
        final_fee = round(fixture["claim_value"] * 0.2, 2)
        if not estimate:
            estimate = HonorariumEstimate(matter_id=matter.id)
            self.db.add(estimate)
        estimate.contract_type = "fixo_mais_exito"
        estimate.fixed_fee = fixture["initial_fee"]
        estimate.quota_litis_percentage = 20.0
        estimate.expected_expenses = 450.0
        estimate.total_estimated_value = fixture["initial_fee"] + final_fee
        estimate.notes = (
            "Cenário local: honorários iniciais, finais e comparação com/sem "
            "obrigação de fazer de conteúdo econômico conforme revisão humana."
        )

        projection = await self.db.scalar(
            select(RevenueProjection).where(RevenueProjection.matter_id == matter.id)
        )
        if not projection:
            projection = RevenueProjection(matter_id=matter.id)
            self.db.add(projection)
        projection.estimated_claim_value = fixture["claim_value"]
        projection.projected_revenue = fixture["initial_fee"] + final_fee
        projection.success_probability = fixture["score"] / 100
        projection.confidence_level = min(0.95, 0.5 + fixture["doc_score"] / 220)
        projection.payback_days = 30
        projection.currency = "BRL"
        projection.notes = "Projeção local, sem decisão jurídica automatizada."
        await self.db.flush()

    async def _seed_treatment_estimate(
        self,
        workspace: Workspace,
        user: User,
        matter: Matter,
        fixture: dict[str, Any],
    ) -> None:
        treatment = fixture.get("treatment")
        if not treatment:
            return
        medication = await self.db.scalar(
            select(MedicationReference).where(
                MedicationReference.product_name == treatment["product"]
            )
        )
        if medication is None:
            return
        await MedicationPricingService(self.db).upsert_treatment_estimate(
            workspace.id,
            matter.id,
            TreatmentEstimateUpsert(
                medication_reference_id=medication.id,
                presentations_per_cycle=treatment["presentations_per_cycle"],
                cycle_count=treatment["cycle_count"],
                prescription_reference=treatment["prescription_reference"],
                review_status="revisado",
                apply_to_matter_economic_value=True,
                notes="Cenário demonstrativo local; revisar a prescrição antes de uso real.",
            ),
            actor_user_id=user.id,
            actor_label=user.name,
        )

    async def _seed_documents(
        self,
        workspace: Workspace,
        user: User,
        matter: Matter,
        fixture: dict[str, Any],
    ) -> None:
        names = [
            "Negativa do plano",
            "Prescrição médica",
            "Relatório médico",
            "Carteirinha do plano",
            "Contrato do plano",
        ]
        desired = min(5, max(0, round(fixture["doc_score"] / 20)))
        knowledge = KnowledgeService(self.db)
        for name in names[:desired]:
            document = await self.db.scalar(
                select(Document).where(Document.matter_id == matter.id, Document.name == name)
            )
            if not document:
                document = Document(
                    matter_id=matter.id,
                    created_by=user.id,
                    name=name,
                    doc_type=DocumentType.comprovante,
                    status=DocumentStatus.completo,
                    mime_type="application/pdf",
                )
                self.db.add(document)
                await self.db.flush()
            fixture_pdf = _local_fixture_pdf(name, fixture["object"])
            storage_object = await self.db.scalar(
                select(StorageObject)
                .where(
                    StorageObject.workspace_id == workspace.id,
                    StorageObject.object_id == document.id,
                )
                .order_by(StorageObject.version.desc())
                .limit(1)
            )
            legacy_key = document.s3_key
            if storage_object is None:
                storage = LocalObjectStorageService(self.db)
                ascii_name = normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
                authorization = await storage.create_upload_authorization(
                    workspace_id=workspace.id,
                    actor_user_id=user.id,
                    filename=f"{ascii_name}.pdf",
                    content_type="application/pdf",
                    byte_size=len(fixture_pdf),
                    checksum_sha256=hashlib.sha256(fixture_pdf).hexdigest(),
                    retention_until=None,
                    expires_in=300,
                    object_id=document.id,
                    matter_id=matter.id,
                    client_id=matter.client_id,
                    initial_object_id=True,
                )
                storage_object = await storage.finalize_upload(
                    ticket=authorization.ticket.token,
                    data=fixture_pdf,
                    request_content_type="application/pdf",
                )
                if legacy_key and legacy_key != storage_object.storage_key:
                    try:
                        S3Service.delete_local_object(legacy_key, missing_ok=True)
                    except (OSError, ValueError, StorageUnavailableError):
                        pass
            if storage_object.deleted_at is not None:
                continue
            document.s3_key = storage_object.storage_key
            document.file_size = (
                str(storage_object.byte_size) if storage_object.byte_size is not None else None
            )
            document.mime_type = (
                storage_object.detected_content_type or storage_object.declared_content_type
            )
            document.status = (
                DocumentStatus.completo
                if storage_object.state is StorageObjectState.available
                else DocumentStatus.invalido
            )
            if storage_object.state is not StorageObjectState.available:
                continue
            await knowledge.upsert_chunk(
                workspace_id=workspace.id,
                matter_id=matter.id,
                source_type="local_document",
                source_ref=str(document.id),
                title=name,
                content_text=(
                    f"Documento local {name} do caso sobre {fixture['object']}. "
                    "Conteúdo fictício para validação do fluxo de RAG."
                ),
                summary=f"{name} indexado localmente.",
                tags=["local", "saude", fixture["subtheme"].value],
                metadata={"documentId": str(document.id), "fixture": True},
                rank_hint=4,
                created_by_user_id=user.id,
            )

    async def _upsert_thread(
        self,
        workspace: Workspace,
        user: User,
        client: Client,
        matter: Matter,
        fixture: dict[str, Any],
    ) -> OmnichannelThread:
        thread = await self.db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.workspace_id == workspace.id,
                OmnichannelThread.matter_id == matter.id,
            )
        )
        channel = (
            OmnichannelChannel.email
            if fixture["channel"] == "email"
            else OmnichannelChannel.form
            if fixture["channel"] == "site_form"
            else OmnichannelChannel.instagram
            if fixture["channel"] == "instagram"
            else OmnichannelChannel.whatsapp
        )
        owner_by_stage = {
            KanbanColumnId.novo_lead: "sdr_saude",
            KanbanColumnId.triagem_sdr: "sdr_saude",
            KanbanColumnId.documentos: "crm_precisao",
            KanbanColumnId.reuniao: "secretaria",
            KanbanColumnId.proposta: "closer",
            KanbanColumnId.contrato: "paralegal",
            KanbanColumnId.pagamento: "cobranca",
            KanbanColumnId.ganho: "andamento",
        }
        status_by_stage = {
            KanbanColumnId.documentos: ThreadStatus.waiting_documents,
            KanbanColumnId.reuniao: ThreadStatus.waiting_human,
            KanbanColumnId.ganho: ThreadStatus.closed,
        }
        if not thread:
            thread = await self.operations.create_thread(
                workspace.id,
                user.id,
                OmnichannelThreadCreate(
                    matter_id=matter.id,
                    client_id=client.id,
                    primary_channel=channel,
                    subject=fixture["subtheme"].value,
                    object_name=fixture["object"],
                    owner_agent_key=owner_by_stage.get(fixture["column"], "juridico_saude"),
                    score=fixture["score"],
                    sla_due_at=_now() + timedelta(minutes=15 + fixture["score"]),
                    thread_metadata={
                        "source": fixture["source"],
                        "fixture": True,
                        "healthSubtheme": fixture["subtheme"].value,
                    },
                ),
            )
        await self.operations.update_thread(
            workspace.id,
            thread.id,
            user.id,
            OmnichannelThreadUpdate(
                status=status_by_stage.get(fixture["column"], ThreadStatus.open),
                owner_agent_key=owner_by_stage.get(fixture["column"], "juridico_saude"),
                score=fixture["score"],
            ),
        )
        return thread

    async def _seed_messages(
        self,
        workspace: Workspace,
        user: User,
        thread: OmnichannelThread,
        fixture: dict[str, Any],
        index: int,
    ) -> None:
        base = _now() - timedelta(hours=8 - index)
        messages = [
            OmnichannelMessageCreate(
                channel=thread.primary_channel,
                direction=MessageDirection.inbound,
                author_type=MessageAuthorType.client,
                author_label=fixture["name"],
                body=f"Meu plano negou {fixture['object']} e preciso entender o que fazer.",
                idempotency_key=f"seed:{fixture['channel']}:{index}:inbound",
                occurred_at=base,
                message_metadata={"fixture": True},
            ),
            OmnichannelMessageCreate(
                channel=thread.primary_channel,
                direction=MessageDirection.outbound,
                author_type=MessageAuthorType.agent,
                author_label="SDR Saúde",
                body="Recebi. Vou organizar os dados e pedir apenas os documentos necessários.",
                idempotency_key=f"seed:{fixture['channel']}:{index}:outbound",
                occurred_at=base + timedelta(minutes=2),
                message_metadata={"fixture": True, "agentKey": "sdr_saude"},
            ),
            OmnichannelMessageCreate(
                channel=OmnichannelChannel.internal,
                direction=MessageDirection.internal,
                author_type=MessageAuthorType.agent,
                author_label="SDR Saúde para Jurídico Saúde",
                body=f"Validar urgência e documentos para {fixture['object']}.",
                idempotency_key=f"seed:{fixture['channel']}:{index}:internal",
                occurred_at=base + timedelta(minutes=3),
                message_metadata={"fixture": True, "visibleToHuman": True},
            ),
        ]
        for message in messages:
            existing = await self.db.scalar(
                select(OmnichannelMessage).where(
                    OmnichannelMessage.workspace_id == workspace.id,
                    OmnichannelMessage.idempotency_key == message.idempotency_key,
                )
            )
            if existing is not None:
                if existing.thread_id != thread.id:
                    raise RuntimeError(
                        "Seed local encontrou uma chave de mensagem vinculada a outra conversa."
                    )
                continue
            await self.operations.create_message(workspace.id, thread.id, user.id, message)

    async def _seed_follow_up(
        self,
        workspace: Workspace,
        user: User,
        matter: Matter,
        thread: OmnichannelThread,
        fixture: dict[str, Any],
    ) -> None:
        if fixture["column"] == KanbanColumnId.ganho:
            return
        existing = await self.db.scalar(
            select(FollowUpTask.id).where(
                FollowUpTask.workspace_id == workspace.id,
                FollowUpTask.thread_id == thread.id,
                FollowUpTask.status.in_([FollowUpStatus.scheduled, FollowUpStatus.due]),
            )
        )
        if not existing:
            await self.operations.schedule_follow_up(
                workspace.id,
                user.id,
                FollowUpCreate(
                    matter_id=matter.id,
                    thread_id=thread.id,
                    channel=thread.primary_channel,
                    due_at=_now() + timedelta(hours=2),
                    reason="Lead nunca fica sem resposta; execução externa bloqueada.",
                    payload={"fixture": True},
                ),
            )

    async def _seed_tracking(
        self,
        workspace: Workspace,
        user: User,
        client: Client,
        matter: Matter,
        fixture: dict[str, Any],
        index: int,
    ) -> None:
        events = [("lead_created", None)]
        if fixture["channel"] in {"google_ads", "meta_ads", "instagram"}:
            events.append(("ad_spend", 120.0 + index * 18))
        if fixture["score"] >= 60:
            events.append(("lead_qualified", None))
        stage_order = list(KanbanColumnId)
        if stage_order.index(fixture["column"]) >= stage_order.index(KanbanColumnId.reuniao):
            events.append(("meeting_booked", None))
        if stage_order.index(fixture["column"]) >= stage_order.index(KanbanColumnId.contrato):
            events.append(("contract_created", fixture["initial_fee"]))
        if fixture["column"] == KanbanColumnId.ganho:
            events.append(("payment_confirmed", fixture["initial_fee"]))
        for event_name, value in events:
            await self.operations.record_acquisition_event(
                workspace.id,
                user.id,
                AcquisitionEventCreate(
                    matter_id=matter.id,
                    client_id=client.id,
                    event_name=event_name,
                    channel=fixture["channel"],
                    source=fixture["source"],
                    medium="local_fixture",
                    campaign="saude-negativas-local",
                    dedupe_key=f"seed:{event_name}:{matter.id}",
                    happened_at=_now() - timedelta(days=max(0, 7 - index)),
                    value=value,
                    click_ids={"local": f"click-{index}"},
                    event_metadata={"fixture": True},
                ),
            )

    async def _seed_meeting(
        self,
        workspace: Workspace,
        user: User,
        matter: Matter,
        fixture: dict[str, Any],
        index: int,
    ) -> None:
        eligible = {
            KanbanColumnId.reuniao,
            KanbanColumnId.proposta,
            KanbanColumnId.contrato,
            KanbanColumnId.ganho,
        }
        if fixture["column"] not in eligible:
            return
        title = f"Reunião online: {fixture['name']}"
        existing = await self.db.scalar(
            select(Meeting.id).where(
                Meeting.workspace_id == workspace.id,
                Meeting.matter_id == matter.id,
                Meeting.title == title,
            )
        )
        if existing:
            return
        completed = fixture["column"] != KanbanColumnId.reuniao
        starts_at = _now() + timedelta(hours=2) if not completed else _now() - timedelta(days=index)
        self.db.add(
            Meeting(
                workspace_id=workspace.id,
                matter_id=matter.id,
                responsible_user_id=user.id,
                title=title,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=45),
                status=MeetingStatus.completed if completed else MeetingStatus.scheduled,
                location="Online",
                notes="Reunião local; nenhum convite externo foi enviado.",
                recipient_overrides=[],
            )
        )
        await self.db.flush()

    async def _seed_finance(
        self,
        workspace: Workspace,
        matter: Matter,
        fixture: dict[str, Any],
        index: int,
    ) -> None:
        if fixture["column"] not in {
            KanbanColumnId.contrato,
            KanbanColumnId.pagamento,
            KanbanColumnId.ganho,
        }:
            return
        external_ref = f"local:{matter.id}"
        entry = await self.db.scalar(
            select(FinanceEntry).where(
                FinanceEntry.workspace_id == workspace.id,
                FinanceEntry.external_ref == external_ref,
            )
        )
        if not entry:
            paid = fixture["column"] == KanbanColumnId.ganho
            entry = FinanceEntry(
                workspace_id=workspace.id,
                matter_id=matter.id,
                title=f"Honorários iniciais: {fixture['name']}",
                description="Cobrança demonstrativa local.",
                entry_type=FinanceEntryType.receivable,
                status=FinanceEntryStatus.paid if paid else FinanceEntryStatus.pending,
                provider="local",
                external_ref=external_ref,
                amount=fixture["initial_fee"],
                due_date=_now() + timedelta(days=3),
                paid_at=_now() - timedelta(days=1) if paid else None,
                notify_client=False,
                tags=["local", "honorarios"],
                entry_metadata={"fixture": True},
            )
            self.db.add(entry)
            await self.db.flush()
        reminder = await self.db.scalar(
            select(FinanceReminder.id).where(FinanceReminder.finance_entry_id == entry.id)
        )
        if not reminder and entry.status != FinanceEntryStatus.paid:
            self.db.add(
                FinanceReminder(
                    workspace_id=workspace.id,
                    finance_entry_id=entry.id,
                    status=FinanceReminderStatus.pending,
                    remind_at=_now() + timedelta(hours=4 + index),
                    channel="whatsapp",
                    message="Lembrete local; envio externo bloqueado.",
                )
            )
            await self.db.flush()

    async def _seed_approval(self, user: User, matter: Matter, fixture: dict[str, Any]) -> None:
        if fixture["column"] != KanbanColumnId.reuniao:
            return
        existing = await self.db.scalar(
            select(ApprovalQueueItem.id).where(
                ApprovalQueueItem.matter_id == matter.id,
                ApprovalQueueItem.approval_type == "meeting_strategy_review",
            )
        )
        if not existing:
            self.db.add(
                ApprovalQueueItem(
                    matter_id=matter.id,
                    requested_by=user.id,
                    approval_type="meeting_strategy_review",
                    intent_key="seed:meeting_strategy_review",
                    title=f"Revisar estratégia antes da reunião: {fixture['object']}",
                    description="Aprovação humana obrigatória no ambiente local.",
                    priority=ApprovalPriority.alta,
                    status=ApprovalStatus.pending,
                )
            )
            matter.needs_approval = True
            matter.updated_at = _now()
            await self.db.flush()

    async def _seed_flow(self, workspace: Workspace) -> None:
        flow_specs = [
            {
                "name": "Saúde: triagem até aprovação local",
                "description": "Triagem, documentos, análise jurídica, reunião e aprovação humana.",
                "trigger": "new_lead",
                "category": "intake",
                "nodes": [
                    {
                        "id": "lead",
                        "type": "trigger",
                        "position": {"x": 80, "y": 180},
                        "data": {"label": "Novo lead"},
                    },
                    {
                        "id": "sdr",
                        "type": "agent",
                        "position": {"x": 300, "y": 180},
                        "data": {"label": "Triagem SDR"},
                    },
                    {
                        "id": "docs",
                        "type": "condition",
                        "position": {"x": 520, "y": 180},
                        "data": {"label": "Docs suficientes?"},
                    },
                    {
                        "id": "follow",
                        "type": "scheduled_message",
                        "position": {"x": 520, "y": 340},
                        "data": {"label": "Follow-up local"},
                    },
                    {
                        "id": "legal",
                        "type": "agent",
                        "position": {"x": 740, "y": 180},
                        "data": {"label": "Jurídico Saúde"},
                    },
                    {
                        "id": "meeting",
                        "type": "human_pause",
                        "position": {"x": 960, "y": 180},
                        "data": {"label": "Reunião online"},
                    },
                    {
                        "id": "contract",
                        "type": "zapsign_request",
                        "position": {"x": 1180, "y": 180},
                        "data": {"label": "Contrato local"},
                    },
                    {
                        "id": "payment",
                        "type": "human_pause",
                        "position": {"x": 1400, "y": 180},
                        "data": {"label": "Pagamento"},
                    },
                    {
                        "id": "petition",
                        "type": "human_pause",
                        "position": {"x": 1620, "y": 180},
                        "data": {"label": "Aprovar petição"},
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "lead", "target": "sdr"},
                    {"id": "e2", "source": "sdr", "target": "docs"},
                    {"id": "e3", "source": "docs", "target": "legal", "label": "sim"},
                    {"id": "e4", "source": "docs", "target": "follow", "label": "não"},
                    {"id": "e5", "source": "follow", "target": "docs"},
                    {"id": "e6", "source": "legal", "target": "meeting"},
                    {"id": "e7", "source": "meeting", "target": "contract"},
                    {"id": "e8", "source": "contract", "target": "payment"},
                    {"id": "e9", "source": "payment", "target": "petition"},
                ],
            },
            {
                "name": "Follow-up: documentos pendentes",
                "description": "Retomada estruturada da coleta de documentos com escalada humana.",
                "trigger": "manual",
                "category": "follow_up",
                "nodes": [
                    {
                        "id": "start",
                        "type": "trigger",
                        "position": {"x": 60, "y": 140},
                        "data": {"label": "Ativação manual"},
                        "config": {"event": "manual"},
                    },
                    {
                        "id": "crm",
                        "type": "agent",
                        "position": {"x": 250, "y": 140},
                        "data": {"label": "CRM confere pendências"},
                        "config": {
                            "agent": "crm_precision",
                            "stage_key": "documentos",
                            "use_rag": True,
                        },
                    },
                    {
                        "id": "request",
                        "type": "send_message",
                        "position": {"x": 440, "y": 140},
                        "data": {"label": "Solicitar documentos"},
                        "config": {
                            "message": (
                                "Olá {nome}. Para concluir sua análise, ainda preciso "
                                "dos documentos pendentes do caso. Posso ajudar com algum deles?"
                            )
                        },
                    },
                    {
                        "id": "wait18",
                        "type": "wait",
                        "position": {"x": 440, "y": 300},
                        "data": {"label": "Aguardar 18h"},
                        "config": {"delay_hours": 18, "condition": "no_response"},
                    },
                    {
                        "id": "reminder",
                        "type": "send_message",
                        "position": {"x": 250, "y": 300},
                        "data": {"label": "Lembrete contextual"},
                        "config": {
                            "message": (
                                "Oi, {nome}. Sua análise continua reservada. Se estiver com "
                                "dificuldade para localizar algum documento, me diga qual para "
                                "eu orientar."
                            )
                        },
                    },
                    {
                        "id": "wait30",
                        "type": "wait",
                        "position": {"x": 60, "y": 300},
                        "data": {"label": "Aguardar 30h"},
                        "config": {"delay_hours": 30, "condition": "no_response"},
                    },
                    {
                        "id": "secretary",
                        "type": "human_pause",
                        "position": {"x": 60, "y": 460},
                        "data": {"label": "Secretaria decide próximo passo"},
                        "config": {
                            "reason": (
                                "Validar resposta, bloqueio ou necessidade de contato humano "
                                "antes de nova tentativa."
                            )
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "start", "target": "crm"},
                    {"id": "e2", "source": "crm", "target": "request"},
                    {"id": "e3", "source": "request", "target": "wait18"},
                    {"id": "e4", "source": "wait18", "target": "reminder"},
                    {"id": "e5", "source": "reminder", "target": "wait30"},
                    {"id": "e6", "source": "wait30", "target": "secretary"},
                ],
            },
            {
                "name": "Recuperação: oportunidade sem resposta",
                "description": (
                    "Retoma oportunidades frias com contexto, limite de tentativas e "
                    "decisão humana."
                ),
                "trigger": "manual",
                "category": "recovery",
                "nodes": [
                    {
                        "id": "start",
                        "type": "trigger",
                        "position": {"x": 60, "y": 140},
                        "data": {"label": "Ativação manual"},
                        "config": {"event": "manual"},
                    },
                    {
                        "id": "review",
                        "type": "agent",
                        "position": {"x": 250, "y": 140},
                        "data": {"label": "Revisar motivo da perda"},
                        "config": {
                            "agent": "klm_sdr",
                            "stage_key": "perdido",
                            "use_rag": True,
                            "human_review_required": True,
                        },
                    },
                    {
                        "id": "reopen",
                        "type": "send_message",
                        "position": {"x": 440, "y": 140},
                        "data": {"label": "Retomada contextual"},
                        "config": {
                            "message": (
                                "Olá, {nome}. Retomei sua análise e posso esclarecer os "
                                "próximos passos do seu caso. Se ainda fizer sentido conversar, "
                                "estou à disposição por aqui."
                            )
                        },
                    },
                    {
                        "id": "wait48",
                        "type": "wait",
                        "position": {"x": 440, "y": 300},
                        "data": {"label": "Aguardar 48h"},
                        "config": {"delay_hours": 48, "condition": "no_response"},
                    },
                    {
                        "id": "value",
                        "type": "send_message",
                        "position": {"x": 250, "y": 300},
                        "data": {"label": "Orientação útil"},
                        "config": {
                            "message": (
                                "{nome}, posso organizar uma conversa online breve para revisar "
                                "a negativa e os documentos. Se preferir não receber novos "
                                "lembretes, basta me avisar."
                            )
                        },
                    },
                    {
                        "id": "wait72",
                        "type": "wait",
                        "position": {"x": 60, "y": 300},
                        "data": {"label": "Aguardar 72h"},
                        "config": {"delay_hours": 72, "condition": "no_response"},
                    },
                    {
                        "id": "decision",
                        "type": "human_pause",
                        "position": {"x": 60, "y": 460},
                        "data": {"label": "Decisão humana"},
                        "config": {
                            "reason": (
                                "Encerrar, manter em remarketing permitido ou oferecer material "
                                "educativo conforme o contexto."
                            )
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "start", "target": "review"},
                    {"id": "e2", "source": "review", "target": "reopen"},
                    {"id": "e3", "source": "reopen", "target": "wait48"},
                    {"id": "e4", "source": "wait48", "target": "value"},
                    {"id": "e5", "source": "value", "target": "wait72"},
                    {"id": "e6", "source": "wait72", "target": "decision"},
                ],
            },
        ]

        for spec in flow_specs:
            existing = await self.db.scalar(
                select(Flow).where(
                    Flow.workspace_id == workspace.id,
                    Flow.name == spec["name"],
                    Flow.deleted_at.is_(None),
                )
            )
            if existing:
                metadata = existing.trigger_config or {}
                if (
                    metadata.get("localOnly")
                    and metadata.get("category") == spec["category"]
                    and metadata.get("seedVersion") != LOCAL_SEED_VERSION
                ):
                    existing.description = spec["description"]
                    existing.nodes = spec["nodes"]
                    existing.edges = spec["edges"]
                    existing.trigger_config = {
                        **metadata,
                        "seedVersion": LOCAL_SEED_VERSION,
                    }
                continue
            self.db.add(
                Flow(
                    workspace_id=workspace.id,
                    name=spec["name"],
                    description=spec["description"],
                    practice_area="saude_suplementar",
                    is_template=False,
                    is_active=True,
                    trigger=spec["trigger"],
                    trigger_config={
                        "localOnly": True,
                        "category": spec["category"],
                        "seedVersion": LOCAL_SEED_VERSION,
                    },
                    nodes=spec["nodes"],
                    edges=spec["edges"],
                )
            )
        await self.db.flush()

    async def _seed_technical_request(self, workspace: Workspace, user: User) -> None:
        existing = await self.db.scalar(
            select(TechnicalRequest.id).where(
                TechnicalRequest.workspace_id == workspace.id,
                TechnicalRequest.title == "Validar responsividade do chat",
            )
        )
        if not existing:
            await self.operations.create_technical_request(
                workspace.id,
                user.id,
                TechnicalRequestCreate(
                    title="Validar responsividade do chat",
                    description="Solicitação local para exercitar o fluxo nativo do TI Agent.",
                    route="/app/conversas",
                    severity=TechnicalRequestSeverity.medium,
                    evidence=[{"kind": "fixture", "local": True}],
                ),
            )
