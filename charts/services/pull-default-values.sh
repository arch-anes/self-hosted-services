#!/usr/bin/env bash
# Fetches upstream default values for all Helm charts under templates/.
# Each template may annotate its values block with "# default-values: <url>" pointing
# to the upstream values.yaml. This script downloads those files into
# default-values/<chart>-<filename>.yaml for local reference.
# Supports GitHub, Codeberg, and OCI registry URLs.
set -euo pipefail

TEMPLATES_DIR="$(dirname "$0")/templates"
OUTPUT_DIR="$(dirname "$0")/default-values"

rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

next_available_filename() {
  local base="$1" ext="$2"
  local filename="${base}.${ext}"
  local count=1
  while [[ -f "$OUTPUT_DIR/$filename" ]]; do
    filename="${base}-${count}.${ext}"
    (( count++ ))
  done
  echo "$filename"
}

fetch() {
  local url="$1" chart="$2"
  local filename

  case "$url" in
    https://github.com/*)
      local raw="${url/github.com/raw.githubusercontent.com}"
      raw="${raw/\/blob\//\/}"
      filename="$(next_available_filename "${chart}-$(basename "$url" .yaml)" yaml)"
      echo "Fetching $url -> $OUTPUT_DIR/$filename"
      curl -fsSL "$raw" -o "$OUTPUT_DIR/$filename"
      ;;
    https://codeberg.org/*)
      local raw="${url/\/src\/branch\//\/raw\/branch\/}"
      filename="$(next_available_filename "${chart}-$(basename "$url" .yaml)" yaml)"
      echo "Fetching $url -> $OUTPUT_DIR/$filename"
      curl -fsSL "$raw" -o "$OUTPUT_DIR/$filename"
      ;;
    oci://*)
      filename="$(next_available_filename "${chart}-$(basename "$url")" yaml)"
      echo "Fetching $url -> $OUTPUT_DIR/$filename"
      helm show values "$url" > "$OUTPUT_DIR/$filename"
      ;;
    *)
      echo "Unsupported URL scheme, skipping: $url"
      ;;
  esac
}

declare -A seen_urls

while IFS= read -r line; do
  file="${line%%:*}"
  url="${line##*# default-values: }"

  [[ -n "${seen_urls[$url]+_}" ]] && continue
  seen_urls[$url]=1

  fetch "$url" "$(basename "${file%.yaml}")"
done < <(grep -r "# default-values:" "$TEMPLATES_DIR" --include="*.yaml")
