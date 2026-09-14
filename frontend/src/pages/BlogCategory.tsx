import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { PublicMediaFrame } from '@/components/public/PublicMediaFrame';
import { usePageMetadata } from '@/hooks/use-page-metadata';
import { useStructuredData } from '@/hooks/use-structured-data';
import { fetchPublicBlogCategory } from '@/services/public-site';

export default function BlogCategory() {
  const { slug = '' } = useParams<{ slug: string }>();
  const query = useQuery({
    queryKey: ['public-blog', 'category', slug],
    queryFn: () => fetchPublicBlogCategory(slug),
    staleTime: 60_000,
  });

  const payload = query.data;
  usePageMetadata(payload?.seo);

  const structuredData = useMemo(() => {
    if (!payload) return null;
    return {
      '@context': 'https://schema.org',
      '@type': 'CollectionPage',
      name: payload.category,
      description: payload.seo.description,
      url: payload.seo.canonicalUrl,
    };
  }, [payload]);

  useStructuredData(structuredData, 'blog-category');

  if (!payload) {
    return (
      <div className="min-h-screen bg-background">
        <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
          <div className="h-12 w-52 animate-pulse rounded bg-muted" />
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-72 animate-pulse rounded-3xl bg-muted" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#f5f7fb]">
      <header className="border-b border-slate-200 bg-white/85 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
          <Link to="/">
            <DesbLogo variant="full" size={54} />
          </Link>
          <Button variant="ghost" asChild>
            <Link to="/blog">Voltar ao blog</Link>
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        <Link to="/blog" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" />
          Todos os artigos
        </Link>

        <section className="mt-6 rounded-[2rem] border border-slate-200 bg-white px-6 py-8 shadow-[0_24px_70px_rgba(7,19,28,0.08)] md:px-8">
          <Badge className="bg-primary/10 text-primary hover:bg-primary/10">Categoria</Badge>
          <h1 className="mt-4 text-4xl font-black tracking-tight text-slate-950">{payload.category}</h1>
          <p className="mt-3 max-w-3xl text-lg leading-relaxed text-slate-600">{payload.seo.description}</p>
        </section>

        <section className="mt-8 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {payload.posts.map((post) => (
            <article key={post.id} className="overflow-hidden rounded-[1.7rem] border border-slate-200 bg-white shadow-sm">
              <div className="border-b border-slate-200 bg-slate-950">
                <PublicMediaFrame
                  media={{
                    posterUrl: post.coverMedia.url,
                    mp4Url: null,
                    webmUrl: null,
                    gifUrl: post.coverMedia.mimeType?.includes('gif') ? post.coverMedia.url : null,
                    alt: post.coverMedia.alt,
                    caption: post.coverMedia.caption,
                    layoutVariant: 'crm',
                  }}
                />
              </div>
              <div className="space-y-4 p-5">
                <div className="flex flex-wrap gap-2">
                  {post.categories.map((category) => (
                    <Badge key={category} variant="outline">{category}</Badge>
                  ))}
                </div>
                <div>
                  <h2 className="text-xl font-semibold text-slate-950">{post.title}</h2>
                  {post.excerpt ? <p className="mt-2 text-sm leading-relaxed text-slate-600">{post.excerpt}</p> : null}
                </div>
                <div className="flex items-center justify-between text-xs text-slate-500">
                  <span>{post.authorName}</span>
                  <span>{post.readingTimeMinutes} min</span>
                </div>
                <Button variant="ghost" className="px-0 text-primary hover:bg-transparent hover:text-primary/80" asChild>
                  <Link to={`/blog/${post.slug}`}>
                    Ler artigo
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </Link>
                </Button>
              </div>
            </article>
          ))}
        </section>
      </main>
    </div>
  );
}
