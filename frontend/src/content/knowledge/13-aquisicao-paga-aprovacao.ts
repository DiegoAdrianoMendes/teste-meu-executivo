import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'aquisicao-paga-aprovacao',
  titulo: 'Aquisição paga: criar campanha com aprovação',
  resumo:
    'Como funciona a tela de Aquisição Paga hoje: rascunho de campanha sempre nasce aguardando aprovação humana, e nenhuma chamada é feita à Meta antes de o gate externo estar aberto.',
  categoria: 'crescimento',
  papeis: ['gestor', 'marketing'],
  paginasRelacionadas: [{ label: 'Aquisição Paga', rota: '/app/aquisicao-paga' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Explica como usar a tela de [[rota:/app/aquisicao-paga|Aquisição Paga]] para preparar campanhas de anúncio, e por que toda campanha criada ali passa por uma aprovação humana antes de qualquer chamada real à Meta Ads.

> ATENÇÃO: nenhuma campanha é publicada automaticamente. Criar uma campanha na tela de Aquisição Paga sempre gera um **rascunho aguardando aprovação**; nada é enviado à Marketing API da Meta nesse passo.

## Status de sincronização

No topo da tela, um painel mostra o status de sincronização com a conta de anúncios:

- **Campanhas sincronizadas**: quantas campanhas já existentes foram lidas da conta de anúncios conectada.
- **Gate externo**: indica se as chamadas externas (criação real de campanha) estão liberadas ou não. Por padrão, o gate vem **fechado** — ou seja, o sistema pode ler dados da conta de anúncios, mas não pode criar nada de verdade nela ainda.
- **Última leitura**: quando foi a última vez que o sistema consultou a conta de anúncios.

## Como criar um rascunho de campanha

1. Preencha os dados da campanha (nome, objetivo, orçamento e demais campos do formulário).
2. Confirme a criação. Isso cria um **rascunho aguardando aprovação humana** — nenhuma chamada é feita à Marketing API neste passo.
3. O rascunho aparece na lista "Rascunhos de campanha", com um status visível (por exemplo, "Aguardando aprovação" ou "Aprovada (simulação local)").

## Como funciona a aprovação

Depois de criado, o rascunho segue para a [[rota:/app/aprovacoes|Central de Aprovações]], onde alguém com permissão de aprovar revisa o orçamento, o objetivo e o público antes de decidir se a campanha deve seguir adiante.

1. Enviar o rascunho para aprovação registra a intenção; ainda **nenhuma campanha real é criada** nesse momento.
2. Um aprovador revisa e decide aprovar ou rejeitar.
3. Mesmo depois de aprovada, a campanha só é de fato publicada na Meta quando o gate externo estiver aberto — enquanto ele estiver fechado, tudo o que acontece fica registrado como simulação local, sem gerar gasto real.

> DICA: pense na aprovação como uma dupla checagem antes de qualquer verba real ser comprometida. Isso evita que uma campanha mal configurada (orçamento errado, público muito amplo) chegue a rodar sem revisão.

## Por que existe esse cuidado extra

Diferente de mensagens de WhatsApp, uma campanha de anúncio compromete orçamento real do escritório assim que é publicada. O fluxo de rascunho → aprovação → gate externo existe justamente para que nenhuma verba seja gasta sem uma decisão humana explícita, mesmo quando a automação de IA sugere ou monta o rascunho inicial.

## Relação com o Rastreamento

Depois que uma campanha estiver publicada de verdade, o desempenho dela aparece em [[rota:/app/rastreamento|Rastreamento]], incluindo CPL, CAC e ROAS — veja [[rota:/app/conhecimento?artigo=cpl-cac-roas-linguagem-simples|Entender CPL, CAC e ROAS em linguagem simples]] para saber como ler esses números.
`,
};

export default article;
