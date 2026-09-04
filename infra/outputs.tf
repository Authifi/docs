output "aws_region" {
  description = "Configured AWS region."
  value       = var.aws_region
}

output "release_bucket_name" {
  description = "Private bucket the deployment workflow uploads release archives to."
  value       = aws_s3_bucket.releases.id
}

output "instance_id" {
  description = "Managed node the deployment command targets."
  value       = aws_instance.docs.id
}

output "target_group_arn" {
  description = "Target group the workflow polls for target health after a deployment."
  value       = aws_lb_target_group.docs.arn
}

output "ssm_document_name" {
  description = "Command document that stages a release and runs the installer."
  value       = aws_ssm_document.deploy.name
}

output "alb_dns_name" {
  description = "Load balancer hostname to point the site's DNS record at."
  value       = aws_lb.docs.dns_name
}

output "alb_zone_id" {
  description = "Hosted zone ID of the load balancer, for an alias record."
  value       = aws_lb.docs.zone_id
}

output "certificate_arn" {
  description = "ACM certificate the HTTPS listener serves once it is issued."
  value       = aws_acm_certificate.docs.arn
}

output "certificate_validation_records" {
  description = "DNS records to create externally before applying with enable_https_listener=true."
  value = [
    for option in aws_acm_certificate.docs.domain_validation_options : {
      name  = option.resource_record_name
      type  = option.resource_record_type
      value = option.resource_record_value
    }
  ]
}

output "github_deploy_role_arn" {
  description = "IAM role ARN for the GitHub Actions OIDC deployment workflow."
  value       = aws_iam_role.github_deploy.arn
}

output "github_oidc_provider_arn" {
  description = "GitHub OIDC provider ARN used by the deployment role."
  value       = local.github_oidc_provider_arn
}
