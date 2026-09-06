#!/usr/bin/env bash
# Configura permissões locais e instala a interface VPN para o usuário atual.
# Deve ser executado uma vez por usuário normal; solicita sudo apenas quando
# precisa criar a regra de permissões e o diretório de logs.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
BUILD_SCRIPT="$PROJECT_DIR/scripts/build.sh"
INSTALL_SCRIPT="$PROJECT_DIR/scripts/install-user.sh"

if [ "$(id -u)" -eq 0 ]; then
  echo "ERRO: não rode este script como root/sudo. Rode como seu usuário normal;" >&2
  echo "ele vai pedir sua senha apenas quando necessário." >&2
  exit 1
fi

TARGET_USER="${SUDO_USER:-$USER}"

echo "==> Verificando openfortivpn..."
BIN="$(command -v openfortivpn || true)"
if [ -z "$BIN" ]; then
  echo "ERRO: openfortivpn não está instalado." >&2
  echo "Instale com o gerenciador de pacotes da sua distro, por exemplo:" >&2
  echo "  sudo apt install openfortivpn      # Debian/Ubuntu" >&2
  echo "  sudo dnf install openfortivpn      # Fedora" >&2
  exit 1
fi
echo "    Encontrado em: $BIN"

echo "==> Configurando sudo sem senha para openfortivpn e kill..."
SUDOERS_FILE="/etc/sudoers.d/openfortivpn"
SUDOERS_LINE="$TARGET_USER ALL=(root) NOPASSWD: $BIN, /bin/kill"

if [ -f "$SUDOERS_FILE" ] && grep -qF "$SUDOERS_LINE" "$SUDOERS_FILE" 2>/dev/null; then
  echo "    Já configurado, pulando."
else
  echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" > /dev/null
  sudo chmod 440 "$SUDOERS_FILE"
  if ! sudo visudo -c > /dev/null 2>&1; then
    echo "ERRO: regra sudoers inválida, revertendo." >&2
    sudo rm -f "$SUDOERS_FILE"
    exit 1
  fi
  echo "    Criado: $SUDOERS_FILE"
fi

echo "==> Criando diretório de logs /var/log/openfortivpn..."
sudo mkdir -p /var/log/openfortivpn
sudo chown "$TARGET_USER":"$TARGET_USER" /var/log/openfortivpn
echo "    OK."

echo "==> Compilando a interface..."
if ! "$BUILD_SCRIPT"; then
  echo "ERRO: não foi possível compilar a interface." >&2
  echo "Ative o ambiente virtual e instale as dependências:" >&2
  echo "  source .venv/bin/activate" >&2
  echo "  python3 -m pip install -r requirements.txt" >&2
  exit 1
fi

echo "==> Instalando o atalho na galeria de aplicativos..."
"$INSTALL_SCRIPT"

echo ""
echo "Setup concluído. Procure por 'VPN Connect' na galeria de aplicativos."
