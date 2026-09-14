import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

function readSource(relativePath: string) {
  return fs.readFileSync(path.resolve(process.cwd(), 'src', relativePath), 'utf8');
}

describe('KLM navigation contract', () => {
  it('maps every primary workspace shortcut to its native SPA route', () => {
    const source = readSource('components/layout/AppSidebar.tsx');

    expect(source).toContain("title: 'Dashboard', moduleId: 'dashboard', icon: LayoutDashboard, route: '/app'");
    expect(source).toContain("title: 'Kanban', moduleId: 'kanban', icon: Columns3, route: '/app/funil'");
    expect(source).toContain("title: 'Lista', moduleId: 'lista', icon: List, route: '/app/leads'");
    expect(source).toContain("title: 'Relatórios', moduleId: 'relatorios', icon: BarChart3, route: '/app/kpis'");
    expect(source).toContain("route: '/app/aprovacoes'");
    expect(source).toContain("route: '/app/configuracoes'");
    expect(source).toContain("routePath === '/app'");
  });

  it('opens the native settings and integrations surfaces from onboarding', () => {
    const source = readSource('pages/Onboarding.tsx');

    expect(source).toContain("navigate('/app/configuracoes')");
    expect(source).toContain("navigate('/app/configuracoes/avancadas?tab=integracoes')");
    expect(source).not.toContain("setActiveModule('configuracoes')");
  });

  it('converges every integration shortcut on the canonical native blade', () => {
    const acquisitionSource = readSource('klm/components/AcquisitionIntegrationsPanel.tsx');
    const commentsSource = readSource('klm/pages/SocialCommentsPage.tsx');
    const toolsSource = readSource('klm/pages/ToolsPage.tsx');
    const onboardingSource = readSource('pages/Onboarding.tsx');
    const combinedSource = [acquisitionSource, commentsSource, toolsSource, onboardingSource].join('\n');

    expect(acquisitionSource).toContain('/app/integracoes?tab=assistente&provider=${provider}');
    expect(commentsSource).toContain("navigate('/app/configuracoes/avancadas?tab=integracoes')");
    expect(commentsSource).toContain('/app/integracoes?tab=assistente&provider=${connectionProvider');
    expect(toolsSource).toContain("path: '/app/integracoes'");
    expect(onboardingSource).toContain("navigate('/app/configuracoes/avancadas?tab=integracoes')");
    expect(combinedSource).not.toContain("navigate('/app/configuracoes?tab=integracoes')");
  });

  it('routes contract and petition case rows into the exact native card context', () => {
    const source = readSource('klm/pages/OperationalModulePage.tsx');

    expect(source).toContain("['contratos', 'peticoes'].includes(slug) && row.id");
    expect(source).toContain("navigate(`/app/${slug}?matter=${encodeURIComponent(row.id)}`)");
  });

  it('keeps personal preferences available without an administrative permission gate', () => {
    const appSource = readSource('App.tsx');
    const topbarSource = readSource('components/layout/Topbar.tsx');

    expect(appSource).toContain('<Route path="preferencias" element={<KlmPreferencesPage />} />');
    expect(topbarSource).toContain("navigate('/app/preferencias')");
    expect(topbarSource).toContain('Minhas preferências');
  });

  it('registers only the canonical native-domain capability route', () => {
    const appSource = readSource('App.tsx');

    expect(appSource).toContain('path="capacidades/:domain/:capability"');
    expect(appSource).not.toContain('path="recursos/:provider/:capability"');
  });

  it('exposes Meta templates inside the KLM shell and preserves legacy deep links', () => {
    const appSource = readSource('App.tsx');
    const navigationSource = readSource('klm/navigation.ts');

    expect(appSource).toContain('path="templates-meta"');
    expect(appSource).toContain("pathname: '/app/templates-meta'");
    expect(navigationSource).toContain("path: '/app/templates-meta'");
    expect(navigationSource).toContain("'/app/templates-meta'");
  });

  it('keeps template creation/copy and sequence editing on native workspace routes', () => {
    const conversationsSource = readSource('klm/pages/ConversationsPage.tsx');
    const appSource = readSource('App.tsx');

    expect(conversationsSource).toContain("navigate(`/app/templates-meta?");
    expect(conversationsSource).toContain("params.set('copy', templateId)");
    expect(conversationsSource).toContain("params.set('create', '1')");
    expect(conversationsSource).toContain("navigate(`/app/flows?");
    expect(appSource).toContain('function KlmFlowsRoute()');
    expect(appSource).toContain("searchParams.get('sequence_draft')");
  });
});
