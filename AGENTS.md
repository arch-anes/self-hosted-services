# Project Instructions (AGENTS.md)

This document is living documentation that provides foundational guidance for LLM agents authoring code for the `self-hosted-services` repository.

## General Principles

- **Continuous Improvement**: This is a living document. If, after a session, you identify insights or patterns that could improve future performance or clarity, you should add them to this document (without staging or committing the changes).
- **Focus**: When tasked with a feature or modification, do not make unrelated changes (e.g., opportunistic refactoring or "cleanup" of surrounding code).
- **Atomicity**: Keep changes atomic to a single feature or fix.
- **Refactoring**: If a refactor is necessary to implement a feature, perform it as a separate, distinct step/commit from the feature implementation itself.
- **Opinionated Setup**: Avoid over-customization in `charts/services/values.yaml`. Keep configuration knobs at a minimum. The project aims to provide a robust, opinionated setup rather than a highly flexible framework.
- **Inventory Protection**: Never read or touch files containing "inventory" in their name (e.g., `inventory_home.yml`).
- **Command Safety**: Never run `kubectl`, `helm`, or `ansible-playbook` commands without explicit user permission, as these can impact the production environment.
- **Secrets Management**: Never commit real secrets. You may commit template or dummy secrets (e.g., `molecule/default/sample_secrets.yml`) for testing purposes only.

## Kubernetes

### Architecture

- **Multitenancy**: This chart deploys a multitenant cluster.
    - **Primary Tenant**: Only the primary tenant (where `.Values.primaryTenant` is true) should deploy **system charts, operators, and core infrastructure** (e.g., Traefik, Cert-Manager, Prometheus, Postgres/Redis/MinIO Operators).
    - **General Tenants**: Application charts and **isolated service instances** (e.g., `PostgresCluster` or MinIO `tenant`) must be deployed for each tenant to ensure isolation.
- **Eventual Consistency**: Prefer using the **k3s-native `HelmChart` CRD** instead of plain Kubernetes objects or custom objects not native to k3s (like `ExternalSecret`). This allows k3s' Helm Controller to handle retries automatically.
    - **Caveat**: If a chart supports wrapping non-native objects (via `additionalObjects`, `extraTpl`, `extraResources`, or similar notations), use it. Otherwise, ensure the dependency is clearly documented or handled via `app.require`.
- **Reloader**: 
    - If a workload needs to be rebalanced/restarted on config or secret changes, add **`reloader.stakater.com/auto: "true"`** to the **Deployment, DaemonSet, or StatefulSet** annotations.
    - **Note**: Reloader is **not** needed if the configuration or secret is mounted as a file volume, as Kubernetes updates these files automatically. Use it for environment-variable based configuration.
