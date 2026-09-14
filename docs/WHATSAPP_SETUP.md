# Guia de Configuração — WhatsApp Business Cloud API (Meta)

> **GUIA FUTURO, NÃO EXECUTAR NESTA FASE.** Nenhuma credencial Meta/AWS está
> presumida como existente ou validada. O onboarding real só começa depois da
> aprovação explícita descrita em
> [KLM_HUB_PLANO_E_APROVACAO_LOCAL.md](./KLM_HUB_PLANO_E_APROVACAO_LOCAL.md), com
> secrets recebidos por canal seguro e gates de entrega ainda fechados no canário.

**Projeto:** KLM Legal Hub — SDR Jurídico Trabalhista v1  
**Responsável:** Equipe de Implantação KLM  
**Versão da API:** v20.0

---

## Pré-requisitos

Antes de começar, você precisa ter:

- Conta no [Meta for Developers](https://developers.facebook.com) com acesso de administrador
- Conta do WhatsApp Business (WABA) já criada para o escritório KLM Advogados
- Número de telefone dedicado para o WhatsApp Business (não pode ser usado no WhatsApp pessoal)
- Acesso futuro ao secret manager escolhido, ainda não fornecido nem validado

---

## Passo 1 — Criar ou Acessar o App no Meta for Developers

1. Acesse [developers.facebook.com](https://developers.facebook.com)
2. Clique em **Meus Apps** → **Criar App**
3. Selecione o tipo: **Business**
4. Preencha:
   - **Nome do App:** `KLM Legal Hub`
   - **E-mail de contato:** seu e-mail
   - **Conta Business:** selecione a conta da KLM Advogados
5. Clique em **Criar App**

> Se o app já existe, apenas acesse-o e pule para o Passo 2.

---

## Passo 2 — Adicionar o Produto WhatsApp

1. No painel do app, clique em **Adicionar Produto**
2. Encontre **WhatsApp** e clique em **Configurar**
3. Selecione a **WhatsApp Business Account (WABA)** da KLM Advogados
4. Clique em **Continuar**

---

## Passo 3 — Obter as Credenciais Necessárias

Navegue até **WhatsApp → Configuração da API** no painel do app.

### 3.1 Phone Number ID

- Visível na seção **"De"** da página de configuração
- Formato: número longo (ex: `123456789012345`)
- **Anote este valor** — será o `WHATSAPP_PHONE_NUMBER_ID`

### 3.2 WhatsApp Business Account ID (WABA ID)

- Visível logo abaixo do Phone Number ID
- Formato: número longo (ex: `987654321098765`)
- **Anote este valor** — será o `WHATSAPP_BUSINESS_ACCOUNT_ID`

### 3.3 Access Token (Token de Acesso)

Para produção, você precisa de um **token permanente** via System User:

1. Acesse [business.facebook.com](https://business.facebook.com)
2. Vá em **Configurações** → **Usuários** → **Usuários do Sistema**
3. Clique em **Adicionar** → crie um usuário do sistema com função **Admin**
4. Clique no usuário criado → **Gerar Token Novo**
5. Selecione o app `KLM Legal Hub`
6. Permissões necessárias:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
7. Clique em **Gerar Token**
8. **Copie o token** — será o `WHATSAPP_ACCESS_TOKEN`

> ⚠️ **Importante:** Tokens de usuário do sistema não expiram, ao contrário dos tokens temporários da página de configuração.

### 3.4 App Secret

1. No painel do app, vá em **Configurações** → **Básico**
2. Clique em **Mostrar** ao lado de **Chave Secreta do App**
3. **Copie o valor** — será o `WHATSAPP_APP_SECRET`

---

## Passo 4 — Configurar o Webhook

O webhook recebe notificações em tempo real quando clientes enviam mensagens.

### 4.1 URL do Webhook

A URL HTTPS será criada e validada somente na fase de implantação aprovada. Use o
valor exibido pelo onboarding do KLM Hub; este arquivo histórico não define um
host de produção.

### 4.2 Token de Verificação

O token será gerado aleatoriamente no onboarding aprovado e armazenado no secret
manager. Não existe token padrão no código ou neste guia.

### 4.3 Configurar no Meta

1. No painel do app, vá em **WhatsApp → Configuração**
2. Clique em **Editar** na seção **Webhook**
3. Preencha:
   - **URL de Callback:** use a URL HTTPS exibida pelo onboarding aprovado
   - **Token de Verificação:** use o valor gerado e armazenado no secret manager
4. Clique em **Verificar e Salvar**
5. O Meta vai fazer uma requisição GET para o endpoint — o backend vai responder automaticamente

### 4.4 Assinar os Campos do Webhook

Após verificar, assine os seguintes campos:
- ✅ `messages` — mensagens recebidas dos clientes
- ✅ `message_deliveries` — confirmações de entrega
- ✅ `message_reads` — confirmações de leitura

---

## Passo 5 — Atualizar o Secret AWS com as Credenciais

Após obter todas as credenciais, execute o script abaixo para atualizar o AWS Secrets Manager:

```bash
# Instalar AWS CLI se necessário
# pip install boto3

python3 << 'EOF'
import json
import boto3

client = boto3.client("secretsmanager", region_name="us-east-1")

# Ler secret atual
resp = client.get_secret_value(SecretId="klm-legal-hub/production")
current = json.loads(resp["SecretString"])

# Atualizar com credenciais WhatsApp
current.update({
    "WHATSAPP_ACCESS_TOKEN": "SEU_TOKEN_AQUI",           # ← substituir
    "WHATSAPP_PHONE_NUMBER_ID": "SEU_PHONE_NUMBER_ID",   # ← substituir
    "WHATSAPP_BUSINESS_ACCOUNT_ID": "SEU_WABA_ID",       # ← substituir
    "WHATSAPP_APP_SECRET": "SEU_APP_SECRET",             # ← substituir
})

client.update_secret(
    SecretId="klm-legal-hub/production",
    SecretString=json.dumps(current),
)
print("✅ Secret atualizado!")
EOF
```

---

## Passo 6 — Testar a Integração

### 6.1 Verificar o Webhook

Use o teste de webhook oferecido pelo onboarding aprovado. O host e o token não
são fixados neste repositório.

### 6.2 Enviar Mensagem de Teste

1. No painel do Meta, vá em **WhatsApp → Configuração da API**
2. Na seção **"Enviar e receber mensagens"**, use o número de teste
3. Envie uma mensagem para o número de teste
4. Verifique nos logs do ECS se a mensagem foi recebida:

```bash
aws logs tail /ecs/klm-legal-hub-api --follow --region us-east-1
```

### 6.3 Verificar Criação do Lead

Após enviar uma mensagem:
1. Acesse o banco de dados e verifique se um `WhatsAppContact` foi criado
2. Verifique se um `Client` e um `Matter` foram criados na coluna `entrada`
3. Verifique se o SDR respondeu automaticamente

---

## Resumo das Credenciais Necessárias

| Variável | Onde Obter | Status |
|---|---|---|
| `WHATSAPP_ACCESS_TOKEN` | Meta → Business → System Users | ⏳ Pendente |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta → WhatsApp → API Setup | ⏳ Pendente |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | Meta → WhatsApp → API Setup | ⏳ Pendente |
| `WHATSAPP_APP_SECRET` | Meta → App → Settings → Basic | ⏳ Pendente |
| `WHATSAPP_VERIFY_TOKEN` | Gerado no onboarding aprovado | ⏳ Pendente |
| `META_GRAPH_API_VERSION` | Pinada por release: `v26.0` | ✅ Pronto |
| `WHATSAPP_API_VERSION` | Alias legado temporário; deixar vazio | ⚠️ Deprecado |

---

## Troubleshooting

### Webhook retorna 403
- Verifique se o `WHATSAPP_APP_SECRET` está correto no secret AWS
- O backend valida a assinatura HMAC-SHA256 de cada requisição

### Webhook retorna 404
- Verifique se o ECS service está rodando com a imagem mais recente
- Verifique se o ALB está roteando para a porta 8000

### Mensagens não chegam
- Verifique se os campos `messages` estão assinados no webhook
- Verifique se o `WHATSAPP_ACCESS_TOKEN` tem as permissões corretas

### SDR não responde
- Verifique se o `AI_PROVIDER` está configurado como `vertex_gemini`
- Verifique se o secret `prod/klm/vertex/service-account-json` está acessível
- Verifique os logs do ECS para erros do Vertex AI

---

## Referências

- [Meta WhatsApp Cloud API Docs](https://developers.facebook.com/docs/whatsapp/cloud-api)
- [Webhook Setup Guide](https://developers.facebook.com/docs/whatsapp/cloud-api/guides/set-up-webhooks)
- [System Users Guide](https://developers.facebook.com/docs/whatsapp/business-management-api/get-started)
