/** Domínios funcionais expostos pela experiência nativa. */
export const KLM_NATIVE_DOMAINS = [
  'communications',
  'collaboration',
  'automation',
  'crm',
] as const;

export type KlmNativeDomain = (typeof KLM_NATIVE_DOMAINS)[number];

const SAFE_SEGMENT = /^[a-z0-9][a-z0-9._-]*$/i;

export function isKlmNativeDomain(value: string): value is KlmNativeDomain {
  return (KLM_NATIVE_DOMAINS as readonly string[]).includes(value);
}

export function nativeCapabilityRoute(
  domain: KlmNativeDomain,
  capability: string,
  sourceRoute?: string | null,
): string {
  if (!SAFE_SEGMENT.test(capability)) throw new Error('Capacidade inválida.');
  const source = sourceRoute ? new URL(sourceRoute, 'http://klm.local') : null;
  const query = source?.search ?? '';
  const hash = source?.hash ?? '';
  return `/app/capacidades/${domain}/${encodeURIComponent(capability)}${query}${hash}`;
}
