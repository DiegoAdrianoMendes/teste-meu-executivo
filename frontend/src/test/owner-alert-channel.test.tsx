/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { OwnerAlertChannelPanel } from '@/klm/components/OwnerAlertChannelPanel';
import { klmApi } from '@/klm/api';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('lets the owner configure the phone channel and preserves honest delivery wording', async () => {
  vi.spyOn(klmApi, 'workspaceOwnerAlertChannel').mockResolvedValue({ ownerWhatsappPhone: null, configured: false });
  const update = vi.spyOn(klmApi, 'updateWorkspaceOwnerAlertChannel').mockResolvedValue({ ownerWhatsappPhone: '5511999990000', configured: true });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><OwnerAlertChannelPanel /></QueryClientProvider>);
  const input = await screen.findByLabelText('WhatsApp do dono para alertas');
  fireEvent.change(input, { target: { value: '55 11 99999-0000' } });
  fireEvent.click(screen.getByRole('button', { name: 'Salvar canal' }));
  await waitFor(() => expect(update).toHaveBeenCalledWith('5511999990000'));
  expect(screen.getByText(/recibo oficial/i)).toBeInTheDocument();
});
