import type { AuthUser } from '@/types';
import { canPlatform, isPlatformRoot } from '@/lib/platform-access';

export interface BlackboxSection {
  path: string;
  label: string;
  description: string;
  module: string;
  action?: string;
  rootOnly?: boolean;
}

export const BLACKBOX_SECTIONS: BlackboxSection[] = [
  { path: '/blackbox', label: 'Meu painel', description: 'Prioridades e atalhos da sua função.', module: 'dashboard' },
  { path: '/blackbox/tasks', label: 'Tarefas e aprovações', description: 'Tarefas internas autoritativas e work items operacionais.', module: 'platform_tasks' },
  { path: '/blackbox/operations', label: 'Operações em massa', description: 'Prévia, impacto, execução e resultados de lotes.', module: 'bulk_operations' },
  { path: '/blackbox/agent-operations', label: 'Agentes e chamadas humanas', description: 'Deployments, catálogo de nós, decisões e acompanhamento do dono.', module: 'ai_operations' },
  { path: '/blackbox/workspaces', label: 'Contas', description: 'Contas contratantes, saúde e onboarding.', module: 'customers' },
  { path: '/blackbox/growth-crm', label: 'Comercial', description: 'Prospecção, oportunidades e vendas do SaaS.', module: 'sales' },
  { path: '/blackbox/support-tickets', label: 'SAC e tickets', description: 'Atendimento, triagem e resolução.', module: 'support' },
  { path: '/blackbox/inbox', label: 'Inbox do SaaS', description: 'Conversas dos canais oficiais da plataforma.', module: 'support' },
  { path: '/blackbox/support-access', label: 'Acesso assistido', description: 'Acesso temporário, justificado e auditado.', module: 'support_access' },
  { path: '/blackbox/token-packages', label: 'Pacotes de tokens', description: 'Solicitações de créditos extras tratadas pelo suporte.', module: 'support' },
  { path: '/blackbox/billing', label: 'Financeiro', description: 'MRR, ARR, cobrança e assinaturas.', module: 'finance' },
  { path: '/blackbox/finance', label: 'Financeiro Executivo 360', description: 'KPIs, DRE, drill-down e exportações por conta.', module: 'finance' },
  { path: '/blackbox/plans', label: 'Planos', description: 'Pacotes, limites e políticas comerciais.', module: 'plans' },
  { path: '/blackbox/benefits', label: 'Indicações e benefícios', description: 'Programa de indicação, benefícios e Partner.', module: 'benefits' },
  { path: '/blackbox/coupons', label: 'Studio de cupons', description: 'Cupons, limites, resgates e auditoria.', module: 'coupons' },
  { path: '/blackbox/performance', label: 'Desempenho', description: 'Gamificação das equipes e indicadores comparáveis.', module: 'performance' },
  { path: '/blackbox/advanced-reports', label: 'Relatórios avançados', description: 'Filtros, tabelas e gráficos sobre os indicadores da plataforma.', module: 'reports' },
  { path: '/blackbox/exports', label: 'Central de downloads', description: 'Exportações privadas, assíncronas e auditadas.', module: 'dashboard' },
  { path: '/blackbox/creative-library', label: 'Biblioteca de campanhas', description: 'Criativos e campanhas de referência da plataforma.', module: 'marketing', rootOnly: true },
  { path: '/blackbox/announcements', label: 'Comunicados', description: 'Avisos gerais e segmentados.', module: 'announcements' },
  { path: '/blackbox/team', label: 'Equipe do SaaS', description: 'Funcionários, funções e permissões.', module: 'staff' },
  { path: '/blackbox/team/continuity', label: 'Continuidade da equipe', description: 'Substituições temporárias e transferências definitivas.', module: 'staff_reassignment' },
  { path: '/blackbox/configuration-studio', label: 'Estúdio assistido', description: 'Agentes, fluxos e experiência das contas.', module: 'workspace_configuration' },
  { path: '/blackbox/errors', label: 'Erros e incidentes', description: 'Falhas, recorrência e saúde operacional.', module: 'errors' },
  { path: '/blackbox/ai-operations', label: 'Operações de IA', description: 'Modelos, consumo, qualidade e execução.', module: 'ai_operations' },
  { path: '/blackbox/ai-costs', label: 'Custos internos de IA', description: 'Custos reais visíveis apenas ao titular.', module: 'ai_operations', rootOnly: true },
  { path: '/blackbox/meta-rates', label: 'Tarifas Meta', description: 'Custos comerciais e versões de tarifação.', module: 'settings', rootOnly: true },
  { path: '/blackbox/meta-health', label: 'Saúde Meta', description: 'Erros, supressões e entrega dos canais Meta.', module: 'infrastructure', rootOnly: true },
  { path: '/blackbox/infrastructure', label: 'Infraestrutura', description: 'Banco, cache, workers, tarefas e histórico operacional.', module: 'infrastructure' },
  { path: '/blackbox/official-whatsapp', label: 'WhatsApps oficiais', description: 'Canais oficiais administrados pelo SaaS.', module: 'whatsapp' },
  { path: '/blackbox/people', label: 'Usuários das contas', description: 'Pessoas vinculadas às contas contratantes.', module: 'customers' },
  { path: '/blackbox/site', label: 'Site', description: 'Conteúdo e operação do site público.', module: 'content' },
  { path: '/blackbox/blog', label: 'Blog', description: 'Conteúdo editorial e publicações.', module: 'content' },
  { path: '/blackbox/media', label: 'Mídia', description: 'Biblioteca de ativos da plataforma.', module: 'content' },
  { path: '/blackbox/seo', label: 'SEO', description: 'Indexação e presença orgânica.', module: 'content' },
  { path: '/blackbox/google-business', label: 'Google Business', description: 'Perfil, publicações e presença local.', module: 'content' },
  { path: '/blackbox/settings', label: 'Configurações globais', description: 'Documentos globais versionados, publicação e restauração.', module: 'settings' },
  { path: '/blackbox/governance/integrations', label: 'Integrações', description: 'Políticas e controles globais das integrações.', module: 'integrations' },
  { path: '/blackbox/governance/logs', label: 'Logs', description: 'Política de observabilidade e retenção de logs.', module: 'logs' },
  { path: '/blackbox/governance/audit', label: 'Auditoria', description: 'Controles e política da trilha de auditoria.', module: 'audit' },
  { path: '/blackbox/governance/security', label: 'Segurança', description: 'Controles operacionais de segurança da plataforma.', module: 'security' },
  { path: '/blackbox/governance/compliance', label: 'Compliance', description: 'Controles, evidências e responsáveis de conformidade.', module: 'compliance' },
  { path: '/blackbox/governance/data_governance', label: 'Governança de dados', description: 'Retenção, classificação e responsabilidades sobre dados.', module: 'data_governance' },
  { path: '/blackbox/governance/releases', label: 'Releases', description: 'Política, versão ativa, rollback e critérios de liberação.', module: 'releases' },
  { path: '/blackbox/governance/feature_flags', label: 'Feature flags', description: 'Catálogo e estado auditável de flags globais.', module: 'feature_flags' },
];

