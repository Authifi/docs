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

variable "vpc_id" {
  description = "Existing shared Authifi VPC ID."
  type        = string

  validation {
    condition     = can(regex("^vpc-[0-9a-f]{8,17}$", var.vpc_id))
    error_message = "vpc_id must be an existing VPC ID such as vpc-0123456789abcdef0."
  }
}

variable "public_subnet_ids" {
  description = "The two existing public subnet IDs, in different availability zones, that the internet-facing load balancer spans."
  type        = list(string)

  # Two entries and two *distinct* entries are different requirements. An
  # internet-facing load balancer needs two availability zones, and the same
  # subnet listed twice would pass a length check while supplying one.
  validation {
    condition     = length(var.public_subnet_ids) == 2 && length(distinct(var.public_subnet_ids)) == 2
    error_message = "Exactly two distinct existing public subnet IDs are required for the ALB."
  }

  validation {
    condition     = alltrue([for id in var.public_subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", id))])
    error_message = "Every entry in public_subnet_ids must be an existing subnet ID."
  }
}

variable "private_app_subnet_id" {
  description = "Existing private application subnet whose route table uses the shared NAT Gateway."
  type        = string

  validation {
    condition     = can(regex("^subnet-[0-9a-f]{8,17}$", var.private_app_subnet_id))
    error_message = "private_app_subnet_id must be an existing subnet ID."
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
  description = "Public HTTPS origin users reach the docs through, with no path below the root. Must match the ALB's certificate."
  type        = string

  # The origin and nothing below it. `server/app.py` mounts every route at `/`,
  # the listener rules strip no prefix, and the server appends to this value
  # verbatim when it builds the OIDC redirect URI and the post-logout URL, so
  # `https://host/docs` is a deployment whose callback URL, landing page, and
  # post-deploy probes all name something nothing serves. The server refuses
  # the same shapes at startup -- and because this value travels in user data,
  # which `user_data_replace_on_change` makes part of the instance's identity,
  # correcting it after an apply costs a destroyed and rebuilt host. So the
  # plan is where it fails.
  #
  # A trailing slash is the one path accepted, because it is the root and it is
  # how a browser shows the address. Credentials, a port, a query, and a
  # fragment are all excluded by the same pattern rather than by separate
  # blacklists: the value either is a bare https origin or it is refused.
  validation {
    condition     = can(regex("^https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+/?$", var.public_base_url))
    error_message = "public_base_url must be an https URL naming a lowercase DNS host and at most the root path, such as https://docs.authifi.io."
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

variable "enable_alb_deletion_protection" {
  description = "Refuse to delete the load balancer. Teardown means applying with this false first, then destroying."
  type        = bool
  default     = true
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

variable "github_repository_id" {
  description = "Numeric GitHub repository ID of github_repository, bound as the OIDC repository_id claim. Numeric IDs are never reused, so this is what a repository deleted and recreated under the same name cannot inherit."
  type        = string
  default     = "993416679"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "github_repository_id must be the numeric repository ID, as returned by the GitHub repository API."
  }
}

variable "deploy_branch" {
  description = "The one branch whose workflow runs may assume the deployment role. Must match the push trigger in .github/workflows/deploy.yml."
  type        = string
  default     = "main"

  validation {
    condition     = can(regex("^[A-Za-z0-9._/-]+$", var.deploy_branch))
    error_message = "deploy_branch must be a plain branch name."
  }
}

variable "deploy_environment" {
  description = "The GitHub Actions environment the deployment job declares. This is part of the OIDC subject, so it must match `environment:` in .github/workflows/deploy.yml exactly."
  type        = string
  default     = "production"

  validation {
    # GitHub allows spaces and a broad punctuation set in environment names,
    # but not the characters that would let one end the subject early.
    condition     = can(regex("^[A-Za-z0-9._ -]+$", var.deploy_environment))
    error_message = "deploy_environment must be a plain GitHub environment name."
  }
}

variable "tags" {
  description = "Additional tags applied to created resources."
  type        = map(string)
  default     = {}
}
