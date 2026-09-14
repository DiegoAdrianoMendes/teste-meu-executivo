/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  StaffAvailabilitySheet,
  type StaffAvailabilitySheetProps,
  type StaffAvailabilityValue,
} from '@/klm/components/StaffAvailabilitySheet';

const staff = { id: 'staff-1', name: 'Ana Martins', role: 'Advogada responsável' };

function availabilityValue(overrides: Partial<StaffAvailabilityValue> = {}): StaffAvailabilityValue {
  return {
    mode: 'away',
    timezone: 'America/Sao_Paulo',
    weeklyAvailability: Array.from({ length: 7 }, (_, weekday) => ({
      weekday: weekday as 0 | 1 | 2 | 3 | 4 | 5 | 6,
      enabled: weekday < 5,
      windows: weekday < 5 ? [{ start: '09:00', end: '18:00' }] : [],
    })),
    exceptions: [],
    absenceMessage: { enabled: true, text: 'Estou ausente. A equipe dará continuidade ao atendimento.' },
    activation: { mode: 'now', startsAt: null, endsAt: null },
    redirect: { kind: 'none', targetId: null },
    automaticSwap: false,
    requestNote: '',
    ...overrides,
  };
}

function renderSheet(props: Partial<StaffAvailabilitySheetProps> = {}) {
  const onCreateRequest = props.onCreateRequest ?? vi.fn();
  const view = render(
    <StaffAvailabilitySheet
      open
      onOpenChange={vi.fn()}
      staff={staff}
      initialValue={availabilityValue()}
      onCreateRequest={onCreateRequest}
      {...props}
    />,
  );
  return { ...view, onCreateRequest };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('StaffAvailabilitySheet', () => {
  it('uses the availability semantic color for the status and enabled weekdays', () => {
    renderSheet({ initialValue: availabilityValue({ mode: 'available' }) });

    expect(screen.getAllByText('Disponível')[0].closest('span')).toHaveClass('text-[#0b7435]');
    expect(screen.getByRole('switch', { name: /Segunda-feira/ })).toHaveClass('data-[state=checked]:bg-emerald-700');
  });

  it('mantém o pedido pendente explícito e oferece decisões humanas em um clique', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    const onReject = vi.fn().mockResolvedValue(undefined);
    const approval = {
      id: 'availability-request-7',
      status: 'pending' as const,
      requestedBy: 'Ana Martins',
      requestedAt: '2026-07-22T12:00:00-03:00',
    };

    const approvedView = renderSheet({ approval, onApprove, onReject });

    expect(screen.getByRole('dialog', { name: 'Disponibilidade de Ana Martins' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Aguardando aprovação');
    expect(screen.getByRole('status')).toHaveTextContent('nenhuma alteração entra em vigor antes da decisão humana');
    expect(screen.getByRole('radio', { name: /Ausente/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Aprovar' }));
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith({
      requestId: 'availability-request-7',
      staffId: 'staff-1',
    }));
    expect(screen.getByRole('status')).toHaveTextContent('Pedido aprovado');

    approvedView.unmount();
    renderSheet({ approval, onApprove, onReject });
    fireEvent.click(screen.getByRole('button', { name: 'Rejeitar' }));

    await waitFor(() => expect(onReject).toHaveBeenCalledWith({
      requestId: 'availability-request-7',
      staffId: 'staff-1',
    }));
    expect(screen.getByRole('status')).toHaveTextContent('Pedido rejeitado');
  });

  it('cria um pedido completo com agenda, exceção, mensagem, equipe e troca automática', async () => {
    const onCreateRequest = vi.fn().mockResolvedValue(undefined);
    renderSheet({
      onCreateRequest,
      redirectOptions: [
        { id: 'user-2', kind: 'user', label: 'Marina Lopes', description: 'Closer' },
        { id: 'team-plantao', kind: 'team', label: 'Equipe de plantão' },
      ],
    });

    fireEvent.click(screen.getByRole('radio', { name: 'Agendar período' }));
    fireEvent.change(screen.getByLabelText('Início'), { target: { value: '2026-08-03T08:00' } });
    fireEvent.change(screen.getByLabelText('Fim'), { target: { value: '2026-08-14T18:00' } });

    fireEvent.change(screen.getByLabelText('Segunda-feira, início do horário 1'), { target: { value: '08:00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar segunda a sexta' }));

    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'Férias de agosto' } });
    fireEvent.change(screen.getByLabelText('Data inicial'), { target: { value: '2026-08-03' } });
    fireEvent.change(screen.getByLabelText('Data final'), { target: { value: '2026-08-14' } });
    fireEvent.click(screen.getByRole('button', { name: 'Adicionar ausência ou exceção' }));
    expect(screen.getByText('Férias de agosto')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Texto da mensagem'), {
      target: { value: 'Estou de férias. A equipe de plantão seguirá com seu atendimento.' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Nota para aprovação' }), {
      target: { value: 'Cobertura alinhada com a coordenação.' },
    });

    const destination = screen.getByRole('combobox', { name: 'Destino durante a ausência' });
    fireEvent.click(destination);
    expect(screen.getByRole('option', { name: 'Marina Lopes · Closer' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', { name: 'Equipe de plantão' }));
    fireEvent.click(screen.getByRole('switch', { name: 'Troca automática' }));

    fireEvent.click(screen.getByRole('button', { name: 'Criar pedido de alteração' }));

    await waitFor(() => expect(onCreateRequest).toHaveBeenCalledTimes(1));
    expect(onCreateRequest).toHaveBeenCalledWith(expect.objectContaining({
      staffId: 'staff-1',
      mode: 'away',
      activation: {
        mode: 'scheduled',
        startsAt: '2026-08-03T08:00',
        endsAt: '2026-08-14T18:00',
      },
      absenceMessage: {
        enabled: true,
        text: 'Estou de férias. A equipe de plantão seguirá com seu atendimento.',
      },
      redirect: { kind: 'team', targetId: 'team-plantao' },
      automaticSwap: true,
      requestNote: 'Cobertura alinhada com a coordenação.',
    }));

    const payload = onCreateRequest.mock.calls[0][0];
    expect(payload.weeklyAvailability.find((day: { weekday: number }) => day.weekday === 1)?.windows[0]).toEqual({ start: '08:00', end: '18:00' });
    expect(payload.exceptions).toEqual([
      expect.objectContaining({
        kind: 'vacation',
        name: 'Férias de agosto',
        startsOn: '2026-08-03',
        endsOn: '2026-08-14',
        mode: 'unavailable',
        windows: [],
      }),
    ]);
    expect(screen.getByRole('status')).toHaveTextContent('Aguardando aprovação');
  });

  it('bloqueia pedidos agendados incompletos com um resumo acessível', () => {
    const onCreateRequest = vi.fn();
    renderSheet({
      onCreateRequest,
      initialValue: availabilityValue({
        activation: { mode: 'scheduled', startsAt: null, endsAt: null },
        absenceMessage: { enabled: true, text: '' },
      }),
    });

    fireEvent.click(screen.getByRole('button', { name: 'Criar pedido de alteração' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Informe início e fim para agendar a alteração.');
    expect(screen.getByRole('alert')).toHaveTextContent('Escreva a mensagem de ausência');
    expect(onCreateRequest).not.toHaveBeenCalled();
  });
});
