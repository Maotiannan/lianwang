# 联网 · macOS

从 Mac 菜单栏为单个应用选择网络出口。需要 Apple 芯片、macOS 14 或更高版本，以及正在运行的 Clash Verge Rev。此版本适配 Clash Verge Rev 2.5.10 和 MClash 1.6.2。

[下载 macOS 安装包](https://github.com/Maotiannan/lianwang/releases/latest) · [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev/releases)

## 第一次安装

1. 在 Clash Verge Rev 启用服务模式、系统代理，选择「规则」模式并关闭 TUN。确认订阅能上网，系统 SOCKS 代理指向 Clash 的本机混合端口。运行时还需要 Apple 命令行工具提供的 Python 3、Node.js 和 Mike Farah 的 yq 4。已有 Homebrew 时可运行 `brew install node yq`。如缺少 Apple 命令行工具，可用 `xcode-select --install` 安装。
2. 下载 Release 中的 `Lianwang-macOS-arm64-*.zip` 并解压。双击 **安装联网.command**。脚本从 MClash 官方下载后台，核对 SHA-256 和原厂签名后，在本机组装为 `/Applications/联网.app`。如果下载失败，把官方的 `MClash-1.6.2-macos-arm64.zip` 放在脚本旁再运行。外层应用使用本地临时签名，未经过 Apple 公证。请在信任来源的前提下使用系统正常的打开流程。[MClash 官方下载](https://github.com/leaperone/MClash/releases/tag/v1.6.2) · [MClash 许可](https://github.com/leaperone/MClash/blob/v1.6.2/LICENSE)
3. **在远程会话结束后**双击 **首次启用.command**。首次启用会在 Clash 加载分流规则，并请求 macOS 允许 MClash 网络扩展，可能短暂中断已有连接。按系统提示完成授权，如提示重启，重启后再运行「首次启用」。就绪后将注册当前用户的登录启动项。已单独运行 MClash、使用自己的 MClash 工作区时，请先处理现有会话和工作区。
4. 点击菜单栏箭头打开「联网」。若 macOS 显示了后台图标、菜单提示缺少菜单栏权限，可在「系统设置 → 隐私与安全性 → 完全磁盘访问权限」允许「联网」，再重开菜单程序。此权限用于修复主图标和后台图标的显示。

已有 `/Applications/联网.app` 时，安装程序会退出以保护正在运行的分流后台。本发布包供新装使用。

## 日常使用

在菜单面板中选择应用。新添加的应用默认「自动」。

| 选择 | 新连接的出口 |
| --- | --- |
| 自动 | 遵循系统代理和当前 Clash 订阅规则 |
| 直连 | 使用直连出口 |
| 代理 | 跟随当前订阅的代理选择。若订阅兜底为直连或没有可用代理，面板会提示检查线路 |

切换在现有后台更新规则，已有连接可能继续使用之前的出口。Docker、cloudflared 和系统 DNS 在新装配置中使用受保护的路径。「退出联网」关闭菜单，已启用的后台继续工作。应用从下次登录起自动启动。

## 构建与验证

在 Apple 芯片 Mac 上运行下列命令。单元测试使用合成配置；构建在 `dist/` 生成安装包和校验文件。

```sh
/usr/bin/python3 -B -m unittest discover -s tests
./packaging/build-release.sh
```

版本来自 `macos/Info.plist`。安装包只分发本项目的程序与安装脚本。安装时再由使用者的 Mac 获取 MClash。可在解压目录运行 `./安装联网.command /已有的临时目录` 验证组装，无需启动分流或改动系统网络。
