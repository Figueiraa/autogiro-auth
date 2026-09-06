output "function_name" {
  description = "Nome da função Lambda publicada."
  value       = aws_lambda_function.auth.function_name
}

output "function_arn" {
  description = "ARN da função Lambda."
  value       = aws_lambda_function.auth.arn
}

output "auth_endpoint" {
  description = "URL pública do endpoint de autenticação por CPF."
  value       = aws_lambda_function_url.auth.function_url
}

output "log_group" {
  description = "Log group da função no CloudWatch."
  value       = aws_cloudwatch_log_group.lambda.name
}
