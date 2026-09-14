import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'recuperar-historico-anuncios-backfill',
  titulo: 'Recuperar histórico de anúncios (backfill)',
  resumo:
    'Como funciona a recuperação retroativa de conversas que vieram de anúncio (CTWA), o que a simulação mostra antes de gravar, e por que rodar de novo não duplica nada.',
  categoria: 'crescimento',
  papeis: ['gestor', 'marketing'],
  paginasRelacionadas: [{ label: 'Rastreamento', rota: '/app/rastreamento' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Detalha a ferramenta "Recuperar histórico de anúncios (CTWA)", disponível em [[rota:/app/rastreamento|Rastreamento]], que resgata conversas de WhatsApp recebidas **antes** de o rastreamento de anúncios estar ativo. Para entender o que é CTWA, veja primeiro [[rota:/app/conhecimento?artigo=rastreamento-ctwa-roi|Ligar o rastreamento de anúncios (CTWA) e ler o ROI]].

> ATENÇÃO: essa ferramenta exige a permissão de administração (admin.manage). Se o botão aparecer desabilitado, é porque o seu usuário não tem esse nível de acesso — peça a alguém da gestão do escritório.

## Por que isso existe

O rastreamento de CTWA funciona automaticamente para conversas novas assim que o WhatsApp oficial está conectado. Mas se o escritório já recebia mensagens vindas de anúncios do Instagram ou Facebook antes disso, essas conversas antigas não tinham como ser identificadas como vindas de anúncio — a informação do clique (referral) já estava na mensagem, só não era lida ainda. O backfill varre esse histórico e recupera a atribuição, sem apagar ou alterar nenhuma evidência existente.

## Passo a passo

1. Em [[rota:/app/rastreamento|Rastreamento]], clique em "Recuperar histórico de anúncios (CTWA)".
2. O sistema roda automaticamente uma **simulação** (nada é gravado ainda). Ela mostra quatro números:
   - **Mensagens varridas**: quantas mensagens do histórico foram analisadas.
   - **Vieram de anúncio**: quantas dessas mensagens têm a marca de que vieram de um clique em anúncio (referral).
   - **Recuperáveis agora**: quantas podem virar evento de aquisição de fato, se você confirmar.
   - **Já recuperados / sem thread**: mensagens que já tinham sido recuperadas antes, ou que vieram de anúncio mas não puderam ser vinculadas a uma conversa existente.
3. Se "Recuperáveis agora" for maior que zero, confirme para gravar os eventos de verdade. Se for zero, não há nada para recuperar — ou porque não existem anúncios no histórico, ou porque já foi tudo recuperado antes.

## Depois de confirmar

O relatório final mostra quantos eventos de aquisição foram recuperados e vinculados às conversas correspondentes. Se alguma mensagem com referral de anúncio não pôde ser vinculada a uma conversa (por exemplo, uma conversa que não existe mais no sistema), isso também aparece separadamente no resultado.

> DICA: rodar a recuperação de novo mais tarde não duplica nada — o sistema reconhece o que já foi recuperado antes e mostra esse número separado em "já recuperados", ignorando-os na nova gravação.

## Quando vale a pena rodar

1. Logo depois de conectar o WhatsApp oficial pela primeira vez, se o número já recebia mensagens de campanhas antes da migração para o Meu Executivo.
2. Depois de qualquer período em que o rastreamento tenha ficado temporariamente indisponível.
3. Sempre que os números de [[rota:/app/rastreamento|Rastreamento]] parecerem menores do que o esperado para um período em que se sabe que havia campanha ativa — o backfill é o primeiro lugar a checar antes de desconfiar da métrica em si.
`,
};

export default article;
