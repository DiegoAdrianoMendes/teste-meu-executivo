/** @vitest-environment jsdom */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { CrmViewNav } from '@/klm/components/CrmViewNav';

afterEach(cleanup);

describe('CRM view navigation', () => {
  it('orders Kanban, Lista and Caso 360 and preserves the selected matter', () => {
    render(
      <MemoryRouter>
        <CrmViewNav active="kanban" matterId="matter-123" />
      </MemoryRouter>,
    );

    const links = screen.getAllByRole('link');
    expect(links.map((link) => link.textContent?.trim())).toEqual(['Kanban', 'Lista', 'Caso 360']);
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/app/funil?view=kanban&matter=matter-123',
      '/app/funil?view=tabela&matter=matter-123',
      '/app/leads?matter=matter-123',
    ]);
    expect(links[0]).toHaveAttribute('aria-current', 'page');
  });
});
