#!/bin/sh
# soap installer — downloads a release binary, verifies its checksum, installs it.
#
#   curl -fsSL https://raw.githubusercontent.com/GhifariArsa/soap/main/install.sh | sh
#
# Env knobs:
#   SOAP_VERSION      pin a release tag (e.g. v0.1.0); default: latest release
#   SOAP_INSTALL_DIR  install directory; default: $HOME/.local/bin
#
# Requires a published GitHub Release with the soap-<target> assets. If you would
# rather install from PyPI:  uv tool install soap-tui
set -eu

REPO="GhifariArsa/soap"
BIN_DIR="${SOAP_INSTALL_DIR:-$HOME/.local/bin}"

err() {
	echo "soap-install: $*" >&2
}

# --- Detect target -----------------------------------------------------------
os=$(uname -s)
arch=$(uname -m)
target=""
case "$os" in
Darwin)
	case "$arch" in
	arm64) target="macos-arm64" ;;
	esac
	;;
Linux)
	case "$arch" in
	aarch64 | arm64) target="linux-arm64" ;;
	x86_64) target="linux-x86_64" ;;
	esac
	;;
esac

if [ -z "$target" ]; then
	err "unsupported platform: ${os} ${arch}."
	err "Install from PyPI instead:  uv tool install soap-tui"
	exit 1
fi

# --- Tooling check -----------------------------------------------------------
if ! command -v curl >/dev/null 2>&1; then
	err "curl is required but was not found on PATH."
	exit 1
fi

# --- Resolve version ---------------------------------------------------------
version="${SOAP_VERSION:-}"
if [ -z "$version" ]; then
	version=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" |
		sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1)
fi

if [ -z "$version" ]; then
	err "could not determine the latest release version."
	err "Set SOAP_VERSION=<tag> explicitly, or check that a release exists."
	exit 1
fi

asset="soap-${target}"
base="https://github.com/${REPO}/releases/download/${version}"

# --- Download + verify in a temp workspace -----------------------------------
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT HUP TERM

echo "Downloading ${asset} (${version})..."
if ! curl -fsSL "${base}/${asset}" -o "${tmp}/${asset}"; then
	err "failed to download ${base}/${asset}"
	err "Verify that release ${version} publishes an asset named ${asset}."
	exit 1
fi
if ! curl -fsSL "${base}/${asset}.sha256" -o "${tmp}/${asset}.sha256"; then
	err "failed to download the checksum ${base}/${asset}.sha256"
	exit 1
fi

echo "Verifying checksum..."
# The .sha256 manifest names the bare asset filename, so verify from inside the
# temp dir where that file lives. Prefer shasum (BSD/macOS), fall back to
# sha256sum (GNU/Linux).
if command -v shasum >/dev/null 2>&1; then
	if ! (cd "$tmp" && shasum -a 256 -c "${asset}.sha256" >/dev/null 2>&1); then
		err "checksum verification failed — refusing to install."
		exit 1
	fi
elif command -v sha256sum >/dev/null 2>&1; then
	if ! (cd "$tmp" && sha256sum -c "${asset}.sha256" >/dev/null 2>&1); then
		err "checksum verification failed — refusing to install."
		exit 1
	fi
else
	err "neither shasum nor sha256sum found — cannot verify download."
	exit 1
fi

# --- Install -----------------------------------------------------------------
mkdir -p "$BIN_DIR"
if command -v install >/dev/null 2>&1; then
	install -m 0755 "${tmp}/${asset}" "${BIN_DIR}/soap"
else
	cp "${tmp}/${asset}" "${BIN_DIR}/soap"
	chmod 0755 "${BIN_DIR}/soap"
fi

echo "Installed soap ${version} -> ${BIN_DIR}/soap"

# PATH nudge.
case ":${PATH}:" in
*":${BIN_DIR}:"*) : ;;
*) echo "Add it to your PATH:  export PATH=\"${BIN_DIR}:\$PATH\"" ;;
esac

echo "Next:  soap init   # then run 'soap'"
