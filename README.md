# Self-hosted services

## Prerequisites
- Ubuntu 24.04 LTS

## Setup Cloudflare DNS

Cloudflare DNS allows you to easily access your self-hosted services via a public IP as well as protect your domain from external attacks. To setup Cloudflare:

1. Buy a domain name
1. Onboard your domain to Cloudflare: https://developers.cloudflare.com/fundamentals/manage-domains/add-site/
1. Set SSL/TLS mode to `Full (strict)` mode
1. Add @ `A` record that points to your public IP
1. Add * `CNAME` record that points to @.
1. (optional) Enable the proxy status for your records to protect your domain.
1. Create an API token:
  - Go to `User Profile > API Tokens > API Tokens`
  - For Permissions, select:
    - `Zone - DNS - Edit`
    - `Zone - Zone - Read`
  - For Zone Resources, select:
    - `Include - All Zones`
  - Save the token to the [Cloudflare Kubernetes Secret](charts/services/templates/cert-manager.yaml).

## Setup SES SMTP (optional, recommended)

AWS SES SMTP enables reliable email delivery for your self-hosted services. To setup SES:

1. Create an AWS account and verify your domain in SES:
   - Go to `AWS SES Console > Verified identities`
   - Click "Create identity" and select "Domain"
   - Add the required DNS records to your domain
2. Request production access (move out of sandbox mode):
   - Go to `AWS SES Console > Account dashboard`
   - Click "Request production access" and submit the form
3. Create SMTP credentials:
   - Go to `AWS SES Console > SMTP settings`
   - Click "Create SMTP credentials"
   - Save the SMTP credentials to [SMTP Kubernetes Secret](charts/services/templates/smtp-secret.yaml)
4. Configure your applications to use SES SMTP:
   - SMTP endpoint: `email-smtp.<region>.amazonaws.com`
   - Port: 587 (STARTTLS) or 465 (TLS)
   - Use the SMTP credentials from step 3

## Setup ZFS (optional, recommended)

ZFS allows you to increase the reliability and performance of existing drives. To setup ZFS:

1. SSH into each host that supports ZFS.
1. Install ZFS (if not already installed)
   ```
    sudo apt install zfsutils-linux
   ```
1. List the drives using stable identifiers
   ```
    ls -lld /dev/disk/by-id/*
   ```
1. Create and configure a ZFS pool
   ```
    sudo zpool create -m /zfs-pool-dummy-mountpoint-do-not-use storage mirror SOME_DEVICE_1 SOME_DEVICE_2
    sudo zfs set compression=lz4 storage
    sudo zfs set atime=off storage
   ```
1. Generate an encryption key:
   ```
    sudo openssl rand -out /root/keyfile-zfs 32
   ```
1. (important) Backup the generated key
1. Create an encrypted dataset:
   ```
    sudo zfs create -o encryption=on -o keylocation=file:///root/keyfile-zfs -o keyformat=raw -o mountpoint=/storage storage/encrypted
   ```

## Setup tailscale (optional)

Tailscale allows you to access your hosts from anywhere without exposing static ports. To setup Tailscale:

1. Create an account at https://login.tailscale.com.
1. Add the following ACL rule at https://login.tailscale.com/admin/acls/file:
   ```
    "tagOwners": {
      "tag:ansible": ["autogroup:admin", "autogroup:owner"],
    },
    "autoApprovers": {
		  "routes": {
        "192.168.0.0/16": ["tag:ansible"]
      },
	  },
   ```
1. Create an OAuth client at https://login.tailscale.com/admin/settings/oauth:
  1. Enable the Write permission for Device/Core, and add the "tag:ansible" tag.
  1. Enable the Write permission for Keys/Auth Keys, and add the "tag:ansible" tag.
  1. Save and write down the OAuth client secret.

### Intranet access via Tailscale

This setup allows remote access to self-hosted services' internal network. It relies on **NextDNS** to rewrite your domain's A record to point to a local IP and  **Tailscale** to advertise the local IP to the connected clients.

#### 1. NextDNS Configuration
We use NextDNS to "fake" the DNS resolution for our domain when on public networks, pointing it to the internal HAProxy VIP.

