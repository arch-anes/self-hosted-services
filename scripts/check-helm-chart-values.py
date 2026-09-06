#!/usr/bin/env python3
"""Cache upstream Helm charts, generate schemas, and validate chart values.

The script reads static chart references from the services templates. It pulls
each distinct chart and asks the Helm schema plugin to analyze its templates.
It reuses schemas when the chart, generator binary, and generated files match
the saved state. It then validates rendered HelmChart resources against those
schemas.
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
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

SPEC_RE = re.compile(r"^\s*spec:\s*$")
FIELD_RE = re.compile(r"^\s*(chart|repo|version):\s*(.+)$")
DOCUMENT_SEPARATOR_RE = re.compile(r"^---\s*$", re.MULTILINE)

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
    chart: str
    version: str
    document: dict[str, object]


@dataclass(frozen=True)
class ValidationJob:
    """Pair one isolated HelmChart file with its resource schema."""

    resource: HelmChartResource
    document_path: Path
    schema_path: Path


def _parse_helm_chart_doc(document: str) -> ChartReference | None:
    """Read a chart reference from one static HelmChart document.

    Helm templates are not valid YAML before Helm renders them. This small
    parser reads only direct fields in the spec block. It ignores template
    expressions and nested YAML.
    """
    lines = iter(document.splitlines())
    spec_indent = -1

    for line in lines:
        if SPEC_RE.match(line):
            spec_indent = len(line) - len(line.lstrip())
            break
    else:
        return None

    chart, repo, version = None, None, None
    expected_indent = -1

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        current_indent = len(line) - len(line.lstrip())

        if current_indent <= spec_indent and not line.lstrip().startswith("{{"):
            break

        if line.lstrip().startswith("{{"):
            continue

        if expected_indent == -1:
            expected_indent = current_indent

        if current_indent != expected_indent:
            continue

        match = FIELD_RE.match(line)
        if not match:
            continue

        key, value = match.groups()
        value = value.strip().strip("\"'")

        if key == "chart" and chart is None:
            chart = value
        elif key == "repo" and repo is None:
            repo = value
        elif key == "version" and version is None:
            version = value

    if chart and version:
        return ChartReference(chart, repo, version)
    return None


def parse_charts_from_file(file_path: Path) -> Iterator[ChartReference]:
    """Yield each complete HelmChart reference in one template file.

    A template can contain other Kubernetes resources. The function ignores
    documents that do not contain a static chart and version.
    """
    content = file_path.read_text(encoding="utf-8")
    for document in DOCUMENT_SEPARATOR_RE.split(content):
        if "kind: HelmChart" not in document:
            continue
        if chart := _parse_helm_chart_doc(document):
            yield chart


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


def refresh_schema(chart_dir: Path) -> bool:
    """Generate schemas for one chart and its unpacked dependencies.

    The Helm plugin replaces each values.schema.json file below the chart
    directory. This function prints plugin output for diagnostics.

    Returns:
        True if schema generation succeeds. Otherwise, False.
    """
    try:
        result = subprocess.run(
            ["helm", "schema", str(chart_dir)],
            capture_output=True,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"Timeout while generating schema for {chart_dir.name}")
        return False
    except subprocess.CalledProcessError as exc:
        print(f"Failed to generate schema for {chart_dir.name}: {exc.stderr.strip()}")
        return False

    output = (result.stdout + result.stderr).strip()
    if output:
        print(output)
    return True


def fetch_chart(
    reference: ChartReference,
    output_dir: Path,
    generator_digest: str,
) -> bool:
    """Prepare one chart and return whether its schemas were regenerated.

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
                print(f"Chart {reference.name} {reference.version} and its schemas are cached.")
                return False
            print(f"Chart {reference.name} {reference.version} is cached. Refreshing its schemas.")
            if refresh_schema(final_dir):
                return True
            raise RuntimeError(f"schema generation failed for {reference.name}")

    if reference.chart.startswith("oci://"):
        print(f"Pulling OCI chart {reference.chart} (version: {reference.version}) -> {final_dir}")
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
        print(
            f"Pulling chart {reference.chart} from {reference.repository} "
            f"(version: {reference.version}) -> {final_dir}"
        )
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

    if refresh_schema(final_dir):
        return True
    raise RuntimeError(f"schema generation failed for {reference.name}")


def gather_chart_references(templates_dir: Path) -> set[ChartReference]:
    """Return the distinct supported chart references in all templates."""
    return {
        chart_data
        for file_path in templates_dir.glob("*.yaml")
        for chart_data in parse_charts_from_file(file_path)
        if chart_data.name not in IGNORED_CHARTS
    }


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
            raise RuntimeError(
                f"chart directory {reference.name} is requested by both " f"{previous!r} and {reference!r}"
            )
    return references_by_name


