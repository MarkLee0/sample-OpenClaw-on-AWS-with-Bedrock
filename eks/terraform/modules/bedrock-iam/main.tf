################################################################################
# OpenClaw Bedrock IAM Module
#
# Creates IAM roles and policies for OpenClaw pods to access AWS Bedrock,
# using IRSA (IAM Roles for Service Accounts) for secure pod-level credentials.
################################################################################

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# Kubernetes Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "openclaw" {
  metadata {
    name = var.openclaw_namespace
  }
}

# -----------------------------------------------------------------------------
# Bedrock IAM Policy
# -----------------------------------------------------------------------------
resource "aws_iam_policy" "bedrock_access" {
  name        = "${var.name}-bedrock-access"
  description = "Allow OpenClaw pods to invoke Bedrock models"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModel",
          "bedrock:ListInferenceProfiles",
          "aws-marketplace:ViewSubscriptions",
          "aws-marketplace:Subscribe"
        ]
        Resource = "*"
      }
    ]
  })

  tags = var.tags
}

# -----------------------------------------------------------------------------
# Secrets Manager Policy (namespace-scoped)
# Allows OpenClaw pods to read secrets under their own namespace prefix.
# Used by OpenClaw SecretRef (exec provider) to load API keys from SM.
# -----------------------------------------------------------------------------
resource "aws_iam_policy" "secrets_access" {
  name        = "${var.name}-secrets-access"
  description = "Allow OpenClaw pods to read namespace-scoped secrets from Secrets Manager"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = "arn:${var.partition}:secretsmanager:*:*:secret:openclaw/$${aws:PrincipalTag/kubernetes-namespace}/*"
      }
    ]
  })

  tags = var.tags
}

# -----------------------------------------------------------------------------
# IRSA Role for OpenClaw Bedrock Access
# Workshop: trust policy allows any service account from this cluster
# (no sub condition) so operator-created SAs can assume the role directly.
# -----------------------------------------------------------------------------
resource "aws_iam_role" "openclaw_bedrock" {
  name_prefix = "${var.name}-bedrock-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = var.oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${replace(var.oidc_provider_arn, "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/", "")}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "openclaw_bedrock_bedrock" {
  role       = aws_iam_role.openclaw_bedrock.name
  policy_arn = aws_iam_policy.bedrock_access.arn
}

resource "aws_iam_role_policy_attachment" "openclaw_bedrock_secrets" {
  role       = aws_iam_role.openclaw_bedrock.name
  policy_arn = aws_iam_policy.secrets_access.arn
}

# -----------------------------------------------------------------------------
# Backup: S3 IAM role + dedicated ServiceAccount + Pod Identity
# Used by spec.backup.serviceAccountName in OpenClawInstance CRD
# -----------------------------------------------------------------------------
resource "aws_iam_policy" "openclaw_backup" {
  name_prefix = "${var.name}-backup-"
  tags        = var.tags

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"]
      Resource = ["*"]
    }]
  })
}

resource "aws_iam_role" "openclaw_backup" {
  name_prefix = "${var.name}-backup-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.${var.is_china_region ? "amazonaws.com.cn" : "amazonaws.com"}" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "openclaw_backup" {
  role       = aws_iam_role.openclaw_backup.name
  policy_arn = aws_iam_policy.openclaw_backup.arn
}

resource "kubernetes_service_account_v1" "openclaw_backup" {
  metadata {
    name      = "openclaw-backup"
    namespace = kubernetes_namespace_v1.openclaw.metadata[0].name
  }
}

resource "aws_eks_pod_identity_association" "openclaw_backup" {
  cluster_name    = var.cluster_name
  namespace       = kubernetes_namespace_v1.openclaw.metadata[0].name
  service_account = kubernetes_service_account_v1.openclaw_backup.metadata[0].name
  role_arn        = aws_iam_role.openclaw_backup.arn
}

# -----------------------------------------------------------------------------
# Kubernetes ServiceAccount with IRSA Annotation
# -----------------------------------------------------------------------------
resource "kubernetes_service_account_v1" "openclaw_sandbox" {
  metadata {
    name      = "openclaw-sandbox"
    namespace = kubernetes_namespace_v1.openclaw.metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.openclaw_bedrock.arn
    }
  }
}
