variable "subscription_id" {
  description = "Azure subscription to deploy into. An identifier, not a secret."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "westus2"
}

variable "name_prefix" {
  description = "Prefix for every resource name (lowercase letters and digits)."
  type        = string
  default     = "evalharness"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,15}$", var.name_prefix))
    error_message = "name_prefix must be 3-16 lowercase letters/digits, starting with a letter."
  }
}

variable "api_authorized_ip_ranges" {
  description = "CIDRs allowed to reach the Kubernetes API (e.g. your office IP/32). Never 0.0.0.0/0."
  type        = list(string)

  validation {
    condition     = length(var.api_authorized_ip_ranges) > 0 && !contains(var.api_authorized_ip_ranges, "0.0.0.0/0")
    error_message = "List at least one CIDR, and don't open the API to the whole internet."
  }
}

variable "admin_group_object_ids" {
  description = "Entra ID groups that get cluster-admin through Azure RBAC (there are no local accounts)."
  type        = list(string)
  default     = []
}

variable "kubernetes_version" {
  description = "AKS Kubernetes version; null lets Azure pick its default."
  type        = string
  default     = null
}

variable "system_vm_size" {
  type    = string
  default = "Standard_D2s_v5"
}

variable "apps_vm_size" {
  description = "Trusted workloads: API, evaluator, Prometheus, Grafana."
  type        = string
  default     = "Standard_D2s_v5"
}

variable "sandbox_vm_size" {
  description = "Untrusted code. Must support nested virtualization for Pod Sandboxing (Kata)."
  type        = string
  default     = "Standard_D4s_v5"
}

variable "sandbox_max_nodes" {
  description = "Upper bound on sandbox nodes, which bounds both cost and blast radius."
  type        = number
  default     = 2
}

variable "tags" {
  type = map(string)
  default = {
    project = "llm-coding-eval-harness"
    owner   = "joshduplaa"
  }
}
