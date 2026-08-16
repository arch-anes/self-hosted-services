# Application catalog

All applications are enabled by default. Disable optional applications in [charts/services/values.yaml](charts/services/values.yaml); core cluster services remain enabled.

This page is the operational reference. It lists the included applications and only expands the ones that need credentials, external accounts, first-run configuration, or a non-default recovery procedure.

## Included services

| Area | Services |
| --- | --- |
| Core platform | Traefik, cert-manager, Descheduler, ddclient, External Secrets, Reflector, Reloader, Node Feature Discovery, Local Path Provisioner |
| Identity and delivery | Authentik, CrowdSec, Argo CD |
| Media | Jellyfin, Jellyseerr, Sonarr, Radarr, Bazarr, Prowlarr, Tdarr, Tracearr, LazyLibrarian, Transmission, Unpackerr, JOAL, FlareSolverr, Gluetun |
| Storage and home | Immich, Nextcloud, Filebrowser, MinIO, ArchiveBox, Home Assistant, n8n, Mosquitto |
| Monitoring and notifications | Gotify, Miniflux, Speedtest Tracker, Epic Games Free Games, Wakapi |
| Operations | NetBox, Homebox, Odoo, PostgreSQL, pgAdmin4, Redis, Redis Insight, Headlamp, Homer Operator |
| Hardware and gaming | OctoPrint, Obico, RED Discord Bot, Minecraft Bedrock, Akri, Intel GPU, NVIDIA GPU, AMD GPU |
| Backups and observability | Velero, Prometheus, Grafana, Blackbox Exporter, Loki, Tempo, Alloy, Node Problem Detector, iDRAC Exporter, IPMI Exporter |
| AI and email | llama.cpp, Open WebUI, Stalwart |

## High availability

Redis, PostgreSQL, Authentik, CrowdSec, and Homer Operator run with three replicas by default. On limited hardware, set `highAvailability: false` in your inventory values to run one replica of each instead. This reduces resource use and fault tolerance.

```yaml
# In your inventory chartValuesOverrides or values.yaml
highAvailability: false  # Set to false to use 1 replica instead of 3
```

## Services that need manual setup

### Core platform and identity

