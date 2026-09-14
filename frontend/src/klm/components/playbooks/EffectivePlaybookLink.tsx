import { useQuery } from '@tanstack/react-query';
import { BookOpenCheck, ChevronRight, LoaderCircle, ShieldAlert } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { KlmPill } from '@/klm/components/Primitives';
import {
  normalizeEffectivePlaybookSelectors,
  type EffectivePlaybookSelectors,
} from '@/klm/playbooks/effective-selectors';
import { autonomyLabel } from '@/klm/playbooks/presentation';
import { cn } from '@/lib/utils';
import { ApiError } from '@/services/http';
import { getEffectivePlaybook } from '@/services/playbooks';

export function EffectivePlaybookLink({
  selectors,
  className,
  label = 'Política efetiva',
  showAutonomy = true,
  onNavigate,
}: {
  selectors: EffectivePlaybookSelectors;
  className?: string;
  label?: string;
  showAutonomy?: boolean;
  onNavigate?: (path: string) => void;
}) {
  const navigate = useNavigate();
  const normalized = normalizeEffectivePlaybookSelectors(selectors);
  const query = useQuery({
    queryKey: [
      'playbooks',
      'effective',
      normalized.area ?? '',
      normalized.channel ?? '',
      normalized.stage ?? '',
      normalized.agent ?? '',
      normalized.offer ?? '',
    ],
    queryFn: () => getEffectivePlaybook(normalized),
    retry: false,
    staleTime: 60_000,
  });
  const notFound = query.error instanceof ApiError && query.error.status === 404;
  const destination = query.data
    ? `/app/playbooks/${query.data.id}?tab=policy&requestChange=1`
    : '/app/playbooks?tab=policy';
  const version = query.data?.publishedVersion ?? query.data?.currentVersion;
  const state = query.isPending ? 'loading' : query.data ? 'resolved' : notFound ? 'unassigned' : 'unavailable';
  const title = query.data
    ? `Abrir ${query.data.name}, versão ${version}`
    : notFound
      ? 'Abrir Playbooks para publicar uma política compatível'
      : 'Abrir Playbooks para verificar a política';

  return (
    <button
      type="button"
      data-testid="effective-playbook-link"
      data-state={state}
      className={cn(
        'inline-flex min-h-9 max-w-full items-center gap-2 rounded-md border border-border bg-card px-2.5 py-1.5 text-left shadow-sm transition hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
      title={title}
      aria-label={title}
      onClick={() => onNavigate ? onNavigate(destination) : navigate(destination)}
    >
      {query.isPending ? (
        <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin text-[#0057d8]" />
      ) : query.data ? (
        <BookOpenCheck className="h-3.5 w-3.5 shrink-0 text-[#0057d8]" />
      ) : (
        <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-[#a53e3c]" />
      )}
      <span className="min-w-0">
        <span className="block text-[8px] font-semibold uppercase text-muted-foreground">{label}</span>
        <strong className="block truncate text-[9px] font-semibold text-foreground">
          {query.isPending
            ? 'Resolvendo versão publicada'
            : query.data
              ? `${query.data.name} · v${version}`
              : notFound
                ? 'Nenhuma versão publicada neste contexto'
                : 'Não foi possível consultar a política'}
        </strong>
      </span>
      {query.data && showAutonomy ? (
        <KlmPill tone="green" className="hidden shrink-0 sm:inline-flex">
          {autonomyLabel[query.data.autonomyMode]}
        </KlmPill>
      ) : null}
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
    </button>
  );
}
