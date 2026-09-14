import { describe, expect, it } from 'vitest';

import { API_BASE, resolveApiHttpUrl, resolveWebSocketBase } from '@/services/api-origin';

describe('same-origin API profile', () => {
  it('uses a relative API base when production did not opt into another origin', () => {
    expect(API_BASE).toBe('');
  });

  it('derives secure and local websocket origins without an extra host', () => {
    expect(resolveWebSocketBase('https://hub.klm.test')).toBe('wss://hub.klm.test');
    expect(resolveWebSocketBase('http://127.0.0.1:4173')).toBe('ws://127.0.0.1:4173');
  });

  it('copies API endpoints from the configured backend origin', () => {
    expect(resolveApiHttpUrl('/api/v1/webhooks/example', 'http://127.0.0.1:4173')).toBe(
      'http://127.0.0.1:4173/api/v1/webhooks/example',
    );
    expect(resolveApiHttpUrl('/api/v1/webhooks/example', 'http://127.0.0.1:4173', 'http://127.0.0.1:8010')).toBe(
      'http://127.0.0.1:8010/api/v1/webhooks/example',
    );
  });
});
