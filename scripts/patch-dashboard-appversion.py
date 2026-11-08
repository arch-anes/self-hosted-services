import os
import json
import re
import urllib.request
import sys
import subprocess
from pathlib import Path

STRIP_CHARS = " \t\n\r\"'"
APP_MARKER_PATTERN = re.compile(r"\s*\*\(App:[^*]*\)\*")
UPDATE_VERSIONS_PATTERN = re.compile(r"\bfrom\s+(?P<from>[^\s`)\]>]+)\s+->\s+(?P<to>[^\s`)\]>]+)")


def extract_oci_url(package_file, dep_name):
    """
    Helper to extract the exact OCI URL from the source YAML file.
    Note: Renovate's 'docker' datasource implicitly strips URL paths when parsing OCI registries
    because the core Docker API does not support paths on the registry host level. If an OCI
    chart includes a path (e.g., oci://oci.trueforge.org/truecharts/valheim), Renovate drops
    '/truecharts' and records only the host. To get the full OCI URL for 'helm show chart',
    we must parse it directly from the original source file.
    """
    if not package_file or not os.path.exists(package_file):
        return None

    with open(package_file, "r") as f:
        for line in f:
            if "chart:" not in line or dep_name not in line:
                continue

            parts = line.split("chart:", 1)
            if len(parts) <= 1:
                continue

            url = parts[1].strip(STRIP_CHARS)
            if not url.endswith(dep_name) or not url.startswith("oci://"):
                continue

            return url

    return None


def get_app_version(dep_name, registry_url, version, datasource, package_file=None):
    """Uses helm CLI to fetch the appVersion from the remote chart."""
    if not registry_url:
        return None

    # Handle standard HTTP Helm repositories
    if datasource != "docker" and "oci://" not in registry_url:
        repo_name = f"temp-{abs(hash(registry_url))}"
        subprocess.run(["helm", "repo", "add", repo_name, registry_url], capture_output=True, check=False)
        cmd = ["helm", "show", "chart", f"{repo_name}/{dep_name}", "--version", version]

    # Handle OCI registries
    else:
        full_url = extract_oci_url(package_file, dep_name)

        # Fallback if file parsing fails
        if not full_url:
            clean_url = registry_url.replace("https://", "").replace("http://", "")
            if not clean_url.startswith("oci://"):
                clean_url = f"oci://{clean_url}"
            full_url = f"{clean_url}/{dep_name}"

        cmd = ["helm", "show", "chart", full_url, "--version", version]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except Exception as e:
        print(f"Warning: Failed to fetch appVersion for {dep_name}:{version} - {e}")
        return None

    # Parse the YAML output for appVersion
    for line in result.stdout.split("\n"):
        if line.startswith("appVersion:"):
            return line.split(":", 1)[1].strip(STRIP_CHARS)

    return None


