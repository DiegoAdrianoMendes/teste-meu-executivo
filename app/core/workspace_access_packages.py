"""Pacotes de acesso prontos para as equipes dos escritórios contratantes."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.rbac import Permission
from app.models.workspace_membership import WorkspaceRole


@dataclass(frozen=True)
class WorkspaceAccessPackage:
    key: str
    label: str
    description: str
    workspace_role: WorkspaceRole
    permission_grants: tuple[Permission, ...] = ()
    permission_denials: tuple[Permission, ...] = ()
    sensitive_data_policy: str = "hidden"


def _p(module: str, action: str) -> Permission:
    return Permission(module=module, action=action, scope="workspace")


WORKSPACE_ACCESS_PACKAGES: dict[str, WorkspaceAccessPackage] = {
    "office_owner": WorkspaceAccessPackage(
        key="office_owner",
        label="Titular do escritório",
        description="Controle integral do workspace, assinatura, equipe, dados e configurações.",
        workspace_role=WorkspaceRole.workspace_owner,
        sensitive_data_policy="edit",
    ),
    "office_admin": WorkspaceAccessPackage(
        key="office_admin",
        label="Administrador do escritório",
        description="Administração operacional, usuários, integrações, automações e dados do escritório.",
        workspace_role=WorkspaceRole.workspace_admin,
        sensitive_data_policy="edit",
    ),
    "managing_partner": WorkspaceAccessPackage(
        key="managing_partner",
        label="Sócio gestor",
        description="Gestão jurídica, aprovações, relatórios, equipe jurídica, agentes e automações.",
        workspace_role=WorkspaceRole.legal_manager,
        permission_grants=(
            _p("users", "manage"),
            _p("memberships", "manage"),
            _p("roles", "view"),
            _p("permissions", "view"),
            _p("automations", "view"),
            _p("automations", "manage"),
            _p("agents", "view"),
            _p("agents", "manage"),
            _p("audit", "view"),
        ),
        sensitive_data_policy="edit",
    ),
    "legal_manager": WorkspaceAccessPackage(
        key="legal_manager",
        label="Gestor jurídico",
        description="Coordena casos, documentos, aprovações e desempenho da operação jurídica.",
        workspace_role=WorkspaceRole.legal_manager,
        sensitive_data_policy="edit",
    ),
    "senior_lawyer": WorkspaceAccessPackage(
        key="senior_lawyer",
        label="Advogado sênior",
        description="Conduz casos, conversa com clientes, revisa documentos e aprova entregas.",
        workspace_role=WorkspaceRole.legal_manager,
        sensitive_data_policy="edit",
    ),
    "equity_partner": WorkspaceAccessPackage(
        key="equity_partner",
        label="Sócio de área",
        description="Lidera uma área jurídica, revisa entregas, acompanha indicadores e decide aprovações.",
        workspace_role=WorkspaceRole.legal_manager,
        sensitive_data_policy="edit",
    ),
    "practice_coordinator": WorkspaceAccessPackage(
        key="practice_coordinator",
        label="Coordenador jurídico",
        description="Coordena carteira, prazos, distribuição de trabalho e qualidade técnica da equipe.",
        workspace_role=WorkspaceRole.legal_manager,
        sensitive_data_policy="edit",
    ),
    "associate_lawyer": WorkspaceAccessPackage(
        key="associate_lawyer",
        label="Advogado associado",
        description="Conduz casos e atividades jurídicas com acesso aos dados necessários do cliente.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"), _p("lead_data", "edit")),
        sensitive_data_policy="edit",
    ),
    "junior_lawyer": WorkspaceAccessPackage(
        key="junior_lawyer",
        label="Advogado júnior",
        description="Atua em casos, pesquisas, documentos e comunicações sob supervisão.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"), _p("lead_data", "edit")),
        permission_denials=(_p("aprovacoes", "approve"),),
        sensitive_data_policy="edit",
    ),
    "legal_intern": WorkspaceAccessPackage(
        key="legal_intern",
        label="Estagiário jurídico",
        description="Apoia pesquisas, documentos e tarefas sem aprovar ou excluir entregas.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"),),
        permission_denials=(
            _p("aprovacoes", "approve"),
            _p("kanban", "delete"),
        ),
        sensitive_data_policy="view",
    ),
    "litigation_lawyer": WorkspaceAccessPackage(
        key="litigation_lawyer",
        label="Advogado contencioso",
        description="Opera processos, prazos, peças, audiências e comunicações do contencioso.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"), _p("lead_data", "edit")),
        sensitive_data_policy="edit",
    ),
    "advisory_lawyer": WorkspaceAccessPackage(
        key="advisory_lawyer",
        label="Advogado consultivo",
        description="Conduz consultas, pareceres, análises preventivas e entregas consultivas.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"), _p("lead_data", "edit")),
        sensitive_data_policy="edit",
    ),
    "contracts_lawyer": WorkspaceAccessPackage(
        key="contracts_lawyer",
        label="Advogado de contratos",
        description="Elabora, revisa e acompanha contratos e respectivas aprovações.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"), _p("lead_data", "edit")),
        sensitive_data_policy="edit",
    ),
    "lawyer": WorkspaceAccessPackage(
        key="lawyer",
        label="Advogado",
        description="Opera casos e comunicações com acesso aos dados necessários do cliente.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"), _p("lead_data", "edit")),
        sensitive_data_policy="edit",
    ),
    "paralegal": WorkspaceAccessPackage(
        key="paralegal",
        label="Paralegal",
        description="Coleta dados e documentos, atualiza cards e acompanha tarefas dos casos.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"), _p("lead_data", "edit")),
        sensitive_data_policy="edit",
    ),
    "intake_sdr": WorkspaceAccessPackage(
        key="intake_sdr",
        label="Triagem / SDR jurídico",
        description="Qualifica leads, solicita documentos e atualiza o funil sem aprovar peças.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"), _p("lead_data", "edit")),
        permission_denials=(_p("aprovacoes", "view"),),
        sensitive_data_policy="edit",
    ),
    "case_controller": WorkspaceAccessPackage(
        key="case_controller",
        label="Controladoria jurídica",
        description="Controla prazos, publicações, cadastros, distribuição e conferência operacional.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"),),
        permission_denials=(_p("aprovacoes", "approve"),),
        sensitive_data_policy="view",
    ),
    "legal_operations": WorkspaceAccessPackage(
        key="legal_operations",
        label="Legal Operations",
        description="Estrutura processos, automações, indicadores e capacidade operacional jurídica.",
        workspace_role=WorkspaceRole.legal_manager,
        permission_grants=(
            _p("automations", "view"),
            _p("automations", "manage"),
            _p("relatorios", "export"),
        ),
        sensitive_data_policy="view",
    ),
    "court_correspondent": WorkspaceAccessPackage(
        key="court_correspondent",
        label="Correspondente jurídico",
        description="Executa diligências e tarefas atribuídas com acesso restrito ao necessário.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"),),
        permission_denials=(_p("aprovacoes", "view"),),
        sensitive_data_policy="view",
    ),
    "secretary": WorkspaceAccessPackage(
        key="secretary",
        label="Secretaria / recepção",
        description="Agenda reuniões, atende mensagens e organiza o fluxo sem editar dados sensíveis.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"),),
        permission_denials=(_p("aprovacoes", "view"),),
        sensitive_data_policy="view",
    ),
    "finance": WorkspaceAccessPackage(
        key="finance",
        label="Financeiro / cobrança",
        description="Cobrança, conciliação, receita, lembretes e integrações financeiras.",
        workspace_role=WorkspaceRole.finance_manager,
        sensitive_data_policy="hidden",
    ),
    "accounting": WorkspaceAccessPackage(
        key="accounting",
        label="Contabilidade / fiscal",
        description="Acompanha registros financeiros e relatórios sem acessar conversas ou dados jurídicos brutos.",
        workspace_role=WorkspaceRole.finance_manager,
        sensitive_data_policy="hidden",
    ),
    "people_hr": WorkspaceAccessPackage(
        key="people_hr",
        label="Pessoas / RH",
        description="Acompanha equipe, disponibilidade e indicadores individuais sem acessar casos de clientes.",
        workspace_role=WorkspaceRole.viewer,
        permission_grants=(_p("users", "view"), _p("relatorios", "view")),
        permission_denials=(_p("kanban", "view"), _p("lista", "view"), _p("mensagens", "view")),
        sensitive_data_policy="hidden",
    ),
    "client_success": WorkspaceAccessPackage(
        key="client_success",
        label="Relacionamento com clientes",
        description="Acompanha comunicações, satisfação e continuidade do atendimento sem aprovar peças.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"),),
        permission_denials=(_p("aprovacoes", "approve"),),
        sensitive_data_policy="view",
    ),
    "business_development": WorkspaceAccessPackage(
        key="business_development",
        label="Comercial / novos negócios",
        description="Qualifica oportunidades e acompanha o funil comercial sem acessar a execução jurídica.",
        workspace_role=WorkspaceRole.operator,
        permission_grants=(_p("lead_data", "view"), _p("lead_data", "edit")),
        permission_denials=(_p("aprovacoes", "view"),),
        sensitive_data_policy="edit",
    ),
    "marketing": WorkspaceAccessPackage(
        key="marketing",
        label="Marketing / growth",
        description="Aquisição e relatórios agregados, sem acesso às conversas ou dados dos clientes.",
        workspace_role=WorkspaceRole.viewer,
        permission_grants=(_p("relatorios", "view"), _p("relatorios", "export")),
        permission_denials=(
            _p("kanban", "view"),
            _p("lista", "view"),
            _p("mensagens", "view"),
        ),
        sensitive_data_policy="hidden",
    ),
    "it_integrations": WorkspaceAccessPackage(
        key="it_integrations",
        label="TI / integrações",
        description="Configura integrações e segurança sem acessar cards, listas ou conversas.",
        workspace_role=WorkspaceRole.viewer,
        permission_grants=(
            _p("configuracoes", "view"),
            _p("configuracoes", "edit"),
            _p("integrations", "view"),
            _p("integrations", "manage"),
            _p("security", "view"),
            _p("audit", "view"),
        ),
        permission_denials=(
            _p("kanban", "view"),
            _p("lista", "view"),
            _p("mensagens", "view"),
        ),
        sensitive_data_policy="hidden",
    ),
    "auditor": WorkspaceAccessPackage(
        key="auditor",
        label="Auditoria / compliance",
        description="Relatórios, segurança e trilhas de auditoria em leitura, sem dados comerciais brutos.",
        workspace_role=WorkspaceRole.viewer,
        permission_grants=(
            _p("relatorios", "view"),
            _p("relatorios", "export"),
            _p("security", "view"),
            _p("audit", "view"),
        ),
        permission_denials=(
            _p("kanban", "view"),
            _p("lista", "view"),
            _p("mensagens", "view"),
        ),
        sensitive_data_policy="hidden",
    ),
}


def get_workspace_access_package(key: str | None) -> WorkspaceAccessPackage | None:
    return WORKSPACE_ACCESS_PACKAGES.get(str(key or ""))
