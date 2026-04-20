################################################################################
# OpenClaw Operator Module
#
# Deploys the OpenClaw Operator into the EKS cluster via Helm.
# Supports both global and China region deployments with ECR mirror images.
################################################################################

# -----------------------------------------------------------------------------
# Operator Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "operator" {
  metadata {
    name = var.operator_namespace
  }
}

# -----------------------------------------------------------------------------
# OpenClaw Operator Helm Release
# -----------------------------------------------------------------------------
resource "helm_release" "openclaw_operator" {
  name       = "openclaw-operator"
  repository = var.chart_repository != "" ? var.chart_repository : "oci://ghcr.io/openclaw-rocks/charts"
  chart      = "openclaw-operator"
  version    = var.operator_version
  namespace  = kubernetes_namespace_v1.operator.metadata[0].name

  set {
    name  = "crds.install"
    value = "true"
  }

  set {
    name  = "crds.keep"
    value = "true"
  }

  set {
    name = "image.pullPolicy"
    value = "Always"
  }

  # For China region, use private ECR mirror (populated by china-image-mirror.sh)
  dynamic "set" {
    for_each = var.ecr_host != "" ? [1] : []
    content {
      name  = "image.repository"
      value = "${var.ecr_host}/openclaw-rocks/openclaw-operator"
    }
  }

  dynamic "set" {
    for_each = var.ecr_host != "" ? [1] : []
    content {
      name  = "image.tag"
      value = "v${var.operator_version}"
    }
  }

  timeout = 600
}
