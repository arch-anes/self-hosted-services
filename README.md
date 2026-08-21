# Self-hosted services

Built for homelab operators who want a complete, opinionated platform they can understand and control.

This repository uses Ansible to deploy Kubernetes and a private suite of services for media, photos, files, home automation, monitoring, backups, and local AI. Applications share PostgreSQL, Redis, MinIO, Authentik, SMTP, observability, and VPN-routed egress where they support them.

## What you get

- Media and requests: Jellyfin, Jellyseerr, the Arr stack, and Transmission.
- Photos, files, and archives: Immich, Nextcloud, MinIO, Filebrowser, and ArchiveBox.
- Home and automation: Home Assistant, n8n, Mosquitto, and 3D-printing tools.
- Platform operations: Authentik SSO, Argo CD, PostgreSQL, Redis, Velero, Grafana, Loki, and Tempo.
- Local AI: llama.cpp and Open WebUI.

See [the application catalog](APPLICATIONS.md) for the complete list and service-specific setup steps.

## Prerequisites

- Ubuntu 24.04 LTS hosts
- A domain managed by Cloudflare
- Ansible access to every host

## Quick start

1. [Configure Cloudflare DNS](#configure-cloudflare-dns).
2. Optionally configure [SES SMTP](#configure-ses-smtp), [ZFS](#configure-zfs), and [Tailscale](#configure-tailscale).
3. Create an inventory from the example below.
4. Complete the required entries in [the application catalog](APPLICATIONS.md#services-that-need-manual-setup).
5. [Deploy](#deploy) and open `https://dash.<your-domain>`.

## Configure Cloudflare DNS

Cloudflare provides public DNS for the services. Its proxy can also shield eligible HTTP services from direct traffic.

1. Buy a domain name and [add it to Cloudflare](https://developers.cloudflare.com/fundamentals/manage-domains/add-site/).
2. Set SSL/TLS encryption mode to `Full (strict)`.
3. Add an `A` record for `@` that points to your public IP.
4. Add a wildcard `CNAME` record (`*`) that points to `@`.
5. Optionally enable Cloudflare's proxy for records that carry HTTP traffic.
6. Create an API token under `User Profile > API Tokens` with:

   - Permissions:
     - `Zone - DNS - Edit`
     - `Zone - Zone - Read`
   - Zone resources:
     - `Include - All Zones`

   Store the token in the [Cloudflare Kubernetes secret](charts/services/templates/cert-manager.yaml).

## Configure SES SMTP (optional, recommended)

AWS SES provides outbound email for applications in this stack.

1. Create an AWS account and verify your domain in `AWS SES Console > Verified identities`.
2. Request production access from `AWS SES Console > Account dashboard` to leave sandbox mode.
3. Create SMTP credentials under `AWS SES Console > SMTP settings`, then store them in the [SMTP Kubernetes secret](charts/services/templates/smtp-secret.yaml).
4. Configure applications with the endpoint `email-smtp.<region>.amazonaws.com` and port 587 (STARTTLS) or 465 (TLS).

## Configure ZFS (optional, recommended)

ZFS adds checksumming, compression, and reliable storage management. Put the
`zfs` object directly under each storage-capable host in your Ansible inventory,
as shown in [Create an inventory](#create-an-inventory). Not every Kubernetes
node necessarily has disks intended for a ZFS pool. Use whole-disk, stable
`/dev/disk/by-id`, `/dev/disk/by-partuuid`, or `/dev/disk/by-uuid` paths; the
role rejects `/dev/sdX`, `/dev/nvme*`, and every other unstable device path.

The role creates `cluster-local-storage` (128K recordsize) and `multimedia`
(1M recordsize) beneath the encrypted dataset. Any additional dataset must
declare a `recordsize`.

Existing pools are never recreated or destroyed. Existing vdevs must exactly
match the declared disk set and topology; otherwise the run fails before it can
change the pool. Declared vdevs that are absent are added. A disk already owned
by another pool is rejected. Existing encrypted datasets must retain their
configured key location; an existing key at that location is reused. Back up
each host's keyfile before relying on encrypted storage.

Set `backup_keys_to_controller: true` under inventory `all.vars` to back up
generated or reused keys to the Ansible controller. They are stored in the
gitignored `keys_backup/<inventory>` directory.

## Configure Tailscale (optional)

Tailscale provides remote access to hosts without exposing static ports.

1. Create an account at [Tailscale](https://login.tailscale.com).
2. Add this ACL fragment in the [ACL editor](https://login.tailscale.com/admin/acls/file):

   ```jsonc
    "tagOwners": {
      "tag:ansible": ["autogroup:admin", "autogroup:owner"],
    },
    "autoApprovers": {
		  "routes": {
        "192.168.0.0/16": ["tag:ansible"]
      },
	  },
   ```
3. Create an [OAuth client](https://login.tailscale.com/admin/settings/oauth). Enable **Write** for **Device/Core** and **Keys/Auth Keys**, and add the `tag:ansible` tag to each permission.

### Intranet access through Tailscale

Use NextDNS to resolve your domain to the internal HAProxy virtual IP while remote devices are on Tailscale.

1. In [NextDNS](https://nextdns.io), add a rewrite under **Settings > Rewrites** for `*.example.org` that returns the HAProxy virtual IP, for example `192.168.1.2`.
2. In the Tailscale admin console under **DNS**, add the IPv6 resolver address from NextDNS as a global nameserver and enable **Override local DNS**.

## Create an inventory

Create an inventory file such as `inventory.yml` and adapt this example to your hosts:

```yml
all:
  vars:
    k3s_control_node: false
    skip_system_setup: false
    skip_zfs_setup: false
    skip_firewall_setup: false
    skip_vpn_setup: false
    skip_k8s_setup: false
    skip_binary_update: false
    manifest_only_setup: false
    backup_keys_to_controller: false
    timezone: America/Vancouver
    tenants_count: 1
    tailscale_oauth_secret: "some_secret"
    chartValuesOverrides:
      fqdn: "example.com"
      storageLocation: /storage
      # Optional: disable high availability (reduces service replicas from 3 to 1)
      highAvailability: false
      # Optional: disable unwanted applications
      applications:
        crowdsec:
          enabled: false
# There must be a minimum of 3 controllers and the number must be odd for etcd to work
k3s_cluster:
  hosts:
    raspi:
      k3s_control_node: true
      labels:
        - local=true
    big_manager:
      k3s_control_node: true
      labels:
        - local=true
        - nas=true
      zfs:
        pools:
          - name: storage
            vdevs:
              - type: mirror
                disks:
                  - /dev/disk/by-id/wwn-0x5000...
                  - /dev/disk/by-id/wwn-0x5000...
            # Optional; defaults to /root/pool_name-keyfile-zfs
            encryption_key_location: /root/storage-keyfile-zfs
            # Optional; defaults to /storage
            mountpoint: /storage
            datasets:
              - name: backups
                recordsize: 1M
                # Optional; snapshots are enabled by default.
                snapshot: true
    small_manager:
      k3s_control_node: true
      labels:
        - local=true
        - public=true
    big_server:
      labels:
        - local=true
        - runner=true
# Optional
headscale:
  hosts:
    headscale_control_server: {}
# Optional
routers:
  hosts:
    gateway:
      # wan_interface: "eth1" # Optional, will be auto-detected if not set
      # lan_ip: "192.168.1.1" # Optional, will be auto-detected from br_lan interface if not set
      haproxy:
        # virtual_ip: 192.168.1.2 # Optional, will be deduced from lan_ip if not set
        servers:
          s1: 192.168.1.11
          s2: 192.168.1.12
          s3: 192.168.1.13
      # (optional, recommended) QoS config
      qos:
        download_kbps: 95000    # 95% of your download speed
        upload_kbps: 19000      # 95% of your upload speed
```

Use an odd number of controller nodes. Add `public` to nodes that receive external traffic, `nas` to nodes that store large files, and `local` to nodes at the site. Nodes dedicated to AI workloads need the `dedicated=ai` label and `dedicated=ai:NoSchedule` taint.

### Load balancing (optional)

On an OpenWrt-capable router, such as the GL-MT6000, HAProxy can distribute inbound traffic across the cluster. Add the router to the `routers` inventory group, then run:

```bash
ansible-playbook setup_router.yml -i inventory.yml
```

All inbound HTTP(S) traffic then passes through HAProxy on gateway ports 9080 and 9443. To skip this setup, set `chartValuesOverrides.behindTcpProxy` to `false`.

### QoS (optional)

Smart Queue Management (SQM) reduces bufferbloat during heavy uploads and downloads. Measure your connection speed, then add 95% of those values to the router configuration:

```yml
routers:
  hosts:
    gateway:
      qos:
        download_kbps: 95000    # 95% of your download speed in kbps
        upload_kbps: 19000      # 95% of your upload speed in kbps
```

Run the router setup playbook to apply the configuration.

## Deploy

Complete the applicable [manual application setup](APPLICATIONS.md#services-that-need-manual-setup), then run:

```bash
ansible-playbook setup_cluster.yml -i inventory_static.yml -i inventory_ec2.yml
```

After deployment, disable Tailscale key expiry on every machine to avoid an unexpected loss of access. See [Tailscale's instructions](https://tailscale.com/kb/1028/key-expiry#disabling-key-expiry).

Open `https://dash.<your-domain>` to access the service dashboard.

## Backup and recovery

The PostgreSQL and Velero recovery procedures, including point-in-time recovery, are documented in [the application catalog](APPLICATIONS.md#backup-and-disaster-recovery).