def fetch_charts(
    chart_references: set[ChartReference],
    output_dir: Path,
    generator_digest: str,
) -> set[ChartReference]:
    """Prepare charts concurrently and return those with new schemas.

    Each available CPU supplies one worker. Each worker owns one output
    directory. The function waits for all workers and reports all errors.

    Raises:
        RuntimeError: One or more workers do not complete successfully.
    """
    if not chart_references:
        raise RuntimeError("no supported chart references were found")

    _chart_references_by_name(chart_references)
    workers = min(os.process_cpu_count() or 1, len(chart_references))
    print(f"Found {len(chart_references)} distinct charts. " f"Fetching them with {workers} workers...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_chart, reference, output_dir, generator_digest): reference
            for reference in sorted(chart_references, key=_reference_sort_key)
        }
        changed_references: set[ChartReference] = set()
        failures: list[str] = []
        for future in concurrent.futures.as_completed(futures):
            reference = futures[future]
            try:
                if future.result():
                    changed_references.add(reference)
            except Exception as exc:
                failures.append(f"{reference.name}: {exc}")
    if failures:
        raise RuntimeError("\n".join(failures))
    return changed_references


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
    chart_references: set[ChartReference],
    services_dir: Path,
    generator_digest: str,
) -> None:
    """Write override and HelmChart schemas beside each full-values schema.

    Raises:
        RuntimeError: A cache name is ambiguous, or a generated schema is
            missing or invalid.
    """
    references_by_name = _chart_references_by_name(chart_references)
    for chart_name, reference in references_by_name.items():
        chart_dir = services_dir / "upstream-charts" / chart_name
        values_schema_path = chart_dir / "values.schema.json"
        if not values_schema_path.is_file():
            raise RuntimeError(f"generated schema is missing for {chart_name}")

        try:
            values_schema = json.loads(values_schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read generated schema for {chart_name}: {exc}") from exc

        if not isinstance(values_schema, (bool, dict)):
            raise RuntimeError(f"generated schema for {chart_name} is not a JSON Schema object")

        _write_json(
            chart_dir / VALUES_OVERRIDE_SCHEMA_NAME,
            _values_override_schema(values_schema),
        )
        _write_json(
            chart_dir / HELMCHART_SCHEMA_NAME,
            _helmchart_schema(chart_name, reference.version),
        )
        _write_schema_state(chart_dir, reference.version, generator_digest)

    if references_by_name:
        print(f"Wrote {len(references_by_name)} HelmChart resource schemas")


def prepare_upstream_charts(root: Path) -> None:
    """Populate chart caches and regenerate chart and HelmChart schemas."""
    services_dir = root / "charts" / "services"
    templates_dir = services_dir / "templates"
    output_dir = services_dir / "upstream-charts"

    output_dir.mkdir(parents=True, exist_ok=True)
    chart_references = gather_chart_references(templates_dir)
    generator_digest = _sha256_file(_helm_schema_binary())
    changed_references = fetch_charts(
        chart_references,
        output_dir,
        generator_digest,
    )
    generate_helmchart_schemas(
        changed_references,
        services_dir,
        generator_digest,
    )


def render_resources(root: Path) -> list[HelmChartResource]:
    """Render the services chart and return its supported HelmChart resources.

    The generic chart accepts arbitrary Kubernetes objects. It has no upstream
    values schema, so this script does not validate its values.
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
    """Return the matching schema for a rendered HelmChart resource."""
    chart_dir = root / "charts" / "services" / "upstream-charts" / resource.chart
    chart_file = chart_dir / "Chart.yaml"
    if not chart_file.is_file():
        raise CheckError(f"{resource.name}: chart {resource.chart} is missing after preparation")

    chart = yaml.safe_load(chart_file.read_text(encoding="utf-8"))
    if not isinstance(chart, dict):
        raise CheckError(f"{resource.name}: {chart_file} does not contain an object")

    cached_version = str(chart.get("version", ""))
    if cached_version != resource.version:
        raise CheckError(
            f"{resource.name}: chart {resource.chart} is version {cached_version}, "
            f"but the HelmChart requests {resource.version}"
        )

    for schema_name in (
        "values.schema.json",
        VALUES_OVERRIDE_SCHEMA_NAME,
        HELMCHART_SCHEMA_NAME,
    ):
        schema_path = chart_dir / schema_name
        if not schema_path.is_file():
            raise CheckError(f"{resource.name}: {schema_name} is missing for {resource.chart}")
    return chart_dir / HELMCHART_SCHEMA_NAME


def prepare_validation_jobs(
    root: Path,
    resources: list[HelmChartResource],
    directory: Path,
) -> tuple[list[ValidationJob], list[str]]:
    """Create one temporary HelmChart file and validation job per resource."""
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
        jobs.append(ValidationJob(resource, document_path, schema_path))
    return jobs, errors


def validate_job(job: ValidationJob) -> str | None:
    """Validate one HelmChart file and return a diagnostic on failure."""
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
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"validation exceeded {COMMAND_TIMEOUT_SECONDS}s"

    if not result.returncode:
        return None
    return "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())


def validate_jobs(jobs: list[ValidationJob]) -> list[str]:
    """Validate HelmChart resources concurrently and report each result."""
    if not jobs:
        return []

    workers = min(os.process_cpu_count() or 1, len(jobs))
    print(f"Validating {len(jobs)} HelmChart resources with {workers} workers...", flush=True)
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
    """Prepare chart schemas and validate all rendered HelmChart values."""
    root = repo_root()
    try:
        prepare_upstream_charts(root)
        resources = render_resources(root)
        with tempfile.TemporaryDirectory(prefix="helmchart-values-") as temporary:
            jobs, setup_errors = prepare_validation_jobs(root, resources, Path(temporary))
            failures = [*setup_errors, *validate_jobs(jobs)]
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
