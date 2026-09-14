/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  klmApi,
  type KlmDailyBriefing,
  type KlmFunnel,
  type KlmMeeting,
  type KlmRuntimeStatus,
} from '@/klm/api';
import AgendaPage from '@/klm/pages/AgendaPage';
import { ApiError } from '@/services/http';
import * as adminService from '@/services/admin';
import type { AdminUser } from '@/types';

vi.mock('@/klm/components/BookingLinkDialog', () => ({ BookingLinkDialog: () => null }));
vi.mock('@/klm/components/TaskCollaborationDrawer', () => ({ TaskCollaborationDrawer: () => null }));
vi.mock('@/klm/components/NativeCRMOperationsPanel', () => ({ NativeCRMOperationsPanel: () => null }));
vi.mock('@/klm/components/NativeModuleBar', () => ({ NativeModuleBar: () => null }));

const MATTER_ID = '11111111-1111-4111-8111-111111111111';
const TASK_ID = '22222222-2222-4222-8222-222222222222';
const ORGANIZER_ID = '33333333-3333-4333-8333-333333333333';
const ATTENDEE_ID = '44444444-4444-4444-8444-444444444444';
const OBSERVER_ID = '55555555-5555-4555-8555-555555555555';
const REMOVED_ID = '66666666-6666-4666-8666-666666666666';

const funnel: KlmFunnel = {
  boardVersion: 1,
  stages: [],
  metrics: {},
  cards: [{
    id: MATTER_ID,
    clientId: 'client-1',
    name: 'Cliente Colaborativo',
    subject: 'Caso contratual',
    object: 'Revisão de contrato',
    source: 'indicação',
    owner: 'Olivia',
    probability: 80,
    initialFee: 5000,
    projectedRevenue: 12000,
    slaDueAt: null,
    documents: 2,
    documentScore: 90,
    stage: 'reuniao',
    microStatus: 'agendada',
  }],
};

const runtime: KlmRuntimeStatus = {
  mode: 'local_simulation',
  localOnly: true,
  localInfrastructure: true,
  externalActionsEnabled: false,
  awsEnabled: false,
  modelApisEnabled: false,
  onlineApproved: false,
  localBootstrapEnabled: true,
  providers: { googleWorkspace: 'offline' },
};

const team: AdminUser[] = [
  [ORGANIZER_ID, 'Olivia Organizadora', 'olivia@example.com'],
  [ATTENDEE_ID, 'Ana Participante', 'ana@example.com'],
  [OBSERVER_ID, 'Bruno Observador', 'bruno@example.com'],
  [REMOVED_ID, 'Carla Temporária', 'carla@example.com'],
].map(([id, name, email]) => ({
  id,
  membershipId: `membership-${id}`,
  name,
  initials: name.slice(0, 2).toUpperCase(),
  email,
  type: 'human',
  role: 'operator',
  color: '#123456',
  isActive: true,
  lastLogin: '-',
  roles: ['operator'],
  directPermissions: [],
}));

const dailyBriefing: KlmDailyBriefing = {
  day: '2026-07-31',
  timezone: 'America/Sao_Paulo',
  total: 0,
  linkedCount: 0,
  orphanCount: 0,
  missingRoomCount: 0,
  summary: 'Sem reuniões.',
  priorities: [],
  items: [],
  generationMode: 'local_context',
  generatedBy: 'Agenda local',
  generatedAt: '2026-07-31T12:00:00Z',
};

function meetingResult(): KlmMeeting {
  const startsAt = new Date(Date.now() + 86_400_000);
  return {
    id: '77777777-7777-4777-8777-777777777777',
    version: 1,
    matterId: MATTER_ID,
    responsibleUserId: ORGANIZER_ID,
    linkedTaskId: TASK_ID,
    title: 'Reunião colaborativa',
    startsAt: startsAt.toISOString(),
    endsAt: new Date(startsAt.getTime() + 3_600_000).toISOString(),
    status: 'scheduled',
    natureza: 'comercial',
    eventType: 'reuniao',
    location: 'Google Meet',
    meetingUrl: null,
    notes: null,
    participants: [],
  };
}

