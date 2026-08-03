# AWS SES SMTP Setup
resource "aws_ses_domain_identity" "domain" {
  count  = var.enable_aws_ses ? 1 : 0
  domain = var.domain_name
}

# Add verification records to Cloudflare
resource "cloudflare_dns_record" "amazonses_verification" {
  count   = var.enable_aws_ses && var.enable_cloudflare ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = "_amazonses.${var.domain_name}"
  content = aws_ses_domain_identity.domain[0].verification_token
  type    = "TXT"
  proxied = false
}

# SMTP IAM User
resource "aws_iam_user" "smtp_user" {
  count = var.enable_aws_ses ? 1 : 0
  name  = "self-hosted-smtp-user"
}

# Attach policy to allow sending emails
resource "aws_iam_policy" "smtp_policy" {
  count       = var.enable_aws_ses ? 1 : 0
  name        = "self-hosted-smtp-policy"
  description = "Allows sending emails via SES"
  policy      = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ses:SendRawEmail"
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_user_policy_attachment" "smtp_attach" {
  count      = var.enable_aws_ses ? 1 : 0
  user       = aws_iam_user.smtp_user[0].name
  policy_arn = aws_iam_policy.smtp_policy[0].arn
}

resource "aws_iam_access_key" "smtp_keys" {
  count = var.enable_aws_ses ? 1 : 0
  user  = aws_iam_user.smtp_user[0].name
}

output "smtp_username" {
  value = length(aws_iam_access_key.smtp_keys) > 0 ? aws_iam_access_key.smtp_keys[0].id : null
}

output "smtp_password" {
  value     = length(aws_iam_access_key.smtp_keys) > 0 ? aws_iam_access_key.smtp_keys[0].ses_smtp_password_v4 : null
  sensitive = true
}
