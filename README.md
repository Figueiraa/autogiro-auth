# autogiro-auth

> **AutoGiro** · Repositório 1 de 4 — Tech Challenge Fase 3 (13SOAT)

**Function Serverless** de autenticação por CPF, executando em **AWS Lambda**.

## Propósito

1. Valida o CPF informado — formato e dígitos verificadores.
2. Consulta a existência e o status do cliente no banco gerenciado (Neon PostgreSQL).
3. Gera e devolve um **JWT** válido para consumo das APIs protegidas.

## Tecnologias

| Item | Tecnologia |
|---|---|
| Runtime | AWS Lambda · Python 3.11 · arm64 (Graviton) |
| Token | PyJWT (HS256) |
| Banco | psycopg 3 → Neon PostgreSQL |
| Endpoint | Lambda Function URL |
| IaC | Terraform (provider aws) |
| Testes | pytest · **100% de cobertura** |
| CI/CD | GitHub Actions |

## Fluxo de autenticação

```
 Cliente                Lambda                  Neon              Kong            API
    │                     │                      │                 │               │
    │─ POST {cpf} ───────►│                      │                 │               │
    │                     │─ valida dígitos      │                 │               │
    │                     │─ SELECT client ─────►│                 │               │
    │                     │◄──── cliente ────────│                 │               │
    │                     │─ assina JWT (HS256)  │                 │               │
    │◄─ access_token ─────│                      │                 │               │
    │                                                              │               │
    │─ GET /api/v1/... + Bearer token ────────────────────────────►│               │
    │                                            valida assinatura │               │
    │                                            (plugin jwt)      │─ encaminha ──►│
    │◄──────────────────────────────────────────────────────────── resposta ───────│
```

**Desacoplamento:** a Lambda emite, o Kong valida. Nenhum conhece o outro — o contrato é o segredo
HS256 e a claim `iss`. Isso permite trocar qualquer um dos lados sem tocar no outro.

### Payload do token

```json
{
  "sub": "52998224725",      // CPF normalizado — a API resolve o cliente por ele
  "client_id": 42,
  "name": "Maria Silva",
  "iss": "autogiro-auth",    // casa com a credencial JWT no Kong
  "iat": 1757180000,
  "exp": 1757183600
}
```

## Uso

```bash
curl -X POST "$AUTH_ENDPOINT" \
  -H 'Content-Type: application/json' \
  -d '{"cpf": "529.982.247-25"}'
```

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

| Situação | Resposta |
|---|---|
| Token emitido | `200` |
| Campo `cpf` ausente | `400` |
| CPF inválido **ou** não cadastrado | `401` |
| Banco indisponível | `503` |

> CPF inválido e CPF não cadastrado devolvem a **mesma** mensagem, deliberadamente: mensagens
> distintas permitiriam descobrir quais CPFs estão cadastrados na base.

## Executando localmente

```bash
python -m venv venv && source venv/Scripts/activate
pip install -r requirements-dev.txt
cp .env.example .env

pytest                    # 35 testes, gate de cobertura de 90%
ruff check src tests
bandit -r src -ll
```

Invocando o handler sem a AWS:

```bash
python -c "
from src.handler import handler
import json
print(handler({'body': json.dumps({'cpf': '52998224725'})}))
"
```

## Deploy

Automático pela pipeline: `develop` publica em homologação, `main` em produção.
Manualmente:

```bash
bash scripts/build.sh                    # empacota código + dependências arm64
cd terraform
cp terraform.tfvars.example terraform.tfvars   # preencha database_url e jwt_secret
terraform init && terraform apply
terraform output auth_endpoint           # URL pública da função
```

## Custo

Free tier **permanente** da AWS Lambda: 1 milhão de requisições e 400.000 GB-s por mês.
A arquitetura arm64 e os 256 MB de memória mantêm o consumo bem abaixo do limite.
Os logs no CloudWatch têm retenção de 7 dias para não acumular custo de armazenamento.

> Usamos **Function URL** em vez do AWS API Gateway porque o free tier deste último dura apenas
> 12 meses. O papel de API Gateway do projeto cabe ao Kong (ver [autogiro-infra-k8s](../autogiro-infra-k8s/)).

## Alinhamento com as aulas

Cobre a **aula 5 de Serverless** ("Realizando Autenticação e serviços de identificação") e a
**aula 6** (AWS SAM e Funções Lambda).
