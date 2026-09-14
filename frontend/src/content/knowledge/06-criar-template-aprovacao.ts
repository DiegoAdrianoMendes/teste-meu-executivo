import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'criar-template-aprovacao',
  titulo: 'Criar um template e entender a aprovação da Meta',
  resumo:
    'O que é um template de WhatsApp, por que ele precisa ser aprovado pela Meta, o que significam as categorias marketing/utility/authentication e por que marketing custa cerca de 7 vezes mais.',
  categoria: 'operacao',
  papeis: ['gestor', 'marketing', 'atendimento'],
  paginasRelacionadas: [{ label: 'Templates oficiais Meta', rota: '/whatsapp/meta-templates' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## O que é um template

No WhatsApp oficial (Cloud API), você não pode simplesmente escrever uma mensagem livre para quem não conversou com o escritório nas últimas 24 horas. Para isso existe o **template**: um modelo de mensagem pré-aprovado pela Meta, com um texto fixo e, se você quiser, campos variáveis (como o nome do cliente).

Pense no template como uma "mensagem de formulário" que a Meta revisa antes de liberar, para garantir que ninguém usa o WhatsApp para spam.

> ATENÇÃO: sem um template aprovado, o escritório não consegue iniciar conversa com um cliente que não falou com o número oficial há mais de 24 horas. É por isso que ter templates aprovados e prontos é parte essencial de operar o WhatsApp.

## Como criar um template

1. Vá até a tela de templates oficiais da Meta.
2. Escreva o texto do template, definindo se ele terá variáveis (por exemplo, "Olá, {{1}}, sua consulta está confirmada para {{2}}").
3. Escolha a categoria certa (veja a seção abaixo — isso importa para o custo).
4. Envie para análise da Meta.

O status do template aparece como:

- **Pendente na Meta**: enviado, aguardando análise.
- **Aprovado**: pronto para uso em disparos e conversas fora da janela de 24 horas.
- **Rejeitado**: a Meta recusou o texto (geralmente por soar como spam, prometer algo exagerado ou ter formatação inválida); ajuste o texto e reenvie.

> DICA: templates com frases muito genéricas de vendas ("Promoção imperdível!", "Só hoje!") são mais propensos a ser rejeitados ou perder qualidade depois. Prefira uma linguagem direta e informativa.

## As três categorias e por que o custo muda

Todo template pertence a uma categoria, escolhida por você ao criá-lo, mas a Meta pode reclassificar o template depois se achar que o conteúdo não bate com a categoria declarada:

1. **Utility (utilidade)**: mensagens operacionais esperadas pelo cliente, como confirmação de reunião, aviso de andamento de caso, lembrete de documento pendente.
2. **Authentication (autenticação)**: códigos de verificação e mensagens de segurança.
3. **Marketing**: qualquer mensagem promocional, de prospecção ou de reengajamento comercial.

> ATENÇÃO: uma mensagem de **marketing custa cerca de 7 vezes mais** do que uma mensagem de utility ou authentication na tabela de preços da Meta. Categorizar um template promocional como "utility" para pagar menos não funciona: a Meta audita o conteúdo e pode recategorizar o template automaticamente, junto com uma queda na qualidade do número. Declare a categoria real desde o início.

## Quality rating (qualidade do template)

Cada template aprovado recebe também uma nota de qualidade (verde, amarela ou vermelha), calculada pela Meta com base em como os destinatários reagem a ele (bloqueios, denúncias, respostas). Um template com qualidade em queda pode ser pausado pela Meta mesmo já estando aprovado.

1. Se um template cair para qualidade vermelha, revise o texto e considere criar uma versão nova, mais clara e menos insistente.
2. Evite reenviar o mesmo template para uma lista grande de contatos que não pediu para receber aquele tipo de mensagem — isso é a causa mais comum de queda de qualidade.

Para entender como isso se conecta ao envio seguro de mensagens em massa, veja [[rota:/app/conhecimento?artigo=disparo-seguro|Fazer um disparo seguro]].
`,
};

export default article;
