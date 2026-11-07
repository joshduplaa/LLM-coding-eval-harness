# `terraform test` with a mock provider: Terraform plans against fake Azure responses, so these
# checks run offline, with no credentials and no cost. Nothing is created.

mock_provider "azurerm" {
  override_during = plan # fill computed values (IDs, identities) during plan as well
  # Plausible fake IDs so resources that reference each other can be planned.
  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-evalharness"
    }
  }
  mock_resource "azurerm_subnet" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-evalharness/providers/Microsoft.Network/virtualNetworks/vnet-evalharness/subnets/snet-aks-nodes"
    }
  }
  mock_resource "azurerm_container_registry" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-evalharness/providers/Microsoft.ContainerRegistry/registries/acrevalharness000000"
    }
  }
  mock_resource "azurerm_kubernetes_cluster" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-evalharness/providers/Microsoft.ContainerService/managedClusters/aks-evalharness"
    }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-evalharness/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-evalharness"
      client_id    = "11111111-1111-1111-1111-111111111111"
      principal_id = "22222222-2222-2222-2222-222222222222"
    }
  }
}

variables {
  subscription_id          = "00000000-0000-0000-0000-000000000000"
  api_authorized_ip_ranges = ["203.0.113.10/32"]
}

run "cluster_is_locked_down" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.local_account_disabled == true
    error_message = "Local (static admin) accounts must be disabled."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.azure_active_directory_role_based_access_control[0].azure_rbac_enabled == true
    error_message = "Cluster access must go through Entra ID + Azure RBAC."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].network_policy == "cilium"
    error_message = "A NetworkPolicy engine is required; the sandbox depends on deny-all."
  }
  assert {
    condition     = toset(azurerm_kubernetes_cluster.this.api_server_access_profile[0].authorized_ip_ranges) == toset(["203.0.113.10/32"])
    error_message = "The Kubernetes API must be limited to the listed addresses."
  }
  assert {
    condition     = azurerm_container_registry.this.admin_enabled == false
    error_message = "The registry admin user must stay off."
  }
}

run "sandbox_pool_is_isolated" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.sandbox.workload_runtime == "KataVmIsolation"
    error_message = "Sandbox pods must run in Kata VMs (Pod Sandboxing)."
  }
  assert {
    condition     = contains(azurerm_kubernetes_cluster_node_pool.sandbox.node_taints, "evalharness.io/sandbox=true:NoSchedule")
    error_message = "The sandbox pool must be tainted so only sandbox pods land there."
  }
  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.sandbox.kubelet_config[0].pod_max_pid == 256
    error_message = "Per-pod process cap must match kind's podPidsLimit (fork bombs)."
  }
  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.sandbox.min_count == 0 && azurerm_kubernetes_cluster_node_pool.sandbox.max_count <= 4
    error_message = "Sandbox pool should scale to zero and stay small (cost and blast radius)."
  }
}

run "rejects_api_open_to_the_internet" {
  command = plan
  variables {
    api_authorized_ip_ranges = ["0.0.0.0/0"]
  }
  expect_failures = [var.api_authorized_ip_ranges]
}

run "rejects_empty_api_allowlist" {
  command = plan
  variables {
    api_authorized_ip_ranges = []
  }
  expect_failures = [var.api_authorized_ip_ranges]
}
