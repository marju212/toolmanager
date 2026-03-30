#!/usr/bin/env bash
# setup.sh — Initialise the demo workspace
#
# Creates bare git repos with tagged versions and rewrites tools.json
# with absolute paths so deploy.sh can work against local repos.
set -euo pipefail

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOS_DIR="${WORKSPACE_DIR}/repos"
DEPLOY_DIR="${WORKSPACE_DIR}/deploy"
MF_DIR="${WORKSPACE_DIR}/modulefiles"
TOOLS_JSON="${WORKSPACE_DIR}/manifest/tools.json"
RELEASE_CONF="${WORKSPACE_DIR}/manifest/.release.conf"

# Safety: verify critical paths are non-empty and absolute
for _var in WORKSPACE_DIR REPOS_DIR DEPLOY_DIR MF_DIR; do
    eval "_val=\${${_var}}"
    if [[ -z "${_val}" || "${_val}" != /* ]]; then
        echo "FATAL: ${_var} is empty or not absolute: '${_val}'" >&2
        exit 1
    fi
done

echo "==> Setting up workspace in ${WORKSPACE_DIR}"

# ── Temp directory cleanup on exit/signal ─────────────────────────
_TMP_DIRS=()
cleanup_tmp() {
    for d in "${_TMP_DIRS[@]}"; do
        [[ -d "$d" ]] && rm -rf "$d"
    done
}
trap cleanup_tmp EXIT

# ── Clean previous run ────────────────────────────────────────────
rm -rf "${REPOS_DIR}" "${DEPLOY_DIR}" "${MF_DIR}"
mkdir -p "${REPOS_DIR}" "${DEPLOY_DIR}" "${MF_DIR}"

# ── Helper: create a bare repo from staged commits/tags ───────────
create_repo() {
    local name="$1"
    local src_dir="${WORKSPACE_DIR}/tool-repos/${name}"
    local tmp_dir
    tmp_dir="$(mktemp -d)"
    _TMP_DIRS+=("${tmp_dir}")
    local bare_dir="${REPOS_DIR}/${name}.git"

    echo "--> Building repo: ${name}" >&2

    git -C "${tmp_dir}" init --initial-branch=main -q
    git -C "${tmp_dir}" config user.email "demo@example.com"
    git -C "${tmp_dir}" config user.name  "Demo User"

    # Return the tmp dir so callers can add commits
    echo "${tmp_dir}"
}

finalise_repo() {
    local name="$1"
    local tmp_dir="$2"
    local bare_dir="${REPOS_DIR}/${name}.git"

    git clone --bare -q "${tmp_dir}" "${bare_dir}"
    rm -rf "${tmp_dir}"
    echo "    Bare repo: ${bare_dir}"
}

# ── hello-cli ─────────────────────────────────────────────────────
tmp="$(create_repo hello-cli)"

# v1.0.0 — basic hello world
cp -r "${WORKSPACE_DIR}/tool-repos/hello-cli/"* "${tmp}/"
# Rewrite to v1.0.0 (basic: no --name, no --greeting)
cat > "${tmp}/bin/hello" << 'SCRIPT'
#!/usr/bin/env bash
# hello-cli: A friendly greeting tool
VERSION="1.0.0"

if [[ "${1:-}" == "--version" ]]; then
    echo "hello-cli $VERSION"
    exit 0
fi
if [[ "${1:-}" == "--help" ]]; then
    echo "Usage: hello [--version] [--help]"
    exit 0
fi

echo "Hello, World!"
SCRIPT
chmod +x "${tmp}/bin/hello"
git -C "${tmp}" add -A && git -C "${tmp}" commit -q -m "Initial release"
git -C "${tmp}" tag v1.0.0

# v1.1.0 — add --name flag
cat > "${tmp}/bin/hello" << 'SCRIPT'
#!/usr/bin/env bash
# hello-cli: A friendly greeting tool
VERSION="1.1.0"
NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)    NAME="$2"; shift 2 ;;
        --version) echo "hello-cli $VERSION"; exit 0 ;;
        --help)    echo "Usage: hello [--name NAME] [--version]"; exit 0 ;;
        *)         echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -n "$NAME" ]]; then
    echo "Hello, $NAME!"
else
    echo "Hello, World!"
fi
SCRIPT
git -C "${tmp}" add -A && git -C "${tmp}" commit -q -m "Add --name flag"
git -C "${tmp}" tag v1.1.0

# v2.0.0 — add --greeting flag (breaking: output format changes)
cat > "${tmp}/bin/hello" << 'SCRIPT'
#!/usr/bin/env bash
# hello-cli: A friendly greeting tool
VERSION="2.0.0"
NAME=""
GREETING="Hello"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)     NAME="$2"; shift 2 ;;
        --greeting) GREETING="$2"; shift 2 ;;
        --version)  echo "hello-cli $VERSION"; exit 0 ;;
        --help)     echo "Usage: hello [--name NAME] [--greeting GREETING] [--version]"; exit 0 ;;
        *)          echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -n "$NAME" ]]; then
    echo "$GREETING, $NAME!"
else
    echo "$GREETING, World!"
fi
SCRIPT
git -C "${tmp}" add -A && git -C "${tmp}" commit -q -m "Add --greeting flag (breaking change)"
git -C "${tmp}" tag v2.0.0

finalise_repo hello-cli "${tmp}"

# ── calculator ────────────────────────────────────────────────────
tmp="$(create_repo calculator)"

# v1.0.0 — basic add/subtract
mkdir -p "${tmp}/bin" "${tmp}/lib"
cp "${WORKSPACE_DIR}/tool-repos/calculator/bin/calc" "${tmp}/bin/calc"
chmod +x "${tmp}/bin/calc"
cat > "${tmp}/lib/calculator.py" << 'PYEOF'
#!/usr/bin/env python3
"""calculator: A simple math tool."""
import sys

VERSION = "1.0.0"


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


OPERATIONS = {"add": add, "sub": subtract}


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--help":
        print(f"calculator {VERSION}")
        print("Usage: calc <operation> <a> <b>")
        print(f"Operations: {', '.join(OPERATIONS)}")
        sys.exit(0)
    if sys.argv[1] == "--version":
        print(f"calculator {VERSION}")
        sys.exit(0)
    if len(sys.argv) != 4:
        print("Usage: calc <operation> <a> <b>", file=sys.stderr)
        sys.exit(1)
    op, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    if op not in OPERATIONS:
        print(f"Unknown operation: {op}", file=sys.stderr)
        sys.exit(1)
    result = OPERATIONS[op](a, b)
    if result is not None:
        print(int(result) if result == int(result) else result)


if __name__ == "__main__":
    main()
PYEOF
git -C "${tmp}" add -A && git -C "${tmp}" commit -q -m "Initial release: add and subtract"
git -C "${tmp}" tag v1.0.0

# v1.0.1 — bug fix: handle negative numbers in output
cat > "${tmp}/lib/calculator.py" << 'PYEOF'
#!/usr/bin/env python3
"""calculator: A simple math tool."""
import sys

VERSION = "1.0.1"


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


OPERATIONS = {"add": add, "sub": subtract}


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--help":
        print(f"calculator {VERSION}")
        print("Usage: calc <operation> <a> <b>")
        print(f"Operations: {', '.join(OPERATIONS)}")
        sys.exit(0)
    if sys.argv[1] == "--version":
        print(f"calculator {VERSION}")
        sys.exit(0)
    if len(sys.argv) != 4:
        print("Usage: calc <operation> <a> <b>", file=sys.stderr)
        sys.exit(1)
    try:
        op, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    except ValueError:
        print("Error: arguments must be numbers", file=sys.stderr)
        sys.exit(1)
    if op not in OPERATIONS:
        print(f"Unknown operation: {op}", file=sys.stderr)
        sys.exit(1)
    result = OPERATIONS[op](a, b)
    if result is not None:
        print(int(result) if result == int(result) else result)


if __name__ == "__main__":
    main()
PYEOF
git -C "${tmp}" add -A && git -C "${tmp}" commit -q -m "Fix: validate numeric arguments"
git -C "${tmp}" tag v1.0.1

# v1.1.0 — add multiply/divide
cat > "${tmp}/lib/calculator.py" << 'PYEOF'
#!/usr/bin/env python3
"""calculator: A simple math tool."""
import sys

VERSION = "1.1.0"


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        print("Error: division by zero", file=sys.stderr)
        return None
    return a / b


OPERATIONS = {
    "add": add,
    "sub": subtract,
    "mul": multiply,
    "div": divide,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--help":
        print(f"calculator {VERSION}")
        print("Usage: calc <operation> <a> <b>")
        print(f"Operations: {', '.join(OPERATIONS)}")
        sys.exit(0)
    if sys.argv[1] == "--version":
        print(f"calculator {VERSION}")
        sys.exit(0)
    if len(sys.argv) != 4:
        print("Usage: calc <operation> <a> <b>", file=sys.stderr)
        sys.exit(1)
    try:
        op, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    except ValueError:
        print("Error: arguments must be numbers", file=sys.stderr)
        sys.exit(1)
    if op not in OPERATIONS:
        print(f"Unknown operation: {op}", file=sys.stderr)
        sys.exit(1)
    result = OPERATIONS[op](a, b)
    if result is not None:
        print(int(result) if result == int(result) else result)


if __name__ == "__main__":
    main()
PYEOF
git -C "${tmp}" add -A && git -C "${tmp}" commit -q -m "Add multiply and divide operations"
git -C "${tmp}" tag v1.1.0

finalise_repo calculator "${tmp}"

# ── text-tools (archive source) ──────────────────────────────────
# Simulates a tool distributed as tar.gz archives on a shared disk.
ARCHIVE_DIR="${WORKSPACE_DIR}/archives/text-tools"
mkdir -p "${ARCHIVE_DIR}"

for version in "1.0.0" "1.1.0"; do
    staging="$(mktemp -d)"
    _TMP_DIRS+=("${staging}")
    mkdir -p "${staging}/text-tools-${version}/bin"
    cat > "${staging}/text-tools-${version}/bin/wordcount" << SCRIPT
#!/usr/bin/env bash
VERSION="${version}"
if [[ "\${1:-}" == "--version" ]]; then
    echo "text-tools/wordcount \$VERSION"
    exit 0
fi
if [[ -n "\${1:-}" && -f "\$1" ]]; then
    wc -w < "\$1"
else
    wc -w
fi
SCRIPT
    chmod +x "${staging}/text-tools-${version}/bin/wordcount"
    ver_dir="${ARCHIVE_DIR}/${version}"
    mkdir -p "${ver_dir}"
    tar -czf "${ver_dir}/text-tools-${version}.tar.gz" \
        -C "${staging}" "text-tools-${version}"
    rm -rf "${staging}"
done
echo "--> Archive source: ${ARCHIVE_DIR}"

# ── json-viewer (external source) ────────────────────────────────
# Simulates a tool installed externally (e.g. by IT), not managed by us.
EXTERNAL_DIR="${WORKSPACE_DIR}/external/json-viewer"

for version in "1.0.0" "2.0.0"; do
    ver_dir="${EXTERNAL_DIR}/${version}"
    mkdir -p "${ver_dir}/bin"
    cat > "${ver_dir}/bin/jview" << PYEOF
#!/usr/bin/env python3
"""json-viewer: Pretty-print JSON from stdin or file."""
import json, sys

VERSION = "${version}"

if len(sys.argv) > 1 and sys.argv[1] == "--version":
    print(f"json-viewer {VERSION}")
    sys.exit(0)

if len(sys.argv) > 1 and sys.argv[1] != "-":
    with open(sys.argv[1]) as f:
        data = json.load(f)
else:
    data = json.load(sys.stdin)

print(json.dumps(data, indent=2))
PYEOF
    chmod +x "${ver_dir}/bin/jview"
done
echo "--> External source: ${EXTERNAL_DIR}"

# ── Rewrite __WORKSPACE__ placeholders with absolute paths ────────
for conf_file in "${TOOLS_JSON}" "${RELEASE_CONF}"; do
    python3 -c "
import sys
ws = sys.argv[1]
with open(sys.argv[2]) as f:
    data = f.read()
data = data.replace('__WORKSPACE__', ws)
with open(sys.argv[2], 'w') as f:
    f.write(data)
" "${WORKSPACE_DIR}" "${conf_file}"
done
chmod 600 "${RELEASE_CONF}"

# ── Deploy initial versions ───────────────────────────────────────
DEPLOY="${WORKSPACE_DIR}/../../scripts/deploy.sh"
DEPLOY_OPTS="--manifest ${TOOLS_JSON} --config ${RELEASE_CONF} -n"

echo ""
echo "--> Deploying initial versions..."
${DEPLOY} deploy hello-cli   --version 1.0.0 ${DEPLOY_OPTS}
${DEPLOY} deploy calculator  --version 1.0.0 ${DEPLOY_OPTS}
${DEPLOY} deploy text-tools  --version 1.0.0 ${DEPLOY_OPTS}
${DEPLOY} deploy json-viewer --version 1.0.0 ${DEPLOY_OPTS} --force

echo ""
echo "==> Workspace ready!"
echo ""
echo "Directory layout:"
echo "  repos/hello-cli.git    — bare repo (tags: v1.0.0, v1.1.0, v2.0.0)"
echo "  repos/calculator.git   — bare repo (tags: v1.0.0, v1.0.1, v1.1.0)"
echo "  deploy/                — deploy target (tool installs)"
echo "  modulefiles/           — modulefile target (separate from deploy)"
echo "  manifest/tools.json    — manifest (paths resolved)"
echo ""
echo "Try these commands (from this directory):"
echo ""
echo "  # Scan for available versions"
echo "  ../../scripts/deploy.sh scan --manifest manifest/tools.json --config manifest/.release.conf -n"
echo ""
echo "  # Deploy hello-cli v1.0.0"
echo "  ../../scripts/deploy.sh deploy hello-cli --version 1.0.0 --manifest manifest/tools.json --config manifest/.release.conf -n"
echo ""
echo "  # Upgrade hello-cli to latest"
echo "  ../../scripts/deploy.sh upgrade hello-cli --manifest manifest/tools.json --config manifest/.release.conf -n"
echo ""
echo "  # Deploy calculator v1.1.0"
echo "  ../../scripts/deploy.sh deploy calculator --version 1.1.0 --manifest manifest/tools.json --config manifest/.release.conf -n"
echo ""
echo "  # Create toolset modulefile"
echo "  ../../scripts/deploy.sh toolset demo-suite --version 1.0.0 --manifest manifest/tools.json --config manifest/.release.conf -n"
echo ""
echo "  # Add --dry-run to any command to preview without making changes"
