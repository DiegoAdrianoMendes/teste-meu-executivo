import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, CalendarDays, Clock3, Link2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DesbLogo } from '@/components/ui/DesbLogo';
import { PublicMediaFrame } from '@/components/public/PublicMediaFrame';
import { usePageMetadata } from '@/hooks/use-page-metadata';
import { useStructuredData } from '@/hooks/use-structured-data';
import { fetchPublicBlogIndex, fetchPublicBlogPost } from '@/services/public-site';
import type { BlogBlock, BlogPost as BlogPostType } from '@/types';

function formatDate(value?: string | null) {
  if (!value) return 'Atualizado recentemente';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'long' }).format(new Date(value));
}

function slugifyHeading(value: string) {
  return value
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '');
}

function scrollToAnchor(targetId: string) {
  const node = document.getElementById(targetId);
  if (!node) return;
  node.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderBlock(block: BlogBlock, index: number) {
  const key = `${block.type}-${index}`;
  if (block.type === 'heading') {
    const level = Math.min(Math.max(block.level ?? 2, 2), 4);
    const Heading = `h${level}` as keyof JSX.IntrinsicElements;
    const anchorId = slugifyHeading(block.content ?? `secao-${index}`);
    return (
      <Heading
        id={anchorId}
        key={key}
        className="group mt-10 scroll-mt-24 text-2xl font-bold tracking-tight text-slate-950 md:text-3xl"
      >
        <button
          type="button"
          onClick={() => scrollToAnchor(anchorId)}
          className="inline-flex items-center gap-2 text-left"
        >
          <span>{block.content}</span>
          <Link2 className="h-4 w-4 text-primary opacity-0 transition-opacity group-hover:opacity-100" />
        </button>
      </Heading>
    );
  }

  if (block.type === 'quote') {
    return (
      <blockquote key={key} className="rounded-[1.6rem] border border-primary/15 bg-primary/5 px-6 py-5 text-lg leading-relaxed text-slate-700">
        “{block.content}”
      </blockquote>
    );
  }

  if (block.type === 'list') {
    return (
      <ul key={key} className="grid gap-3 rounded-[1.4rem] border border-slate-200 bg-slate-50/80 p-5 text-slate-700">
        {(block.items ?? []).map((item) => (
          <li key={item} className="flex items-start gap-3">
            <span className="mt-1 h-2 w-2 rounded-full bg-primary" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    );
  }

  if (block.type === 'faq') {
    return (
      <section key={key} className="rounded-[1.5rem] border border-slate-200 bg-white p-6 shadow-sm">
        <h3 className="text-xl font-semibold text-slate-950">{block.title ?? 'Perguntas frequentes'}</h3>
        <div className="mt-4 space-y-3">
          {(block.items ?? []).map((item) => (
            <div key={item} className="rounded-2xl bg-slate-50 px-4 py-3 text-sm leading-relaxed text-slate-700">
              {item}
            </div>
          ))}
        </div>
      </section>
    );
  }

  if (block.type === 'callout') {
    return (
      <aside key={key} className="rounded-[1.5rem] border border-primary/20 bg-white px-6 py-5 shadow-[0_18px_40px_rgba(5,26,43,0.08)]">
        {block.title ? <p className="text-sm font-semibold uppercase tracking-[0.18em] text-primary">{block.title}</p> : null}
        {block.content ? <p className="mt-3 text-base leading-relaxed text-slate-700">{block.content}</p> : null}
      </aside>
    );
  }

  return (
    <p key={key} className="text-lg leading-8 text-slate-700">
      {block.content}
    </p>
  );
}

function buildFaqStructuredData(post: BlogPostType) {
  const faqBlock = post.contentBlocks.find((block) => block.type === 'faq' && block.items.length > 0);
  if (!faqBlock) return null;
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: faqBlock.items.map((item) => ({
      '@type': 'Question',
      name: item,
      acceptedAnswer: {
        '@type': 'Answer',
        text: item,
      },
    })),
  };
}

export default function BlogPost() {
  const { slug = '' } = useParams<{ slug: string }>();
  const postQuery = useQuery({
    queryKey: ['public-blog', 'post', slug],
    queryFn: () => fetchPublicBlogPost(slug),
    staleTime: 60_000,
  });
  const indexQuery = useQuery({
    queryKey: ['public-blog', 'index'],
    queryFn: fetchPublicBlogIndex,
    staleTime: 60_000,
  });

  const post = postQuery.data;
  usePageMetadata(post?.seo);

  const toc = useMemo(
    () =>
      (post?.contentBlocks ?? [])
        .filter((block) => block.type === 'heading' && block.content)
        .map((block) => ({
          id: slugifyHeading(block.content ?? ''),
          label: block.content ?? '',
        })),
    [post],
  );

  const relatedPosts = useMemo(() => {
    if (!post || !indexQuery.data) return [];
    return indexQuery.data.posts
      .filter((item) => item.slug !== post.slug && item.categories.some((category) => post.categories.includes(category)))
      .slice(0, 3);
  }, [indexQuery.data, post]);

  const postStructuredData = useMemo(() => {
    if (!post) return null;
    return {
      '@context': 'https://schema.org',
      '@type': 'BlogPosting',
      headline: post.title,
      description: post.excerpt,
      datePublished: post.publishedAt,
      dateModified: post.updatedAt,
      timeRequired: `PT${post.readingTimeMinutes}M`,
      author: {
        '@type': 'Organization',
        name: post.authorName,
      },
      image: post.ogMedia.url ?? post.coverMedia.url ?? undefined,
      mainEntityOfPage: post.seo.canonicalUrl,
      keywords: post.seo.keywords.join(', '),
    };
  }, [post]);

  useStructuredData(postStructuredData, 'blog-post');
  useStructuredData(buildFaqStructuredData(post as BlogPostType), 'blog-post-faq');

  if (!post) {
    return (
      <div className="min-h-screen bg-white">
        <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
          <div className="h-10 w-40 animate-pulse rounded bg-muted" />
          <div className="mt-8 h-16 max-w-3xl animate-pulse rounded bg-muted" />
          <div className="mt-6 h-[420px] animate-pulse rounded-[2rem] bg-muted" />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#f5f7fb] text-slate-950">
      <header className="sticky top-0 z-40 border-b border-slate-200/80 bg-white/85 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-4">
            <Link to="/">
              <DesbLogo variant="full" size={54} />
            </Link>
            <Badge variant="outline" className="hidden border-primary/20 text-primary lg:inline-flex">Editorial Meu Executivo</Badge>
          </div>
          <Button variant="ghost" asChild>
            <Link to="/blog">Voltar ao blog</Link>
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        <Link to="/blog" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" />
          Voltar ao blog
        </Link>

        <section className="mt-6 grid gap-8 lg:grid-cols-[1fr,320px]">
          <article className="overflow-hidden rounded-[2rem] border border-slate-200 bg-white shadow-[0_30px_80px_rgba(7,19,28,0.08)]">
            <div className="border-b border-slate-200 bg-[linear-gradient(135deg,#07131c_0%,#0b3d60_55%,#11a7d8_100%)] px-6 py-8 text-white md:px-10 md:py-10">
              <div className="flex flex-wrap items-center gap-2">
                {post.categories.map((category) => (
                  <Badge key={category} className="bg-white/10 text-white hover:bg-white/10">{category}</Badge>
                ))}
              </div>
              <h1 className="mt-5 max-w-4xl text-4xl font-black tracking-tight md:text-5xl">{post.title}</h1>
              {post.subtitle ? <p className="mt-4 max-w-3xl text-lg leading-relaxed text-white/80">{post.subtitle}</p> : null}
              <div className="mt-5 flex flex-wrap gap-4 text-sm text-white/70">
                <span className="inline-flex items-center gap-2"><CalendarDays className="h-4 w-4" />{formatDate(post.publishedAt ?? post.updatedAt)}</span>
                <span className="inline-flex items-center gap-2"><Clock3 className="h-4 w-4" />{post.readingTimeMinutes} min de leitura</span>
                <span>{post.authorName}{post.authorRole ? ` · ${post.authorRole}` : ''}</span>
              </div>
            </div>

            <div className="border-b border-slate-200 bg-slate-950">
              <PublicMediaFrame
                media={{
                  posterUrl: post.coverMedia.url,
                  mp4Url: null,
                  webmUrl: null,
                  gifUrl: post.coverMedia.mimeType?.includes('gif') ? post.coverMedia.url : null,
                  alt: post.coverMedia.alt,
                  caption: post.coverMedia.caption,
                  layoutVariant: 'hero',
                }}
                priority
              />
            </div>

            <div className="grid gap-10 px-6 py-8 md:px-10 lg:grid-cols-[minmax(0,1fr)_260px]">
              <div className="space-y-7">
                {post.excerpt ? (
                  <p className="rounded-[1.5rem] border border-primary/15 bg-primary/5 px-6 py-5 text-lg leading-8 text-slate-700">
                    {post.excerpt}
                  </p>
                ) : null}
                {post.contentBlocks.map((block, index) => renderBlock(block, index))}
                <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 p-6">
                  <h2 className="text-xl font-semibold">Quer ver isso aplicado na prática?</h2>
                  <p className="mt-2 text-sm leading-relaxed text-slate-600">
                    A Meu Executivo organiza atendimento, documentos e acompanhamento no mesmo fluxo.
                  </p>
                  <div className="mt-4 flex flex-wrap gap-3">
                    <Button className="bg-brand-gradient text-white hover:opacity-90" asChild>
                      <a href="https://wa.me/5561993328108?text=Quero%20falar%20com%20a%20KLM%20Advogados" target="_blank" rel="noreferrer">
                        Agendar demonstração
                      </a>
                    </Button>
                    <Button variant="outline" asChild>
                      <Link to="/">Voltar para a home</Link>
                    </Button>
                  </div>
                </div>
              </div>

              <aside className="space-y-5">
                {toc.length > 0 ? (
                  <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 p-5">
                    <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Neste artigo</p>
                    <div className="mt-4 space-y-2">
                      {toc.map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => scrollToAnchor(item.id)}
                          className="block w-full rounded-xl px-3 py-2 text-left text-sm text-slate-600 transition-colors hover:bg-white hover:text-slate-950"
                        >
                          {item.label}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}
                <div className="rounded-[1.5rem] border border-slate-200 bg-white p-5 shadow-sm">
                  <p className="text-sm font-semibold">SEO & indexação</p>
                  <p className="mt-2 text-sm text-slate-600">
                    Score heurístico: <span className="font-semibold text-slate-950">{post.seoAudit.score}/100</span>
                  </p>
                  <ul className="mt-3 space-y-2 text-sm text-slate-600">
                    {post.seoAudit.suggestions.slice(0, 3).map((suggestion) => (
                      <li key={suggestion}>• {suggestion}</li>
                    ))}
                  </ul>
                </div>
              </aside>
            </div>
          </article>

          <aside className="space-y-5">
            <div className="rounded-[1.7rem] border border-slate-200 bg-white p-5 shadow-sm">
              <p className="text-sm font-semibold">Relacionados</p>
              <div className="mt-4 space-y-4">
                {relatedPosts.map((item) => (
                  <Link key={item.id} to={`/blog/${item.slug}`} className="block rounded-2xl border border-slate-200 p-4 transition-colors hover:border-primary/30 hover:bg-primary/5">
                    <p className="text-sm font-semibold">{item.title}</p>
                    {item.excerpt ? <p className="mt-2 text-xs leading-relaxed text-slate-600">{item.excerpt}</p> : null}
                  </Link>
                ))}
                {!relatedPosts.length && (
                  <p className="text-sm text-slate-500">Mais conteúdos editoriais estarão aparecendo aqui conforme o blog crescer.</p>
                )}
              </div>
            </div>
            <div className="rounded-[1.7rem] border border-primary/15 bg-primary/5 p-5">
              <p className="text-sm font-semibold text-primary">Categorias deste artigo</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {post.categories.map((category) => (
                  <Button key={category} variant="outline" size="sm" asChild>
                    <Link to={`/blog/categoria/${slugifyHeading(category)}`}>{category}</Link>
                  </Button>
                ))}
              </div>
              <Button className="mt-5 w-full bg-brand-gradient text-white hover:opacity-90" asChild>
                <Link to="/blog">
                  Ver todos os artigos
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Link>
              </Button>
            </div>
          </aside>
        </section>
      </main>
    </div>
  );
}
