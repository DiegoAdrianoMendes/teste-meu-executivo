import { useEffect, useState } from 'react';
import { Clock3, LogOut, ShieldAlert } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { SUPPORT_ACCESS_SCOPE_OPTIONS } from '@/services/support-access';
import type { SupportModeSession } from '@/support-mode/session';

function remainingTime(expiresAt: string, now: number): string {
  const seconds = Math.max(0, Math.ceil((new Date(expiresAt).getTime() - now) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes > 0 ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

function scopeLabel(scope: SupportModeSession['scopes'][number]): string {
  return SUPPORT_ACCESS_SCOPE_OPTIONS.find((option) => option.value === scope)?.label ?? scope;
}

export function SupportModeBanner({
  session,
  onExit,
}: {
  session: SupportModeSession;
  onExit: () => Promise<void>;
}) {
  const [now, setNow] = useState(() => Date.now());
  const [exiting, setExiting] = useState(false);

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, []);

  return (
    <header className="sticky top-0 z-50 border-b border-amber-400 bg-amber-50 text-amber-950 shadow-sm dark:border-amber-800 dark:bg-amber-950 dark:text-amber-50">
      <div className="mx-auto flex max-w-[1480px] flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <ShieldAlert className="h-4 w-4" aria-hidden="true" />
            <strong className="text-sm">
              {session.sessionType === 'root_direct'
                ? `Super Admin — acesso direto: ${session.workspaceName}`
                : `Modo suporte aprovado: ${session.workspaceName}`}
            </strong>
            <Badge variant="outline" className="border-amber-500 text-inherit">
              <Clock3 className="mr-1 h-3 w-3" />{remainingTime(session.expiresAt, now)}
            </Badge>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Escopos ativos do modo suporte">
            {session.scopes.map((scope) => (
              <span key={scope} className="rounded border border-amber-300 px-2 py-0.5 text-[11px] dark:border-amber-800">
                {scopeLabel(scope)}
              </span>
            ))}
          </div>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="shrink-0 border-amber-500 bg-transparent hover:bg-amber-100 dark:hover:bg-amber-900"
          disabled={exiting}
          onClick={() => {
            setExiting(true);
            void onExit().catch(() => setExiting(false));
          }}
        >
          <LogOut className="mr-1.5 h-3.5 w-3.5" />
          {session.sessionType === 'root_direct' ? 'Voltar ao Blackbox' : 'Sair do modo suporte'}
        </Button>
      </div>
    </header>
  );
}
