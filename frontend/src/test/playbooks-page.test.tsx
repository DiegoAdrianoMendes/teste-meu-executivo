/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PlaybooksPage from '@/klm/pages/PlaybooksPage';
import { playbookFixture } from '@/test/playbooks-fixture';

const api = vi.hoisted(() => ({ create: vi.fn(), list: vi.fn() }));
vi.mock('@/services/playbooks', async (original) => {
  const actual = await original<typeof import('@/services/playbooks')>();
  return { ...actual, createPlaybook: api.create, listPlaybooks: api.list };
});
vi.mock('@/store/app-context', () => ({ useAppStore: () => ({ can: () => true }) }));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><PlaybooksPage /></MemoryRouter></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('PlaybooksPage', () => {
  it('lists real records and creates an isolated draft', async () => {
    api.list.mockResolvedValue([playbookFixture]);
    api.create.mockResolvedValue(playbookFixture);
    renderPage();

    expect(await screen.findByTestId('playbook-library')).toBeInTheDocument();
    expect(await screen.findByText('Triagem, qualificação e contratação para negativas de cobertura.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Novo playbook/i }));
    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'Saúde premium' } });
    fireEvent.click(screen.getByRole('button', { name: 'Criar rascunho' }));

    await waitFor(() => expect(api.create).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Saúde premium',
      autonomyMode: 'ai_triage_human_meeting',
      scope: expect.objectContaining({ areas: ['cobranca_indevida'], channels: ['whatsapp'] }),
    })));
  });

  it('does not replace an API failure with demonstration content', async () => {
    api.list.mockRejectedValue(new Error('backend offline'));
    renderPage();
    expect(await screen.findByText('Não foi possível carregar a biblioteca')).toBeInTheDocument();
    expect(screen.getByText('Nenhum playbook de demonstração foi usado como substituto.')).toBeInTheDocument();
    expect(screen.queryByText(playbookFixture.description)).not.toBeInTheDocument();
  });
});
