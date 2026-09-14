import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { fetchBlackboxSessionManifest } from '@/services/platform';
import type { AuthUser, BlackboxSessionManifest } from '@/types';

interface BlackboxManifestContextValue {
  manifest: BlackboxSessionManifest;
  effectiveUser: AuthUser;
}

const BlackboxManifestContext = createContext<BlackboxManifestContextValue | null>(null);

export function BlackboxManifestProvider({
  user,
  children,
}: {
  user: AuthUser;
  children: ReactNode;
}) {
  const query = useQuery({
    queryKey: ['blackbox', 'session-manifest', user.id],
    queryFn: fetchBlackboxSessionManifest,
    staleTime: 30_000,
    retry: 1,
  });
  const value = useMemo<BlackboxManifestContextValue | null>(() => {
    if (!query.data) return null;
    return {
      manifest: query.data,
      effectiveUser: {
        ...user,
        isPlatformRoot: query.data.isPlatformRoot,
        platformRolePackage: query.data.rolePackage,
        platformPermissions: query.data.permissions,
      },
    };
  }, [query.data, user]);

  if (query.isLoading || !value) {
    if (query.isError) {
      return (
        <main className="grid min-h-screen place-items-center bg-background px-6">
          <section className="max-w-md rounded-xl border bg-card p-6 text-center shadow-sm">
            <h1 className="text-lg font-semibold">Capacidades do Blackbox indisponíveis</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              O painel não será aberto sem confirmar as permissões efetivas no backend.
            </p>
            <Button className="mt-5" onClick={() => { void query.refetch(); }}>
              Tentar novamente
            </Button>
          </section>
        </main>
      );
    }
    return (
      <main className="grid min-h-screen place-items-center bg-background px-6">
        <p className="text-sm text-muted-foreground" role="status">
          Confirmando capacidades da sessão...
        </p>
      </main>
    );
  }

  return (
    <BlackboxManifestContext.Provider value={value}>
      {children}
    </BlackboxManifestContext.Provider>
  );
}

// The provider and its hook intentionally share this module so the session
// contract cannot drift between routing and action authorization.
// eslint-disable-next-line react-refresh/only-export-components
export function useBlackboxManifest(): BlackboxManifestContextValue {
  const context = useContext(BlackboxManifestContext);
  if (!context) throw new Error('useBlackboxManifest must be used inside BlackboxManifestProvider');
  return context;
}
