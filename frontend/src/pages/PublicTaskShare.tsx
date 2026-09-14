import { useQuery } from '@tanstack/react-query';
import { CalendarClock, LoaderCircle, ShieldCheck } from 'lucide-react';
import { useEffect, useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { redeemPublicTaskExternalShare } from '@/services/task-external-shares';

const statusLabels: Record<string, string> = {
  pending: 'Pendente',
  in_progress: 'Em andamento',
  blocked: 'Bloqueada',
  done: 'Concluída',
  cancelled: 'Cancelada',
};

function formatDate(value: string | null): string {
  if (!value) return 'Não definido';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'long',
    timeStyle: 'short',
  }).format(new Date(value));
}

export default function PublicTaskShare() {
  const location = useLocation();
  const token = useMemo(() => {
    const fragment = location.hash.startsWith('#') ? location.hash.slice(1) : '';
    return new URLSearchParams(fragment).get('token') ?? '';
  }, [location.hash]);
  useEffect(() => {
    const existing = document.head.querySelector<HTMLMetaElement>('meta[name="referrer"]');
    const previous = existing?.content ?? null;
    const meta = existing ?? document.createElement('meta');
    if (!existing) {
      meta.name = 'referrer';
      document.head.appendChild(meta);
    }
    meta.content = 'no-referrer';
    return () => {
      if (existing) meta.content = previous ?? '';
      else meta.remove();
    };
  }, []);
  const shareQuery = useQuery({
    queryKey: ['public-task-share', token],
    queryFn: () => redeemPublicTaskExternalShare(token),
    enabled: token.length >= 64,
    retry: false,
  });

  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 px-4 py-10 text-slate-950">
      <section className="w-full max-w-2xl rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <div className="flex items-center gap-2 text-sm font-semibold text-blue-700">
          <ShieldCheck className="h-5 w-5" />Meu Executivo · compartilhamento seguro
        </div>
        {shareQuery.isPending && (
          <div className="flex items-center gap-2 py-16 text-sm text-slate-600" role="status">
            <LoaderCircle className="h-5 w-5 animate-spin" />Validando link…
          </div>
        )}
        {(shareQuery.isError || !token) && (
          <div className="py-12" role="alert">
            <h1 className="text-xl font-semibold">Link indisponível</h1>
            <p className="mt-2 text-sm leading-6 text-slate-600">O link é inválido, expirou ou foi revogado. Solicite um novo acesso ao escritório responsável.</p>
          </div>
        )}
        {shareQuery.data && (
          <div className="mt-8 space-y-6">
            <div><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Tarefa jurídica</p><h1 className="mt-2 text-2xl font-semibold">{shareQuery.data.title}</h1></div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl bg-slate-50 p-4"><p className="text-xs text-slate-500">Status</p><p className="mt-1 font-medium">{statusLabels[shareQuery.data.status] ?? shareQuery.data.status}</p></div>
              <div className="rounded-xl bg-slate-50 p-4"><p className="flex items-center gap-1 text-xs text-slate-500"><CalendarClock className="h-3.5 w-3.5" />Prazo</p><p className="mt-1 font-medium">{formatDate(shareQuery.data.dueAt)}</p></div>
            </div>
            {shareQuery.data.safeContext && <div className="rounded-xl border border-blue-100 bg-blue-50 p-4"><p className="text-xs font-semibold text-blue-800">Contexto compartilhado pelo escritório</p><p className="mt-2 whitespace-pre-wrap text-sm leading-6">{shareQuery.data.safeContext}</p></div>}
            <p className="border-t pt-4 text-xs text-slate-500">Este acesso é somente leitura e expira em {formatDate(shareQuery.data.expiresAt)}. Comentários, histórico, participantes e anexos internos não são compartilhados.</p>
          </div>
        )}
      </section>
    </main>
  );
}
