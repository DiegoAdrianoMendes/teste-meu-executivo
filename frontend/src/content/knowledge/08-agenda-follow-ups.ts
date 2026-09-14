import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'agenda-follow-ups',
  titulo: 'Agenda e follow-ups',
  resumo:
    'Como a Agenda mistura reuniões e follow-ups comerciais em uma única visão, o que cada cor e ícone significa, e como não deixar um retorno vencido passar batido.',
  categoria: 'operacao',
  papeis: ['gestor', 'atendimento', 'marketing'],
  paginasRelacionadas: [{ label: 'Agenda', rota: '/app/agenda' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Explica como ler a tela de [[rota:/app/agenda|Agenda]], que mistura dois tipos de compromisso na mesma grade: **reuniões** marcadas de verdade e **follow-ups**, que são lembretes automáticos de retorno a um cliente.

## Os dois tipos de item na Agenda

1. **Reunião**: um compromisso com data, horário e (quando aplicável) local, vinculado ou não a um caso específico.
2. **Follow-up**: um lembrete de que um caso precisa de um retorno — por exemplo, cobrar um documento ou dar continuidade a uma proposta que ainda não teve resposta. Cada follow-up tem uma tentativa (por exemplo, "tentativa 2 de 3") e um motivo.

Na grade, os dois aparecem juntos, diferenciados por cor: follow-ups usam uma cor própria para não se confundir com reuniões confirmadas, canceladas ou que não compareceram.

> DICA: use o filtro de tipo no topo da tela ("Reunião" / "Follow-up") quando quiser olhar só um dos dois de cada vez, por exemplo para revisar apenas os retornos comerciais pendentes do dia.

## Status de uma reunião

Uma reunião pode aparecer com status diferentes, indicados pela cor do marcador: confirmada, concluída, cancelada, ou "não compareceu" (no-show). Isso ajuda a distinguir de relance, sem abrir cada item, quais compromissos do dia já foram resolvidos e quais ainda estão em aberto.

## Como um follow-up vencido aparece

Quando um follow-up passa da data prevista sem ter sido resolvido, ele conta como **follow-up vencido**. Esse número aparece:

1. No resumo do dia, na barra lateral do sistema.
2. No sino de decisões pendentes, no header, somado às aprovações aguardando decisão.
3. Na própria Agenda, junto com os demais compromissos do dia.

> ATENÇÃO: follow-ups vencidos não desaparecem sozinhos — cada um representa um cliente esperando um retorno. Trate o número no resumo do dia como uma lista de tarefas, não como uma estatística.

## Vinculação a um caso

Quando uma reunião ou um follow-up está vinculado a um card do funil, o item mostra o nome do caso na própria grade. Isso permite abrir o caso direto da Agenda, sem precisar procurá-lo depois em [[rota:/app/leads|Leads e casos]] ou em [[rota:/app/funil|Funil de vendas]].

## Criar um compromisso manualmente

Clique em um horário livre na grade (ou no botão de criar compromisso) para agendar uma reunião nova, com data, horário e, se for o caso, o link para o caso correspondente. Follow-ups, por sua vez, nascem automaticamente do fluxo de atendimento do caso — eles não costumam ser criados manualmente.

## Rotina sugerida

1. Comece o dia pela Agenda, olhando primeiro os follow-ups vencidos (eles indicam clientes já esperando).
2. Confirme as reuniões do dia e prepare o que for necessário para cada uma.
3. Ao final de uma reunião ou de um retorno, atualize o status do item (concluída, cancelada, não compareceu) para manter o painel confiável para o resto da equipe.
`,
};

export default article;
