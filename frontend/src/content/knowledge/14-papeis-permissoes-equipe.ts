import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'papeis-permissoes-equipe',
  titulo: 'Papéis e permissões da equipe',
  resumo:
    'Como convidar alguém para o escritório, o que é um papel (role) e como a matriz de permissões controla o que cada pessoa pode ver e fazer no sistema.',
  categoria: 'gestao',
  papeis: ['gestor'],
  paginasRelacionadas: [{ label: 'Configurações — Equipe & permissões', rota: '/app/configuracoes/avancadas?tab=equipe' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Explica como gerenciar quem tem acesso ao Meu Executivo e o que cada pessoa pode fazer, na aba "Equipe & permissões" de [[rota:/app/configuracoes/avancadas?tab=equipe|Configurações]].

## Convidar alguém para o escritório

1. Na aba "Equipe & permissões", adicione a pessoa informando os dados básicos e o papel que ela deve receber.
2. Um convite é gerado. É possível reenviar o convite (rotacionando o link) se a pessoa não tiver recebido, ou revogá-lo se foi enviado por engano.
3. A pessoa convidada acessa o link do convite para criar sua conta e entrar no workspace do escritório.

> DICA: se o link do convite não chegar por e-mail, reenvie pela própria tela em vez de criar um convite duplicado — reenviar gera um novo link válido para o mesmo convite.

## O que é um papel (role)

Um **papel** é um conjunto de permissões que definem o que uma pessoa pode ver e fazer no sistema — por exemplo, quem pode aprovar disparos, quem vê dados financeiros, ou quem pode gerenciar integrações. Cada usuário do escritório tem um ou mais papéis atribuídos, e o sistema calcula o acesso combinando todos eles.

Ao lado de cada papel, a tela mostra quantas pessoas do escritório já têm esse papel atribuído.

## Como a matriz de permissões funciona

Cada papel tem uma matriz cruzando **módulos** (as áreas do sistema, como Mensagens, Kanban, Financeiro, Configurações, Integrações, Agentes, Auditoria, entre outros) com **ações** (Ver, Criar, Editar, Excluir, Aprovar, Exportar, Gerenciar IA, Enviar mensagem, Gerenciar). Uma permissão concedida em um módulo só libera aquela ação específica — por exemplo, ter "Ver" em Financeiro não dá automaticamente "Editar".

> ATENÇÃO: a navegação lateral do sistema já respeita essas permissões automaticamente: um item de menu só aparece para quem tem "Ver" no módulo correspondente. Se alguém da equipe não está vendo uma tela que deveria, o primeiro lugar a checar é o papel atribuído a essa pessoa, não um bug de exibição.

## Perfis de acesso mais comuns em um escritório

Embora os papéis exatos sejam configuráveis, o desenho típico de um escritório usando o Meu Executivo costuma separar:

- **Gestor(a) / sócio(a)**: acesso amplo, incluindo financeiro, aprovações e configurações da conta.
- **Atendimento**: foco em conversas, casos e agenda, geralmente sem acesso a configurações administrativas.
- **Time jurídico**: acesso aos casos e ao Caso 360, com visão sobre a esteira jurídica.
- **Marketing / aquisição**: acesso a disparos, templates, rastreamento e aquisição paga.

## Trocar ou revisar o papel de alguém

Abra o cadastro da pessoa na lista de usuários e ajuste o papel atribuído. A mudança passa a valer no próximo carregamento das telas daquela pessoa — não é preciso recriar o convite nem a conta.

Para entender o que cada módulo controla na prática, veja também [[rota:/app/conhecimento?artigo=seguranca-privacidade-dados|Segurança e privacidade dos dados]], que trata da trilha de auditoria que registra o que cada pessoa fez no sistema.
`,
};

export default article;
