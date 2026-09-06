#!/usr/bin/env python3
"""Prepare and validate each rendered HelmChart as one independent job.

The services chart is rendered once to obtain its exact HelmChart resources.
Separate worker pools prepare charts, update schemas, and validate values. A
chart enters its next pool as soon as its current task completes.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import yaml

IGNORED_CHARTS = {"generic"}

HELMCHART_API_VERSION = "helm.cattle.io/v1"
HELMCHART_KIND = "HelmChart"
HELMCHART_SCHEMA_NAME = "helmchart.schema.json"
VALUES_OVERRIDE_SCHEMA_NAME = "values.override.schema.json"
SCHEMA_STATE_NAME = ".helm-schema-state.json"
# Increment this when this script changes how it derives schemas.
SCHEMA_STATE_VERSION = 1
GENERATED_SCHEMA_NAMES = {
    "values.schema.json",
    VALUES_OVERRIDE_SCHEMA_NAME,
    HELMCHART_SCHEMA_NAME,
}
COMMAND_TIMEOUT_SECONDS = 120

JsonSchema = dict[str, object] | bool

# These keywords contain schemas rather than ordinary JSON data. Restricting
# recursion to these locations prevents changes to examples and default values.
SCHEMA_MAP_KEYWORDS = ("$defs", "definitions", "dependentSchemas")
SCHEMA_LIST_KEYWORDS = ("allOf", "anyOf", "oneOf", "prefixItems")
SCHEMA_VALUE_KEYWORDS = (
    "additionalItems",
    "contains",
    "contentSchema",
    "else",
    "if",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
)


@dataclass(frozen=True)
class ChartReference:
    """Store one chart location and its requested version."""

    chart: str
    repository: str | None
    version: str

    @property
    def name(self) -> str:
        """Return the final chart name used for its cache directory."""
        return Path(self.chart).name


class CheckError(Exception):
    """A required schema or local validation command failed."""


@dataclass(frozen=True)
class HelmChartResource:
    """Store the identity and YAML data of one rendered HelmChart resource."""

    name: str
    reference: ChartReference
    document: dict[str, object]


@dataclass(frozen=True)
class ChartPreparation:
    """Describe whether the chart and its schemas came from their caches."""

    chart_status: str
    schema_status: str

    def __str__(self) -> str:
        """Return the concise status shown beside a completed chart."""
        return f"chart {self.chart_status}, schema {self.schema_status}"


class PipelineStage(Enum):
    """Name the independently scheduled stages of a chart pipeline."""

    PREPARE = auto()
    SCHEMA = auto()
    VALIDATE = auto()


@dataclass(frozen=True)
class PendingTask:
    """Identify the chart and optional resource owned by one pending future."""

    stage: PipelineStage
    reference: ChartReference
    resource: HelmChartResource | None = None


def repo_root() -> Path:
    """Return the repository root relative to this script's location."""
    return Path(__file__).resolve().parents[1]


