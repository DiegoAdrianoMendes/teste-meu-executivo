import { describe, expect, it } from 'vitest';

import { requireMetaGraphApiVersion } from '@/lib/meta-graph-version';

describe('Meta Graph API release contract', () => {
  it('accepts the pinned v26 release', () => {
    expect(requireMetaGraphApiVersion('v26.0')).toBe('v26.0');
  });

  it.each(['', '25', 'v25', 'v0.0', 'latest'])('rejects an invalid version: %s', (value) => {
    expect(() => requireMetaGraphApiVersion(value)).toThrow(/v<major>\.0/);
  });
});
