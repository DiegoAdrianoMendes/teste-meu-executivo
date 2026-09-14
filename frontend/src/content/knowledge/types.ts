export type KnowledgeCategoria = 'conexao' | 'operacao' | 'crescimento' | 'gestao';

export type KnowledgePapel = 'gestor' | 'atendimento' | 'juridico' | 'marketing' | 'todos';

export type KnowledgePaginaRelacionada = {
  label: string;
  rota: string;
};

export type KnowledgeArticle = {
  slug: string;
  titulo: string;
  resumo: string;
  categoria: KnowledgeCategoria;
  papeis: KnowledgePapel[];
  paginasRelacionadas: KnowledgePaginaRelacionada[];
  atualizadoEm: string;
  /** Corpo em markdown simplificado (ver `frontend/src/klm/knowledge/markdown.tsx`). */
  corpo: string;
};

export const CATEGORIA_LABEL: Record<KnowledgeCategoria, string> = {
  conexao: 'Conexão',
  operacao: 'Operação diária',
  crescimento: 'Crescimento',
  gestao: 'Gestão',
};

export const PAPEL_LABEL: Record<KnowledgePapel, string> = {
  gestor: 'Gestor(a) do escritório',
  atendimento: 'Atendimento',
  juridico: 'Time jurídico',
  marketing: 'Marketing / aquisição',
  todos: 'Todos os papéis',
};
