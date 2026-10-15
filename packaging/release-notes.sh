#!/bin/sh
# Print GitHub release notes for a version: its CHANGELOG.md section plus
# install instructions.  Usage: packaging/release-notes.sh 0.2.0 [rpm-file-name]
set -eu

version=$1
rpm=${2:-glint-$version-1.noarch.rpm}
root=$(cd "$(dirname "$0")/.." && pwd)

notes=$(awk -v v="$version" '
    $0 ~ "^## \\[" v "\\]" { on = 1; next }
    on && /^## \[/        { exit }
    on && /^\[[^]]+\]: /  { exit }
    on                    { print }
' "$root/CHANGELOG.md")

if [ -z "$(printf '%s' "$notes" | tr -d '[:space:]')" ]; then
    echo "No CHANGELOG.md entry for $version" >&2
    exit 1
fi

cat <<EOF
$notes

### Install

Download **\`$rpm\`** below, then:

\`\`\`sh
sudo dnf install ./$rpm
\`\`\`

Built and tested on Fedora 45 with KDE Plasma 6 (Wayland); it should also
install on other recent Fedora releases. Before upgrading or removing an older
copy installed with \`./install.sh\`, run \`./install.sh --uninstall\` first.
Checksums are in \`SHA256SUMS\`.

See the [README](https://github.com/baldybuilds/glint#readme) for what to
expect and how to get started.
EOF
