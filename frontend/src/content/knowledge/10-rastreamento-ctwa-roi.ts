import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'rastreamento-ctwa-roi',
  titulo: 'Ligar o rastreamento de anúncios (CTWA) e ler o ROI',
  resumo:
    'O que é CTWA (clique no anúncio que abre o WhatsApp), como recuperar histórico de conversas antigas, e como ler CPL, CAC e ROAS na tela de Rastreamento sem ser especialista em métricas.',
  categoria: 'crescimento',
  papeis: ['gestor', 'marketing'],
  paginasRelacionadas: [{ label: 'Rastreamento', rota: '/app/rastreamento' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## O que é CTWA

CTWA quer dizer "Click to WhatsApp Ad" (clique no anúncio que abre o WhatsApp): é o tipo de anúncio do Instagram ou Facebook que, ao ser clicado, abre uma conversa de WhatsApp já com o cliente, em vez de levá-lo a um site. Quando isso acontece, a Meta envia junto um identificador do clique, e é esse identificador que permite ao Meu Executivo saber que aquela conversa começou por causa de um anúncio específico — e não de qualquer outro canal.

## Como ativar o rastreamento

O rastreamento de CTWA já funciona automaticamente para conversas novas assim que o WhatsApp oficial está conectado (veja [[rota:/app/conhecimento?artigo=conectar-whatsapp-manual|Conectar o WhatsApp do escritório (modo manual)]]). Não é preciso configurar nada a mais para conversas que chegarem daqui para frente.

## Recuperar conversas antigas (histórico)

Se o escritório já recebia mensagens de anúncios antes de o rastreamento estar ativo, é possível recuperar esse histórico retroativamente, sem apagar nenhuma evidência:

1. Vá até [[rota:/app/rastreamento|Rastreamento]] e abra "Recuperar histórico de anúncios (CTWA)".
2. Rode primeiro uma simulação (não grava nada): ela mostra quantas mensagens foram varridas, quantas vieram de anúncio e quantas podem ser recuperadas agora.
3. Se o número de "recuperáveis agora" for maior que zero, confirme para gravar os eventos de verdade.

> DICA: rodar essa recuperação de novo depois não duplica nada — o sistema reconhece o que já foi recuperado antes e ignora.

## Como ler a tela de Rastreamento

A tela de [[rota:/app/rastreamento|Rastreamento]] tem uma tabela de canais de aquisição, com estas colunas, em ordem do funil:

1. **Invest.**: quanto foi gasto em anúncios naquele canal.
2. **Leads**: quantas pessoas iniciaram contato.
3. **Qualif.**: quantas foram qualificadas como um caso real.
4. **Reuniões**: quantas chegaram a agendar uma reunião.
5. **Clientes**: quantas efetivamente fecharam contrato.
6. **CPL** (custo por lead): quanto custou, em média, cada pessoa que iniciou contato.
7. **CAC** (custo de aquisição de cliente): quanto custou, em média, cada cliente que fechou contrato.
8. **Receita**: quanto esse canal já gerou em honorários atribuíveis.

## O que é ROAS

**ROAS** (retorno sobre o investimento em anúncios) mostra quantas vezes o valor investido em anúncios voltou em receita. Um ROAS de 12x, por exemplo, significa que para cada R$ 1 investido, o canal gerou R$ 12 em receita atribuída. Quanto maior o ROAS, melhor o retorno daquele canal.

> ATENÇÃO: números vindos de uma base de demonstração (seed) aparecem identificados na tela com um selo próprio. Ao decidir onde investir mais verba, sempre confira se o número que você está olhando é real ou de demonstração — a tela distingue isso para você não confundir os dois.

## Devolvendo dados para a Meta otimizar (visão geral)

Além de mostrar métricas, o Meu Executivo também devolve para a Meta os eventos importantes do funil (lead qualificado, contrato assinado, honorário pago), para que os próprios algoritmos de anúncio da Meta aprendam a encontrar mais gente parecida com quem realmente virou cliente. Isso acontece de forma automática nos bastidores, sem exigir nenhuma ação manual na tela de Rastreamento.
`,
};

export default article;
