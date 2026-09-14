import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'erros-comuns-conexao',
  titulo: 'Erros comuns ao conectar o WhatsApp',
  resumo:
    'Display name recusado, número preso em outra WABA, PIN de verificação em duas etapas (133005) e verificação pendente: o que cada mensagem significa e como resolver.',
  categoria: 'conexao',
  papeis: ['gestor', 'atendimento'],
  paginasRelacionadas: [{ label: 'Central de Conexão', rota: '/app/conexoes' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Reúne os quatro problemas mais comuns durante a conexão descrita em [[rota:/app/conhecimento?artigo=conectar-whatsapp-manual|Conectar o WhatsApp do escritório (modo manual)]]. Se algo travou, procure a mensagem ou a situação mais parecida com a sua abaixo.

## Display name recusado

A Meta recusa o nome de exibição quando ele foge das diretrizes: nomes genéricos ("Promoção", "Vendas"), símbolos ou emojis, ou um nome que não bate com o que aparece no site do escritório.

1. Ajuste o nome para o nome real do escritório ou da marca, exatamente como aparece publicamente.
2. Reenvie o nome na tela do produto WhatsApp, dentro do app criado no passo 1 do artigo de conexão.
3. Aguarde a nova análise (geralmente rápida, poucas horas).

> DICA: se o escritório tiver mais de um nome popular (nome fantasia e razão social, por exemplo), use o que aparece no rodapé do site ou nas redes sociais oficiais — é o que a Meta compara.

## Número preso em outra WABA

Se o número já foi cadastrado em outra WhatsApp Business Account — de outro app, de uma agência anterior, ou de um teste antigo — a Meta não deixa vinculá-lo de novo até ele ser removido de lá primeiro.

1. Peça ao administrador da WABA anterior para excluir o número em business.facebook.com → WhatsApp Manager → Números de telefone.
2. Se não houver mais acesso a essa WABA (por exemplo, uma agência que não atende mais o escritório), abra um chamado no Business Help Center da Meta pedindo a liberação do número.
3. Depois de liberado, repita o passo 2 do artigo de conexão (vincular o número à nova WABA).

## PIN de verificação em duas etapas — erro 133005

Esse código aparece quando o número já tem a verificação em duas etapas ativada e a Meta pede o PIN de 6 dígitos configurado anteriormente.

1. Se você lembra o PIN, cole-o no campo "PIN de verificação" no passo 6 da conexão manual.
2. Se não lembra, é possível resetá-lo pela API (endpoint de reset de two-step), ou, sem acesso à API, redefinir manualmente no app WhatsApp Business instalado no celular, em Configurações → Conta → Confirmação em duas etapas.
3. Depois de obter ou redefinir o PIN, cole-o novamente na [[rota:/app/conexoes|Central de Conexão]].

> ATENÇÃO: sem o PIN correto, a vinculação falha silenciosamente algumas vezes. Se o formulário não avançar, confira primeiro se esse é o problema antes de suspeitar de token ou webhook.

## Verificação da empresa "em análise" ou "recusada"

Enquanto a Business Verification estiver "Em análise", o número funciona só com contatos de teste cadastrados manualmente — não é erro do Meu Executivo, é limite da própria Meta.

1. Se vier como "Recusada", confira em business.facebook.com/settings/security-center o motivo exato: documento ilegível, CNPJ divergente do nome do Business, ou endereço que não confere.
2. Corrija o documento apontado e reenvie.
3. O prazo de nova análise costuma repetir o ciclo de 1 a 5 dias úteis.

## Token, Phone Number ID ou WABA ID incorretos

Se a validação automática da Central de Conexão falhar depois de colar as credenciais:

1. Revise o token: ele precisa ter sido gerado com validade "Nunca expira" e com os escopos **whatsapp_business_messaging** e **whatsapp_business_management** marcados. Se algum escopo faltar, volte ao System User, gere um token novo e cole o valor atualizado.
2. Confira o Phone Number ID na tela "Detalhes da conta" do produto WhatsApp, copiando o valor exato, sem espaços.
3. Confira o WABA ID na mesma tela.

Se, mesmo assim, o erro continuar, revise um dado de cada vez em vez de trocar tudo de uma vez — isso ajuda a identificar exatamente qual campo está errado.
`,
};

export default article;
