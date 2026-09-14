export type AgendaRealtimeStatus = 'connecting' | 'connected' | 'reconnecting' | 'offline';

export const FOLLOW_UP_RECONCILIATION_MS = 30_000;

export function meetingReconciliationMs(status: AgendaRealtimeStatus) {
  return status === 'connected' ? 60_000 : 15_000;
}