export const BLACKBOX_ROLE_LABELS: Record<string, string> = {
  platform_owner: 'Titular da plataforma',
  platform_admin: 'Administração da plataforma',
  sales_sdr: 'Vendas · SDR',
  commercial_manager: 'Gestão comercial',
  onboarding: 'Onboarding',
  customer_success: 'Sucesso do cliente',
  support: 'Suporte',
  technical_support: 'Suporte técnico',
  finance_billing: 'Financeiro e cobrança',
  marketing_content: 'Marketing e conteúdo',
  community_manager: 'Comunidade',
  compliance_security: 'Compliance e segurança',
  product_operations: 'Produto e operações',
  engineering: 'Engenharia e TI',
};

export interface BlackboxRoleProfile {
  label: string;
  mission: string;
  primaryPath: string;
  primaryLabel: string;
  queueLabel: string;
  guardrail: string;
}

export interface BlackboxWorkstream {
  title: string;
  description: string;
  path: string;
  eyebrow: string;
  capability: string;
}

const SHARED_WORKSTREAMS: Record<string, BlackboxWorkstream> = {
  customers: {
    title: 'Contas contratantes',
    description: 'Saúde, implantação, responsáveis e contexto operacional das contas.',
    path: '/blackbox/workspaces',
    eyebrow: 'Carteira',
    capability: 'customers',
  },
  support: {
    title: 'SAC e tickets',
    description: 'Triagem, prazos, responsáveis e resolução das solicitações.',
    path: '/blackbox/support-tickets',
    eyebrow: 'Fila',
    capability: 'support',
  },
  supportAccess: {
    title: 'Acesso assistido',
    description: 'Solicitações temporárias, justificadas e integralmente auditadas.',
    path: '/blackbox/support-access',
    eyebrow: 'Controle',
    capability: 'support_access',
  },
  configuration: {
    title: 'Configuração das contas',
    description: 'Agentes, fluxos, integrações e experiências versionadas por conta.',
    path: '/blackbox/configuration-studio',
    eyebrow: 'Estúdio',
    capability: 'workspace_configuration',
  },
  benefits: {
    title: 'Benefícios e indicações',
    description: 'Concessões, parceiros e histórico do programa de relacionamento.',
    path: '/blackbox/benefits',
    eyebrow: 'Relacionamento',
    capability: 'benefits',
  },
  reports: {
    title: 'Relatórios avançados',
    description: 'Indicadores autorizados, filtros e exportações da plataforma.',
    path: '/blackbox/advanced-reports',
    eyebrow: 'Análise',
    capability: 'reports',
  },
  performance: {
    title: 'Desempenho das equipes',
    description: 'Metas, produtividade e indicadores comparáveis da operação.',
    path: '/blackbox/performance',
    eyebrow: 'Gestão',
    capability: 'performance',
  },
  incidents: {
    title: 'Erros e incidentes',
    description: 'Sinais operacionais, recorrência, severidade e tratamento.',
    path: '/blackbox/errors',
    eyebrow: 'Risco',
    capability: 'errors',
  },
  content: {
    title: 'Conteúdo institucional',
    description: 'Site, blog, mídia e presença orgânica da marca.',
    path: '/blackbox/site',
    eyebrow: 'Publicação',
    capability: 'content',
  },
  announcements: {
    title: 'Comunicação com a base',
    description: 'Comunicados segmentados e relacionamento recorrente.',
    path: '/blackbox/announcements',
    eyebrow: 'Comunidade',
    capability: 'announcements',
  },
  aiOperations: {
    title: 'Operações de IA',
    description: 'Execuções, qualidade, fallback e sinais da camada de IA.',
    path: '/blackbox/ai-operations',
    eyebrow: 'Inteligência',
    capability: 'ai_operations',
  },
  billing: {
    title: 'Receita e cobrança',
    description: 'Assinaturas, inadimplência, MRR, ARR e conciliação.',
    path: '/blackbox/billing',
    eyebrow: 'Financeiro',
    capability: 'finance',
  },
  growth: {
    title: 'Pipeline comercial',
    description: 'Prospecção, oportunidades, atividades e avanço do funil.',
    path: '/blackbox/growth-crm',
    eyebrow: 'Comercial',
    capability: 'sales',
  },
  plans: {
    title: 'Planos e políticas',
    description: 'Pacotes, limites e regras comerciais oferecidas às contas.',
    path: '/blackbox/plans',
    eyebrow: 'Oferta',
    capability: 'plans',
  },
  team: {
    title: 'Equipe do SaaS',
    description: 'Funções, permissões e continuidade da operação interna.',
    path: '/blackbox/team',
    eyebrow: 'Pessoas',
    capability: 'staff',
  },
};

