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
    content = file_path.read_text()
    return (
        result
        for document in content.split("---")
        if "kind: HelmChart" in document
        if (result := _parse_helm_chart_doc(document)) is not None
    )


def get_repo_root() -> Path:
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path(__file__).resolve().parent.parent


def fetch_chart(chart: str, repo: Optional[str], version: str, output_dir: Path) -> None:
    filename = f"{Path(chart).name}-{version}.yaml"
    output_file = output_dir / filename

    if chart.startswith("oci://"):
        print(f"Fetching OCI chart {chart} (version: {version}) -> {output_file}")
        cmd = ["helm", "show", "values", chart, "--version", version]
    else:
        print(f"Fetching chart {chart} from {repo} (version: {version}) -> {output_file}")
        cmd = ["helm", "show", "values", chart, "--repo", repo or "", "--version", version]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        output_file.write_text(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Failed to fetch {chart}: {e.stderr.strip()}")


def _prepare_output_directory(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def _gather_unique_charts(templates_dir: Path) -> set:
    return {
        chart_data
        for file_path in templates_dir.glob("*.yaml")
        for chart_data in parse_charts_from_file(file_path)
        if Path(chart_data[0]).name not in IGNORED_CHARTS
    }


def _fetch_charts_in_parallel(unique_charts: set, output_dir: Path) -> None:
    print(f"Found {len(unique_charts)} unique charts. Fetching in parallel...")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(fetch_chart, chart, repo, version, output_dir)
            for chart, repo, version in sorted(unique_charts)
        ]
        concurrent.futures.wait(futures)


def main():
    repo_root = get_repo_root()
    services_dir = repo_root / "charts" / "services"
    templates_dir = services_dir / "templates"
    output_dir = services_dir / "default-values"

    _prepare_output_directory(output_dir)
    unique_charts = _gather_unique_charts(templates_dir)
    _fetch_charts_in_parallel(unique_charts, output_dir)


if __name__ == "__main__":
    main()
