# Project Instructions (AGENTS.md)

This document is living documentation that provides foundational guidance for LLM agents authoring code for the `self-hosted-services` repository.

## General Principles

- **Continuous Improvement**: This is a living documentation file. If, after a session, you identify insights or patterns that could improve future performance or clarity, you SHOULD add them to this document (without staging or committing the changes).
- **Focus**: When tasked with a feature or modification, you MUST NOT make unrelated changes (e.g., opportunistic refactoring or "cleanup" of surrounding code), because doing so violates commit atomicity and complicates code review.
- **Atomicity**: You MUST keep changes atomic to a single feature or fix.
- **Refactoring**: If a refactor is necessary to implement a feature, you MUST perform it as a separate, distinct step/commit from the feature implementation itself.
- **Opinionated Setup**: You SHOULD NOT over-customize `charts/services/values.yaml` and you MUST keep configuration knobs at a minimum, because the project aims to provide a robust, opinionated setup rather than a highly flexible framework.
- **Inventory Protection**: You MUST NOT read or touch files containing "inventory" in their name (e.g., `inventory_home.yml`), because these files contain sensitive production details and credentials that must not be exposed.
- **Command Safety**: You MUST NOT run `kubectl`, `helm`, or `ansible-playbook` commands without explicit user permission, because running these commands arbitrarily could impact or disrupt the live production environment.
- **Secrets Management**: You MUST NOT commit real secrets, because committing actual credentials poses a severe security risk and compromises repository security. You MAY commit template or dummy secrets (e.g., `molecule/default/sample_secrets.yml`) for testing purposes only.

## Kubernetes

### Architecture

- **Multitenancy**: This chart deploys a multitenant cluster.
    - **Primary Tenant**: Only the primary tenant (where `.Values.primaryTenant` is true) SHALL deploy **system charts, operators, and core infrastructure** (e.g., Traefik, Cert-Manager, Prometheus, Postgres/Redis/MinIO Operators).
    - **General Tenants**: Application charts and **isolated service instances** (e.g., `PostgresCluster` or MinIO `tenant`) MUST be deployed for each tenant to ensure isolation.
- **Eventual Consistency**: You SHOULD use the **k3s-native `HelmChart` CRD** instead of plain Kubernetes objects or custom objects not native to k3s (like `ExternalSecret`), because this allows k3s' Helm Controller to handle retries automatically.
    - **Caveat**: If a chart supports wrapping non-native objects (via `additionalObjects`, `extraTpl`, `extraResources`, or similar notations), you SHOULD use it. Otherwise, you MUST ensure the dependency is clearly documented or handled via `app.require`.
- **Reloader**: 
    - If a workload needs to be rebalanced/restarted on config or secret changes, you MUST add **`reloader.stakater.com/auto: "true"`** to the **Deployment, DaemonSet, or StatefulSet** annotations.
    - **Note**: Reloader is NOT RECOMMENDED if the configuration or secret is mounted as a file volume, because Kubernetes updates these files automatically and unnecessary workload restarts should be avoided. You MUST use it for environment-variable based configuration.
