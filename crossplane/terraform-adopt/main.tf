terraform {
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.33"
    }
  }
}

variable "kubeconfig_path" {
  type    = string
  default = "~/.kube/config"
}

provider "kubernetes" {
  config_path    = var.kubeconfig_path
  config_context = "kind-simulated-aws"
}

# A team built this object store by hand, in Terraform, before the Crossplane
# platform existed. It is 9 of the 10 resources the XObjectStore composition
# would create for the same bucket (everything except the NetworkPolicy),
# named identically so the platform can adopt them rather than re-create them.

resource "kubernetes_namespace" "legacy_bucket" {
  metadata {
    name = "legacy-bucket"
  }
}

resource "kubernetes_config_map" "bucket_config" {
  metadata {
    name      = "bucket-config"
    namespace = kubernetes_namespace.legacy_bucket.metadata[0].name
  }
  data = {
    bucket     = "legacy-uploads"
    region     = "us-east-1"
    versioning = "true"
  }
}

resource "kubernetes_config_map" "bucket_lifecycle_policy" {
  metadata {
    name      = "bucket-lifecycle-policy"
    namespace = kubernetes_namespace.legacy_bucket.metadata[0].name
  }
  data = {
    transitionAfterDays  = "7"
    expirationAfterDays  = "30"
  }
}

resource "kubernetes_secret" "bucket_encryption_key" {
  metadata {
    name      = "bucket-encryption-key"
    namespace = kubernetes_namespace.legacy_bucket.metadata[0].name
  }
  data = {
    algorithm = "AES256"
    keyId     = "simulated-kms-key-legacy-uploads"
  }
}

resource "kubernetes_service_account" "bucket_access_role" {
  metadata {
    name      = "bucket-access-role"
    namespace = kubernetes_namespace.legacy_bucket.metadata[0].name
  }
}

resource "kubernetes_role" "bucket_reader" {
  metadata {
    name      = "bucket-reader"
    namespace = kubernetes_namespace.legacy_bucket.metadata[0].name
  }
  rule {
    api_groups     = [""]
    resources      = ["configmaps"]
    resource_names = ["bucket-config", "bucket-lifecycle-policy"]
    verbs          = ["get", "list", "watch"]
  }
}

resource "kubernetes_role" "bucket_writer" {
  metadata {
    name      = "bucket-writer"
    namespace = kubernetes_namespace.legacy_bucket.metadata[0].name
  }
  rule {
    api_groups     = [""]
    resources      = ["configmaps"]
    resource_names = ["bucket-config"]
    verbs          = ["get", "update", "patch"]
  }
  rule {
    api_groups     = [""]
    resources      = ["secrets"]
    resource_names = ["bucket-encryption-key"]
    verbs          = ["get"]
  }
}

resource "kubernetes_role_binding" "bucket_reader_binding" {
  metadata {
    name      = "bucket-reader-binding"
    namespace = kubernetes_namespace.legacy_bucket.metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.bucket_access_role.metadata[0].name
    namespace = kubernetes_namespace.legacy_bucket.metadata[0].name
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.bucket_reader.metadata[0].name
  }
}

resource "kubernetes_role_binding" "bucket_writer_binding" {
  metadata {
    name      = "bucket-writer-binding"
    namespace = kubernetes_namespace.legacy_bucket.metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.bucket_access_role.metadata[0].name
    namespace = kubernetes_namespace.legacy_bucket.metadata[0].name
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.bucket_writer.metadata[0].name
  }
}

output "resource_count" {
  value = 9
}