def _sha256_file(path: Path) -> str:
    """Return a file's SHA-256 digest without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _helm_schema_binary() -> Path:
    """Resolve the executable used by the installed Helm schema plugin."""
    try:
        result = subprocess.run(
            ["helm", "env", "HELM_PLUGINS"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot locate Helm plugins: {exc}") from exc
    if result.returncode or not result.stdout.strip():
        diagnostic = result.stderr.strip() or "HELM_PLUGINS is empty"
        raise RuntimeError(f"cannot locate Helm plugins: {diagnostic}")

    plugins_dir = Path(result.stdout.strip())
    plugin_dir: Path | None = None
    manifest: dict[str, object] | None = None
    manifest_path: Path | None = None
    for candidate in sorted(plugins_dir.glob("*/plugin.yaml")):
        try:
            candidate_manifest = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(candidate_manifest, dict) and candidate_manifest.get("name") == "schema":
            plugin_dir = candidate.parent
            manifest = candidate_manifest
            manifest_path = candidate
            break
    if plugin_dir is None or manifest is None or manifest_path is None:
        raise RuntimeError(f"the Helm schema plugin is not installed in {plugins_dir}")

    runtime_config = manifest.get("runtimeConfig")
    commands = runtime_config.get("platformCommand") if isinstance(runtime_config, dict) else None
    if not isinstance(commands, list):
        raise RuntimeError(f"{manifest_path} has no runtime platform command")

    platform_name = "windows" if os.name == "nt" else "darwin" if sys.platform == "darwin" else "linux"
    platform_commands = [entry for entry in commands if isinstance(entry, dict) and entry.get("os") == platform_name]
    generic_commands = [entry for entry in commands if isinstance(entry, dict) and "os" not in entry]
    selected = next(iter(platform_commands or generic_commands), None)
    command = selected.get("command") if isinstance(selected, dict) else None
    if not isinstance(command, str):
        raise RuntimeError(f"{manifest_path} has no command for {platform_name}")

    expanded_command = command.replace("${HELM_PLUGIN_DIR}", str(plugin_dir)).replace(
        "$HELM_PLUGIN_DIR",
        str(plugin_dir),
    )
    command_parts = shlex.split(expanded_command, posix=os.name != "nt")
    if len(command_parts) != 1:
        raise RuntimeError(f"cannot identify the helm-schema executable from {command!r}")

    binary = Path(command_parts[0])
    if not binary.is_absolute():
        binary = plugin_dir / binary
    binary = binary.resolve()
    if not binary.is_file():
        raise RuntimeError(f"the helm-schema executable is missing: {binary}")
    return binary


def _chart_schema_input_digest(chart_dir: Path) -> str:
    """Hash every chart input that can affect generated schemas."""
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in chart_dir.rglob("*") if candidate.is_file()):
        if path.name == SCHEMA_STATE_NAME or path.name in GENERATED_SCHEMA_NAMES:
            continue
        digest.update(path.relative_to(chart_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _schema_output_paths(chart_dir: Path) -> set[Path]:
    """Return every generated schema expected below one chart directory."""
    return {
        *chart_dir.rglob("values.schema.json"),
        chart_dir / "values.schema.json",
        chart_dir / VALUES_OVERRIDE_SCHEMA_NAME,
        chart_dir / HELMCHART_SCHEMA_NAME,
    }


def _schema_output_digests(chart_dir: Path) -> dict[str, str]:
    """Return the relative path and digest of every generated schema."""
    output_paths = _schema_output_paths(chart_dir)
    missing = [path for path in output_paths if not path.is_file()]
    if missing:
        names = ", ".join(sorted(path.relative_to(chart_dir).as_posix() for path in missing))
        raise RuntimeError(f"generated schema files are missing: {names}")
    return {path.relative_to(chart_dir).as_posix(): _sha256_file(path) for path in sorted(output_paths)}


def _schema_state_is_current(
    chart_dir: Path,
    chart_version: str,
    chart_digest: str,
    generator_digest: str,
) -> bool:
    """Return whether the saved schema inputs and outputs still match."""
    try:
        state = json.loads((chart_dir / SCHEMA_STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(state, dict):
        return False
    if state.get("version") != SCHEMA_STATE_VERSION:
        return False
    if state.get("chartVersion") != chart_version:
        return False
    if state.get("chartSha256") != chart_digest:
        return False
    if state.get("generatorSha256") != generator_digest:
        return False

    outputs = state.get("outputs")
    if not isinstance(outputs, dict):
        return False
    current_paths = {path.relative_to(chart_dir).as_posix() for path in _schema_output_paths(chart_dir)}
    if set(outputs) != current_paths:
        return False

    for relative_path, expected_digest in outputs.items():
        if not isinstance(relative_path, str) or not isinstance(expected_digest, str):
            return False
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            return False
        path = chart_dir / relative
        try:
            if not path.is_file() or _sha256_file(path) != expected_digest:
                return False
        except OSError:
            return False
    return True


def refresh_schema(chart_dir: Path) -> None:
    """Generate schemas for one chart and its unpacked dependencies."""
    try:
        subprocess.run(
            ["helm", "schema", str(chart_dir)],
            capture_output=True,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"schema generation timed out for {chart_dir.name}") from exc
    except subprocess.CalledProcessError as exc:
        diagnostic = exc.stderr.strip() or exc.stdout.strip() or f"exit status {exc.returncode}"
        raise RuntimeError(f"schema generation failed for {chart_dir.name}: {diagnostic}") from exc


def prepare_chart(
    reference: ChartReference,
    output_dir: Path,
    generator_digest: str,
) -> ChartPreparation:
    """Prepare one chart and report whether its schemas need an update.

    A chart is current only when its version, source digest, generator digest,
    generated paths, and generated file digests match the saved state.
    """
    final_dir = output_dir / reference.name
    chart_yaml = final_dir / "Chart.yaml"

    if chart_yaml.is_file():
        match = re.search(
            r"^version:\s*(.+)$",
            chart_yaml.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if match and match.group(1).strip().strip("\"'") == reference.version:
            chart_digest = _chart_schema_input_digest(final_dir)
            if _schema_state_is_current(
                final_dir,
                reference.version,
                chart_digest,
                generator_digest,
            ):
                return ChartPreparation("cached", "cached")
            return ChartPreparation("cached", "update required")

    if reference.chart.startswith("oci://"):
        command = [
            "helm",
            "pull",
            reference.chart,
            "--version",
            reference.version,
            "--untar",
            "--untardir",
            str(output_dir),
        ]
    else:
        if not reference.repository:
            raise RuntimeError(f"cannot pull {reference.chart}: its HelmChart has no repository")
        command = [
            "helm",
            "pull",
            reference.chart,
            "--repo",
            reference.repository,
            "--version",
            reference.version,
            "--untar",
            "--untardir",
            str(output_dir),
        ]

    if final_dir.exists():
        shutil.rmtree(final_dir)
    try:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"timed out while pulling {reference.chart}") from None
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"failed to pull {reference.chart}: {exc.stderr.strip()}") from exc

    return ChartPreparation("downloaded", "update required")


def _reference_sort_key(reference: ChartReference) -> tuple[str, str, str]:
    """Return a stable sort key that supports a missing repository value."""
    return reference.chart, reference.repository or "", reference.version


def _chart_references_by_name(
    chart_references: set[ChartReference],
) -> dict[str, ChartReference]:
    """Index chart references by cache name and reject cache collisions.

    Two different references with the same final path component use the same
    output directory. Rejecting that case prevents concurrent workers from
    replacing each other's files.

    Raises:
        RuntimeError: Two distinct references use the same chart name.
    """
    references_by_name: dict[str, ChartReference] = {}
    for reference in sorted(chart_references, key=_reference_sort_key):
        previous = references_by_name.setdefault(reference.name, reference)
        if previous != reference:
            raise RuntimeError(f"chart directory {reference.name} is requested by both {previous!r} and {reference!r}")
    return references_by_name


def _helmchart_schema(chart_name: str, version: str) -> dict:
    """Return a resource schema for one chart's HelmChart objects.

    The schema validates the chart reference and version before applying the
    adjacent override schema to ``spec.values``. Keeping one resource schema
    per chart prevents validators from compiling every upstream schema for
    each HelmChart object.
    """
    escaped_name = re.escape(chart_name)
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": f"{chart_name} HelmChart",
        "type": "object",
        "properties": {
            "apiVersion": {"const": HELMCHART_API_VERSION},
            "kind": {"const": HELMCHART_KIND},
            "spec": {
                "type": "object",
                "properties": {
                    "chart": {"type": "string", "pattern": rf"(^|/){escaped_name}$"},
                    "version": {"const": version},
                    "values": {"$ref": VALUES_OVERRIDE_SCHEMA_NAME},
                },
                "required": ["chart", "version"],
            },
        },
        "required": ["apiVersion", "kind", "spec"],
    }


def _allows_null(schema: JsonSchema) -> bool:
    """Return True if a JSON Schema accepts null without conversion."""
    if schema is True or schema == {}:
        return True
    if schema is False:
        return False

    schema_type = schema.get("type")
    if schema_type == "null":
        return True
    if isinstance(schema_type, list) and "null" in schema_type:
        return True

    enum = schema.get("enum")
    if isinstance(enum, list) and None in enum:
        return True
    if "const" in schema and schema["const"] is None:
        return True

    for keyword in ("anyOf", "oneOf"):
        options = schema.get(keyword)
        if isinstance(options, list) and any(
            isinstance(option, (bool, dict)) and _allows_null(option) for option in options
        ):
            return True
    return False


def _nullable(schema: JsonSchema) -> JsonSchema:
    """Return the schema with support for a null Helm map override."""
    if _allows_null(schema):
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _convert_schema(value: object) -> object:
    """Convert a child value only when it is a JSON Schema."""
    if isinstance(value, (bool, dict)):
        return _values_override_schema(value)
    return value


def _convert_property_schema(value: object) -> object:
    """Convert a property schema and permit a null map override."""
    converted = _convert_schema(value)
    if isinstance(converted, (bool, dict)):
        return _nullable(converted)
    return converted


def _values_override_schema(schema: JsonSchema) -> JsonSchema:
    """Convert a full-values schema into a schema for Helm value overrides.

    Helm treats a null map value as removal of that key during value merging.
    A full-values schema describes the result of that merge. Thus, its normal
    type constraints reject a valid null override.

    This function permits null for map members and keeps all other constraints.
    It does not permit null array elements because Helm replaces whole arrays.
    """
    if isinstance(schema, bool):
        return schema

    converted = schema.copy()
    for keyword in ("properties", "patternProperties"):
        schemas = converted.get(keyword)
        if isinstance(schemas, dict):
            converted[keyword] = {name: _convert_property_schema(child) for name, child in schemas.items()}

    for keyword in (*SCHEMA_MAP_KEYWORDS, "dependencies"):
        schemas = converted.get(keyword)
        if isinstance(schemas, dict):
            converted[keyword] = {name: _convert_schema(child) for name, child in schemas.items()}

    for keyword in SCHEMA_LIST_KEYWORDS:
        schemas = converted.get(keyword)
        if isinstance(schemas, list):
            converted[keyword] = [_convert_schema(child) for child in schemas]

    for keyword in SCHEMA_VALUE_KEYWORDS:
        if keyword in converted:
            converted[keyword] = _convert_schema(converted[keyword])

    if "items" in converted:
        items = converted["items"]
        converted["items"] = (
            [_convert_schema(child) for child in items] if isinstance(items, list) else _convert_schema(items)
        )

    additional_properties = converted.get("additionalProperties")
    if isinstance(additional_properties, dict):
        converted["additionalProperties"] = _convert_property_schema(additional_properties)
    return converted


def _write_json(path: Path, value: object) -> None:
    """Write compact, deterministic JSON and a final newline."""
    path.write_text(
        json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_schema_state(
    chart_dir: Path,
    chart_version: str,
    generator_digest: str,
) -> None:
    """Record the inputs and outputs of successful schema generation."""
    _write_json(
        chart_dir / SCHEMA_STATE_NAME,
        {
            "version": SCHEMA_STATE_VERSION,
            "chartVersion": chart_version,
            "chartSha256": _chart_schema_input_digest(chart_dir),
            "generatorSha256": generator_digest,
            "outputs": _schema_output_digests(chart_dir),
        },
    )


def generate_helmchart_schemas(
    reference: ChartReference,
    chart_dir: Path,
    generator_digest: str,
) -> None:
    """Write override and HelmChart schemas beside one full-values schema."""
    values_schema_path = chart_dir / "values.schema.json"
    if not values_schema_path.is_file():
        raise RuntimeError(f"generated schema is missing for {reference.name}")

    try:
        values_schema = json.loads(values_schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read generated schema for {reference.name}: {exc}") from exc
    if not isinstance(values_schema, (bool, dict)):
        raise RuntimeError(f"generated schema for {reference.name} is not a JSON Schema object")

    _write_json(
        chart_dir / VALUES_OVERRIDE_SCHEMA_NAME,
        _values_override_schema(values_schema),
    )
    _write_json(
        chart_dir / HELMCHART_SCHEMA_NAME,
        _helmchart_schema(reference.name, reference.version),
    )
    _write_schema_state(chart_dir, reference.version, generator_digest)


def render_resources(root: Path) -> list[HelmChartResource]:
    """Render the services chart and return its supported HelmChart resources.

    The generic chart accepts arbitrary Kubernetes objects. It has no upstream
    values schema, so this script does not validate its values.
    """
    try:
        result = subprocess.run(
            ["helm", "template", "charts/services"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise CheckError("helm template timed out") from exc
    if result.returncode:
        raise CheckError(f"helm template failed:\n{result.stderr.strip()}")

    resources: list[HelmChartResource] = []
    for document in yaml.safe_load_all(result.stdout):
        if not isinstance(document, dict) or document.get("kind") != "HelmChart":
            continue

        spec = document.get("spec")
        if not isinstance(spec, dict):
            raise CheckError("a rendered HelmChart resource has no object spec")

        chart = spec.get("chart")
        if not isinstance(chart, str) or not chart:
            raise CheckError("a rendered HelmChart resource has no chart name")
        repository = spec.get("repo")
        if repository is not None and not isinstance(repository, str):
            raise CheckError(f"HelmChart for {chart} has a non-string repository")
        version = spec.get("version")
        if not isinstance(version, (str, int, float)) or not str(version):
            raise CheckError(f"HelmChart for {chart} has no version")

        reference = ChartReference(chart, repository, str(version))
        if reference.name in IGNORED_CHARTS:
            continue

        metadata = document.get("metadata")
        name = metadata.get("name", "?") if isinstance(metadata, dict) else "?"
        resources.append(
            HelmChartResource(
                name=str(name),
                reference=reference,
                document=document,
            )
        )

    if not resources:
        raise CheckError("the rendered services chart has no supported HelmChart resources")
    return resources


def chart_schema_path(chart_dir: Path, reference: ChartReference) -> Path:
    """Return the prepared HelmChart schema after checking its chart."""
    chart_file = chart_dir / "Chart.yaml"
    if not chart_file.is_file():
        raise CheckError(f"chart {reference.name} is missing after preparation")

    chart = yaml.safe_load(chart_file.read_text(encoding="utf-8"))
    if not isinstance(chart, dict):
        raise CheckError(f"{chart_file} does not contain an object")

    cached_version = str(chart.get("version", ""))
    if cached_version != reference.version:
        raise CheckError(
            f"chart {reference.name} is version {cached_version}, " f"but the HelmChart requests {reference.version}"
        )

    for schema_name in (
        "values.schema.json",
        VALUES_OVERRIDE_SCHEMA_NAME,
        HELMCHART_SCHEMA_NAME,
    ):
        schema_path = chart_dir / schema_name
        if not schema_path.is_file():
            raise CheckError(f"{schema_name} is missing for {reference.name}")
    return chart_dir / HELMCHART_SCHEMA_NAME


def validate_resource(resource: HelmChartResource, schema_path: Path) -> str | None:
    """Validate one HelmChart resource and return a diagnostic on failure."""
    try:
        result = subprocess.run(
            [
                "kubeconform",
                "-strict",
                "-schema-location",
                schema_path.resolve().as_uri(),
            ],
            input=yaml.safe_dump(resource.document, sort_keys=False),
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"validation exceeded {COMMAND_TIMEOUT_SECONDS}s"

    if not result.returncode:
        return None
    return "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())


def _validation_failure(resource: HelmChartResource, diagnostic: object) -> str | None:
    """Format a failed validation result and ignore a successful one."""
    if diagnostic is None:
        return None
    if isinstance(diagnostic, BaseException):
        diagnostic = f"unexpected validation error: {diagnostic}"
    return f"{resource.name}:\n{diagnostic}"


def update_chart_schema(
    reference: ChartReference,
    output_dir: Path,
    generator_digest: str,
) -> Path:
    """Regenerate one chart's schemas and return its resource schema."""
    chart_dir = output_dir / reference.name
    refresh_schema(chart_dir)
    generate_helmchart_schemas(reference, chart_dir, generator_digest)
    return chart_schema_path(chart_dir, reference)