def get_cached_app_version(dep_name, version):
    """Read one chart version's appVersion from the lint job's chart cache."""
    chart_path = Path(".cache/upstream-charts") / Path(dep_name).name
    if not (chart_path / "Chart.yaml").is_file():
        return None

    try:
        result = subprocess.run(
            ["helm", "show", "chart", str(chart_path)],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception as e:
        print(f"Warning: Failed to read cached appVersion for {dep_name}:{version} - {e}")
        return None

    chart_metadata = {}
    for line in result.stdout.split("\n"):
        key, separator, value = line.partition(":")
        if separator and key in {"version", "appVersion"}:
            chart_metadata[key] = value.strip(STRIP_CHARS)

    if chart_metadata.get("version") != version:
        print(f"Warning: Cached chart for {dep_name} does not match requested version {version}")
        return None

    return chart_metadata.get("appVersion") or None


def dependency_sources(dependencies):
    """
    Index every chart dependency by name with the data needed to fetch its appVersions.

    A dependency can appear in several package files, so its pending updates are merged
    instead of letting the last entry win.
    """
    sources = {}
    for package_file, dep in dependencies:
        dep_name = dep.get("depName")
        registry_url = dep.get("registryUrl")
        datasource = dep.get("datasource")
        if not (dep_name and registry_url) or datasource not in {"helm", "docker"}:
            continue

        source = sources.setdefault(
            dep_name,
            {
                "registry_url": registry_url,
                "datasource": datasource,
                "package_file": package_file,
                "current_version": dep.get("currentValue"),
                "new_versions": set(),
            },
        )
        source["new_versions"].update(
            update["newValue"] for update in dep.get("updates") or () if update.get("newValue")
        )

    return sources


def resolve_app_version(dep_name, version, source, resolved, prefer_cache=True):
    """
    Return the appVersion of one chart version and memoize the result.

    The chart cache only holds the version that was current when the lint job ran, so
    it is trusted for the old side of a dashboard line and skipped for the new side.
    """
    key = (dep_name, version)
    if key in resolved:
        return resolved[key]

    app_version = get_cached_app_version(dep_name, version) if prefer_cache else None
    if app_version is None:
        print(f"Fetching metadata for {dep_name}:{version}...")
        app_version = get_app_version(
            dep_name,
            source["registry_url"],
            version,
            source["datasource"],
            source["package_file"],
        )
    resolved[key] = app_version
    return app_version


def is_known_app_version(app_version):
    """
    Tell a concrete appVersion from a missing or floating one.

    appVersion is free-form and often not semantic (2026.11.0, 5.0.0.2941,
    v4.7.4, b12169), so it is never parsed and only compared as a string.
    Floating labels such as "full" or "latest" carry no digit and cannot
    prove that the application stayed the same.
    """
    return bool(app_version) and any(char.isdigit() for char in app_version)


def app_version_marker(line, sources, resolved):
    """
    Build the appVersion annotation for one dashboard line.

    The versions are read from the line itself because a dependency can have several
    pending updates and stale lines can still show an older version than the report.
    Returns an empty string when the line matched but no appVersion delta is known.
    """
    versions = UPDATE_VERSIONS_PATTERN.search(line)
    if versions is None:
        return None

    old_version = versions.group("from")
    new_version = versions.group("to")

    candidates = sorted((name for name in sources if name in line), key=len, reverse=True)
    for dep_name in candidates:
        source = sources[dep_name]
        known_versions = {source["current_version"], *source["new_versions"]}
        if old_version not in known_versions and new_version not in known_versions:
            continue

        old_app = resolve_app_version(dep_name, old_version, source, resolved)
        new_app = resolve_app_version(dep_name, new_version, source, resolved, prefer_cache=False)
        if not (is_known_app_version(old_app) and is_known_app_version(new_app)):
            return ""
        if old_app == new_app:
            return " *(App: Unchanged)*"
        return f" *(App: `{old_app}` &rarr; `{new_app}`)*"

    return None


def main():
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("RENOVATE_GITHUB_APP_TOKEN")
    report_path = sys.argv[1] if len(sys.argv) > 1 else "renovate-report.json"

    if not repo or not token:
        print("Missing GITHUB_REPOSITORY or RENOVATE_GITHUB_APP_TOKEN environment variables")
        sys.exit(1)

    try:
        with open(report_path, "r") as f:
            report = json.load(f)
    except Exception as e:
        print(f"Could not read renovate report: {e}")
        sys.exit(1)

    # Flatten the nested renovate-report.json structure using a list comprehension
    dependencies = [
        (pf.get("packageFile"), dep)
        for repo_data in report.get("repositories", {}).values()
        for files in repo_data.get("packageFiles", {}).values()
        for pf in files
        for dep in pf.get("deps", [])
    ]
    sources = dependency_sources(dependencies)

    if not any(source["new_versions"] for source in sources.values()):
        print("No chart upgrades found that required appVersion patching.")
        sys.exit(0)

    url = f"https://api.github.com/repos/{repo}/issues?state=open"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")

    try:
        with urllib.request.urlopen(req) as response:
            issues = json.loads(response.read())
    except Exception as e:
        print(f"Failed to fetch issues: {e}")
        sys.exit(1)

    dashboard_issue = next((issue for issue in issues if "Dependency Dashboard" in issue.get("title", "")), None)

    if not dashboard_issue:
        print("Dependency Dashboard issue not found.")
        sys.exit(0)

    resolved_app_versions = {}
    new_body_lines = []
    modified = False

    for line in dashboard_issue["body"].split("\n"):
        # Skip lines that aren't list items or table rows
        if not line.strip().startswith("- [ ]") and "|" not in line:
            new_body_lines.append(line)
            continue

        marker = app_version_marker(line, sources, resolved_app_versions)
        if marker is None:
            new_body_lines.append(line)
            continue

        annotated = APP_MARKER_PATTERN.sub("", line).rstrip()
        if marker:
            annotated += marker

        if annotated != line:
            modified = True
        new_body_lines.append(annotated)

    if not modified:
        print("No lines modified. App versions might already be present or no matching charts found in issue.")
        sys.exit(0)

    update_url = f"https://api.github.com/repos/{repo}/issues/{dashboard_issue['number']}"
    patch_data = json.dumps({"body": "\n".join(new_body_lines)}).encode("utf-8")
    patch_req = urllib.request.Request(update_url, data=patch_data, method="PATCH")
    patch_req.add_header("Authorization", f"Bearer {token}")
    patch_req.add_header("Accept", "application/vnd.github.v3+json")

    try:
        with urllib.request.urlopen(patch_req) as response:
            print(f"Successfully updated Dependency Dashboard issue #{dashboard_issue['number']} with appVersions.")
    except Exception as e:
        print(f"Failed to update issue: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
