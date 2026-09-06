#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
APP_NAME="openfortivpn-gui"
SOURCE_BIN="$PROJECT_DIR/dist/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
APPLICATIONS_DIR="$HOME/.local/share/applications"
DESKTOP_TEMPLATE="$PROJECT_DIR/packaging/vpn-connect.desktop"
DESKTOP_FILE="$APPLICATIONS_DIR/vpn-connect.desktop"

if ! command -v openfortivpn >/dev/null 2>&1; then
  echo "Erro: openfortivpn não está instalado." >&2
  echo "Instale com: sudo apt install openfortivpn" >&2
  exit 1
fi

if [[ ! -x "$SOURCE_BIN" ]]; then
  echo "Binário não encontrado. Compilando..."
  "$PROJECT_DIR/scripts/build.sh"
fi

install -d -m 0755 "$BIN_DIR" "$APPLICATIONS_DIR"
install -Dm755 "$SOURCE_BIN" "$BIN_DIR/$APP_NAME"

sed "s|@HOME@|$HOME|g" "$DESKTOP_TEMPLATE" > "$DESKTOP_FILE"
chmod 644 "$DESKTOP_FILE"

update-desktop-database "$APPLICATIONS_DIR" 2>/dev/null || true

echo "Instalado:"
echo "  Binário: $BIN_DIR/$APP_NAME"
echo "  Atalho:  $DESKTOP_FILE"
