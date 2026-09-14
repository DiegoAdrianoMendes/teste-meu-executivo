import { useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { BookOpen, ChevronRight, Search } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { KlmPill } from '@/klm/components/Primitives';
import { KnowledgeMarkdown } from '@/klm/knowledge/markdown';
import { getArticlesForRoute } from '@/klm/knowledge/routeArticleMap';
import {
  CATEGORIA_LABEL,
  knowledgeArticles,
  searchArticles,
  type KnowledgeArticle,
} from '@/content/knowledge';

/**
 * Botão "?" do header + drawer lateral com o artigo mapeado à rota atual.
 * Nunca navega para fora da tela: fechar o drawer devolve o usuário exatamente
 * onde estava. Rotas sem artigo mapeado caem no modo de busca com a lista
 * completa, agrupada por categoria.
 */
export function HelpDrawer({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [manualSlug, setManualSlug] = useState<string | null>(null);

  const { primary, related } = useMemo(
    () => getArticlesForRoute(location.pathname, location.search),
    [location.pathname, location.search],
  );

  const activeArticle: KnowledgeArticle | undefined = manualSlug
    ? knowledgeArticles.find((article) => article.slug === manualSlug)
    : primary;

  const filteredArticles = useMemo(() => searchArticles(knowledgeArticles, query), [query]);

  function close(next: boolean) {
    if (!next) {
      setManualSlug(null);
      setQuery('');
    }
    onOpenChange(next);
  }

  function openFullPage() {
    close(false);
    navigate(activeArticle ? `/app/conhecimento?artigo=${activeArticle.slug}` : '/app/conhecimento');
  }

  return (
    <Sheet open={open} onOpenChange={close}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-md" data-testid="help-drawer">
        <SheetHeader className="border-b border-border px-5 py-4 text-left">
          <div className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-primary" />
            <SheetTitle className="text-sm">{activeArticle ? activeArticle.titulo : 'Ajuda desta tela'}</SheetTitle>
          </div>
          <SheetDescription className="text-xs">
            {activeArticle
              ? 'Artigo da Central de Conhecimento relacionado ao que você está vendo agora.'
              : 'Ainda não há um artigo específico para esta tela. Busque abaixo ou veja a lista completa.'}
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
          {activeArticle ? (
            <div className="space-y-4 px-5 py-4">
              <div className="flex flex-wrap gap-1.5">
                <KlmPill tone="blue">{CATEGORIA_LABEL[activeArticle.categoria]}</KlmPill>
              </div>
              <KnowledgeMarkdown corpo={activeArticle.corpo} />
              {(manualSlug ? [] : related).length > 0 && (
                <div className="border-t border-border pt-3">
                  <p className="mb-2 text-2xs font-semibold uppercase text-muted-foreground">Veja também</p>
                  <div className="space-y-1.5">
                    {related.map((article) => (
                      <button
                        key={article.slug}
                        type="button"
                        onClick={() => setManualSlug(article.slug)}
                        className="flex w-full items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-left text-xs hover:bg-secondary"
                      >
                        <span className="truncate">{article.titulo}</span>
                        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {manualSlug && (
                <Button type="button" variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={() => setManualSlug(null)}>
                  <ChevronRight className="h-3.5 w-3.5 rotate-180" /> Voltar ao artigo desta tela
                </Button>
              )}
            </div>
          ) : (
            <div className="space-y-3 px-5 py-4">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Buscar por assunto ou palavra-chave"
                  className="h-9 bg-secondary pl-9 text-xs"
                  aria-label="Buscar artigos de ajuda"
                  autoFocus
                />
              </div>
              <div className="space-y-1.5">
                {filteredArticles.map((article) => (
                  <button
                    key={article.slug}
                    type="button"
                    onClick={() => setManualSlug(article.slug)}
                    className="flex w-full flex-col items-start gap-0.5 rounded-md border border-border px-3 py-2 text-left hover:bg-secondary"
                  >
                    <span className="text-xs font-medium text-foreground">{article.titulo}</span>
                    <span className="line-clamp-1 text-2xs text-muted-foreground">{article.resumo}</span>
                  </button>
                ))}
                {filteredArticles.length === 0 && (
                  <p className="px-1 py-4 text-center text-xs text-muted-foreground">Nenhum artigo encontrado.</p>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-border px-5 py-3">
          <Button type="button" variant="outline" size="sm" className="h-8 w-full text-xs" onClick={openFullPage}>
            Abrir na Central de Conhecimento
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
