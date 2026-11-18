#!/usr/bin/env bash
# Build the installable plugin zip: dist/gisdeploy-plugin-<version>.zip
# (install in QGIS via Plugins → Manage and Install Plugins → Install from ZIP)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(sed -n 's/^version=//p' "$here/gisdeploy/metadata.txt" | tr -d '[:space:]')"
[ -n "$version" ] || { echo "version missing from metadata.txt" >&2; exit 1; }
[[ "$version" =~ ^[0-9][a-zA-Z0-9._-]*$ ]] || { echo "invalid plugin version" >&2; exit 1; }
# plugins.qgis.org rejects packages without a LICENSE file (GPLv2-or-later compatible).
[ -f "$here/gisdeploy/LICENSE" ] || { echo "gisdeploy/LICENSE missing" >&2; exit 1; }

out="$here/dist"
mkdir -p "$out"
zip_path="$out/gisdeploy-plugin-$version.zip"
rm -f "$zip_path"

cd "$here"
# Include only installable plugin sources and assets: code, icons, metadata, the
# license and compiled translations (.qm; the .ts sources stay out). Keep local
# caches, editor backups, credentials and test output out of the public download.
find gisdeploy -type f \( -name '*.py' -o -name '*.svg' -o -name '*.ui' -o -path 'gisdeploy/metadata.txt' \
  -o -path 'gisdeploy/LICENSE' -o -path 'gisdeploy/i18n/*.qm' \) \
  ! -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z | xargs -0 zip -X "$zip_path" >/dev/null

echo "$zip_path"
