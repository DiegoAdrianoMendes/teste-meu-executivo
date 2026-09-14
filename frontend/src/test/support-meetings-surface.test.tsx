/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const supportHttp = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}));

vi.mock('@/services/http', async () => {
  const actual = await vi.importActual<typeof import('@/services/http')>('@/services/http');
  return { ...actual, apiFetch: supportHttp.apiFetch };
});

import { ApiError } from '@/services/http';
import { SupportMeetingsSurface } from '@/support-mode/SupportMeetingsSurface';
import {
  activateStagedSupportMode,
  clearSupportModeCredential,
  completeSupportModeEnding,
  stageSupportModeCredential,
  type SupportModeSession,
} from '@/support-mode/session';
import type { SupportAccessScope } from '@/support-mode/scopes';

const MEETING_ID = '77777777-7777-4777-8777-777777777777';
const MATTER_ID = '11111111-1111-4111-8111-111111111111';
const TASK_ID = '22222222-2222-4222-8222-222222222222';
const ORGANIZER_ID = '33333333-3333-4333-8333-333333333333';
const ATTENDEE_ID = '44444444-4444-4444-8444-444444444444';
const OBSERVER_ID = '55555555-5555-4555-8555-555555555555';

const meeting = {
  id: MEETING_ID,
  workspaceId: 'workspace-support',
  matterId: MATTER_ID,
  responsibleUserId: ORGANIZER_ID,
  linkedTaskId: TASK_ID,
  title: 'Reunião de estratégia',
  startsAt: '2026-08-03T13:00:00.000Z',
  endsAt: '2026-08-03T14:00:00.000Z',
  status: 'scheduled',
  natureza: 'juridico',
  eventType: 'reuniao',
  location: 'Sala principal',
  meetingUrl: null,
  notes: 'Revisar próximos passos.',
  recipientOverrides: [{ channel: 'email', recipient: 'convidado@example.com' }],
  version: 4,
  participants: [
    {
      id: '66666666-6666-4666-8666-666666666666',
      membershipId: '77777777-7777-4777-8777-777777777778',
      userId: ORGANIZER_ID,
      role: 'organizer',
      rsvp: 'accepted',
      reminderMinutesBefore: 30,
      labelSnapshot: 'Olivia Organizadora',
      active: true,
      removedAt: null,
    },
    {
      id: '88888888-8888-4888-8888-888888888888',
      membershipId: '99999999-9999-4999-8999-999999999999',
      userId: ATTENDEE_ID,
      role: 'attendee',
      rsvp: 'pending',
      reminderMinutesBefore: null,
      labelSnapshot: 'Ana Participante',
      active: true,
      removedAt: null,
    },
  ],
  createdAt: '2026-07-31T10:00:00.000Z',
  updatedAt: '2026-07-31T10:00:00.000Z',
} as const;

const members = [
  {
    userId: ORGANIZER_ID,
    name: 'Olivia Organizadora',
    email: 'olivia@example.com',
    status: 'active',
    isActive: true,
  },
  {
    userId: ATTENDEE_ID,
    name: 'Ana Participante',
    email: 'ana@example.com',
    status: 'active',
    isActive: true,
  },
  {
    userId: OBSERVER_ID,
    name: 'Bruno Observador',
    email: 'bruno@example.com',
    status: 'active',
    isActive: true,
  },
];

const matter = {
  id: MATTER_ID,
  caseNumber: '0001234-56.2026.8.07.0001',
  demandType: 'Ação de cobrança',
  client: { name: 'Cliente Autorizado' },
};

const task = {
  id: TASK_ID,
  title: 'Preparar defesa contratual',
  status: 'in_progress',
};

function supportSession(scopes: SupportAccessScope[]): SupportModeSession {
  return {
    grantId: 'grant-support-meetings',
    workspaceId: 'workspace-support',
    workspaceName: 'Workspace em suporte',
    scopes,
    expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
    sessionType: 'support_approved',
  };
}

function defaultApi(path: string, init?: RequestInit): Promise<unknown> {
  if (path === '/api/v1/meetings' && !init?.method) return Promise.resolve([meeting]);
  if (path === '/api/v1/admin/team-members' && !init?.method) return Promise.resolve(members);
  if (path === '/api/v1/matters?limit=100' && !init?.method) return Promise.resolve([matter]);
  if (path === `/api/v1/matters/${MATTER_ID}/tasks` && !init?.method) return Promise.resolve([task]);
  if (path === '/api/v1/meetings' && init?.method === 'POST') {
    return Promise.resolve({ ...meeting, title: 'Nova reunião de suporte', version: 1 });
  }
  if (path === `/api/v1/meetings/${MEETING_ID}` && init?.method === 'PATCH') {
    const body = JSON.parse(init.body as string) as { title?: string };
    return Promise.resolve({ ...meeting, ...body, version: 5 });
  }
  return Promise.reject(new Error(`Rota não simulada no teste: ${path}`));
}

