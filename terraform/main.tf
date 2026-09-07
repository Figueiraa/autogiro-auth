locals {
  # Sufixa o nome com o ambiente para que homolog e prod coexistam na conta.
  name = "${var.function_name}-${var.environment}"
}

# ─── Empacotamento do código ─────────────────────────────────────────────────
# O diretório build/ é montado pela pipeline (ou por scripts/build.sh) com o
# código-fonte e as dependências instaladas.
data "archive_file" "package" {
  type        = "zip"
  source_dir  = "${path.module}/../build"
  output_path = "${path.module}/.terraform/${local.name}.zip"
}

# ─── IAM ─────────────────────────────────────────────────────────────────────
data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

# Permissão mínima: apenas escrever logs no CloudWatch. A função não acessa
# nenhum outro serviço da AWS — o banco é o Neon, alcançado pela internet.
resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ─── Log group ───────────────────────────────────────────────────────────────
# Declarado explicitamente para controlar a retenção; sem isso a AWS cria o
# grupo com retenção infinita, o que gera custo de armazenamento com o tempo.
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = var.log_retention_days
}

# ─── Função ──────────────────────────────────────────────────────────────────
resource "aws_lambda_function" "auth" {
  function_name = local.name
  role          = aws_iam_role.lambda.arn
  handler       = "src.handler.handler"
  runtime       = "python3.11"
  architectures = ["arm64"] # Graviton: mais barato e mais rápido que x86_64.

  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256

  timeout     = 10
  memory_size = 256

  environment {
    variables = {
      DATABASE_URL                = var.database_url
      JWT_SECRET                  = var.jwt_secret
      ACCESS_TOKEN_EXPIRE_MINUTES = tostring(var.token_expire_minutes)
      LOG_LEVEL                   = var.log_level
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.logs,
    aws_cloudwatch_log_group.lambda,
  ]
}

# ─── Endpoint HTTP ───────────────────────────────────────────────────────────
# Function URL em vez do AWS API Gateway: o free tier do API Gateway dura
# apenas 12 meses, enquanto a Function URL não tem custo adicional. O papel de
# API Gateway do projeto é exercido pelo Kong (ver autogiro-infra-k8s).
resource "aws_lambda_function_url" "auth" {
  function_name      = aws_lambda_function.auth.function_name
  authorization_type = "NONE" # O endpoint de login é público por natureza.

  cors {
    allow_origins = ["*"]
    allow_methods = ["POST"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

# `authorization_type = "NONE"` sozinho não basta: a AWS ainda exige uma
# resource-based policy autorizando explicitamente a invocação. Sem ela a
# Function URL responde 403 Forbidden antes de a Lambda ser executada.
#
# O acesso é público por desenho — é o endpoint de autenticação, chamado por
# clientes não autenticados. A proteção contra abuso é a validação do CPF e,
# como registrado na RFC-003, rate limiting é a mitigação recomendada.
resource "aws_lambda_permission" "function_url_public" {
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.auth.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}
