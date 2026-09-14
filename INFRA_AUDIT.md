# Auditoria de Infraestrutura AWS — KLM Legal Hub

> **REGISTRO HISTÓRICO NÃO REVALIDADO.** Não use os recursos, identificadores ou
> comandos abaixo como prova de estado atual nem como autorização de acesso ou
> deploy. A entrega R5 é exclusivamente local; AWS só será inspecionada novamente
> depois da aprovação explícita do proprietário.

**Data:** 2026-04-01  
**Conta AWS:** 051457669961  
**Região:** us-east-1

---

## Recursos Existentes

### ✅ ECR (Elastic Container Registry)
- **Repositório:** `klm-legal-hub-api`
- **URI:** `051457669961.dkr.ecr.us-east-1.amazonaws.com/klm-legal-hub-api`
- **Criado:** 2026-03-27
- **Scan on push:** Habilitado

### ✅ ECS (Elastic Container Service)
- **Cluster:** `klm-legal-hub`
- **Service:** `klm-legal-hub-api-v2`
  - Status: `ACTIVE`
  - Running: 1 task
  - Desired: 1 task
  - Task Definition: `klm-legal-hub-api:6`

### ✅ RDS (Relational Database Service)
- **Identifier:** `klm-legal-hub-db`
- **Status:** `available`
- **Endpoint:** `klm-legal-hub-db.cmlc4gkk8eh8.us-east-1.rds.amazonaws.com`
- **Engine:** PostgreSQL (presumido)

### ✅ ElastiCache (Redis)
- **Cluster ID:** `klm-legal-hub-redis`
- **Status:** `available`
- **Engine:** Redis 7.0.7
- **Node Type:** cache.t3.micro
- **Nodes:** 1
- **AZ:** us-east-1a

### ✅ S3 (Simple Storage Service)
- `klm-legal-hub-builds` (criado 2026-03-27)
- `klm-legal-hub-builds-v2` (criado 2026-03-27)
- **`klm-legal-hub-documents-051457669961`** ← Bucket de documentos (usado pelo backend)
- `klm-legal-hub-frontend-051457669961` (criado 2026-03-28)

### ✅ ALB (Application Load Balancer)
- **Nome:** `klm-legal-hub-alb`
- **DNS:** `klm-legal-hub-alb-1386945627.us-east-1.elb.amazonaws.com`
- **Estado:** `active`

### ✅ Secrets Manager
- **`klm-legal-hub/production`**
  - Chaves existentes: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `AWS_S3_BUCKET`, `AWS_REGION`, `ENVIRONMENT`, `DEBUG`, `ALLOWED_ORIGINS`, `OPENAI_API_KEY`, `SLACK_WEBHOOK_URL`, `OPENCLAW_WEBHOOK_SECRET`
- **`prod/klm/vertex/service-account-json`** ← JSON da Service Account do Vertex AI (já existe!)

---

## Secrets Faltantes (a criar)

Para o SDR v1 funcionar, precisamos adicionar ao secret `klm-legal-hub/production`:

| Chave | Descrição | Origem |
|---|---|---|
| `WHATSAPP_ACCESS_TOKEN` | Token permanente da System User | Meta for Developers |
| `WHATSAPP_PHONE_NUMBER_ID` | ID do número de telefone WABA | Meta for Developers |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | WABA ID | Meta for Developers |
| `WHATSAPP_APP_SECRET` | App Secret para validação HMAC | Meta for Developers |
| `AI_PROVIDER` | `vertex_gemini` | Config |
| `VERTEX_PROJECT_ID` | `advocacia-hub` | Config |
| `VERTEX_LOCATION` | `us-central1` | Config |
| `VERTEX_MODEL` | `gemini-2.5-flash-lite` | Config |
| `PAPERCLIP_PUBLIC_URL` | URL pública do ALB ou domínio custom | Config |

---

## Task Definitions Existentes

- `klm-legal-hub-api:1` a `klm-legal-hub-api:6` (API backend)
- `klm-legal-hub-migrations:1` a `klm-legal-hub-migrations:5` (Alembic migrations)
- `klm-legal-hub-seed:1` a `klm-legal-hub-seed:4` (Seed data)

---

## Próximos Passos

1. **Merge do PR #2** (`feature/sdr-v1` → `main`)
2. **Build da nova imagem Docker** com o código do SDR v1
3. **Push para ECR:** `051457669961.dkr.ecr.us-east-1.amazonaws.com/klm-legal-hub-api:sdr-v1`
4. **Atualizar secret** `klm-legal-hub/production` com as chaves WhatsApp e Vertex AI
5. **Criar nova task definition** `klm-legal-hub-api:7` com a imagem `sdr-v1`
6. **Executar migration task** para aplicar `3cd88727g27d_add_sdr_qualification_to_matters.py`
7. **Atualizar ECS service** `klm-legal-hub-api-v2` para usar task definition `:7`
8. **Configurar webhook WhatsApp** no Meta for Developers apontando para o ALB
9. **Testar fluxo end-to-end:** enviar mensagem via WhatsApp → verificar qualificação no banco

---

## Comandos de Deploy (resumo)

```bash
# 1. Build e push da imagem
docker build -t klm-legal-hub-api:sdr-v1 .
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 051457669961.dkr.ecr.us-east-1.amazonaws.com
docker tag klm-legal-hub-api:sdr-v1 051457669961.dkr.ecr.us-east-1.amazonaws.com/klm-legal-hub-api:sdr-v1
docker push 051457669961.dkr.ecr.us-east-1.amazonaws.com/klm-legal-hub-api:sdr-v1

# 2. Atualizar secret (via AWS Console ou CLI)
aws secretsmanager update-secret --secret-id klm-legal-hub/production \
  --secret-string '{"DATABASE_URL":"...","WHATSAPP_ACCESS_TOKEN":"...","AI_PROVIDER":"vertex_gemini",...}'

# 3. Registrar nova task definition (via JSON ou AWS Console)
aws ecs register-task-definition --cli-input-json file://task-definition-v7.json

# 4. Executar migration
aws ecs run-task --cluster klm-legal-hub \
  --task-definition klm-legal-hub-migrations:6 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}"

# 5. Atualizar service
aws ecs update-service --cluster klm-legal-hub \
  --service klm-legal-hub-api-v2 \
  --task-definition klm-legal-hub-api:7 \
  --force-new-deployment
```

---

## Observações

- **Vertex AI:** O secret `prod/klm/vertex/service-account-json` já existe. O `VertexGeminiProvider` vai ler ele em runtime.
- **WhatsApp:** Ainda não há secrets configurados. Precisam ser criados após obter as credenciais no Meta for Developers.
- **ALB:** Já está ativo e roteando para o ECS service. Webhook do WhatsApp pode apontar diretamente para ele.
- **RDS e Redis:** Já provisionados e disponíveis.
- **S3:** Bucket de documentos já existe (`klm-legal-hub-documents-051457669961`).

---

**Conclusão:** A infraestrutura base está 100% provisionada. Faltam apenas:
1. Secrets do WhatsApp
2. Deploy da nova imagem com SDR v1
3. Configuração do webhook no Meta