const ROLE_WORKSTREAM_KEYS: Record<string, string[]> = {
  platform_admin: [
    'customers', 'team', 'billing', 'support', 'incidents', 'aiOperations',
    'configuration', 'reports',
  ],
  sales_sdr: ['growth', 'customers', 'plans'],
  commercial_manager: ['growth', 'performance', 'reports', 'billing', 'customers', 'plans'],
  onboarding: ['customers', 'configuration', 'support', 'benefits', 'plans'],
  customer_success: ['customers', 'reports', 'support', 'benefits', 'billing'],
  support: ['support', 'supportAccess', 'incidents', 'configuration', 'benefits', 'customers'],
  technical_support: ['support', 'incidents', 'supportAccess', 'configuration', 'customers'],
  finance_billing: ['billing', 'reports', 'customers', 'benefits', 'plans'],
  marketing_content: ['content', 'reports', 'announcements'],
  community_manager: ['announcements', 'content'],
  compliance_security: ['incidents', 'supportAccess', 'reports', 'customers'],
  product_operations: ['reports', 'aiOperations', 'incidents', 'configuration', 'customers'],
  engineering: ['incidents', 'aiOperations', 'support', 'supportAccess', 'configuration'],
};

export const BLACKBOX_ROLE_PROFILES: Record<string, BlackboxRoleProfile> = {
  platform_admin: {
    label: BLACKBOX_ROLE_LABELS.platform_admin,
    mission: 'Administrar a operação interna do SaaS conforme as permissões delegadas pelo titular.',
    primaryPath: '/blackbox/overview',
    primaryLabel: 'Abrir administração da plataforma',
    queueLabel: 'Contas, equipes e operações autorizadas',
    guardrail: 'Custos internos, tarifas e controles exclusivos do titular permanecem bloqueados.',
  },
  sales_sdr: {
    label: BLACKBOX_ROLE_LABELS.sales_sdr,
    mission: 'Qualificar oportunidades e conduzir o primeiro contato comercial do SaaS.',
    primaryPath: '/blackbox/growth-crm',
    primaryLabel: 'Abrir carteira comercial',
    queueLabel: 'Oportunidades sob sua responsabilidade',
    guardrail: 'Sem acesso operacional às contas contratantes.',
  },
  commercial_manager: {
    label: BLACKBOX_ROLE_LABELS.commercial_manager,
    mission: 'Gerir metas, distribuição, desempenho e previsibilidade comercial.',
    primaryPath: '/blackbox/growth-crm',
    primaryLabel: 'Abrir gestão comercial',
    queueLabel: 'Equipe e oportunidades do SaaS',
    guardrail: 'Acesso limitado ao ciclo comercial da plataforma.',
  },
  onboarding: {
    label: BLACKBOX_ROLE_LABELS.onboarding,
    mission: 'Implantar novas contas com checklist, prazo e evidências de ativação.',
    primaryPath: '/blackbox/workspaces',
    primaryLabel: 'Abrir implantações',
    queueLabel: 'Contas em onboarding',
    guardrail: 'Acesso assistido exige solicitação própria, prazo e auditoria.',
  },
  customer_success: {
    label: BLACKBOX_ROLE_LABELS.customer_success,
    mission: 'Acompanhar saúde, adoção, riscos e expansão das contas contratantes.',
    primaryPath: '/blackbox/workspaces',
    primaryLabel: 'Abrir carteira de clientes',
    queueLabel: 'Contas sob acompanhamento',
    guardrail: 'Dados operacionais do cliente não são abertos por padrão.',
  },
  support: {
    label: BLACKBOX_ROLE_LABELS.support,
    mission: 'Triar, responder e resolver solicitações dos usuários do SaaS.',
    primaryPath: '/blackbox/support-tickets',
    primaryLabel: 'Abrir fila de atendimento',
    queueLabel: 'Tickets da fila de suporte',
    guardrail: 'O ticket não concede acesso automático à conta do cliente.',
  },
  technical_support: {
    label: BLACKBOX_ROLE_LABELS.technical_support,
    mission: 'Investigar falhas técnicas, incidentes e problemas de integração.',
    primaryPath: '/blackbox/support-tickets',
    primaryLabel: 'Abrir fila técnica',
    queueLabel: 'Tickets técnicos e incidentes',
    guardrail: 'Toda intervenção precisa de escopo, justificativa e trilha.',
  },
  finance_billing: {
    label: BLACKBOX_ROLE_LABELS.finance_billing,
    mission: 'Gerir assinaturas, cobrança, inadimplência e receita recorrente.',
    primaryPath: '/blackbox/billing',
    primaryLabel: 'Abrir financeiro do SaaS',
    queueLabel: 'Pendências de cobrança e assinatura',
    guardrail: 'Custos internos de IA permanecem exclusivos do titular.',
  },
  marketing_content: {
    label: BLACKBOX_ROLE_LABELS.marketing_content,
    mission: 'Operar conteúdo, aquisição e presença institucional da plataforma.',
    primaryPath: '/blackbox/site',
    primaryLabel: 'Abrir operação de conteúdo',
    queueLabel: 'Conteúdos e campanhas da plataforma',
    guardrail: 'Sem acesso a conversas ou documentos das contas.',
  },
  community_manager: {
    label: BLACKBOX_ROLE_LABELS.community_manager,
    mission: 'Administrar comunicação, comunidade e avisos aos usuários.',
    primaryPath: '/blackbox/announcements',
    primaryLabel: 'Abrir comunicados',
    queueLabel: 'Comunicados e interações da comunidade',
    guardrail: 'Segmentação e envio obedecem às permissões concedidas.',
  },
  compliance_security: {
    label: BLACKBOX_ROLE_LABELS.compliance_security,
    mission: 'Revisar acessos, eventos de segurança, auditoria e conformidade.',
    primaryPath: '/blackbox/errors',
    primaryLabel: 'Abrir monitoramento',
    queueLabel: 'Alertas, evidências e exceções',
    guardrail: 'Acesso de investigação é mínimo, temporário e auditado.',
  },
  product_operations: {
    label: BLACKBOX_ROLE_LABELS.product_operations,
    mission: 'Converter sinais de clientes e operação em decisões de produto.',
    primaryPath: '/blackbox/errors',
    primaryLabel: 'Abrir operação de produto',
    queueLabel: 'Erros, sugestões e versões',
    guardrail: 'Feedback não concede acesso ao conteúdo privado da conta.',
  },
  engineering: {
    label: BLACKBOX_ROLE_LABELS.engineering,
    mission: 'Manter disponibilidade, integrações, releases e resposta a incidentes.',
    primaryPath: '/blackbox/errors',
    primaryLabel: 'Abrir operação técnica',
    queueLabel: 'Incidentes e falhas de infraestrutura',
    guardrail: 'Acesso assistido depende de autorização explícita e expira.',
  },
};

