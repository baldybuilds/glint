#!/bin/sh
# Build Glint's RPM (and SRPM) into packaging/rpmbuild/.
#
#   packaging/build-rpm.sh                 build RPM + SRPM (runs the tests)
#   packaging/build-rpm.sh --srpm DIR      only build the SRPM into DIR (used by Copr)
#   sudo dnf install packaging/rpmbuild/RPMS/noarch/glint-*.noarch.rpm
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
version=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$root/glint/__init__.py")
top="$root/packaging/rpmbuild"
name="glint-$version"
srpm_dir=""
if [ "${1:-}" = "--srpm" ]; then
    srpm_dir=${2:?usage: build-rpm.sh --srpm OUTDIR}
fi

rm -rf "$top"
mkdir -p "$top/SOURCES" "$top/SPECS"

# Source tarball with a glint-<version>/ prefix. Inside a git checkout only
# tracked (and new, non-ignored) files are packed, so ignored private files
# never end up in a source package.
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir "$stage/$name"
if command -v git >/dev/null 2>&1 && git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    (cd "$root" && git ls-files -z --cached --others --exclude-standard | xargs -0 tar -cf - --) \
        | tar -C "$stage/$name" -xf -
else
    tar -C "$root" --exclude='./packaging/rpmbuild' --exclude='__pycache__' --exclude='.ruff_cache' \
        --exclude='.git' --exclude='./docs/reverse-engineering.md' -cf - . | tar -C "$stage/$name" -xf -
fi
tar -C "$stage" -czf "$top/SOURCES/$name.tar.gz" "$name"

if [ -n "$srpm_dir" ]; then
    mkdir -p "$srpm_dir"
    rpmbuild -bs \
        --define "_topdir $top" \
        --define "_srcrpmdir $srpm_dir" \
        --define "glint_version $version" \
        "$root/packaging/glint.spec"
    exit 0
fi

rpmbuild -ba \
    --define "_topdir $top" \
    --define "glint_version $version" \
    "$root/packaging/glint.spec"

echo
echo "Built:"
find "$top/RPMS" "$top/SRPMS" -name '*.rpm' -printf '  %p\n'
echo
echo "Install with: sudo dnf install $(find "$top/RPMS" -name '*.rpm' | head -1)"
