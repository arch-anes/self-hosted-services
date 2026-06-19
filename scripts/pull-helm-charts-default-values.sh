#!/usr/bin/env bash
# Fetches upstream default values for all Helm charts under templates/.
# Each template may annotate its values block with "# default-values: <url>" pointing
# to the upstream values.yaml. This script downloads those files into
# default-values/<chart>-<filename>.yaml for local reference.
# Supports GitHub, Codeberg, and OCI registry URLs.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || dirname "$(dirname "$0")")"
TEMPLATES_DIR="${REPO_ROOT}/charts/services/templates"
OUTPUT_DIR="${REPO_ROOT}/charts/services/default-values"

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Fetches upstream default values.yaml for every Helm chart in
${TEMPLATES_DIR} that is annotated with "# default-values: <url>".
Results are written to ${OUTPUT_DIR}/.

Fetches happen in a staging directory; OUTPUT_DIR is only replaced after
all fetches have been attempted, so a transient network failure can no
longer wipe previously-cached values.

Options:
  -h, --help    Show this help message and exit.
EOF
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage 0 ;;
        *) echo "Error: Unknown argument '$1'" >&2; usage 1 ;;
    esac
    # shellcheck disable=SC2317 # reachable if a non-exiting case is added later
    shift
done

# Resolve a non-colliding filename inside the staging directory.
next_available_filename() {
  local base="$1" ext="$2"
  local filename="${base}.${ext}"
  local count=1
  while [[ -f "$STAGING_DIR/$filename" ]]; do
    filename="${base}-${count}.${ext}"
    count=$((count + 1))
  done
  echo "$filename"
}

# Fetch a single URL into the staging directory.
# Echoes the filename written on success; returns non-zero on failure.
fetch() {
  local url="$1" chart="$2"
  local raw filename out tool

  # Translate each supported URL into (raw URL, output filename, fetch tool).
  case "$url" in
    https://github.com/*)
      raw="${url/github.com/raw.githubusercontent.com}"
      raw="${raw/\/blob\//\/}"
      filename="$(next_available_filename "${chart}-$(basename "$url" .yaml)" yaml)"
      tool="curl"
      ;;
    https://codeberg.org/*)
      raw="${url/\/src\/branch\//\/raw\/branch\/}"
      filename="$(next_available_filename "${chart}-$(basename "$url" .yaml)" yaml)"
      tool="curl"
      ;;
    oci://*)
      raw="$url"
      filename="$(next_available_filename "${chart}-$(basename "$url")" yaml)"
      tool="helm"
      ;;
    *)
      echo "  ERROR: unsupported URL scheme, skipping: $url" >&2
      return 1
      ;;
  esac

  out="$STAGING_DIR/$filename"
  echo "  fetching $url" >&2
  if [ "$tool" = "helm" ]; then
    if ! helm show values "$raw" > "$out" 2>/dev/null; then
      rm -f "$out"
      echo "  ERROR: failed to fetch $url" >&2
      return 1
    fi
  else
    if ! curl -fsSL "$raw" -o "$out" 2>/dev/null; then
      echo "  ERROR: failed to fetch $url" >&2
      return 1
    fi
  fi
  echo "$filename"
}

# Stage every fetch in a scratch directory so OUTPUT_DIR is never left in a
# half-deleted state if something fails (or the user hits Ctrl-C).
STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/helm-default-values.XXXXXX")"
# shellcheck disable=SC2329 # invoked via EXIT trap below
cleanup() { rm -rf "$STAGING_DIR"; }
trap cleanup EXIT

mkdir -p "$STAGING_DIR"

declare -A seen_urls=()
total=0
failures=0

while IFS= read -r line; do
  file="${line%%:*}"
  url="${line##*# default-values: }"

  [[ -n "${seen_urls[$url]+_}" ]] && continue
  seen_urls[$url]=1
  total=$((total + 1))

  chart="$(basename "${file%.yaml}")"
  if ! fetch "$url" "$chart" >/dev/null; then
      failures=$((failures + 1))
  fi
done < <(grep -r "# default-values:" "$TEMPLATES_DIR" --include="*.yaml")

if (( total == 0 )); then
  echo "No '# default-values:' annotations found in $TEMPLATES_DIR."
  exit 0
fi

# Atomically replace OUTPUT_DIR with the freshly-fetched staging directory.
rm -rf "$OUTPUT_DIR"
mv "$STAGING_DIR" "$OUTPUT_DIR"
trap - EXIT   # staging dir no longer exists; disable trap

{
  echo ""
  echo "Fetched $(( total - failures ))/$total files into ${OUTPUT_DIR#"$REPO_ROOT"/}"
  if (( failures > 0 )); then
    echo "WARNING: $failures fetch(es) failed. OUTPUT_DIR was refreshed with the"
    echo "         successful fetches only; previously-cached files for failed"
    echo "         URLs are no longer present."
  fi
} >&2

exit $(( failures > 0 ? 1 : 0 ))
