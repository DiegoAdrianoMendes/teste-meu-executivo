import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'conectar-instagram-pagina',
  titulo: 'Conectar Instagram e Página do Facebook',
  resumo:
    'O que já é possível fazer hoje na conexão do Instagram, por que a autorização fica "preparada, aguardando aprovação" e o que muda quando a Meta aprovar o app do Meu Executivo.',
  categoria: 'conexao',
  papeis: ['gestor', 'marketing'],
  paginasRelacionadas: [{ label: 'Central de Conexão', rota: '/app/conexoes' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Explica, com honestidade sobre o estado atual, como funciona a conexão do Instagram na [[rota:/app/conexoes|Central de Conexão]]. Diferente do WhatsApp, que já funciona de ponta a ponta hoje, o Instagram está com a estrutura pronta no Meu Executivo, mas depende de uma aprovação da Meta que ainda não chegou.

> ATENÇÃO: hoje, iniciar a conexão do Instagram deixa o pedido "preparado", mas não conecta uma conta de verdade ainda. Isso não é um erro do escritório nem de digitação — é um limite temporário do processo de aprovação da Meta, explicado abaixo.

## Pré-requisitos da conta Instagram

Antes de tentar conectar, confirme que a conta do escritório atende aos pré-requisitos da própria Meta:

1. A conta Instagram precisa ser uma conta **profissional** (Business ou Creator), não uma conta pessoal.
2. Ela precisa estar vinculada a uma **Página do Facebook** do escritório.
3. A automação de mensagens do Instagram tem um teto de cerca de **200 mensagens por hora** por conta, imposto pela própria Meta — não é um limite do Meu Executivo.

## Como cadastrar a conexão hoje

1. Na [[rota:/app/conexoes|Central de Conexão]], na seção "Conectar Instagram", informe o nome da conta profissional (e o @usuário, se quiser).
2. Confirme o cadastro. Esse passo salva localmente os dados da conta, mas ainda não fala com a Meta.
3. Com a conexão cadastrada, o botão "Iniciar conexão OAuth" fica disponível.

## O que acontece ao clicar em "Iniciar conexão OAuth"

O Meu Executivo já monta a URL oficial de autorização do Instagram Login com os três escopos necessários e um código de segurança (state assinado, anti-CSRF, de validade curta) — essa parte da engenharia está pronta. O que falta é a aprovação da Meta para o app do escritório usar esses escopos com contas reais de clientes.

1. O Meu Executivo prepara a URL de autorização.
2. Você seria redirecionado à Meta para fazer login e escolher a conta profissional.
3. A Meta chamaria de volta o Meu Executivo com um código de autorização.
4. **Hoje**, como as ações externas do Meu Executivo ainda não foram aprovadas pela Meta, esse retorno só registra a intenção de conexão, com status **"Preparado, aguardando aprovação de APIs externas"** — a troca do código pelo token de verdade só acontece depois que a Meta liberar.

## O que é essa aprovação que falta (App Review)

A tela de conexão mostra um checklist chamado **"Jornada A · App Review do Instagram"**, que é feito uma única vez para todo o escritório (não por conta conectada). Ele reúne quatro itens que a Meta precisa aprovar no app do Meu Executivo:

- **instagram_business_basic**: acesso básico à conta profissional.
- **instagram_business_manage_messages**: permissão para ler e responder mensagens do Instagram.
- **instagram_business_manage_comments**: permissão para gerenciar comentários.
- **Business Verification do escritório concluída** — veja [[rota:/app/conhecimento?artigo=business-verification-meta|Entender a Business Verification da Meta]].

Esse checklist é informativo: você pode marcar manualmente o que já sabe que avançou, mas a Meta é a fonte oficial de aprovação, não o Meu Executivo.

## O que fazer enquanto isso

1. Deixe a conexão Instagram cadastrada (passo acima), para que o vínculo já esteja pronto assim que a aprovação sair.
2. Priorize concluir a Business Verification do escritório, já que ela é pré-requisito tanto para o WhatsApp quanto para o Instagram.
3. Acompanhe o WhatsApp, que já opera de ponta a ponta hoje — veja [[rota:/app/conhecimento?artigo=conectar-whatsapp-manual|Conectar o WhatsApp do escritório (modo manual)]].

Assim que a aprovação da Meta sair, a conexão Instagram passa a completar o fluxo OAuth normalmente, sem exigir que você refaça o cadastro da conta.
`,
};

export default article;
