# Debian/Ubuntu VPS 部署

本文面向没有桌面环境、通过 SSH 管理的 Debian/Ubuntu VPS。pawbot 是单进程优先的
本地 Agent，推荐先完成一次交互式初始化，再交给 systemd 常驻。

## 选择访问方式

- 有域名：让 pawbot 只监听 `127.0.0.1`，前面使用 Caddy 或 Nginx 提供 HTTPS。
- 没有域名：可以临时使用 `服务器 IP:端口`。远程 WebUI 会自动生成随机 WebSocket
  入口和强随机访问密码；随机路径只是降低扫描噪声，真正的认证仍是密码，不能替代
  HTTPS。
- 最安全的临时方式：WebUI 仍监听 localhost，通过 SSH 隧道访问，不开放 VPS 防火墙端口。

## 安装与首次初始化

建议预先安装 Python、`venv` 和 curl：

```bash
sudo apt-get update
sudo apt-get install -y curl python3 python3-venv
```

发布版本安装脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/m2dumpling/pawbot/v0.3.8/scripts/install.sh | sh
```

如果当前 shell 没有交互终端，安装器会跳过 wizard，并打印稍后可执行的命令。通过
SSH 登录时建议显式使用：

```bash
pawbot onboard --wizard
```

wizard 会配置 Provider、API Key、模型和本机 WebUI 密码。所有密码输入都不会回显。

## 没有域名：IP + 随机路径 + 密码

这是最方便的无域名路径：

```bash
pawbot webui --remote --yes --no-open --show-access --detach
```

命令会配置并在后台保留 gateway，然后：

1. 创建 `~/.pawbot/config.json` 和 workspace（如果尚不存在）；
2. 把 WebUI 绑定到 `0.0.0.0:8765`；
3. 生成强随机的 WebSocket 路径和访问密码；
4. 输出一个可以复制到本地浏览器的认证 URL；
5. 在没有 Provider 配置时仍启动 WebUI，登录后进入 **Settings → Models** 完成模型配置。

`--show-access` 会在终端输出包含密码的 URL，只应在自己的 SSH 会话中使用，不要把
终端输出写入公共日志、截图或工单。若不希望密码出现在终端，去掉该选项，打开命令
输出中的地址后，在 WebUI 密码框中手动输入配置文件里的
`channels.websocket.tokenIssueSecret`。

云厂商安全组和 VPS 防火墙只放行 WebUI 端口，不要放行 Gateway health 端口：

```bash
sudo ufw allow 8765/tcp
sudo ufw status
```

IP 直连默认是 HTTP，只适合临时或受信任网络。公网长期运行请改用域名 HTTPS、VPN
或 SSH 隧道。`--detach` 适合快速启动，但 VPS 重启后不会自动恢复；长期运行应安装
systemd 服务。

## 有域名：Caddy + HTTPS

先用 `pawbot onboard --wizard` 完成 Provider 和本机 WebUI 初始化，然后让 gateway
以 systemd user service 常驻：

```bash
sudo loginctl enable-linger "$USER"
pawbot gateway install-service --manager systemd --enable --start
pawbot gateway status
```

安装 Caddy 后，将 `example.com` 换成自己的域名：

```caddyfile
example.com {
    reverse_proxy 127.0.0.1:8765
}
```

Caddy 会处理 HTTPS 和 WebSocket upgrade；pawbot 只需保持 localhost 监听。域名方式
不需要 `--remote`。如果此前为了 IP 访问改成了公网监听，应先用：

```bash
pawbot webui --host 127.0.0.1 --yes --no-open
```

然后重启 gateway，使新配置生效。

## SSH 隧道：不开放公网端口

让 WebUI 保持默认 localhost，并让命令立即返回：

```bash
pawbot webui --yes --no-open --detach
```

在本地电脑另开终端：

```bash
ssh -N -L 8765:127.0.0.1:8765 <用户>@<服务器>
```

然后打开 `http://127.0.0.1:8765`。如果使用自定义端口，把两处 `8765` 同时替换。

## 直接使用 TUI

SSH 必须分配交互式终端：

```bash
ssh -t <用户>@<服务器> pawbot agent
```

安装发布版本后，`pawbot agent` 会下载并校验当前平台的原生 TUI，并自动连接本机
gateway。如果对应 Release 的 TUI 资产尚未上传，临时使用兼容性较好的 Python prompt：

```bash
pawbot agent --classic
```

## Telegram Bot

服务器上最省事的配置方式是先用 SSH 隧道打开 WebUI，再进入 **Settings → Channels →
Telegram**：

1. 在 BotFather 创建 Bot，把 token 填入 Telegram channel；
2. 启用 channel，保存设置并重启 gateway（首次启用时会准备 Telegram 可选依赖）；
3. 保持 `allowFrom` 为空时，首次私聊会进入 pairing 流程；
4. 在 WebUI 的 **Settings → Pairing** 批准自己的 Telegram 用户，或填入明确的数字
   用户 ID；
5. 用 `pawbot gateway install-service --manager systemd --enable --start` 保证重启后
   Bot 自动恢复。

Telegram polling 模式不需要域名或公网入站端口；只有 webhook 模式才需要公网 HTTPS
域名和额外的 webhook 配置。

## 日常检查

```bash
pawbot status
pawbot gateway status
pawbot gateway logs
```

不要把 `config.json`、Provider Key、Telegram token、WebUI bootstrap secret 或 SSH
终端输出提交到 Git。单个 WebUI 密码适合个人/小规模使用；如果需要多用户账号、撤销
和审计，应在 pawbot 前面部署具备这些能力的身份代理。
