import type { KlmMatterDetail, KlmMessage } from '@/klm/api';

type TimelineMessage = Pick<KlmMessage, 'id' | 'occurredAt' | 'body' | 'authorLabel'>;
type MatterTimelineEvent = KlmMatterDetail['timeline'][number];

export type TimelineRow = {
  key: string;
  time: string;
  action: string;
  agent: string;
  detail?: string;
};

type DatedTimelineRow = TimelineRow & {
  occurredAt: number;
  insertionOrder: number;
};

function timestampValue(value: string): number {
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function formatTime(value: string): string {
  const timestamp = timestampValue(value);
  if (!timestamp) return 'Horário não informado';
  return new Date(timestamp).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

function eventTypeLabel(value: string): string {
  const normalized = value.replaceAll('_', ' ').trim();
  return normalized || 'Evento do caso';
}

export function buildTimelineRows(
  messages?: ReadonlyArray<TimelineMessage>,
  matterEvents?: ReadonlyArray<MatterTimelineEvent>,
): TimelineRow[] {
  const messageRows: DatedTimelineRow[] = (messages ?? [])
    .filter((message) => message.body.trim())
    .map((message, index) => ({
      key: message.id,
      time: formatTime(message.occurredAt),
      action: message.body,
      agent: message.authorLabel,
      occurredAt: timestampValue(message.occurredAt),
      insertionOrder: index,
    }));
  const eventRows: DatedTimelineRow[] = (matterEvents ?? [])
    .filter((event) => event.title.trim())
    .map((event, index) => ({
      key: `matter-event:${event.id}`,
      time: formatTime(event.timestamp),
      action: event.title,
      agent: eventTypeLabel(event.type),
      detail: event.description?.trim() || undefined,
      occurredAt: timestampValue(event.timestamp),
      insertionOrder: messageRows.length + index,
    }));

  return [...messageRows, ...eventRows]
    .sort((left, right) => left.occurredAt - right.occurredAt || left.insertionOrder - right.insertionOrder)
    .slice(-7)
    .map(({ occurredAt: _occurredAt, insertionOrder: _insertionOrder, ...row }) => row);
}
