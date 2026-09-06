# Backend do Terraform state.
#
# O state precisa sobreviver entre execuções da pipeline; sem isso, cada `apply`
# tentaria recriar recursos que já existem. Duas opções gratuitas:
#
#   1. HCP Terraform (Terraform Cloud) — 500 recursos no plano gratuito.
#      Descomente o bloco `cloud` abaixo e rode `terraform login` uma vez.
#
#   2. S3 — cabe no free tier (o state tem poucos KB), mas exige criar o bucket
#      antes. Neste projeto preferimos o HCP para não depender de bootstrap.
#
# Enquanto nenhum backend estiver configurado, o state fica local — suficiente
# para desenvolvimento, mas o job de deploy da pipeline precisa de um remoto.

# terraform {
#   cloud {
#     organization = "autogiro"
#
#     workspaces {
#       name = "autogiro-auth"
#     }
#   }
# }
