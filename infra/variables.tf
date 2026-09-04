variable "aws_region" {
  description = "AWS region for the VPC, load balancer, instance, and IAM resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.aws_region))
    error_message = "aws_region must be a region code such as us-east-1."
  }
}

variable "service_name" {
  description = "Name applied to the load balancer, target group, instance, and IAM resources."
  type        = string
  default     = "authifi-docs"

  validation {
    # Also the load balancer and target group name, which AWS limits to 32
    # characters of alphanumerics and hyphens, with no hyphen at either end.
    condition     = can(regex("^[a-zA-Z0-9]([a-zA-Z0-9-]{0,30}[a-zA-Z0-9])?$", var.service_name))
    error_message = "service_name must be 1-32 alphanumerics or hyphens and must not start or end with a hyphen."
  }
}

variable "vpc_cidr" {
  description = "IPv4 CIDR block for the docs VPC."
  type        = string
  default     = "10.42.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

variable "public_subnet_cidrs" {
  description = "The two public subnet CIDR blocks the internet-facing load balancer spans."
  type        = list(string)
  default     = ["10.42.0.0/24", "10.42.1.0/24"]

  validation {
    condition     = length(var.public_subnet_cidrs) == 2
    error_message = "Exactly two public subnet CIDRs are required for the ALB."
  }

  validation {
    condition     = alltrue([for block in var.public_subnet_cidrs : can(cidrnetmask(block))])
    error_message = "Every entry in public_subnet_cidrs must be a valid IPv4 CIDR block."
  }
}

variable "private_subnet_cidr" {
  description = "IPv4 CIDR block for the private subnet that holds the docs instance."
  type        = string
  default     = "10.42.10.0/24"

  validation {
    condition     = can(cidrnetmask(var.private_subnet_cidr))
    error_message = "private_subnet_cidr must be a valid IPv4 CIDR block."
  }
}

variable "instance_type" {
  description = "EC2 instance type for the docs server."
  type        = string
  default     = "t3.micro"
}

variable "root_volume_size_gib" {
  description = "Encrypted root EBS volume size in GiB. Holds up to three releases and their virtualenvs."
  type        = number
  default     = 20

  validation {
    condition     = var.root_volume_size_gib >= 8
    error_message = "root_volume_size_gib must be at least 8."
  }
}

variable "app_port" {
  description = "TCP port the uvicorn process listens on, and the target group's target port."
  type        = number
  default     = 8080

  # infra/scripts/deploy-release.sh probes the restarted service on
  # 127.0.0.1:8080, so changing this without changing the installer would make
  # every deployment roll itself back on a health check that can never pass.
  validation {
    condition     = var.app_port == 8080
    error_message = "app_port must stay 8080 until infra/scripts/deploy-release.sh takes the port as an input."
  }
}

variable "release_bucket_name" {
  description = "Release bucket name. Leave null to derive one from service_name and the account ID."
  type        = string
  default     = null
  nullable    = true
}

variable "release_retention_days" {
  description = "How long release archives and their superseded versions are kept in the bucket."
  type        = number
  default     = 90

  validation {
    condition     = var.release_retention_days > 0
    error_message = "release_retention_days must be greater than 0."
  }
}

variable "oidc_issuer" {
  description = "OIDC issuer base URL the docs server discovers and exchanges codes against."
  type        = string

  validation {
    condition     = startswith(var.oidc_issuer, "https://")
    error_message = "oidc_issuer must be an https URL."
  }
}

variable "oidc_client_id" {
  description = "OIDC client ID exposed to the docs server."
  type        = string

  validation {
    condition     = trimspace(var.oidc_client_id) != ""
    error_message = "oidc_client_id must not be empty."
  }
}

variable "public_base_url" {
  description = "Public HTTPS origin users reach the docs through. Must match the ALB's certificate."
  type        = string

  validation {
    condition     = startswith(var.public_base_url, "https://")
    error_message = "public_base_url must be an https URL."
  }
}

variable "site_dir" {
  description = "Absolute on-host path to the built MkDocs site inside the active release."
  type        = string
  default     = "/opt/authifi-docs/current/site"

  validation {
    condition     = startswith(var.site_dir, "/")
    error_message = "site_dir must be an absolute path."
  }
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

  # Mirrors PUBLIC_EXACT_PATHS in server/app.py, which the server re-checks at
  # startup. The public *prefixes* are deliberately excluded: they serve
  # stylesheets, scripts, and well-known documents, none of which is a page to
  # land on. server/tests/test_public_boundary.py fails if the two lists drift.
  validation {
    condition = contains(
      ["/auth.md", "/privacy-policy/", "/robots.txt", "/sitemap.xml", "/sms-opt-in.html", "/terms-of-service/"],
      var.post_logout_path
    )
    error_message = "post_logout_path must be one of the publicly served pages in the server allowlist, otherwise logout sends users straight back into a login redirect."
  }
}

variable "custom_domain_name" {
  description = "Domain name the ACM certificate is issued for and the ALB serves."
  type        = string
  default     = "docs.authifi.io"

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.custom_domain_name))
    error_message = "custom_domain_name must be a lowercase DNS name such as docs.authifi.io."
  }
}

variable "enable_https_listener" {
  description = "Enable redirect and HTTPS listeners after ACM DNS validation succeeds."
  type        = bool
  default     = false
}

variable "existing_github_oidc_provider_arn" {
  description = "Existing account-level GitHub OIDC provider ARN. Leave null to create one."
  type        = string
  default     = null
  nullable    = true
}

variable "github_repository" {
  description = "GitHub repository, as owner/name, whose workflow may assume the deployment role."
  type        = string
  default     = "Authifi/docs"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "github_repository must be in owner/name form."
  }
}

variable "deploy_branch" {
  description = "The one branch whose workflow runs may assume the deployment role."
  type        = string
  default     = "main"

  validation {
    condition     = can(regex("^[A-Za-z0-9._/-]+$", var.deploy_branch))
    error_message = "deploy_branch must be a plain branch name."
  }
}

variable "tags" {
  description = "Additional tags applied to created resources."
  type        = map(string)
  default     = {}
}
