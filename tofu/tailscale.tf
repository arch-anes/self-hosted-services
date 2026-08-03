# Tailscale ACL configuration
resource "tailscale_acl" "self_hosted_acl" {
  count = var.enable_tailscale ? 1 : 0
  acl   = jsonencode({
    tagOwners = {
      "tag:ansible" = ["autogroup:admin", "autogroup:owner"]
    }
    autoApprovers = {
      routes = {
        "192.168.0.0/16" = ["tag:ansible"]
      }
    }
    acls = [
      {
        action = "accept"
        src    = ["*"]
        dst    = ["*:*"]
      }
    ]
  })
}
