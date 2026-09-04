variable "aws_region" {
  description = "AWS region for ECR, App Runner, and IAM resources."
  type        = string
}

variable "service_name" {
  description = "App Runner service name."
  type        = string
  default     = "authifi-docs"
}

variable "ecr_repository_name" {
  description = "Private ECR repository name for deployable docs images."
  type        = string
  default     = "authifi-docs"
}

variable "ecr_image_retention_count" {
  description = "How many of the most recent images the ECR lifecycle policy retains."
  type        = number
  default     = 10

  validation {
    condition     = var.ecr_image_retention_count > 0
    error_message = "ecr_image_retention_count must be greater than 0."
  }
}

variable "create_service" {
  description = "Create the App Runner service and custom domain resources. Set false for the first bootstrap apply before an image exists."
  type        = bool
  default     = true
}

variable "image_identifier" {
  description = "Full ECR image identifier to deploy, for example 123456789012.dkr.ecr.us-east-1.amazonaws.com/authifi-docs:<git-sha>."
  type        = string
  default     = ""
}

variable "oidc_issuer" {
  description = "OIDC issuer base URL exposed to the docs server."
  type        = string
}

variable "oidc_client_id" {
  description = "OIDC client ID exposed to the docs server."
  type        = string
}

variable "oidc_client_secret_arn" {
  description = "ARN of the pre-created Secrets Manager secret containing the OIDC client secret."
  type        = string
}

variable "session_secret_arn" {
  description = "ARN of the pre-created Secrets Manager secret containing the session secret."
  type        = string
}

variable "runtime_secret_kms_key_arns" {
  description = "Optional customer-managed KMS key ARNs that encrypt the runtime secrets. When set, App Runner also gets kms:Decrypt on only these keys."
  type        = list(string)
  default     = []
}

variable "public_base_url" {
  description = "Public HTTPS origin served by App Runner."
  type        = string
  default     = "https://docs.authifi.io"
}

variable "site_dir" {
  description = "Absolute in-container path to the built MkDocs site."
  type        = string
  default     = "/app/site"
}

variable "post_logout_path" {
  description = "Site-relative path users land on after logout. Must be a publicly served path, and must be registered with Authifi as a post-logout redirect URI."
  type        = string
  default     = "/privacy-policy/"

  validation {
    condition     = startswith(var.post_logout_path, "/") && !startswith(var.post_logout_path, "//")
    error_message = "post_logout_path must be a site-relative path starting with a single '/'."
  }

  validation {
    condition     = length(regexall("[\\\\[:cntrl:]]", var.post_logout_path)) == 0
    error_message = "post_logout_path must not contain backslashes or control characters."
  }

  validation {
    condition = contains(
      ["/privacy-policy/", "/terms-of-service/", "/sms-opt-in.html", "/robots.txt", "/auth.md", "/sitemap.xml"],
      var.post_logout_path
    ) || startswith(var.post_logout_path, "/.well-known/") || startswith(var.post_logout_path, "/assets/") || startswith(var.post_logout_path, "/javascripts/") || startswith(var.post_logout_path, "/stylesheets/")
    error_message = "post_logout_path must be one of the publicly served paths in the server allowlist, otherwise logout sends users straight back into a login redirect."
  }
}

variable "custom_domain_name" {
  description = "App Runner custom domain to associate with the service."
  type        = string
  default     = "docs.authifi.io"
}

variable "enable_custom_domain" {
  description = "Whether to create the App Runner custom domain association when the service exists."
  type        = bool
  default     = true
}

variable "existing_github_oidc_provider_arn" {
  description = "Existing account-level GitHub OIDC provider ARN. Leave null to create one."
  type        = string
  default     = null
  nullable    = true
}

variable "apprunner_encryption_kms_key_arn" {
  description = "Optional customer-managed KMS key ARN for App Runner service encryption. Leave null to use the AWS-managed App Runner key."
  type        = string
  default     = null
  nullable    = true
}

variable "instance_cpu" {
  description = "App Runner vCPU reservation. Defaults to the lowest 1 vCPU tier."
  type        = string
  default     = "1024"
}

variable "instance_memory" {
  description = "App Runner memory reservation paired with instance_cpu."
  type        = string
  default     = "2048"
}

variable "auto_scaling_max_concurrency" {
  description = "Maximum concurrent requests per App Runner instance before scaling out."
  type        = number
  default     = 50
}

variable "auto_scaling_max_size" {
  description = "Maximum number of provisioned App Runner instances."
  type        = number
  default     = 2
}

variable "auto_scaling_min_size" {
  description = "Minimum number of provisioned App Runner instances."
  type        = number
  default     = 1
}

variable "tags" {
  description = "Additional tags applied to created resources."
  type        = map(string)
  default     = {}
}
