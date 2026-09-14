/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import FunctionUnitInventoryPage from '@/klm/pages/FunctionUnitInventoryPage';
import {
  klmApi,
  type KlmFunctionUnit,
  type KlmFunctionUnitList,
  type KlmFunctionUnitRuntime,
} from '@/klm/api';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const unit: KlmFunctionUnit = {
  id: 'fu_0123456789abcdef01234567',
  domain: 'communications',
  name: 'Gestão de contatos: listar contatos',
  category: 'api',
  capability: 'contacts-management',
  maturity: 'native_primary',
  disposition: 'mapped',
  summary: 'Função mapeada para uma capacidade Meu Executivo. Domínio: Comunicações.',
};

const runtime: KlmFunctionUnitRuntime = {
  function: unit,
  state: {
    maturity: 'native_primary',
    availableModes: ['dry-run', 'execute'],
    auditEventCount: 0,
    lastActivityAt: null,
    externalEffectsEnabled: false,
  },
};

function listFixture(overrides: Partial<KlmFunctionUnitList> = {}): KlmFunctionUnitList {
  return {
    schemaVersion: '1.0',
    scope: 'klm-capability-functions',
    summary: {
      total: 3845,
      available: 3768,
      blocked: 77,
      verified: 0,
      remaining: 3768,
      byDomain: { communications: 1117, collaboration: 1919, automation: 279, crm: 530 },
      byCategory: { api: 1812, ui: 398 },
      byDisposition: { mapped: 3768, blocked_license: 77 },
    },
    filters: {
      domains: ['communications', 'collaboration', 'automation', 'crm'],
      categories: ['api', 'ui'],
      dispositions: ['mapped', 'blocked_license'],
      capabilities: ['contacts-management'],
    },
    page: 1,
    pageSize: 50,
    total: 51,
    pages: 2,
    items: [unit],
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/app/ferramentas/inventario']}>
        <Routes>
          <Route path="*" element={<><FunctionUnitInventoryPage /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('FunctionUnitInventoryPage', () => {
  it('shows the public Meu Executivo totals and never presents mapped as verified', async () => {
    vi.spyOn(klmApi, 'functionUnits').mockResolvedValue(listFixture());

    renderPage();

    expect(await screen.findByText('3.845')).toBeInTheDocument();
    expect(screen.getByText('3.768')).toBeInTheDocument();
    expect(screen.getAllByText('77')).toHaveLength(1);
    expect(screen.getByText('Gestão de contatos: listar contatos')).toBeInTheDocument();
    expect(screen.getAllByText('Mapeada').length).toBeGreaterThan(0);
    expect(screen.getByText('0% disponíveis')).toBeInTheDocument();
  });

  it('filters by domain, paginates, and opens the opaque function detail', async () => {
    const list = vi.spyOn(klmApi, 'functionUnits').mockImplementation(async (query) =>
      listFixture({ page: query.page ?? 1 }),
    );
    const detail = vi.spyOn(klmApi, 'functionUnit').mockResolvedValue(unit);
    const runtimeDetail = vi.spyOn(klmApi, 'functionUnitRuntime').mockResolvedValue(runtime);
    const validation = vi.spyOn(klmApi, 'executeFunctionUnit').mockResolvedValue({
      status: 'simulated',
      replayed: false,
      externalEffectExecuted: false,
      summary: 'Validação local concluída.',
    });

    renderPage();
    await screen.findByText(unit.name);

    fireEvent.change(screen.getByLabelText('Domínio'), { target: { value: 'communications' } });
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ domain: 'communications', page: 1 })));

    fireEvent.click(await screen.findByRole('button', { name: /próxima/i }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })));
    await screen.findByText(unit.name);

    fireEvent.click(screen.getByRole('button', { name: new RegExp(unit.name, 'i') }));
    expect(await screen.findByText('Detalhes da função Meu Executivo')).toBeInTheDocument();
    expect(detail).toHaveBeenCalledWith(unit.id);
    await waitFor(() => expect(runtimeDetail).toHaveBeenCalledWith(unit.id));
    expect(screen.getAllByText('Comunicações').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Primária no Meu Executivo').length).toBeGreaterThan(0);

    const validateButton = await screen.findByRole('button', { name: /Validar localmente/i });
    await waitFor(() => expect(validateButton).toBeEnabled());
    fireEvent.click(validateButton);
    await waitFor(() => expect(validation).toHaveBeenCalledWith(
      unit.id,
      expect.objectContaining({ mode: 'dry-run', payload: {} }),
    ));
    expect(await screen.findByText('Validação local concluída.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Abrir capacidade Meu Executivo/i }));
    expect(screen.getByTestId('location')).toHaveTextContent('/app/capacidades/communications/contacts-management');
  });
});
