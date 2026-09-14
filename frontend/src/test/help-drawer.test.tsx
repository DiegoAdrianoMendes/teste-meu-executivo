/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { HelpDrawer } from '@/klm/components/HelpDrawer';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderDrawer(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <HelpDrawer open onOpenChange={() => {}} />
    </MemoryRouter>,
  );
}

describe('HelpDrawer', () => {
  it('shows the article mapped to the current route', () => {
    renderDrawer('/app/conexoes');
    expect(screen.getByRole('heading', { name: 'Conectar o WhatsApp do escritório (modo manual)' })).toBeInTheDocument();
  });

  it('shows a different article for a different mapped route', () => {
    renderDrawer('/app/agenda');
    expect(screen.getByRole('heading', { name: 'Agenda e follow-ups' })).toBeInTheDocument();
  });

  it('falls back to search when the route has no mapped article', () => {
    renderDrawer('/app/atividade');
    expect(screen.getByLabelText('Buscar artigos de ajuda')).toBeInTheDocument();
  });

  it('lets the user open a related article without leaving the drawer', () => {
    renderDrawer('/app/conexoes');
    fireEvent.click(screen.getByText('Erros comuns ao conectar o WhatsApp'));
    expect(screen.getByRole('heading', { name: 'Erros comuns ao conectar o WhatsApp' })).toBeInTheDocument();
  });
});
