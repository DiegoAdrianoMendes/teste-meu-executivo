import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import { klmApi } from '@/klm/api';

afterEach(() => vi.clearAllMocks());

describe('finance KPI API contract', () => {
  it('normalizes Decimal strings before the finance UI formats them', async () => {
    http.apiFetch.mockResolvedValue({
      periodStart: '2026-07-01',
      periodEnd: '2026-07-31',
      asOf: '2026-07-29',
      recebido: '100.50',
      aReceber: '250.00',
      vencido: {
        dias1A15: '10.00',
        dias16A30: '20.00',
        dias31A60: '30.00',
        dias61Mais: '40.00',
        total: '100.00',
      },
      ticketMedio: '50.25',
      inadimplenciaPct: '2.5',
      mrr: '500.00',
      despesas: '75.00',
      resultado: '425.00',
    });

    const result = await klmApi.financeKpis();

    expect(result.inadimplenciaPct).toBe(2.5);
    expect(result.vencido).toEqual({
      dias1A15: 10,
      dias16A30: 20,
      dias31A60: 30,
      dias61Mais: 40,
      total: 100,
    });
    expect(result.resultado).toBe(425);
  });

  it('rejects malformed financial values instead of presenting a false zero', async () => {
    http.apiFetch.mockResolvedValue({
      periodStart: '2026-07-01',
      periodEnd: '2026-07-31',
      asOf: '2026-07-29',
      recebido: 'invalid',
      aReceber: '0',
      vencido: {
        dias1A15: '0',
        dias16A30: '0',
        dias31A60: '0',
        dias61Mais: '0',
        total: '0',
      },
      ticketMedio: '0',
      inadimplenciaPct: '0',
      mrr: '0',
      despesas: '0',
      resultado: '0',
    });

    await expect(klmApi.financeKpis()).rejects.toThrow(
      'Resposta financeira inválida no campo recebido.',
    );
  });
});
