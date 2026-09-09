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

## Arquitetura do repositório

O que este repositório provisiona e como as peças se encaixam:

```
+- AWS ---------------------------------------------------------------------+
|                                                                           |
|   aws_lambda_function_url          (endpoint HTTPS, authorization = NONE) |
|              |                                                            |
|              v                                                            |
|   aws_lambda_function "auth"                                              |
|   |- runtime  python3.11 - arm64 (Graviton)                               |
|   |- memoria  256 MB - timeout 10s                                        |
|   |- handler  src/handler.py :: handler                                   |
|   |   |- src/cpf.py ......... valida formato e digitos (modulo 11)        |
|   |   \- PyJWT .............. assina HS256 com o JWT_SECRET               |
|   |                                                                       |
|   |- env  DATABASE_URL, JWT_SECRET  (via Terraform, nunca no repositorio) |
|   |                                                                       |
|   |- aws_iam_role "lambda" -- AWSLambdaBasicExecutionRole                 |
|   \- aws_cloudwatch_log_group  (retencao de 7 dias)                       |
|                                                                           |
+---------------------------+-----------------------------------------------+
                            | psycopg 3 (TLS)
                            v
                  Neon PostgreSQL - tabela clients
                  SELECT id, name, cpf_cnpj, is_active WHERE cpf_cnpj = %s
```

**Sem VPC de propósito.** A Lambda fica fora de VPC para alcançar o Neon pela internet:
colocá-la numa subnet privada exigiria um NAT Gateway (~US$ 8,60/mês) sem ganho de
segurança, já que a conexão é TLS e o Neon não está na nossa rede.

| Arquivo | Papel |
|---|---|
| `src/handler.py` | Ponto de entrada: parse do evento, orquestração, respostas HTTP |
| `src/cpf.py` | Validação e normalização de CPF — sem dependência externa |
| `terraform/main.tf` | Função, Function URL, IAM role e log group |
| `scripts/build.sh` | Empacota código + dependências compiladas para arm64 |

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
  "sub": "44232322191",      // CPF normalizado — a API resolve o cliente por ele
  "client_id": 1,
  "name": "Maria Oliveira",
  "iss": "autogiro-auth",    // casa com a credencial JWT no Kong
  "iat": 1757180000,
  "exp": 1757183600
}
```

## Uso

```bash
curl -X POST "$AUTH_ENDPOINT" \
  -H 'Content-Type: application/json' \
  -d '{"cpf": "442.323.221-91"}'
```

Pela AWS CLI, sem depender da Function URL — o handler espera o formato de evento do
API Gateway, com o JSON **dentro** de `body`, como string:

```bash
echo -n '{"body":"{\"cpf\":\"44232322191\"}"}' > payload.json
aws lambda invoke --function-name autogiro-auth-homolog \
  --payload fileb://payload.json resposta.json
```

> Enviar `{"cpf": "..."}` na raiz do payload devolve `400 O campo 'cpf' é obrigatório`.
> Não é erro de validação: o handler lê `event["body"]`, então um CPF fora dali é
> invisível para ele.

Resposta de sucesso, em qualquer um dos dois caminhos:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

| Situação | Resposta | Corpo |
|---|---|---|
| Token emitido | `200` | `access_token`, `token_type`, `expires_in` |
| Campo `cpf` ausente | `400` | `O campo 'cpf' é obrigatório` |
| CPF inválido **ou** não cadastrado | `401` | `CPF inválido ou não cadastrado` |
| Cliente cadastrado mas **inativo** | `403` | `Cadastro inativo. Procure a oficina.` |
| Banco indisponível | `503` | `Serviço temporariamente indisponível` |

> CPF inválido e CPF não cadastrado devolvem a **mesma** mensagem, deliberadamente: mensagens
> distintas permitiriam descobrir quais CPFs estão cadastrados na base.
>
> O cliente inativo é a exceção, e por um motivo: o `403` revela que o documento é válido e o
> cadastro existe — nada que já não se saiba ao receber um `200` — e em troca diz à pessoa o
> que fazer. Um `401` genérico aqui mandaria o cliente conferir um CPF que está correto.

O status vem da coluna `is_active` da tabela `clients`, criada pela migration
`004_status_do_cliente.sql` do [autogiro-infra-db](https://github.com/Figueiraa/autogiro-infra-db). É o que atende o
requisito do enunciado de consultar "a existência **e o status** do cliente".

## Contrato da API

A função expõe um único endpoint, `POST`, sem rotas adicionais — o contrato completo é a
tabela de respostas acima. Por isso não há OpenAPI aqui: um documento de especificação para
um endpoint só repetiria o que o README já diz.

Para exercitar a função sem escrever `curl` à mão, a coleção Postman do projeto tem a
requisição pronta:

| Onde | O quê |
|---|---|
| [`autogiro.postman_collection.json`](https://github.com/Figueiraa/autogiro-app/blob/main/docs/autogiro.postman_collection.json) | Pasta **Autenticação → Login por CPF** |
| Variável `auth_endpoint` | Preencha com o `terraform output auth_endpoint` |
| Variável `cpf` | Vem com `44232322191` (Maria Oliveira, do seed) |

A mesma coleção cobre as 21 requisições da API protegida, que consomem o token emitido aqui.
O Swagger dessas rotas é servido pela aplicação — ver
[autogiro-app](https://github.com/Figueiraa/autogiro-app#documentação-da-api).

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
print(handler({'body': json.dumps({'cpf': '44232322191'})}))
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
> 12 meses. O papel de API Gateway do projeto cabe ao Kong (ver [autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s)).

## Alinhamento com as aulas

Cobre a **aula 5 de Serverless** ("Realizando Autenticação e serviços de identificação") e a
**aula 6** (AWS SAM e Funções Lambda).
