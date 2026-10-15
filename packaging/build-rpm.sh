#!/bin/sh
# Build Glint's RPM (and SRPM) into packaging/rpmbuild/.
#
#   packaging/build-rpm.sh
#   sudo dnf install packaging/rpmbuild/RPMS/noarch/glint-*.noarch.rpm
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
version=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$root/glint/__init__.py")
top="$root/packaging/rpmbuild"
name="glint-$version"

rm -rf "$top"
mkdir -p "$top/SOURCES" "$top/SPECS"

# Source tarball with a glint-<version>/ prefix, without build junk.
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir "$stage/$name"
tar -C "$root" --exclude='./packaging/rpmbuild' --exclude='__pycache__' --exclude='.ruff_cache' \
    --exclude='.git' -cf - . | tar -C "$stage/$name" -xf -
tar -C "$stage" -czf "$top/SOURCES/$name.tar.gz" "$name"

rpmbuild -ba \
    --define "_topdir $top" \
    --define "glint_version $version" \
    "$root/packaging/glint.spec"

echo
echo "Built:"
find "$top/RPMS" "$top/SRPMS" -name '*.rpm' -printf '  %p\n'
echo
echo "Install with: sudo dnf install $(find "$top/RPMS" -name '*.rpm' | head -1)"
