import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';

import { DesbLogo } from '@/components/ui/DesbLogo';
import { usePageMetadata } from '@/hooks/use-page-metadata';
import { fetchPublicLegal } from '@/services/public-site';

export default function Privacidade() {
  const query = useQuery({
    queryKey: ['public-legal', 'privacy'],
    queryFn: () => fetchPublicLegal('privacy'),
    staleTime: 60_000,
  });

  usePageMetadata(query.data?.seo);

  if (!query.data) {
    return (
      <div className="min-h-screen bg-background">
        <header className="border-b border-border">
          <div className="mx-auto flex h-14 max-w-3xl items-center px-4">
            <Link to="/">
              <DesbLogo variant="full" size={48} />
            </Link>
          </div>
        </header>
        <main className="mx-auto max-w-3xl px-4 py-12">
          <div className="h-8 w-40 animate-pulse rounded bg-muted" />
          <div className="mt-6 h-6 w-64 animate-pulse rounded bg-muted" />
          <div className="mt-8 space-y-3">
            {Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-16 animate-pulse rounded bg-muted" />)}
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-3xl items-center gap-3 px-4">
          <Link to="/">
            <DesbLogo variant="full" size={48} />
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-12">
        <Link to="/" className="mb-8 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" /> Voltar
        </Link>
        <h1 className="mb-2 text-3xl font-bold">{query.data.title}</h1>
        <p className="mb-8 text-sm text-muted-foreground">Última atualização: {query.data.updatedLabel}</p>

        <div className="space-y-6">
          {query.data.sections.map((section) => (
            <section key={section.heading}>
              <h2 className="mb-2 text-lg font-semibold">{section.heading}</h2>
              <p className="leading-relaxed text-muted-foreground">{section.body}</p>
            </section>
          ))}
        </div>
      </main>
    </div>
  );
}
