import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'funil-mover-casos',
  titulo: 'Funil: mover casos e o que cada etapa significa',
  resumo:
    'Como o quadro do Funil de vendas funciona, como arrastar (ou usar o teclado) para mover um caso de etapa, e o que normalmente representa cada etapa do funil jurídico.',
  categoria: 'operacao',
  papeis: ['gestor', 'atendimento', 'juridico'],
  paginasRelacionadas: [{ label: 'Funil de vendas', rota: '/app/funil' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Explica como usar a tela de [[rota:/app/funil|Funil de vendas]], o quadro em colunas (estilo kanban) onde cada card representa um caso ou lead em uma etapa da esteira jurídica.

## Como o quadro é organizado

Cada coluna do funil é uma etapa, e cada card é um caso. As etapas são configuráveis pelo escritório, mas o desenho típico de uma esteira jurídica segue algo como: novo lead, triagem, documentos, reunião, proposta e contrato. O nome exato das colunas do seu escritório aparece no topo de cada uma delas na tela.

> DICA: use o filtro de etapa no topo da tela para olhar só uma coluna de cada vez — útil quando o quadro completo tem muitos cards e você quer focar em um gargalo específico.

## Como mover um card de etapa

1. **Com o mouse**: clique e arraste o card da coluna atual até a coluna de destino.
2. **Pelo teclado**: selecione o card, pressione espaço para "pegar" o card e use as setas para movê-lo entre colunas e posições; pressione espaço de novo para soltar. Essa forma existe para quem não consegue ou prefere não usar arrastar-e-soltar.

Cada movimento é salvo automaticamente — não existe um botão "salvar" separado.

> ATENÇÃO: se dois usuários tentarem mover o mesmo card ao mesmo tempo, o sistema detecta o conflito e recusa o segundo movimento até a tela ser atualizada, para não sobrescrever silenciosamente a alteração de outra pessoa.

## O que a tela mostra além do quadro

1. **Etapa com mais cards (gargalo)**: a tela destaca automaticamente qual coluna está acumulando mais casos, para ajudar a enxergar onde a operação está travando.
2. **Busca e filtros**: é possível buscar por nome do caso, filtrar por origem (canal de aquisição) e por campos personalizados que o escritório tiver configurado.

## Relação com o Caso 360

Abrir um card no funil leva às mesmas informações que aparecem no painel Caso 360 dentro da conversa — veja [[rota:/app/conhecimento?artigo=atender-conversas-caso-360|Atender conversas e usar o Caso 360]]. Mover um card de etapa no funil e mudar o estágio do caso no Caso 360 são a mesma informação, vista de dois lugares diferentes: uma mudança em um reflete imediatamente no outro.

## Boas práticas ao mover casos

1. Mova o card assim que o caso realmente avançar — o funil só é útil como termômetro do escritório se refletir a realidade em tempo real, não o que "deveria" ter acontecido.
2. Se um caso travar muito tempo na mesma etapa, use isso como sinal para revisar o que está faltando (documento, retorno do cliente, decisão interna), em vez de simplesmente movê-lo adiante para "limpar" o quadro.
3. Casos perdidos ou cancelados devem ser movidos para a etapa correspondente (quando o escritório tiver uma), em vez de deixados parados nas etapas ativas — isso mantém o gargalo e as métricas do funil confiáveis.
`,
};

export default article;
