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
    # The intersection of what every consumer of this name accepts, which is
    # narrower than any one of them.
    #
    # The load balancer and target group take 32 characters of alphanumerics
    # and hyphens with no hyphen at either end, and they would take mixed case.
    # `local.release_bucket_name` derives the default release bucket from the
    # same value, and S3 bucket names are lowercase only -- so `Authifi-Docs`
    # used to validate, plan cleanly, and then fail during apply at bucket
    # creation, after the security groups and the certificate had already been
    # created. Lowercasing only the bucket-derived value would leave the plan
    # naming something that does not exist, so the constraint belongs here:
    # one contract, valid for every resource this name reaches.
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$", var.service_name))
    error_message = "service_name must be 1-32 lowercase alphanumerics or hyphens and must not start or end with a hyphen."
  }

  # The alphabet is necessary and not sufficient: each of the three services
  # reserves a set of leading strings, and a name that trips one of them fails
  # during apply, after the VPC lookups, the security groups, and the
  # certificate have been created.
  #
  # S3 refuses a bucket name beginning `xn--` (its punycode prefix) or
  # `sthree-`. An internet-facing load balancer may not be named `internal-*`,
  # because that is how the ELB API spells the internal scheme. Systems Manager
  # reserves `aws`, `amazon`, and `amzn` for document names, and
  # `aws_ssm_document.deploy` is named from this value.
  #
  # Anchored on purpose. Only the prefix is reserved, so `authifi-aws-docs` and
  # `docs-internal` are fine, and a rule that merely looked for the words would
  # refuse a good name for a reason nobody could find.
  validation {
    condition     = length(regexall("^(xn--|sthree-|internal-|aws|amazon|amzn)", var.service_name)) == 0
    error_message = "service_name must not begin with xn--, sthree-, internal-, aws, amazon, or amzn: S3, the load balancer, and Systems Manager each reserve those prefixes and would refuse the derived name during apply."
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
  description = "EC2 instance type for the docs server. Must be x86_64: the selected AMI and the Linux wheelhouse built into each release are amd64 only. Checked at plan time against the EC2 API's supported_architectures."
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

  # Authlib appends `/.well-known/openid-configuration` to this value after
  # stripping a trailing slash, so the plan refuses anything that would make
  # discovery URL the issuer never serves. `server/app.py` re-checks the same
  # shapes at startup via `validate_oidc_issuer`.
  validation {
    condition     = can(regex("^https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(/[a-z0-9_][a-z0-9._-]*(/[a-z0-9_][a-z0-9._-]*)*)?/?$", var.oidc_issuer))
    error_message = "oidc_issuer must be an https URL naming a lowercase DNS host and an optional path, such as https://issuer.example.com/tenants/acme."
  }

  validation {
    condition     = length(regexall("[?#@]", var.oidc_issuer)) == 0
    error_message = "oidc_issuer must not contain a query, fragment, or credentials."
  }

  # See site_dir for why every value that travels in user data is checked for
  # these.
  validation {
    condition     = length(regexall("[[:cntrl:]]", var.oidc_issuer)) == 0
    error_message = "oidc_issuer must not contain control characters."
  }
}

variable "oidc_client_id" {
  description = "OIDC client ID exposed to the docs server."
  type        = string

  validation {
    condition     = trimspace(var.oidc_client_id) != ""
    error_message = "oidc_client_id must not be empty."
  }

  validation {
    condition     = length(regexall("[[:cntrl:]]", var.oidc_client_id)) == 0
    error_message = "oidc_client_id must not contain control characters."
  }
}

variable "oidc_client_secret_parameter_name" {
  description = "Fixed SSM SecureString name synchronized from the GitHub production environment."
  type        = string
  default     = "/authifi-docs/oidc-client-secret"

  validation {
    condition     = contains(["/authifi-docs/oidc-client-secret"], var.oidc_client_secret_parameter_name)
    error_message = "oidc_client_secret_parameter_name is fixed to /authifi-docs/oidc-client-secret."
  }
}

variable "public_base_url" {
  description = "Public HTTPS origin for this deployment. Fixed to https://docs.authifi.io because MkDocs output and static agent assets are authored for that origin."
  type        = string

  validation {
    condition     = contains(["https://docs.authifi.io", "https://docs.authifi.io/"], var.public_base_url)
    error_message = "public_base_url must be https://docs.authifi.io (or the same origin with a trailing slash): MkDocs and static agent assets are authored for that hostname."
  }
}

variable "site_dir" {
  description = "On-host path to the built MkDocs site inside the active release. Fixed to /opt/authifi-docs/current/site to match the release builder, installer, and systemd unit."
  type        = string
  default     = "/opt/authifi-docs/current/site"

  validation {
    condition     = contains(["/opt/authifi-docs/current/site"], var.site_dir)
    error_message = "site_dir must be /opt/authifi-docs/current/site: the release builder, installer, and systemd unit all expect that path."
  }
}

variable "post_logout_path" {
  description = "Site-relative path users land on after logout. Must be a publicly served path, and must be registered with Authifi as a post-logout redirect URI."
  type        = string
  default     = "/logged-off"

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
      ["/auth.md", "/logged-off", "/logged-off/", "/privacy-policy/", "/robots.txt", "/sitemap.xml", "/sms-opt-in.html", "/terms-of-service/"],
      var.post_logout_path
    )
    error_message = "post_logout_path must be one of the publicly served pages in the server allowlist, otherwise logout sends users straight back into a login redirect."
  }
}

variable "custom_domain_name" {
  description = "Domain name the ACM certificate is issued for and the ALB serves. Fixed to docs.authifi.io for this single-site deployment."
  type        = string
  default     = "docs.authifi.io"

  validation {
    condition     = contains(["docs.authifi.io"], var.custom_domain_name)
    error_message = "custom_domain_name must be docs.authifi.io: this root deploys one site whose MkDocs output and static assets target that hostname."
  }
}

variable "enable_alb_deletion_protection" {
  description = "Refuse to delete the load balancer. Teardown means applying with this false and release_bucket_force_destroy true first, then destroying."
  type        = bool
  default     = true
}

variable "release_bucket_force_destroy" {
  description = "Allow Terraform to delete the versioned release bucket even when it holds objects. Teardown means applying with this true first, then destroying."
  type        = bool
  default     = false
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

variable "github_repository_subject" {
  description = "Exact OIDC subject GitHub issues to the production deployment job, as observed in AWS CloudTrail."
  type        = string
  default     = "repo:Authifi@37509689/docs@993416679:environment:production"

  validation {
    condition     = can(regex("^repo:[A-Za-z0-9._-]+@[0-9]+/[A-Za-z0-9._-]+@[0-9]+:environment:[A-Za-z0-9._-]+$", var.github_repository_subject))
    error_message = "github_repository_subject must be the exact immutable GitHub OIDC subject for an environment deployment."
  }
}

variable "github_repository_owner_id" {
  description = "Numeric GitHub owner ID encoded in github_repository_subject."
  type        = string
  default     = "37509689"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_owner_id))
    error_message = "github_repository_owner_id must be the numeric organization or user ID returned by the GitHub API."
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
