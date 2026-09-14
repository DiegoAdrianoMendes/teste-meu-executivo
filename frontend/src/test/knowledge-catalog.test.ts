import { describe, expect, it } from 'vitest';
import { knowledgeArticles } from '@/content/knowledge';

const EXPECTED_NEW_SLUGS = [
  'business-verification-meta',
  'conectar-instagram-pagina',
  'agenda-follow-ups',
  'funil-mover-casos',
  'recuperar-historico-anuncios-backfill',
  'cpl-cac-roas-linguagem-simples',
  'aquisicao-paga-aprovacao',
  'papeis-permissoes-equipe',
  'planos-cotas-ia-estouro',
  'seguranca-privacidade-dados',
];

describe('knowledge catalog', () => {
  it('publishes all 16 planned articles', () => {
    expect(knowledgeArticles).toHaveLength(16);
    const slugs = knowledgeArticles.map((article) => article.slug);
    expect(new Set(slugs).size).toBe(16);
  });

  it('includes the 10 new articles added in this sprint', () => {
    const slugs = new Set(knowledgeArticles.map((article) => article.slug));
    for (const slug of EXPECTED_NEW_SLUGS) {
      expect(slugs.has(slug)).toBe(true);
    }
  });

  it('has non-empty title, body and at least one related page for every article', () => {
    for (const article of knowledgeArticles) {
      expect(article.titulo.trim().length).toBeGreaterThan(0);
      expect(article.resumo.trim().length).toBeGreaterThan(0);
      expect(article.corpo.trim().length).toBeGreaterThan(100);
      expect(article.paginasRelacionadas.length).toBeGreaterThan(0);
    }
  });
});
