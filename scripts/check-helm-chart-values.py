#!/usr/bin/env python3
"""Validate the values in rendered HelmChart resources.

The chart pull script generates full-values, override, and HelmChart schemas
for each cached upstream chart. This script renders the services chart once.
It then writes each HelmChart resource to a temporary file.

Each Kubeconform process receives one resource and one chart-specific schema.
This design prevents Kubeconform from loading all chart schemas for each
resource. The processes run concurrently and report progress as they finish.
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

HELMCHART_SCHEMA_NAME = "helmchart.schema.json"
VALUES_OVERRIDE_SCHEMA_NAME = "values.override.schema.json"
IGNORED_CHARTS = {"generic"}
VALIDATION_TIMEOUT_SECONDS = 120


class CheckError(Exception):
    """A required schema or local validation command failed."""


@dataclass(frozen=True)
class HelmChartResource:
    """Store the identity and YAML data of one rendered HelmChart resource."""

    name: str
    chart: str
    version: str
    document: dict[str, object]


@dataclass(frozen=True)
class ValidationJob:
    """Pair one isolated HelmChart file with its resource schema."""

    resource: HelmChartResource
    document_path: Path
    schema_path: Path


def repo_root() -> Path:
    """Return the repository root relative to this script's location."""
    return Path(__file__).resolve().parents[1]


def render_resources(root: Path) -> list[HelmChartResource]:
    """Render the services chart and return its supported HelmChart resources.

    The generic chart accepts arbitrary Kubernetes objects. It has no upstream
    values schema, so this script does not validate its values.

    Raises:
        CheckError: Helm cannot render the chart, or a HelmChart has no spec.
    """
    print("Rendering the services chart...", flush=True)
    result = subprocess.run(
        ["helm", "template", "charts/services"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise CheckError(f"helm template failed:\n{result.stderr.strip()}")

    resources: list[HelmChartResource] = []
    for document in yaml.safe_load_all(result.stdout):
        if not isinstance(document, dict) or document.get("kind") != "HelmChart":
            continue

        spec = document.get("spec")
        if not isinstance(spec, dict):
            raise CheckError("a rendered HelmChart resource has no object spec")

        chart = str(spec.get("chart", "")).rstrip("/").rsplit("/", 1)[-1]
        if not chart:
            raise CheckError("a rendered HelmChart resource has no chart name")
        if chart in IGNORED_CHARTS:
            continue

        metadata = document.get("metadata")
        name = metadata.get("name", "?") if isinstance(metadata, dict) else "?"
        resources.append(
            HelmChartResource(
                name=str(name),
                chart=chart,
                version=str(spec.get("version", "")),
                document=document,
            )
        )

    print(f"Rendered {len(resources)} HelmChart resources", flush=True)
    if not resources:
        raise CheckError("the rendered services chart has no supported HelmChart resources")
    return resources


def chart_schema_path(root: Path, resource: HelmChartResource) -> Path:
    """Return the matching schema for a rendered HelmChart resource.

    Raises:
        CheckError: The chart cache is absent, stale, or incomplete.
    """
    chart_dir = root / "charts" / "services" / "upstream-charts" / resource.chart
    chart_file = chart_dir / "Chart.yaml"
    if not chart_file.is_file():
        raise CheckError(
            f"{resource.name}: chart {resource.chart} is missing. " "Run scripts/pull-upstream-helm-charts.py."
        )

    chart = yaml.safe_load(chart_file.read_text(encoding="utf-8"))
    if not isinstance(chart, dict):
        raise CheckError(f"{resource.name}: {chart_file} does not contain an object")

    cached_version = str(chart.get("version", ""))
    if cached_version != resource.version:
        raise CheckError(
            f"{resource.name}: chart {resource.chart} is version {cached_version}, "
            f"but the HelmChart requests {resource.version}. "
            "Run scripts/pull-upstream-helm-charts.py."
        )

    for schema_name in (
        "values.schema.json",
        VALUES_OVERRIDE_SCHEMA_NAME,
        HELMCHART_SCHEMA_NAME,
    ):
        schema_path = chart_dir / schema_name
        if not schema_path.is_file():
            raise CheckError(
                f"{resource.name}: {schema_name} is missing for {resource.chart}. "
                "Run scripts/pull-upstream-helm-charts.py."
            )
    return chart_dir / HELMCHART_SCHEMA_NAME


def prepare_jobs(
    root: Path,
    resources: list[HelmChartResource],
    directory: Path,
) -> tuple[list[ValidationJob], list[str]]:
    """Create one validation job and temporary YAML file for each resource.

    A missing schema affects only its matching resource. The function returns
    all setup errors so the caller can still validate the remaining resources.
    """
    jobs: list[ValidationJob] = []
    errors: list[str] = []
    for index, resource in enumerate(resources, start=1):
        try:
            schema_path = chart_schema_path(root, resource)
        except CheckError as exc:
            errors.append(str(exc))
            continue

        document_path = directory / f"{index:03d}.yaml"
        document_path.write_text(
            yaml.safe_dump(resource.document, sort_keys=False),
            encoding="utf-8",
        )
        jobs.append(
            ValidationJob(
                resource=resource,
                document_path=document_path,
                schema_path=schema_path,
            )
        )
    return jobs, errors


def validate_job(job: ValidationJob) -> str | None:
    """Validate one HelmChart file and return an error message if it fails."""
    try:
        result = subprocess.run(
            [
                "kubeconform",
                "-strict",
                "-schema-location",
                job.schema_path.resolve().as_uri(),
                str(job.document_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=VALIDATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"validation exceeded {VALIDATION_TIMEOUT_SECONDS}s"

    if not result.returncode:
        return None
    return "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())


def validate_jobs(jobs: list[ValidationJob]) -> list[str]:
    """Validate jobs with one worker per available CPU and report progress."""
    if not jobs:
        return []

    workers = min(os.process_cpu_count() or 1, len(jobs))
    print(
        f"Validating {len(jobs)} HelmChart resources with {workers} workers...",
        flush=True,
    )
    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(validate_job, job): job for job in jobs}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            job = futures[future]
            try:
                diagnostic = future.result()
            except Exception as exc:
                diagnostic = f"unexpected validation error: {exc}"

            status = "FAIL" if diagnostic else "PASS"
            print(
                f"[{completed}/{len(jobs)}] {status} {job.resource.name} "
                f"({job.resource.chart} {job.resource.version})",
                flush=True,
            )
            if diagnostic:
                failures.append(f"{job.resource.name}:\n{diagnostic}")
    return failures


def main() -> int:
    """Render the services chart and validate each HelmChart resource."""
    root = repo_root()
    try:
        resources = render_resources(root)
        with tempfile.TemporaryDirectory(prefix="helmchart-values-") as temporary:
            jobs, setup_errors = prepare_jobs(root, resources, Path(temporary))
            print(f"Wrote {len(jobs)} isolated HelmChart files", flush=True)
            failures = [*setup_errors, *validate_jobs(jobs)]
    except (CheckError, OSError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if failures:
        print("\n\n".join(failures), file=sys.stderr)
        print(
            f"{len(failures)} of {len(resources)} HelmChart resources failed",
            file=sys.stderr,
        )
        return 1

    print(f"All {len(resources)} HelmChart resources match their generated schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