1.  Go to [NextDNS.io](https://nextdns.io) and create an account (or use your existing one).
2.  Navigate to **Settings** > **Rewrites**.
3.  Add a new Rewrite:
    * **Domain:** `*.example.org`
    * **Answer:** `192.168.1.2` (Your HAProxy Virtual IP)

#### 2. Tailscale DNS Configuration
Configure Tailscale to force remote devices to use NextDNS, ensuring they see the "fake" internal IP for your domain.

1.  Open the **Tailscale Admin Console** > **DNS**.
2.  Under **Global Nameservers**:
    * Click **Add Nameserver** > **Custom...**
    * Enter the IPv6 DNS address from your NextDNS dashboard (e.g., `2a07:a8c0::ic:abcd`).
3.  Enable **Override local DNS**.
    * *This ensures Android/iOS devices use NextDNS instead of the cellular provider's DNS.*

## Create an inventory

```yml
all:
  vars:
    k3s_control_node: false
    skip_system_setup: false
    skip_firewall_setup: false
    skip_vpn_setup: false
    skip_k8s_setup: false
    skip_binary_update: false
    manifest_only_setup: false
    display_k8s_dashboard_password: false
    timezone: America/Vancouver
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
        - nas=true
        - local=true
    small_manager:
      k3s_control_node: true
      labels:
        - public=true
        - local=true
    big_server:
      labels:
        - local=true
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

### Setup load balancing (optional)
In a typical home network setup, when HTTP(S) ports are forwarded to a specific machine, the entire service becomes unavailable if that machine goes offline. However, if your router supports OpenWRT (such as the GL-MT6000), you can install HAProxy to address this issue. To do so, add routers to your inventory under the `routers` group and run the router setup playbook: `ansible-playbook setup_router.yml -i inventory.yml`.

With this configuration, all incoming HTTP(S) traffic must now flow through the gateway ports 9080/9443 where HAProxy is installed. This is because the router forwards traffic to the HAProxy instance, which then distributes it to the backend servers. This setup ensures that even if one server goes down, the service remains available, as HAProxy will route traffic to the remaining operational servers.

To opt-out of this feature, set `chartValuesOverrides.behindTcpProxy` to `false`.

### Setup QoS (optional)

Quality of Service (QoS) via Smart Queue Management (SQM) prevents bufferbloat and ensures responsive network performance under load. SQM uses the cake qdisc (queue discipline) to intelligently manage traffic and reduce latency spikes during heavy upload/download activity. To make use of this feature:
1. Measure your actual internet speeds using a speed test
2. Add the `qos` variable to your router configuration with 95% of your measured speeds:
   ```yml
   routers:
     hosts:
       gateway:
         qos:
           download_kbps: 95000    # 95% of your download speed in kbps
           upload_kbps: 19000      # 95% of your upload speed in kbps
   ```
3. Run the router setup playbook to apply the configuration

**Note:** Setting speeds to 95% of your maximum allows SQM to manage the queue before your ISP's equipment does, preventing bufferbloat. For more details on SQM configuration and tuning, see the [OpenWrt SQM documentation](https://openwrt.org/docs/guide-user/network/traffic-shaping/sqm).

#### Note on labels

- `public`: Add to nodes that will receive external traffic directly.
- `nas`: Add to nodes that should store heavy files.
- `local`: Add to nodes that are local to the site; useful when having a hybrid cloud.

### Application specific setup

Make sure to follow the [application specific setup guide](#Applications) below before performing the initial deployment.

## Deploy

Run `ansible-playbook setup_cluster.yml -i inventory_static.yml -i inventory_ec2.yml`

## Post-deployment step
To ensure no down time, make sure all the machines have key expiry disabled: https://tailscale.com/kb/1028/key-expiry#disabling-key-expiry.

## Accessing services

After deployment, services are accessible at: `https://dash.<your-domain>`.

## Advanced use-cases

### Restore backup from Velero

Provided an S3-compatible bucket, the cluster and select volumes will be backed up by Velero and Kopia.

#### Access files manually

One can use the Kopia or Kopia UI to access the backed up files manually. Simply provide Kopia with the bucket, the keyID and keySecret to access the bucket, the respository encryption key and `kopia/default/` as the prefix.

#### Restore cluster and files

Read https://velero.io/docs/v1.16/restore-reference/.


#### Traefik TCP router

When using a TCP router, make sure to set the proxy protocol version to 2:
```
  proxyProtocol:
    version: 2
```

## Applications

By default, all applications are enabled. To selectively disable applications, edit the [values](charts/services/values.yaml) file accordingly. Some applications are mandatory for the cluster to function and cannot be disabled.

### High Availability Configuration

By default, critical services (Redis, PostgreSQL, Authentik, CrowdSec, and Homer Operator) are configured to run with 3 replicas for high availability. If you have limited compute resources, you can disable high availability by setting `highAvailability: false` in your values configuration. This will reduce the replica count from 3 to 1 for these services, significantly reducing resource usage.

```yaml
# In your inventory chartValuesOverrides or values.yaml
highAvailability: false  # Set to false to use 1 replica instead of 3
```

**Note**: Disabling high availability will reduce fault tolerance but is suitable for smaller deployments or resource-constrained environments.

### Core Infrastructure

These applications provide essential cluster functionality.

#### Traefik

Reverse proxy and load balancer.

Setup [Traefik admin secret](charts/services/templates/traefik.yaml) for dashboard access.

Access at `https://traefik.<your-domain>`.

#### Cert Manager

Automatic TLS certificate management.

Setup [Cloudflare secret](charts/services/templates/cert-manager.yaml) with API token for DNS-01 challenge.

#### Descheduler

Kubernetes descheduler for rebalancing pods.

Automatically evicts pods to optimize cluster resource usage.

### ddclient

Dynamic DNS client for Cloudflare.

Automatically updates DNS records with your public IP. Uses the Cloudflare secret configured in cert-manager setup.

#### External Secrets

Kubernetes operator for managing secrets from external sources.

Automatically generates passwords and secrets for applications.

#### Reflector

Kubernetes operator for mirroring secrets and configmaps across namespaces.

#### Reloader

Kubernetes operator for automatically reloading pods when secrets or configmaps change.

#### Node Feature Discovery

Detects hardware features and labels nodes accordingly.

#### Local Path Provisioner

Dynamic local storage provisioner for Kubernetes.

Provides storage classes: `local-path-ephemeral`, `local-path-persistent`, `local-path-persistent-namespaced`.

### Security & Authentication

#### Authentik

SSO and identity provider with LDAP support.

Open `https://auth.<your-domain>/if/flow/initial-setup/` to perform the initial setup. Note: the trailing `/` is important.

#### Crowdsec

Crowdsourced intrusion prevention system.

1. Sign-in to Crowdsec dashboard: https://app.crowdsec.net/sign-in
2. Write down the enroll key from https://app.crowdsec.net/security-engines
3. Setup [Crowdsec secret](charts/services/templates/crowdsec.yaml) with `enroll_key` and a randomly generated `bouncer_key`.

### Gitops

#### Argo CD

GitOps continuous delivery tool for Kubernetes.

Open `https://argo.<your-domain>` to perform the initial setup.

### Media Management

#### Jellyfin

Media server for streaming movies, TV shows, and music.

Access at `https://jellyfin.<your-domain>` to perform the initial setup.

#### Jellyseerr

Media request management for Jellyfin.

Access at `https://jellyseerr.<your-domain>` to perform the initial setup.

#### Arr Stack

**Sonarr**: TV show monitoring and management.  
**Radarr**: Movie monitoring and management.  
**Bazarr**: Subtitle management for your media library.  
**Prowlarr**: Centralized indexer management for Sonarr and Radarr.  
**Tdarr**: Automated media transcoding and optimization.

Setup [Arr secret](charts/services/templates/arr.yaml) with API keys for each service.

##### Bazarr

Subtitle management for movies and TV shows.

Access at `https://bazarr.<your-domain>`.

##### Prowlarr

Indexer manager for Sonarr and Radarr.

Access at `https://prowlarr.<your-domain>`.

##### Radarr

Movie collection manager.

Access at `https://radarr.<your-domain>`.

##### Sonarr

TV show collection manager.

Access at `https://sonarr.<your-domain>`.

##### Tdarr

Automated media transcoding.

Access at `https://tdarr.<your-domain>`. Setup [Arr secret](charts/services/templates/arr.yaml) with Tdarr API key.

##### LazyLibrarian

Book and audiobook management.

Access at `https://lib.<your-domain>` to perform the initial setup.

##### Transmission

BitTorrent client.

Setup [Transmission secret](charts/services/templates/transmission.yaml) with credentials.

Access at `https://transmission.<your-domain>`.

##### JOAL

Torrent ratio management.

Setup [JOAL secret](charts/services/templates/joal.yaml) with access token.

Access at `https://joal.<your-domain>`.

##### FlareSolverr

Proxy server to bypass Cloudflare protection.

Used by Prowlarr for indexers behind Cloudflare.

##### Gluetun

VPN client container for routing traffic through VPN.

1. Create an account at a supported VPN provider: https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers
2. Setup [Gluetun secret](charts/services/templates/gluetun.yaml).

### Storage & Files

#### Immich

Self-hosted photo and video backup solution.

Login to `https://immich.<your-domain>` to perform the initial setup.

#### Nextcloud

Self-hosted file sync and collaboration platform.

Login to `https://nextcloud.<your-domain>` to perform the initial setup.

#### Filebrowser

Web-based file manager.

Login to `https://filebrowser.<your-domain>` with default credentials (admin/admin), then change password.

#### MinIO

S3-compatible object storage.

Setup [MinIO secret](charts/services/templates/minio.yaml) with access credentials.

Access at `https://minio.<your-domain>`.

### Automation & Workflows

#### Home Assistant

Home automation platform.

Login to `https://ha.<your-domain>` to perform the initial setup.

#### n8n

Workflow automation tool.

Access at `https://n8n.<your-domain>`. Create an account on first visit.

### Notifications & Monitoring

#### Gotify

Self-hosted notification server.

Login to `https://gotify.<your-domain>` with default credentials (admin/admin), then change password.

#### Miniflux

Minimalist RSS feed reader.

Setup [Miniflux secret](charts/services/templates/miniflux.yaml) with admin credentials.

Access at `https://miniflux.<your-domain>`.

#### Speedtest Tracker

Internet speed monitoring. To setup:

1. Run `echo -n 'base64:'; openssl rand -base64 32;` to generate an app key.
2. Setup [Speedtest Tracker secret](charts/services/templates/speedtest-tracker.yaml) with app key.

Access at `https://speedtest.<your-domain>`.

#### Epic Games Free Games

Get notified when free games from Epic Games Store are available.

1. Setup an application on Gotify for Epic Games Free Games.
2. Setup [Epic Games secret](charts/services/templates/epicgames-freegames.yaml).

Access at `https://epicgames-freegames.<your-domain>`.

#### Wakapi

Coding activity tracker.

Setup [Wakapi secret](charts/services/templates/wakapi.yaml) with password salt.

Access at `https://wakapi.<your-domain>`.

### Business & Infrastructure Management

#### Netbox

Infrastructure resource modeling and IPAM.

Access at `https://netbox.<your-domain>`. Default credentials: admin/admin.

#### InvenTree

Inventory management system.

Setup [InvenTree secret](charts/services/templates/inventree.yaml) with admin credentials.

Access at `https://inventree.<your-domain>`.

#### Odoo

Open-source ERP and CRM.

Access at `https://odoo.<your-domain>`. Default credentials: admin/admin.

### Database Management

#### PostgreSQL Cluster

PostgreSQL database management and backup operator.

1. Create an account with S3 or an S3 compatible storage such as Backblaze B2.
2. Create a bucket where your data will be backed up.
3. Create an access key for the bucket.
4. Setup [PostgreSQL secrets](charts/services/templates/postgresql.yaml) with S3 credentials and encryption key.

#### pgAdmin4

PostgreSQL administration tool.

Setup [pgAdmin4 secret](charts/services/templates/pgadmin4.yaml) with admin password.

Access at `https://pgadmin4.<your-domain>`.

#### Redis

In-memory data store.

#### Redis Insight

Management UI for Redis.

Access Redis Insight at `https://redis.<your-domain>`.

### 3D Printing

#### OctoPrint

3D printer web interface.

Access at `https://octoprint.<your-domain>`. Create an account on first visit.

#### Obico

3D printer monitoring with AI failure detection.

Setup [Obico secret](charts/services/templates/obico.yaml) with Django secret key.

Access at `https://obico.<your-domain>`.

### Gaming

#### RED Discord Bot

Multi-purpose Discord bot. To setup:

1. Create a bot account by following https://docs.discord.red/en/stable/bot_application_guide.html.
2. Setup [RED secret](charts/services/templates/red.yaml) with Discord bot token.

#### Minecraft Bedrock

Minecraft Bedrock Edition server.

Access via port 30778 (UDP).

### Kubernetes Management

#### Kubernetes Dashboard

Web-based Kubernetes management interface.

To get the dashboard password, run the playbook with `display_k8s_dashboard_password: true`.

Access at `https://kubernetes.<your-domain>`.

#### Homer Operator

Automatically generates a dashboard from Ingress annotations.

Access at `https://dash.<your-domain>`.

### Backup & Disaster Recovery

#### Velero

Kubernetes backup and disaster recovery.

1. Create an account with S3 or an S3 compatible storage such as Backblaze B2.
2. Create a bucket where your data will be backed up.
3. Create an access key for the bucket.
4. Setup [Velero secrets](charts/services/templates/velero.yaml) with S3 credentials and encryption key.

### Observability

#### Prometheus & Grafana

Monitoring and observability stack.

Setup [Prometheus secret](charts/services/templates/prometheus.yaml) with Grafana admin credentials.

Access Grafana at `https://grafana.<your-domain>`.

#### Loki

Log aggregation system.

Setup [Loki secret](charts/services/templates/loki.yaml) with MinIO credentials.

Integrated with Grafana for log visualization.

#### Tempo

Distributed tracing backend.

Setup [Tempo secret](charts/services/templates/tempo.yaml) with MinIO credentials.

Integrated with Grafana for trace visualization.

#### Node Problem Detector

Kubernetes node problem detector.

Detects and reports node-level issues to the cluster.

#### iDRAC Exporter

Prometheus exporter for Dell iDRAC metrics.

Setup [iDRAC Exporter secret](charts/services/templates/idrac-exporter.yaml) with iDRAC credentials.

#### IPMI Exporter

Prometheus exporter for IPMI metrics.

Setup [IPMI Exporter secret](charts/services/templates/ipmi-exporter.yaml) with IPMI credentials and target hosts.

### Device Discovery

#### Akri

Kubernetes device plugin for discovering and using edge hardware.

Automatically discovers USB devices like webcams and serial devices.

#### Intel GPU

Intel GPU device plugin for Kubernetes.

Enables GPU acceleration for applications like Jellyfin, Immich, and Tdarr.

#### NVIDIA GPU

NVIDIA GPU device plugin for Kubernetes.

Enables GPU acceleration for applications like Jellyfin, Immich, and Tdarr.

#### AMD GPU

AMD GPU device plugin for Kubernetes.

Enables GPU acceleration for applications like Jellyfin, Immich, and Tdarr.

### Email

#### Stalwart

All-in-one email server with SMTP, IMAP, and JMAP support.

Setup [Stalwart secret](charts/services/templates/stalwart.yaml) with S3 credentials and admin password.

Access at `https://mail.<your-domain>`.

##### AWS-relay

Install [aws-smtp-relay](https://github.com/arch-anes/aws-smtp-relay) on your AWS account to relay emails from and to your Stalwart instance.

##### Cloudflare proxy

When using Cloudflare proxy, ensure CNAME `mail.example.org` record is not proxied through Cloudflare, otherwise the proxy will block the mail traffic [ref](https://community.cloudflare.com/t/emails-blocked-since-cloudflare-firewall-applied/659995).

##### DNS records

Open `https://mail.<your-domain>/manage/dns/<your-domain>/view` to download the zone file to import to the DNS provider.

For this setup:
1. Skip `mail` `MX` record
1. Skip `mail` `TXT` record
1. Skip `TLSA` records
1. Add `mail-ses` `MX` record with `inbound-smtp.<aws-region>.amazonaws.com`
1. Add `mail-ses` `TXT` records that contain `v=spf1` with `v=spf1 include:amazonses.com ~all`

References:
- https://docs.aws.amazon.com/ses/latest/dg/eb-ingress.html
- https://stalw.art/docs/install/dns
