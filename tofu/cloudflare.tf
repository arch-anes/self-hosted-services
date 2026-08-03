# Set SSL/TLS to Full (strict)
resource "cloudflare_zone_settings_override" "domain_settings" {
  count   = var.enable_cloudflare ? 1 : 0
  zone_id = var.cloudflare_zone_id

  settings {
    ssl = "strict"
  }
}

# @ A record
resource "cloudflare_dns_record" "root" {
  count   = var.enable_cloudflare ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = "@"
  content = var.public_ip
  type    = "A"
  proxied = true
}

# * CNAME record
resource "cloudflare_dns_record" "wildcard" {
  count   = var.enable_cloudflare ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = "*"
  content = var.domain_name
  type    = "CNAME"
  proxied = true
}

# DNS token for cert-manager
resource "cloudflare_api_token" "cert_manager" {
  count = var.enable_cloudflare ? 1 : 0
  name  = "k8s-cert-manager-dns-challenge"

  policy {
    permission_groups = [
      data.cloudflare_api_token_permission_groups.all[0].zone["DNS Write"],
      data.cloudflare_api_token_permission_groups.all[0].zone["Zone Read"]
    ]
    resources = {
      "com.cloudflare.api.account.zone.*" = "*"
    }
  }
}

data "cloudflare_api_token_permission_groups" "all" {
  count = var.enable_cloudflare ? 1 : 0
}

# Mail DNS records for Stalwart/AWS-relay
resource "cloudflare_dns_record" "mail_ses_mx" {
  count    = var.enable_cloudflare && var.enable_aws_ses ? 1 : 0
  zone_id  = var.cloudflare_zone_id
  name     = "mail-ses"
  content  = "inbound-smtp.${var.aws_region}.amazonaws.com"
  type     = "MX"
  priority = 10
  proxied  = false
}

resource "cloudflare_dns_record" "mail_ses_txt" {
  count   = var.enable_cloudflare && var.enable_aws_ses ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = "mail-ses"
  content = "v=spf1 include:amazonses.com ~all"
  type    = "TXT"
  proxied = false
}

output "cert_manager_api_token" {
  value     = length(cloudflare_api_token.cert_manager) > 0 ? cloudflare_api_token.cert_manager[0].value : null
  sensitive = true
}
