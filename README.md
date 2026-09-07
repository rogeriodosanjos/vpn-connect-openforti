# VPN Connect

Interface gráfica para conectar e desconectar VPNs Fortinet em Linux usando
[openfortivpn](https://github.com/adrienverge/openfortivpn).

O endpoint (host e porta) e o usuário são informados na interface. 

> **Importante:** obtenha o endpoint, as credenciais, o token MFA e a impressão
> digital do certificado com a organização responsável pela VPN. Nunca publique
> esses dados em commits, issues, logs ou capturas de tela.

## Requisitos

O aplicativo foi desenvolvido para Linux e testado no Ubuntu. São necessários:

- `openfortivpn`;
- Python 3;
- Tkinter;
- `pexpect`;
- PyInstaller, apenas para compilar o binário distribuível;
- acesso a `sudo` durante a configuração inicial.

### Ubuntu e Debian

```bash
sudo apt update
sudo apt install -y openfortivpn python3 python3-tk python3-venv
```

Em outras distribuições, instale os pacotes equivalentes com o gerenciador de
pacotes local.

## Instalação

### 1. Baixe o projeto

```bash
git clone <URL_DO_REPOSITORIO>
cd feature-vpn
```

Substitua `<URL_DO_REPOSITORIO>` pela URL deste repositório.

### 2. Crie o ambiente Python

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

### 3. Configure as permissões necessárias

Execute este passo uma única vez, como seu usuário normal:

```bash
./scripts/setup.sh
```

Não execute o script usando `sudo` diretamente:

```bash
# Correto
./scripts/setup.sh

# Incorreto
sudo ./scripts/setup.sh
```

O script solicita sua senha administrativa somente quando necessário e:

1. verifica se `openfortivpn` está instalado;
2. cria a regra `/etc/sudoers.d/openfortivpn` para a interface executar
   `openfortivpn` e `kill` sem pedir uma senha a cada conexão;
3. cria `/var/log/openfortivpn` e o torna gravável pelo usuário atual.

Revise `scripts/setup.sh` antes de executá-lo, principalmente em máquinas
corporativas ou compartilhadas. Siga as políticas da organização e peça auxílio
ao administrador de TI quando necessário.

O setup também compila e instala o aplicativo na galeria. Mantenha o ambiente
virtual ativado enquanto o executa.

O instalador cria os arquivos abaixo apenas para o usuário atual:

| Item              | Caminho                                             |
| ----------------- | --------------------------------------------------- |
| Binário          | `~/.local/bin/openfortivpn-gui`                   |
| Atalho da galeria | `~/.local/share/applications/vpn-connect.desktop` |

Depois, procure por **VPN Connect** na galeria de aplicativos do ambiente
gráfico.

## Uso

1. Abra **VPN Connect** pela galeria de aplicativos.
2. Informe o endpoint fornecido pela organização, normalmente no formato
   `vpn.exemplo.org:443`.
3. Informe o usuário da VPN.
4. Clique em **Conectar**.
5. Quando solicitado, informe a senha e o token de autenticação multifator.

### Primeira conexão e certificado confiável

Na primeira conexão de cada perfil, o aplicativo solicitará a senha da VPN
**duas vezes**. Isso é esperado e acontece porque o `openfortivpn` realiza duas
execuções:

1. na primeira execução, ele acessa o gateway, encontra um certificado que
   ainda não está na lista local de confiança, informa sua impressão digital
   SHA-256 e encerra a tentativa;
2. o aplicativo identifica essa impressão digital, salva o valor no cache e
   executa o `openfortivpn` novamente com a opção `--trusted-cert`. Como é um
   novo processo de autenticação, a senha precisa ser informada outra vez.

O certificado fica armazenado por perfil em:

```text
~/.cache/openfortivpn-gui-trusted-cert-<nome_do_perfil>
```

Nas conexões seguintes, o valor é lido do cache e normalmente a senha será
solicitada apenas uma vez. O processo de duas solicitações ocorrerá novamente
se o arquivo de cache for removido ou se o certificado do gateway for renovado
ou substituído.

> **Segurança:** antes da primeira conexão, confirme com a organização
> responsável pela VPN se a impressão digital SHA-256 apresentada nos logs é a
> esperada. O aplicativo salva automaticamente essa impressão digital para
> conseguir realizar a segunda tentativa.

O aplicativo salva perfis localmente, permitindo alternar entre combinações de
endpoint e usuário. Para salvar um perfil, informe os dados, escolha um nome e
use **Salvar perfil**. As credenciais e tokens não são gravados pelo aplicativo.

Use **Desconectar** antes de fechar a janela para encerrar a sessão de forma
limpa.

## Verificar conexão e rotas

Após conectar, confirme se a interface VPN foi criada:

```bash
ip -4 addr show dev ppp0
```

Liste as rotas criadas para o túnel:

```bash
ip -4 route show dev ppp0
```

Para confirmar por qual interface um endereço interno será acessado:

```bash
ip -4 route get <IP_INTERNO>
```

O resultado esperado deve incluir `dev ppp0` para um endereço que pertença à
rede corporativa.

## Logs

Os logs de conexão são gravados em:

```text
/var/log/openfortivpn/
```

Veja os registros recentes:

```bash
tail -n 200 /var/log/openfortivpn/openfortivpn-*.log
```

Filtre eventos de conexão, rotas e erros:

```bash
grep -iEn 'route|split|ppp|tun|gateway|error|fail|permission' \
  /var/log/openfortivpn/openfortivpn-*.log | tail -n 150
```

> Logs podem conter endpoints e endereços IP internos. Não os publique sem
> revisão e remoção dos dados sensíveis.

## Desenvolvimento

Para executar o código-fonte sem gerar o binário:

```bash
source .venv/bin/activate
python3 src/vpn_gui.py
```

Após alterar `src/vpn_gui.py`, a galeria continuará usando o binário instalado
anteriormente. Compile e instale novamente para atualizar o aplicativo:

```bash
source .venv/bin/activate
./scripts/setup.sh
```

O binário gerado fica em `dist/openfortivpn-gui`. Os diretórios `build/` e
`dist/` são artefatos locais de compilação e não devem ser enviados ao Git.

## Desinstalação

Remova o binário e o atalho da galeria:

```bash
./scripts/uninstall-user.sh
```

Esse script preserva perfis, certificados em cache e logs. Para remover também
a regra de permissões criada pelo setup:

```bash
sudo rm -f /etc/sudoers.d/openfortivpn
sudo visudo -c
```

Se desejar remover os logs:

```bash
sudo rm -rf /var/log/openfortivpn
```

## Dados locais

| Dado                                    | Local                                        |
| --------------------------------------- | -------------------------------------------- |
| Perfis                                  | `~/.config/openfortivpn-gui/profiles.json` |
| Certificados confiados                  | `~/.cache/openfortivpn-gui-trusted-cert-*` |
| PID da conexão                         | `~/.cache/openfortivpn-gui.pid`            |
| Configuração opcional do openfortivpn | `~/.config/openfortivpn/config`            |
| Logs                                    | `/var/log/openfortivpn/`                   |

Não envie ao Git nem compartilhe publicamente arquivos que contenham endpoints
internos, usuários, certificados, tokens, senhas, perfis ou logs.

## Solução de problemas

### `openfortivpn` não foi encontrado

Instale o pacote:

```bash
sudo apt install openfortivpn
```

Depois execute novamente:

```bash
./scripts/setup.sh
```

### O aplicativo não aparece na galeria

Reinstale o launcher e atualize o banco de atalhos:

```bash
./scripts/install-user.sh
update-desktop-database ~/.local/share/applications
```

Se necessário, encerre a sessão gráfica e entre novamente.

### Alterações no código não aparecem no aplicativo

Recompile e reinstale o binário:

```bash
source .venv/bin/activate
./scripts/setup.sh
```

### A VPN conecta, mas não acessa a rede interna

Confirme as rotas instaladas:

```bash
ip -4 route show dev ppp0
```

Se a rota para a rede necessária não existir, solicite à equipe de rede as redes
que devem ser distribuídas pela VPN. O servidor deve fornecer as rotas, ou a
equipe responsável deve informar a configuração de split tunnel apropriada.

### Há erro de permissão ao conectar

Execute o setup novamente e valide a regra do `sudo`:

```bash
./scripts/setup.sh
sudo visudo -c
```
