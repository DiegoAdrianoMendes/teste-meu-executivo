import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

describe('registro canônico da Central Blackbox', () => {
  it('não mantém alias visual ou redirecionamento pela rota /platform', () => {
    const appSource = fs.readFileSync(
      path.resolve(process.cwd(), 'src', 'BlackboxApp.tsx'),
      'utf8',
    );
    const runtimeSource = fs.readFileSync(
      path.resolve(process.cwd(), 'src', 'runtime-surface.ts'),
      'utf8',
    );
    const dashboardSource = fs.readFileSync(
      path.resolve(process.cwd(), 'src', 'pages', 'PlatformDashboard.tsx'),
      'utf8',
    );

    expect(appSource).not.toContain('LegacyPlatformRedirect');
    expect(appSource).not.toMatch(/<Route path="\/platform/);
    expect(runtimeSource).not.toMatch(/pathname(?:\.startsWith)?\(['"`]\/platform/);
    expect(dashboardSource).not.toMatch(/['"`]\/platform(?:\/|['"`])/);
    expect(dashboardSource).not.toMatch(/navigate\(\s*['"`]\/app['"`]/);
    expect(dashboardSource).toContain(
      "navigate('/blackbox/support-session', { replace: true })",
    );
    expect(dashboardSource).toContain("announcements: '/blackbox/announcements'");
    expect(dashboardSource).toContain("errors: '/blackbox/errors'");
    expect(dashboardSource).not.toContain("canPlatform(state.currentUser");
  });

  it('usa shell próprio sem alterar o namespace interno da API', () => {
    const appSource = fs.readFileSync(
      path.resolve(process.cwd(), 'src', 'BlackboxApp.tsx'),
      'utf8',
    );
    const supportSource = fs.readFileSync(
      path.resolve(
        process.cwd(),
        'src',
        'pages',
        'PlatformSupportTicketsPanel.tsx',
      ),
      'utf8',
    );

    expect(appSource).toContain('path="/blackbox"');
    expect(appSource).toContain('<BlackboxShell />');
    expect(appSource).not.toContain('<AppShell module="platform" />');
    expect(supportSource).not.toMatch(/navigate\(\s*['"`]\/platform/);
    expect(supportSource).toContain('/api/v1/platform/support/tickets');
  });

  it('expõe runtime de agentes e decisões humanas dentro do Blackbox', () => {
    const appSource = fs.readFileSync(path.resolve(process.cwd(), 'src', 'BlackboxApp.tsx'), 'utf8');
    const accessSource = fs.readFileSync(path.resolve(process.cwd(), 'src', 'blackbox', 'access.ts'), 'utf8');
    const pageSource = fs.readFileSync(path.resolve(process.cwd(), 'src', 'pages', 'BlackboxAgentOperations.tsx'), 'utf8');
    const tasksSource = fs.readFileSync(path.resolve(process.cwd(), 'src', 'pages', 'BlackboxTasks.tsx'), 'utf8');
    expect(appSource).toContain('path="agent-operations"');
    expect(accessSource).toContain("/blackbox/agent-operations");
    expect(pageSource).toContain('AgentPlatformOperationsPanel');
    expect(pageSource).toContain('StudioWorkspaceCombobox');
    expect(pageSource).toContain('const workspaceId = isRoot ? workspace?.id ?? null : approvedSupportSession?.workspaceId ?? null');
    expect(pageSource).toContain('workspaceId={workspaceId}');
    expect(pageSource).toContain("session.sessionType === 'support_approved'");
    expect(pageSource).toContain("session.scopes.includes('configurations:read')");
    expect(pageSource).toContain('Abrir Conta 360');
    expect(pageSource).toContain('Edição protegida de flows');
    expect(pageSource).toContain('inicie acesso direto root');
    expect(pageSource).not.toContain('/app/flows');
    expect(pageSource).not.toContain('/app/agents');
    expect(tasksSource).toContain("id: 'agent-decisions'");
    expect(tasksSource).toContain('AgentPlatformOperationsPanel');
    expect(tasksSource).toContain('PlatformEntityCombobox');
    expect(tasksSource).toContain('workspaceId={agentWorkspaceId}');
  });

  it('gera app e Blackbox como runtimes explícitos e independentes', () => {
    const packageJson = JSON.parse(
      fs.readFileSync(path.resolve(process.cwd(), 'package.json'), 'utf8'),
    ) as { scripts: Record<string, string> };
    const mainSource = fs.readFileSync(
      path.resolve(process.cwd(), 'src', 'main.tsx'),
      'utf8',
    );
    const authSource = fs.readFileSync(
      path.resolve(process.cwd(), 'src', 'services', 'auth.ts'),
      'utf8',
    );
    const viteSource = fs.readFileSync(
      path.resolve(process.cwd(), 'vite.config.ts'),
      'utf8',
    );
    const workspaceSource = fs.readFileSync(
      path.resolve(process.cwd(), 'src', 'App.tsx'),
      'utf8',
    );
    const blackboxSource = fs.readFileSync(
      path.resolve(process.cwd(), 'src', 'BlackboxApp.tsx'),
      'utf8',
    );

    expect(packageJson.scripts['dev:app']).toContain(
      'VITE_APP_SURFACE=workspace',
    );
    expect(packageJson.scripts['dev:blackbox']).toContain(
      'VITE_APP_SURFACE=platform',
    );
    expect(packageJson.scripts['dev:app']).toContain('--port 4173');
    expect(packageJson.scripts['dev:blackbox']).toContain('--port 4174');
    expect(mainSource).toContain('await recoverAccessToken()');
    expect(mainSource).toContain('await import("@surface-app")');
    expect(viteSource).toContain('process.env.VITE_APP_SURFACE === "platform"');
    expect(workspaceSource).not.toContain('PlatformDashboard');
    expect(blackboxSource).not.toContain('KlmShell');
    expect(authSource).toContain("'X-Auth-Audience': APP_SURFACE");
  });

  it('reconhece o tipo do bundle mesmo com caminho absoluto ou barra final', () => {
    const verifierSource = fs.readFileSync(
      path.resolve(process.cwd(), 'scripts', 'verify-production-bundle.mjs'),
      'utf8',
    );

    expect(verifierSource).toContain("const distName = basename(distRoot)");
    expect(verifierSource).toContain("distName === 'dist-app'");
    expect(verifierSource).toContain("distName === 'dist-blackbox'");
    expect(verifierSource).not.toContain("distDirectory.endsWith('dist-app')");
  });
});
