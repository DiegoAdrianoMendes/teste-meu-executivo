import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import PublicTaskShare from '@/pages/PublicTaskShare';
import * as api from '@/services/task-external-shares';

vi.mock('@/services/task-external-shares');

describe('PublicTaskShare', () => {
  it('renderiza somente o payload externo seguro', async () => {
    vi.mocked(api.redeemPublicTaskExternalShare).mockResolvedValue({
      title: 'Prazo de contestação',
      status: 'pending',
      dueAt: null,
      safeContext: 'Comparecer ao fórum.',
      expiresAt: new Date(Date.now() + 86400_000).toISOString(),
    });
    const token = 'x'.repeat(80);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={[`/task-share#token=${token}`]}>
          <Routes><Route path="/task-share" element={<PublicTaskShare />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Prazo de contestação')).toBeInTheDocument();
    expect(screen.getByText('Comparecer ao fórum.')).toBeInTheDocument();
    expect(screen.getByText(/Comentários, histórico, participantes e anexos internos não são compartilhados/)).toBeInTheDocument();
    expect(screen.queryByText(/UUID|download|WhatsApp/i)).not.toBeInTheDocument();
  });
});
