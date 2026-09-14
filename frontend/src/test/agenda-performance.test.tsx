/** @vitest-environment jsdom */
import { act, cleanup, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { klmApi, type KlmDailyBriefing, type KlmFunnel, type KlmRuntimeStatus } from '@/klm/api';
import AgendaPage from '@/klm/pages/AgendaPage';
import * as adminService from '@/services/admin';
import { AppProvider } from '@/store/app-context';

vi.mock('@/klm/components/BookingLinkDialog', () => ({ BookingLinkDialog: () => null }));
vi.mock('@/klm/components/NativeCRMOperationsPanel', () => ({ NativeCRMOperationsPanel: () => null }));
vi.mock('@/klm/components/NativeModuleBar', () => ({ NativeModuleBar: () => null }));

const funnel: KlmFunnel = {
  boardVersion: 1,
  stages: [],
  cards: [],
  metrics: {},
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

const dailyBriefing: KlmDailyBriefing = {
  day: '2026-07-19',
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
  generatedAt: '2026-07-19T12:00:00Z',
};

function renderAgenda(queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, gcTime: 0 },
    mutations: { retry: false },
  },
})) {
  return {
    queryClient,
    view: render(
      <QueryClientProvider client={queryClient}>
        <AppProvider>
          <MemoryRouter initialEntries={['/app/agenda']}>
            <AgendaPage />
          </MemoryRouter>
        </AppProvider>
      </QueryClientProvider>,
    ),
  };
}

async function flushEffects(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('Agenda visibility performance', () => {
  let visibility: DocumentVisibilityState;

  beforeEach(() => {
    vi.useFakeTimers();
    visibility = 'visible';
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
    vi.spyOn(klmApi, 'meetings').mockResolvedValue([]);
    vi.spyOn(klmApi, 'followUps').mockResolvedValue([]);
    vi.spyOn(klmApi, 'funnel').mockResolvedValue(funnel);
    vi.spyOn(klmApi, 'runtime').mockResolvedValue(runtime);
    vi.spyOn(klmApi, 'dailyBriefing').mockResolvedValue(dailyBriefing);
    vi.spyOn(klmApi, 'unifiedAgenda').mockResolvedValue([]);
    vi.spyOn(adminService, 'fetchTeamMembers').mockResolvedValue([]);
    delete document.documentElement.dataset.klmRealtimeStatus;
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('does not query while hidden and resumes each Agenda query only once', async () => {
    visibility = 'hidden';
    renderAgenda();
    await flushEffects();

    expect(klmApi.meetings).not.toHaveBeenCalled();
    expect(klmApi.followUps).not.toHaveBeenCalled();
    expect(klmApi.funnel).not.toHaveBeenCalled();
    expect(klmApi.runtime).not.toHaveBeenCalled();
    expect(klmApi.dailyBriefing).not.toHaveBeenCalled();
    expect(adminService.fetchTeamMembers).not.toHaveBeenCalled();

    visibility = 'visible';
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(klmApi.meetings).toHaveBeenCalledTimes(1);
    expect(klmApi.followUps).toHaveBeenCalledTimes(1);
    expect(klmApi.funnel).toHaveBeenCalledTimes(1);
    expect(klmApi.runtime).toHaveBeenCalledTimes(1);
    expect(klmApi.dailyBriefing).toHaveBeenCalledTimes(1);
    expect(adminService.fetchTeamMembers).toHaveBeenCalledTimes(1);

    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await Promise.resolve();
    });
    expect(klmApi.meetings).toHaveBeenCalledTimes(1);
    expect(klmApi.followUps).toHaveBeenCalledTimes(1);

    visibility = 'hidden';
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(klmApi.meetings).toHaveBeenCalledTimes(1);
    expect(klmApi.followUps).toHaveBeenCalledTimes(1);

    visibility = 'visible';
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(klmApi.meetings).toHaveBeenCalledTimes(2);
    expect(klmApi.followUps).toHaveBeenCalledTimes(2);
    expect(klmApi.funnel).toHaveBeenCalledTimes(2);
    expect(klmApi.runtime).toHaveBeenCalledTimes(2);
    expect(klmApi.dailyBriefing).toHaveBeenCalledTimes(2);
    expect(adminService.fetchTeamMembers).toHaveBeenCalledTimes(2);
  });

  it('debounces meeting-event invalidations into one refresh batch', async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });
    const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries');
    renderAgenda(queryClient);
    await flushEffects();
    invalidateQueries.mockClear();

    act(() => {
      window.dispatchEvent(new CustomEvent('klm:realtime', { detail: { event: 'MEETING_CREATED', data: { meeting_id: 'meeting-1' } } }));
      window.dispatchEvent(new CustomEvent('klm:realtime', { detail: { event: 'MEETING_UPDATED', data: { meeting_id: 'meeting-1' } } }));
      window.dispatchEvent(new CustomEvent('klm:realtime', { detail: { event: 'MEETING_CANCELLED', data: { meeting_id: 'meeting-1' } } }));
      window.dispatchEvent(new CustomEvent('klm:realtime', { detail: { event: 'MEETING_DELETED', data: { meeting_id: 'meeting-1' } } }));
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(499);
    });
    expect(invalidateQueries).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(invalidateQueries).toHaveBeenCalledTimes(2);
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['klm', 'meetings'] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['klm', 'daily-briefing'] });
  });
});
