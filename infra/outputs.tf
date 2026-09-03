output "aws_region" {
  description = "Configured AWS region."
  value       = var.aws_region
}

output "ecr_repository_arn" {
  description = "Private ECR repository ARN."
  value       = aws_ecr_repository.docs.arn
}

output "ecr_repository_name" {
  description = "Private ECR repository name."
  value       = aws_ecr_repository.docs.name
}

output "ecr_repository_url" {
  description = "Private ECR repository URL used by Docker builds and App Runner."
  value       = aws_ecr_repository.docs.repository_url
}

output "apprunner_service_arn" {
  description = "App Runner service ARN, or null when create_service is false."
  value       = try(aws_apprunner_service.docs[0].arn, null)
}

output "apprunner_service_url" {
  description = "App Runner-generated service hostname, or null when create_service is false."
  value       = try(aws_apprunner_service.docs[0].service_url, null)
}

output "apprunner_service_https_url" {
  description = "App Runner-generated service HTTPS URL, or null when create_service is false."
  value       = try("https://${aws_apprunner_service.docs[0].service_url}", null)
}

output "github_deploy_role_arn" {
  description = "IAM role ARN for the GitHub Actions OIDC deployment workflow."
  value       = aws_iam_role.github_deploy.arn
}

output "github_oidc_provider_arn" {
  description = "GitHub OIDC provider ARN used by the deployment role."
  value       = local.github_oidc_provider_arn
}

output "custom_domain_dns_target" {
  description = "App Runner DNS target for the custom domain CNAME record, or null when no custom domain association exists."
  value       = try(aws_apprunner_custom_domain_association.docs[0].dns_target, null)
}

output "custom_domain_cname_record" {
  description = "Primary custom-domain DNS record to point at the App Runner service."
  value = try({
    name  = var.custom_domain_name
    type  = "CNAME"
    value = aws_apprunner_custom_domain_association.docs[0].dns_target
  }, null)
}

output "custom_domain_certificate_validation_records" {
  description = "Certificate validation CNAME records that must be created in DNS after the custom domain association is applied."
  value = try([
    for record in aws_apprunner_custom_domain_association.docs[0].certificate_validation_records : {
      name   = record.name
      status = record.status
      type   = record.type
      value  = record.value
    }
  ], null)
}
