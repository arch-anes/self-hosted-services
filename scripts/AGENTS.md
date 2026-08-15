# Scripts Agent Guide

This guide applies to the `scripts` subtree. Follow the repository-wide
[`AGENTS.md`](../AGENTS.md) as well.

## Python

- Python scripts MUST be idiomatic and readable.
- Python scripts MUST use guard clauses (`return` or `continue`) instead of
  deeply nested `if` and `try` blocks.
- You SHOULD use comprehensions, generator expressions, and targeted string
  splitting when they improve clarity and keep control flow flat.

## CI Paths

- When a script runs in CI, use relative paths or paths supplied by the CI
  environment.
- You MUST NOT hard-code host paths such as `/workspace/`.

## Validation

Use the Python checks in
[`../.woodpecker/lint.yaml`](../.woodpecker/lint.yaml) as the canonical
validation flow.
