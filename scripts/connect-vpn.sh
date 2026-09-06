#!/usr/bin/env bash
set -euo pipefail

# Uso: connect-vpn.sh <host:porta> <usuario>
# Ex.:  connect-vpn.sh vpn.exemplo.com:443 fulano
if [ $# -lt 2 ]; then
  echo "Uso: $0 <host:porta> <usuario>" >&2
  exit 1
fi
VPN_HOST="$1"
VPN_USER="$2"
CONFIG_FILE="$HOME/.config/openfortivpn/config"

BIN="$(command -v openfortivpn || true)"
if [ -z "$BIN" ]; then
  echo "ERRO: openfortivpn não encontrado no PATH." >&2
  exit 1
fi

# openfortivpn checa geteuid() == 0 internamente, então "setcap" não é
# suficiente: ele exige root de fato. Usamos sudo, liberado sem senha via
# /etc/sudoers.d/openfortivpn (veja README/instruções de setup).
if ! sudo -n -l "$BIN" >/dev/null 2>&1; then
  echo "ERRO: sudo sem senha não está configurado para $BIN." >&2
  echo "Configure uma vez com:" >&2
  echo "  echo \"$USER ALL=(root) NOPASSWD: $BIN, /bin/kill\" | sudo tee /etc/sudoers.d/openfortivpn" >&2
  echo "  sudo chmod 440 /etc/sudoers.d/openfortivpn" >&2
  exit 1
fi

# Diretório de log em /var/log (precisa existir e ser gravável pelo usuário;
# veja instruções de setup abaixo se ainda não tiver sido criado).
LOG_DIR="/var/log/openfortivpn"
if [ ! -d "$LOG_DIR" ] || [ ! -w "$LOG_DIR" ]; then
  echo "ERRO: $LOG_DIR não existe ou não é gravável por $USER." >&2
  echo "Configure uma vez com:" >&2
  echo "  sudo mkdir -p $LOG_DIR && sudo chown $USER:$USER $LOG_DIR" >&2
  exit 1
fi
LOG_FILE="$LOG_DIR/connect-$(date +%Y%m%d-%H%M%S).log"

# Cache do hash (sha256) do certificado do gateway aceito. O openfortivpn
# recusa se conectar a um gateway cujo certificado não esteja "confiável",
# e esse hash pode mudar quando o certificado for renovado/trocado.
TRUSTED_CERT_FILE="$HOME/.cache/openfortivpn-gui-trusted-cert"
mkdir -p "$(dirname "$TRUSTED_CERT_FILE")"

# Pidfile usado pelo disconnect-vpn.sh para localizar e encerrar a sessão.
PID_FILE="$HOME/.cache/openfortivpn-gui.pid"

build_args() {
  ARGS=("$VPN_HOST" -u "$VPN_USER")
  if [ -f "$TRUSTED_CERT_FILE" ]; then
    ARGS+=(--trusted-cert "$(cat "$TRUSTED_CERT_FILE")")
  fi
}

# Roda o openfortivpn em background, com stdin ligado ao terminal (para os
# prompts de senha/token 2FA) e stdout/stderr espelhados no terminal e no
# arquivo de log. Assim que o túnel sobe, o script devolve o prompt e a VPN
# continua rodando em segundo plano.
run_and_wait_for_tunnel() {
  local logfile="$1"
  shift
  sudo -n "$BIN" "$@" < /dev/tty > >(tee -a "$logfile") 2>&1 &
  VPN_PID=$!
  disown "$VPN_PID" 2>/dev/null || true

  while kill -0 "$VPN_PID" 2>/dev/null; do
    if grep -q "Tunnel is up and running" "$logfile" 2>/dev/null; then
      # sudo normalmente faz exec() no binário, mas para garantir o PID certo
      # do processo real do openfortivpn (que roda como root), buscamos por
      # nome/argumentos em vez de confiar apenas no PID do sudo.
      local real_pid
      real_pid="$(pgrep -f "$BIN $VPN_HOST" | tail -1 || true)"
      echo "${real_pid:-$VPN_PID}" > "$PID_FILE"
      echo "VPN conectada (PID $(cat "$PID_FILE")). Logs em: $logfile"
      return 0
    fi
    sleep 0.5
  done

  wait "$VPN_PID" 2>/dev/null
  return 1
}

build_args
if run_and_wait_for_tunnel "$LOG_FILE" "${ARGS[@]}"; then
  exit 0
fi

NEW_DIGEST="$(grep -oE -- '--trusted-cert[[:space:]]+[0-9a-f]{64}' "$LOG_FILE" | tail -1 | awk '{print $2}')"
if [ -z "$NEW_DIGEST" ]; then
  echo "ERRO: falha ao conectar. Veja o log completo em: $LOG_FILE" >&2
  tail -n 20 "$LOG_FILE" >&2
  exit 1
fi

echo "Certificado do gateway mudou/não está na lista de confiança. Salvando hash e tentando novamente..." >&2
echo "$NEW_DIGEST" > "$TRUSTED_CERT_FILE"

build_args
if run_and_wait_for_tunnel "$LOG_FILE" "${ARGS[@]}"; then
  exit 0
fi

echo "ERRO: falha ao conectar mesmo após atualizar o certificado. Veja o log em: $LOG_FILE" >&2
tail -n 20 "$LOG_FILE" >&2
exit 1