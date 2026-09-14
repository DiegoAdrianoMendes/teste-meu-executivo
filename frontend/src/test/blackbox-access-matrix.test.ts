import { describe, expect, it } from 'vitest';

import {
  BLACKBOX_ROLE_PROFILES,
  blackboxRoleProfile,
  blackboxRoleWorkstreams,
  canOpenBlackboxPath,
  visibleBlackboxSections,
} from '@/blackbox/access';
import type { AuthUser } from '@/types';

function platformUser(
  role: string,
  permissions: Array<{ module: string; action: string }>,
): AuthUser {
  return {
    id: `${role}-id`,
    name: role,
    initials: role.slice(0, 2).toUpperCase(),
    email: `${role}@example.com`,
    role: 'admin',
    isInternalStaff: true,
    isPlatformAdmin: false,
    isPlatformRoot: false,
    platformAccessActive: true,
    platformRolePackage: role,
    platformPermissions: permissions,
  };
}

describe('matriz de acesso da Blackbox', () => {
  it('define uma área de trabalho própria para cada função do SaaS', () => {
    const rolePackages = [
      'platform_admin',
      'sales_sdr',
      'commercial_manager',
      'onboarding',
      'customer_success',
      'support',
      'technical_support',
      'finance_billing',
      'marketing_content',
      'community_manager',
      'compliance_security',
      'product_operations',
      'engineering',
    ];

    expect(Object.keys(BLACKBOX_ROLE_PROFILES).sort()).toEqual(
      [...rolePackages].sort(),
    );
    for (const role of rolePackages) {
      const profile = blackboxRoleProfile(platformUser(role, []));
      expect(profile.primaryPath).toMatch(/^\/blackbox(?:\/|$)/);
      expect(profile.primaryPath).not.toMatch(/^\/app(?:\/|$)/);
      expect(profile.mission.length).toBeGreaterThan(20);
      expect(profile.guardrail.length).toBeGreaterThan(20);
    }
  });

  it('limita o SDR ao painel pessoal, contas, planos e comercial', () => {
    const user = platformUser('sales_sdr', [
      { module: 'dashboard', action: 'view' },
      { module: 'customers', action: 'view' },
      { module: 'plans', action: 'view' },
      { module: 'sales', action: 'view' },
      { module: 'sales', action: 'manage' },
      { module: 'platform_tasks', action: 'view' },
    ]);

    const paths = visibleBlackboxSections(user).map((section) => section.path);
    expect(paths).toEqual([
      '/blackbox',
      '/blackbox/tasks',
      '/blackbox/workspaces',
      '/blackbox/growth-crm',
      '/blackbox/inbox',
      '/blackbox/plans',
      '/blackbox/exports',
      '/blackbox/people',
    ]);
    expect(canOpenBlackboxPath(user, '/blackbox/growth-crm')).toBe(true);
    expect(canOpenBlackboxPath(user, '/blackbox/support-tickets')).toBe(false);
    expect(canOpenBlackboxPath(user, '/blackbox/performance')).toBe(false);
    expect(canOpenBlackboxPath(user, '/blackbox/advanced-reports')).toBe(false);
    expect(canOpenBlackboxPath(user, '/blackbox/ai-costs')).toBe(false);
  });

  it('separa a gestão de desempenho do acesso comercial de execução', () => {
    const manager = platformUser('commercial_manager', [
      { module: 'dashboard', action: 'view' },
      { module: 'performance', action: 'view' },
      { module: 'reports', action: 'view' },
      { module: 'sales', action: 'view' },
      { module: 'sales', action: 'manage' },
    ]);

    const paths = visibleBlackboxSections(manager).map((section) => section.path);
    expect(paths).toContain('/blackbox/performance');
    expect(paths).toContain('/blackbox/advanced-reports');
    expect(paths).not.toContain('/blackbox/creative-library');
    expect(canOpenBlackboxPath(manager, '/blackbox/performance')).toBe(true);
    expect(canOpenBlackboxPath(manager, '/blackbox/creative-library')).toBe(false);
  });

  it('limita suporte ao SAC, acesso assistido, tokens, contas e erros', () => {
    const user = platformUser('support', [
      { module: 'dashboard', action: 'view' },
      { module: 'customers', action: 'view' },
      { module: 'support', action: 'view' },
      { module: 'support', action: 'respond' },
      { module: 'support_access', action: 'view' },
      { module: 'support_access', action: 'request' },
      { module: 'errors', action: 'view' },
    ]);

    const paths = visibleBlackboxSections(user).map((section) => section.path);
    expect(paths).toContain('/blackbox/support-tickets');
    expect(paths).toContain('/blackbox/support-access');
    expect(paths).toContain('/blackbox/token-packages');
    expect(paths).toContain('/blackbox/workspaces');
    expect(paths).toContain('/blackbox/errors');
    expect(paths).not.toContain('/blackbox/growth-crm');
    expect(paths).not.toContain('/blackbox/billing');
    expect(paths).not.toContain('/blackbox/ai-costs');

    const workstreams = blackboxRoleWorkstreams(user);
    expect(workstreams.map((item) => item.path)).toEqual([
      '/blackbox/support-tickets',
      '/blackbox/support-access',
      '/blackbox/errors',
      '/blackbox/workspaces',
    ]);
    expect(workstreams.every((item) => canOpenBlackboxPath(user, item.path))).toBe(true);
  });

  it('traduz capacidades internas em frentes navegáveis sem ampliar o RBAC', () => {
    const onboardingUser = platformUser('onboarding', [
      { module: 'dashboard', action: 'view' },
      { module: 'customers', action: 'view' },
      { module: 'workspace_configuration', action: 'view' },
      { module: 'support', action: 'view' },
      { module: 'benefits', action: 'view' },
      { module: 'plans', action: 'view' },
      { module: 'integrations', action: 'view' },
    ]);

    const workstreams = blackboxRoleWorkstreams(onboardingUser);
    expect(workstreams.map((item) => item.title)).toEqual([
      'Contas contratantes',
      'Configuração das contas',
      'SAC e tickets',
      'Benefícios e indicações',
      'Planos e políticas',
    ]);
    expect(workstreams.every((item) => item.path.startsWith('/blackbox/'))).toBe(true);
    expect(workstreams.every((item) => canOpenBlackboxPath(onboardingUser, item.path))).toBe(true);
  });

  it('mantém custos internos e saúde Meta exclusivos do titular', () => {
    const admin = platformUser('platform_admin', [
      { module: 'dashboard', action: 'view' },
      { module: 'ai_operations', action: 'view' },
      { module: 'settings', action: 'view' },
      { module: 'infrastructure', action: 'view' },
    ]);
    expect(canOpenBlackboxPath(admin, '/blackbox/ai-costs')).toBe(false);
    expect(canOpenBlackboxPath(admin, '/blackbox/meta-rates')).toBe(false);
    expect(canOpenBlackboxPath(admin, '/blackbox/meta-health')).toBe(false);

    const owner = { ...admin, isPlatformRoot: true };
    expect(canOpenBlackboxPath(owner, '/blackbox/ai-costs')).toBe(true);
    expect(canOpenBlackboxPath(owner, '/blackbox/meta-rates')).toBe(true);
    expect(canOpenBlackboxPath(owner, '/blackbox/meta-health')).toBe(true);
  });

  it('não confunde o pacote platform_admin com a identidade root', () => {
    const operationalAdmin = platformUser('platform_admin', [
      { module: 'dashboard', action: 'view' },
      { module: 'finance', action: 'manage' },
      { module: 'infrastructure', action: 'view' },
    ]);
    const owner = { ...operationalAdmin, isPlatformRoot: true };

    expect(canOpenBlackboxPath(operationalAdmin, '/blackbox/ai-costs')).toBe(false);
    expect(canOpenBlackboxPath(owner, '/blackbox/ai-costs')).toBe(true);
  });

  it('routes managed incidents through the security capability, not infrastructure', () => {
    const compliance = platformUser('compliance_security', [
      { module: 'security', action: 'view' },
    ]);
    const technicalSupport = platformUser('technical_support', [
      { module: 'infrastructure', action: 'view' },
    ]);

    expect(canOpenBlackboxPath(compliance, '/blackbox/governance/security')).toBe(true);
    expect(canOpenBlackboxPath(compliance, '/blackbox/infrastructure')).toBe(false);
    expect(canOpenBlackboxPath(technicalSupport, '/blackbox/infrastructure')).toBe(true);
    expect(canOpenBlackboxPath(technicalSupport, '/blackbox/governance/security')).toBe(false);
  });
});