const DEFAULT_ROLE_PROFILE: BlackboxRoleProfile = {
  label: 'Equipe Meu Executivo',
  mission: 'Executar apenas as atividades liberadas para a função atribuída.',
  primaryPath: '/blackbox/overview',
  primaryLabel: 'Abrir painel autorizado',
  queueLabel: 'Itens sob sua responsabilidade',
  guardrail: 'Nenhum acesso a contas contratantes é concedido implicitamente.',
};

export function blackboxRoleLabel(user: AuthUser | null | undefined): string {
  if (isPlatformRoot(user)) return BLACKBOX_ROLE_LABELS.platform_owner;
  return BLACKBOX_ROLE_LABELS[user?.platformRolePackage ?? ''] ?? 'Equipe Meu Executivo';
}

export function blackboxHomePath(user: AuthUser | null | undefined): string {
  if (isPlatformRoot(user)) return '/blackbox/overview';
  return blackboxRoleProfile(user).primaryPath;
}

export function blackboxRoleProfile(
  user: AuthUser | null | undefined,
): BlackboxRoleProfile {
  if (isPlatformRoot(user)) {
    return {
      label: BLACKBOX_ROLE_LABELS.platform_owner,
      mission: 'Governar a plataforma, suas contas, equipes, riscos e resultados.',
      primaryPath: '/blackbox/overview',
      primaryLabel: 'Abrir visão executiva',
      queueLabel: 'Decisões e alertas da plataforma',
      guardrail: 'Ações sensíveis exigem autenticação reforçada e auditoria.',
    };
  }
  return BLACKBOX_ROLE_PROFILES[user?.platformRolePackage ?? '']
    ?? DEFAULT_ROLE_PROFILE;
}

