# Services Chart Agent Guide

This guide applies to the `charts/services` subtree. Follow the repository-wide
[`AGENTS.md`](../../AGENTS.md) as well.

## 1. Chart and Resource Layout

- You MUST keep each application's resources in one file:
  `templates/<app_name>.yaml`.
- You MUST use structured YAML objects under `spec.values` for value overrides.
  You MUST NOT use `valuesContent`.
- You SHOULD prefer the k3s-native `HelmChart` CRD over plain Kubernetes
  resources so the Helm Controller can retry reconciliation.
- If an upstream chart can wrap non-native resources through a mechanism such
  as `extraResources`, you SHOULD use it. Otherwise, you MUST document the
  dependency clearly or enforce it with `app.require`.

## 2. Template Helpers

- You MUST wrap every application template with:

  ```gotemplate
  {{- if (include "app.enabled" (list . "app_name")) }}
  ```

- You MUST declare hard dependencies with:

  ```gotemplate
  {{- include "app.require" (list . "AppName" "dependency" "Display") -}}
  ```

- You MUST declare GPU resources with the `gpu.device` helper. Do not duplicate
  vendor mapping or driver checks in application templates. For example:

  ```gotemplate
  {{- include "gpu.device" (list . "AppName" $gpuVendor) | nindent 18 }}
  ```

## 3. Workload Configuration

### 3.1 Reloads and Resources

- You MUST add `reloader.stakater.com/auto: "true"` to Deployments, StatefulSets,
  and DaemonSets whose environment-variable configuration comes from a
  ConfigMap or Secret.
- You SHOULD NOT add Reloader solely for file-mounted ConfigMaps or Secrets.
  Kubernetes updates those files automatically, and a restart is unnecessary.
- You MUST NOT set CPU limits. For TrueCharts or TrueForge charts, explicitly
  set the CPU limit to `null`. See [Stop using CPU limits][cpu-limits] for the
  rationale.

### 3.2 Backups

Applications MUST support Velero/Kopia backups. Annotate pods with the volumes
that contain persistent application data, for example:

```yaml
backup.velero.io/backup-volumes: "vol1,vol2"
```

### 3.3 Scheduling and Storage

- You MUST use node selectors or affinity when a workload has a specific
  placement requirement:
  - You MUST use `nas: "true"` for storage-heavy applications.
  - You MUST use `public: "true"` for services receiving external traffic.
  - You MUST use the `dedicated=ai:NoSchedule` taint for AI/ML workloads.
- Let the installed GPU operators manage GPU placement and allocation. You
  MUST NOT add manual GPU node labels.
- You MUST use `local-path-persistent-namespaced` for persistent,
  tenant-isolated data.
- You MUST use `local-path-ephemeral` for transient data.
- You MUST NOT use `hostPath` volumes.
- For TrueCharts workloads on single-node or otherwise isolated nodes, you MUST
  set `podOptions.defaultAffinity: false`. This prevents the `common` library's
  automatic PVC pod affinity from leaving pods in `Pending`.

## 4. Application Integration

Implement the shared-service choices in the root guide as follows:

- You SHOULD enable metrics with the `metrics.enabled` helper when supported.
- You MUST use the `tunnel.deployment.container` sidecar helper from
  `_tunnel.tpl` for VPN routing.
- You MUST add Authentik blueprints to `templates/authentik.yaml` or provide
  them through `extraManifests`.
- TrueCharts and TrueForge integrations MAY provide Authentik blueprints
  through ConfigMap values.
- You MUST use the `ldap.base_dn` helper when LDAP is required.

## 5. Secrets and Database Connectivity

- You MUST use `ExternalSecret` or `ClusterGenerator` for dynamically managed
  secrets.
- When a user must create a secret manually, you MUST include a commented-out
  `Secret` manifest as a reference.
- PostgreSQL users MUST specify alphanumeric generated passwords to avoid URL
  and connection-string parsing errors:

  ```yaml
  password:
    type: AlphaNumeric
  ```

- Crunchy Data PostgreSQL clusters enforce `hostssl`. You MUST NOT weaken this
  by adding `hostnossl`.
- You MUST configure clients with their native TLS environment variables, such
  as `PGSSLMODE="verify-full"`, and mount `ca.crt` from the
  `<cluster>-cluster-cert` Secret.
- You MUST use `external-secrets.io/v1`. You MUST NOT introduce the deprecated
  `v1beta1` API.

## 6. Ingress and Homer Discovery

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

## 7. Observability

- You MUST add Grafana dashboards for application metrics to
  `templates/prometheus.yaml`.
- In a `ServiceMonitor`, you SHOULD relabel `instance` to the stable
  `__meta_kubernetes_pod_name` value. This avoids time-series churn when pods
  restart.
- Loki node logs use `node_name`, not the kube-state-metrics-style `node`
  label. Query `node_name` before concluding that syslog or kernel streams are
  missing.

## 8. Dependency Management

- You MUST express container images with conventional `repository` and `tag`
  fields so Renovate's regex managers can detect them.
- You MUST use the `oci://` prefix in the `chart` field for OCI Helm charts.
- You MUST keep Helm dependencies discoverable through the `chart`, `repo`, and
  `version` fields used by Renovate.
- You SHOULD run `scripts/pull-upstream-helm-charts.py` from the repository root
  when adding or updating a chart. Store the upstream defaults in
  `upstream-charts/<chartname>/values.yaml`.

## 9. Validation

- Any helper change MUST include `helm-unittest` coverage in
  `tests/<topic>_test.yaml`.
- Helper test fixtures MUST use a gate such as:

  ```gotemplate
  {{- if (.Values.testFixtures).<name> }}
  ```

  This keeps ordinary `helm template` and `helm lint` runs valid.
- After changing helpers, verify with `helm unittest charts/services` from the
  repository root. Because `helm` requires explicit permission under the root
  guide, ask before running it.

[cpu-limits]: https://home.robusta.dev/blog/stop-using-cpu-limits
