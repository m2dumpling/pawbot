# Debian/Ubuntu VPS deployment

This guide is for a headless Debian/Ubuntu VPS managed over SSH. Pawbot is
single-process-first, so complete the interactive setup once and then keep the
gateway running with a systemd user service.

## Choose an access model

- With a domain: keep pawbot on `127.0.0.1` and put Caddy or Nginx in front of
  it for HTTPS.
- Without a domain: use `server-ip:port` temporarily. Remote WebUI setup now
  generates a random WebSocket path and a strong access password. The random
  path only reduces scanning noise; the password is the actual authentication
  boundary and does not replace HTTPS.
- For the safest temporary access: keep pawbot on localhost and use an SSH
  tunnel instead of opening a firewall port.

## Install and initialize

Install Python, venv support, and curl first:

```bash
sudo apt-get update
sudo apt-get install -y curl python3 python3-venv
```

Install the release:

```bash
curl -fsSL https://raw.githubusercontent.com/m2dumpling/pawbot/v0.3.8/scripts/install.sh | sh
```

If the shell has no interactive terminal, the installer skips the wizard and
prints the command to run later. Over SSH, run:

```bash
pawbot onboard --wizard
```

The wizard configures the Provider, API key, model, and local WebUI password.
Secret inputs are not echoed.

## No domain: IP + random path + password

Run this on the VPS:

```bash
pawbot webui --remote --yes --no-open --show-access --detach
```

It creates the config/workspace, binds WebUI to `0.0.0.0:8765`, generates a
random WebSocket path and access password, prints a copyable authenticated URL,
and leaves the managed gateway running in the background. If no Provider is
configured yet, log in to the WebUI and finish setup in **Settings → Models**.

`--show-access` prints a URL containing the password. Use it only in a private
SSH session; do not copy the terminal output to public logs, screenshots, or
tickets. Without that option, open the printed address and enter
`channels.websocket.tokenIssueSecret` from the config file manually.

Open only the WebUI port in the cloud firewall/VPS firewall; keep the gateway
health port private:

```bash
sudo ufw allow 8765/tcp
sudo ufw status
```

IP-only access is HTTP and is suitable only for temporary or trusted networks.
Use HTTPS, a VPN, or an SSH tunnel for long-term public access. `--detach` keeps
the gateway running after the command returns, but it does not make the process
boot-persistent; use systemd for that.

## Domain: Caddy + HTTPS

Complete `pawbot onboard --wizard` first, then enable lingering and install the
gateway user service:

```bash
sudo loginctl enable-linger "$USER"
pawbot gateway install-service --manager systemd --enable --start
pawbot gateway status
```

After installing Caddy, replace `example.com` with the real domain:

```caddyfile
example.com {
    reverse_proxy 127.0.0.1:8765
}
```

Caddy handles HTTPS and WebSocket upgrade. Pawbot stays bound to localhost;
the domain path does not need `--remote`.

## SSH tunnel

Keep the default localhost binding and return immediately:

```bash
pawbot webui --yes --no-open --detach
```

From the local computer:

```bash
ssh -N -L 8765:127.0.0.1:8765 <user>@<server>
```

Open `http://127.0.0.1:8765` locally.

## Native TUI

Allocate an interactive terminal over SSH:

```bash
ssh -t <user>@<server> pawbot agent
```

The released package downloads and verifies the native TUI asset for the
current platform. If a release's TUI asset is not available yet, use the
compatibility prompt temporarily:

```bash
pawbot agent --classic
```

## Telegram Bot

The easiest server setup is to open the WebUI through an SSH tunnel and go to
**Settings → Channels → Telegram**:

1. Create a Bot with BotFather, paste its token, and enable the channel.
2. Save the settings and restart the gateway; the Telegram optional dependency
   is prepared when the channel is enabled.
3. Leave `allowFrom` empty to use pairing for the first private message.
4. Approve your Telegram user in **Settings → Pairing**, or enter an explicit
   numeric user ID.
5. Keep it running after reboot with the systemd service command above.

Telegram polling does not require a domain or inbound public port. Webhook mode
requires a public HTTPS domain and its webhook settings.

## Daily checks

```bash
pawbot status
pawbot gateway status
pawbot gateway logs
```

Never commit `config.json`, Provider keys, Telegram tokens, WebUI bootstrap
secrets, or SSH terminal output. A single WebUI password is suitable for a
personal or small deployment; use an identity-aware proxy for multi-user
accounts, revocation, and audit requirements.
