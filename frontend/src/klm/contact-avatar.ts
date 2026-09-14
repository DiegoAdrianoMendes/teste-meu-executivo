import type { KlmOmnichannelChannel } from '@/klm/api';

export type ContactAvatarValue = {
  url: string;
  source: string;
};

const SOURCE_PRIORITY: Record<string, number> = {
  whatsapp: 0,
  instagram: 10,
  messenger: 20,
  facebook: 20,
  tiktok: 30,
  webchat: 40,
  form: 50,
  email: 60,
  audio: 70,
  internal: 80,
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function safeImageUrl(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const parsed = new URL(value.trim());
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function candidate(value: unknown, fallbackSource: string): ContactAvatarValue | null {
  const directUrl = safeImageUrl(value);
  if (directUrl) return { url: directUrl, source: fallbackSource };
  const record = asRecord(value);
  if (!record) return null;
  const url = safeImageUrl(
    record.url
      ?? record.avatarUrl
      ?? record.profileAvatarUrl
      ?? record.profilePhotoUrl
      ?? record.pictureUrl,
  );
  if (!url) return null;
  return {
    url,
    source: String(record.sourceChannel ?? record.channel ?? record.source ?? fallbackSource).toLocaleLowerCase('pt-BR'),
  };
}

function priority(source: string, primaryChannel: KlmOmnichannelChannel): number {
  const normalized = source.toLocaleLowerCase('pt-BR');
  return SOURCE_PRIORITY[normalized] ?? (normalized === primaryChannel ? 35 : 90);
}

export function resolveContactAvatar(
  metadata: Record<string, unknown>,
  primaryChannel: KlmOmnichannelChannel,
): ContactAvatarValue | null {
  const contact = asRecord(metadata.contact);
  const canonical = asRecord(metadata.canonical);
  const candidates: ContactAvatarValue[] = [];
  const append = (value: unknown, source: string = primaryChannel) => {
    const resolved = candidate(value, source);
    if (resolved && !candidates.some((item) => item.url === resolved.url && item.source === resolved.source)) {
      candidates.push(resolved);
    }
  };

  append(metadata.profileAvatar);
  append(metadata.profileAvatarUrl);
  append(metadata.avatarUrl);
  append(metadata.profilePhotoUrl);
  append(contact?.profileAvatar);
  append(contact?.avatarUrl);
  append(canonical?.profileAvatar);

  if (Array.isArray(metadata.profileAvatars)) {
    metadata.profileAvatars.forEach((value) => append(value));
  } else {
    const profiles = asRecord(metadata.profileAvatars);
    Object.entries(profiles ?? {}).forEach(([source, value]) => append(value, source));
  }

  return candidates.sort((left, right) => priority(left.source, primaryChannel) - priority(right.source, primaryChannel))[0] ?? null;
}
