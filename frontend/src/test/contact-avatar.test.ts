import { describe, expect, it } from 'vitest';

import { resolveContactAvatar } from '@/klm/contact-avatar';

describe('contact avatar resolution', () => {
  it('prefers the official WhatsApp photo across a unified identity', () => {
    expect(resolveContactAvatar({
      profileAvatars: {
        instagram: { url: 'https://cdn.example.test/instagram.jpg', sourceChannel: 'instagram' },
        whatsapp: { url: 'https://cdn.example.test/whatsapp.jpg', sourceChannel: 'whatsapp' },
      },
    }, 'instagram')).toEqual({
      url: 'https://cdn.example.test/whatsapp.jpg',
      source: 'whatsapp',
    });
  });

  it('rejects unsafe image protocols and leaves the initials fallback active', () => {
    expect(resolveContactAvatar({ profileAvatarUrl: 'javascript:alert(1)' }, 'whatsapp')).toBeNull();
  });
});
