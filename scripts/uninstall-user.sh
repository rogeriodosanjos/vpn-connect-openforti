#!/usr/bin/env bash
set -euo pipefail

rm -f "$HOME/.local/bin/openfortivpn-gui"
rm -f "$HOME/.local/share/applications/vpn-connect.desktop"

update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "VPN Connect removido."
echo "Perfis, certificados em cache e logs foram preservados."
