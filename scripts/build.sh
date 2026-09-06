#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
SOURCE_FILE="$PROJECT_DIR/src/vpn_gui.py"

if [[ ! -f "$SOURCE_FILE" ]]; then
  echo "Erro: código-fonte não encontrado: $SOURCE_FILE" >&2
  exit 1
fi

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name openfortivpn-gui \
  --distpath "$PROJECT_DIR/dist" \
  --workpath "$PROJECT_DIR/build" \
  "$SOURCE_FILE"

echo "Binário criado em: $PROJECT_DIR/dist/openfortivpn-gui"
