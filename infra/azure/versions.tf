terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "5.11.0" # exact; .terraform.lock.hcl pins the checksums too
    }
  }

  # State: local by default, so `terraform init -backend=false && terraform validate` needs no
  # cloud access. A real deployment should keep state in Azure Storage, configured at init time
  # (no names or keys in code), e.g. from the deploy workflow:
  #
  #   terraform init \
  #     -backend-config="resource_group_name=..." -backend-config="storage_account_name=..." \
  #     -backend-config="container_name=tfstate" -backend-config="key=evalharness.tfstate" \
  #     -backend-config="use_azuread_auth=true"
  #
  # backend "azurerm" {}
}