- **CPU Limits**: **Avoid declaring CPU limits.** Refer to [Stop using CPU limits](https://home.robusta.dev/blog/stop-using-cpu-limits). For `truecharts`/`trueforge` charts, explicitly set the CPU limit to `null`.
- **Storage Best Practices**: **Avoid `hostPath` volumes.** 
    - Use the **`local-path-persistent-namespaced`** storageClass for persistent, tenant-isolated data.
    - Use the default **`local-path-ephemeral`** storageClass for transient/generated data that can be safely deleted.
- **Pod Scheduling & Hardware**: Use node selectors/affinity for specific storage requirements:
    - **`nas: "true"`**: For storage-heavy applications (MinIO, Immich, etc.).
    - **`public: "true"`**: For services receiving external traffic directly.
    - **`dedicated=ai:NoSchedule`**: For AI/ML workloads (Immich ML, llama.cpp).
    - **GPUs**: Do **not** use manual node labels for GPUs. Rely on the installed **GPU operators** (Intel, NVIDIA, AMD) to handle hardware discovery and allocation.

### Service Integration

- **Database**: If an application supports **PostgreSQL**, use it instead of SQLite or other embedded databases.
- **Cache/Storage**: If an application supports **Redis**, use it for caching or session storage.
- **S3 Storage**: Use **MinIO** for S3-compatible object storage.
- **SSO**: If an application supports SSO, use **Authentik** (ideally via OAuth/OIDC). 
    - Add the relevant blueprint to `charts/services/templates/authentik.yaml` OR as a `ConfigMap` in the application's own template using `extraManifests`.
    - For **TrueCharts/TrueForge** based charts, blueprints can also be added via the **`configmap`** values section (see `immich.yaml` for an example).
    - Use `ldap.base_dn` helper if LDAP integration is required.
- **VPN Routing (Egress)**:
    - If an application needs to route its outgoing traffic through a VPN, use the **`tunnel.deployment.container`** sidecar helper (defined in `_tunnel.tpl`).
    - This sidecar connects to the central `gluetun` service and routes all pod traffic through it.
- **Email**: Use the shared **`smtp`** secret for outgoing notifications (AWS SES is the default provider).
- **Metrics & Dashboards**:
    - Enable metrics via the `metrics.enabled` helper if supported.
    - Add Grafana dashboards to the `dashboards` section in `charts/services/templates/prometheus.yaml`.
- **Homer Dashboard Discovery**:
    - Ingresses are automatically discovered and added to the Homer dashboard by `homer-operator`.
    - Add the following annotations to the **Ingress** resource (usually in the `values` of the `HelmChart` or the `ingress` section of a library chart):
        - **`homer.service.name`**: The group/section name (e.g., "Administration", "Media", "Automation").
        - **`homer.service.icon`**: Optional FontAwesome icon for the group (e.g., "fas fa-heartbeat").
        - **`homer.service.rank`**: Optional sort order for the group (lower numbers first).
        - **`homer.item.name`**: The display name of the application.
        - **`homer.item.logo`**: A URL to a square logo (SVG preferred).
        - **`homer.item.excluded: "true"`**: Explicitly exclude the ingress from the dashboard.
        - **`homer.item.rank`**: Sort order within the group (lower numbers first).
        - **`homer.item.type`**: Optional application type for specific dashboard integrations (e.g., "Nextcloud").

### Standards

1.  **Unified Chart**: All applications are part of the `charts/services` chart.
2.  **One App, One File**: Keep each application's resources in `charts/services/templates/<app_name>.yaml`.
3.  **Backups**: Applications should be designed to be compatible with **Velero/Kopia** backups.
    - **Important**: Annotate the **Pod** (not the Deployment/StatefulSet) with a comma-separated list of volumes to backup: `backup.velero.io/backup-volumes: "vol1,vol2"`.
4.  **HelmChart Configuration**:
    - Use `spec.values` (YAML object) to override values. **Never use `valuesContent`**.
    - Always include a comment referencing the default values: `# default-values: <URL to upstream values.yaml>`.
5.  **Helper Usage**:
    - Wrap templates in `{{- if (include "app.enabled" (list . "app_name")) }}`.
    - Use `{{- include "app.require" (list . "AppName" "dependency" "DependencyDisplay") -}}` for hard dependencies.
6.  **Reference Values**: Run `charts/services/pull-default-values.sh` to automatically pull reference `values.yaml` files for each chart. This avoids manual lookups and keeps a local copy in the `default-values/` directory for development.

### Secrets Management

- **Generated Secrets**: Use `ExternalSecret` (wrapped in a `HelmChart`'s wrapping fields if supported) or `ClusterGenerator` to manage secrets dynamically.
- **User-Provided Secrets**: If a secret must be provided manually by the user, include a **commented-out `Secret` template** in the application file to serve as a reference and setup guide.

## Ansible

### Playbooks

- **`setup_cluster.yml`**: Configures the K3s cluster and deploys applications onto it.
- **`setup_router.yml`**: Configures an OpenWRT-based router (HAProxy, QoS, etc.).

### Standards

- **Encapsulation**: Prefer roles in `roles/` over direct tasks in playbooks. Use `when` clauses to respect `skip_*` variables.
- **Dependencies**: Ansible Galaxy roles and collections are defined in `requirements.yml`. Run `ansible-galaxy install -r requirements.yml` before executing playbooks or molecule tests.

### Testing

- **Molecule**: Use `molecule test` to verify roles in a sandbox environment. Refer to `.woodpecker/test.yaml` for the canonical test execution flow, including necessary system dependencies (KVM, libvirt, privileged mode) and requirements installation.

## Renovate

This repository uses **Renovate** for automated dependency updates. To ensure Renovate can correctly identify and update dependencies in your Helm templates, follow these patterns:

- **Docker Images**: Use a standard `repository` and `tag` structure. Renovate is configured to match these via regex.
- **Helm Charts**: Renovate tracks `HelmChart` resources by monitoring the `chart`, `repo`, and `version` fields. For OCI charts, use the `oci://` prefix in the `chart` field.

## Linting & Quality Control

- Refer to `.woodpecker/lint.yaml` for the canonical linting and validation flow. Do not duplicate tool lists or configurations here.