function renderSession(session: SupportModeSession) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false, gcTime: 0 },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <SupportMeetingsSurface session={session} />
    </QueryClientProvider>,
  );
}

function renderSurface(scopes: SupportAccessScope[]) {
  const session = supportSession(scopes);
  stageSupportModeCredential({
    token: 'support-token',
    grantId: session.grantId,
    workspaceId: session.workspaceId,
    scopes,
    expiresAt: session.expiresAt,
  });
  activateStagedSupportMode({
    grantId: session.grantId,
    workspaceId: session.workspaceId,
    workspaceName: session.workspaceName,
    sessionType: session.sessionType,
  });
  return renderSession(session);
}

async function choose(dialog: HTMLElement, label: string, option: string | RegExp) {
  fireEvent.click(within(dialog).getByRole('combobox', { name: label }));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}

async function prepareNewMeeting(title: string) {
  const newButton = await screen.findByRole('button', { name: 'Nova reunião' });
  await waitFor(() => expect(newButton).toBeEnabled());
  fireEvent.click(newButton);
  const dialog = await screen.findByRole('dialog', { name: 'Nova reunião' });
  fireEvent.change(within(dialog).getByLabelText('Título'), { target: { value: title } });
  await choose(dialog, 'Organizador do compromisso', /Olivia Organizadora/);
  return dialog;
}

beforeEach(() => {
  supportHttp.apiFetch.mockImplementation(defaultApi);
});

afterEach(() => {
  cleanup();
  clearSupportModeCredential();
  completeSupportModeEnding();
  vi.clearAllMocks();
});

