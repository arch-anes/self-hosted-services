# Agent Guide

This file defines repository-specific rules for agents working on
`self-hosted-services`. Follow it for every task in this repository.

## 1. How to Use This Guide

The requirement words in this document have consistent meanings:

- **MUST** and **MUST NOT** are mandatory.
- **SHOULD** and **SHOULD NOT** are the default. Deviate only when the task
  requires it, and explain why.
- **MAY** is optional.

When rules appear to conflict, apply them in this order:

1. Protect production systems, credentials, and user data.
2. Follow the user's explicit scope and approvals.
3. Preserve tenant isolation and repository architecture.
4. Follow the implementation and validation standards below.

If a higher-priority rule prevents a lower-priority requirement, stop at the
safe boundary and report what remains.

## 2. Safety Boundaries

### 2.1 Production Commands

- You MUST NOT run `kubectl`, `helm`, or `ansible-playbook` without explicit
  user permission. These commands can affect live infrastructure.
- Permission to inspect or edit the repository does not imply permission to
  deploy, reconcile, or mutate a cluster.

### 2.2 Sensitive Files and Secrets

- You MUST NOT read or modify any file whose name contains `inventory`, such
  as `inventory_home.yml`. These files contain sensitive production details
  and credentials.
- You MUST NOT commit real secrets.
- You MAY commit clearly identified dummy or template secrets used only for
  tests, such as `molecule/default/sample_secrets.yml`.

## 3. Working in the Repository

### 3.1 Scope and Git Workflow

- Use a dedicated git worktree for each new task.
- Keep each change focused on one feature or fix. Do not include opportunistic
  cleanup or unrelated edits.
- If a refactor is necessary, perform it as a separate step and commit before
  implementing the feature that depends on it.
- Preserve existing user changes and do not stage or commit them as part of
  your work.

### 3.2 Project Philosophy

- Prefer a robust, opinionated setup over a broadly configurable framework.
- Keep configuration knobs to a minimum, especially in
  `charts/services/values.yaml`.
- When a task reveals a durable repository pattern or pitfall, you SHOULD
  update this guide. Unless updating this guide is part of the requested task,
  leave that change unstaged and uncommitted for user review.

## 4. Kubernetes Architecture

### 4.1 Chart and Resource Layout

- All applications MUST be part of the `charts/services` chart.
- Keep each application's resources in one file:
  `charts/services/templates/<app_name>.yaml`.
- Use structured YAML objects under `spec.values` for Helm value overrides.
  You MUST NOT use `valuesContent`.
- Prefer the k3s-native `HelmChart` CRD over plain Kubernetes resources so the
  Helm Controller can retry reconciliation.
- If an upstream chart can wrap non-native resources through a mechanism such
  as `extraResources`, use it. Otherwise, document the dependency clearly or
  enforce it with `app.require`.

### 4.2 Multitenancy

- Only the primary tenant (`.Values.primaryTenant: true`) MAY deploy system
  charts and core infrastructure.
- Deploy application charts and isolated service instances, including
  `PostgresCluster` and MinIO `tenant` resources, once per tenant.

### 4.3 Template Helpers

- Wrap every application template with:

  ```gotemplate
  {{- if (include "app.enabled" (list . "app_name")) }}
  ```

- Declare hard dependencies with:

  ```gotemplate
  {{- include "app.require" (list . "AppName" "dependency" "Display") -}}
  ```

- Declare GPU resources with the `gpu.device` helper. Do not duplicate vendor
  mapping or driver checks in application templates. For example:

  ```gotemplate
  {{- include "gpu.device" (list . "AppName" $gpuVendor) | nindent 18 }}
  ```

### 4.4 Workload Configuration

- Add `reloader.stakater.com/auto: "true"` to Deployments, StatefulSets, and
  DaemonSets whose environment-variable configuration comes from a ConfigMap
  or Secret.
- Do not add Reloader solely for file-mounted ConfigMaps or Secrets. Kubernetes
  updates those files automatically, and a restart is unnecessary.
- You MUST NOT set CPU limits. For TrueCharts or TrueForge charts, explicitly
  set the CPU limit to `null`. See [Stop using CPU limits][cpu-limits] for the
  rationale.
- Applications MUST support Velero/Kopia backups. Annotate pods with the
  volumes that contain persistent application data, for example:

  ```yaml
  backup.velero.io/backup-volumes: "vol1,vol2"
  ```

### 4.5 Scheduling and Storage

- You MUST use node selectors or affinity when a workload has a specific
  placement requirement:
  - Use `nas: "true"` for storage-heavy applications.
  - Use `public: "true"` for services receiving external traffic.
  - Use the `dedicated=ai:NoSchedule` taint for AI/ML workloads.
- Let the installed GPU operators manage GPU placement and allocation. You
  MUST NOT add manual GPU node labels.
- Use `local-path-persistent-namespaced` for persistent, tenant-isolated data.
- Use `local-path-ephemeral` for transient data.
- You MUST NOT use `hostPath` volumes.
- For TrueCharts workloads on single-node or otherwise isolated nodes, set
  `podOptions.defaultAffinity: false`. This prevents the `common` library's
  automatic PVC pod affinity from leaving pods in `Pending`.

## 5. Shared Services and Application Integration

You MUST use the following platform services whenever the application supports
them, except where a more specific rule below says otherwise:

