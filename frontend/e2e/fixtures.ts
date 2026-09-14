import {
  expect,
  test as base,
  type APIRequestContext,
  type Locator,
  type Page,
} from '@playwright/test';

const API_URL = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
const AUTH_OVERRIDE_KEY = 'klm_e2e_auth_override';

type BrowserSession = {
  accessToken?: string;
  access_token?: string;
  user: unknown;
  session: unknown;
};

export async function sessionForRole(
  request: APIRequestContext,
  rolePackage: string,
): Promise<BrowserSession & { token: string }> {
  const sessionPath = rolePackage === 'platform_owner'
    ? '/api/v1/klm/local/platform-session'
    : `/api/v1/klm/local/platform-session/${encodeURIComponent(rolePackage)}`;
  const response = await request.post(
    `${API_URL}${sessionPath}`,
  );
  const responseBody = await response.text();
  expect(response.status(), `sessão ${rolePackage} indisponível: ${responseBody.slice(0, 500)}`).toBe(200);
  const session = JSON.parse(responseBody) as BrowserSession;
  const token = session.accessToken ?? session.access_token;
  expect(token).toBeTruthy();
  return { ...session, token: token as string };
}

export async function authenticatePageForRole(
  page: Page,
  request: APIRequestContext,
  rolePackage: string,
): Promise<string> {
  // Production-bundle responsive suites authenticate the same root on every
  // viewport. Reusing that canonical bootstrap avoids repeatedly mutating the
  // disposable workspace and exhausting/locking the local session factory.
  // Role journeys still receive a fresh identity because they intentionally
  // mutate permissions and sessions.
  const session = process.env.KLM_E2E_PRODUCTION === '1' && rolePackage === 'platform_owner'
    ? await loadProductionSession(request, 'platform').then((value) => ({
        ...value,
        token: (value.accessToken ?? value.access_token) as string,
      }))
    : await sessionForRole(request, rolePackage);
  await page.addInitScript(({ tokenValue, user, sessionContext }) => {
    sessionStorage.setItem('klm_token', tokenValue);
    sessionStorage.setItem('klm_user', JSON.stringify(user));
    sessionStorage.setItem('klm_session', JSON.stringify(sessionContext));
  }, { tokenValue: session.token, user: session.user, sessionContext: session.session });
  return session.token;
}

/**
 * Authenticate the tenant `/app` surface in local/staging E2E without
 * changing the global fixture.  Keeping this explicit prevents tests that
 * intentionally exercise the unauthenticated shell from being silently
 * converted into authenticated journeys.
 */
export async function authenticateWorkspacePage(
  page: Page,
  request: APIRequestContext,
): Promise<string> {
  const session = process.env.KLM_E2E_PRODUCTION === '1'
    ? await loadProductionSession(request, 'workspace')
    : await (async () => {
        let lastStatus = 0;
        let lastBody = '';
        for (let attempt = 1; attempt <= 3; attempt += 1) {
          const response = await request.post(`${API_URL}/api/v1/klm/local/session`);
          lastStatus = response.status();
          lastBody = await response.text();
          if (lastStatus === 200) return JSON.parse(lastBody) as BrowserSession;
          if (![500, 503].includes(lastStatus) || attempt === 3) break;
          await new Promise((resolve) => setTimeout(resolve, attempt * 150));
        }
        expect(
          lastStatus,
          `sessão workspace indisponível após bootstrap local: ${lastBody.slice(0, 500)}`,
        ).toBe(200);
        throw new Error('Sessão local não pôde ser inicializada.');
      })();
  const token = session.accessToken ?? session.access_token;
  expect(token).toBeTruthy();
  await page.addInitScript(({ tokenValue, user, sessionContext }) => {
    sessionStorage.setItem('klm_token', tokenValue);
    sessionStorage.setItem('klm_user', JSON.stringify(user));
    sessionStorage.setItem('klm_session', JSON.stringify(sessionContext));
  }, { tokenValue: token as string, user: session.user, sessionContext: session.session });
  return token as string;
}

