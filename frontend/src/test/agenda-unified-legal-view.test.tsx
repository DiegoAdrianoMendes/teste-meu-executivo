/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  klmApi,
  type KlmAgendaUnifiedEvent,
  type KlmDailyBriefing,
  type KlmFunnel,
  type KlmMeeting,
  type KlmRuntimeStatus,
} from '@/klm/api';
import AgendaPage from '@/klm/pages/AgendaPage';
import * as adminService from '@/services/admin';

/**
 * Agenda unificada comercial + jurídico (Sprint B UI) — toggle Tudo |
 * Comercial | Jurídico, cores/etiquetas por tipo de evento (os 7 tipos) e
 * a listagem dos eventos jurídicos vindos do read-model unificado
 * (`GET /agenda`, `source: 'process_publication'`). Ver `AgendaPage`.
 *
 * Asserções ficam escopadas ao painel "Lista completa da agenda" (aba
 * "Todos") — a Legenda estática também mostra os 7 rótulos de tipo de
 * evento, então buscar o rótulo solto em `screen` (sem escopo) captura
 * falso-positivo da Legenda.
 */

vi.mock('@/klm/components/BookingLinkDialog', () => ({ BookingLinkDialog: () => null }));
vi.mock('@/klm/components/TaskCollaborationDrawer', () => ({ TaskCollaborationDrawer: () => null }));
vi.mock('@/klm/components/NativeCRMOperationsPanel', () => ({ NativeCRMOperationsPanel: () => null }));
vi.mock('@/klm/components/NativeModuleBar', () => ({ NativeModuleBar: () => null }));

const funnel: KlmFunnel = { boardVersion: 1, stages: [], cards: [], metrics: {} };

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

function dailyBriefing(day: string): KlmDailyBriefing {
  return {
    day,
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
    generatedAt: new Date().toISOString(),
  };
}

const now = new Date();
const commercialStart = new Date(now.getTime() + 60 * 60 * 1000);
const commercialMeeting: KlmMeeting = {
  id: 'meeting-comercial',
  version: 1,
  matterId: null,
  title: 'Reunião online: prospecção Zeta',
  startsAt: commercialStart.toISOString(),
  endsAt: new Date(commercialStart.getTime() + 60 * 60 * 1000).toISOString(),
  status: 'scheduled',
  natureza: 'comercial',
  eventType: 'reuniao',
  location: 'Google Meet',
  meetingUrl: null,
  notes: null,
};

const hearingStart = new Date(now.getTime() + 2 * 60 * 60 * 1000);
const hearingMeeting: KlmMeeting = {
  id: 'meeting-audiencia',
  version: 1,
  matterId: null,
  title: 'Audiência de instrução processo Beta',
  startsAt: hearingStart.toISOString(),
  endsAt: new Date(hearingStart.getTime() + 90 * 60 * 1000).toISOString(),
  status: 'scheduled',
  natureza: 'juridico',
  eventType: 'audiencia',
  location: 'Fórum',
  meetingUrl: null,
  notes: null,
};

const fatalDeadlineEvent: KlmAgendaUnifiedEvent = {
  id: 'prazo:publication-1',
  source: 'process_publication',
  sourceId: 'publication-1',
  natureza: 'juridico',
  eventType: 'prazo_fatal',
  title: 'Prazo fatal · 0700001-11.2026.8.07.0001',
  startsAt: new Date(now.getTime() + 3 * 60 * 60 * 1000).toISOString(),
  endsAt: null,
  matterId: null,
  responsibleUserId: null,
  status: 'confirmado',
  isInviolable: true,
  location: null,
  meetingUrl: null,
  dias: 15,
  memoriaCalculo: [],
};

const taskEvents: KlmAgendaUnifiedEvent[] = [
  {
    id: 'task-due:task-1', source: 'task_due', sourceId: 'task-1', natureza: 'juridico', eventType: 'prazo_interno',
    title: 'Tarefa · Preparar manifestação', startsAt: new Date(now.getTime() + 4 * 60 * 60 * 1000).toISOString(), endsAt: null,
    matterId: 'matter-1', matterLabel: 'CASO-AGENDA-1', clientLabel: 'Cliente Agenda', taskId: 'task-1',
    responsibleUserId: 'owner-1', responsibleLabel: 'Responsável Agenda', status: 'in_progress', isInviolable: false,
    location: null, meetingUrl: null, dias: null, memoriaCalculo: null,
  },
  {
    id: 'task-participant-due:participant-1', source: 'task_participant_due', sourceId: 'participant-1', natureza: 'juridico', eventType: 'prazo_interno',
    title: 'Prazo do executor · Preparar manifestação', startsAt: new Date(now.getTime() + 5 * 60 * 60 * 1000).toISOString(), endsAt: null,
    matterId: 'matter-1', matterLabel: 'CASO-AGENDA-1', clientLabel: 'Cliente Agenda', taskId: 'task-1', participantRole: 'assignee',
    responsibleUserId: 'assignee-1', responsibleLabel: 'Executora Agenda', status: 'assigned', isInviolable: false,
    location: null, meetingUrl: null, dias: null, memoriaCalculo: null,
  },
  {
    id: 'task-review-due:task-1', source: 'task_review_due', sourceId: 'task-1', natureza: 'juridico', eventType: 'revisao',
    title: 'Revisão · Preparar manifestação', startsAt: new Date(now.getTime() + 6 * 60 * 60 * 1000).toISOString(), endsAt: null,
    matterId: 'matter-1', matterLabel: 'CASO-AGENDA-1', clientLabel: 'Cliente Agenda', taskId: 'task-1', participantRole: 'reviewer',
    responsibleUserId: 'reviewer-1', responsibleLabel: 'Revisora Agenda', status: 'pending', isInviolable: false,
    location: null, meetingUrl: null, dias: null, memoriaCalculo: null,
  },
];

