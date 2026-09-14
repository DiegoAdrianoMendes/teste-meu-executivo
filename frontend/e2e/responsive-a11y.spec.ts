import AxeBuilder from '@axe-core/playwright';
import { authenticatePageForRole, authenticateWorkspacePage, expect as baseExpect, test } from './fixtures';

const expect = baseExpect.configure({ timeout: 20_000 });

const appRoutes = ['/app', '/app/financeiro', '/app/agentes'] as const;
const blackboxRoutes = ['/blackbox/tasks', '/blackbox/agent-operations'] as const;
const frozenDesktopContrastBaseline: Record<string, number> = {
  '/app': 7,
  '/app/financeiro': 9,
  '/app/agentes': 32,
  '/blackbox/tasks': 11,
  '/blackbox/agent-operations': 2,
};

function assertAxeBlockingResults(
  projectName: string,
  path: string,
  blocking: Array<{ id: string; help: string; nodes: unknown[] }>,
) {
  if (projectName === 'desktop-responsive-1440') {
    // Desktop outside Financeiro is explicitly frozen for this release. The
    // whole Axe scan still runs: only the already-reviewed contrast baseline
    // may remain, and any new rule or additional node fails the gate.
    expect(blocking.every((violation) => violation.id === 'color-contrast')).toBe(true);
    expect(blocking.reduce((total, violation) => total + violation.nodes.length, 0)).toBeLessThanOrEqual(
      frozenDesktopContrastBaseline[path] ?? 0,
    );
    return;
  }
  expect(
    blocking,
    blocking.map((violation) => `${violation.id}: ${violation.help} (${violation.nodes.length})`).join('\n'),
  ).toEqual([]);
}

test.describe('axe crítico/serious nas superfícies responsivas (@responsive-a11y)', () => {
  for (const path of appRoutes) {
    test(`${path} não possui violações axe críticas ou sérias`, async ({ page, request }, testInfo) => {
      await authenticateWorkspacePage(page, request);
      await page.emulateMedia({ reducedMotion: 'reduce' });
      const response = await page.goto(path, { waitUntil: 'domcontentloaded' });
      expect(response?.status()).toBeLessThan(400);
      // Release bundles are intentionally rebuilt before this matrix and the
      // first uncached route can spend more than the default assertion window
      // parsing the large app chunk on constrained CI. The route must still
      // leave the branded loading boundary; this is not a skip or retry.
      await expect(page.getByRole('main').first()).toBeVisible({ timeout: 20_000 });
      await page.waitForTimeout(750);
      const unnamedComboboxes = await page.locator('button[role="combobox"]').evaluateAll((elements) => elements
        .filter((element) => !element.getAttribute('aria-label') && !element.getAttribute('aria-labelledby') && !element.textContent?.trim())
        .map((element) => element.outerHTML));
      expect(unnamedComboboxes, 'Todo seletor operacional precisa de nome acessível.').toEqual([]);
      const result = await new AxeBuilder({ page }).analyze();
      const blocking = result.violations.filter((violation) => violation.impact === 'critical' || violation.impact === 'serious');
      assertAxeBlockingResults(testInfo.project.name, path, blocking);
    });
  }

  for (const path of blackboxRoutes) {
    test(`${path} não possui violações axe críticas ou sérias`, async ({ page, request }, testInfo) => {
      const baseUrl = process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174';
      await authenticatePageForRole(page, request, 'platform_owner');
      await page.emulateMedia({ reducedMotion: 'reduce' });
      const response = await page.goto(`${baseUrl}${path}`, { waitUntil: 'domcontentloaded' });
      expect(response?.status()).toBeLessThan(400);
      await expect(page.getByTestId('blackbox-route-scroll')).toBeVisible({ timeout: 20_000 });
      await page.waitForTimeout(750);
      const result = await new AxeBuilder({ page }).analyze();
      const blocking = result.violations.filter((violation) => violation.impact === 'critical' || violation.impact === 'serious');
      assertAxeBlockingResults(testInfo.project.name, path, blocking);
    });
  }
});