describe('SupportMeetingsSurface', () => {
  it('fails closed when the rendered scope set differs from the active vault', () => {
    const active = supportSession(['meetings:read']);
    stageSupportModeCredential({
      token: 'support-token',
      grantId: active.grantId,
      workspaceId: active.workspaceId,
      scopes: active.scopes,
      expiresAt: active.expiresAt,
    });
    activateStagedSupportMode({
      grantId: active.grantId,
      workspaceId: active.workspaceId,
      workspaceName: active.workspaceName,
      sessionType: active.sessionType,
    });

    renderSession({
      ...active,
      scopes: ['meetings:read', 'meetings:write'],
    });

    expect(screen.getByRole('alert')).toHaveTextContent('Sessão administrativa ativa obrigatória');
    expect(supportHttp.apiFetch).not.toHaveBeenCalled();
  });

  it('lists and creates a collaborative meeting with label-only selectors', async () => {
    renderSurface([
      'meetings:read',
      'meetings:write',
      'members:read',
      'matters:read',
      'tasks:read',
    ]);

    expect(await screen.findByText('Reunião de estratégia')).toBeInTheDocument();
    expect(screen.getByText(/Olivia Organizadora, Ana Participante/)).toBeInTheDocument();

    const dialog = await prepareNewMeeting('Nova reunião de suporte');
    await choose(dialog, 'Caso vinculado', /Cliente Autorizado/);
    await waitFor(() => expect(supportHttp.apiFetch).toHaveBeenCalledWith(
      `/api/v1/matters/${MATTER_ID}/tasks`,
    ));
    fireEvent.change(within(dialog).getByLabelText('Pesquisar tarefa do caso'), {
      target: { value: 'defesa' },
    });
    await choose(dialog, 'Tarefa vinculada', /Preparar defesa contratual/);

    fireEvent.change(within(dialog).getByLabelText('Adicionar pessoas'), {
      target: { value: 'Ana' },
    });
    fireEvent.click(within(dialog).getByRole('button', {
      name: 'Adicionar Ana Participante como participante',
    }));
    fireEvent.change(within(dialog).getByLabelText('Adicionar pessoas'), {
      target: { value: 'Bruno' },
    });
    fireEvent.click(within(dialog).getByRole('button', {
      name: 'Adicionar Bruno Observador como observador',
    }));

    expect(dialog).not.toHaveTextContent(MATTER_ID);
    expect(dialog).not.toHaveTextContent(TASK_ID);
    expect(dialog).not.toHaveTextContent(ORGANIZER_ID);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Criar reunião' }));

    await waitFor(() => expect(supportHttp.apiFetch).toHaveBeenCalledWith(
      '/api/v1/meetings',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': expect.any(String) }),
      }),
    ));
    const createCall = supportHttp.apiFetch.mock.calls.find(
      ([path, init]) => path === '/api/v1/meetings' && init?.method === 'POST',
    );
    const payload = JSON.parse(createCall?.[1]?.body as string);
    expect(payload).toMatchObject({
      title: 'Nova reunião de suporte',
      matterId: MATTER_ID,
      linkedTaskId: TASK_ID,
      responsibleUserId: ORGANIZER_ID,
      confirmParticipantConflicts: false,
      conflictReason: null,
    });
    expect(payload).not.toHaveProperty('recipientOverrides');
    expect(payload.participants).toEqual([
      expect.objectContaining({ userId: ORGANIZER_ID, role: 'organizer' }),
      expect.objectContaining({ userId: ATTENDEE_ID, role: 'attendee' }),
      expect.objectContaining({ userId: OBSERVER_ID, role: 'observer' }),
    ]);
  });

  it('preserves people, case, and task when auxiliary lookups were not granted', async () => {
    renderSurface(['meetings:read', 'meetings:write', 'lead_data:read']);

    expect(await screen.findByText('Reunião de estratégia')).toBeInTheDocument();
    expect(screen.getByText(/members:read/)).toBeInTheDocument();
    expect(screen.getByText(/matters:read/)).toBeInTheDocument();
    expect(screen.getByText(/tasks:read/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nova reunião' })).toBeDisabled();
    expect(supportHttp.apiFetch).not.toHaveBeenCalledWith('/api/v1/admin/team-members');
    expect(supportHttp.apiFetch).not.toHaveBeenCalledWith('/api/v1/matters?limit=100');

    fireEvent.click(screen.getByRole('button', { name: 'Editar' }));
    const dialog = await screen.findByRole('dialog', { name: 'Editar compromisso' });
    expect(within(dialog).getByText('Caso vinculado preservado.')).toBeInTheDocument();
    expect(within(dialog).getByText('Tarefa compatível preservada.')).toBeInTheDocument();
    expect(within(dialog).getByText('Olivia Organizadora')).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(MATTER_ID);
    expect(dialog).not.toHaveTextContent(TASK_ID);

    fireEvent.change(within(dialog).getByLabelText('Título'), {
      target: { value: 'Estratégia atualizada pelo suporte' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Salvar alterações' }));

    await waitFor(() => expect(supportHttp.apiFetch).toHaveBeenCalledWith(
      `/api/v1/meetings/${MEETING_ID}`,
      expect.objectContaining({ method: 'PATCH' }),
    ));
    const updateCall = supportHttp.apiFetch.mock.calls.find(
      ([path, init]) => path === `/api/v1/meetings/${MEETING_ID}` && init?.method === 'PATCH',
    );
    const payload = JSON.parse(updateCall?.[1]?.body as string);
    expect(payload).toMatchObject({
      expectedVersion: 4,
      matterId: MATTER_ID,
      linkedTaskId: TASK_ID,
      responsibleUserId: ORGANIZER_ID,
    });
    expect(payload).not.toHaveProperty('recipientOverrides');
    expect(payload.participants).toHaveLength(2);
  });

  it('preserves a legacy responsible user as organizer without exposing its UUID', async () => {
    const legacyMeeting = { ...meeting, participants: [] };
    supportHttp.apiFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/v1/meetings' && !init?.method) return Promise.resolve([legacyMeeting]);
      if (path === `/api/v1/meetings/${MEETING_ID}` && init?.method === 'PATCH') {
        return Promise.resolve({ ...legacyMeeting, version: 5 });
      }
      return defaultApi(path, init);
    });
    renderSurface(['meetings:read', 'meetings:write', 'lead_data:read']);

    expect(await screen.findByText('Reunião de estratégia')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Editar' }));
    const dialog = await screen.findByRole('dialog', { name: 'Editar compromisso' });
    expect(within(dialog).getByText('Pessoa vinculada')).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(ORGANIZER_ID);

    fireEvent.change(within(dialog).getByLabelText('Título'), {
      target: { value: 'Reunião legada preservada' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Salvar alterações' }));

    await waitFor(() => expect(supportHttp.apiFetch).toHaveBeenCalledWith(
      `/api/v1/meetings/${MEETING_ID}`,
      expect.objectContaining({ method: 'PATCH' }),
    ));
    const updateCall = supportHttp.apiFetch.mock.calls.find(
      ([path, init]) => path === `/api/v1/meetings/${MEETING_ID}` && init?.method === 'PATCH',
    );
    const payload = JSON.parse(updateCall?.[1]?.body as string);
    expect(payload.responsibleUserId).toBe(ORGANIZER_ID);
    expect(payload.participants).toEqual([
      expect.objectContaining({ userId: ORGANIZER_ID, role: 'organizer' }),
    ]);
  });

  it('keeps creation available but blocks editing a masked meeting without lead_data:read', async () => {
    const maskedMeeting = {
      ...meeting,
      title: 'Reunião do lead-1234',
      location: 'Online',
      meetingUrl: null,
      notes: '[conteúdo protegido]',
      recipientOverrides: [],
    };
    supportHttp.apiFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/v1/meetings' && !init?.method) return Promise.resolve([maskedMeeting]);
      return defaultApi(path, init);
    });
    renderSurface(['meetings:read', 'meetings:write', 'members:read']);

    expect(await screen.findByText('Reunião do lead-1234')).toBeInTheDocument();
    expect(screen.getByText(/editar compromissos existentes foi bloqueado/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Editar' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nova reunião' })).toBeEnabled();
  });

  it('requires explicit confirmation and an audited 8-character reason for MEE-008', async () => {
    let attempts = 0;
    supportHttp.apiFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/v1/meetings' && init?.method === 'POST') {
        attempts += 1;
        if (attempts === 1) {
          return Promise.reject(new ApiError(409, 'Conflito', {
            code: 'MEE-008',
            message: 'Ana possui outro compromisso.',
            participantUserIds: [ATTENDEE_ID],
          }));
        }
      }
      return defaultApi(path, init);
    });
    renderSurface([
      'meetings:read',
      'meetings:write',
      'members:read',
      'matters:read',
      'tasks:read',
    ]);
    const dialog = await prepareNewMeeting('Reunião com sobreposição');
    fireEvent.change(within(dialog).getByLabelText('Adicionar pessoas'), {
      target: { value: 'Ana' },
    });
    fireEvent.click(within(dialog).getByRole('button', {
      name: 'Adicionar Ana Participante como participante',
    }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Criar reunião' }));

    expect(await within(dialog).findByRole('alert', { name: 'Aviso de conflito' })).toHaveTextContent(
      'Ana Participante',
    );
    fireEvent.click(within(dialog).getByRole('checkbox', {
      name: 'Confirmar sobreposição de participantes',
    }));
    const reason = within(dialog).getByLabelText('Motivo da confirmação');
    fireEvent.change(reason, { target: { value: 'curto' } });
    expect(within(dialog).getByRole('button', { name: 'Criar reunião' })).toBeDisabled();
    fireEvent.change(reason, { target: { value: 'Cliente confirmou a sobreposição.' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Criar reunião' }));

    await waitFor(() => expect(attempts).toBe(2));
    const creates = supportHttp.apiFetch.mock.calls.filter(
      ([path, init]) => path === '/api/v1/meetings' && init?.method === 'POST',
    );
    expect(JSON.parse(creates[1][1]?.body as string)).toMatchObject({
      confirmParticipantConflicts: true,
      conflictReason: 'Cliente confirmou a sobreposição.',
    });
  });

  it('treats MEE-007 as blocking and offers no override', async () => {
    supportHttp.apiFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/v1/meetings' && init?.method === 'POST') {
        return Promise.reject(new ApiError(409, 'Conflito', {
          code: 'MEE-007',
          message: 'O organizador já possui outro compromisso.',
        }));
      }
      return defaultApi(path, init);
    });
    renderSurface(['meetings:read', 'meetings:write', 'members:read']);
    const dialog = await prepareNewMeeting('Reunião impossível');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Criar reunião' }));

    expect(await within(dialog).findByRole('alert', { name: 'Conflito bloqueante' })).toHaveTextContent(
      'não possui override',
    );
    expect(within(dialog).queryByRole('checkbox', {
      name: 'Confirmar sobreposição de participantes',
    })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Criar reunião' })).toBeDisabled();
  });

  it('ignores a pending mutation result after the administrative session is revoked', async () => {
    let resolveCreate: ((value: unknown) => void) | null = null;
    supportHttp.apiFetch.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/v1/meetings' && init?.method === 'POST') {
        return new Promise((resolve) => {
          resolveCreate = resolve;
        });
      }
      return defaultApi(path, init);
    });
    renderSurface(['meetings:read', 'meetings:write', 'members:read']);
    const dialog = await prepareNewMeeting('Reunião revogada');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Criar reunião' }));
    await waitFor(() => expect(resolveCreate).not.toBeNull());

    clearSupportModeCredential('unauthorized');
    resolveCreate?.({ ...meeting, title: 'Reunião revogada' });

    await waitFor(() => expect(screen.queryByText('Alteração concluída')).not.toBeInTheDocument());
    expect(screen.queryByText('Compromisso salvo e enviado à trilha de auditoria.')).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Nova reunião' })).toBeInTheDocument();
  });
});
