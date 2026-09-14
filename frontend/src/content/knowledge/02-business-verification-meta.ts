import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'business-verification-meta',
  titulo: 'Entender a Business Verification da Meta',
  resumo:
    'O que é a verificação da empresa exigida pela Meta, por que ela existe, quais documentos costuma pedir, quanto tempo leva e o que dá para fazer enquanto ela está em análise.',
  categoria: 'conexao',
  papeis: ['gestor', 'atendimento'],
  paginasRelacionadas: [{ label: 'Central de Conexão', rota: '/app/conexoes' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Este artigo aprofunda o passo 3 de [[rota:/app/conhecimento?artigo=conectar-whatsapp-manual|Conectar o WhatsApp do escritório (modo manual)]]: a Business Verification, o processo pelo qual a Meta confirma que o Business Manager pertence de fato ao escritório. Leia aqui se quiser entender o "porquê" antes de enviar os documentos, ou se a verificação estiver demorando mais do que o esperado.

## Por que a Meta exige essa verificação

A Meta usa a Business Verification para confirmar que quem está enviando mensagens em nome de uma empresa é realmente essa empresa, e não alguém se passando por ela. É uma proteção contra fraude e spam que vale para qualquer negócio que use a Cloud API do WhatsApp, o Instagram Business ou os anúncios da Meta — não é uma exigência específica do Meu Executivo.

> ATENÇÃO: sem a verificação concluída, o número do WhatsApp fica limitado a enviar mensagens apenas para contatos de teste cadastrados manualmente. Nenhum cliente real recebe mensagem enquanto isso.

## Onde fazer a verificação

O envio dos documentos acontece em business.facebook.com/settings, na seção "Segurança do Business" (Business Verification). É o mesmo Business Manager criado no passo 1 do artigo de conexão do WhatsApp.

## Documentos que a Meta costuma pedir

A lista exata pode variar conforme o país e o tipo de negócio, mas para um escritório brasileiro os documentos mais comuns são:

1. **Documento de constituição da empresa**: contrato social ou cartão CNPJ, com o nome legal do escritório.
2. **Comprovante de endereço comercial**: conta de consumo, contrato de locação ou documento oficial recente com o mesmo endereço cadastrado no Business Manager.
3. **Site ou presença digital**: um domínio ou página que comprove a atividade do escritório, com informações de contato compatíveis com o que foi cadastrado.

> DICA: o nome legal, o endereço e o telefone informados no Business Manager precisam bater exatamente com o que aparece nos documentos enviados e no site do escritório. Divergências de grafia (razão social abreviada, endereço desatualizado) são a causa mais comum de recusa.

## Quanto tempo leva

O prazo de análise costuma levar de 1 a 5 dias úteis, mas é um prazo definido pela Meta — o Meu Executivo não tem como acelerar essa etapa. Enquanto o status estiver "Em análise", isso não é um erro do sistema.

## O que dá para fazer enquanto espera

A verificação não trava o restante da configuração. Enquanto ela está em análise, o escritório pode:

1. Seguir os passos 4 a 6 de [[rota:/app/conhecimento?artigo=conectar-whatsapp-manual|Conectar o WhatsApp do escritório (modo manual)]] (System User, token, webhook e credenciais no Meu Executivo).
2. Testar o envio e recebimento de mensagens usando números de teste cadastrados manualmente no painel da Meta.
3. Preparar os primeiros templates de mensagem em [[rota:/app/conhecimento?artigo=criar-template-aprovacao|Criar um template e entender a aprovação da Meta]], já que a aprovação de template é um processo separado da Business Verification.

## Se a verificação for recusada

Se o status vier como "Recusada", o painel de Segurança do Business mostra o motivo. Os mais comuns são documento ilegível, CNPJ divergente do nome cadastrado no Business, ou endereço que não confere com o comprovante enviado.

1. Corrija exatamente o ponto apontado pela Meta, sem trocar outros dados que não foram questionados.
2. Reenvie os documentos corrigidos.
3. O prazo de nova análise costuma repetir o ciclo de 1 a 5 dias úteis.

Se a verificação continuar travada mesmo depois de duas tentativas corrigidas, veja [[rota:/app/conhecimento?artigo=erros-comuns-conexao|Erros comuns de conexão]] para outros pontos que costumam ser confundidos com problema de verificação.
`,
};

export default article;
