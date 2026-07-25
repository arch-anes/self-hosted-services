# Project Instructions (AGENTS.md)

This living document provides foundational guidance for LLM agents authoring code for the `self-hosted-services` repository.

## 1. General Principles

### 1.1 Commit & Scope Management
- **Atomicity & Focus**: You MUST keep changes atomic to a single feature or fix. You MUST NOT make unrelated changes (e.g., opportunistic refactoring) as this violates commit atomicity and complicates code review.
- **Refactoring**: If a refactor is necessary, you MUST perform it as a separate, distinct step/commit prior to the feature implementation.

### 1.2 Safety & Security
- **Command Safety**: You MUST NOT run `kubectl`, `helm`, or `ansible-playbook` without explicit user permission to prevent arbitrary disruptions to the live production environment.
- **Inventory Protection**: You MUST NOT read or modify files with "inventory" in their name (e.g., `inventory_home.yml`), as they contain sensitive production details and credentials that must not be exposed.
- **Secrets**: You MUST NOT commit real secrets, as this poses a severe security risk and compromises the repository. You MAY commit template/dummy secrets (e.g., `molecule/default/sample_secrets.yml`) for testing purposes only.

### 1.3 Project Philosophy
- **Continuous Improvement**: You SHOULD update this document with new insights or patterns that improve future performance (without staging or committing the changes).
- **Opinionated Setup**: You SHOULD NOT over-customize `charts/services/values.yaml` and you MUST keep configuration knobs at a minimum, since the project aims to provide a robust, opinionated setup rather than a highly flexible framework.

## 2. Kubernetes Architecture

### 2.1 Multitenancy & Workload Isolation
- **Primary vs. General**: Only the primary tenant (`.Values.primaryTenant: true`) SHALL deploy system charts and core infrastructure. Application charts and isolated service instances (e.g., `PostgresCluster`, MinIO `tenant`) MUST be deployed for each tenant to ensure proper isolation.
- **Eventual Consistency**: You SHOULD use the k3s-native `HelmChart` CRD over plain Kubernetes objects to allow k3s' Helm Controller to handle retries automatically. If a chart supports wrapping non-native objects (like `extraResources`), you SHOULD use it; otherwise, you MUST ensure the dependency is clearly documented or handled via `app.require`.