export function blackboxRoleWorkstreams(
  user: AuthUser | null | undefined,
): BlackboxWorkstream[] {
  const keys = isPlatformRoot(user)
    ? ['customers', 'billing', 'support', 'incidents', 'aiOperations', 'team', 'reports', 'content']
    : ROLE_WORKSTREAM_KEYS[user?.platformRolePackage ?? ''] ?? [];
  return keys
    .map((key) => SHARED_WORKSTREAMS[key])
    .filter((item): item is BlackboxWorkstream => Boolean(item))
    .filter((item) => canOpenBlackboxPath(user, item.path));
}

export function visibleBlackboxSections(user: AuthUser | null | undefined): BlackboxSection[] {
  return BLACKBOX_SECTIONS.filter((section) => (
    !section.rootOnly
    && (
      section.path === '/blackbox'
      || canPlatform(user, section.module, section.action ?? 'view')
      || (section.path === '/blackbox/inbox' && canPlatform(user, 'sales', 'view'))
    )
  )).concat(isPlatformRoot(user)
    ? BLACKBOX_SECTIONS.filter((section) => section.rootOnly)
    : []
  );
}

export function canOpenBlackboxPath(
  user: AuthUser | null | undefined,
  pathname: string,
): boolean {
  if (isPlatformRoot(user)) return true;
  if (pathname === '/blackbox' || pathname === '/blackbox/overview') {
    return canPlatform(user, 'dashboard', 'view');
  }
  if (pathname === '/blackbox/support-session') {
    return canPlatform(user, 'support_access', 'request');
  }
  if (pathname.startsWith('/blackbox/inbox')) {
    return canPlatform(user, 'support', 'view') || canPlatform(user, 'sales', 'view');
  }
  const section = BLACKBOX_SECTIONS
    .filter((candidate) => candidate.path !== '/blackbox')
    .sort((left, right) => right.path.length - left.path.length)
    .find((candidate) => pathname.startsWith(candidate.path));
  if (section?.rootOnly) return false;
  return section
    ? canPlatform(user, section.module, section.action ?? 'view')
    : false;
}
