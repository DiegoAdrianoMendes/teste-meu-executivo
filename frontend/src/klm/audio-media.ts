import { API_BASE } from '@/services/api-origin';

export function trustedAudioSource(
  value: unknown,
  apiBase = API_BASE,
) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const base = new URL(apiBase || window.location.origin, window.location.origin);
    const candidate = new URL(value.trim(), base);
    if (
      candidate.origin !== base.origin
      || candidate.pathname !== '/api/v1/local-storage/download'
      || !candidate.searchParams.get('ticket')
      || candidate.username
      || candidate.password
      || candidate.hash
    ) return null;
    return candidate.toString();
  } catch {
    return null;
  }
}
