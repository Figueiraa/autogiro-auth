# Backend do Terraform state — HCP Terraform (Terraform Cloud).
#
# O state precisa sobreviver entre execuções da pipeline: o runner do GitHub começa
# com disco limpo a cada job, então um state local seria descartado e o `apply`
# seguinte tentaria recriar recursos que já existem (aqui, falharia com
# `EntityAlreadyExists` no IAM role).
#
# Plano gratuito do HCP: 500 recursos, sem cartão de crédito.
#
# `tags` em vez de `name`: o workspace é escolhido em tempo de execução pela
# pipeline (`TF_WORKSPACE=autogiro-auth-homolog` ou `-prod`), de modo que os dois
# ambientes tenham states independentes. Rodando localmente, use
# `terraform workspace select`.
#
# Autenticação: `TF_TOKEN_app_terraform_io` no ambiente (a pipeline injeta a partir
# do secret `TF_API_TOKEN`) ou `terraform login` na máquina.
terraform {
  cloud {
    organization = "autogiro"

    workspaces {
      tags = ["autogiro-auth"]
    }
  }
}
