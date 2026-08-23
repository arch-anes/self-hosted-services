#!/usr/bin/env python3
"""
Detects HelmChart value overrides that will be silently dropped.

The k3s Helm controller (like Helm itself) ignores spec.values keys that the
upstream chart never reads: they are merged into the values but no template
references them, so the override takes no effect without any warning.

This script renders charts/services, then checks every HelmChart spec.values
override against the vendored upstream chart in
charts/services/upstream-charts/ (kept in sync by
scripts/pull-upstream-helm-charts.py). A value path is considered consumed by
the chart when it is found in any of these sources:

  - values.yaml (including subcharts; library charts share the parent scope)
  - template references such as .Values.foo, $.Values.foo or
    $rootCtx.Values.foo, including guarded reads (hasKey, dig, index)
  - template expressions embedded in values.yaml (e.g. default config strings)
  - values documented only as comments (e.g. "# N8N_HOST: localhost")
  - values.schema.json, when the chart ships one
  - dependency conditions (Chart.yaml "condition: nfd.enabled")
  - imageSelector names (TrueCharts-style dynamic top-level image values)

A path below an iterated map (range .Values.x), an empty declared map, or a
list is free-form and not checked further.

Exits non-zero when a dropped override is found or when the vendored chart
cache is missing or stale, so the check can fail CI.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

# Charts whose values are arbitrary (raw manifest passthrough), like the pull script.
IGNORED_CHARTS = {"generic"}
PULL_SCRIPT = "scripts/pull-upstream-helm-charts.py"

logger = logging.getLogger("check-helm-chart-values")


# ---------------------------------------------------------------------------
# Helm template value-access patterns
# ---------------------------------------------------------------------------

KEY = r"[A-Za-z_][A-Za-z0-9_\-]*"
# A values access: .Values, $.Values, a root-context variable
# ($rootCtx.Values), or a context field holding the root context (.ctx.Values).
VBASE = r"(?:(?<![\w.$])(?:\$\w+)?(?:\.\w+)?\.Values|\$\.Values)"
VALUES_REF_RE = re.compile(VBASE + r"\.(?P<path>(?:" + KEY + r"\.)*(?:" + KEY + r"))")
VALUES_ROOT_RE = re.compile(VBASE + r"(?![\w.\-])")
RANGE_VALUES_RE = re.compile(
    r"range\s+(?:\$\w+(?:\s*,\s*\$\w+)*\s*:=\s+)?" + VBASE + r"(?P<path>(?:\.(?:" + KEY + r"))*)"
)
INDEX_VALUES_RE = re.compile(VBASE + r"(?P<path>(?:\.(?:" + KEY + r"))*)\s+\"(?P<key>[^\"]+)\"")
# dig "key1" "key2" <default> .Values.path  (keys before the object)
DIG_KEY_FIRST_RE = re.compile(r"dig\s+(?P<keys>(?:\"[^\"]*\"\s+)+)" + VBASE + r"(?P<path>(?:\.(?:" + KEY + r"))*)")
# dig .Values "key1" "key2"  (object before the keys)
DIG_OBJ_FIRST_RE = re.compile(VBASE + r"(?P<path>(?:\.(?:" + KEY + r"))*)\s+(?P<keys>(?:\"[^\"]*\"\s+)+)")
COMMENT_KEY_RE = re.compile(r"^\s*#\s*(?P<key>" + KEY + r")\s*:(?P<rest>\s.*)$")
ACTIVE_KEY_RE = re.compile(r"^(?P<indent>\s*)(?P<key>" + KEY + r")\s*:(?P<rest>.*)$")
LIST_ITEM_KEY_RE = re.compile(r"^(?P<indent>\s*)-\s+(?P<key>" + KEY + r")\s*:(?P<rest>.*)$")
BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?$")


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


class ChartCacheError(Exception):
    """The vendored upstream chart cache is missing or out of date."""


class HelmRenderError(Exception):
    """Rendering charts/services with helm template failed."""


@dataclass
class HelmChartResource:
    """One rendered HelmChart from charts/services."""

    name: str
    chart: str
    version: str
    values: dict[str, Any]


@dataclass
class ValueSurface:
    """Everything a chart can read from its values.

    declared:  the merged values.yaml surface (parent chart plus subcharts)
    refs:      value paths referenced by templates, schemas, comments, ...
    open_paths: paths whose subtree is free-form (iterated maps, toYaml of a
                root, empty declared maps, ...)
    """

    declared: dict[str, Any] = field(default_factory=dict)
    refs: set[str] = field(default_factory=set)
    open_paths: set[str] = field(default_factory=set)

    def add_refs(self, refs: set[str], open_paths: set[str]) -> None:
        self.refs |= refs
        self.open_paths |= open_paths

    @property
    def free_form(self) -> set[str]:
        return self.refs | self.open_paths

    def is_free_form(self, path: str) -> bool:
        return any(path == prefix or path.startswith(f"{prefix}.") for prefix in self.free_form)

    def declared_at(self, path: str) -> Any:
        node: Any = self.declared
        for part in path.split("."):
            if not isinstance(node, dict):
                return None
            node = node.get(part)
        return node


# ---------------------------------------------------------------------------
# Loading and scoping
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path(__file__).resolve().parent.parent


def load_yaml(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        logger.error("Cannot parse %s: %s", path, exc)
        return None


def load_yaml_dict(path: Path) -> dict:
    loaded = load_yaml(path)
    return loaded if isinstance(loaded, dict) else {}


def iter_subcharts(chart_dir: Path) -> list[Path]:
    charts_dir = chart_dir / "charts"
    if not charts_dir.is_dir():
        return []
    return sorted(d for d in charts_dir.iterdir() if d.is_dir())


def is_library(sub: Path) -> bool:
    return load_yaml_dict(sub / "Chart.yaml").get("type") == "library"


def dependency_alias(chart_dir: Path, sub: Path) -> str:
    """User values address subcharts by dependency alias when one is set."""
    chart = load_yaml_dict(chart_dir / "Chart.yaml")
    for dep in chart.get("dependencies") or []:
        if dep.get("name") == sub.name or dep.get("alias") == sub.name:
            return dep.get("alias") or sub.name
    return sub.name


def subchart_scope(chart_dir: Path, sub: Path, prefix: str = "") -> str:
    """User-values prefix for a subchart's templates and values.

    Library charts read the values of the chart that includes them, so they
    keep the enclosing prefix instead of adding their own name.
    """
    if is_library(sub):
        return prefix
    return f"{prefix}{dependency_alias(chart_dir, sub)}."


def build_declared_values(chart_dir: Path) -> dict:
    """Merged values surface: parent chart plus subcharts.

    Library charts share the parent's values scope, so they are flattened into
    the top level; other subcharts are nested under their dependency alias.
    """
    declared = dict(load_yaml_dict(chart_dir / "values.yaml"))
    for sub in iter_subcharts(chart_dir):
        sub_values = load_yaml_dict(sub / "values.yaml")
        if not sub_values:
            continue
        scope = subchart_scope(chart_dir, sub)
        if not scope:
            # Library charts share the parent's values scope.
            for key, value in sub_values.items():
                declared.setdefault(key, value)
            continue
        name = scope.rstrip(".")
        existing = declared.get(name)
        declared[name] = {**sub_values, **existing} if isinstance(existing, dict) else sub_values
    return declared


def iter_values_files(chart_dir: Path) -> Iterator[tuple[Path, str]]:
    """Yield (values.yaml path, scope prefix) for the parent chart and subcharts."""
    yield chart_dir / "values.yaml", ""
    for sub in iter_subcharts(chart_dir):
        yield sub / "values.yaml", subchart_scope(chart_dir, sub, "")


# ---------------------------------------------------------------------------
# Extracting references from text
# ---------------------------------------------------------------------------


def join_path(prefix: str, path: str) -> str:
    """Join a scope prefix (with trailing dot) and a values path without double dots."""
    return (prefix + (path.lstrip(".") if path else "")).rstrip(".")


def is_literal_key(key: str) -> bool:
    """A key is literal when it is a plain key or a dotted string of plain keys."""
    return bool(re.fullmatch(KEY, key)) or ("." in key and all(re.fullmatch(KEY, part) for part in key.split(".")))


def add_dig_refs(refs: set[str], open_paths: set[str], base: str, keys: list[str]) -> None:
    """Record a dig() read: literal keys resolve to an exact path, dynamic keys open the base."""
    keys = [key for key in keys if key]
    if keys and all(is_literal_key(key) for key in keys):
        refs.add(".".join(([base] if base else []) + keys))
    elif base:
        open_paths.add(base)


def extract_value_refs(text: str, prefix: str) -> tuple[set[str], set[str]]:
    """Collect value references (exact paths) and open (free-form) paths in text.

    A static reference to a map path consumes the whole map, so its subtree is
    free-form. Iterated maps (range .Values.x) are free-form from the start.
    """
    refs: set[str] = set()
    open_paths: set[str] = set()

    for m in INDEX_VALUES_RE.finditer(text):
        base = join_path(prefix, m.group("path"))
        key = m.group("key")
        if is_literal_key(key):
            # A dotted quoted key (e.g. index .Values "grafana.ini") names a
            # single value key that contains dots.
            refs.add(f"{base}.{key}" if base else key)
        elif base:
            open_paths.add(base)

    for m in DIG_KEY_FIRST_RE.finditer(text):
        keys = [key.strip('"') for key in m.group("keys").split()]
        add_dig_refs(refs, open_paths, join_path(prefix, m.group("path")), keys)

    for m in DIG_OBJ_FIRST_RE.finditer(text):
        keys = [key.strip('"') for key in m.group("keys").split()]
        add_dig_refs(refs, open_paths, join_path(prefix, m.group("path")), keys)

    for m in RANGE_VALUES_RE.finditer(text):
        if path := join_path(prefix, m.group("path")):
            open_paths.add(path)

    for m in VALUES_REF_RE.finditer(text):
        refs.add(prefix + m.group("path"))

    if prefix and VALUES_ROOT_RE.search(text):
        # The whole scope is handed to a function (e.g. toYaml .Values).
        open_paths.add(prefix)

    return refs, open_paths


def scan_template_refs(chart_dir: Path, prefix: str) -> tuple[set[str], set[str]]:
    refs: set[str] = set()
    open_paths: set[str] = set()

    templates_dir = chart_dir / "templates"
    if templates_dir.is_dir():
        files = [*templates_dir.rglob("*.yaml"), *templates_dir.rglob("*.tpl")]
        for tpl in files:
            try:
                text = tpl.read_text(errors="replace")
            except OSError:
                continue
            tpl_refs, tpl_open = extract_value_refs(text, prefix)
            refs |= tpl_refs
            open_paths |= tpl_open

    for sub in iter_subcharts(chart_dir):
        sub_refs, sub_open = scan_template_refs(sub, subchart_scope(chart_dir, sub, prefix))
        refs |= sub_refs
        open_paths |= sub_open

    return refs, open_paths


def scan_commented_keys(values_path: Path, prefix: str) -> tuple[set[str], set[str]]:
    """Collect keys documented only as comments, e.g. '# N8N_HOST: localhost'."""
    refs: set[str] = set()
    open_paths: set[str] = set()
    if not values_path.is_file():
        return refs, open_paths

    stack: list[tuple[int, str, bool]] = []  # (indent, key, is_real_key)
    block_indent = -1  # > 0 while inside a literal/folded block scalar
    for line in values_path.read_text().splitlines():
        if block_indent > 0:
            if not line.strip() or (len(line) - len(line.lstrip())) > block_indent:
                continue
            block_indent = -1

        if m := COMMENT_KEY_RE.match(line):
            indent = len(line) - len(line.lstrip())
            while stack and stack[-1][0] >= indent:
                stack.pop()
            # Keys inside list items do not matter: list overrides are
            # append-merged and the check stops at the list level.
            if not any(is_real for _, _, is_real in stack):
                continue
            path = prefix + ".".join([*(key for _, key, _ in stack), m.group("key")])
            (refs if m.group("rest").strip() else open_paths).add(path)
            continue

        if m := ACTIVE_KEY_RE.match(line):
            indent = len(m.group("indent"))
            rest = m.group("rest").strip()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, m.group("key"), True))
            if rest and BLOCK_SCALAR_RE.match(rest):
                block_indent = indent
            continue

        if m := LIST_ITEM_KEY_RE.match(line):
            indent = len(m.group("indent")) + 2
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, m.group("key"), False))

    return refs, open_paths


def schema_paths(schema: Any, prefix: str = "") -> Iterator[tuple[str, bool]]:
    """Yield (path, is_open) paths declared by a values.schema.json."""
    if not isinstance(schema, dict):
        return
    for key, sub in (schema.get("properties") or {}).items():
        path = f"{prefix}.{key}" if prefix else key
        yield path, False
        yield from schema_paths(sub, path)
    if schema.get("patternProperties") and prefix:
        yield prefix, True
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        yield from schema_paths(additional, prefix)
    elif additional is True and prefix:
        # Explicitly allows arbitrary keys.
        yield prefix, True


def dependency_condition_refs(chart_dir: Path) -> set[str]:
    """Dependency conditions are value paths too: 'condition: nfd.enabled'."""
    chart = load_yaml_dict(chart_dir / "Chart.yaml")
    refs: set[str] = set()
    for dep in chart.get("dependencies") or []:
        for condition in (dep.get("condition") or "").split(","):
            condition = condition.strip()
            if condition and all(is_literal_key(part) for part in condition.split(".")):
                refs.add(condition)
    return refs


def image_selector_names(values: Any) -> set[str]:
    """TrueCharts imageSelector values point at dynamic top-level image keys."""
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            selector = node.get("imageSelector")
            if isinstance(selector, str) and re.fullmatch(KEY, selector):
                names.add(selector)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(values)
    return names


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


def build_surface(chart_dir: Path) -> ValueSurface:
    """Collect everything the chart reads from its values."""
    surface = ValueSurface(declared=build_declared_values(chart_dir))
    surface.add_refs(*scan_template_refs(chart_dir, ""))
    surface.refs |= dependency_condition_refs(chart_dir)
    for values_path, prefix in iter_values_files(chart_dir):
        surface.add_refs(*scan_commented_keys(values_path, prefix))
        if values_path.is_file():
            # Template expressions embedded in values.yaml (e.g. default
            # config strings) read values too.
            text = values_path.read_text(errors="replace")
            surface.add_refs(*extract_value_refs(text, prefix))
    schema = load_yaml(chart_dir / "values.schema.json")
    for path, is_open in schema_paths(schema):
        (surface.open_paths if is_open else surface.refs).add(path)
    return surface


def find_dropped_overrides(overrides: dict, surface: ValueSurface) -> list[str]:
    """Walk the override tree; return the paths that are silently dropped."""
    dropped: list[str] = []

    def walk(values: dict, prefix: str) -> None:
        for key, value in values.items():
            path = f"{prefix}.{key}" if prefix else key
            if surface.is_free_form(path):
                continue
            declared = surface.declared_at(path)
            if declared is None:
                dropped.append(path)
            elif isinstance(value, dict) and isinstance(declared, dict) and declared:
                # Both are non-empty maps: children must be declared too.
                # An empty declared map (or a list, or a scalar) is free-form.
                walk(value, path)

    if isinstance(overrides, dict):
        walk(overrides, "")
    return dropped


def check_chart(chart_dir: Path, resource: HelmChartResource) -> list[str]:
    """Return the override paths that chart_dir will silently drop.

    Raises ChartCacheError when the vendored chart is missing or stale.
    """
    if not chart_dir.is_dir():
        raise ChartCacheError(f"upstream chart '{resource.chart}' is missing from the cache; run {PULL_SCRIPT}")
    vendored = load_yaml_dict(chart_dir / "Chart.yaml")
    if str(vendored.get("version")) != resource.version:
        raise ChartCacheError(
            f"stale chart cache: '{resource.chart}' is at version {vendored.get('version')}, "
            f"but the HelmChart requests {resource.version}; run {PULL_SCRIPT}"
        )

    surface = build_surface(chart_dir)
    surface.refs |= image_selector_names(resource.values)
    return find_dropped_overrides(resource.values, surface)


def render_helm_charts(repo_root: Path) -> str:
    rendered = subprocess.run(
        ["helm", "template", "charts/services"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if rendered.returncode != 0:
        raise HelmRenderError(rendered.stderr.strip())
    return rendered.stdout


def parse_helm_charts(rendered: str) -> list[HelmChartResource]:
    resources: list[HelmChartResource] = []
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") != "HelmChart":
            continue
        spec = doc.get("spec") or {}
        values = spec.get("values")
        resources.append(
            HelmChartResource(
                name=(doc.get("metadata") or {}).get("name", "?"),
                chart=str(spec.get("chart", "")).rstrip("/").rsplit("/", 1)[-1],
                version=str(spec.get("version", "")),
                values=values if isinstance(values, dict) else {},
            )
        )
    return resources


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    upstream_dir = repo_root() / "charts" / "services" / "upstream-charts"

    logger.info("Rendering charts/services ...")
    try:
        rendered = render_helm_charts(repo_root())
    except HelmRenderError as exc:
        logger.error("helm template failed:\n%s", exc)
        return 1

    resources = parse_helm_charts(rendered)
    to_check = [r for r in resources if r.chart and r.chart not in IGNORED_CHARTS]
    ignored = len(resources) - len(to_check)
    logger.info("Found %d HelmChart resources (%d ignored)", len(resources), ignored)

    memo: dict[str, list[str]] = {}
    errors = 0
    total_dropped = 0
    for index, resource in enumerate(to_check, start=1):
        logger.info(
            "Checking %s (chart %s %s) [%d/%d]",
            resource.name,
            resource.chart,
            resource.version,
            index,
            len(to_check),
        )
        try:
            if resource.chart not in memo:
                memo[resource.chart] = check_chart(upstream_dir / resource.chart, resource)
            dropped = memo[resource.chart]
        except ChartCacheError as exc:
            errors += 1
            logger.error("%s: %s", resource.name, exc)
            continue
        total_dropped += len(dropped)
        for path in dropped:
            logger.warning(
                "%s: value override '%s' is not consumed by chart %s %s and will be silently dropped",
                resource.name,
                path,
                resource.chart,
                resource.version,
            )

    if total_dropped or errors:
        logger.error(
            "Found %d dropped value override(s) in %d HelmChart resource(s), %d error(s)",
            total_dropped,
            len(to_check),
            errors,
        )
        return 1

    logger.info("Checked %d HelmChart resources: all value overrides are consumed", len(to_check))
    return 0


if __name__ == "__main__":
    sys.exit(main())
