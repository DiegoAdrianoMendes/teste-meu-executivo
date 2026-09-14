/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';

import { buildExecutiveReportHtml, openExecutiveReport } from '@/klm/components/executive-report';
import type { Brand } from '@/types/frontend-contracts';

function brandFixture(): Brand {
  return {
    id: 'brand-1',
    workspaceId: 'workspace-1',
    version: 1,
    name: 'Escritório Barbalho & Associados',
    shortName: 'Barbalho',
    tagline: 'Excelência jurídica',
    legalName: 'Barbalho Advocacia LTDA',
    domain: 'barbalho.adv.br',
    defaultLocale: 'pt-BR',
    assets: {
      logoLightUrl: 'https://cdn.example.test/logo-light.svg',
      logoDarkUrl: 'https://cdn.example.test/logo-dark.svg',
      markUrl: 'https://cdn.example.test/mark.svg',
      faviconUrl: 'https://cdn.example.test/favicon.svg',
    },
    colors: {
      primary: '#7a1e2b',
      accent: '#c9a227',
      foreground: '#1a1a1a',
      background: '#ffffff',
      sidebar: '#0f0f0f',
      success: '#0b7435',
      warning: '#b98900',
      danger: '#a53e3c',
    },
    cssVariables: {},
    typography: { sansFamily: 'Inter', legalFamily: 'EB Garamond' },
    metadata: {
      defaultTitle: 'Barbalho Advocacia',
      titleTemplate: '%s · Barbalho',
      description: 'Escritório de advocacia',
      applicationName: 'Barbalho',
      author: 'Barbalho Advocacia',
      themeColor: '#7a1e2b',
    },
    contact: { email: 'contato@barbalho.adv.br', supportEmail: 'suporte@barbalho.adv.br', websiteUrl: 'https://barbalho.adv.br' },
    legal: { registeredName: 'Barbalho Advocacia LTDA' },
    socialProof: [],
    updatedAt: '2026-07-23T12:00:00Z',
    updatedBy: 'system',
  };
}

describe('buildExecutiveReportHtml', () => {
  it('inclui a marca do workspace, o período e os KPIs no documento gerado', () => {
    const html = buildExecutiveReportHtml({
      brand: brandFixture(),
      periodLabel: '30 dias',
      generatedAt: new Date('2026-07-23T15:30:00Z'),
      metrics: [{ label: 'Leads novos', value: '42' }, { label: 'Receita prevista', value: 'R$ 12.000,00' }],
      funnel: [{ label: 'Qualificação', value: 8, progress: 80 }],
      channels: [{ label: 'Google Ads', leads: 5, cac: 'R$ 120,00', roas: '3,20x' }],
      revenue: { paid: 'R$ 5.000,00', overdue: 'R$ 200,00', scheduled: 'R$ 1.000,00', receivables: 'R$ 3.000,00' },
      revenueTrend: [10, 20, 15, 30],
    });

    expect(html).toContain('Escritório Barbalho &amp; Associados');
    expect(html).toContain('30 dias');
    expect(html).toContain('Leads novos');
    expect(html).toContain('42');
    expect(html).toContain('Google Ads');
    expect(html).toContain('#7a1e2b');
    expect(html).toContain('Gerado pelo Escritório Barbalho &amp; Associados em');
    expect(html).toContain('<svg');
    expect(html).toContain('@media print');
    expect(html).toContain('logo-light.svg');
  });

  it('cai para uma marca padrão quando o workspace ainda não tem branding carregado', () => {
    const html = buildExecutiveReportHtml({
      brand: null,
      periodLabel: 'Hoje',
      generatedAt: new Date('2026-07-23T15:30:00Z'),
      metrics: [],
      funnel: [],
      channels: [],
      revenue: { paid: '—', overdue: '—', scheduled: '—', receivables: '—' },
      revenueTrend: [],
    });

    expect(html).toContain('Meu Executivo');
    expect(html).toContain('Nenhum caso no funil neste período.');
    expect(html).toContain('Nenhum canal com eventos neste período.');
  });

  it('escapa conteúdo potencialmente perigoso vindo da marca', () => {
    const brand = brandFixture();
    brand.name = '<img src=x onerror=alert(1)>';
    const html = buildExecutiveReportHtml({
      brand,
      periodLabel: 'Hoje',
      generatedAt: new Date('2026-07-23T15:30:00Z'),
      metrics: [],
      funnel: [],
      channels: [],
      revenue: { paid: '—', overdue: '—', scheduled: '—', receivables: '—' },
      revenueTrend: [],
    });
    expect(html).not.toContain('<img src=x onerror=alert(1)>');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
  });
});

describe('openExecutiveReport', () => {
  it('abre uma nova janela, escreve o HTML e aciona window.print()', () => {
    const printSpy = { calls: 0 };
    const fakeWindow = {
      document: {
        open: () => {},
        write: (_html: string) => {},
        close: () => {},
      },
      focus: () => {},
      print: () => { printSpy.calls += 1; },
    } as unknown as Window;

    const originalOpen = window.open;
    window.open = (() => fakeWindow) as typeof window.open;
    try {
      const result = openExecutiveReport('<html><body>relatório</body></html>');
      expect(result).toBe(fakeWindow);
      expect(printSpy.calls).toBe(1);
    } finally {
      window.open = originalOpen;
    }
  });

  it('retorna null sem lançar quando o navegador bloqueia a nova aba', () => {
    const originalOpen = window.open;
    window.open = (() => null) as typeof window.open;
    try {
      expect(openExecutiveReport('<html></html>')).toBeNull();
    } finally {
      window.open = originalOpen;
    }
  });
});
