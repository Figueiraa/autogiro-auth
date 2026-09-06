variable "aws_region" {
  description = "Região AWS onde a Lambda é publicada."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Ambiente de deploy (homolog ou prod)."
  type        = string
  default     = "homolog"

  validation {
    condition     = contains(["homolog", "prod"], var.environment)
    error_message = "O ambiente deve ser 'homolog' ou 'prod'."
  }
}

variable "function_name" {
  description = "Nome base da função Lambda."
  type        = string
  default     = "autogiro-auth"
}

variable "database_url" {
  description = "Connection string do Neon (driver psycopg, sem +asyncpg)."
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "Segredo HS256 compartilhado com a aplicação principal (autogiro-app)."
  type        = string
  sensitive   = true
}

variable "token_expire_minutes" {
  description = "Validade do token emitido, em minutos."
  type        = number
  default     = 60
}

variable "log_level" {
  description = "Nível de log da função."
  type        = string
  default     = "INFO"
}

variable "log_retention_days" {
  description = "Retenção dos logs no CloudWatch. Valores baixos mantêm o custo em zero."
  type        = number
  default     = 7
}
