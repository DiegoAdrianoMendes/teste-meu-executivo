/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import MetaOAuthCallbackPage from '@/klm/pages/MetaOAuthCallbackPage';

const instagramMocks = vi.hoisted(() => ({ completeInstagramOAuth: vi.fn() }));
const messengerMocks = vi.hoisted(() => ({ completeMessengerOAuth: vi.fn() }));

vi.mock('@/services/instagramOauth', () => instagramMocks);
vi.mock('@/services/messengerOauth', () => messengerMocks);

function renderCallback(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/conexoes/:channel/callback" element={<MetaOAuthCallbackPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  instagramMocks.completeInstagramOAuth.mockReset();
  messengerMocks.completeMessengerOAuth.mockReset();
});

afterEach(cleanup);

describe('MetaOAuthCallbackPage', () => {
  it('conclui o callback Instagram e não expõe code/state na interface', async () => {
    instagramMocks.completeInstagramOAuth.mockResolvedValue({
      status: 'connected',
      connectionId: 'connection-1',
      message: 'Credencial armazenada de forma cifrada.',
    });
    renderCallback('/app/conexoes/instagram/callback?code=secret-code&state=signed-state');

    expect(await screen.findByText('Conexão concluída')).toBeInTheDocument();
    expect(instagramMocks.completeInstagramOAuth).toHaveBeenCalledWith('secret-code', 'signed-state');
    expect(screen.queryByText(/secret-code|signed-state/)).not.toBeInTheDocument();
  });

  it('reflete o gate fechado como preparado, sem afirmar conexão', async () => {
    messengerMocks.completeMessengerOAuth.mockResolvedValue({
      status: 'oauth_prepared',
      connectionId: 'connection-2',
      message: 'Autorização validada; troca gated.',
    });
    renderCallback('/app/conexoes/messenger/callback?code=code&state=state');

    expect(await screen.findByText('Autorização preparada')).toBeInTheDocument();
    expect(screen.queryByText('Conexão concluída')).not.toBeInTheDocument();
  });

  it('não chama o backend quando a Meta devolve erro', async () => {
    renderCallback('/app/conexoes/instagram/callback?error=access_denied&error_description=Usu%C3%A1rio%20cancelou');

    expect(await screen.findByText('Não foi possível conectar')).toBeInTheDocument();
    expect(screen.getByText(/Usuário cancelou/)).toBeInTheDocument();
    await waitFor(() => expect(instagramMocks.completeInstagramOAuth).not.toHaveBeenCalled());
  });
});
