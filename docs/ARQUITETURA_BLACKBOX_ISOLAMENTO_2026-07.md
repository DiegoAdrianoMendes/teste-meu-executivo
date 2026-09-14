# Arquitetura da Central Blackbox

## Objetivo

A Central Blackbox é o backoffice dos funcionários do Meu Executivo. Ela não é
uma área administrativa do produto contratado e não compartilha navegação,
sessão ou identidade com o aplicativo dos escritórios.

O desenho adota negação por padrão: qualquer combinação de host, bundle,
credencial, identidade ou permissão incompatível interrompe a operação.

## Zonas de confiança

| Zona | Runtime | Rota canônica | Usuários |
| --- | --- | --- | --- |
| Conta contratante | `workspace` | `/app` | Dono e funcionários do escritório |
| Backoffice do SaaS | `platform` | `/blackbox` | Titular e funcionários do Meu Executivo |
| Acesso assistido | `support` | Sessão temporária própria | Funcionário autorizado, mediante concessão |

- O frontend da conta e o Blackbox são artefatos de build distintos.
- Cada artefato parte de um grafo de módulos próprio. O bundle do cliente não
  importa o shell, a home ou o painel administrativo; o bundle Blackbox não
  importa o shell nem as conversas do workspace.
- O verificador de bundle interrompe o build se encontrar módulos da outra
  superfície.
- Em desenvolvimento local, o app usa a porta `4173` e o Blackbox usa `4174`.
- Em produção, cada runtime exige origem HTTPS e host próprios.
- O Nginx do app responde `404` para `/blackbox`; o Nginx interno usa allowlist:
  serve o SPA somente sob `/blackbox` e responde `404` para qualquer outra rota
  visual. O smoke de publicação prova as negativas.
- A rota visual `/platform` não existe. O prefixo `/api/v1/platform` permanece
  apenas como namespace interno das APIs do backoffice.

## Identidades mutuamente exclusivas

Uma pessoa usa identidades diferentes se atuar nos dois lados do negócio.

- Identidade de funcionário do SaaS nunca recebe `WorkspaceMembership`.
- Identidade de cliente nunca é promovida a funcionário do SaaS.
- Suspender um funcionário não o transforma em cliente: sua identidade
  continua pertencendo ao Blackbox e apenas perde a autorização operacional.
- Convites, criação de contas, onboarding e rotinas administrativas aplicam a
  mesma regra canônica.
- O PostgreSQL bloqueia tanto a membership de uma identidade Blackbox quanto a
  promoção de uma identidade que já pertença a um workspace.

## Sessões e cookies

- Login do cliente: `/login`, audiência `workspace`.
- Login interno: `/blackbox/login`, audiência `platform`.
- Tokens são emitidos sem contexto de workspace para o Blackbox.
- Tokens internos que contenham `workspace_id`, `membership_id` ou `role` são
  rejeitados.
- Cookies de renovação são diferentes, host-only, `HttpOnly`, `Secure` em
  produção e não usam domínio compartilhado.
- O backend deriva a superfície da identidade atual no banco; não confia apenas
  na rota ou no estado do frontend.
- Alterar função, permissões ou situação do funcionário avança o `auth_epoch` e
  invalida as sessões existentes.

## Painéis por função

Todos os painéis vivem dentro do shell próprio do `/blackbox`, mas cada
funcionário recebe somente sua área de trabalho e os módulos permitidos.

| Função | Prioridade inicial | Escopo |
| --- | --- | --- |
| SDR e gestão comercial | Comercial | Leads e vendas do SaaS |
| Onboarding | Contas | Implantações e ativação |
| Sucesso do cliente | Contas | Saúde, adoção e expansão |
| Suporte | SAC e tickets | Triagem, resposta e resolução |
| Suporte técnico | SAC e tickets | Falhas, integrações e incidentes |
| Financeiro | Financeiro | Assinaturas, cobrança, MRR e ARR |
| Marketing | Site | Conteúdo e aquisição da plataforma |
| Comunidade | Comunicados | Avisos e relacionamento |
| Compliance | Erros e incidentes | Acessos, evidências e exceções |
| Produto | Erros e incidentes | Feedback, versões e priorização |
| Engenharia e TI | Erros e incidentes | Infraestrutura e resposta técnica |
| Titular | Visão executiva | Governança integral da plataforma |

O menu, a rota e a API usam a mesma matriz `módulo + ação`. Uma rota não
catalogada é negada. Custos internos, tarifas e saúde Meta continuam exclusivos
do titular.

## Acesso assistido

Funcionários do SaaS não entram no `/app` com a sessão Blackbox. Quando uma
investigação exigir dados da conta:

1. o funcionário solicita acesso com motivo e escopo;
2. um responsável autorizado aprova;
3. o sistema emite uma credencial `support` separada, curta e vinculada à
   concessão;
4. a experiência continua em `/blackbox/support-session`; a sessão interna
   nunca navega para `/app`;
5. o backend restringe o acesso ao workspace, ações e prazo aprovados;
6. visualizações e alterações são auditadas;
7. a expiração encerra o acesso sem reutilizar a sessão do Blackbox.

Não há impersonação permanente nem troca silenciosa de superfície.

## Barreiras obrigatórias

1. **Rede:** hosts independentes e CORS explícito.
2. **Artefato:** builds `workspace` e `platform` separados.
3. **Roteamento:** guard de runtime antes das rotas protegidas.
4. **Autenticação:** audiência, cookie e sessão próprios.
5. **Identidade:** categorias disjuntas no serviço e no banco.
6. **Autorização:** pacote de função e permissão por operação.
7. **Dados:** RLS e contexto temporário para suporte.
8. **Auditoria:** alteração de acesso, concessão e ação sensível registradas.

## Gates para produção

- Migração deve recusar o deploy se encontrar identidade dupla.
- Os dois builds devem passar separadamente e não podem conter a rota visual
  da outra superfície.
- Testes negativos devem provar cliente no Blackbox, funcionário no app,
  funcionário em painel alheio, token interno com workspace e sessão suspensa.
- MFA real é obrigatório para todas as funções internas. A simulação local não
  constitui mecanismo de produção; o lançamento do Blackbox permanece
  bloqueado no login e no workflow de publicação até o provedor e o fluxo de
  recuperação serem validados. A variável de ambiente de publicação
  `KLM_BLACKBOX_MFA_VERIFIED` não substitui a prova técnica do provedor.
- O proxy deve encaminhar somente o host do Blackbox ao bundle `platform`.
- Espelhos e CDNs usam buckets, distribuições e invalidações separados; um
  artefato nunca é sincronizado para o destino da outra superfície.

## Critério de aceite

O isolamento só é considerado pronto quando as barreiras de aplicação, banco e
infraestrutura falham de forma segura e os testes negativos passam nos dois
runtimes. Aparência de menu separado, isoladamente, não comprova segurança.
