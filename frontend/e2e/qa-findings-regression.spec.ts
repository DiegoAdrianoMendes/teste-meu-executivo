import { authenticateWorkspacePage, expect, test, type Page } from './fixtures';

const safeRoutes = [
  '/app/agenda',
  '/app/tarefas',
  '/app/funil',
  '/app/conhecimento',
  '/app/areas',
  '/app/rastreamento',
  '/app/aquisicao-paga',
  '/app/beneficios',
  '/app/migracao',
  '/app/executivos',
  '/app/agentes',
  '/app/integracoes',
  '/app/ti',
  '/app/kpis',
  '/app/financeiro',
] as const;

async function visibleUnnamedControls(page: Page) {
  return page.locator('main button, main a[href], main input:not([type="hidden"]), main textarea, main select, main [role="button"], main [role="switch"], main [role="combobox"], main [role="tab"], main [role="radio"], main [role="checkbox"]').evaluateAll((elements) => {
    const isVisible = (element: Element) => {
      const node = element as HTMLElement;
      const style = window.getComputedStyle(node);
      return style.visibility !== 'hidden' && style.display !== 'none' && node.getClientRects().length > 0;
    };
    const accessibleName = (element: Element) => {
      const labelledBy = element.getAttribute('aria-labelledby')
        ?.split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent ?? '')
        .join(' ');
      const explicit = element.id
        ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)?.textContent
        : '';
      return [
        element.getAttribute('aria-label'),
        labelledBy,
        explicit,
        element.closest('label')?.textContent,
        element.getAttribute('title'),
        element.getAttribute('placeholder'),
        element.textContent,
        element.getAttribute('alt'),
      ].find((value) => value?.trim())?.trim() ?? '';
    };
    return elements
      .filter(isVisible)
      .filter((element) => !accessibleName(element))
      .map((element) => element.outerHTML.slice(0, 240));
  });
}

test.beforeEach(async ({ page, request }) => {
  await authenticateWorkspacePage(page, request);
});

for (const route of safeRoutes) {
  test(`${route} preserva landmark, titulo e nomes acessiveis`, async ({ page }) => {
    await page.goto(route, { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL((url) => url.pathname === route);
    await expect(page.getByRole('main')).toHaveCount(1);
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await expect.poll(() => visibleUnnamedControls(page), {
      message: `Controles visiveis sem nome acessivel em ${route}`,
    }).toEqual([]);
  });
}

test('/app/tarefas comunica o modo selecionado e usa estados em portugues', async ({ page }) => {
  await page.goto('/app/tarefas', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Lista' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: 'Quadro' })).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByText('Done', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Blocked', { exact: true })).toHaveCount(0);
});
