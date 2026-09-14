# Dossiê Tech Provider — WhatsApp Business (Meta)

> Preparação para o proprietário protocolar. Nenhuma etapa de Business Verification,
> App Review ou submissão à Meta foi executada por esta sessão — todos os passos
> abaixo dependem de contas reais (Business Manager, developers.facebook.com) e
> aprovação humana.

## (a) Pré-requisitos

| Pré-requisito | Estado |
|---|---|
| Business Manager verificado da KLM | **A confirmar com o proprietário.** Não há evidência no repositório de que a verificação já foi concluída; `INFRA_AUDIT.md` é registro histórico não revalidado e não cobre Business Manager. |
| Domínio com política de privacidade e termos de uso publicados | **A confirmar.** Buscar `desburocratize.com/privacidade` e `/termos` (ou path equivalente) antes de preencher o formulário do app Meta — a Meta exige URLs públicas e acessíveis para ambos. Se não existirem, é bloqueador para o passo (b.1). |
| Data Deletion Callback URL | **GAP — endpoint não existe hoje.** Busca em `app/api/v1/endpoints/` não encontrou nenhuma rota de "data deletion callback" (exigência da Meta para apps que processam dados de usuários via login/API: `https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback`). Duas opções: (1) implementar um endpoint que recebe o `POST` assinado da Meta e retorna `{url, confirmation_code}`, registrado como pendência de desenvolvimento fora do escopo desta entrega (que só mexe em infra/Docker); (2) usar a alternativa da Meta de uma página de instruções manuais de exclusão de dados (aceita como substituto do callback em apps que não fazem login social — aplicável aqui, já que a integração é Business Messaging, não Facebook Login de usuário final). Recomendação: opção (2) para desbloquear o cadastro do app agora, com a opção (1) registrada como item de backlog. |

## (b) Passo a passo em developers.facebook.com

1. **Criar app Business**: developers.facebook.com → My Apps → Create App → tipo
   "Business" → caso de uso "Other" ou "Business Messaging" (a Meta reorganiza esse
   menu periodicamente; escolher o fluxo que leva ao produto WhatsApp). Nome
   sugerido: "KLM Legal Hub" ou o nome comercial vigente da plataforma.
2. **Adicionar produto WhatsApp** ao app: painel do app → Add Product → WhatsApp →
   Set Up. Isso cria automaticamente um número de teste e uma WABA de teste,
   suficiente para começar a configurar sem ainda ter Business Verification completa.
3. **Configurar Embedded Signup**:
   - App → WhatsApp → Configuration (ou "Embedded Signup" conforme o menu atual).
   - Gerar um `config_id` (Configuration ID do Facebook Login for Business,
     associado ao produto WhatsApp Embedded Signup).
   - Registrar os domínios permitidos (`Allowed Domains for the JavaScript SDK`):
     `https://desburocratize.com` (e o domínio de staging, se houver).
   - Esses valores alimentam `META_APP_ID`, `META_CONFIG_ID` (ver
     `docs/ENV_PRODUCAO_TEMPLATE.md`, seção "Meta Embedded Signup").
4. **Webhooks**:
   - URL de produção: `https://api.desburocratize.com/api/v1/whatsapp/webhook`
     (rota já implementada — ver "O que já está pronto" abaixo).
   - Verify token: gerar um valor aleatório e usá-lo tanto no painel da Meta quanto
     em `WHATSAPP_VERIFY_TOKEN` (`.env` de produção).
   - Campos a assinar (webhook fields): no mínimo `messages`,
     `message_template_status_update`, `account_update`. Avaliar também
     `messaging_handovers` se o backend vier a usar Handover Protocol (não é o
     caso hoje).
   - Testar o eco: a Meta faz uma verificação `GET` com `hub.challenge` contra a URL
     configurada — a rota `GET /api/v1/whatsapp/webhook` (`app/api/v1/endpoints/whatsapp.py`,
     linha ~483) já implementa essa resposta.

## (c) App Review

### Permissões a solicitar e textos de justificativa

