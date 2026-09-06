#!/usr/bin/env python3
"""
Interface gráfica para conectar/desconectar de uma VPN via openfortivpn.

Requisitos de sistema, configurados uma única vez (veja README.md):
  - sudo sem senha para /usr/bin/openfortivpn e /bin/kill
  - diretório /var/log/openfortivpn gravável pelo usuário

Uso:
  python3 vpn_gui.py
"""
import json
import os
import re
import shutil
import subprocess
import threading
import queue
import time
import signal
import shlex
from pathlib import Path
from datetime import datetime

# Garante que utilitários de rede (/sbin, /usr/sbin, etc.) estejam no PATH quando o app rodar via .desktop
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
current_paths = os.environ.get("PATH", "").split(os.pathsep)
for path in reversed(SYSTEM_PATH.split(os.pathsep)):
    if path not in current_paths:
        current_paths.insert(0, path)
os.environ["PATH"] = os.pathsep.join(current_paths)

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

try:
    import pexpect
except ImportError:
    pexpect = None

CACHE_DIR = Path.home() / ".cache"
CONFIG_DIR = Path.home() / ".config" / "openfortivpn-gui"
USER_CONFIG_FILE = Path.home() / ".config" / "openfortivpn" / "config"
PROFILES_FILE = CONFIG_DIR / "profiles.json"
PID_FILE = CACHE_DIR / "openfortivpn-gui.pid"
LOG_DIR = Path("/var/log/openfortivpn")

PASSWORD_PROMPT_RE = re.compile(r"(VPN account password|Password)\s*:\s*$", re.IGNORECASE)
TOKEN_PROMPT_RE = re.compile(
    r"(Two-factor authentication token|Please enter one-time password)\s*:\s*$",
    re.IGNORECASE,
)
TUNNEL_UP_RE = re.compile(r"Tunnel is up and running")
CERT_DIGEST_RE = re.compile(r"--trusted-cert\s+([0-9a-f]{64})")


def _slug(name):
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower()) or "default"


def trusted_cert_file_for(profile_name):
    return CACHE_DIR / f"openfortivpn-gui-trusted-cert-{_slug(profile_name)}"


class ProfileStore:
    """Persiste perfis (nome -> {endpoint, user}) em ~/.config/openfortivpn-gui/profiles.json."""

    def __init__(self, path):
        self.path = path
        self.data = {"last_profile": "", "profiles": {}}
        self.load()

    def load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        self.data.setdefault("profiles", {})
        self.data.setdefault("last_profile", "")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))

    def names(self):
        return sorted(self.data["profiles"].keys())

    def get(self, name):
        return self.data["profiles"].get(name)

    def set(self, name, endpoint, user):
        self.data["profiles"][name] = {"endpoint": endpoint, "user": user}
        self.data["last_profile"] = name
        self.save()

    def delete(self, name):
        self.data["profiles"].pop(name, None)
        if self.data.get("last_profile") == name:
            self.data["last_profile"] = ""
        self.save()

    def last_profile(self):
        return self.data.get("last_profile", "")


