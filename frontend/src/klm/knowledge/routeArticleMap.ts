import { knowledgeArticles, type KnowledgeArticle } from '@/content/knowledge';

/**
 * Mapa rota → slugs de artigo, usado pelo drawer contextual "?" do shell.
 * O primeiro slug de cada entrada é o artigo principal daquela tela; os
 * seguintes aparecem como "veja também". Prefixos mais longos são checados
 * primeiro, então uma rota como `/app/configuracoes/avancadas` não cai
 * acidentalmente na entrada genérica de `/app/configuracoes`.
 */
const ROUTE_ARTICLE_ENTRIES: { prefix: string; slugs: string[] }[] = [
  { prefix: '/app/conexoes', slugs: ['conectar-whatsapp-manual', 'business-verification-meta', 'erros-comuns-conexao', 'conectar-instagram-pagina'] },
  { prefix: '/whatsapp/meta-templates', slugs: ['criar-template-aprovacao', 'disparo-seguro'] },
  { prefix: '/app/disparos', slugs: ['disparo-seguro', 'criar-template-aprovacao'] },
  { prefix: '/app/conversas', slugs: ['atender-conversas-caso-360', 'disparo-seguro'] },
  { prefix: '/app/leads', slugs: ['funil-mover-casos', 'atender-conversas-caso-360'] },
  { prefix: '/app/funil', slugs: ['funil-mover-casos', 'atender-conversas-caso-360'] },
  { prefix: '/app/agenda', slugs: ['agenda-follow-ups'] },
  { prefix: '/app/rastreamento', slugs: ['rastreamento-ctwa-roi', 'recuperar-historico-anuncios-backfill', 'cpl-cac-roas-linguagem-simples'] },
  { prefix: '/app/aquisicao-paga', slugs: ['aquisicao-paga-aprovacao', 'cpl-cac-roas-linguagem-simples'] },
  { prefix: '/app/configuracoes/avancadas', slugs: ['papeis-permissoes-equipe', 'planos-cotas-ia-estouro', 'seguranca-privacidade-dados'] },
  { prefix: '/app/configuracoes', slugs: ['papeis-permissoes-equipe', 'planos-cotas-ia-estouro', 'seguranca-privacidade-dados'] },
];

/** Tabs da tela de Configurações que têm um artigo mais específico do que o primeiro da lista. */
const CONFIG_TAB_SLUG: Record<string, string> = {
  equipe: 'papeis-permissoes-equipe',
  ia: 'planos-cotas-ia-estouro',
  seguranca: 'seguranca-privacidade-dados',
};

export type RouteArticles = {
  primary: KnowledgeArticle | undefined;
  related: KnowledgeArticle[];
};

function bySlug(slug: string): KnowledgeArticle | undefined {
  return knowledgeArticles.find((article) => article.slug === slug);
}

/** Resolve o artigo principal (e os relacionados) para a rota atual do shell. */
export function getArticlesForRoute(pathname: string, search: string): RouteArticles {
  const entry = [...ROUTE_ARTICLE_ENTRIES]
    .sort((a, b) => b.prefix.length - a.prefix.length)
    .find((candidate) => pathname.startsWith(candidate.prefix));

  if (!entry) return { primary: undefined, related: [] };

  let slugs = entry.slugs;
  if (entry.prefix === '/app/configuracoes/avancadas') {
    const tab = new URLSearchParams(search).get('tab');
    const tabSlug = tab ? CONFIG_TAB_SLUG[tab] : undefined;
    if (tabSlug) slugs = [tabSlug, ...slugs.filter((slug) => slug !== tabSlug)];
  }

  const articles = slugs.map(bySlug).filter((article): article is KnowledgeArticle => Boolean(article));
  return { primary: articles[0], related: articles.slice(1) };
}
