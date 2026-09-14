import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowRight, CalendarDays, Clock3, Sparkles } from 'lucide-react';

import { DesbLogo } from '@/components/ui/DesbLogo';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { PublicMediaFrame } from '@/components/public/PublicMediaFrame';
import { usePageMetadata } from '@/hooks/use-page-metadata';
import { useStructuredData } from '@/hooks/use-structured-data';
import { fetchPublicBlogIndex } from '@/services/public-site';

function formatDate(value?: string | null) {
  if (!value) return 'Atualizado recentemente';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'long' }).format(new Date(value));
}

export default function Blog() {
  const query = useQuery({
    queryKey: ['public-blog', 'index'],
    queryFn: fetchPublicBlogIndex,
    staleTime: 60_000,
  });

  const payload = query.data;
  usePageMetadata(payload?.seo);

  const structuredData = useMemo(() => {
    if (!payload) return null;
    return {
      '@context': 'https://schema.org',
      '@type': 'Blog',
      name: payload.pageTitle,
      description: payload.intro,
      url: 'https://klmadvogados.com/blog',
      blogPost: payload.posts.slice(0, 10).map((post) => ({
        '@type': 'BlogPosting',
        headline: post.title,
        description: post.excerpt,
        url: `https://klmadvogados.com/blog/${post.slug}`,
        datePublished: post.publishedAt,
        dateModified: post.updatedAt,
        author: {
          '@type': 'Organization',
          name: post.authorName,
        },
      })),
    };
  }, [payload]);

  useStructuredData(structuredData, 'blog-index');

  if (!payload) {
    return (
      <div className="min-h-screen bg-background text-foreground">
        <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
          <div className="h-10 w-56 animate-pulse rounded bg-muted" />
          <div className="mt-10 grid gap-6 lg:grid-cols-[1.2fr,0.8fr]">
            <div className="h-[420px] animate-pulse rounded-[2rem] bg-muted" />
            <div className="space-y-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <div key={index} className="h-28 animate-pulse rounded-2xl bg-muted" />
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const featured = payload.featuredPost;
  const list = payload.posts.filter((post) => post.id !== featured?.id);

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,#10324b_0%,#07131c_28%,#041017_100%)] text-white">
      <header className="sticky top-0 z-40 border-b border-white/10 bg-[#05131b]/80 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
          <Link to="/" className="flex items-center">
            <DesbLogo variant="full" size={56} />
          </Link>
          <div className="flex items-center gap-3">
            <Button variant="ghost" className="text-white/80 hover:bg-white/10 hover:text-white" asChild>
              <Link to="/">Home</Link>
            </Button>
            <Button className="bg-brand-gradient text-white hover:opacity-90" asChild>
              <a href={payload.featuredPost?.seo.canonicalUrl ?? 'https://klmadvogados.com'}>Ler destaque</a>
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
        <section className="grid gap-8 rounded-[2rem] border border-white/10 bg-white/5 p-6 shadow-[0_30px_80px_rgba(0,0,0,0.24)] backdrop-blur-xl lg:grid-cols-[1.18fr,0.82fr] lg:p-8">
          <div className="space-y-6">
            <Badge className="w-fit bg-white/10 text-white hover:bg-white/10">
              <Sparkles className="mr-1 h-3.5 w-3.5" />
              Blog da Meu Executivo
            </Badge>
            <div className="space-y-4">
              <h1 className="max-w-3xl text-4xl font-black tracking-tight text-white md:text-5xl">
                {payload.pageTitle}
              </h1>
              <p className="max-w-2xl text-lg leading-relaxed text-white/72">
                {payload.intro}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {payload.categories.slice(0, 6).map((category) => (
                <Button
                  key={category}
                  variant="outline"
                  className="border-white/15 bg-white/5 text-white hover:bg-white/10"
                  asChild
                >
                  <Link to={`/blog/categoria/${category.toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '').replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')}`}>
                    {category}
                  </Link>
                </Button>
              ))}
            </div>
          </div>

          {featured ? (
            <article className="group rounded-[1.75rem] border border-white/10 bg-[#071926] p-4 shadow-[0_20px_60px_rgba(2,10,18,0.35)] transition-transform hover:-translate-y-1">
              <div className="overflow-hidden rounded-[1.25rem] border border-white/10 bg-white/5">
                <PublicMediaFrame
                  media={{
                    posterUrl: featured.coverMedia.url,
                    mp4Url: null,
                    webmUrl: null,
                    gifUrl: featured.coverMedia.mimeType?.includes('gif') ? featured.coverMedia.url : null,
                    alt: featured.coverMedia.alt,
                    caption: featured.coverMedia.caption,
                    layoutVariant: 'hero',
                  }}
                  priority
                />
              </div>
              <div className="space-y-4 p-2 pt-5">
                <div className="flex flex-wrap items-center gap-2 text-xs text-white/55">
                  <Badge className="bg-primary/20 text-primary hover:bg-primary/20">Destaque</Badge>
                  <span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" />{featured.readingTimeMinutes} min</span>
                  <span className="inline-flex items-center gap-1"><CalendarDays className="h-3.5 w-3.5" />{formatDate(featured.publishedAt)}</span>
                </div>
                <div className="space-y-2">
                  <h2 className="text-2xl font-bold text-white">{featured.title}</h2>
                  {featured.excerpt ? <p className="text-sm leading-relaxed text-white/68">{featured.excerpt}</p> : null}
                </div>
                <Button className="w-full bg-brand-gradient text-white hover:opacity-90" asChild>
                  <Link to={`/blog/${featured.slug}`}>
                    Ler artigo
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </Link>
                </Button>
              </div>
            </article>
          ) : null}
        </section>

        <section className="mt-10 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {list.map((post) => (
            <article key={post.id} className="group rounded-[1.6rem] border border-white/10 bg-white/5 p-5 shadow-[0_18px_48px_rgba(0,0,0,0.18)] transition-all hover:-translate-y-1 hover:bg-white/8">
              <div className="mb-4 overflow-hidden rounded-[1.15rem] border border-white/10 bg-[#071926]">
                <PublicMediaFrame
                  media={{
                    posterUrl: post.coverMedia.url,
                    mp4Url: null,
                    webmUrl: null,
                    gifUrl: post.coverMedia.mimeType?.includes('gif') ? post.coverMedia.url : null,
                    alt: post.coverMedia.alt,
                    caption: post.coverMedia.caption,
                    layoutVariant: 'features',
                  }}
                />
              </div>
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {post.categories.slice(0, 2).map((category) => (
                    <Badge key={category} variant="outline" className="border-white/15 text-white/80">
                      {category}
                    </Badge>
                  ))}
                </div>
                <div>
                  <h3 className="text-xl font-semibold text-white">{post.title}</h3>
                  {post.excerpt ? <p className="mt-2 text-sm leading-relaxed text-white/68">{post.excerpt}</p> : null}
                </div>
                <div className="flex items-center justify-between text-xs text-white/50">
                  <span>{post.authorName}</span>
                  <span>{post.readingTimeMinutes} min</span>
                </div>
                <Button variant="ghost" className="px-0 text-primary hover:bg-transparent hover:text-primary/80" asChild>
                  <Link to={`/blog/${post.slug}`}>
                    Continuar lendo
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
