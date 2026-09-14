const configuredApiBase = (import.meta.env.VITE_API_URL || '').trim();

export const API_BASE = configuredApiBase.replace(/\/+$/, '');

export function resolveApiHttpUrl(
  path: string,
  origin: string,
  apiBase = API_BASE,
): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const base = apiBase.trim().replace(/\/+$/, '') || origin;
  return new URL(normalizedPath, base).toString();
}

export function resolveWebSocketBase(origin: string): string {
  const target = new URL(API_BASE || origin, origin);
  target.protocol = target.protocol === 'https:' ? 'wss:' : 'ws:';
  target.username = '';
  target.password = '';
  target.search = '';
  target.hash = '';
  target.pathname = target.pathname.replace(/\/+$/, '');
  return target.toString().replace(/\/$/, '');
}
