import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { BookOpen, ChevronRight, List, Search, ThumbsDown, ThumbsUp } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { KlmPanel, KlmPill, KlmSectionTitle } from '@/klm/components/Primitives';
import { extractHeadings, KnowledgeMarkdown } from '@/klm/knowledge/markdown';
import { klmApi } from '@/klm/api';
import {
  CATEGORIA_LABEL,
  getArticleBySlug,
  knowledgeArticles,
  PAPEL_LABEL,
  searchArticles,
  sortArticlesByRole,
  type KnowledgeArticle,
  type KnowledgeCategoria,
} from '@/content/knowledge';
import { useAppStore } from '@/store/app-context';
import { cn } from '@/lib/utils';

const CATEGORIA_ORDER: KnowledgeCategoria[] = ['conexao', 'operacao', 'crescimento', 'gestao'];

type Feedback = 'up' | 'down';

function formatDate(value: string) {
  try {
    return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'long' }).format(new Date(value));
  } catch {
    return value;
  }
}

function ArticleReader({
  article,
  feedback,
  onFeedback,
  onBack,
}: {
  article: KnowledgeArticle;
  feedback: Feedback | undefined;
  onFeedback: (value: Feedback) => void;
  onBack: () => void;
}) {
  const headings = useMemo(() => extractHeadings(article.corpo), [article.corpo]);

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
      <KlmPanel className="min-w-0 p-5">
        <button
          type="button"
          onClick={onBack}
          className="mb-4 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          <ChevronRight className="h-3.5 w-3.5 rotate-180" /> Voltar para a lista
        </button>
        <p className="text-2xs font-semibold uppercase text-[#0057d8]">{CATEGORIA_LABEL[article.categoria]}</p>
        <h2 className="mt-1 text-2xl font-semibold text-foreground">{article.titulo}</h2>
        <p className="mt-1 text-xs text-muted-foreground">Atualizado em {formatDate(article.atualizadoEm)}</p>

        {article.paginasRelacionadas.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {article.paginasRelacionadas.map((pagina) => (
              <Link key={pagina.rota} to={pagina.rota}>
                <KlmPill tone="blue">{pagina.label}</KlmPill>
              </Link>
            ))}
          </div>
        )}

        <div className="mt-6">
          <KnowledgeMarkdown corpo={article.corpo} />
        </div>

        <div className="mt-8 flex flex-wrap items-center gap-3 border-t border-border pt-5">
          <span className="text-xs font-medium text-foreground">Este artigo ajudou?</span>
          <Button
            type="button"
            size="sm"
            variant={feedback === 'up' ? 'default' : 'outline'}
            className="h-8 gap-1.5 text-xs"
            onClick={() => onFeedback('up')}
          >
            <ThumbsUp className="h-3.5 w-3.5" /> Sim
          </Button>
          <Button
            type="button"
            size="sm"
            variant={feedback === 'down' ? 'default' : 'outline'}
            className="h-8 gap-1.5 text-xs"
            onClick={() => onFeedback('down')}
          >
            <ThumbsDown className="h-3.5 w-3.5" /> Não
          </Button>
          {feedback && (
            <span className="text-2xs text-muted-foreground">Obrigado pelo retorno, ele fica registrado no workspace.</span>
          )}
        </div>
      </KlmPanel>

      {headings.length > 0 && (
        <KlmPanel className="h-fit p-4 lg:sticky lg:top-4">
          <div className="mb-2 flex items-center gap-2 text-2xs font-semibold uppercase text-muted-foreground">
            <List className="h-3.5 w-3.5" /> Nesta página
          </div>
          <nav className="space-y-1.5 text-xs">
            {headings.map((heading) => (
              <a
                key={heading.id}
                href={`#${heading.id}`}
                className={cn(
                  'block truncate text-muted-foreground hover:text-foreground',
                  heading.nivel === 3 && 'pl-3',
                )}
              >
                {heading.texto}
              </a>
            ))}
          </nav>
        </KlmPanel>
      )}
    </div>
  );
}

