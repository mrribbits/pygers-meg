#!/usr/bin/env bash
# install_edf2asc.sh
# Installs SR Research's edf2asc on Linux, without sudo. eye2bids needs it
# to read EyeLink .edf files. (eye2bids itself is installed with pip; see README.)
#
# Usage:
#   bash install_edf2asc.sh                                # into ~/software/edf2asc
#   INSTALL_DIR=/jukebox/[...]/edf2asc bash install_edf2asc.sh
#
# Then add INSTALL_DIR/bin to your PATH (the script prints the line).
# Everything goes into INSTALL_DIR; delete that folder to uninstall.
#
# On macOS, install the EyeLink Developers Kit instead (it includes edf2asc).

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/software/edf2asc}"

SR_REPO="https://apt.sr-research.com"          # SR Research's apt repository
SR_PACKAGES="eyelink-edf2asc eyelink-edfapi"    # edf2asc needs the EDF Access API library

if [[ "$(uname -s)" != "Linux" ]]; then
    cat >&2 <<'MSG'
This script is for Linux. On macOS, install the EyeLink Developers Kit:
  1. Log in to the SR Research support forum (https://www.sr-research.com/support/)
  2. Download and run the EyeLink Developers Kit installer for macOS
  3. Open a new terminal and check:  which edf2asc
MSG
    exit 1
fi

case "$(uname -m)" in
    x86_64)        ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) echo "ERROR: unsupported CPU '$(uname -m)'" >&2; exit 1 ;;
esac

mkdir -p "$INSTALL_DIR"/{bin,lib,libexec}
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cd "$TMP"

# SR Research only ships .deb packages. Instead of apt/sudo, download the
# .debs and unpack them. The repo layout (suite and component) comes from
# SR Research's own apt config file, so this keeps working if they rename things.
echo "==> Reading SR Research repo configuration"
curl -fsSL "$SR_REPO/SR_Research_repo.sources" -o repo.sources
SR_SUITE=$(awk '/^Suites:/ {print $2; exit}' repo.sources)
SR_COMPONENT=$(awk '/^Components:/ {print $2; exit}' repo.sources)
INDEX_URL="$SR_REPO/dists/$SR_SUITE/$SR_COMPONENT/binary-$ARCH"

echo "==> Downloading EyeLink package index ($INDEX_URL)"
if curl -fsSL "$INDEX_URL/Packages.gz" -o Packages.gz; then
    gunzip -f Packages.gz
else
    curl -fsSL "$INDEX_URL/Packages" -o Packages
fi

for pkg in $SR_PACKAGES; do
    # Find this package's .deb path in the index, picking the highest version
    deb_path=$(awk -v p="$pkg" '
        /^Package: /  { cur = $2 }
        /^Version: /  && cur == p { ver = $2 }
        /^Filename: / && cur == p { print ver, $2 }' Packages \
        | sort -V | tail -1 | cut -d" " -f2)
    if [[ -z "$deb_path" ]]; then
        echo "ERROR: package '$pkg' not found in the SR Research index" >&2
        exit 1
    fi

    echo "==> Downloading $pkg ($deb_path)"
    curl -fsSL "$SR_REPO/$deb_path" -o "$pkg.deb"

    # A .deb is an 'ar' archive; the files are inside data.tar.*
    mkdir -p "unpack_$pkg" && cd "unpack_$pkg"
    ar x "../$pkg.deb"
    tar -xf data.tar.*          # .zst needs zstd; xz/gz work with plain tar
    cd ..
done

# The real program goes in libexec/ and its libraries in lib/.
find unpack_* -type f -name edf2asc -exec cp {} "$INSTALL_DIR/libexec/" \;
find unpack_* \( -type f -o -type l \) -name "*.so*" -exec cp -P {} "$INSTALL_DIR/lib/" \;
chmod +x "$INSTALL_DIR/libexec/edf2asc"

# bin/edf2asc is a small wrapper that points the program at its libraries,
# so only bin/ needs to go on PATH (no LD_LIBRARY_PATH in your shell).
cat > "$INSTALL_DIR/bin/edf2asc" <<EOF
#!/bin/sh
LD_LIBRARY_PATH="$INSTALL_DIR/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}" \\
    exec "$INSTALL_DIR/libexec/edf2asc" "\$@"
EOF
chmod +x "$INSTALL_DIR/bin/edf2asc"

# Quick check: missing system libraries would show up here
echo "==> Checking install"
if LD_LIBRARY_PATH="$INSTALL_DIR/lib" ldd "$INSTALL_DIR/libexec/edf2asc" | grep -q "not found"; then
    echo "WARNING: edf2asc is missing libraries:" >&2
    LD_LIBRARY_PATH="$INSTALL_DIR/lib" ldd "$INSTALL_DIR/libexec/edf2asc" | grep "not found" >&2
fi
"$INSTALL_DIR/bin/edf2asc" 2>&1 | grep -m1 -i "version" || true   # exits non-zero without input

echo
echo "Done. Add edf2asc to your PATH (e.g. in ~/.bashrc):"
echo "  export PATH=\"$INSTALL_DIR/bin:\$PATH\""
