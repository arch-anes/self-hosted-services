# Agent Guide

This file defines repository-wide rules for agents working on
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

### 1.1 Scoped Guides

Read the nearest scoped guide before changing files in these areas:

- [`charts/services/AGENTS.md`](charts/services/AGENTS.md): Helm, Kubernetes,
  application integration, secrets, ingress, and observability.
- [`roles/AGENTS.md`](roles/AGENTS.md): Ansible role design and validation.
- [`scripts/AGENTS.md`](scripts/AGENTS.md): Python and CI-oriented scripting.

Scoped guides supplement this file. Repository-wide safety and workflow rules
continue to apply; this root guide takes precedence if instructions conflict.

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

- You MUST use a dedicated git worktree for each new task.
- You MUST keep each change focused on one feature or fix. Do not include
  opportunistic cleanup or unrelated edits.
- If a refactor is necessary, perform it as a separate step and commit before
  implementing the feature that depends on it.
- Preserve existing user changes and do not stage or commit them as part of
  your work.

### 3.2 Continuous Improvement

When a task reveals a durable repository pattern or pitfall, you SHOULD update
the applicable guide. Unless guide maintenance is part of the requested task,
leave that change unstaged and uncommitted for user review.

## 4. Project Architecture

### 4.1 Opinionated Configuration

- You SHOULD prefer a robust, opinionated setup over a broadly configurable
  framework.
- You MUST keep configuration knobs to a minimum, especially in
  `charts/services/values.yaml`.
- All applications MUST be part of the `charts/services` chart.

### 4.2 Multitenancy

- Only the primary tenant (`.Values.primaryTenant: true`) MAY deploy system
  charts and core infrastructure.
- You MUST deploy application charts and isolated service instances, including
  `PostgresCluster` and MinIO `tenant` resources, once per tenant.

### 4.3 Shared Services

You MUST use the following platform services whenever the application supports
them, except where a scoped guide gives more specific implementation details:

- **Database:** PostgreSQL, not an embedded database such as SQLite.
- **Cache and sessions:** Redis.
- **Object storage:** MinIO through its S3-compatible API.
- **SSO:** Authentik, preferably through OAuth/OIDC.
- **Outbound email:** the shared `smtp` secret, backed by AWS SES by default.
- **Metrics:** you SHOULD enable metrics when supported.
- **VPN routing:** route all pod egress through the central `gluetun` service.

## 5. Validation

Use `.woodpecker/lint.yaml` as the source of truth for linting and validation.
Do not duplicate its complete tool list in agent guides. Follow any additional
validation requirements in the applicable scoped guide.

## 6. Completion Checklist

Before handing off a change, confirm that:

- The diff contains only the requested feature, fix, or documentation update.
- No sensitive inventory file was read or modified, and no real secret was
  introduced.
- The applicable root and scoped instructions were followed.
- Required validation passed, or skipped validation is reported with a reason.
- No production-affecting command was run without explicit permission.
