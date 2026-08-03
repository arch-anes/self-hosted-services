# Backblaze B2 Buckets for PostgreSQL & Velero
resource "b2_bucket" "velero_backups" {
  count       = var.enable_b2_backups ? 1 : 0
  bucket_name = "shs-velero-backups-${replace(var.domain_name, ".", "-")}"
  bucket_type = "allPrivate"
}

resource "b2_bucket" "postgres_backups" {
  count       = var.enable_b2_backups ? 1 : 0
  bucket_name = "shs-postgres-backups-${replace(var.domain_name, ".", "-")}"
  bucket_type = "allPrivate"
}

# Application Key for the buckets
resource "b2_application_key" "backup_key" {
  count        = var.enable_b2_backups ? 1 : 0
  key_name     = "shs-backup-key"
  capabilities = ["listKeys", "listBuckets", "readBuckets", "readFiles", "writeFiles", "deleteFiles"]
}

output "b2_app_key_id" {
  value = length(b2_application_key.backup_key) > 0 ? b2_application_key.backup_key[0].application_key_id : null
}

output "b2_app_key" {
  value     = length(b2_application_key.backup_key) > 0 ? b2_application_key.backup_key[0].application_key : null
  sensitive = true
}
