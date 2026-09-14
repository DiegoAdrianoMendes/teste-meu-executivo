import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'conectar-whatsapp-manual',
  titulo: 'Conectar o WhatsApp do escritório (modo manual)',
  resumo:
    'Passo a passo completo para ligar o número oficial do escritório ao Meu Executivo sem depender de aprovação prévia da Meta: criar o app, vincular o número e colar as credenciais.',
  categoria: 'conexao',
  papeis: ['gestor', 'atendimento'],
  paginasRelacionadas: [{ label: 'Central de Conexão', rota: '/app/conexoes' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Este artigo espelha, passo a passo, o assistente que você encontra em [[rota:/app/conexoes|Central de Conexão]]. Use-o para entender o que cada etapa pede antes de clicar, ou para revisar um passo que travou.

O modo manual (também chamado de B1) existe porque conectar o WhatsApp oficial do escritório não depende de o Meu Executivo ser aprovado pela Meta como parceiro técnico: o próprio escritório cria o aplicativo e conecta o próprio número. É mais trabalhoso do que um clique único, mas funciona desde o primeiro dia.

> DICA: reserve cerca de 30 a 40 minutos sem interrupção para os passos 1 a 7. A Business Verification (passo 4) pode levar de 1 a 5 dias úteis para a Meta analisar, mas isso não trava os passos seguintes — você segue usando números de teste enquanto espera.

## Antes de começar

1. Tenha em mãos o CNPJ e os documentos do escritório (contrato social ou cartão CNPJ), pois a Meta vai pedir isso na verificação da empresa.
2. Escolha o número de telefone que vai virar o WhatsApp oficial. Ele **não pode** estar em uso em nenhum WhatsApp comum, WhatsApp Business App ou outra WABA (WhatsApp Business Account) — se estiver, remova o WhatsApp desse número do celular antes de prosseguir.
3. Defina o nome de exibição (display name) que vai aparecer para os clientes: o nome real do escritório ou da marca, sem emojis e sem termos genéricos como "vendas" ou "promoção". Ele precisa ser compatível com o que já aparece no seu site.

> ATENÇÃO: pular a checagem do número (item 2) é a causa mais comum de travamento. Se o número já estiver vinculado a outra WABA, a Meta recusa a vinculação até ele ser liberado de lá.

## Passo 1 · Criar o app Business no site de desenvolvedores da Meta

Acesse developers.facebook.com, crie uma conta de desenvolvedor (se ainda não tiver) e crie um novo app do tipo "Business", escolhendo o caso de uso "Business Messaging" ou "WhatsApp". Dê um nome claro ao app, como "WhatsApp — Nome do Escritório".

## Passo 2 · Adicionar o produto WhatsApp e vincular a WABA

Dentro do app, na lista de produtos, clique em "Adicionar produto" e escolha "WhatsApp". A tela vai pedir para você criar uma nova WhatsApp Business Account (WABA) ou vincular uma existente.

1. Adicione o número de telefone do escritório.
2. Escolha a verificação por SMS ou chamada de voz e confirme o código recebido.
3. Defina o display name (o mesmo validado na seção anterior).

## Passo 3 · Business Verification (verificação da empresa)

Em business.facebook.com/settings, envie os documentos da empresa para a Meta analisar (Business Verification). Sem essa verificação concluída, o número fica limitado a enviar mensagens apenas para números de teste cadastrados manualmente — nenhum cliente real recebe mensagem ainda.

> ATENÇÃO: o prazo de análise da Meta costuma levar de 1 a 5 dias úteis. Isso é um limite da Meta, não do Meu Executivo. Enquanto isso, você pode seguir os próximos passos normalmente.

## Passo 4 · Criar o System User e gerar o token permanente

Em business.facebook.com/settings → "Usuários" → "Usuários do sistema", crie um usuário de sistema com papel de administrador.

1. Gere um token de acesso para esse usuário.
2. Na lista de permissões, marque obrigatoriamente **whatsapp_business_messaging** e **whatsapp_business_management**.
3. Escolha a validade "Nunca expira" (token permanente).
4. Copie o valor imediatamente — a Meta só mostra esse token uma única vez.

## Passo 5 · Configurar o webhook

Volte à [[rota:/app/conexoes|Central de Conexão]] no Meu Executivo: lá você encontra a Callback URL e o Verify Token específicos do seu escritório, prontos para copiar. Cole os dois valores na configuração de webhook do seu app, dentro do painel da Meta, e assine os campos de mensagens.

Cole também o **App Secret** do seu app com o time de operações do Meu Executivo — por enquanto esse valor não é enviado pelo formulário da tela, é configurado à parte.

## Passo 6 · Colar as credenciais no Meu Executivo

De volta à Central de Conexão, preencha o formulário "Conectar WhatsApp (modo manual)" com:

1. Token de acesso permanente (do passo 4).
2. Phone Number ID (encontrado na tela do produto WhatsApp, em "Detalhes da conta").
3. WhatsApp Business Account ID — WABA ID (na mesma tela).
4. PIN de verificação em duas etapas, apenas se o número já tiver um configurado.

Ao confirmar, o Meu Executivo faz uma validação automática: testa o eco do webhook e tenta um envio de teste. Se tudo estiver certo, a conexão aparece como ativa e o tier de envio do dia é exibido na tela.

## O que acontece depois

Quando o escritório quiser, pode migrar depois para o modo automático (Embedded Signup), que dispensa criar o app manualmente. Essa migração é opcional e a conexão manual que você acabou de criar continua funcionando normalmente até lá.

Se algum passo não funcionar como descrito aqui, veja [[rota:/app/conhecimento?artigo=erros-comuns-conexao|Erros comuns de conexão]] antes de repetir o processo do zero — a maioria dos travamentos tem solução rápida.
`,
};

export default article;
