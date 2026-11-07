# A provisional Azure target for the harness (D22). It mirrors the kind setup (D20-D21) and
# is checked with `terraform fmt` and `terraform validate` only. Nothing here has been applied.

locals {
  name = var.name_prefix
  # Sandbox pods land only on the sandbox pool: it is tainted, and the evaluator adds the
  # matching toleration + node selector (EVALHARNESS_SANDBOX_NODE_POOL).
  sandbox_taint = "evalharness.io/sandbox=true:NoSchedule"
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = var.tags
}

resource "azurerm_virtual_network" "this" {
  name                = "vnet-${local.name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = ["10.40.0.0/16"]
  tags                = var.tags
}

resource "azurerm_subnet" "nodes" {
  name                 = "snet-aks-nodes"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.40.0.0/22"]
}

# Private image registry. No admin user: nodes pull with their managed identity (AcrPull below),
# and the deploy workflow pushes with its OIDC identity.
resource "azurerm_container_registry" "this" {
  name                = "acr${local.name}${substr(md5(azurerm_resource_group.this.id), 0, 6)}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Standard"
  admin_enabled       = false
  tags                = var.tags
}

# Identities are created before the cluster, so the image-pull permission already exists when
# the first node tries to pull, and each identity can be audited on its own.
resource "azurerm_user_assigned_identity" "control_plane" {
  name                = "id-${local.name}-control-plane"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "kubelet" {
  name                = "id-${local.name}-kubelet"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags
}

# AKS's control plane must be allowed to attach the kubelet identity to the nodes.
resource "azurerm_role_assignment" "control_plane_assigns_kubelet_identity" {
  scope                = azurerm_user_assigned_identity.kubelet.id
  role_definition_name = "Managed Identity Operator"
  principal_id         = azurerm_user_assigned_identity.control_plane.principal_id
}

# Nodes may pull images from our registry, and nothing else.
resource "azurerm_role_assignment" "kubelet_pulls_from_acr" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.kubelet.principal_id
}

resource "azurerm_kubernetes_cluster" "this" {
  name                = "aks-${local.name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  dns_prefix          = local.name
  kubernetes_version  = var.kubernetes_version
  sku_tier            = "Free"

  # Identity and access: Entra ID + Azure RBAC only. Local accounts (a static admin
  # kubeconfig that never expires) are disabled.
  local_account_disabled = true
  azure_active_directory_role_based_access_control {
    azure_rbac_enabled     = true
    admin_group_object_ids = var.admin_group_object_ids
  }
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.control_plane.id]
  }
  kubelet_identity {
    client_id                 = azurerm_user_assigned_identity.kubelet.client_id
    object_id                 = azurerm_user_assigned_identity.kubelet.principal_id
    user_assigned_identity_id = azurerm_user_assigned_identity.kubelet.id
  }

  # The Kubernetes API is reachable only from listed addresses.
  api_server_access_profile {
    authorized_ip_ranges = var.api_authorized_ip_ranges
  }

  # Cilium enforces NetworkPolicy (deploy/k8s), like kindnet does on kind. The in-pod egress
  # gate (D20) stays on regardless: "the policy exists" is not "the policy is enforced".
  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_data_plane  = "cilium"
    network_policy      = "cilium"
    load_balancer_sku   = "standard"
  }

  # We define every node pool ourselves (system, apps, sandbox) instead of letting AKS
  # create machines on demand, so the sandbox pool's isolation settings can't be bypassed.
  node_provisioning_profile {
    mode = "Manual"
  }

  automatic_upgrade_channel = "patch"
  node_os_upgrade_channel   = "NodeImage"

  # System pool: only Kubernetes' own components run here.
  default_node_pool {
    name                         = "system"
    vm_size                      = var.system_vm_size
    vnet_subnet_id               = azurerm_subnet.nodes.id
    only_critical_addons_enabled = true
    auto_scaling_enabled         = true
    min_count                    = 1
    max_count                    = 2
    os_sku                       = "AzureLinux"
    upgrade_settings {
      max_surge = "1"
    }
  }

  tags = var.tags

  depends_on = [
    azurerm_role_assignment.control_plane_assigns_kubelet_identity,
    azurerm_role_assignment.kubelet_pulls_from_acr,
  ]
}

# Trusted workloads: results API, evaluator Job, Prometheus, Grafana.
resource "azurerm_kubernetes_cluster_node_pool" "apps" {
  name                  = "apps"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.this.id
  vm_size               = var.apps_vm_size
  vnet_subnet_id        = azurerm_subnet.nodes.id
  auto_scaling_enabled  = true
  min_count             = 1
  max_count             = 2
  os_sku                = "AzureLinux"
  node_labels           = { "evalharness.io/pool" = "apps" }
  upgrade_settings {
    max_surge = "1"
  }
  tags = var.tags
}

# Untrusted code only. Separate machines from the trusted pods, tainted so nothing else lands
# here, and Pod Sandboxing: each pod runs in its own lightweight VM (Kata), the AKS analogue of
# running sandboxes under gVisor (D13). The process cap matches podPidsLimit on kind (D21).
resource "azurerm_kubernetes_cluster_node_pool" "sandbox" {
  name                  = "sandbox"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.this.id
  vm_size               = var.sandbox_vm_size
  vnet_subnet_id        = azurerm_subnet.nodes.id
  auto_scaling_enabled  = true
  min_count             = 0
  max_count             = var.sandbox_max_nodes
  os_sku                = "AzureLinux"
  workload_runtime      = "KataVmIsolation"
  node_labels           = { "evalharness.io/pool" = "sandbox" }
  node_taints           = [local.sandbox_taint]
  kubelet_config {
    pod_max_pid = 256
  }
  upgrade_settings {
    max_surge = "1"
  }
  tags = var.tags
}
