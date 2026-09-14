import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/services/http';
import * as http from '@/services/http';
import {
  classifyWorkspaceCapabilityManifestError,
  fetchWorkspaceCapabilityManifest,
  getWorkspaceCapability,
  isWorkspaceCapabilityUsable,
  isWorkspaceCapabilityVisible,
} from '@/services/workspace-capabilities';
import type { EffectiveCapabilityManifest } from '@/services/platform-commercial-catalog';

const manifest = {
  workspaceId: 'workspace-1',
  contractId: 'contract-1',
  contractVersion: 1,
  generatedAt: '2026-07-30T15:00:00Z',
  capabilities: {
    active_tasks: {
      key: 'active_tasks',
      contracted: true,
      enabled: true,
      permitted: false,
      limit: 500,
      graceLimit: null,
      used: 500,
      remaining: 0,
      unit: 'tasks',
      period: 'concurrent',
      enforcement: 'hard',
      sources: [],
      denialReason: 'quota_exhausted',
      upgradeOffer: 'crm_advanced',
    },
  },
} as unknown as EffectiveCapabilityManifest;

afterEach(() => vi.restoreAllMocks());

describe('workspace effective capability manifest', () => {
  it('loads only the active workspace manifest from the tenant-safe endpoint', async () => {
    const apiFetch = vi.spyOn(http, 'apiFetch').mockResolvedValue(manifest);

    await expect(fetchWorkspaceCapabilityManifest()).resolves.toBe(manifest);

    expect(apiFetch).toHaveBeenCalledWith(
      '/api/v1/workspaces/capability-manifest',
      { signal: undefined },
    );
  });

  it('keeps exhausted capabilities visible for history while blocking new mutations', () => {
    const capability = getWorkspaceCapability(manifest, 'active_tasks');

    expect(isWorkspaceCapabilityVisible(capability)).toBe(true);
    expect(isWorkspaceCapabilityUsable(capability)).toBe(false);
    expect(capability?.denialReason).toBe('quota_exhausted');
    expect(capability?.remaining).toBe(0);
  });

  it('distinguishes missing commercial configuration, forbidden access, and outages', () => {
    expect(classifyWorkspaceCapabilityManifestError(new ApiError(
      409,
      'not configured',
      { code: 'COMMERCIAL-MANIFEST-NOT-CONFIGURED' },
    ))).toBe('not_configured');
    expect(classifyWorkspaceCapabilityManifestError(new ApiError(403, 'forbidden'))).toBe('forbidden');
    expect(classifyWorkspaceCapabilityManifestError(new Error('network'))).toBe('unavailable');
  });
});
