# Ansible Roles Agent Guide

This guide applies to the `roles` subtree. Follow the repository-wide
[`AGENTS.md`](../AGENTS.md) as well.

## Role Design

- You SHOULD prefer roles over direct tasks in playbooks.
- You MUST gate optional behavior with `when` clauses for `skip_*` variables.

## Validation

- Before running Ansible playbooks or Molecule tests, you MUST install
  dependencies from the repository root with
  `ansible-galaxy install -r requirements.yml`.
- You MUST validate roles with `molecule test` in a sandbox.
- You SHOULD refer to
  [`../.woodpecker/test.yaml`](../.woodpecker/test.yaml) for dependencies and
  the canonical command flow.
- The root guide's permission requirement for `ansible-playbook` continues to
  apply.
