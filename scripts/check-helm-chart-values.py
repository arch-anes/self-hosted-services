#!/usr/bin/env python3
"""Validate every HelmChart reachable from the services chart.

The services chart is rendered once to find its HelmChart resources. Each
resource independently passes through this iterative pipeline:

1. Prepare its chart package and generated override schema.
2. Validate its ``spec.values`` against that override schema.
3. Render the chart with those exact values and offline CRD capabilities.
4. Validate the rendered Kubernetes objects with Kubeconform.
5. Add HelmCharts found in rendered output to the same work queue.

Successful value validation and rendered nested-chart discovery are cached by
their relevant inputs. Cached rendered output never bypasses validation after a
chart, value, validator, or available CRD API changes.
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
import tarfile
from collections.abc import Iterable
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
RENDER_CACHE_VERSION = 2
VALUE_CACHE_VERSION = 1
CRD_CACHE_VERSION = 1
# Increment this when this script changes how it derives schemas.
SCHEMA_STATE_VERSION = 1
GENERATED_SCHEMA_NAMES = {
    "values.schema.json",
    VALUES_OVERRIDE_SCHEMA_NAME,
    HELMCHART_SCHEMA_NAME,
}
COMMAND_TIMEOUT_SECONDS = 120
KUBERNETES_VERSION = "1.36.0"
KUBECONFORM_SCHEMA_LOCATIONS = (
    "default",
    "https://raw.githubusercontent.com/abelfodil/CRDs-catalog/helmchart/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json",
)

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


SafeYamlLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class HelmYamlLoader(SafeYamlLoader):
    """Load Helm output safely, using libyaml when the installed PyYAML has it."""


def _construct_yaml_value(
    loader: HelmYamlLoader,
    node: yaml.nodes.ScalarNode,
) -> str:
    """Treat the bare equals marker emitted by some charts as text."""
    return loader.construct_scalar(node)


HelmYamlLoader.add_constructor("tag:yaml.org,2002:value", _construct_yaml_value)


RenderKey = tuple[ChartReference, str, str]


@dataclass(frozen=True)
class HelmChartResource:
    """Store one HelmChart and the chart-render path that produced it."""

    name: str
    target_namespace: str
    reference: ChartReference
    values: dict[str, object]
    document: dict[str, object]
    ancestry: tuple[str, ...]
    lineage: frozenset[RenderKey]

    @property
    def label(self) -> str:
        """Return a readable path from the services chart to this resource."""
        return " -> ".join(self.ancestry)

    @property
    def render_key(self) -> RenderKey:
        """Return the inputs that determine the relevant chart behavior."""
        values = yaml.safe_dump(self.values, sort_keys=True)
        return self.reference, self.target_namespace, values

    @property
    def identity(self) -> str:
        """Return stable content used to remove duplicate discoveries."""
        return yaml.safe_dump(self.document, sort_keys=True)


@dataclass(frozen=True)
class ChartPreparation:
    """Describe whether the chart and its schemas came from their caches."""

    chart_status: str
    schema_status: str
    source_digest: str
    render_digest: str

    def __str__(self) -> str:
        """Return the concise status shown beside a completed chart."""
        return f"chart {self.chart_status}, schema {self.schema_status}"


class PipelineStage(Enum):
    """Name the independently scheduled package, value, render, and manifest stages."""

    PREPARE = auto()
    SCHEMA = auto()
    VALIDATE = auto()
    RENDER = auto()
    MANIFEST = auto()


@dataclass(frozen=True)
class PendingTask:
    """Keep the resource and cache data needed when one worker future finishes."""

    stage: PipelineStage
    reference: ChartReference
    resource: HelmChartResource | None = None
    value_cache_path: Path | None = None
    render_cache_path: Path | None = None
    nested_resources: tuple[HelmChartResource, ...] = ()


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


def _executable_digest(command: str) -> str:
    """Return the digest of an executable resolved from the current PATH."""
    executable = shutil.which(command)
    if not executable:
        raise RuntimeError(f"required executable is missing: {command}")
    return _sha256_file(Path(executable).resolve())


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


def _chart_input_digests(chart_dir: Path) -> tuple[str, str]:
    """Hash schema and render inputs in one pass over a chart.

    Helm may unpack ``charts/name-version.tgz`` into ``charts/name`` while it
    renders. Both locations contain the same dependency. Hashing the archive
    and its extracted copy would make a cache miss after an otherwise unchanged
    render. A locally unpacked dependency without its archive remains part of
    the digest.
    """
    charts_dir = chart_dir / "charts"
    packaged_dependencies = (
        {
            directory.name
            for directory in charts_dir.iterdir()
            if directory.is_dir()
            and any(archive.name.startswith(f"{directory.name}-") for archive in charts_dir.glob("*.tgz"))
        }
        if charts_dir.is_dir()
        else set()
    )

    schema_digest = hashlib.sha256()
    render_digest = hashlib.sha256()
    for path in sorted(candidate for candidate in chart_dir.rglob("*") if candidate.is_file()):
        relative = path.relative_to(chart_dir)
        if path.name == SCHEMA_STATE_NAME or path.name in GENERATED_SCHEMA_NAMES:
            continue
        relative_name = relative.as_posix().encode()
        file_digest = _sha256_file(path).encode()
        schema_digest.update(relative_name)
        schema_digest.update(b"\0")
        schema_digest.update(file_digest)
        schema_digest.update(b"\0")
        if len(relative.parts) > 1 and relative.parts[0] == "charts" and relative.parts[1] in packaged_dependencies:
            continue
        render_digest.update(relative_name)
        render_digest.update(b"\0")
        render_digest.update(file_digest)
        render_digest.update(b"\0")
    return schema_digest.hexdigest(), render_digest.hexdigest()


def _chart_schema_input_digest(chart_dir: Path) -> str:
    """Hash every chart input that can affect generated schemas."""
    return _chart_input_digests(chart_dir)[0]


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
            chart_digest, render_digest = _chart_input_digests(final_dir)
            if _schema_state_is_current(
                final_dir,
                reference.version,
                chart_digest,
                generator_digest,
            ):
                return ChartPreparation(
                    "cached",
                    "cached",
                    chart_digest,
                    render_digest,
                )
            return ChartPreparation(
                "cached",
                "update required",
                chart_digest,
                render_digest,
            )

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

    chart_digest, render_digest = _chart_input_digests(final_dir)
    return ChartPreparation(
        "downloaded",
        "update required",
        chart_digest,
        render_digest,
    )


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


def _parse_helmchart(
    document: dict[str, object],
    parent: HelmChartResource | None,
) -> HelmChartResource | None:
    """Parse one HelmChart object and reject value sources we cannot reproduce."""
    if document.get("apiVersion") != HELMCHART_API_VERSION or document.get("kind") != HELMCHART_KIND:
        return None

    spec = document.get("spec")
    if not isinstance(spec, dict):
        raise CheckError("a rendered HelmChart resource has no object spec")

    chart = spec.get("chart")
    version = spec.get("version")
    if not isinstance(chart, str) or not chart:
        raise CheckError("a rendered HelmChart resource has no chart name")
    if not isinstance(version, (str, int, float)) or not str(version):
        raise CheckError(f"HelmChart for {chart} has no version")

    repository = spec.get("repo")
    if repository is not None and not isinstance(repository, str):
        raise CheckError(f"HelmChart for {chart} has a non-string repository")

    for field in ("set", "valuesContent", "valuesSecrets"):
        if spec.get(field) not in (None, "", [], {}):
            raise CheckError(f"HelmChart for {chart} uses unsupported spec.{field}")

    values = spec.get("values") or {}
    if not isinstance(values, dict):
        raise CheckError(f"HelmChart for {chart} has non-object spec.values")

    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise CheckError(f"HelmChart for {chart} has no object metadata")
    name = metadata.get("name")
    if not isinstance(name, str) or not name:
        raise CheckError(f"HelmChart for {chart} has no metadata.name")

    namespace = metadata.get("namespace", "default")
    target_namespace = spec.get("targetNamespace", namespace)
    if not isinstance(target_namespace, str) or not target_namespace:
        raise CheckError(f"HelmChart {name} has an invalid target namespace")

    ancestry = (*parent.ancestry, name) if parent else (name,)
    lineage = parent.lineage | {parent.render_key} if parent else frozenset()
    return HelmChartResource(
        name=name,
        target_namespace=target_namespace,
        reference=ChartReference(chart, repository, str(version)),
        values=values,
        document=document,
        ancestry=ancestry,
        lineage=lineage,
    )


def _helmcharts_from_object(
    value: object,
    parent: HelmChartResource | None = None,
) -> list[HelmChartResource]:
    """Find every HelmChart in an object tree using an explicit work stack."""
    resources: list[HelmChartResource] = []
    pending: list[tuple[object, HelmChartResource | None]] = [(value, parent)]
    while pending:
        current, current_parent = pending.pop()
        if isinstance(current, list):
            pending.extend((child, current_parent) for child in reversed(current))
            continue
        if not isinstance(current, dict):
            continue

        resource = _parse_helmchart(current, current_parent)
        child_parent = resource or current_parent
        if resource and resource.reference.name not in IGNORED_CHARTS:
            resources.append(resource)
        pending.extend((child, child_parent) for child in reversed(tuple(current.values())))
    return resources


def _helmcharts_from_yaml(
    rendered_yaml: str,
    parent: HelmChartResource | None = None,
) -> list[HelmChartResource]:
    """Find every HelmChart in a rendered YAML stream."""
    if HELMCHART_KIND not in rendered_yaml:
        return []
    return [
        resource
        for document in yaml.load_all(rendered_yaml, Loader=HelmYamlLoader)
        for resource in _helmcharts_from_object(document, parent)
    ]


def render_resources(root: Path) -> list[HelmChartResource]:
    """Render the services chart and find direct and embedded HelmCharts."""
    try:
        result = subprocess.run(
            ["helm", "template", "charts/services", "--kube-version", KUBERNETES_VERSION],
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

    resources = _helmcharts_from_yaml(result.stdout)
    if not resources:
        raise CheckError("the rendered services chart has no supported HelmChart resources")
    return resources


def _crd_files(chart_dir: Path) -> Iterable[Path]:
    """Yield YAML files from CRD directories in a chart and its dependencies."""
    for path in chart_dir.rglob("*"):
        if not path.is_file() or path.suffix not in {".yaml", ".yml"}:
            continue
        parts = path.relative_to(chart_dir).parts
        is_chart_crd = parts[0] == "crds" or any(
            part == "crds" and (index == 1 or index >= 2 and parts[index - 2] == "charts")
            for index, part in enumerate(parts)
        )
        if is_chart_crd:
            yield path


def _crd_documents(chart_dirs: Iterable[Path]) -> Iterable[object]:
    """Yield CRD documents from unpacked charts and packaged dependencies."""
    for chart_dir in chart_dirs:
        for path in _crd_files(chart_dir):
            try:
                yield from yaml.load_all(path.read_text(encoding="utf-8"), Loader=HelmYamlLoader)
            except (OSError, yaml.YAMLError):
                continue

        for archive_path in chart_dir.rglob("*.tgz"):
            try:
                with tarfile.open(archive_path, "r:gz") as archive:
                    for member in archive:
                        member_path = Path(member.name)
                        if (
                            not member.isfile()
                            or member_path.suffix not in {".yaml", ".yml"}
                            or "crds" not in member_path.parts
                        ):
                            continue
                        source = archive.extractfile(member)
                        if source is not None:
                            yield from yaml.load_all(source.read().decode(), Loader=HelmYamlLoader)
            except (OSError, tarfile.TarError, UnicodeDecodeError, yaml.YAMLError):
                continue


def _crd_api_versions(chart_dirs: Iterable[Path]) -> set[str]:
    """Return the API identifiers supplied by locally available chart CRDs.

    Offline Helm rendering cannot ask a cluster which CRDs exist. Passing these
    identifiers to Helm lets capability checks reflect the packages this
    repository will install. Helm accepts both ``group/version`` and
    ``group/version/kind`` forms.
    """
    api_versions: set[str] = set()
    for document in _crd_documents(chart_dirs):
        if not isinstance(document, dict) or document.get("kind") != "CustomResourceDefinition":
            continue
        spec = document.get("spec")
        if not isinstance(spec, dict):
            continue
        names = spec.get("names")
        group = spec.get("group")
        kind = names.get("kind") if isinstance(names, dict) else None
        if not isinstance(group, str) or not isinstance(kind, str):
            continue
        versions = spec.get("versions")
        for version in versions if isinstance(versions, list) else []:
            name = version.get("name") if isinstance(version, dict) else None
            if isinstance(name, str) and version.get("served") is not False:
                api_versions.update({f"{group}/{name}", f"{group}/{name}/{kind}"})
        legacy_version = spec.get("version")
        if isinstance(legacy_version, str):
            api_versions.update({f"{group}/{legacy_version}", f"{group}/{legacy_version}/{kind}"})
    return api_versions


def _crd_source_digest(chart_dir: Path) -> str:
    """Hash the files that can supply offline Helm CRD capabilities."""
    paths = {*_crd_files(chart_dir), *chart_dir.rglob("*.tgz")}
    digest = hashlib.sha256(str(CRD_CACHE_VERSION).encode())
    for path in sorted(paths):
        digest.update(path.relative_to(chart_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _cached_crd_api_versions(chart_dir: Path) -> set[str]:
    """Return CRD capabilities from a content-addressed cache when possible."""
    cache_path = _render_cache_dir().parent / "helm-crd-capabilities" / f"{_crd_source_digest(chart_dir)}.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            isinstance(cached, dict)
            and cached.get("version") == CRD_CACHE_VERSION
            and isinstance(cached.get("apiVersions"), list)
            and all(isinstance(value, str) for value in cached["apiVersions"])
        ):
            return set(cached["apiVersions"])
    except (OSError, json.JSONDecodeError):
        pass

    api_versions = _crd_api_versions([chart_dir])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        cache_path,
        {"version": CRD_CACHE_VERSION, "apiVersions": sorted(api_versions)},
    )
    return api_versions


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
    """Validate one HelmChart resource against its generated override schema."""
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


def render_chart(
    resource: HelmChartResource,
    chart_dir: Path,
    api_versions: tuple[str, ...],
) -> str:
    """Render one chart for the pinned Kubernetes version and local CRD APIs.

    The generated override schema was validated first, so Helm's full-values
    schema is skipped. That schema describes merged defaults, not a partial
    override, and can reject valid null map removals.
    """
    api_version_arguments = [argument for version in api_versions for argument in ("--api-versions", version)]
    try:
        result = subprocess.run(
            [
                "helm",
                "template",
                resource.name,
                str(chart_dir),
                "--namespace",
                resource.target_namespace,
                "--kube-version",
                KUBERNETES_VERSION,
                "--include-crds",
                "--skip-schema-validation",
                "--values",
                "-",
                *api_version_arguments,
            ],
            input=yaml.safe_dump(resource.values, sort_keys=False),
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"chart rendering exceeded {COMMAND_TIMEOUT_SECONDS}s") from exc

    if result.returncode:
        diagnostic = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"chart rendering failed:\n{diagnostic}")
    return result.stdout


def validate_manifests(rendered_yaml: str, cache_dir: Path) -> str | None:
    """Strictly validate Kubernetes objects produced by one rendered chart.

    Kubeconform uses the same pinned Kubernetes version, built-in schemas, and
    CRD catalog as CI. Resources absent from both schema sources are ignored
    because many upstream charts contain custom resources without published
    schemas. HelmChart resources are separately validated before charts render.
    """
    if not rendered_yaml.strip():
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        schema_arguments = [
            argument for location in KUBECONFORM_SCHEMA_LOCATIONS for argument in ("-schema-location", location)
        ]
        result = subprocess.run(
            [
                "kubeconform",
                "-strict",
                "-kubernetes-version",
                KUBERNETES_VERSION,
                "-cache",
                str(cache_dir),
                *schema_arguments,
                "-ignore-missing-schemas",
            ],
            input=rendered_yaml,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"manifest validation exceeded {COMMAND_TIMEOUT_SECONDS}s"

    if not result.returncode:
        return None
    return "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())


def _render_cache_path(
    cache_dir: Path,
    resource: HelmChartResource,
    chart_digest: str,
    tool_digest: str,
    api_versions: tuple[str, ...],
) -> Path:
    """Return the cache path for one fully validated rendered chart.

    The cache key covers every local input that can alter Helm rendering or
    Kubernetes validation. Its data records only HelmCharts discovered in the
    validated output, which lets cached runs continue nested discovery without
    retaining or reparsing full rendered manifests.
    """
    digest = hashlib.sha256()
    for value in (
        str(RENDER_CACHE_VERSION),
        chart_digest,
        tool_digest,
        "\n".join(api_versions),
        yaml.safe_dump(resource.document, sort_keys=True),
    ):
        digest.update(value.encode())
        digest.update(b"\0")
    return cache_dir / f"{digest.hexdigest()}.json"


def _render_cache_dir() -> Path:
    """Return the user cache directory for validated rendered-chart discovery."""
    cache_home = Path(os.environ["XDG_CACHE_HOME"]) if "XDG_CACHE_HOME" in os.environ else Path.home() / ".cache"
    return cache_home / "self-hosted-services" / "helm-renders"


def _value_cache_path(
    resource: HelmChartResource,
    schema_path: Path,
    kubeconform_digest: str,
) -> Path:
    """Return the cache path for a successful generated-schema validation."""
    digest = hashlib.sha256()
    for value in (
        str(VALUE_CACHE_VERSION),
        _sha256_file(schema_path),
        kubeconform_digest,
        yaml.safe_dump(resource.document, sort_keys=True),
    ):
        digest.update(value.encode())
        digest.update(b"\0")
    return _render_cache_dir().parent / "helm-value-validations" / f"{digest.hexdigest()}.ok"


def _serialize_render_key(key: RenderKey) -> dict[str, str | None]:
    """Convert one render-cycle key into JSON-compatible data."""
    reference, target_namespace, values = key
    return {
        "chart": reference.chart,
        "repository": reference.repository,
        "version": reference.version,
        "targetNamespace": target_namespace,
        "values": values,
    }


def _deserialize_render_key(value: object) -> RenderKey:
    """Read one render-cycle key stored in the rendered-output cache."""
    if not isinstance(value, dict):
        raise ValueError("render key is not an object")
    chart = value.get("chart")
    repository = value.get("repository")
    version = value.get("version")
    target_namespace = value.get("targetNamespace")
    values = value.get("values")
    if not isinstance(chart, str) or not isinstance(version, str):
        raise ValueError("render key has no chart reference")
    if repository is not None and not isinstance(repository, str):
        raise ValueError("render key has a non-string repository")
    if not isinstance(target_namespace, str) or not isinstance(values, str):
        raise ValueError("render key has invalid render inputs")
    return ChartReference(chart, repository, version), target_namespace, values


def _write_render_cache(path: Path, resources: tuple[HelmChartResource, ...]) -> None:
    """Save only the nested HelmCharts found in validated rendered output."""
    entries = [
        {
            "document": resource.document,
            "ancestry": list(resource.ancestry),
            "lineage": [_serialize_render_key(key) for key in resource.lineage],
        }
        for resource in resources
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, {"version": RENDER_CACHE_VERSION, "resources": entries})


def _read_render_cache(path: Path) -> list[HelmChartResource] | None:
    """Read cached nested HelmCharts, treating missing or invalid data as a miss."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != RENDER_CACHE_VERSION:
            return None
        entries = data.get("resources")
        if not isinstance(entries, list):
            return None

        resources: list[HelmChartResource] = []
        for entry in entries:
            if not isinstance(entry, dict):
                return None
            document = entry.get("document")
            ancestry = entry.get("ancestry")
            lineage = entry.get("lineage")
            if not isinstance(document, dict) or not isinstance(ancestry, list) or not isinstance(lineage, list):
                return None
            parsed = _parse_helmchart(document, None)
            if parsed is None or not all(isinstance(name, str) for name in ancestry):
                return None
            resources.append(
                HelmChartResource(
                    name=parsed.name,
                    target_namespace=parsed.target_namespace,
                    reference=parsed.reference,
                    values=parsed.values,
                    document=document,
                    ancestry=tuple(ancestry),
                    lineage=frozenset(_deserialize_render_key(key) for key in lineage),
                )
            )
        return resources
    except (OSError, ValueError, json.JSONDecodeError, CheckError):
        return None


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