function renderAgenda() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/app/agenda']}>
        <AgendaPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** "Lista completa da agenda", aba "Todos" — não sofre com o limite
 * visual do calendário nem com a janela "próximos"/"histórico". */
async function fullAgendaList() {
  const panel = screen.getByLabelText('Lista completa da agenda');
  fireEvent.click(within(panel).getByRole('button', { name: 'Todos' }));
  await waitFor(() => expect(within(panel).getByRole('button', { name: 'Todos' })).toHaveAttribute('aria-pressed', 'true'));
  return panel;
}

describe('Agenda unificada — toggle Tudo/Comercial/Jurídico e eventos jurídicos', () => {
  beforeEach(() => {
    vi.spyOn(klmApi, 'meetings').mockResolvedValue([commercialMeeting, hearingMeeting]);
    vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    vi.spyOn(klmApi, 'runtime').mockResolvedValue(runtime);
    vi.spyOn(klmApi, 'dailyBriefing').mockImplementation(async (day) => dailyBriefing(day));
    vi.spyOn(klmApi, 'unifiedAgenda').mockResolvedValue([fatalDeadlineEvent, ...taskEvents]);
    vi.spyOn(adminService, 'fetchTeamMembers').mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('lists commercial meetings, legal meetings, and the confirmed fatal deadline from the unified read-model together under "Tudo"', async () => {
    renderAgenda();
    await screen.findByRole('heading', { name: 'Agenda' });
    fireEvent.click(screen.getByRole('button', { name: 'Tudo' }));

    const panel = await fullAgendaList();
    expect(await within(panel).findByText(/prospecção Zeta/)).toBeInTheDocument();
    expect(within(panel).getByText(/Audiência de instrução processo Beta/)).toBeInTheDocument();
    expect(within(panel).getByText(/0700001-11\.2026\.8\.07\.0001/)).toBeInTheDocument();
  });

  it('projects the general, participant, and review task clocks with the real case and client labels', async () => {
    renderAgenda();
    await screen.findByRole('heading', { name: 'Agenda' });
    fireEvent.click(screen.getByRole('button', { name: 'Tudo' }));

    const panel = await fullAgendaList();
    expect(within(panel).getAllByText('Cliente Agenda · CASO-AGENDA-1')).toHaveLength(3);
    expect(within(panel).getByText('Tarefa · Preparar manifestação')).toBeInTheDocument();
    expect(within(panel).getByText('Prazo do executor · Preparar manifestação')).toBeInTheDocument();
    expect(within(panel).getByText('Revisão · Preparar manifestação')).toBeInTheDocument();
    expect(within(panel).getAllByText('Responsável Agenda')).toHaveLength(2);
    expect(within(panel).getAllByText('Executora Agenda')).toHaveLength(2);
    expect(within(panel).getAllByText('Revisora Agenda')).toHaveLength(2);
    expect(within(panel).getAllByText('Tarefas')).toHaveLength(6);
    expect(within(panel).getAllByText('Tarefa persistida')).toHaveLength(3);
  });

  it('the Comercial toggle hides legal-natured items and the Jurídico toggle hides commercial ones', async () => {
    renderAgenda();
    await screen.findByRole('heading', { name: 'Agenda' });

    fireEvent.click(screen.getByRole('button', { name: 'Comercial' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Comercial' })).toHaveAttribute('aria-pressed', 'true'));
    let panel = await fullAgendaList();
    expect(within(panel).getByText(/prospecção Zeta/)).toBeInTheDocument();
    expect(within(panel).queryByText(/Audiência de instrução processo Beta/)).not.toBeInTheDocument();
    expect(within(panel).queryByText(/0700001-11\.2026\.8\.07\.0001/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Jurídico' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Jurídico' })).toHaveAttribute('aria-pressed', 'true'));
    panel = await fullAgendaList();
    expect(within(panel).queryByText(/prospecção Zeta/)).not.toBeInTheDocument();
    expect(within(panel).getByText(/Audiência de instrução processo Beta/)).toBeInTheDocument();
    expect(within(panel).getByText(/0700001-11\.2026\.8\.07\.0001/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Tudo' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Tudo' })).toHaveAttribute('aria-pressed', 'true'));
    panel = await fullAgendaList();
    expect(within(panel).getByText(/prospecção Zeta/)).toBeInTheDocument();
    expect(within(panel).getByText(/Audiência de instrução processo Beta/)).toBeInTheDocument();
  });

  it('offers the 7 event types and the natureza selector when creating a new event, with counting fields for prazos', async () => {
    renderAgenda();
    await screen.findByRole('heading', { name: 'Agenda' });
    fireEvent.click(screen.getByRole('button', { name: 'Nova reunião' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('Tipo de evento')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Natureza do evento')).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('Dias de contagem')).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByLabelText('Tipo de evento'));
    fireEvent.click(await screen.findByRole('option', { name: 'Prazo interno' }));

    expect(await within(dialog).findByText('Contagem do prazo (interno × fatal)')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Dias de contagem')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Tipo de contagem do prazo')).toBeInTheDocument();
  });
});