class VpnGui:
    def __init__(self, root):
        self.root = root
        self.root.title("VPN Connect")
        self.root.geometry("640x500")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.profiles = ProfileStore(PROFILES_FILE)
        self.vpn_pid = None
        self.vpn_user = None
        self.vpn_endpoint = None
        self.active_profile = None
        self.worker = None
        self.child = None
        self.stop_requested = False
        self.log_queue = queue.Queue()
        self._loading_profile = False

        self._build_ui()
        self._load_cached_pid()
        self.root.after(150, self._drain_log_queue)

    # ---------- UI ----------

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)

        profile_row = ttk.Frame(top)
        profile_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(profile_row, text="Perfil:").pack(side=tk.LEFT)
        self.profile_var = tk.StringVar(value="")
        self.profile_combo = ttk.Combobox(
            profile_row, textvariable=self.profile_var, width=20, state="readonly"
        )
        self.profile_combo.pack(side=tk.LEFT, padx=(4, 8))
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)

        ttk.Button(profile_row, text="Salvar perfil", command=self._on_save_profile).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(profile_row, text="Excluir perfil", command=self._on_delete_profile).pack(
            side=tk.LEFT, padx=2
        )

        endpoint_row = ttk.Frame(top)
        endpoint_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(endpoint_row, text="Endpoint (host:porta):").pack(side=tk.LEFT)
        self.endpoint_var = tk.StringVar(value="")
        self.endpoint_entry = ttk.Entry(endpoint_row, textvariable=self.endpoint_var, width=32)
        self.endpoint_entry.pack(side=tk.LEFT, padx=(4, 0))
        self.endpoint_entry.bind("<Return>", lambda _e: self.on_connect())

        controls_row = ttk.Frame(top)
        controls_row.pack(fill=tk.X)

        ttk.Label(controls_row, text="Usuário:").pack(side=tk.LEFT)
        self.user_var = tk.StringVar(value="")
        self.user_entry = ttk.Entry(controls_row, textvariable=self.user_var, width=18)
        self.user_entry.pack(side=tk.LEFT, padx=(4, 20))
        # Enter no campo de usuário já dispara a conexão, sem precisar clicar.
        self.user_entry.bind("<Return>", lambda _e: self.on_connect())

        self.status_var = tk.StringVar(value="Desconectado")
        ttk.Label(controls_row, text="Status:").pack(side=tk.LEFT)
        self.status_label = ttk.Label(
            controls_row, textvariable=self.status_var, foreground="red", font=("Sans", 10, "bold")
        )
        self.status_label.pack(side=tk.LEFT, padx=(4, 20))

        self.connect_btn = ttk.Button(controls_row, text="Conectar", command=self.on_connect)
        self.connect_btn.pack(side=tk.LEFT, padx=4)
        self.disconnect_btn = ttk.Button(
            controls_row, text="Desconectar", command=self.on_disconnect, state=tk.DISABLED
        )
        self.disconnect_btn.pack(side=tk.LEFT, padx=4)

        # Garante que Enter/Espaço acionem o botão que estiver com foco no
        # teclado (comportamento padrão do Tk às vezes não cobre Espaço em
        # todos os temas/plataformas).
        for btn in (self.connect_btn, self.disconnect_btn):
            btn.bind("<Return>", lambda _e, b=btn: b.invoke())
            btn.bind("<space>", lambda _e, b=btn: b.invoke())

        # Enter em qualquer lugar da janela também dispara Conectar, desde
        # que o botão esteja habilitado (atalho global de conveniência).
        self.root.bind("<Return>", self._on_global_return)

        self._refresh_profile_combo(select=self.profiles.last_profile())

        log_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        log_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(log_frame, text="Log:").pack(anchor=tk.W)

        text_container = ttk.Frame(log_frame)
        text_container.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(text_container, wrap=tk.WORD, state=tk.DISABLED, height=18)
        scroll = ttk.Scrollbar(text_container, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _append_log(self, line):
        self.log_queue.put(line)

    def _drain_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, line + "\n")
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(150, self._drain_log_queue)

    def _set_status(self, text, color):
        self.status_var.set(text)
        self.status_label.configure(foreground=color)

    def _set_connected_ui(self, connected):
        self.connect_btn.configure(state=tk.DISABLED if connected else tk.NORMAL)
        self.disconnect_btn.configure(state=tk.NORMAL if connected else tk.DISABLED)
        self.user_entry.configure(state=tk.DISABLED if connected else tk.NORMAL)
        self.endpoint_entry.configure(state=tk.DISABLED if connected else tk.NORMAL)
        self.profile_combo.configure(state=tk.DISABLED if connected else "readonly")

    # ---------- Perfis ----------

    def _refresh_profile_combo(self, select=None):
        names = self.profiles.names()
        self.profile_combo["values"] = names
        if select and select in names:
            self.profile_var.set(select)
            self._load_profile(select)
        elif names:
            self.profile_var.set(names[0])
            self._load_profile(names[0])
        else:
            self.profile_var.set("")
            self.active_profile = None

    def _load_profile(self, name):
        profile = self.profiles.get(name)
        if not profile:
            return
        self._loading_profile = True
        self.endpoint_var.set(profile.get("endpoint", ""))
        self.user_var.set(profile.get("user", ""))
        self._loading_profile = False
        self.active_profile = name

    def _on_profile_selected(self, _event=None):
        name = self.profile_var.get()
        if name:
            self._load_profile(name)

    def _on_save_profile(self):
        endpoint = self.endpoint_var.get().strip()
        user = self.user_var.get().strip()
        if not endpoint or not user:
            messagebox.showwarning(
                "Dados incompletos",
                "Preencha Endpoint e Usuário antes de salvar o perfil.",
                parent=self.root,
            )
            return

        suggested = self.profile_var.get() or endpoint
        name = simpledialog.askstring(
            "Salvar perfil", "Nome do perfil:", initialvalue=suggested, parent=self.root
        )
        if not name:
            return
        name = name.strip()
        if not name:
            return

        self.profiles.set(name, endpoint, user)
        self._refresh_profile_combo(select=name)
        self._append_log(f"Perfil '{name}' salvo.")

    def _on_delete_profile(self):
        name = self.profile_var.get()
        if not name:
            return
        if not messagebox.askyesno(
            "Excluir perfil", f"Excluir o perfil '{name}'?", parent=self.root
        ):
            return
        self.profiles.delete(name)
        self._refresh_profile_combo()
        self._append_log(f"Perfil '{name}' excluído.")

    def _on_global_return(self, _event):
        # Evita disparar Conectar duas vezes quando o Enter já foi tratado
        # pelo bind específico do campo de usuário ou de um botão focado.
        focused = self.root.focus_get()
        if focused is self.user_entry or focused in (self.connect_btn, self.disconnect_btn):
            return
        if str(self.connect_btn["state"]) == tk.NORMAL:
            self.on_connect()

    # ---------- Prompt dialogs (executados na thread principal) ----------

    def _ask_secret_on_main(self, title, prompt):
        result = {}
        done = threading.Event()

        def _show():
            value = simpledialog.askstring(title, prompt, show="*", parent=self.root)
            result["value"] = value
            done.set()

        self.root.after(0, _show)
        done.wait()
        return result.get("value")

    def _show_error_on_main(self, title, message):
        done = threading.Event()

        def _show():
            messagebox.showerror(title, message, parent=self.root)
            done.set()

        self.root.after(0, _show)
        done.wait()

    def _show_info_on_main(self, title, message):
        self.root.after(0, lambda: messagebox.showinfo(title, message, parent=self.root))

    # ---------- Pré-checagens ----------

    def _preflight(self):
        bin_path = shutil.which("openfortivpn")
        if not bin_path:
            self._show_error_on_main("Erro", "openfortivpn não encontrado no PATH.")
            return None

        if pexpect is None:
            self._show_error_on_main(
                "Erro",
                "Módulo Python 'pexpect' não está instalado.\n"
                "Instale com: pip install pexpect",
            )
            return None

        sudo_ok = subprocess.run(
            ["sudo", "-n", "-l", bin_path], capture_output=True
        ).returncode == 0
        if not sudo_ok:
            self._show_error_on_main(
                "Erro",
                "sudo sem senha não está configurado para openfortivpn.\n\n"
                "Configure uma vez em um terminal:\n"
                f'echo "{os.environ.get("USER", "$USER")} ALL=(root) NOPASSWD: '
                f'{bin_path}, /bin/kill" | sudo tee /etc/sudoers.d/openfortivpn\n'
                "sudo chmod 440 /etc/sudoers.d/openfortivpn",
            )
            return None

        if not LOG_DIR.is_dir() or not os.access(LOG_DIR, os.W_OK):
            self._show_error_on_main(
                "Erro",
                f"{LOG_DIR} não existe ou não é gravável pelo usuário atual.\n\n"
                "Configure uma vez em um terminal:\n"
                f"sudo mkdir -p {LOG_DIR} && sudo chown $USER:$USER {LOG_DIR}",
            )
            return None

        return bin_path

    # ---------- Conectar ----------

    def on_connect(self):
        if self.worker and self.worker.is_alive():
            return

        endpoint = self.endpoint_var.get().strip()
        if not endpoint:
            messagebox.showwarning(
                "Endpoint obrigatório", "Informe o endpoint da VPN (host:porta).", parent=self.root
            )
            self.endpoint_entry.focus_set()
            return

        vpn_user = self.user_var.get().strip()
        if not vpn_user:
            messagebox.showwarning("Usuário obrigatório", "Informe o usuário da VPN.", parent=self.root)
            self.user_entry.focus_set()
            return

        self.vpn_endpoint = endpoint
        self.vpn_user = vpn_user

        profile_name = self.profile_var.get()
        self.trusted_cert_file = trusted_cert_file_for(profile_name or endpoint)

        self.connect_btn.configure(state=tk.DISABLED)
        self._set_status("Conectando...", "orange")
        self.worker = threading.Thread(target=self._connect_worker, daemon=True)
        self.worker.start()

    def _connect_worker(self):
        """Executa as validações e inicia a VPN fora da thread da interface."""
        connected = False

        try:
            bin_path = self._preflight()
            if not bin_path:
                return

            # Só procura o executável real da VPN, nunca o aplicativo
            # openfortivpn-gui que está executando esta função.
            self._cleanup_orphan_vpn(bin_path)

            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            log_file = LOG_DIR / f"openfortivpn-{timestamp}.log"
            self._append_log(f"Log salvo em: {log_file}")

            connected = self._spawn_and_connect(bin_path, log_file)

            if connected:
                self.root.after(0, lambda: self._set_status("Conectado", "green"))
                self.root.after(0, lambda: self._set_connected_ui(True))
            else:
                self.root.after(0, lambda: self._set_status("Desconectado", "red"))
                self.root.after(0, lambda: self._set_connected_ui(False))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"ERRO inesperado ao conectar: {exc}")
            self.root.after(
                0,
                lambda: messagebox.showerror(
                    "Erro ao conectar", str(exc), parent=self.root
                ),
            )
            self.root.after(0, lambda: self._set_status("Desconectado", "red"))
            self.root.after(0, lambda: self._set_connected_ui(False))

    def _cleanup_orphan_vpn(self, bin_path):
        """Encerra somente instâncias do executável openfortivpn real."""
        try:
            process_name = Path(bin_path).name
            result = subprocess.run(
                ["pgrep", "-x", process_name],
                capture_output=True,
                text=True,
                check=False,
            )

            pids = [
                pid.strip()
                for pid in result.stdout.splitlines()
                if pid.strip() and pid.strip() != str(os.getpid())
            ]

            for pid in pids:
                self._append_log(f"Encerrando sessão VPN anterior (PID {pid})...")
                subprocess.run(
                    ["sudo", "-n", "/bin/kill", "-TERM", pid],
                    capture_output=True,
                    check=False,
                )

            if pids:
                time.sleep(1.5)

            PID_FILE.unlink(missing_ok=True)
            self.vpn_pid = None
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Aviso: não foi possível limpar sessões anteriores: {exc}")

    def _route_snapshot(self):
        """Retorna a tabela de rotas IPv4 para diagnóstico no log."""
        result = subprocess.run(
            ["ip", "-4", "route", "show"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() or "(nenhuma rota IPv4 retornada)"

    def _log_route_snapshot(self, title):
        self._append_log(f"--- {title} ---")
        for line in self._route_snapshot().splitlines():
            self._append_log(f"ROTA: {line}")
        self._append_log("--- fim das rotas ---")

    def _build_args(self):
        # Opções de linha de comando ficam após -c para garantir que
        # set-routes=1 prevaleça caso o arquivo de configuração tenha
        # set-routes=0.
        args = [self.vpn_endpoint, "-u", self.vpn_user]

        if USER_CONFIG_FILE.exists():
            args += ["-c", str(USER_CONFIG_FILE)]

        args += ["--set-routes=1"]

        if self.trusted_cert_file.exists():
            digest = self.trusted_cert_file.read_text().strip()
            if digest:
                args += ["--trusted-cert", digest]

        return args

    def _spawn_and_connect(self, bin_path, log_file):
        args = self._build_args()
        sudo_args = ["-n", bin_path, *args]

        self._append_log("$ " + shlex.join(["sudo", *sudo_args]))
        self._log_route_snapshot("Rotas antes da conexão")

        with open(log_file, "a", encoding="utf-8") as fh:
            try:
                child = pexpect.spawn(
                    "sudo",
                    args=sudo_args,
                    timeout=60,
                    encoding="utf-8",
                    codec_errors="ignore",
                )
                # Salva a saída integral do openfortivpn/pppd no arquivo,
                # incluindo mensagens sobre split routes e falhas de rota.
                child.logfile_read = fh
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"ERRO ao iniciar processo: {exc}")
                return False

            self.child = child
            connected = False
            try:
                while True:
                    index = child.expect(
                        [
                            PASSWORD_PROMPT_RE,
                            TOKEN_PROMPT_RE,
                            TUNNEL_UP_RE,
                            pexpect.EOF,
                            pexpect.TIMEOUT,
                        ],
                        timeout=60,
                    )

                    chunk = child.before or ""
                    for line in chunk.splitlines():
                        if line.strip():
                            self._append_log(line.rstrip())
                    fh.flush()

                    if index == 0:
                        pwd = self._ask_secret_on_main("VPN", "Senha da conta VPN:")
                        if pwd is None:
                            child.terminate(force=True)
                            self._append_log("Conexão cancelada pelo usuário.")
                            return False
                        child.sendline(pwd)

                    elif index == 1:
                        token = self._ask_secret_on_main(
                            "VPN", "Token de autenticação (2FA):"
                        )
                        if token is None:
                            child.terminate(force=True)
                            self._append_log("Conexão cancelada pelo usuário.")
                            return False
                        child.sendline(token)

                    elif index == 2:
                        self._append_log("Tunnel is up and running")
                        connected = True
                        # Dá tempo ao pppd/openfortivpn de instalar rotas.
                        time.sleep(1)
                        self._log_route_snapshot("Rotas após o túnel subir")
                        break

                    elif index == 3:
                        self._append_log(
                            f"Processo terminou (EOF, status={child.exitstatus}, "
                            f"signal={child.signalstatus})."
                        )
                        break

                    else:
                        self._append_log(
                            "Timeout aguardando resposta do openfortivpn."
                        )
                        break
            finally:
                if connected:
                    # Mantém o pseudo-terminal do pexpect aberto durante toda
                    # a conexão. Remover esta referência pode fechar o PTY,
                    # encerrar openfortivpn/pppd e remover as rotas ppp0.
                    self._find_and_store_pid(child.pid)
                    self.child = child
                    self._append_log(
                        f"Processo VPN mantido em execução (PID {child.pid})."
                    )
                else:
                    try:
                        child.terminate(force=True)
                    except Exception:  # noqa: BLE001
                        pass
                    self.child = None

            return connected

    def _find_and_store_pid(self, pid):
        """Armazena o PID do processo criado pelo pexpect/sudo."""
        pid = str(pid) if pid else None
        if pid and self._pid_alive(pid):
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            PID_FILE.write_text(pid + "\n")
            self.vpn_pid = pid
            self._append_log(f"VPN conectada (PID {pid}).")
        else:
            self._append_log("VPN conectada, mas não foi possível registrar o PID.")

    def _load_cached_pid(self):
        if PID_FILE.exists():
            pid = PID_FILE.read_text().strip()
            if pid and self._pid_alive(pid):
                self.vpn_pid = pid
                self._set_status("Conectado", "green")
                self._set_connected_ui(True)
            else:
                PID_FILE.unlink(missing_ok=True)

    @staticmethod
    def _pid_alive(pid):
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError):
            return False

    # ---------- Desconectar ----------

    def on_disconnect(self):
        self.disconnect_btn.configure(state=tk.DISABLED)
        threading.Thread(target=self._disconnect_worker, daemon=True).start()

    def _disconnect_worker(self):
        pid = self.vpn_pid or (PID_FILE.read_text().strip() if PID_FILE.exists() else None)
        if not pid or not self._pid_alive(pid):
            self._append_log("Nenhuma sessão VPN em execução foi encontrada.")
            PID_FILE.unlink(missing_ok=True)
            self.root.after(0, lambda: self._set_status("Desconectado", "red"))
            self.root.after(0, lambda: self._set_connected_ui(False))
            self.root.after(0, lambda: self.connect_btn.configure(state=tk.NORMAL))
            return

        self._append_log(f"Desconectando VPN (PID {pid})...")
        subprocess.run(["sudo", "-n", "/bin/kill", "-TERM", pid])

        for _ in range(30):
            if not self._pid_alive(pid):
                break
            time.sleep(0.5)

        if self._pid_alive(pid):
            self._append_log("Processo não encerrou a tempo, forçando com SIGKILL...")
            subprocess.run(["sudo", "-n", "/bin/kill", "-KILL", pid])

        # Fecha o PTY somente depois que o processo VPN foi encerrado.
        if self.child is not None:
            try:
                self.child.close(force=True)
            except Exception:  # noqa: BLE001
                pass
            self.child = None

        PID_FILE.unlink(missing_ok=True)
        self.vpn_pid = None
        self._append_log("VPN desconectada.")
        self.root.after(0, lambda: self._set_status("Desconectado", "red"))
        self.root.after(0, lambda: self._set_connected_ui(False))
        self.root.after(0, lambda: self.connect_btn.configure(state=tk.NORMAL))

    # ---------- Encerramento ----------

    def on_close(self):
        # Não derruba a VPN ao fechar a janela; ela continua em background.
        self.root.destroy()


def main():
    # className define o WM_CLASS da janela (em vez do genérico "Tk"),
    # permitindo que o .desktop launcher associe corretamente esta janela
    # ao ícone fixado na dock/barra de tarefas.
    root = tk.Tk(className="openfortivpn-gui")
    VpnGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
