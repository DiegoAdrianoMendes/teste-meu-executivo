import { createIdempotencyKey } from '@/services/identity';

const pendingMeetingCreateKeys = new Map<string, string>();

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, item]) => item !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)]),
    );
  }
  return value;
}

/**
 * Keep one key for a logical meeting submission until its response is known.
 *
 * A network timeout leaves the key cached so a user retry replays the durable
 * server receipt.  A successful response consumes it, allowing a later,
 * intentional appointment with identical fields to receive a fresh key.
 */
export async function withMeetingCreateIdempotency<T>(
  payload: unknown,
  request: (idempotencyKey: string) => Promise<T>,
): Promise<T> {
  const fingerprint = JSON.stringify(canonicalize(payload));
  const idempotencyKey = pendingMeetingCreateKeys.get(fingerprint) ?? createIdempotencyKey();
  pendingMeetingCreateKeys.set(fingerprint, idempotencyKey);
  // If this await rejects, the deletion below is intentionally skipped: the
  // server may have committed before the response was interrupted, and only
  // replay with this same key can prove the outcome.
  const result = await request(idempotencyKey);
  if (pendingMeetingCreateKeys.get(fingerprint) === idempotencyKey) {
    pendingMeetingCreateKeys.delete(fingerprint);
  }
  return result;
}
