import os
import json
import urllib.request
import sys
import subprocess

STRIP_CHARS = ' \t\n\r"\''

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
        return "Unknown"
        
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
        return "Unknown"
    
    # Parse the YAML output for appVersion
    for line in result.stdout.split('\n'):
        if line.startswith("appVersion:"):
            return line.split(':', 1)[1].strip(STRIP_CHARS)
            
    return "Unknown"

def main():
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    report_path = sys.argv[1] if len(sys.argv) > 1 else "renovate-report.json"
    
    if not repo or not token:
        print("Missing GITHUB_REPOSITORY or GITHUB_TOKEN environment variables")
        sys.exit(1)

    try:
        with open(report_path, "r") as f:
            report = json.load(f)
    except Exception as e:
        print(f"Could not read renovate report: {e}")
        sys.exit(1)

    app_versions = {}
    
    # Flatten the nested renovate-report.json structure using a list comprehension
    dependencies = [
        (pf.get("packageFile"), dep)
        for repo_data in report.get("repositories", {}).values()
        for files in repo_data.get("packageFiles", {}).values()
        for pf in files
        for dep in pf.get("deps", [])
    ]
    
    for package_file, dep in dependencies:
        updates = dep.get("updates")
        if not updates:
            continue
        
        dep_name = dep.get("depName")
        current_version = dep.get("currentValue")
        new_version = updates[0].get("newValue")
        registry_url = dep.get("registryUrl")
        datasource = dep.get("datasource")
        
        if not (dep_name and current_version and new_version and registry_url):
            continue
            
        if datasource not in ["helm", "docker"]:
            continue
            
        print(f"Fetching metadata for {dep_name}...")
        
        curr_app = get_app_version(dep_name, registry_url, current_version, datasource, package_file)
        new_app = get_app_version(dep_name, registry_url, new_version, datasource, package_file)
        
        if curr_app == "Unknown" and new_app == "Unknown":
            continue
            
        app_versions[dep_name] = f" *(App: `{curr_app}` &rarr; `{new_app}`)*"

    if not app_versions:
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

    new_body_lines = []
    modified = False
    
    for line in dashboard_issue["body"].split('\n'):
        # Skip lines that aren't list items or table rows
        if not line.strip().startswith("- [ ]") and "|" not in line:
            new_body_lines.append(line)
            continue
            
        for dep_name, app_str in app_versions.items():
            if dep_name not in line or app_str in line:
                continue
                
            line = line.rstrip() + app_str
            modified = True
            break
                
        new_body_lines.append(line)

    if not modified:
        print("No lines modified. App versions might already be present or no matching charts found in issue.")
        sys.exit(0)

    update_url = f"https://api.github.com/repos/{repo}/issues/{dashboard_issue['number']}"
    patch_data = json.dumps({"body": '\n'.join(new_body_lines)}).encode('utf-8')
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
