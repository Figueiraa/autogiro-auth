# autogiro-auth

> **AutoGiro** · autenticação do cliente — Tech Challenge Fase 4 (13SOAT)

**Function Serverless** de autenticação por CPF, executando em **AWS Lambda**.

## Propósito

1. Valida o CPF informado — formato e dígitos verificadores.
2. Consulta a existência e o status do cliente no banco do **OS Service**, por uma view
   somente leitura.
3. Gera e devolve um **JWT** válido para consumo das APIs protegidas dos três microsserviços.

## Repositórios da plataforma

| Repositório | Papel |
|---|---|
| [autogiro-os-service](https://github.com/Figueiraa/autogiro-os-service) | Atendimento e ordens de serviço; dono dos dados de clientes que esta Lambda consulta |
| [autogiro-billing-service](https://github.com/Figueiraa/autogiro-billing-service) | Orçamentos e pagamento pelo Mercado Pago |
| [autogiro-execution-service](https://github.com/Figueiraa/autogiro-execution-service) | Peças, estoque e fila de execução da oficina |
| [autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s) | Cluster EKS, Kong (que valida o token emitido aqui), RabbitMQ e New Relic |
| **autogiro-auth** (este) | Autenticação por CPF e emissão do JWT |

## Tecnologias

| Item | Tecnologia |
|---|---|
| Runtime | AWS Lambda · Python 3.11 · arm64 (Graviton) |
| Token | PyJWT (HS256) |
| Banco | psycopg 3 → Neon PostgreSQL do OS Service (view `auth.clients`) |
| Endpoint | Lambda Function URL (bloqueada nesta conta; ver [Uso](#uso)) |
| IaC | Terraform (provider aws) · state no HCP Terraform |
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
                            | psycopg 3 (TLS) - usuario auth_reader
                            v
          Neon PostgreSQL do OS Service - view auth.clients
          SELECT id, name, cpf_cnpj, is_active FROM clients WHERE cpf_cnpj = %s
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

## De onde vêm os clientes

Os clientes pertencem ao **OS Service**, que guarda o cadastro no próprio banco Neon. A Lambda
não lê a tabela dele: lê uma **view** publicada como contrato, num schema separado, com um
usuário que só enxerga essa view. A decisão está registrada no
[ADR-013](https://github.com/Figueiraa/autogiro-os-service/blob/main/docs/adr/ADR-013-lambda-no-contexto-de-atendimento.md).

| Peça | Onde é criada |
|---|---|
| View `auth.clients` com 4 colunas: `id`, `name`, `cpf_cnpj`, `is_active` | Migration `002_contrato_autenticacao.sql` do OS Service |
| Usuário somente leitura `auth_reader`, com `SELECT` só na view | Terraform do OS Service (`neon_role`) + a mesma migration |
| `DATABASE_URL` desta Lambda | Saída `auth_database_url` do Terraform do OS Service |

O `search_path=auth` vai **na própria `DATABASE_URL`** (`options=-csearch_path%3Dauth`). Por
isso a consulta do código continua `FROM clients`: com esse `search_path`, o nome resolve para
a view `auth.clients`, sem mudar uma linha de `src/`. O usuário é criado pela API do Neon e o
usuário do serviço não pode alterá-lo, por isso o `search_path` não fica no papel, e sim na URL.

Com isso o `auth_reader` não enxerga veículos, ordens de serviço, e-mail, telefone nem endereço,
e o OS Service pode mudar a tabela `clients` à vontade, desde que mantenha a view.

## Fluxo de autenticação

```
 Cliente           Lambda              Neon (OS Service)     Kong                Serviço
    │                 │                       │                │                    │
    │─ CPF ──────────►│                       │                │                    │
    │                 │─ valida dígitos       │                │                    │
    │                 │─ SELECT auth.clients ►│                │                    │
    │                 │◄──── cliente ─────────│                │                    │
    │                 │─ assina JWT (HS256)   │                │                    │
    │◄─ access_token ─│                       │                │                    │
    │                                                          │                    │
    │─ GET /api/v1/... + Bearer token ────────────────────────►│                    │
    │                                       valida assinatura, │                    │
    │                                       iss e exp          │─ encaminha ───────►│
    │                                                          │  revalida o token  │
    │◄──────────────────────────────────────────────────────────────── resposta ────│
```

**Desacoplamento:** a Lambda emite, o Kong valida. Nenhum conhece o outro — o contrato é o
segredo HS256 e a claim `iss`. Isso permite trocar qualquer um dos lados sem tocar no outro.

O token é validado **duas vezes**. O Kong confere a assinatura e a expiração antes de rotear,
achando o segredo pela claim `iss` (`autogiro-auth`), que casa com a credencial do Consumer.
Depois, cada um dos três serviços (OS, Billing e Execution) revalida assinatura, expiração e
emissor com o **mesmo `JWT_SECRET`**, que recebem do cluster. A revalidação cobre chamadas
internas que não passam pelo Kong, como a consulta do OS Service ao catálogo de peças do
Execution, que repassa o token de quem abriu a OS.

Rotas protegidas, a única rota pública e os endpoints de cada serviço estão em
[APIs e API Gateway](https://github.com/Figueiraa/autogiro-infra-k8s/blob/main/docs/apis-e-gateway.md).

### Payload do token

```json
{
  "sub": "44232322191",      // CPF normalizado — os serviços identificam o cliente por ele
  "client_id": 1,
  "name": "Maria Oliveira",
  "iss": "autogiro-auth",    // casa com a credencial JWT no Kong
  "iat": 1757180000,
  "exp": 1757183600
}
```

## Uso

**Nesta conta AWS, a Function URL responde 403.** A configuração está correta
(`authorization_type = NONE` e resource policy `AllowPublicFunctionUrlInvoke` com
`principal = "*"`), mas a conta bloqueia invocação pública: a requisição é recusada antes de a
Lambda executar. É a mesma família de restrição que impede Load Balancers na conta. Por isso o
token é emitido pela **AWS CLI**.

O handler espera o formato de evento do API Gateway, com o JSON **dentro** de `body`, como
string:

```bash
echo -n '{"body":"{\"cpf\":\"44232322191\"}"}' > payload.json
aws lambda invoke --function-name autogiro-auth-prod --region us-east-1 \
  --payload fileb://payload.json resposta.json

# O token está dentro de `body`, que também é uma string JSON
python -c "import json; print(json.loads(json.load(open('resposta.json'))['body'])['access_token'])"
```

No PowerShell, aspas simples são literais, então o JSON vai como é:

```powershell
$payload = '{"body":"{\"cpf\":\"44232322191\"}"}'
aws lambda invoke --function-name autogiro-auth-prod --region us-east-1 `
  --cli-binary-format raw-in-base64-out --payload $payload resposta.json
```

> Enviar `{"cpf": "..."}` na raiz do payload devolve `400 O campo 'cpf' é obrigatório`.
> Não é erro de validação: o handler lê `event["body"]`, então um CPF fora dali é
> invisível para ele.

Numa conta sem a restrição, a Function URL aceita o CPF direto no corpo:

```bash
curl -X POST "$AUTH_ENDPOINT" \
  -H 'Content-Type: application/json' \
  -d '{"cpf": "442.323.221-91"}'
```

Resposta de sucesso (na AWS CLI, dentro de `body`):

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

O status vem da coluna `is_active` da view `auth.clients`, que reflete o cadastro do OS
Service. É o que atende o requisito do enunciado de consultar "a existência **e o status** do
cliente". Os dados de demonstração do OS Service trazem `44232322191` (Maria Oliveira, ativa)
e `94789277879` (Transportes Lima ME, inativo).

## Contrato da API

A função expõe um único endpoint, `POST`, sem rotas adicionais — o contrato completo é a
tabela de respostas acima. Por isso não há OpenAPI aqui: um documento de especificação para
um endpoint só repetiria o que o README já diz.

O token emitido aqui é o que a coleção Postman
[`autogiro-fluxo-completo`](https://github.com/Figueiraa/autogiro-os-service/blob/main/docs/postman/autogiro-fluxo-completo.postman_collection.json)
espera na variável `token`, para percorrer a ordem de serviço pelos três microsserviços.

## Executando localmente

```bash
python -m venv venv && source venv/Scripts/activate
pip install -r requirements-dev.txt
cp .env.example .env

pytest                    # 40 testes, 100% de cobertura (gate mínimo: 90%)
ruff check src tests
bandit -r src -ll
```

Os testes não precisam de banco: a conexão é substituída por um dublê.

Invocando o handler sem a AWS (com `DATABASE_URL` e `JWT_SECRET` no ambiente):

```bash
python -c "
from src.handler import handler
import json
print(handler({'body': json.dumps({'cpf': '44232322191'})}))
"
```

## Deploy

Automático pela pipeline, a cada push: `develop` publica `autogiro-auth-homolog`, `main`
publica `autogiro-auth-prod`. Antes do deploy rodam ruff, bandit, pytest com gate de
cobertura de 90% e `terraform validate`. O state fica no HCP Terraform, nos workspaces
`autogiro-auth-homolog` e `autogiro-auth-prod`.

A `DATABASE_URL` vem do secret do ambiente no GitHub e deve ser a saída `auth_database_url`
do Terraform do OS Service. O `JWT_SECRET` tem de ser o mesmo configurado no
`autogiro-infra-k8s`, que o entrega ao Kong e aos três serviços.

Manualmente:

```bash
bash scripts/build.sh                    # empacota código + dependências arm64
cd terraform
cp terraform.tfvars.example terraform.tfvars   # preencha database_url e jwt_secret
terraform init && terraform apply
terraform output auth_endpoint           # URL da Function URL
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
