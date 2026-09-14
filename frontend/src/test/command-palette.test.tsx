/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { CommandPalette } from '@/klm/components/CommandPalette';
import type { KlmNavItem } from '@/klm/navigation';
import { LayoutDashboard } from 'lucide-react';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const navigationItems: KlmNavItem[] = [
  { label: 'Início', shortLabel: 'Início', path: '/app', icon: LayoutDashboard, group: 'operacao', permission: { module: 'dashboard', action: 'view' } },
  { label: 'Central de Conexão', shortLabel: 'Conexões', path: '/app/conexoes', icon: LayoutDashboard, group: 'sistema', permission: { module: 'integrations', action: 'view' } },
];

const funnelCards = [
  { id: 'card-1', name: 'Maria Souza', subject: 'trabalhista', object: 'Rescisão', source: 'instagram', probability: '60%' } as unknown as import('@/klm/api').KlmFunnelCard,
];

function Probe() {
  return <div data-testid="probe-route">rota-alvo</div>;
}

function renderPalette(open = true) {
  const onOpenChange = vi.fn();
  render(
    <MemoryRouter initialEntries={['/app']}>
      <Routes>
        <Route path="/app/conexoes" element={<Probe />} />
        <Route
          path="*"
          element={
            <CommandPalette
              open={open}
              onOpenChange={onOpenChange}
              navigationItems={navigationItems}
              funnelCards={funnelCards}
              threads={[]}
              contextualPath={(path) => path}
            />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  return { onOpenChange };
}

describe('CommandPalette', () => {
  it('opens and lists pages, knowledge articles and leads/casos', () => {
    renderPalette();
    expect(screen.getByPlaceholderText('Buscar página, artigo de ajuda, lead/caso ou conversa…')).toBeInTheDocument();
    expect(screen.getByText('Central de Conexão')).toBeInTheDocument();
    expect(screen.getByText('Conectar o WhatsApp do escritório (modo manual)')).toBeInTheDocument();
    expect(screen.getByText('Maria Souza')).toBeInTheDocument();
  });

  it('filters results as the user types', () => {
    renderPalette();
    const input = screen.getByPlaceholderText('Buscar página, artigo de ajuda, lead/caso ou conversa…');
    fireEvent.change(input, { target: { value: 'Maria Souza' } });
    expect(screen.getByText('Maria Souza')).toBeInTheDocument();
    expect(screen.queryByText('Central de Conexão')).not.toBeInTheDocument();
  });

  it('navigates and closes the palette when a page result is selected', () => {
    const { onOpenChange } = renderPalette();
    fireEvent.click(screen.getByText('Central de Conexão'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(screen.getByTestId('probe-route')).toBeInTheDocument();
  });
});
