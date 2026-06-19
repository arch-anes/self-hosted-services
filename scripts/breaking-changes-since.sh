#!/usr/bin/env bash
#
# breaking-changes-since.sh
#
# Lists breaking changes since a specified commit hash.
# Matches any commit whose title or description contains "breaking change" (case-insensitive).
#

# Exit on error, undefined variables, and pipe failures
set -euo pipefail

# ASCII control characters used as field/record separators so that commit
# messages (which may contain any printable character, including newlines) can
# be parsed unambiguously from a single `git log` invocation.
FS=$'\x1f'   # Unit Separator   - between fields within a commit
RS=$'\x1e'   # Record Separator - between commits

# Split $1 on $FS into the global array FIELDS.
# The final field keeps everything after the last separator, so multi-line
# values (such as a commit body) are preserved intact.
split_fields() {
    local s="$1"
    FIELDS=()
    while [[ "$s" == *"${FS}"* ]]; do
        FIELDS+=("${s%%"${FS}"*}")
        s="${s#*"${FS}"}"
    done
    FIELDS+=("$s")
}

# Strip every trailing newline from $1 (mimics what `$(...)` does), so that
# piping a body through `sed 's/^/  /'` does not emit a trailing blank line.
strip_trailing_newlines() {
    local s="$1"
    while [[ "$s" == *$'\n' ]]; do s="${s%$'\n'}"; done
    printf '%s' "$s"
}

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
if ! git rev-parse --verify "$START_COMMIT^{}" >/dev/null 2>&1; then
    echo "Error: Invalid starting commit '$START_COMMIT'." >&2
    exit 1
fi

# Validate ending revision
if ! git rev-parse --verify "$END_COMMIT^{}" >/dev/null 2>&1; then
    echo "Error: Invalid ending revision '$END_COMMIT'." >&2
    exit 1
fi

# Pull every field we need in a single pass. Using one `git log` call (instead
# of one `git log -1` per commit) avoids spawning dozens of subprocesses when
# many commits match.
if [ "$FORMAT" = "markdown" ]; then
    date_opt="short"
    fmt="%H${FS}%an${FS}%ad${FS}%s${RS}"
else
    date_opt="default"
    fmt="%H${FS}%an${FS}%ae${FS}%ad${FS}%s${FS}%b${RS}"
fi

raw=$(git log "${START_COMMIT}..${END_COMMIT}" \
    --grep="breaking change" \
    -i \
    --reverse \
    --date="$date_opt" \
    --format="$fmt")

if [ -z "$raw" ]; then
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

    while IFS= read -r -d "$RS" record; do
        [ -z "$record" ] && continue
        # Drop the newline that `git log --format` appends after every entry.
        record="${record#$'\n'}"
        split_fields "$record"

        hash="${FIELDS[0]}"
        author="${FIELDS[1]}"
        date="${FIELDS[2]}"
        subject="${FIELDS[3]}"

        short_hash="${hash:0:8}"

        # Escape pipe characters for markdown table syntax safety
        safe_author="${author//|/\\|}"
        safe_subject="${subject//|/\\|}"

        echo "| \`$short_hash\` | $safe_author | $date | $safe_subject |"
    done <<< "$raw"
else
    echo "Breaking changes since $START_COMMIT (up to $END_COMMIT):"
    echo "================================================================================"

    first=true
    while IFS= read -r -d "$RS" record; do
        [ -z "$record" ] && continue
        record="${record#$'\n'}"
        split_fields "$record"

        hash="${FIELDS[0]}"
        author="${FIELDS[1]}"
        email="${FIELDS[2]}"
        date="${FIELDS[3]}"
        subject="${FIELDS[4]}"
        body="$(strip_trailing_newlines "${FIELDS[5]:-}")"

        if [ "$first" = true ]; then
            first=false
        else
            echo "--------------------------------------------------------------------------------"
        fi

        short_hash="${hash:0:8}"
        echo "Commit:  $short_hash ($hash)"
        echo "Author:  $author <$email>"
        echo "Date:    $date"
        echo "Subject: $subject"
        if [ -n "$body" ]; then
            echo ""
            echo "Details:"
            # shellcheck disable=SC2001
            echo "$body" | sed 's/^/  /'
        fi
    done <<< "$raw"
fi
