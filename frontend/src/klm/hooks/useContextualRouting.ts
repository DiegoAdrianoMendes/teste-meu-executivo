import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

export type ContextualPageProps = {
  embedded?: boolean;
  onNavigate?: (path: string) => void;
  contextPath?: string;
};

export function useContextualNavigate({
  embedded = false,
  onNavigate,
}: ContextualPageProps = {}) {
  const navigate = useNavigate();

  return useCallback((path: string) => {
    if (embedded) {
      onNavigate?.(path);
      return;
    }
    navigate(path);
  }, [embedded, navigate, onNavigate]);
}

export function useContextualSearchParams({
  embedded = false,
  contextPath,
}: Pick<ContextualPageProps, 'embedded' | 'contextPath'> = {}) {
  const [routeSearchParams, setRouteSearchParams] = useSearchParams();
  const routeSearch = routeSearchParams.toString();
  const contextualSearch = useMemo(() => {
    if (!embedded || !contextPath) return routeSearch;
    return new URL(contextPath, 'http://klm.local').searchParams.toString();
  }, [contextPath, embedded, routeSearch]);
  const [localSearchParams, setLocalSearchParams] = useState(
    () => new URLSearchParams(contextualSearch),
  );

  useEffect(() => {
    if (embedded) setLocalSearchParams(new URLSearchParams(contextualSearch));
  }, [contextualSearch, embedded]);

  const searchParams = useMemo(
    () => embedded ? localSearchParams : routeSearchParams,
    [embedded, localSearchParams, routeSearchParams],
  );

  const setSearchParams = useCallback((next: URLSearchParams) => {
    const stableNext = new URLSearchParams(next);
    if (embedded) {
      setLocalSearchParams(stableNext);
      return;
    }
    setRouteSearchParams(stableNext, { replace: true });
  }, [embedded, setRouteSearchParams]);

  return [searchParams, setSearchParams] as const;
}
