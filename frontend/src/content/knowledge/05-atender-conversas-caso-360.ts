import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'atender-conversas-caso-360',
  titulo: 'Atender conversas e usar o Caso 360',
  resumo:
    'Como funciona a tela de conversas no dia a dia e como o painel Caso 360 reúne, ao lado do chat, tudo o que se sabe sobre aquele cliente e aquele caso.',
  categoria: 'operacao',
  papeis: ['atendimento', 'juridico'],
  paginasRelacionadas: [{ label: 'Conversas', rota: '/app/conversas' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Explica como usar a tela de [[rota:/app/conversas|Conversas]] no dia a dia e como o painel lateral, chamado de **Caso 360**, ajuda a responder qualquer cliente sem precisar procurar informação em outro lugar.

## A tela de conversas

A tela de conversas tem duas colunas principais: a lista de conversas à esquerda e o chat aberto no centro. Quando você abre uma conversa, um terceiro painel aparece à direita: o Caso 360.

1. A lista à esquerda mostra as conversas mais recentes primeiro, com um indicador de mensagens não lidas.
2. Ao clicar em uma conversa, o histórico de mensagens abre no centro, com a mesma aparência de um WhatsApp normal.
3. Use a caixa de busca no topo da lista para encontrar uma conversa por nome, telefone ou trecho de mensagem.

> DICA: se o campo de digitação mostrar um aviso de "janela fechada — requer template", significa que o cliente não manda mensagem há mais de 24 horas. Veja o artigo [[rota:/app/conhecimento?artigo=disparo-seguro|Fazer um disparo seguro]] para entender por que essa regra existe e o que fazer nesse caso.

## O que é o Caso 360

O Caso 360 é o painel que aparece ao lado da conversa reunindo tudo o que o escritório já sabe sobre aquele cliente e aquele caso, para você não precisar sair da tela de conversas para consultar outro sistema. Ele é dividido em seções:

- **Dados do cliente e card**: nome, contato e um atalho para abrir o cadastro completo do cliente (Cliente 360).
- **Origem e atribuição**: de onde veio esse contato (por exemplo, um anúncio do Instagram ou uma indicação), com atalho para o [[rota:/app/rastreamento|Rastreamento]].
- **Jornada dos fluxos**: em qual etapa de atendimento automatizado o caso está, com atalho para gerenciar os fluxos.
- **Saúde e estratégia jurídica**: o estágio do caso na esteira jurídica (novo lead, triagem, documentos, reunião, proposta, contrato).
- **Contrato, procuração e proposta**: estado dos documentos formais do caso.
- **Honorários e financeiro**: informações financeiras do caso, quando você tem permissão para ver.
- **Auditor independente**: um resumo de qualidade sobre o atendimento daquele caso.
- **Log interno dos agentes**: o que os agentes de IA fizeram automaticamente nesse caso, para você poder conferir.
- **Linha do tempo**: o histórico completo de eventos do caso, do primeiro contato até agora.

## Fluxo de atendimento sugerido

1. Ao abrir uma conversa nova, olhe primeiro a seção "Dados do cliente e card" para saber quem é a pessoa.
2. Confira "Saúde e estratégia jurídica" para saber em que etapa o caso está e o que falta.
3. Responda a mensagem no chat central normalmente.
4. Se precisar avançar o caso (por exemplo, enviar uma proposta ou pedir um documento), use os atalhos das seções do Caso 360 em vez de procurar em outra tela.

> ATENÇÃO: o Caso 360 mostra dados vivos, não uma cópia. Uma mudança feita ali (por exemplo, editar a etapa do caso) reflete imediatamente em [[rota:/app/leads|Leads e casos]] e em [[rota:/app/funil|Funil de vendas]].

## Quando um caso muda de responsável

Se o caso for transferido para outra pessoa da equipe, o histórico completo do Caso 360 (incluindo a linha do tempo e o log de agentes) continua visível para quem assumir — nada se perde na troca.
`,
};

export default article;
