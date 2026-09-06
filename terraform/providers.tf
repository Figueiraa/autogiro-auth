provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "autogiro"
      Component   = "auth"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
