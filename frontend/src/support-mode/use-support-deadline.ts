import { useEffect } from 'react';
import type { QueryClient, QueryKey } from '@tanstack/react-query';

import type { SupportAccessGrant } from '@/services/support-access';

export function useSupportAccessDeadline(
  queryClient: QueryClient,
  queryKey: QueryKey,
  grants: SupportAccessGrant[],
  onDeadline?: () => void,
): void {
  const nearestDeadline = grants.reduce<number | null>((nearest, grant) => {
    if (grant.state !== 'active' || !grant.expiresAt) return nearest;
    const deadline = new Date(grant.expiresAt).getTime();
    if (!Number.isFinite(deadline)) return nearest;
    return nearest === null || deadline < nearest ? deadline : nearest;
  }, null);

  useEffect(() => {
    if (nearestDeadline === null) return undefined;
    const delay = Math.max(0, nearestDeadline - Date.now());
    const timeout = window.setTimeout(() => {
      onDeadline?.();
      void queryClient.invalidateQueries({ queryKey });
    }, delay);
    return () => window.clearTimeout(timeout);
  }, [nearestDeadline, onDeadline, queryClient, queryKey]);
}