def _resources_by_reference(
    resources: list[HelmChartResource],
) -> dict[ChartReference, list[HelmChartResource]]:
    """Group rendered resources by the exact chart package they use."""
    grouped: dict[ChartReference, list[HelmChartResource]] = {}
    for resource in resources:
        grouped.setdefault(resource.reference, []).append(resource)
    _chart_references_by_name(set(grouped))
    return grouped


def _validation_tasks(
    executor: concurrent.futures.ThreadPoolExecutor,
    reference: ChartReference,
    resources: list[HelmChartResource],
    schema_path: Path,
) -> dict[concurrent.futures.Future, PendingTask]:
    """Submit one validation future per resource that uses a chart."""
    return {
        executor.submit(validate_resource, resource, schema_path): PendingTask(
            PipelineStage.VALIDATE,
            reference,
            resource,
        )
        for resource in resources
    }


def check_charts(
    resources: list[HelmChartResource],
    output_dir: Path,
    generator_digest: str,
) -> list[str]:
    """Pass charts through task-specific pools without waiting between stages.

    Chart preparation uses one worker per distinct chart because cache reads
    and downloads are I/O-heavy. Schema generation and validation each use a
    CPU-sized pool because both run local analysis processes.
    """
    grouped = _resources_by_reference(resources)
    chart_count = len(grouped)
    cpu_workers = min(os.process_cpu_count() or 1, chart_count)
    validation_workers = min(cpu_workers, len(resources))
    print(f"Checking {chart_count} charts...", flush=True)

    failures: list[str] = []
    chart_failures: dict[ChartReference, list[str]] = {reference: [] for reference in grouped}
    preparations: dict[ChartReference, ChartPreparation] = {}
    remaining_validations: dict[ChartReference, int] = {}
    completed = 0

    def complete_chart(reference: ChartReference) -> None:
        """Record and print the final result for one chart."""
        nonlocal completed
        completed += 1
        current_failures = chart_failures[reference]
        failures.extend(current_failures)
        outcome = "FAIL" if current_failures else "PASS"
        preparation = preparations.get(reference)
        detail = f" ({preparation})" if preparation else ""
        print(
            f"[{completed}/{chart_count}] {reference.name}: {outcome}{detail}",
            flush=True,
        )

    with (
        concurrent.futures.ThreadPoolExecutor(
            max_workers=chart_count,
            thread_name_prefix="chart",
        ) as chart_executor,
        concurrent.futures.ThreadPoolExecutor(
            max_workers=cpu_workers,
            thread_name_prefix="schema",
        ) as schema_executor,
        concurrent.futures.ThreadPoolExecutor(
            max_workers=validation_workers,
            thread_name_prefix="validation",
        ) as validation_executor,
    ):
        pending: dict[concurrent.futures.Future, PendingTask] = {
            chart_executor.submit(
                prepare_chart,
                reference,
                output_dir,
                generator_digest,
            ): PendingTask(PipelineStage.PREPARE, reference)
            for reference in sorted(grouped, key=_reference_sort_key)
        }

        while pending:
            done, _ = concurrent.futures.wait(
                pending,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                task = pending.pop(future)
                reference = task.reference

                if task.stage is PipelineStage.PREPARE:
                    try:
                        preparation = future.result()
                        preparations[reference] = preparation
                        if preparation.schema_status == "update required":
                            schema_future = schema_executor.submit(
                                update_chart_schema,
                                reference,
                                output_dir,
                                generator_digest,
                            )
                            pending[schema_future] = PendingTask(PipelineStage.SCHEMA, reference)
                            continue

                        schema_path = chart_schema_path(output_dir / reference.name, reference)
                    except Exception as exc:
                        chart_failures[reference].append(f"{reference.name}: {exc}")
                        complete_chart(reference)
                        continue

                    remaining_validations[reference] = len(grouped[reference])
                    pending.update(
                        _validation_tasks(
                            validation_executor,
                            reference,
                            grouped[reference],
                            schema_path,
                        )
                    )
                    continue

                if task.stage is PipelineStage.SCHEMA:
                    try:
                        schema_path = future.result()
                    except Exception as exc:
                        preparation = preparations[reference]
                        preparations[reference] = ChartPreparation(
                            preparation.chart_status,
                            "update failed",
                        )
                        chart_failures[reference].append(f"{reference.name}: {exc}")
                        complete_chart(reference)
                        continue

                    preparation = preparations[reference]
                    preparations[reference] = ChartPreparation(
                        preparation.chart_status,
                        "updated",
                    )
                    remaining_validations[reference] = len(grouped[reference])
                    pending.update(
                        _validation_tasks(
                            validation_executor,
                            reference,
                            grouped[reference],
                            schema_path,
                        )
                    )
                    continue

                resource = task.resource
                diagnostic = (
                    "validation task has no HelmChart resource"
                    if resource is None
                    else future.exception() or future.result()
                )
                if resource is not None and (failure := _validation_failure(resource, diagnostic)):
                    chart_failures[reference].append(failure)
                elif diagnostic:
                    chart_failures[reference].append(f"{reference.name}: {diagnostic}")

                remaining_validations[reference] -= 1
                if not remaining_validations[reference]:
                    complete_chart(reference)

    return failures


def main() -> int:
    """Render HelmCharts and run each through its independent pipeline."""
    root = repo_root()
    try:
        output_dir = root / "charts" / "services" / "upstream-charts"
        output_dir.mkdir(parents=True, exist_ok=True)
        generator_digest = _sha256_file(_helm_schema_binary())
        resources = render_resources(root)
        failures = check_charts(resources, output_dir, generator_digest)
    except (CheckError, RuntimeError, OSError, yaml.YAMLError) as exc:
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