export async function ensurePublishedAgentDeployment(
  request: APIRequestContext,
  token: string,
  agentKey: string,
): Promise<string> {
  const headers = { Authorization: `Bearer ${token}` };
  type Deployment = {
    id: string;
    agentKey?: string;
    agent_key?: string;
    status: string;
    version: number;
  };
  type DeploymentPage = {
    items: Deployment[];
    total: number;
    limit: number;
    offset: number;
    hasMore: boolean;
  };
  const deployments: Deployment[] = [];
  let offset = 0;
  let hasMore = true;
  while (hasMore) {
    const deploymentsResponse = await request.get(
      `${API_URL}/api/v1/agent-platform/deployments?limit=100&offset=${offset}`,
      { headers },
    );
    expect(deploymentsResponse.status(), await deploymentsResponse.text()).toBe(200);
    const deploymentPage = await deploymentsResponse.json() as DeploymentPage;
    deployments.push(...deploymentPage.items);
    if (deploymentPage.hasMore && deploymentPage.items.length === 0) {
      throw new Error('Página de deployments informou próximos resultados sem itens; interrompida para evitar loop.');
    }
    offset += deploymentPage.items.length;
    hasMore = deploymentPage.hasMore;
  }
  const active = deployments.find((item) => (item.agentKey ?? item.agent_key) === agentKey && item.status === 'active');
  if (active) return active.id;

  const catalogResponse = await request.get(`${API_URL}/api/v1/agent-platform/catalog`, { headers });
  expect(catalogResponse.status(), await catalogResponse.text()).toBe(200);
  const catalog = await catalogResponse.json() as {
    agents: Array<{ key: string; version?: number; tools?: string[]; allowedModels?: string[] }>;
  };
  const definition = catalog.agents.find((item) => item.key === agentKey);
  expect(definition, `agente ${agentKey} ausente do catálogo governado`).toBeTruthy();
  const previousVersions = deployments
    .filter((item) => (item.agentKey ?? item.agent_key) === agentKey)
    .map((item) => Number(item.version) || 0);
  const version = Math.max(definition?.version ?? 1, ...previousVersions.map((item) => item + 1));
  const model = definition?.allowedModels?.[0];
  const tools = definition?.tools ?? [];
  expect(model, `agente ${agentKey} sem modelo autorizado`).toBeTruthy();
  expect(tools.length, `agente ${agentKey} sem ferramentas autorizadas`).toBeGreaterThan(0);

  const createResponse = await request.post(`${API_URL}/api/v1/agent-platform/deployments`, {
    headers,
    data: {
      agent_key: agentKey,
      role_package: 'office_owner',
      version,
      config: {
        definitionVersion: definition?.version ?? 1,
        rolePackage: 'office_owner',
        model,
        tools,
        knowledgeSources: ['matter_context'],
        autonomyPolicy: 'A1',
        humanRoutingPolicy: 'role_skill_hierarchy_v1',
        budgetPolicy: { monthlyTokenLimit: 100000 },
      },
      gates: {},
    },
  });
  expect(createResponse.status(), await createResponse.text()).toBe(200);
  const created = await createResponse.json() as { id: string };
  const validateResponse = await request.post(
    `${API_URL}/api/v1/agent-platform/deployments/${created.id}/validate`,
    { headers },
  );
  expect(validateResponse.status(), await validateResponse.text()).toBe(200);
  const publishResponse = await request.post(
    `${API_URL}/api/v1/agent-platform/deployments/${created.id}/publish`,
    { headers },
  );
  expect(publishResponse.status(), await publishResponse.text()).toBe(200);
  return created.id;
}

let workspaceSessionPromise: Promise<BrowserSession> | null = null;
let platformSessionPromise: Promise<BrowserSession> | null = null;

async function loadProductionSession(
  request: APIRequestContext,
  surface: 'workspace' | 'platform',
): Promise<BrowserSession> {
  const existing = surface === 'platform' ? platformSessionPromise : workspaceSessionPromise;
  if (existing) return existing;
  const sessionPath = surface === 'platform'
    ? '/api/v1/klm/local/platform-session'
    : '/api/v1/klm/local/session';
  const created = (async () => {
    let lastStatus = 0;
    let lastBody = '';
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      const response = await request.post(`${API_URL}${sessionPath}`);
      lastStatus = response.status();
      lastBody = await response.text();
      if (lastStatus === 200) return JSON.parse(lastBody) as BrowserSession;
      // Only the disposable local bootstrap can contend while it seeds the
      // canonical SQLite workspace. Permanent/auth failures remain immediate.
      if (![500, 503].includes(lastStatus) || attempt === 3) break;
      await new Promise((resolve) => setTimeout(resolve, attempt * 150));
    }
    throw new Error(
      `sessao ${surface} indisponivel apos bootstrap local: HTTP ${lastStatus} ${lastBody.slice(0, 500)}`,
    );
  })();
  if (surface === 'platform') platformSessionPromise = created;
  else workspaceSessionPromise = created;
  try {
    return await created;
  } catch (error) {
    // A transient failed bootstrap must not poison every remaining test in
    // this worker. The next test receives one independent bootstrap attempt.
    if (surface === 'platform') platformSessionPromise = null;
    else workspaceSessionPromise = null;
    throw error;
  }
}

const test = base.extend({
  page: async ({ page, request }, provide) => {
    if (process.env.KLM_E2E_PRODUCTION === '1') {
      // The local bootstrap endpoints seed the same canonical workspace. Keep
      // their first invocation ordered so the workspace transaction commits
      // before the platform identities are materialized. Subsequent calls are
      // cached independently and do not add latency to the suite.
      const workspaceSession = await loadProductionSession(request, 'workspace');
      const platformSession = await loadProductionSession(request, 'platform');
      const workspaceToken = workspaceSession.accessToken ?? workspaceSession.access_token;
      const platformToken = platformSession.accessToken ?? platformSession.access_token;
      expect(workspaceToken).toBeTruthy();
      expect(platformToken).toBeTruthy();
      const blackboxOrigin = new URL(
        process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174',
      ).origin;
      await page.addInitScript(({ authOverrideKey, workspace, platform, internalOrigin }) => {
        try {
          if (localStorage.getItem(authOverrideKey) === '1') return;
          const selected = location.origin === internalOrigin || location.pathname.startsWith('/blackbox')
            ? platform
            : workspace;
          sessionStorage.setItem('klm_token', selected.token);
          sessionStorage.setItem('klm_user', JSON.stringify(selected.user));
          sessionStorage.setItem('klm_session', JSON.stringify(selected.session));
          localStorage.removeItem('klm_user');
          localStorage.removeItem('klm_session');
        } catch {
          // The script also runs in the browser's initial opaque document.
        }
      }, {
        authOverrideKey: AUTH_OVERRIDE_KEY,
        workspace: {
          token: workspaceToken as string,
          user: workspaceSession.user,
          session: workspaceSession.session,
        },
        platform: {
          token: platformToken as string,
          user: platformSession.user,
          session: platformSession.session,
        },
        internalOrigin: blackboxOrigin,
      });
    }
    await provide(page);
  },
});

export { expect, test };
export type { APIRequestContext, Locator, Page };
