# Contrato de ambiente de produção — Meu Executivo V1

O catálogo sanitizado está em `deploy/.env.production.example`. Ele lista nomes e
valores não secretos, mas não deve ser copiado como `.env` para ECS.

## Onde cada valor deve ficar

| Tipo | Destino |
|---|---|
| Senha, token, chave ou URL com senha | AWS Secrets Manager |
| Região, hostname, ID de recurso | GitHub Variable ou variável da task ECS |
| Permissão AWS do runtime | IAM Task Role |
| Permissão AWS do deploy | IAM Role assumida por OIDC |
| Identificador público usado pelo Vite | GitHub Variable de build |

Não utilizar `AWS_ACCESS_KEY_ID` ou `AWS_SECRET_ACCESS_KEY` no GitHub, `.env`,
task definition, user-data ou imagem Docker.

## Sessões e origens

```dotenv
FRONTEND_URL=https://app.meuexecutivo.com
BLACKBOX_FRONTEND_URL=https://blackbox.meuexecutivo.com
CORS_ORIGINS=https://app.meuexecutivo.com,https://blackbox.meuexecutivo.com
AUTH_REFRESH_COOKIE_DOMAIN=
AUTH_REFRESH_COOKIE_SECURE=true
AUTH_REFRESH_COOKIE_SAMESITE=lax
```

`AUTH_REFRESH_COOKIE_DOMAIN` precisa permanecer vazio. App e Blackbox possuem
cookies host-only com nomes diferentes. Em produção, `VITE_API_URL` permanece
vazio porque CloudFront encaminha `/api/*` e `/api/v1/ws*` à API.

## Banco e filas

- A task de migration recebe as URLs do usuário migrador.
- API, worker e beat recebem apenas as URLs do usuário runtime.
- Redis/ElastiCache deve usar transporte criptografado quando configurado.
- As tasks não recebem senha por variável comum do GitHub.
- No fallback Compose, use `.env.migration` apenas no serviço `migrate` e
  `.env.runtime` em API, worker e beat. Nenhum dos dois arquivos é versionado.

## Gates iniciais

```dotenv
KLM_ONLINE_APPROVED=false
KLM_EXTERNAL_ACTIONS_ENABLED=false
KLM_AWS_ENABLED=false
KLM_MODEL_APIS_ENABLED=false
```

Os gates sobem fechados. Ativar um provedor exige credencial válida, homologação,
telemetria, orçamento, rollback e aprovação humana correspondente.

## Verificação

Antes de publicar:

```bash
python scripts/verify_aws_v1_release_contract.py
```

Durante o deploy autenticado, o workflow também executa:

```bash
./scripts/verify_aws_v1_deploy_environment.sh
./scripts/verify_aws_v1_edge_contract.sh
```