A Meta exige, para cada permissão avançada, uma descrição do caso de uso em inglês.
Os textos abaixo descrevem honestamente o uso real do KLM Legal Hub (SaaS jurídico
que atende escritórios de advocacia brasileiros, com atendimento a clientes via
WhatsApp).

**Fase 1 — obrigatórias para o WhatsApp Cloud API funcionar em produção:**

1. **`whatsapp_business_messaging`**
   > "KLM Legal Hub is a SaaS platform for Brazilian law firms. Law firms use our
   > platform to communicate with their own clients over WhatsApp: qualifying new
   > leads, answering case status questions, scheduling consultations, and sending
   > document signature requests. Each law firm connects their own WhatsApp Business
   > number (Business-Initiated and User-Initiated conversations) and only messages
   > their own clients who have an existing attorney-client relationship or an
   > active inbound inquiry. We request `whatsapp_business_messaging` to send and
   > receive messages, message templates, and delivery/read status updates on
   > behalf of the connected law firm's WhatsApp Business Account."

2. **`whatsapp_business_management`**
   > "We request `whatsapp_business_management` to let each connected law firm
   > manage their own WhatsApp Business Account through our platform: viewing
   > phone number quality rating and messaging limits, managing message templates
   > (creating and submitting templates for the firm's own client communications),
   > and monitoring account health. This is used exclusively within the scope of
   > the WhatsApp Business Accounts our platform's users have explicitly connected
   > via Embedded Signup or manual credential entry."

**Fase 2 (canais sociais) — não solicitar agora, registrar para quando F3/F4 do
plano estiverem prontos (`docs/PLANO_META_MAXIMO_2026-07-23.md`):**

3. **`pages_messaging`**
   > "KLM Legal Hub allows connected law firms to also receive and respond to
   > messages sent to their Facebook Page through our unified inbox, alongside
   > WhatsApp. We request `pages_messaging` to send and receive messages on behalf
   > of the Page the law firm has explicitly connected and authorized."

4. **`instagram_business_basic`** / **`instagram_business_manage_messages`**
   > "KLM Legal Hub allows connected law firms to receive and respond to Instagram
   > Direct messages from prospective clients through our unified inbox. We request
   > Instagram Business messaging permissions to read and send messages on behalf
   > of the Instagram professional account the law firm has explicitly connected."

### Roteiro do screencast exigido

A Meta exige um vídeo de tela mostrando o fluxo completo, ponta a ponta, dentro do
próprio produto (não um mockup). Roteiro sugerido, gravado durante a conexão piloto
real da KLM (mesma captura que alimenta os prints do wizard, ver
`docs/PLANO_META_MAXIMO_2026-07-23.md` W6):

1. Login no KLM Legal Hub como usuário do escritório.
2. Navegação até `/app/conexoes` (Central de Conexão).
3. Clique em "Conectar WhatsApp" → fluxo Embedded Signup (popup da Meta: login,
   seleção/criação de Business, criação de WABA, número, verificação, display name).
4. Retorno ao KLM: confirmação de conexão bem-sucedida, template inicial enviado.
5. Envio de uma mensagem de teste real para um número de teste.
6. Recebimento da resposta no inbox unificado do KLM
   (`ConversationsPage.tsx`), mostrando a mensagem chegando em tempo real.
7. (Se `whatsapp_business_management` estiver no mesmo review) mostrar o painel de
   templates e o indicador de tier/quality do número.

## (d) Cronograma realista e ordem recomendada

| Etapa | Prazo típico (documentação pública da Meta) |
|---|---|
| Business Verification (Business Manager) | 1-5 dias úteis (pode se estender se a Meta pedir documentos adicionais) |
| Criação do app + configuração básica (produto WhatsApp, Embedded Signup, webhooks) | horas, sem dependência externa |
| App Review — Advanced Access de `whatsapp_business_messaging` + `whatsapp_business_management` | 5-10 dias úteis por rodada (contar reenvio se a primeira submissão for recusada) |
| App Review — permissões sociais (Fase 2: `pages_messaging`, `instagram_business_*`) | 5-10 dias úteis por rodada, feito depois, quando F3/F4 estiverem prontas |

**Ordem recomendada** (consistente com `docs/PLANO_META_MAXIMO_2026-07-23.md` seção 8,
risco 1: "App Review da Meta é serial e demorado: iniciar Business Verification e
submissões na F1, não na F3"):

1. Resolver os pré-requisitos da seção (a) — especialmente política de
   privacidade/termos publicados e a decisão sobre Data Deletion Callback.
2. Iniciar Business Verification em paralelo ao desenvolvimento do wizard (não
   esperar o wizard ficar pronto para começar a verificação — são dependências
   independentes).
3. Criar o app e configurar produto WhatsApp + Embedded Signup + webhooks
   (passos b.1 a b.4) assim que o domínio de produção estiver ativo (depende da
   subida AWS, `docs/RUNBOOK_AWS_FASE1.md`).
4. Submeter App Review de `whatsapp_business_messaging` +
   `whatsapp_business_management` assim que houver uma conexão piloto real
   gravável para o screencast.
5. Só depois, em paralelo à Fase 2/3 do plano, preparar as permissões sociais.

## (e) O que o sistema já tem pronto para cada exigência

| Exigência da Meta | Já pronto no repositório | Caminho |
|---|---|---|
| Verificação do webhook (`hub.challenge`) | Sim | `app/api/v1/endpoints/whatsapp.py` (`GET /webhook`, linha ~483) |
| Recebimento de eventos do webhook | Sim | `app/api/v1/endpoints/whatsapp.py` (`POST /webhook`, linha ~502) |
| Callback de conclusão do Tech Provider (onboarding server-to-server) | Sim | `app/api/v1/endpoints/tech_providers.py` (`POST` callback, linha ~34) |
| Embedded Signup do lado do tenant (conexão do escritório) | Sim | `app/api/v1/endpoints/whatsapp.py` (`embedded_signup_connect`, linha ~669) |
| Canal oficial da PLATAFORMA (Tech Provider) — conexão manual e via Embedded Signup | Sim | `app/api/v1/endpoints/platform_official_whatsapp.py` (`PUT /{role}/manual`, `POST /{role}/embedded-signup`) |
| Teste de envio pós-conexão | Sim | `app/api/v1/endpoints/platform_official_whatsapp.py` (`POST /{role}/test`) |
| Intake de credenciais manuais por tenant (modo B1/BYOA) | Sim, porém descrito no plano como "existente e hoje órfão" (sem tela) | `POST /connections/manual` — ver `docs/PLANO_META_MAXIMO_2026-07-23.md`, Jornada B, Modo B1 |
| Gate de segurança para qualquer envio real | Sim | `KLM_EXTERNAL_ACTIONS_ENABLED` / `_require_external_delivery` em `app/api/v1/endpoints/tech_providers.py` e `platform_official_whatsapp.py` |
| Wizard de UI passo a passo com prints reais (Jornada A e B) | **Não** — é o item W6 do plano, ainda não implementado | `docs/PLANO_META_MAXIMO_2026-07-23.md`, seção W6 |
| Data Deletion Callback | **Não** — gap confirmado nesta auditoria (ver seção (a)) | — |
| Página pública de política de privacidade / termos | **A confirmar com o proprietário** — não verificado nesta sessão (fora do escopo de infra) | — |

## Resumo de gaps para o proprietário resolver antes de submeter

1. Confirmar (ou publicar) política de privacidade e termos de uso em URL pública.
2. Decidir a estratégia de Data Deletion: implementar callback (item de backlog de
   desenvolvimento) ou usar a página de instruções manuais aceita pela Meta como
   alternativa mais rápida.
3. Confirmar se o Business Manager da KLM já está verificado; se não, iniciar a
   verificação o quanto antes (é o item mais lento e não paraleliza com nada).
4. Gravar o piloto real (conexão + envio + recebimento) para servir de screencast do
   App Review e de fonte dos prints do wizard (W6).
