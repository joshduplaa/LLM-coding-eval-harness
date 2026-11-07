# Credentials never appear here. The provider authenticates from the environment:
# `az login` on a laptop, or GitHub's OIDC token in the deploy workflow (no stored secret).
provider "azurerm" {
  features {}
  subscription_id     = var.subscription_id
  storage_use_azuread = true
}
