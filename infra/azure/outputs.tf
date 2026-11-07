# Names only. No kubeconfig output: access goes through `az aks get-credentials` + Entra ID.
output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "cluster_name" {
  value = azurerm_kubernetes_cluster.this.name
}

output "acr_login_server" {
  value = azurerm_container_registry.this.login_server
}

output "sandbox_node_taint" {
  description = "Set EVALHARNESS_SANDBOX_NODE_POOL=sandbox so sandbox pods tolerate it."
  value       = local.sandbox_taint
}