- **Traefik:** Configure the [admin secret](charts/services/templates/traefik.yaml), then open `https://traefik.<your-domain>`.
- **cert-manager:** Configure the [Cloudflare secret](charts/services/templates/cert-manager.yaml) with the DNS-01 API token.
- **Authentik:** Open `https://auth.<your-domain>/if/flow/initial-setup/` for initial setup. Keep the trailing `/`.
- **CrowdSec:** Sign in to the [CrowdSec dashboard](https://app.crowdsec.net/sign-in), copy the enrollment key from [Security Engines](https://app.crowdsec.net/security-engines), then configure the [CrowdSec secret](charts/services/templates/crowdsec.yaml) with `enroll_key` and a random `bouncer_key`.
- **Argo CD:** Open `https://argo.<your-domain>` for initial setup.

### Media

- **Jellyfin, Jellyseerr, Bazarr, Prowlarr, Radarr, Sonarr, Tdarr, Tracearr, and LazyLibrarian:** Open the relevant `https://<service>.<your-domain>` address for its first-run configuration.
- **Arr stack:** Configure the [Arr secret](charts/services/templates/arr.yaml) with API keys. Add Tdarr's API key to the same secret.
- **Transmission:** Configure the [Transmission secret](charts/services/templates/transmission.yaml) with credentials.
- **JOAL:** Configure the [JOAL secret](charts/services/templates/joal.yaml) with an access token.
- **Gluetun:** Create an account with a [supported VPN provider](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers), then configure the [Gluetun secret](charts/services/templates/gluetun.yaml).

### Storage, home, and automation

- **Immich, Nextcloud, ArchiveBox, Home Assistant, and n8n:** Open the service URL for initial setup or account creation.
- **Filebrowser:** Sign in with `admin` / `admin`, then change the password.
- **MinIO:** Configure the [MinIO secret](charts/services/templates/minio.yaml) with access credentials.
- **Gotify:** Sign in with `admin` / `admin`, then change the password.
- **Miniflux:** Configure the [Miniflux secret](charts/services/templates/miniflux.yaml) with admin credentials.
- **Speedtest Tracker:** Generate an app key with `echo -n 'base64:'; openssl rand -base64 32;`, then configure the [secret](charts/services/templates/speedtest-tracker.yaml).
- **Epic Games Free Games:** Create a Gotify application, then configure the [secret](charts/services/templates/epicgames-freegames.yaml).
- **Wakapi:** Configure the [Wakapi secret](charts/services/templates/wakapi.yaml) with a password salt.

### Operations, hardware, and AI

- **NetBox and Odoo:** Change the default `admin` / `admin` credentials after first sign-in.
- **PostgreSQL:** Create an S3-compatible backup bucket and access key, then configure the [PostgreSQL secrets](charts/services/templates/postgresql.yaml) with those credentials and an encryption key.
- **pgAdmin4:** Configure the [pgAdmin4 secret](charts/services/templates/pgadmin4.yaml) with an admin password.
- **OctoPrint:** Create an account on first visit.
- **Obico:** Configure the [Obico secret](charts/services/templates/obico.yaml) with a Django secret key.
- **RED Discord Bot:** [Create a bot account](https://docs.discord.red/en/stable/bot_application_guide.html), then configure the [RED secret](charts/services/templates/red.yaml) with its token.
- **Minecraft Bedrock:** Connect on UDP port 30778.
- **Headlamp:** Run the playbook with `display_headlamp_token: true` to print a token.
- **Velero:** Create an S3-compatible backup bucket and access key, then configure the [Velero secrets](charts/services/templates/velero.yaml) with those credentials and an encryption key.
- **Prometheus and Grafana:** Configure the [Prometheus secret](charts/services/templates/prometheus.yaml) with Grafana admin credentials.
- **Loki and Tempo:** Configure their [Loki](charts/services/templates/loki.yaml) and [Tempo](charts/services/templates/tempo.yaml) secrets with MinIO credentials.
- **iDRAC Exporter and IPMI Exporter:** Configure the [iDRAC](charts/services/templates/idrac-exporter.yaml) and [IPMI](charts/services/templates/ipmi-exporter.yaml) secrets with credentials and target hosts.
- **llama.cpp:** Configure the [Hugging Face secret](charts/services/templates/llama.cpp.yaml) with an access token for faster model downloads.
- **Open WebUI:** Requires PostgreSQL. It connects to llama.cpp automatically when enabled; create an account at `https://chat.<your-domain>`.

### Email

- **Stalwart:** Configure the [Stalwart secret](charts/services/templates/stalwart.yaml) with S3 credentials and an admin password, then open `https://mail.<your-domain>`.
- **AWS relay:** Install [aws-smtp-relay](https://github.com/arch-anes/aws-smtp-relay) in your AWS account to relay email to and from Stalwart.
- **Cloudflare:** Do not proxy the `mail.example.org` CNAME through Cloudflare, because its proxy blocks mail traffic. See the [Cloudflare discussion](https://community.cloudflare.com/t/emails-blocked-since-cloudflare-firewall-applied/659995).
- **DNS records:** Open `https://mail.<your-domain>/manage/dns/<your-domain>/view` to download a zone file. Skip the `mail` MX and TXT records and TLSA records; add a `mail-ses` MX record for `inbound-smtp.<aws-region>.amazonaws.com` and `mail-ses` TXT records with `v=spf1 include:amazonses.com ~all`. See the [AWS SES](https://docs.aws.amazon.com/ses/latest/dg/eb-ingress.html) and [Stalwart](https://stalw.art/docs/install/dns) references.

## Backup and disaster recovery

### PostgreSQL restore from scratch

Use this when the cluster is lost and needs to be recreated from the remote backup. Add a `dataSource` block to the `PostgresCluster` manifest before applying it:

```yaml
        spec:
          dataSource:
            pgbackrest:
              stanza: db
              configuration:
                - secret:
                    name: postgresql-backup-credentials
              options:
                - --type=time
                - --target="2021-06-09 14:15:11-04"
              global:
                compress-level: '1'
                compress-level-network: '1'
                compress-type: zst
                repo1-s3-uri-style: path
                repo1-cipher-type: aes-256-cbc
              repo:
                name: repo1
                {{- .Values.applications.postgresql.remoteBackupLocation | toYaml | nindent 16 }}
```

Remove the `dataSource` block once the cluster is running to prevent it from re-triggering on the next reconcile.

### PostgreSQL point-in-time recovery

To recover a running cluster to a specific time, add this restore configuration to the `PostgresCluster` manifest:

```yaml
        spec:
          backups:
            pgbackrest:
              restore:
                enabled: true
                repoName: repo1
                options:
                  - --type=time
                  - --target="2021-06-09 14:15:11-04"
```

Trigger the restore, then disable it after recovery completes:

```
kubectl annotate -n default postgrescluster postgresql --overwrite \
  postgres-operator.crunchydata.com/pgbackrest-restore="$(date)"
```

Once recovery is complete, disable the restore to prevent it from re-triggering:

```yaml
spec:
  backups:
    pgbackrest:
      restore:
        enabled: false
```

See the [Crunchy Data recovery reference](https://access.crunchydata.com/documentation/postgres-operator/latest/tutorials/backups-disaster-recovery/disaster-recovery#perform-an-in-place-point-in-time-recovery-pitr).

### Velero

With an S3-compatible bucket configured, Velero and Kopia back up the cluster and selected volumes. Use Kopia or the Kopia UI to browse backed-up files, configured with the bucket, its `keyID` and `keySecret`, the repository encryption key, and the `kopia/default/` prefix. See the [Velero restore reference](https://velero.io/docs/v1.16/restore-reference/).

### Traefik TCP router

When using a TCP router, set proxy protocol version 2:

```
  proxyProtocol:
    version: 2
```
