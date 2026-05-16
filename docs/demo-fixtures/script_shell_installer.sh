#!/usr/bin/env sh
set -e

PROFILE="${PROFILE:-$HOME/.profile}"
INSTALL_DIR="${NVM_DIR:-$HOME/.nvm}"

echo "Installing demo runtime into $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
curl -fsSL "https://example.com/runtime.tar.gz" -o "$INSTALL_DIR/runtime.tar.gz"
echo "export DEMO_RUNTIME_HOME=$INSTALL_DIR" >> "$PROFILE"
echo "Installation complete"