- **CPU Limits**: You MUST NOT declare CPU limits, because they can cause unnecessary CPU throttling and performance degradation (refer to [Stop using CPU limits](https://home.robusta.dev/blog/stop-using-cpu-limits)). For `truecharts`/`trueforge` charts, you MUST explicitly set the CPU limit to `null`.
- **Storage Best Practices**: You MUST NOT use `hostPath` volumes, because they bypass tenant isolation and tie workloads to specific nodes, making scheduling less flexible.
    - You MUST use the **`local-path-persistent-namespaced`** storageClass for persistent, tenant-isolated data.
    - You MUST use the default **`local-path-ephemeral`** storageClass for transient/generated data that can be safely deleted.
- **Pod Scheduling & Hardware**: You MUST use node selectors/affinity for specific storage requirements:
    - **`nas: "true"`**: REQUIRED for storage-heavy applications (MinIO, Immich, etc.).
    - **`public: "true"`**: REQUIRED for services receiving external traffic directly.
    - **`dedicated=ai:NoSchedule`**: REQUIRED for AI/ML workloads (Immich ML, llama.cpp).
    - **GPUs**: You MUST NOT use manual node labels for GPUs, because manual labeling is error-prone and bypasses automated hardware allocation. You MUST rely on the installed **GPU operators** (Intel, NVIDIA, AMD) to handle hardware discovery and allocation.

### Service Integration

- **Database**: If an application supports **PostgreSQL**, you MUST use it instead of SQLite or other embedded databases, because embedded databases lack clustering, backups, and scalability features required in this multi-tenant cluster.
- **Cache/Storage**: If an application supports **Redis**, you MUST use it for caching or session storage.
- **S3 Storage**: You MUST use **MinIO** for S3-compatible object storage.
- **SSO**: If an application supports SSO, you MUST use **Authentik** (RECOMMENDED to integrate via OAuth/OIDC). 
    - You MUST add the relevant blueprint to `charts/services/templates/authentik.yaml` OR as a `ConfigMap` in the application's own template using `extraManifests`.
    - For **TrueCharts/TrueForge** based charts, blueprints MAY also be added via the **`configmap`** values section (see `immich.yaml` for an example).
    - You MUST use the `ldap.base_dn` helper if LDAP integration is REQUIRED.
- **VPN Routing (Egress)**:
    - If an application needs to route its outgoing traffic through a VPN, you MUST use the **`tunnel.deployment.container`** sidecar helper (defined in `_tunnel.tpl`).
    - This sidecar connects to the central `gluetun` service and routes all pod traffic through it.
- **Email**: You MUST use the shared **`smtp`** secret for outgoing notifications (AWS SES is the default provider).
- **Metrics & Dashboards**:
    - You SHOULD enable metrics via the `metrics.enabled` helper if supported.
    - You MUST add Grafana dashboards to the `dashboards` section in `charts/services/templates/prometheus.yaml`.
- **Homer Dashboard Discovery**:
    - Ingresses are automatically discovered and added to the Homer dashboard by `homer-operator`.
    - You MUST add the following annotations to the **Ingress** resource (usually in the `values` of the `HelmChart` or the `ingress` section of a library chart):
        - **`homer.service.name`**: REQUIRED. The group/section name (e.g., "Administration", "Media", "Automation").
        - **`homer.service.icon`**: OPTIONAL FontAwesome icon for the group (e.g., "fas fa-heartbeat").
        - **`homer.service.rank`**: OPTIONAL sort order for the group (lower numbers first).
        - **`homer.item.name`**: REQUIRED. The display name of the application.
        - **`homer.item.logo`**: REQUIRED (SVG RECOMMENDED). A URL to a square logo.
        - **`homer.item.excluded: "true"`**: REQUIRED if the ingress MUST NOT be included on the dashboard in order to avoid clutter.
        - **`homer.item.rank`**: OPTIONAL sort order within the group (lower numbers first).
        - **`homer.item.type`**: OPTIONAL application type for specific dashboard integrations (e.g., "Nextcloud").

### Standards

1.  **Unified Chart**: All applications MUST be part of the `charts/services` chart.
2.  **One App, One File**: You MUST keep each application's resources in `charts/services/templates/<app_name>.yaml`.
3.  **Backups**: Applications MUST be designed to be compatible with **Velero/Kopia** backups.
    - **Important**: You MUST annotate the **Pod** (not the Deployment/StatefulSet) with a comma-separated list of volumes to backup: `backup.velero.io/backup-volumes: "vol1,vol2"`.
4.  **HelmChart Configuration**:
    - You MUST use `spec.values` (YAML object) to override values. You MUST NOT use `valuesContent`, because structured YAML objects are easier to validate, merge, and maintain than raw multi-line strings.
    - You MUST always include a comment referencing the default values: `# default-values: <URL to upstream values.yaml>`.
5.  **Helper Usage**:
    - You MUST wrap templates in `{{- if (include "app.enabled" (list . "app_name")) }}`.
    - You MUST use `{{- include "app.require" (list . "AppName" "dependency" "DependencyDisplay") -}}` for hard dependencies.
    - You MUST use the `gpu.device` helper (e.g., `{{- include "gpu.device" (list . "AppName" $gpuVendor) | nindent 18 }}`) to declare GPU resources in container limits, because it standardizes vendor mapping, checks enabled driver dependencies, and avoids redundant conditional blocks.
6.  **Reference Values**: You SHOULD run `scripts/pull-helm-charts-default-values.sh` to automatically pull reference `values.yaml` files for each chart, because this keeps a local copy in the `default-values/` directory for development and avoids manual upstream searches.
7.  **Helper Unit Tests**: You MUST add `helm-unittest` cases in `charts/services/tests/<topic>_test.yaml` for any new or modified helper in `_helpers.tpl`, because the helper layer is shared by every application template and regressions there are far-reaching.
    - Helpers emit strings (not YAML manifests), so they cannot be asserted on directly. You MUST use the gated fixture pattern in `charts/services/templates/tests-helpers-fixture.yaml`: each fixture section is wrapped in `{{- if (.Values.testFixtures).<name> }}` and is therefore a safe no-op under `helm template` / `helm lint` (the `testFixtures` value is NEVER declared in `values.yaml`).
    - For helpers that can call `fail` (e.g. `app.require`), you MUST place their fixture behind a separate `testFixtures` sub-flag and assert with the `failedTemplate` matcher.
    - You MUST verify locally with `helm unittest charts/services` (also runs in `.woodpecker/lint.yaml`).

### Secrets Management

- **Generated Secrets**: You MUST use `ExternalSecret` (wrapped in a `HelmChart`'s wrapping fields if supported) or `ClusterGenerator` to manage secrets dynamically.
- **Secret Remapping**: If a secret provisioned by an operator (or upstream chart) has keys that do not match the expected keys of an application, you MUST FIRST attempt to map these natively via environment variables (using the chart's `env`, `extraEnv`, or `envFrom` values). Only as a **last resort**—when a chart rigidly requires a specific key name in an `existingSecret` reference—should you use an `ExternalSecret` targeting the central `local-kubernetes-cluster` `ClusterSecretStore` to remap the keys. You MUST NOT build custom init containers or configure new `SecretStore` providers using operator credentials.
- **Alphanumeric Passwords Policy (PostgreSQL only)**: You MUST always configure generated PostgreSQL passwords to use alphanumeric characters (without symbols or punctuation) because special characters can break database URL/connection string parsing in application workloads. Specifically, for PostgreSQL users under `spec.users` in `PostgresCluster`, you MUST set `password: { type: AlphaNumeric }`.
- **User-Provided Secrets**: If a secret MUST be provided manually by the user, you MUST include a **commented-out `Secret` template** in the application file to serve as a reference and setup guide.

## Ansible

### Playbooks

- **`setup_cluster.yml`**: Configures the K3s cluster and deploys applications onto it.
- **`setup_router.yml`**: Configures an OpenWRT-based router (HAProxy, QoS, etc.).

### Standards

- **Encapsulation**: You SHOULD prefer roles in `roles/` over direct tasks in playbooks. You MUST use `when` clauses to respect `skip_*` variables.
- **Dependencies**: Ansible Galaxy roles and collections are defined in `requirements.yml`. You MUST run `ansible-galaxy install -r requirements.yml` before executing playbooks or molecule tests.

### Testing

- **Molecule**: You MUST use `molecule test` to verify roles in a sandbox environment. You SHOULD refer to `.woodpecker/test.yaml` for the canonical test execution flow, including necessary system dependencies (KVM, libvirt, privileged mode) and requirements installation.

## Renovate

This repository uses **Renovate** for automated dependency updates. To ensure Renovate can correctly identify and update dependencies in your Helm templates, you MUST follow these patterns:

- **Docker Images**: You MUST use a standard `repository` and `tag` structure, because Renovate is configured to match these via regex.
- **Helm Charts**: Renovate tracks `HelmChart` resources by monitoring the `chart`, `repo`, and `version` fields. For OCI charts, you MUST use the `oci://` prefix in the `chart` field.

## Automation & Scripting

- **Idiomatic Code**: Custom Python automation scripts MUST prioritize readability and idiomatic patterns. You MUST use **guard clauses** (early `return` or `continue`) instead of deeply nested `if/try` blocks. You SHOULD use list comprehensions, generator expressions (e.g., `next()`), and targeted string splitting (e.g., `.split(":", 1)`) to keep logic flat and robust.
- **CI/CD Workspace Paths**: When running custom scripts or automation pipelines inside CI (e.g., Woodpecker), you MUST use **relative paths** (like `./renovate-report.json`) or CI-provided environment variables for file generation. You MUST NOT use absolute host paths like `/workspace/`, because CI runners frequently execute as non-root users and mount the codebase into dynamically generated directories, which will lead to `EACCES: permission denied` errors.

## Linting & Quality Control

- You MUST refer to `.woodpecker/lint.yaml` for the canonical linting and validation flow. You MUST NOT duplicate tool lists or configurations here, because doing so creates configuration drift and increases maintenance overhead.

## Gotchas

Recurring traps encountered during operations on this cluster.

### API Version Deprecations

- **External Secrets**: You MUST use API version `external-secrets.io/v1` (not `v1beta1`) for `ExternalSecret` and `ClusterSecretStore` resources, as older APIs are deprecated and disabled by default in modern chart versions.

### Loki Label Inconsistency for Node Logs

- Node logs ingested by Alloy use the label **`node_name`**, NOT `node`. The selector `{job="node/syslog", node="..."}` returns nothing; use `node_name=...` instead.
- This differs from kube-state-metrics and cAdvisor, which use `node`. Before claiming a node log stream is missing or recommending host-level commands (`dmesg`, `journalctl`), you MUST first re-query under `node_name`, because the syslog/kern streams DO capture kernel events and are queryable in Loki.

### Stable Instance Labels for Metrics Scraped from Workloads

- When configuring Prometheus metrics/ServiceMonitor for workloads, the default `instance` label is based on the pod IP and port, which changes whenever a pod restarts. This causes metric churn and creates a new time-series/instance in Prometheus.
- You SHOULD configure `relabelings` in the `ServiceMonitor` settings to overwrite the `instance` label with the stable pod name (`__meta_kubernetes_pod_name`) so the metrics remain mapped to the same logical instance.

### PostgreSQL SSL connections (Crunchy Data / PGO)

- The Crunchy Data Postgres Operator (`pgo`) enforces strict TLS/SSL connections (`hostssl`) via `pg_hba.conf` by default. It automatically generates a custom CA stored in a secret named `<cluster_name>-cluster-cert` (e.g., `postgresql-cluster-cert`).
- If an application natively defaults to unencrypted database connections and does not expose a dedicated SSL toggle in its Helm chart, you MUST NOT disable SSL by adding a `hostnossl` exception in the `PostgresCluster` `pg_hba.conf` configuration. Doing so weakens the cluster's zero-trust posture and is considered a last resort.
- Instead, you MUST leverage native database driver environment variables (e.g. `PGSSLMODE: "verify-full"`) in the application container to force SSL. To satisfy strict verification, you MUST mount the `ca.crt` key from the `<cluster_name>-cluster-cert` secret using `extraVolumes`/`extraVolumeMounts` (or similar chart-provided volume mounts) and point the driver to it (e.g. `PGSSLROOTCERT: "/etc/ssl/postgresql/ca.crt"`).
