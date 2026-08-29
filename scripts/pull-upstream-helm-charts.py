#!/usr/bin/env python3
"""Cache upstream Helm charts and generate schemas for their values.

The script reads static chart references from the services templates. It pulls
each distinct chart and asks the Helm schema plugin to analyze its templates.
It also creates the resource schemas used by check-helm-chart-values.py.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SPEC_RE = re.compile(r"^\s*spec:\s*$")
FIELD_RE = re.compile(r"^\s*(chart|repo|version):\s*(.+)$")
DOCUMENT_SEPARATOR_RE = re.compile(r"^---\s*$", re.MULTILINE)

IGNORED_CHARTS = {"generic"}

HELMCHART_API_VERSION = "helm.cattle.io/v1"
HELMCHART_KIND = "HelmChart"
HELMCHART_SCHEMA_NAME = "helmchart.schema.json"
VALUES_OVERRIDE_SCHEMA_NAME = "values.override.schema.json"
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


def fetch_chart(chart: str, repo: str | None, version: str, output_dir: Path) -> bool:
    """Cache one chart version and generate its full-values schemas.

    A matching cached chart does not require another download. The function
    still refreshes its schemas because the generator can change.

    If the cached version differs, Helm replaces that chart directory with the
    requested version.

    Returns:
        True if the chart and its schemas are available. Otherwise, False.
    """
    chart_name = Path(chart).name
    final_dir = output_dir / chart_name
    chart_yaml = final_dir / "Chart.yaml"

    if chart_yaml.is_file():
        match = re.search(
            r"^version:\s*(.+)$",
            chart_yaml.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if match and match.group(1).strip().strip("\"'") == version:
            print(f"Chart {chart_name} {version} is cached. " "Refreshing its schemas.")
            return refresh_schema(final_dir)

    if chart.startswith("oci://"):
        print(f"Pulling OCI chart {chart} (version: {version}) -> {final_dir}")
        command = [
            "helm",
            "pull",
            chart,
            "--version",
            version,
            "--untar",
            "--untardir",
            str(output_dir),
        ]
    else:
        if not repo:
            print(f"Cannot pull {chart}: its HelmChart has no repository")
            return False
        print(f"Pulling chart {chart} from {repo} (version: {version}) -> {final_dir}")
        command = [
            "helm",
            "pull",
            chart,
            "--repo",
            repo or "",
            "--version",
            version,
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
        print(f"Timeout while pulling {chart}")
        return False
    except subprocess.CalledProcessError as exc:
        print(f"Failed to pull {chart}: {exc.stderr.strip()}")
        return False

    return refresh_schema(final_dir)


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


def fetch_charts(chart_references: set[ChartReference], output_dir: Path) -> None:
    """Fetch all distinct charts concurrently.

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
        futures = [
            executor.submit(
                fetch_chart,
                reference.chart,
                reference.repository,
                reference.version,
                output_dir,
            )
            for reference in sorted(chart_references, key=_reference_sort_key)
        ]
        failures = 0
        for future in concurrent.futures.as_completed(futures):
            try:
                if not future.result():
                    failures += 1
            except Exception as exc:
                failures += 1
                print(f"Unexpected chart error: {exc}")
    if failures:
        raise RuntimeError(f"{failures} chart fetch or schema generation job(s) failed")


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


def generate_helmchart_schemas(chart_references: set[ChartReference], services_dir: Path) -> None:
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

    print(f"Wrote {len(references_by_name)} HelmChart resource schemas")


def main() -> None:
    """Populate chart caches and regenerate chart and HelmChart schemas."""
    services_dir = repo_root() / "charts" / "services"
    templates_dir = services_dir / "templates"
    output_dir = services_dir / "upstream-charts"

    output_dir.mkdir(parents=True, exist_ok=True)
    chart_references = gather_chart_references(templates_dir)
    try:
        fetch_charts(chart_references, output_dir)
        generate_helmchart_schemas(chart_references, services_dir)
    except RuntimeError as exc:
        print(f"error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
