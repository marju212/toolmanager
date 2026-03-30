#!/usr/bin/env bash
# teardown.sh — Remove generated workspace artifacts
set -euo pipefail

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${WORKSPACE_DIR}" || "${WORKSPACE_DIR}" != /* ]]; then
    echo "FATAL: WORKSPACE_DIR is empty or not absolute: '${WORKSPACE_DIR}'" >&2
    exit 1
fi

echo "==> Cleaning up workspace"

rm -rf "${WORKSPACE_DIR}/repos" "${WORKSPACE_DIR}/deploy" "${WORKSPACE_DIR}/modulefiles" \
       "${WORKSPACE_DIR}/archives" "${WORKSPACE_DIR}/external"

# Restore tools.json to template form (reset paths and strip versions)
python3 -c "
import json, sys
ws = sys.argv[1]
path = sys.argv[2]
with open(path) as f:
    manifest = json.load(f)
manifest['deploy_base_path'] = '__WORKSPACE__/deploy'
for name, tool in manifest.get('tools', {}).items():
    src = tool.get('source', {})
    if src.get('type') == 'git':
        src['url'] = '__WORKSPACE__/repos/' + name + '.git'
    elif src.get('type') == 'archive':
        src['path'] = '__WORKSPACE__/archives/' + name
    elif src.get('type') == 'external':
        src['path'] = '__WORKSPACE__/external/' + name
    tool.pop('version', None)
    tool.pop('available', None)
with open(path, 'w') as f:
    json.dump(manifest, f, indent=2)
    f.write('\n')
" "${WORKSPACE_DIR}" "${WORKSPACE_DIR}/manifest/tools.json"

# Restore .release.conf to template form
python3 -c "
import sys
ws = sys.argv[1]
path = sys.argv[2]
with open(path) as f:
    data = f.read()
data = data.replace(ws, '__WORKSPACE__')
with open(path, 'w') as f:
    f.write(data)
" "${WORKSPACE_DIR}" "${WORKSPACE_DIR}/manifest/.release.conf"

echo "==> Done. Run ./setup.sh to recreate."
