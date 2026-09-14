import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'disparo-seguro',
  titulo: 'Fazer um disparo seguro (opt-in, supressão e pausa automática)',
  resumo:
    'O que é opt-in e opt-out, por que uma lista de contatos entra em supressão, e por que uma campanha pode pausar sozinha no meio do envio.',
  categoria: 'operacao',
  papeis: ['marketing', 'gestor'],
  paginasRelacionadas: [{ label: 'Disparos e listas', rota: '/app/disparos' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Um disparo é o envio de uma mensagem para uma lista de contatos, feito na tela de [[rota:/app/disparos|Disparos e listas]]. Este artigo explica as três proteções que existem para que um disparo não gere bloqueio da conta do escritório na Meta: opt-in, supressão e pausa automática.

## Opt-in e opt-out

**Opt-in** é o registro de que a pessoa aceitou receber mensagens do escritório. **Opt-out** é o registro de que ela retirou essa autorização.

1. Antes de montar uma lista de disparo, o sistema já aplica automaticamente o filtro de opt-in: contatos sem consentimento registrado, ou que já deram opt-out, não entram no envio.
2. Se um cliente responder "SAIR", "PARE", "STOP" ou algo como "não quero mais receber" em qualquer conversa, o sistema registra o opt-out automaticamente e aquele contato passa a ser suprimido de disparos futuros.

> ATENÇÃO: não existe forma de "forçar" o envio para quem deu opt-out. Essa é uma proteção contra bloqueio da conta pela Meta, e vale tanto para proteger o cliente quanto o número oficial do escritório.

## O que é a lista de supressão

A lista de supressão reúne todo contato que não deve receber disparos, por qualquer um destes motivos:

- Pediu para sair (opt-out), como descrito acima.
- Teve uma mensagem anterior recusada de forma permanente pela Meta (por exemplo, número inválido ou bloqueio definitivo).
- Foi marcado manualmente pela equipe com um motivo de supressão.

Ao montar uma nova lista de disparo, a prévia já mostra quantos contatos foram removidos por supressão antes do envio começar — não é preciso conferir cada contato manualmente.

## Por que a campanha pausa sozinha

Se você notar que um disparo parou no meio, sem ninguém ter cancelado manualmente, geralmente é uma destas proteções automáticas agindo:

1. **Taxa de falha alta no lote**: se uma parte grande dos envios de um mesmo lote falhar (por exemplo, muitos números inválidos ou muitas mensagens recusadas seguidas), o sistema pausa o restante do lote sozinho, para evitar que o problema se repita em milhares de contatos.
2. **Erro grave de conta**: se a Meta sinalizar um problema sério com o número (como conta restrita), a campanha inteira é pausada até alguém revisar a situação.
3. **Limite diário do número (tier)**: cada número tem um limite de destinatários únicos por 24 horas, que cresce com o tempo e o uso de boa qualidade. Se a campanha ultrapassar esse limite, o excedente é reagendado para dentro do limite, em vez de falhar.
4. **Horário do destinatário**: mensagens configuradas para respeitar quiet hours (horário de descanso do destinatário) não saem fora do horário permitido; elas ficam na fila até o horário liberar.

> DICA: se uma campanha pausar, veja primeiro o motivo indicado na própria tela de disparos antes de tentar reenviar — relançar sem entender o motivo pode repetir o mesmo problema com o restante da lista.

## Antes de disparar

1. Confira a categoria do template usado (veja [[rota:/app/conhecimento?artigo=criar-template-aprovacao|Criar um template e entender a aprovação da Meta]]): um template de marketing custa mais e tem regras mais rígidas.
2. Revise o tamanho da lista e o número de destinatários únicos já usados no dia, para não estourar o limite do número.
3. Prefira listas menores e mais segmentadas a uma lista única muito grande — isso reduz o risco de queda de qualidade do número.
`,
};

export default article;
