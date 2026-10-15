# Copr "make srpm" entry point:
#   make -f .copr/Makefile srpm outdir=<dir> [spec=<path>]
# Copr runs this in a fresh Fedora chroot with the repository checked out.
root := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

srpm:
	dnf -y install rpm-build tar gzip sed git findutils
	git config --global --add safe.directory '*' || true
	$(root)/packaging/build-rpm.sh --srpm $(outdir)
