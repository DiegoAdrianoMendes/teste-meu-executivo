/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import KnowledgePage from '@/klm/pages/KnowledgePage';
import { knowledgeArticles, sortArticlesByRole } from '@/content/knowledge';
import { klmApi } from '@/klm/api';

const appState = vi.hoisted(() => ({ current: { activeRole: null as string | null, workspaceId: 'workspace-1' } }));

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: () => true,
    state: {
      session: { activeRole: appState.current.activeRole },
      currentUser: { role: null },
      activeWorkspace: { id: appState.current.workspaceId },
    },
  }),
}));

vi.mock('@/klm/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/klm/api')>();
  return {
    ...original,
    klmApi: {
      ...original.klmApi,
      submitKnowledgeArticleFeedback: vi.fn(),
    },
  };
});

function renderPage(initialEntries: string[] = ['/app/conhecimento']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <KnowledgePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  appState.current = { activeRole: null, workspaceId: 'workspace-1' };
  vi.mocked(klmApi.submitKnowledgeArticleFeedback).mockReset();
  vi.mocked(klmApi.submitKnowledgeArticleFeedback).mockResolvedValue({
    articleId: 'conectar-whatsapp-manual',
    upCount: 1,
    downCount: 0,
    myFeedback: 'up',
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('KnowledgePage', () => {
  it('lists all 6 published articles grouped by category', () => {
    renderPage();
    for (const article of knowledgeArticles) {
      expect(screen.getByText(article.titulo)).toBeInTheDocument();
    }
  });

  it('filters the list with client-side search over title and body', () => {
    renderPage();
    const search = screen.getByLabelText('Buscar artigos');
    fireEvent.change(search, { target: { value: '133005' } });

    expect(screen.getByText('Erros comuns ao conectar o WhatsApp')).toBeInTheDocument();
    expect(screen.queryByText('Ligar o rastreamento de anúncios (CTWA) e ler o ROI')).not.toBeInTheDocument();
  });

  it('shows an empty state when the search matches nothing', () => {
    renderPage();
    const search = screen.getByLabelText('Buscar artigos');
    fireEvent.change(search, { target: { value: 'assunto-inexistente-xyz' } });

    expect(screen.getByText('Nenhum artigo encontrado')).toBeInTheDocument();
  });

  it('opens an article and follows an internal [[rota:]] link to another article', () => {
    renderPage();
    fireEvent.click(screen.getByText('Conectar o WhatsApp do escritório (modo manual)'));

    expect(screen.getByRole('heading', { name: 'Conectar o WhatsApp do escritório (modo manual)' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: 'Erros comuns de conexão' }));

    expect(screen.getByRole('heading', { name: 'Erros comuns ao conectar o WhatsApp' })).toBeInTheDocument();
  });

  it('sends thumbs up/down feedback to the backend instead of localStorage', async () => {
    renderPage();
    fireEvent.click(screen.getByText('Conectar o WhatsApp do escritório (modo manual)'));

    fireEvent.click(screen.getByRole('button', { name: /Sim/ }));

    // Otimista: aparece de imediato, antes da resposta do backend chegar.
    expect(screen.getByText(/Obrigado pelo retorno, ele fica registrado no workspace/)).toBeInTheDocument();
    await waitFor(() =>
      expect(klmApi.submitKnowledgeArticleFeedback).toHaveBeenCalledWith('conectar-whatsapp-manual', 'up'),
    );
    expect(window.localStorage.getItem('klm_knowledge_feedback:workspace-1')).toBeNull();
  });

  it('keeps the optimistic vote when the feedback API call fails', async () => {
    vi.mocked(klmApi.submitKnowledgeArticleFeedback).mockRejectedValueOnce(new Error('network down'));
    renderPage();
    fireEvent.click(screen.getByText('Conectar o WhatsApp do escritório (modo manual)'));

    const downButton = screen.getByRole('button', { name: /Não/ });
    fireEvent.click(downButton);

    await waitFor(() =>
      expect(klmApi.submitKnowledgeArticleFeedback).toHaveBeenCalledWith('conectar-whatsapp-manual', 'down'),
    );
    expect(screen.getByText(/Obrigado pelo retorno, ele fica registrado no workspace/)).toBeInTheDocument();
  });

  it('orders articles for the active role without hiding any of them', () => {
    const ordered = sortArticlesByRole(knowledgeArticles, 'workspace_owner');
    expect(ordered).toHaveLength(knowledgeArticles.length);
    expect(ordered.map((a) => a.slug).sort()).toEqual(knowledgeArticles.map((a) => a.slug).sort());

    appState.current.activeRole = 'workspace_owner';
    renderPage();
    expect(screen.getByText(/Mostrando primeiro os artigos mais úteis/)).toBeInTheDocument();
  });
});
