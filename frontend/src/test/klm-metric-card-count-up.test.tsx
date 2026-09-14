/** @vitest-environment jsdom */
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { KlmMetricCard } from '@/klm/components/Primitives';

function mockMatchMedia(reduced: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('prefers-reduced-motion') ? reduced : false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

beforeEach(() => {
  vi.stubGlobal(
    'requestAnimationFrame',
    (cb: FrameRequestCallback) => window.setTimeout(() => cb(performance.now()), 16) as unknown as number,
  );
  vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id));
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('KlmMetricCard contagem animada', () => {
  it('anima do valor anterior até o valor final quando reduced-motion está desligado', async () => {
    mockMatchMedia(false);
    const { rerender } = render(<KlmMetricCard label="Leads novos" value="0" delta="hoje" />);
    // Passa da janela de graça pós-montagem para que a próxima troca conte como
    // uma atualização "ao vivo" (e não como o placeholder inicial resolvendo).
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    rerender(<KlmMetricCard label="Leads novos" value="120" delta="hoje" />);

    // Em algum ponto durante a animação o valor exibido não é ainda o final.
    await act(async () => {
      vi.advanceTimersByTime(64);
    });
    const midValue = screen.getByTestId('klm-metric-value').textContent;
    expect(midValue).not.toBe('120');

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByTestId('klm-metric-value').textContent).toBe('120');
  });

  it('exibe o valor final imediatamente quando prefers-reduced-motion está ativo', async () => {
    mockMatchMedia(true);
    const { rerender } = render(<KlmMetricCard label="Leads novos" value="0" delta="hoje" />);
    rerender(<KlmMetricCard label="Leads novos" value="120" delta="hoje" />);

    await act(async () => {
      vi.advanceTimersByTime(0);
    });
    expect(screen.getByTestId('klm-metric-value').textContent).toBe('120');
  });

  it('não anima valores não numéricos (mantém o texto original)', async () => {
    mockMatchMedia(false);
    render(<KlmMetricCard label="Leads novos" value="Indisponível" delta="hoje" />);
    expect(screen.getByTestId('klm-metric-value').textContent).toBe('Indisponível');
  });
});