@dataclass(frozen=True)
class CheckSummary:
    """Return the final result after all discovered HelmCharts finish."""

    failures: tuple[str, ...]
    resource_count: int


def check_charts(
    resources: list[HelmChartResource],
    output_dir: Path,
    generator_digest: str,
    manifest_tool_digest: str,
) -> CheckSummary:
    """Run dynamically discovered HelmCharts through independent worker pools.

    The coordinator owns all mutable state. Workers only prepare, render, or
    validate one input, which keeps concurrent work independent. Package
    preparation is shared by HelmCharts with the same exact reference.
    Rendering and manifest validation remain per resource because values and
    target namespaces can differ. A resource can add more work when its render
    contains nested HelmCharts; this uses the same queue rather than recursion.
    Rendering waits for all currently known chart packages to finish preparing
    so every chart in that group receives the same CRD capabilities.
    """
    initial_references = {resource.reference for resource in resources}
    _chart_references_by_name(initial_references)
    cpu_workers = max(1, os.process_cpu_count() or 1)
    api_versions = _cached_crd_api_versions(output_dir)
    print(f"Checking {len(resources)} HelmChart resources...", flush=True)

    failures: list[str] = []
    preparations: dict[ChartReference, ChartPreparation] = {}
    schemas: dict[ChartReference, Path] = {}
    reference_errors: dict[ChartReference, str] = {}
    waiting_resources: dict[ChartReference, list[HelmChartResource]] = {}
    references_by_name: dict[str, ChartReference] = {}
    started_references: set[ChartReference] = set()
    unprepared_references: set[ChartReference] = set()
    waiting_renders: list[HelmChartResource] = []
    seen_resources: set[str] = set()
    completed_resources: set[str] = set()
    completed = 0
    resource_count = 0

    def complete_resource(resource: HelmChartResource, diagnostic: str | None = None) -> None:
        """Print and retain the final result for one HelmChart resource once."""
        nonlocal completed
        if resource.identity in completed_resources:
            return
        completed_resources.add(resource.identity)
        completed += 1
        if diagnostic:
            failures.append(f"{resource.label}:\n{diagnostic}")
        outcome = "FAIL" if diagnostic else "PASS"
        preparation = preparations.get(resource.reference)
        detail = f" ({preparation})" if preparation else ""
        print(f"[{completed}/{resource_count}] {resource.label}: {outcome}{detail}", flush=True)

    def cached_nested_resources(resource: HelmChartResource) -> list[HelmChartResource] | None:
        """Return HelmCharts found in validated cached output for one resource."""
        preparation = preparations[resource.reference]
        api_version_list = tuple(sorted(api_versions))
        path = _render_cache_path(
            render_cache_dir,
            resource,
            preparation.render_digest,
            manifest_tool_digest,
            api_version_list,
        )
        return _read_render_cache(path)

    with (
        concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, len(initial_references)),
            thread_name_prefix="chart",
        ) as chart_executor,
        concurrent.futures.ThreadPoolExecutor(
            max_workers=cpu_workers,
            thread_name_prefix="schema",
        ) as schema_executor,
        concurrent.futures.ThreadPoolExecutor(
            max_workers=cpu_workers,
            thread_name_prefix="validation",
        ) as validation_executor,
        concurrent.futures.ThreadPoolExecutor(
            max_workers=cpu_workers,
            thread_name_prefix="render",
        ) as render_executor,
        concurrent.futures.ThreadPoolExecutor(
            max_workers=cpu_workers,
            thread_name_prefix="manifest",
        ) as manifest_executor,
    ):
        pending: dict[concurrent.futures.Future, PendingTask] = {}

        def schedule_render(resource: HelmChartResource) -> None:
            """Render a resource once all currently known packages are ready."""
            if unprepared_references:
                waiting_renders.append(resource)
                return

            api_version_list = tuple(sorted(api_versions))
            cache_path = _render_cache_path(
                render_cache_dir,
                resource,
                preparations[resource.reference].render_digest,
                manifest_tool_digest,
                api_version_list,
            )
            render_future = render_executor.submit(
                render_chart,
                resource,
                output_dir / resource.reference.name,
                api_version_list,
            )
            pending[render_future] = PendingTask(
                PipelineStage.RENDER,
                resource.reference,
                resource,
                render_cache_path=cache_path,
            )

        def schedule_waiting_renders() -> None:
            """Release renders after the known package capability set is stable."""
            if unprepared_references:
                return
            ready = waiting_renders.copy()
            waiting_renders.clear()
            for resource in ready:
                schedule_render(resource)

        def schedule_validation(resource: HelmChartResource) -> None:
            """Submit schema validation after its shared chart preparation ends."""
            schema_path = schemas.get(resource.reference)
            if schema_path is not None:
                cache_path = _value_cache_path(resource, schema_path, manifest_tool_digest)
                if cache_path.is_file():
                    future: concurrent.futures.Future[str | None] = concurrent.futures.Future()
                    future.set_result(None)
                else:
                    future = validation_executor.submit(validate_resource, resource, schema_path)
                pending[future] = PendingTask(
                    PipelineStage.VALIDATE,
                    resource.reference,
                    resource,
                    value_cache_path=cache_path,
                )
                return
            if error := reference_errors.get(resource.reference):
                complete_resource(resource, error)
                return
            waiting_resources.setdefault(resource.reference, []).append(resource)

        def schedule_waiting_resources(reference: ChartReference) -> None:
            """Move all resources waiting on one prepared package to validation."""
            for resource in waiting_resources.pop(reference, []):
                schedule_validation(resource)

        def fail_reference(reference: ChartReference, error: str) -> None:
            """Finish all waiting users of a package that could not be prepared."""
            reference_errors[reference] = error
            for resource in waiting_resources.pop(reference, []):
                complete_resource(resource, error)

        def add_resources(candidates: list[HelmChartResource]) -> None:
            """Register newly found HelmCharts and start their shared package work."""
            nonlocal resource_count
            for resource in candidates:
                if resource.identity in seen_resources:
                    continue
                seen_resources.add(resource.identity)
                resource_count += 1
                if resource.render_key in resource.lineage:
                    complete_resource(resource, "nested HelmChart rendering forms a cycle")
                    continue

                previous = references_by_name.setdefault(resource.reference.name, resource.reference)
                if previous != resource.reference:
                    raise CheckError(
                        f"chart directory {resource.reference.name} is requested by both "
                        f"{previous!r} and {resource.reference!r}"
                    )

                if resource.reference not in started_references:
                    started_references.add(resource.reference)
                    unprepared_references.add(resource.reference)
                    future = chart_executor.submit(
                        prepare_chart,
                        resource.reference,
                        output_dir,
                        generator_digest,
                    )
                    pending[future] = PendingTask(PipelineStage.PREPARE, resource.reference)
                schedule_validation(resource)

        add_resources(resources)
        manifest_cache_dir = output_dir / ".kubeconform-cache"
        render_cache_dir = _render_cache_dir()

        while pending:
            done, _ = concurrent.futures.wait(
                pending,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                task = pending.pop(future)
                reference = task.reference
                resource = task.resource

                if task.stage is PipelineStage.PREPARE:
                    try:
                        preparation = future.result()
                        preparations[reference] = preparation
                        if preparation.chart_status == "downloaded":
                            api_versions.update(_crd_api_versions([output_dir / reference.name]))
                        if preparation.schema_status == "update required":
                            schema_future = schema_executor.submit(
                                update_chart_schema,
                                reference,
                                output_dir,
                                generator_digest,
                            )
                            pending[schema_future] = PendingTask(PipelineStage.SCHEMA, reference)
                            continue
                        schemas[reference] = chart_schema_path(output_dir / reference.name, reference)
                    except Exception as exc:
                        fail_reference(reference, str(exc))
                        continue
                    finally:
                        unprepared_references.discard(reference)
                        schedule_waiting_renders()
                    schedule_waiting_resources(reference)
                    continue

                if task.stage is PipelineStage.SCHEMA:
                    try:
                        schemas[reference] = future.result()
                        preparation = preparations[reference]
                        preparations[reference] = ChartPreparation(
                            preparation.chart_status,
                            "updated",
                            preparation.source_digest,
                            preparation.render_digest,
                        )
                    except Exception as exc:
                        preparation = preparations.get(reference)
                        if preparation:
                            preparations[reference] = ChartPreparation(
                                preparation.chart_status,
                                "update failed",
                                preparation.source_digest,
                                preparation.render_digest,
                            )
                        fail_reference(reference, str(exc))
                        continue
                    schedule_waiting_resources(reference)
                    continue

                if resource is None:
                    raise CheckError(f"{task.stage.name.lower()} task has no HelmChart resource")

                try:
                    result = future.result()
                except Exception as exc:
                    complete_resource(resource, str(exc))
                    continue

                if task.stage is PipelineStage.VALIDATE:
                    if result:
                        complete_resource(resource, result)
                        continue
                    if task.value_cache_path is not None:
                        task.value_cache_path.parent.mkdir(parents=True, exist_ok=True)
                        task.value_cache_path.touch()
                    if (cached := cached_nested_resources(resource)) is not None:
                        add_resources(cached)
                        complete_resource(resource)
                        continue
                    schedule_render(resource)
                    continue

                if task.stage is PipelineStage.RENDER:
                    nested_resources = tuple(_helmcharts_from_yaml(result, resource))
                    add_resources(list(nested_resources))
                    manifest_future = manifest_executor.submit(validate_manifests, result, manifest_cache_dir)
                    pending[manifest_future] = PendingTask(
                        PipelineStage.MANIFEST,
                        reference,
                        resource,
                        render_cache_path=task.render_cache_path,
                        nested_resources=nested_resources,
                    )
                    continue

                if task.stage is PipelineStage.MANIFEST:
                    if result is None and task.render_cache_path is not None:
                        _write_render_cache(task.render_cache_path, task.nested_resources)
                    complete_resource(resource, result)
                    continue

                raise CheckError(f"unsupported pipeline stage: {task.stage}")

    return CheckSummary(tuple(failures), resource_count)


def main() -> int:
    """Render HelmCharts and run each through its independent pipeline."""
    root = repo_root()
    try:
        output_dir = root / "charts" / "services" / "upstream-charts"
        output_dir.mkdir(parents=True, exist_ok=True)
        generator_digest = _sha256_file(_helm_schema_binary())
        manifest_tool_digest = hashlib.sha256(
            "\0".join(
                (
                    _executable_digest("helm"),
                    _executable_digest("kubeconform"),
                    KUBERNETES_VERSION,
                    *KUBECONFORM_SCHEMA_LOCATIONS,
                    "ignore-missing-schemas",
                )
            ).encode()
        ).hexdigest()
        resources = render_resources(root)
        summary = check_charts(resources, output_dir, generator_digest, manifest_tool_digest)
    except (CheckError, RuntimeError, OSError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if summary.failures:
        print("\n\n".join(summary.failures), file=sys.stderr)
        print(
            f"{len(summary.failures)} of {summary.resource_count} HelmChart resources failed",
            file=sys.stderr,
        )
        return 1

    print(f"All {summary.resource_count} HelmChart resources passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
