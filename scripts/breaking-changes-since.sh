#!/usr/bin/env bash
#
# breaking-changes-since.sh
#
# Lists breaking changes since a specified commit hash.
# Matches any commit whose title or description contains "breaking change" (case-insensitive).
#

# Exit on error, undefined variables, and pipe failures
set -euo pipefail

usage() {
    echo "Usage: $0 [options] <commit-hash> [until-revision]"
    echo ""
    echo "Arguments:"
    echo "  <commit-hash>       The starting commit hash (exclusive)."
    echo "  [until-revision]    The ending commit or branch (inclusive). Defaults to HEAD."
    echo ""
    echo "Options:"
    echo "  -m, --markdown      Format the output as Markdown (useful for release notes/changelogs)."
    echo "  -h, --help          Show this help message and exit."
    echo ""
    echo "Example:"
    echo "  $0 36ff4f51"
    echo "  $0 --markdown 36ff4f51 dev"
    exit 1
}

FORMAT="text"
START_COMMIT=""
END_COMMIT="HEAD"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--markdown)
            FORMAT="markdown"
            shift
            ;;
        -h|--help)
            usage
            ;;
        -*)
            echo "Error: Unknown option '$1'" >&2
            usage
            ;;
        *)
            if [ -z "$START_COMMIT" ]; then
                START_COMMIT="$1"
            elif [ "$END_COMMIT" = "HEAD" ]; then
                END_COMMIT="$1"
            else
                echo "Error: Unexpected argument '$1'" >&2
                usage
            fi
            shift
            ;;
    esac
done

if [ -z "$START_COMMIT" ]; then
    echo "Error: Missing starting commit hash." >&2
    usage
fi

# Ensure we are inside a Git repository
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Error: Not in a git repository." >&2
    exit 1
fi

# Validate starting commit
if ! git rev-parse --verify "$START_COMMIT" >/dev/null 2>&1; then
    echo "Error: Invalid starting commit '$START_COMMIT'." >&2
    exit 1
fi

# Validate ending revision
if ! git rev-parse --verify "$END_COMMIT" >/dev/null 2>&1; then
    echo "Error: Invalid ending revision '$END_COMMIT'." >&2
    exit 1
fi

# Retrieve matching commit hashes first to avoid delimiter/splitting bugs
hashes=$(git log "${START_COMMIT}..${END_COMMIT}" \
    --grep="breaking change" \
    -i \
    --reverse \
    --format="%H")

if [ -z "$hashes" ]; then
    if [ "$FORMAT" = "markdown" ]; then
        echo "No breaking changes found between \`$START_COMMIT\` and \`$END_COMMIT\`."
    else
        echo "No breaking changes found between $START_COMMIT and $END_COMMIT."
    fi
    exit 0
fi

if [ "$FORMAT" = "markdown" ]; then
    echo "## Breaking Changes ($START_COMMIT..$END_COMMIT)"
    echo ""
    echo "| Commit | Author | Date | Description |"
    echo "| :--- | :--- | :--- | :--- |"
    
    # Process each hash
    while read -r hash; do
        [ -z "$hash" ] && continue
        short_hash="${hash:0:8}"
        author=$(git log -1 --format="%an" "$hash")
        date=$(git log -1 --format="%ad" --date=short "$hash")
        subject=$(git log -1 --format="%s" "$hash")
        
        # Escape pipe characters for markdown table syntax safety
        safe_author="${author//|/\\|}"
        safe_subject="${subject//|/\\|}"
        
        echo "| \`$short_hash\` | $safe_author | $date | $safe_subject |"
    done <<< "$hashes"
else
    echo "Breaking changes since $START_COMMIT (up to $END_COMMIT):"
    echo "================================================================================"
    
    first=true
    while read -r hash; do
        [ -z "$hash" ] && continue
        if [ "$first" = true ]; then
            first=false
        else
            echo "--------------------------------------------------------------------------------"
        fi
        
        short_hash="${hash:0:8}"
        author=$(git log -1 --format="%an <%ae>" "$hash")
        date=$(git log -1 --format="%ad" --date=default "$hash")
        subject=$(git log -1 --format="%s" "$hash")
        body=$(git log -1 --format="%b" "$hash")
        
        echo "Commit:  $short_hash ($hash)"
        echo "Author:  $author"
        echo "Date:    $date"
        echo "Subject: $subject"
        if [ -n "$body" ]; then
            echo ""
            echo "Details:"
            # shellcheck disable=SC2001
            echo "$body" | sed 's/^/  /'
        fi
    done <<< "$hashes"
fi
