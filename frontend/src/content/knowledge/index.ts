import conectarWhatsappManual from './01-conectar-whatsapp-manual';
import businessVerificationMeta from './02-business-verification-meta';
import errosComunsConexao from './03-erros-comuns-conexao';
import conectarInstagramPagina from './04-conectar-instagram-pagina';
import atenderConversasCaso360 from './05-atender-conversas-caso-360';
import criarTemplateAprovacao from './06-criar-template-aprovacao';
import disparoSeguro from './07-disparo-seguro';
import agendaFollowUps from './08-agenda-follow-ups';
import funilMoverCasos from './09-funil-mover-casos';
import rastreamentoCtwaRoi from './10-rastreamento-ctwa-roi';
import recuperarHistoricoAnunciosBackfill from './11-recuperar-historico-anuncios-backfill';
import cplCacRoasLinguagemSimples from './12-cpl-cac-roas-linguagem-simples';
import aquisicaoPagaAprovacao from './13-aquisicao-paga-aprovacao';
import papeisPermissoesEquipe from './14-papeis-permissoes-equipe';
import planosCotasIaEstouro from './15-planos-cotas-ia-estouro';
import segurancaPrivacidadeDados from './16-seguranca-privacidade-dados';
import type { KnowledgeArticle, KnowledgePapel } from './types';

export * from './types';

/** Catálogo completo dos 16 artigos planejados. */
export const knowledgeArticles: KnowledgeArticle[] = [
  conectarWhatsappManual,
  businessVerificationMeta,
  errosComunsConexao,
  conectarInstagramPagina,
  atenderConversasCaso360,
  criarTemplateAprovacao,
  disparoSeguro,
  agendaFollowUps,
  funilMoverCasos,
  rastreamentoCtwaRoi,
  recuperarHistoricoAnunciosBackfill,
  cplCacRoasLinguagemSimples,
  aquisicaoPagaAprovacao,
  papeisPermissoesEquipe,
  planosCotasIaEstouro,
  segurancaPrivacidadeDados,
];

export function getArticleBySlug(slug: string | null | undefined): KnowledgeArticle | undefined {
  if (!slug) return undefined;
  return knowledgeArticles.find((article) => article.slug === slug);
}

function normalizeRole(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase();
}

/** Papéis de workspace conhecidos que mapeiam para o papel "gestor" dos artigos. */
const GESTOR_ROLE_HINTS = ['owner', 'admin', 'gestor', 'socio', 'sócio'];

function roleMatchesArticle(activeRole: string | null | undefined, papeis: KnowledgePapel[]): boolean {
  if (papeis.includes('todos')) return true;
  const role = normalizeRole(activeRole);
  if (!role) return false;
  if (papeis.includes('gestor') && GESTOR_ROLE_HINTS.some((hint) => role.includes(hint))) return true;
  return papeis.some((papel) => role.includes(papel));
}

/**
 * Ordena os artigos priorizando o papel ativo do usuário, sem ocultar nenhum item
 * (a diretriz do plano é "nada oculto, só ordenado"). Empates preservam a ordem do catálogo.
 */
export function sortArticlesByRole(articles: KnowledgeArticle[], activeRole: string | null | undefined): KnowledgeArticle[] {
  return articles
    .map((article, index) => ({ article, index, priority: roleMatchesArticle(activeRole, article.papeis) ? 0 : 1 }))
    .sort((a, b) => a.priority - b.priority || a.index - b.index)
    .map((entry) => entry.article);
}

function normalizeSearchText(value: string): string {
  return value
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '');
}

/** Busca client-side por título e corpo (case/acento-insensível). */
export function searchArticles(articles: KnowledgeArticle[], query: string): KnowledgeArticle[] {
  const term = normalizeSearchText(query.trim());
  if (!term) return articles;
  return articles.filter((article) => {
    const haystack = normalizeSearchText(`${article.titulo} ${article.resumo} ${article.corpo}`);
    return haystack.includes(term);
  });
}