export default function KnowledgePage() {
  const { state } = useAppStore();
  const activeRole = state?.session?.activeRole ?? state?.currentUser?.role ?? null;

  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState('');
  const [feedback, setFeedback] = useState<Record<string, Feedback>>({});

  const activeSlug = searchParams.get('artigo');
  const activeArticle = getArticleBySlug(activeSlug);

  const orderedArticles = useMemo(() => sortArticlesByRole(knowledgeArticles, activeRole), [activeRole]);
  const filteredArticles = useMemo(() => searchArticles(orderedArticles, query), [orderedArticles, query]);

  const grouped = useMemo(() => {
    const map = new Map<KnowledgeCategoria, KnowledgeArticle[]>();
    for (const article of filteredArticles) {
      const list = map.get(article.categoria) ?? [];
      list.push(article);
      map.set(article.categoria, list);
    }
    return CATEGORIA_ORDER.map((categoria) => ({ categoria, articles: map.get(categoria) ?? [] })).filter(
      (group) => group.articles.length > 0,
    );
  }, [filteredArticles]);

  function openArticle(slug: string) {
    setSearchParams((params) => {
      const next = new URLSearchParams(params);
      next.set('artigo', slug);
      return next;
    });
  }

  function closeArticle() {
    setSearchParams((params) => {
      const next = new URLSearchParams(params);
      next.delete('artigo');
      return next;
    });
  }

  function handleFeedback(slug: string, value: Feedback) {
    // Otimista: reflete o clique de imediato e reconcilia com o valor
    // persistido pelo backend (RLS por workspace) quando a resposta chegar.
    setFeedback((prev) => ({ ...prev, [slug]: value }));
    klmApi
      .submitKnowledgeArticleFeedback(slug, value)
      .then((summary) => {
        if (!summary.myFeedback) return;
        setFeedback((prev) => ({ ...prev, [slug]: summary.myFeedback as Feedback }));
      })
      .catch(() => {
        // Falha de rede: mantém o voto otimista local; o próximo clique tenta de novo.
      });
  }

  if (activeArticle) {
    return (
      <div className="mx-auto max-w-5xl space-y-4 p-4 sm:p-6">
        <ArticleReader
          article={activeArticle}
          feedback={feedback[activeArticle.slug]}
          onFeedback={(value) => handleFeedback(activeArticle.slug, value)}
          onBack={closeArticle}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-4 sm:p-6">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-secondary">
          <BookOpen className="h-5 w-5 text-[#0057d8]" />
        </div>
        <div>
          <p className="text-2xs font-semibold uppercase text-[#0057d8]">Central de Ajuda</p>
          <h2 className="mt-1 text-2xl font-semibold text-foreground">Conhecimento</h2>
          <p className="mt-1 max-w-2xl text-xs leading-4 text-muted-foreground">
            Passo a passo em linguagem simples para conectar canais, atender clientes e acompanhar resultados sem sair do
            sistema.
          </p>
        </div>
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Buscar por assunto, tela ou palavra-chave"
          className="h-10 bg-secondary pl-9 text-xs"
          aria-label="Buscar artigos"
        />
      </div>

      {activeRole && (
        <p className="text-2xs text-muted-foreground">
          Mostrando primeiro os artigos mais úteis para o seu papel ({activeRole}). Nada fica escondido, só reordenado.
        </p>
      )}

      {grouped.length === 0 && (
        <KlmPanel className="p-8 text-center">
          <p className="text-sm font-medium text-foreground">Nenhum artigo encontrado</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Tente outra palavra-chave ou revise a lista completa de artigos acima.
          </p>
        </KlmPanel>
      )}

      {grouped.map((group) => (
        <KlmPanel key={group.categoria} className="p-4">
          <KlmSectionTitle title={CATEGORIA_LABEL[group.categoria]} description={`${group.articles.length} artigo(s)`} />
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {group.articles.map((article) => (
              <button
                key={article.slug}
                type="button"
                onClick={() => openArticle(article.slug)}
                className="group flex flex-col items-start gap-1 rounded-lg border border-border bg-card p-3.5 text-left transition-colors hover:border-primary/40 hover:bg-secondary"
              >
                <span className="flex w-full items-center justify-between gap-2">
                  <span className="text-sm font-medium text-foreground">{article.titulo}</span>
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                </span>
                <span className="text-xs leading-4 text-muted-foreground">{article.resumo}</span>
                <span className="mt-1 flex flex-wrap gap-1">
                  {article.papeis
                    .filter((papel) => papel !== 'todos')
                    .map((papel) => (
                      <KlmPill key={papel} tone="muted">
                        {PAPEL_LABEL[papel]}
                      </KlmPill>
                    ))}
                </span>
              </button>
            ))}
          </div>
        </KlmPanel>
      ))}
    </div>
  );
}
