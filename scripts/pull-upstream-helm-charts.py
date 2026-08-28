#!/usr/bin/env python3
"""
Fetches upstream default values for all Helm charts under templates/.
This script parses HelmChart resources to pull values using repo + chart + version,
or in the case of OCI, chart + version.
"""

import concurrent.futures
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterator, Tuple, Optional

# Pre-compile regexes for performance and readability
SPEC_RE = re.compile(r"^\s*spec:\s*$")
FIELD_RE = re.compile(r"^\s*(chart|repo|version):\s*(.+)$")

# Charts to explicitly skip fetching (extend this set as needed)
IGNORED_CHARTS = {
    "generic",
}


def _parse_helm_chart_doc(document: str) -> Optional[Tuple[str, Optional[str], str]]:
    """Extract one HelmChart document's chart, optional repo, and version.

    This intentionally reads only direct fields in the document's ``spec``
    block.  It ignores Helm template expressions and nested YAML, which keeps
    the parser suitable for the static chart references maintained here.
    """
    lines = iter(document.splitlines())
    spec_indent = -1

    # Fast-forward to the spec block
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

        curr_indent = len(line) - len(line.lstrip())

        if curr_indent <= spec_indent and not line.lstrip().startswith("{{"):
            break  # Exited spec block

        if line.lstrip().startswith("{{"):
            continue

        if expected_indent == -1:
            expected_indent = curr_indent

        if curr_indent != expected_indent:
            continue

        match = FIELD_RE.match(line)
        if not match:
            continue

        key, val = match.groups()
        val = val.strip().strip("\"'")

        if key == "chart" and chart is None:
            chart = val
        elif key == "repo" and repo is None:
            repo = val
        elif key == "version" and version is None:
            version = val

    if chart and version:
        return chart, repo, version
    return None


def parse_charts_from_file(file_path: Path) -> Iterator[Tuple[str, Optional[str], str]]:
    """Yield complete chart references from HelmChart documents in one file.

    Template files can contain ordinary Kubernetes resources as well as
    HelmChart resources.  Documents without a complete chart and version are
    skipped by ``_parse_helm_chart_doc``.
    """
    content = file_path.read_text()
    return (
        result
        for document in content.split("---")
        if "kind: HelmChart" in document
        if (result := _parse_helm_chart_doc(document)) is not None
    )


def get_repo_root() -> Path:
    """Return the repository root relative to this script's fixed location.

    Both scripts live in the repository's ``scripts`` directory.  Resolving
    their path works in a Git checkout and a source archive, without relying
    on the current working directory or Git metadata.
    """
    return Path(__file__).resolve().parents[1]


def refresh_schema(chart_dir: Path) -> bool:
    """Generate schemas for a chart and its unpacked dependencies.

    The Helm plugin replaces every relevant ``values.schema.json`` below
    ``chart_dir``.  Output is replayed for diagnostics.  A timeout or plugin
    failure returns ``False`` so parallel callers can include this chart in
    the final failure count.
    """
    try:
        result = subprocess.run(
            ["helm", "schema", str(chart_dir)],
            capture_output=True,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
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


def fetch_chart(chart: str, repo: Optional[str], version: str, output_dir: Path) -> bool:
    """Ensure one requested chart version and its generated schemas are cached.

    A matching cached ``Chart.yaml`` avoids another network pull but still
    refreshes schemas, since generator behavior can change independently of a
    chart version.  A different cached version is removed before Helm untars
    the requested chart into the shared output directory.

    Returns:
        ``True`` only when the chart is available and schema generation
        succeeds; otherwise ``False`` after printing the relevant diagnostic.
    """
    chart_name = Path(chart).name
    final_dir = output_dir / chart_name
    chart_yaml = final_dir / "Chart.yaml"

    if chart_yaml.exists():
        match = re.search(r"^version:\s*(.+)$", chart_yaml.read_text(), re.MULTILINE)
        if match and match.group(1).strip().strip("\"'") == version:
            print(f"Chart {chart_name} (version: {version}) already exists in {final_dir}. Refreshing schema.")
            return refresh_schema(final_dir)

    if chart.startswith("oci://"):
        print(f"Pulling OCI chart {chart} (version: {version}) -> {final_dir}")
        cmd = ["helm", "pull", chart, "--version", version, "--untar", "--untardir", str(output_dir)]
    else:
        print(f"Pulling chart {chart} from {repo} (version: {version}) -> {final_dir}")
        cmd = [
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
        subprocess.run(cmd, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL, timeout=120)
    except subprocess.TimeoutExpired:
        print(f"Timeout while pulling {chart}")
        return False
    except subprocess.CalledProcessError as e:
        print(f"Failed to pull {chart}: {e.stderr.strip()}")
        return False

    return refresh_schema(final_dir)


def _prepare_output_directory(output_dir: Path) -> None:
    """Create the shared upstream-chart cache directory when it is absent."""
    output_dir.mkdir(parents=True, exist_ok=True)


def _gather_unique_charts(templates_dir: Path) -> set:
    """Return distinct, non-generic chart references declared in templates."""
    return {
        chart_data
        for file_path in templates_dir.glob("*.yaml")
        for chart_data in parse_charts_from_file(file_path)
        if Path(chart_data[0]).name not in IGNORED_CHARTS
    }


def _fetch_charts_in_parallel(unique_charts: set, output_dir: Path) -> None:
    """Fetch every chart concurrently and raise if any job does not complete.

    Each job owns one final chart directory because duplicate chart references
    were removed before submission.  Exceptions are collected so independent
    jobs can finish and all failed charts are reported in one run.
    """
    print(f"Found {len(unique_charts)} unique charts. Fetching in parallel...")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(fetch_chart, chart, repo, version, output_dir)
            for chart, repo, version in sorted(unique_charts)
        ]
        failures = 0
        for future in concurrent.futures.as_completed(futures):
            try:
                failures += not future.result()
            except Exception as exc:
                failures += 1
                print(f"Unexpected chart fetch or schema generation error: {exc}")
    if failures:
        raise RuntimeError(f"{failures} chart fetch or schema generation job(s) failed")


def main() -> None:
    """Populate the chart cache and regenerate schemas for every service chart."""
    repo_root = get_repo_root()
    services_dir = repo_root / "charts" / "services"
    templates_dir = services_dir / "templates"
    output_dir = services_dir / "upstream-charts"

    _prepare_output_directory(output_dir)
    unique_charts = _gather_unique_charts(templates_dir)
    try:
        _fetch_charts_in_parallel(unique_charts, output_dir)
    except RuntimeError as exc:
        print(f"error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