- **Database:** PostgreSQL, not an embedded database such as SQLite.
- **Cache and sessions:** Redis.
- **Object storage:** MinIO through its S3-compatible API.
- **SSO:** Authentik, preferably through OAuth/OIDC.
- **Outbound email:** the shared `smtp` secret, backed by AWS SES by default.
- **Metrics:** you SHOULD enable metrics with the `metrics.enabled` helper when
  supported.
- **VPN routing:** the `tunnel.deployment.container` sidecar helper from
  `_tunnel.tpl`, which routes all pod egress through the central `gluetun`
  service.

For Authentik integrations:

- Add the relevant blueprint to `charts/services/templates/authentik.yaml` or
  provide it through `extraManifests`.
- TrueCharts and TrueForge integrations MAY provide the blueprint through
  ConfigMap values.
- Use the `ldap.base_dn` helper when LDAP is required.

## 6. Secrets and Database Connectivity

- Use `ExternalSecret` or `ClusterGenerator` for dynamically managed secrets.
- When a user must create a secret manually, include a commented-out `Secret`
  manifest as a reference.
- PostgreSQL users MUST specify alphanumeric generated passwords to avoid URL
  and connection-string parsing errors:

  ```yaml
  password:
    type: AlphaNumeric
  ```

- Crunchy Data PostgreSQL clusters enforce `hostssl`. You MUST NOT weaken this
  by adding `hostnossl`.
- Configure clients with their native TLS environment variables, such as
  `PGSSLMODE="verify-full"`, and mount `ca.crt` from the
  `<cluster>-cluster-cert` Secret.

## 7. Ingress and Homer Discovery

`homer-operator` discovers Ingress resources automatically. Every included
Ingress MUST have:

- `homer.service.name`: dashboard group name, such as `Media`.
- `homer.item.name`: display name.
- `homer.item.logo`: URL to a square logo; SVG is preferred.

An Ingress intentionally hidden from the dashboard MUST instead include
`homer.item.excluded: "true"` to prevent clutter.

The following annotations are optional:

- `homer.service.icon`
- `homer.service.rank`
- `homer.item.rank`
- `homer.item.type`

## 8. Observability and Operational Conventions

- Add Grafana dashboards for application metrics to `prometheus.yaml`.
- In a `ServiceMonitor`, you SHOULD relabel `instance` to the stable
  `__meta_kubernetes_pod_name` value. This avoids time-series churn when pods
  restart.
- Loki node logs use `node_name`, not the kube-state-metrics-style `node`
  label. Query `node_name` before concluding that syslog or kernel streams are
  missing.
- Use `external-secrets.io/v1`. You MUST NOT introduce the deprecated
  `v1beta1` API.

## 9. Automation and Dependency Management

### 9.1 Python and CI Paths

- Python scripts MUST be idiomatic and readable.
- Python scripts MUST use guard clauses (`return` or `continue`) instead of
  deeply nested `if` and `try` blocks.
- You SHOULD use comprehensions, generator expressions, and targeted string
  splitting when they improve clarity and keep control flow flat.
- In CI pipelines, use relative paths or paths supplied by the CI environment.
  You MUST NOT hard-code host paths such as `/workspace/`.

### 9.2 Renovate Compatibility

- Express container images with conventional `repository` and `tag` fields so
  Renovate's regex managers can detect them.
- Use the `oci://` prefix in the `chart` field for OCI Helm charts.
- Keep Helm dependencies discoverable through the `chart`, `repo`, and
  `version` fields used by Renovate.

### 9.3 Upstream Chart References

You SHOULD run `scripts/pull-upstream-helm-charts.py` when adding or updating a
chart. Store the upstream defaults in `upstream-charts/<chartname>/values.yaml`
so implementation decisions can be checked against a local reference.

## 10. Validation

Use `.woodpecker/lint.yaml` as the source of truth for linting and validation.
Do not duplicate its complete tool list in this guide.

Additional requirements apply in these areas:

- Any helper change MUST include `helm-unittest` coverage in
  `charts/services/tests/<topic>_test.yaml`.
- Helper test fixtures MUST use a gate such as:

  ```gotemplate
  {{- if (.Values.testFixtures).<name> }}
  ```

  This keeps ordinary `helm template` and `helm lint` runs valid.
- After changing helpers, verify with `helm unittest charts/services`. Because
  `helm` requires explicit permission under Section 2.1, ask before running it.
- For Ansible work, prefer roles under `roles/` over direct playbook tasks and
  gate optional behavior with `when` clauses for `skip_*` variables.
- Before running Ansible playbooks or Molecule tests, install dependencies with
  `ansible-galaxy install -r requirements.yml`.
- Validate roles with `molecule test` in a sandbox. Refer to
  `.woodpecker/test.yaml` for dependencies and the canonical command flow.

## 11. Completion Checklist

Before handing off a change, confirm that:

- The diff contains only the requested feature, fix, or documentation update.
- No sensitive inventory file was read or modified, and no real secret was
  introduced.
- Tenant boundaries, storage classes, scheduling, backups, and shared-service
  integrations follow this guide where applicable.
- Tests cover shared helper changes and use gated fixtures.
- Validation matches `.woodpecker/lint.yaml`, or any skipped validation is
  reported with the reason.
- No production-affecting command was run without explicit permission.

[cpu-limits]: https://home.robusta.dev/blog/stop-using-cpu-limits
