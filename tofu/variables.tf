variable "enable_cloudflare" {
  type        = bool
  default     = false
  description = "Enable Cloudflare DNS configuration"
}

variable "enable_aws_ses" {
  type        = bool
  default     = false
  description = "Enable AWS SES configuration"
}

variable "enable_tailscale" {
  type        = bool
  default     = false
  description = "Enable Tailscale configuration"
}

variable "enable_b2_backups" {
  type        = bool
  default     = false
  description = "Enable Backblaze B2 backups configuration"
}

variable "domain_name" {
  type        = string
  description = "The main domain name (e.g., example.com)"
  default     = ""
}

variable "cloudflare_admin_token" {
  type        = string
  description = "Cloudflare API token with admin privileges for bootstrapping"
  sensitive   = true
  default     = ""
}

variable "cloudflare_zone_id" {
  type        = string
  description = "The Cloudflare Zone ID for the domain"
  default     = ""
}

variable "public_ip" {
  type        = string
  description = "Public IP address for the A record"
  default     = ""
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "aws_access_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "aws_secret_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "tailscale_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "tailscale_tailnet" {
  type    = string
  default = ""
}

variable "b2_application_key_id" {
  type      = string
  sensitive = true
  default   = ""
}

variable "b2_application_key" {
  type      = string
  sensitive = true
  default   = ""
}
