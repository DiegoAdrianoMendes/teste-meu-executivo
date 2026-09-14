import type { KnowledgeArticle } from './types';

const article: KnowledgeArticle = {
  slug: 'seguranca-privacidade-dados',
  titulo: 'Segurança e privacidade dos dados do escritório',
  resumo:
    'Autenticação em duas etapas (MFA), sessões administrativas com elevação temporária, dispositivos conectados e as políticas de sessão e retenção de auditoria configuráveis pela gestão.',
  categoria: 'gestao',
  papeis: ['gestor'],
  paginasRelacionadas: [{ label: 'Configurações — Segurança & auditoria', rota: '/app/configuracoes/avancadas?tab=seguranca' }],
  atualizadoEm: '2026-07-23',
  corpo: `
## Para que serve este artigo

Reúne os controles de segurança e privacidade disponíveis na aba "Segurança & auditoria" de [[rota:/app/configuracoes/avancadas?tab=seguranca|Configurações]], para quem tem permissão de administração acompanhar e ajustar.

## Autenticação em duas etapas (MFA)

O Meu Executivo permite habilitar MFA (verificação em duas etapas) para a conta de cada usuário, exigindo um código de verificação além da senha para entrar.

1. Ao habilitar, o sistema mostra um código de matrícula e pede a confirmação de um código de verificação para ativar de fato.
2. Depois de confirmado, o MFA fica habilitado localmente, e a tela mostra códigos de recuperação — guarde-os antes de sair da tela, pois eles são a forma de recuperar o acesso caso o segundo fator seja perdido.

> DICA: quanto mais pessoas do escritório tiverem MFA ativo, menor o risco de uma conta comprometida por senha vazada ou reutilizada em outro serviço.

## Sessões administrativas com elevação temporária

Ações administrativas mais sensíveis exigem uma **elevação de sessão**: mesmo já estando logado, o usuário precisa confirmar um código adicional para desbloquear temporariamente esse nível de acesso.

1. A elevação tem duração limitada (a tela mostra em quantos minutos ela expira).
2. É possível revogar uma sessão administrativa ativa manualmente, ou revogar todas de uma vez — útil se houver suspeita de uso indevido de uma sessão elevada.

## Dispositivos e sessões conectadas

A tela lista os dispositivos com sessão ativa na conta, identificados por um rótulo de dispositivo. É possível desconectar um dispositivo específico, ou revogar todas as outras sessões de uma vez (mantendo apenas a sessão atual), por exemplo depois de usar um computador compartilhado.

## Eventos de identidade

Um histórico de eventos de identidade (como tentativas de login, ativação de MFA e mudanças de sessão) fica disponível para revisão, com um botão para atualizar a lista com os eventos mais recentes.

## Política de sessão e retenção de auditoria

Na seção de auditoria, a gestão pode configurar:

1. **Timeout de sessão**: depois de quantos minutos de inatividade uma sessão comum expira automaticamente.
2. **Retenção de auditoria**: por quantos dias o sistema mantém o histórico de eventos de auditoria antes de descartá-lo.

Essas duas políticas, junto com a trilha operacional do sistema, formam a base de conformidade do escritório: quem acessou o quê, quando, e por quanto tempo os registros ficam disponíveis para revisão.

> ATENÇÃO: reduzir demais o período de retenção de auditoria diminui a capacidade de investigar um incidente que só é percebido semanas depois. Avalie a política de retenção junto com as obrigações do escritório (por exemplo, prazos de guarda de dados de clientes) antes de encurtá-la.

## Quem pode ver e alterar essas configurações

O painel de segurança e auditoria só fica visível para quem tem permissão de administração (admin.manage) ou de segurança/auditoria. Sem essa permissão, mesmo um usuário com acesso ao restante das Configurações não visualiza esta aba — veja [[rota:/app/conhecimento?artigo=papeis-permissoes-equipe|Papéis e permissões da equipe]] para entender como as permissões são atribuídas.
`,
};

export default article;
