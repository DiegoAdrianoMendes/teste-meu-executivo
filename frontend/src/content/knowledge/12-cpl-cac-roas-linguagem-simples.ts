import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'cpl-cac-roas-linguagem-simples',
  titulo: 'Entender CPL, CAC e ROAS em linguagem simples',
  resumo:
    'As três siglas que aparecem no Rastreamento explicadas sem jargão de marketing, com exemplos de conta simples e o que fazer quando os números parecem se contradizer.',
  categoria: 'crescimento',
  papeis: ['gestor', 'marketing'],
  paginasRelacionadas: [{ label: 'Rastreamento', rota: '/app/rastreamento' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

A tela de [[rota:/app/rastreamento|Rastreamento]] usa três siglas o tempo todo: CPL, CAC e ROAS. Este artigo explica cada uma com um exemplo de conta simples, sem exigir conhecimento prévio de marketing.

## CPL — custo por lead

**CPL** é quanto, em média, custou cada pessoa que iniciou contato com o escritório por um canal específico.

> Exemplo: se o escritório investiu R$ 1.000 em anúncios em um mês e recebeu 50 pessoas iniciando conversa por causa desses anúncios, o CPL é R$ 1.000 ÷ 50 = **R$ 20 por lead**.

Um CPL baixo não é necessariamente bom sozinho — vale a pena olhar junto com o CAC, porque um canal pode trazer muitos leads baratos que não viram cliente.

## CAC — custo de aquisição de cliente

**CAC** é quanto, em média, custou cada cliente que efetivamente fechou contrato, contando todo o investimento daquele canal, não só os que converteram.

> Exemplo: dos 50 leads do exemplo acima, 5 fecharam contrato. O CAC é R$ 1.000 ÷ 5 = **R$ 200 por cliente**.

O CAC é o número mais importante para decidir se vale a pena continuar investindo em um canal: se o valor médio de um caso fechado for bem maior que o CAC daquele canal, o investimento se paga.

## ROAS — retorno sobre o investimento em anúncios

**ROAS** mostra quantas vezes o valor investido em anúncios voltou em receita atribuída àquele canal.

> Exemplo: se esse mesmo canal gerou R$ 12.000 em honorários atribuíveis a partir de R$ 1.000 investidos, o ROAS é 12.000 ÷ 1.000 = **12x** — para cada R$ 1 investido, o canal retornou R$ 12 em receita.

Quanto maior o ROAS, melhor o retorno daquele canal, mas ele não substitui o CAC: um canal pode ter ROAS alto e ainda assim ter CAC alto, se o valor médio dos casos fechados também for alto.

## Como ler os três juntos, sem se confundir

1. Comece pelo CPL para saber se o canal está atraindo gente a um custo razoável.
2. Olhe o CAC para saber se esses leads estão virando cliente a um custo que compensa.
3. Confirme com o ROAS se o retorno em receita justifica manter ou aumentar o investimento.

> ATENÇÃO: se um número parecer contraditório (por exemplo, "CAC — 0 clientes" ao lado de vendas atribuídas maiores que zero em outra tela), normalmente é uma diferença de definição entre as duas métricas sendo comparadas, e não um erro de cálculo. Em caso de dúvida sobre um número específico, confira a explicação inline da própria métrica na tela antes de descartar o dado.

## Dados de demonstração

Enquanto o escritório ainda não tem histórico real suficiente, a tela de [[rota:/app/rastreamento|Rastreamento]] pode mostrar números vindos de uma base de demonstração (seed), sempre identificados com um selo próprio. Use esses números só para entender como a tela funciona, nunca para decidir onde investir de verdade — para isso, use apenas dados marcados como reais.
`,
};

export default article;
