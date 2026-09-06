#!/usr/bin/env bash
set -euo pipefail

BIN="$(command -v openfortivpn || true)"
PID_FILE="$HOME/.cache/openfortivpn-gui.pid"

# Encontra o PID do processo real do openfortivpn: primeiro tenta o pidfile
# salvo pelo connect-vpn.sh, e cai para busca por nome como reforço, caso o
# pidfile esteja ausente ou desatualizado (ex.: script encerrado à força).
PID=""
if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if ! kill -0 "$PID" 2>/dev/null; then
    PID=""
  fi
fi

if [ -z "$PID" ] && [ -n "$BIN" ]; then
  PID="$(pgrep -f "^$BIN " | tail -1 || true)"
fi

if [ -z "$PID" ]; then
  echo "Nenhuma sessão VPN (openfortivpn) em execução foi encontrada." >&2
  rm -f "$PID_FILE"
  exit 0
fi

echo "Desconectando VPN (PID $PID)..."
# openfortivpn faz logout limpo do gateway ao receber SIGTERM/SIGINT.
sudo -n /bin/kill -TERM "$PID"

# Aguarda o processo encerrar de fato (timeout de 15s), depois força se preciso.
for _ in $(seq 1 30); do
  if ! kill -0 "$PID" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

if kill -0 "$PID" 2>/dev/null; then
  echo "Processo não encerrou a tempo, forçando com SIGKILL..." >&2
  sudo -n /bin/kill -KILL "$PID" 2>/dev/null || true
fi

rm -f "$PID_FILE"
echo "VPN desconectada."