### 2.2 Pod Configuration & Scheduling
- **Reloader**: You MUST add the `reloader.stakater.com/auto: "true"` annotation to Deployments/StatefulSets/DaemonSets requiring restart on ConfigMap/Secret changes. *Note*: Reloader is NOT RECOMMENDED for file-mounted volumes, as Kubernetes updates them automatically and unnecessary workload restarts should be avoided. You MUST use it for environment-variable based configuration.
- **CPU Limits**: You MUST NOT declare CPU limits to prevent unnecessary CPU throttling and performance degradation (refer to [Stop using CPU limits](https://home.robusta.dev/blog/stop-using-cpu-limits)). For TrueCharts/TrueForge charts, you MUST explicitly set the CPU limit to `null`.
- **Scheduling**: You MUST use node selectors/affinity for specific storage requirements instead of manual labels:
  - `nas: "true"` is REQUIRED for storage-heavy apps.
  - `public: "true"` is REQUIRED for external traffic services.
  - `dedicated=ai:NoSchedule` is REQUIRED for AI/ML workloads.
  - **GPUs**: You MUST rely on the installed GPU operators to handle hardware allocation. You MUST NOT use manual node labels for GPUs, as manual labeling is error-prone and bypasses automated hardware allocation.

### 2.3 Storage
- **Storage Classes**: You MUST use `local-path-persistent-namespaced` for persistent, tenant-isolated data, and you MUST use `local-path-ephemeral` for transient data.
- **HostPath**: You MUST NOT use `hostPath` volumes, as they bypass tenant isolation and tie workloads to specific nodes, making scheduling less flexible.

## 3. Service Integration

Standardize on the following core services when supported by an application:
- **Database**: You MUST use PostgreSQL over embedded databases like SQLite since embedded databases lack clustering, backups, and scalability features required in this multi-tenant cluster.
- **Cache/Session**: You MUST use Redis for caching or session storage if supported.
- **Object Storage**: You MUST use MinIO for S3-compatible object storage.
- **SSO**: You MUST use Authentik if supported (RECOMMENDED to integrate via OAuth/OIDC). You MUST add the relevant blueprint to `charts/services/templates/authentik.yaml` or as `extraManifests`. For TrueCharts/TrueForge, they MAY also be added via `configmap` values. You MUST use the `ldap.base_dn` helper if LDAP integration is REQUIRED.
- **VPN Routing**: You MUST use the `tunnel.deployment.container` sidecar helper (from `_tunnel.tpl`) to route all pod outbound traffic through the central `gluetun` service.
- **Email**: You MUST use the shared `smtp` secret (AWS SES default) for outgoing notifications.
- **Metrics**: You SHOULD enable metrics via the `metrics.enabled` helper if supported, and you MUST add Grafana dashboards to `prometheus.yaml`.

### 3.1 Homer Dashboard Discovery
Ingresses are auto-discovered by `homer-operator`. You MUST add the following annotations to Ingress resources:
- `homer.service.name`: REQUIRED. Group name (e.g., "Media").
- `homer.item.name`: REQUIRED. Display name.
- `homer.item.logo`: REQUIRED (SVG RECOMMENDED). URL to a square logo.
- `homer.item.excluded: "true"`: REQUIRED if hiding from the dashboard to avoid clutter.
- `homer.service.icon`, `homer.service.rank`, `homer.item.rank`, `homer.item.type`: OPTIONAL.

## 4. Helm Chart Standards

- **Unified Structure**: All applications MUST be part of the `charts/services` chart.
- **One App, One File**: You MUST keep each application's resources in `charts/services/templates/<app_name>.yaml`.
- **Configuration**: You MUST use structured `spec.values` (YAML objects) to override values. You MUST NOT use `valuesContent`, as structured YAML objects are easier to validate, merge, and maintain than raw multi-line strings.
- **Backups**: Applications MUST be designed to be compatible with Velero/Kopia backups. You MUST annotate the Pod with: `backup.velero.io/backup-volumes: "vol1,vol2"`.
- **Reference Values**: You SHOULD run `scripts/pull-upstream-helm-charts.py` to keep a local copy of upstream defaults in `upstream-charts/<chartname>/values.yaml`, since this keeps the full context locally and avoids manual upstream searches.

### 4.1 Helpers (`_helpers.tpl`)
- You MUST wrap templates in: `{{- if (include "app.enabled" (list . "app_name")) }}`.
- You MUST use `{{- include "app.require" (list . "AppName" "dependency" "Display") -}}` for hard dependencies.
- You MUST use the `gpu.device` helper (e.g., `{{- include "gpu.device" (list . "AppName" $gpuVendor) | nindent 18 }}`) to declare GPU resources, because it standardizes vendor mapping, checks enabled driver dependencies, and avoids redundant conditional blocks.
- **Testing**: Any helper modifications MUST include `helm-unittest` cases in `charts/services/tests/<topic>_test.yaml`. You MUST use the gated fixture pattern (`{{- if (.Values.testFixtures).<name> }}`) to allow `helm template` and `helm lint` to pass safely, since the helper layer is shared by every template and regressions are far-reaching. You MUST verify locally with `helm unittest charts/services`.

## 5. Kubernetes Secrets Management

- **Dynamic Secrets**: You MUST use `ExternalSecret` or `ClusterGenerator` to manage secrets dynamically.
- **Postgres Passwords**: You MUST always enforce alphanumeric passwords (`password: { type: AlphaNumeric }` in `spec.users`) to prevent parsing errors in application database URLs/connection strings.
- **Manual Secrets**: You MUST include a commented-out `Secret` template as a reference if the user must provide one manually.

## 6. Ansible Guidelines
- **Structure**: You SHOULD prefer roles (`roles/`) over direct tasks in playbooks. You MUST use `when` clauses for `skip_*` variables.
- **Dependencies**: You MUST run `ansible-galaxy install -r requirements.yml` before executing playbooks or molecule tests.
- **Testing**: You MUST use `molecule test` to verify roles in a sandbox environment. You SHOULD refer to `.woodpecker/test.yaml` for system dependencies and the canonical execution flow.

## 7. Ecosystem Tools

### 7.1 Renovate
Ensure automated dependency updates function correctly:
- **Docker**: You MUST use standard `repository` and `tag` structures as Renovate is configured to match these via regex.
- **Helm**: You MUST use the `oci://` prefix in the `chart` field for OCI charts. Renovate tracks `HelmChart` resources via `chart`, `repo`, and `version` fields.

### 7.2 Linting
You MUST refer to `.woodpecker/lint.yaml` for the canonical linting/validation flow. You MUST NOT duplicate tool lists here to avoid configuration drift and increased maintenance overhead.

## 8. Operational Gotchas

- **External Secrets API**: You MUST use `external-secrets.io/v1` (not `v1beta1`) as older APIs are deprecated and disabled by default in modern chart versions.
- **Loki Node Logs**: Node logs use the label `node_name`, not `node` (unlike kube-state-metrics). You MUST first re-query under `node_name` before assuming missing streams, since syslog/kern streams do capture kernel events.
- **Prometheus Metric Churn**: You SHOULD configure `relabelings` in `ServiceMonitor` to map the `instance` label to the stable `__meta_kubernetes_pod_name`. This prevents metrics churn and new time-series generation upon pod restarts.
- **Postgres SSL**: The Crunchy Data operator enforces `hostssl` by default. You MUST NOT disable this via `hostnossl` as doing so weakens the cluster's zero-trust posture. Instead, you MUST leverage native database driver environment variables (e.g., `PGSSLMODE="verify-full"`) and you MUST mount the `ca.crt` key from the `<cluster>-cluster-cert` secret.
- **TrueCharts PVC Deadlocks**: The `common` library automatically injects a `podAffinity` for PVCs. For single-node clusters or isolated nodes, you MUST explicitly disable this auto-injection by setting `podOptions.defaultAffinity: false` in `HelmChart` values to prevent pods from getting stuck in `Pending` due to unsatisfied affinity.
