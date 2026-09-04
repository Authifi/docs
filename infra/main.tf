provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  github_repository_subject = "repo:Authifi/docs:ref:refs/heads/main"
  github_oidc_provider_arn  = coalesce(var.existing_github_oidc_provider_arn, try(aws_iam_openid_connect_provider.github[0].arn, null))
  service_arn_for_policy    = format("arn:%s:apprunner:%s:%s:service/%s/*", data.aws_partition.current.partition, var.aws_region, data.aws_caller_identity.current.account_id, var.service_name)
  common_tags = merge(var.tags, {
    ManagedBy  = "Terraform"
    Repository = "Authifi/docs"
    Service    = var.service_name
  })
}

resource "aws_ecr_repository" "docs" {
  name                 = var.ecr_repository_name
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.common_tags, {
    Name = var.ecr_repository_name
  })
}

resource "aws_ecr_lifecycle_policy" "docs" {
  repository = aws_ecr_repository.docs.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Retain the configured number of recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.ecr_image_retention_count
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

data "aws_iam_policy_document" "apprunner_access_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "apprunner_access" {
  name               = "${var.service_name}-apprunner-access"
  assume_role_policy = data.aws_iam_policy_document.apprunner_access_assume_role.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "apprunner_access" {
  statement {
    sid       = "AllowAuthorizationToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "AllowPullFromRepository"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.docs.arn]
  }
}

resource "aws_iam_policy" "apprunner_access" {
  name   = "${var.service_name}-apprunner-access"
  policy = data.aws_iam_policy_document.apprunner_access.json
  tags   = local.common_tags
}

resource "aws_iam_role_policy_attachment" "apprunner_access" {
  role       = aws_iam_role.apprunner_access.name
  policy_arn = aws_iam_policy.apprunner_access.arn
}

data "aws_iam_policy_document" "apprunner_instance_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "apprunner_instance" {
  name               = "${var.service_name}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.apprunner_instance_assume_role.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "apprunner_instance_secrets" {
  statement {
    sid     = "AllowReadRuntimeSecrets"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      var.oidc_client_secret_arn,
      var.session_secret_arn,
    ]
  }

  dynamic "statement" {
    for_each = length(var.runtime_secret_kms_key_arns) == 0 ? [] : [var.runtime_secret_kms_key_arns]

    content {
      sid       = "AllowDecryptRuntimeSecretKeys"
      effect    = "Allow"
      actions   = ["kms:Decrypt"]
      resources = statement.value
    }
  }
}

resource "aws_iam_policy" "apprunner_instance_secrets" {
  name   = "${var.service_name}-apprunner-instance-secrets"
  policy = data.aws_iam_policy_document.apprunner_instance_secrets.json
  tags   = local.common_tags
}

resource "aws_iam_role_policy_attachment" "apprunner_instance_secrets" {
  role       = aws_iam_role.apprunner_instance.name
  policy_arn = aws_iam_policy.apprunner_instance_secrets.arn
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.existing_github_oidc_provider_arn == null ? 1 : 0

  url = "https://token.actions.githubusercontent.com"

  client_id_list = [
    "sts.amazonaws.com",
  ]

  tags = local.common_tags
}

data "aws_iam_policy_document" "github_deploy_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    actions = ["sts:AssumeRoleWithWebIdentity"]

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_repository_subject]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${var.service_name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume_role.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "AllowEcrAuthorizationToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "AllowPushToRepository"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.docs.arn]
  }

  statement {
    sid    = "AllowAppRunnerDeploy"
    effect = "Allow"
    actions = [
      "apprunner:DescribeService",
      "apprunner:ListOperations",
      "apprunner:StartDeployment",
      "apprunner:UpdateService",
    ]
    resources = [local.service_arn_for_policy]
  }

  # `apprunner update-service` reposts the service's own source configuration,
  # which carries `AuthenticationConfiguration.AccessRoleArn`. That makes the
  # call a role hand-off, and App Runner rejects it without `iam:PassRole`.
  statement {
    sid       = "AllowPassAppRunnerAccessRole"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.apprunner_access.arn]

    # Without this the grant would let the deploy role attach that role to any
    # service willing to assume it. The value has to stay in step with the
    # role's own trust policy above.
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "github_deploy" {
  name   = "${var.service_name}-github-deploy"
  policy = data.aws_iam_policy_document.github_deploy.json
  tags   = local.common_tags
}

resource "aws_iam_role_policy_attachment" "github_deploy" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = aws_iam_policy.github_deploy.arn
}

resource "aws_apprunner_auto_scaling_configuration_version" "docs" {
  auto_scaling_configuration_name = "${var.service_name}-autoscaling"
  max_concurrency                 = var.auto_scaling_max_concurrency
  max_size                        = var.auto_scaling_max_size
  min_size                        = var.auto_scaling_min_size
  tags                            = local.common_tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_apprunner_service" "docs" {
  count        = var.create_service ? 1 : 0
  service_name = var.service_name

  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.docs.arn

  source_configuration {
    auto_deployments_enabled = false

    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }

    image_repository {
      image_identifier      = var.image_identifier
      image_repository_type = "ECR"

      image_configuration {
        port = "8080"

        runtime_environment_variables = {
          OIDC_CLIENT_ID   = var.oidc_client_id
          OIDC_ISSUER      = var.oidc_issuer
          POST_LOGOUT_PATH = var.post_logout_path
          PUBLIC_BASE_URL  = var.public_base_url
          SITE_DIR         = var.site_dir
        }

        runtime_environment_secrets = {
          OIDC_CLIENT_SECRET = var.oidc_client_secret_arn
          SESSION_SECRET     = var.session_secret_arn
        }
      }
    }
  }

  instance_configuration {
    cpu               = var.instance_cpu
    instance_role_arn = aws_iam_role.apprunner_instance.arn
    memory            = var.instance_memory
  }

  health_check_configuration {
    healthy_threshold   = 1
    interval            = 10
    path                = "/health"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 5
  }

  dynamic "encryption_configuration" {
    for_each = var.apprunner_encryption_kms_key_arn == null ? [] : [var.apprunner_encryption_kms_key_arn]

    content {
      kms_key = encryption_configuration.value
    }
  }

  tags = local.common_tags

  lifecycle {
    # GitHub Actions owns the deployed image after bootstrap. Without this,
    # the next terraform apply would roll production back to whatever SHA is
    # still recorded in tfvars. Roll images forward or back with the App
    # Runner procedures in infra/README.md, not with terraform apply.
    ignore_changes = [
      source_configuration[0].image_repository[0].image_identifier,
    ]

    precondition {
      condition     = trimspace(var.image_identifier) != ""
      error_message = "image_identifier must be set when create_service is true. Bootstrap with create_service=false until an image exists in ECR."
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.apprunner_access,
    aws_iam_role_policy_attachment.apprunner_instance_secrets,
  ]
}

resource "aws_apprunner_custom_domain_association" "docs" {
  count = var.create_service && var.enable_custom_domain ? 1 : 0

  domain_name = var.custom_domain_name
  service_arn = aws_apprunner_service.docs[0].arn
}