function renderAgenda() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/app/agenda']}>
        <AgendaPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function chooseOption(
  dialog: HTMLElement,
  comboboxName: string,
  optionName: string | RegExp,
) {
  fireEvent.click(within(dialog).getByRole('combobox', { name: comboboxName }));
  fireEvent.click(await screen.findByRole('option', { name: optionName }));
}

async function prepareCollaborativeDraft() {
  fireEvent.click(await screen.findByRole('button', { name: 'Nova reunião' }));
  const dialog = await screen.findByRole('dialog', { name: 'Nova reunião' });
  fireEvent.change(within(dialog).getByLabelText('Título'), {
    target: { value: 'Reunião colaborativa' },
  });
  await chooseOption(dialog, 'Caso vinculado', /Cliente Colaborativo/);
  await chooseOption(dialog, 'Organizador do compromisso', 'Olivia Organizadora');
  await waitFor(() => expect(klmApi.matterTasks).toHaveBeenCalledWith(MATTER_ID));
  return dialog;
}

describe('Agenda — compromissos colaborativos', () => {
  beforeEach(() => {
    vi.spyOn(klmApi, 'meetings').mockResolvedValue([]);
    vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    vi.spyOn(klmApi, 'runtime').mockResolvedValue(runtime);
    vi.spyOn(klmApi, 'dailyBriefing').mockResolvedValue(dailyBriefing);
    vi.spyOn(klmApi, 'unifiedAgenda').mockResolvedValue([]);
    vi.spyOn(klmApi, 'matterTasks').mockResolvedValue([{
      id: TASK_ID,
      version: 1,
      matterId: MATTER_ID,
      title: 'Preparar defesa contratual',
      description: null,
      status: 'in_progress',
      priority: 'alta',
      assignedTo: ORGANIZER_ID,
      dueDate: null,
      isAiGenerated: false,
      createdAt: '2026-07-31T10:00:00Z',
    }]);
    vi.spyOn(adminService, 'fetchTeamMembers').mockResolvedValue(team);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('adds and removes people, stores RSVP/reminders, and links a searched task without UUID input', async () => {
    const createMeeting = vi.spyOn(klmApi, 'createMeeting').mockResolvedValue(meetingResult());
    renderAgenda();
    const dialog = await prepareCollaborativeDraft();

    fireEvent.change(within(dialog).getByLabelText('Pesquisar participante interno'), {
      target: { value: 'Ana' },
    });
    fireEvent.click(within(dialog).getByRole('button', {
      name: 'Adicionar Ana Participante como participante',
    }));
    fireEvent.change(within(dialog).getByLabelText('Pesquisar participante interno'), {
      target: { value: 'Bruno' },
    });
    fireEvent.click(within(dialog).getByRole('button', {
      name: 'Adicionar Bruno Observador como observador',
    }));
    fireEvent.change(within(dialog).getByLabelText('Pesquisar participante interno'), {
      target: { value: 'Carla' },
    });
    fireEvent.click(within(dialog).getByRole('button', {
      name: 'Adicionar Carla Temporária como participante',
    }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remover Carla Temporária' }));

    await chooseOption(dialog, 'RSVP de Ana Participante', 'Confirmado');
    await chooseOption(dialog, 'Lembrete de Ana Participante', '30 min antes');

    fireEvent.change(within(dialog).getByLabelText('Pesquisar tarefa do caso'), {
      target: { value: 'defesa' },
    });
    await chooseOption(dialog, 'Tarefa vinculada', /Preparar defesa contratual/);

    expect(dialog).not.toHaveTextContent(MATTER_ID);
    expect(dialog).not.toHaveTextContent(TASK_ID);
    expect(dialog).not.toHaveTextContent(ORGANIZER_ID);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Criar reunião' }));

    await waitFor(() => expect(createMeeting).toHaveBeenCalledTimes(1));
    expect(createMeeting).toHaveBeenCalledWith(expect.objectContaining({
      matterId: MATTER_ID,
      linkedTaskId: TASK_ID,
      responsibleUserId: ORGANIZER_ID,
      confirmParticipantConflicts: false,
      conflictReason: null,
      participants: [
        expect.objectContaining({ userId: ORGANIZER_ID, role: 'organizer' }),
        expect.objectContaining({
          userId: ATTENDEE_ID,
          role: 'attendee',
          rsvp: 'accepted',
          reminderMinutesBefore: 30,
        }),
        expect.objectContaining({ userId: OBSERVER_ID, role: 'observer' }),
      ],
    }));
    expect(createMeeting.mock.calls[0][0].participants).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ userId: REMOVED_ID })]),
    );
  });

  it('shows attendee conflicts as a warning and requires an audited reason before retrying', async () => {
    const createMeeting = vi.spyOn(klmApi, 'createMeeting')
      .mockRejectedValueOnce(new ApiError(
        409,
        'Há participantes com conflito de agenda.',
        {
          code: 'MEE-008',
          message: 'Há participantes com conflito de agenda. Confirme com motivo.',
          participantUserIds: [ATTENDEE_ID],
        },
      ))
      .mockResolvedValueOnce(meetingResult());
    renderAgenda();
    const dialog = await prepareCollaborativeDraft();
    fireEvent.change(within(dialog).getByLabelText('Pesquisar participante interno'), {
      target: { value: 'Ana' },
    });
    fireEvent.click(within(dialog).getByRole('button', {
      name: 'Adicionar Ana Participante como participante',
    }));

    fireEvent.click(within(dialog).getByRole('button', { name: 'Criar reunião' }));
    expect(await within(dialog).findByRole('alert', { name: 'Aviso de conflito' })).toHaveTextContent(
      'Ana Participante',
    );
    expect(within(dialog).getByRole('button', { name: 'Criar reunião' })).toBeDisabled();

    fireEvent.click(within(dialog).getByRole('checkbox', {
      name: 'Confirmar sobreposição de participantes',
    }));
    const reason = within(dialog).getByLabelText('Motivo da confirmação');
    expect(reason).toHaveAttribute('maxlength', '500');
    fireEvent.change(reason, {
      target: { value: '1234567' },
    });
    expect(within(dialog).getByRole('button', { name: 'Criar reunião' })).toBeDisabled();
    fireEvent.change(reason, {
      target: { value: '12345678' },
    });
    expect(within(dialog).getByRole('button', { name: 'Criar reunião' })).toBeEnabled();
    fireEvent.change(reason, {
      target: { value: 'Cliente confirmou a presença simultânea.' },
    });
    const retryButton = within(dialog).getByRole('button', { name: 'Criar reunião' });
    expect(retryButton).toBeEnabled();
    fireEvent.click(retryButton);

    await waitFor(() => expect(createMeeting).toHaveBeenCalledTimes(2));
    expect(createMeeting.mock.calls[1][0]).toMatchObject({
      confirmParticipantConflicts: true,
      conflictReason: 'Cliente confirmou a presença simultânea.',
    });
  });

  it('blocks organizer conflicts and does not offer an override', async () => {
    vi.spyOn(klmApi, 'createMeeting').mockRejectedValue(new ApiError(
      409,
      'O organizador já tem um compromisso nesse horário.',
      {
        code: 'MEE-007',
        message: 'O organizador já tem um compromisso nesse horário.',
        meetingIds: ['88888888-8888-4888-8888-888888888888'],
      },
    ));
    renderAgenda();
    const dialog = await prepareCollaborativeDraft();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Criar reunião' }));

    expect(await within(dialog).findByRole('alert', { name: 'Conflito bloqueante' })).toHaveTextContent(
      'Este conflito não possui override.',
    );
    expect(within(dialog).queryByRole('checkbox', {
      name: 'Confirmar sobreposição de participantes',
    })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Criar reunião' })).toBeDisabled();
  });
});
