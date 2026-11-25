# 联网

按应用选择自动、直连或代理。各平台的安装方式和支持范围随对应版本发布。

## Windows x64

Windows 10（2004 或更新）和 Windows 11，单用户管理员账户，Clash Verge Rev 2.5.10 或兼容版本。首次安装需要管理员权限。联网后台使用 WinDivert 2.2.2 的原厂签名驱动，托盘使用普通权限。

1. 启动 Clash Verge，启用系统代理并选择规则模式，确认当前订阅可以上网。
2. 安装 Windows x64 安装包。在 Clash Verge 重新应用当前订阅一次，让它载入联网的扩展脚本。首次接入可安排在远程会话结束后进行。
3. 点击任务栏通知区域的联网箭头，搜索正在运行的程序，或粘贴本机 .exe 完整路径。点击应用旁边的模式。

| 模式 | 新连接如何处理 |
| --- | --- |
| 自动 | 恢复应用原来的联网方式。使用系统代理的请求遵循 Clash 订阅规则，原生直连套接字按 Windows 路由。 |
| 直连 | 原生连接直接出网，通过本机 Clash 的请求命中该应用的 DIRECT 规则。 |
| 代理 | 即使 TUN 关闭，WinDivert 也会将该程序的互联网 TCP、UDP 转到本机 Clash 的专用 SOCKS 入口。主动使用 Clash 的请求也有对应进程规则。 |

手动代理优先跟随订阅 MATCH 策略选中的实际代理节点。如果订阅默认直连，联网保留专用组中的代理节点，初次使用取订阅中的首个代理节点。节点的连通性和 UDP 能力取决于订阅。独立进程（例如应用的辅助进程）按各自的 .exe 选择。Windows DNS 客户端、Docker 和 cloudflared 保留原路径。界面显示已选策略。退出托盘后后台继续运行，登录后托盘自动出现。卸载后需要在 Clash Verge 重新应用原订阅。

Windows 安装程序和管理界面目前未作代码签名。企业驱动策略可阻止 WinDivert 加载。原厂内核驱动签名在构建阶段校验。未在使用个人远程连接的设备上做驱动安装测试。

### 构建

Windows x64，Visual Studio C++ 工具链、.NET 10 SDK 和 Inno Setup 6。在 x64 开发者 PowerShell 运行 `windows/packaging/build.ps1`。该脚本锁定 [ProxyBridge 4.3.0 源码](https://github.com/InterceptSuite/ProxyBridge/tree/v4.3.0_Windows) 和 [WinDivert 2.2.2 发布包](https://github.com/basil00/WinDivert/releases/tag/v2.2.2)，只编译用户态转发库，为后台程序添加 TCP 34010、UDP 34011 两条限定端口的防火墙规则，驱动过滤来自物理网卡的外部入站访问。许可证随安装包分发。Windows 集成测试只在一次性 GitHub Actions 虚拟机执行。

## Android

支持 Android 10 及以上的 arm64 手机和 x86_64 设备。首次点「开启」时，Android 会询问是否允许建立本机 VPN。同一用户配置内，Android 一次允许一个 VPN。可使用电脑或路由器上的 SOCKS5，或手机上以纯代理模式运行的客户端。

1. 安装 APK，点「出口设置」，填入手机可达的 `socks5://主机:端口`。需要认证时可填 `socks5://用户名:密码@主机:端口`。代理服务器需要支持 UDP ASSOCIATE 才能转发 UDP。
2. 如果有按 Clash 规则分流的独立入口，可填入「自动模式的规则入口」。留空时，自动模式沿用应用提出的连接目的地，通过原网络转发。用于「代理」的入口应指向实际代理出口。
3. 点「开启」，再点每个应用旁的「自动」「直连」或「代理」。VPN 已运行时，切换应用只更新其新连接的选择。关闭 VPN 会结束由它承载的连接。

列表显示具有启动图标的应用。分流依赖 Android 报告的套接字 UID，共享 UID 的应用一起切换。系统无法归属的连接采用自动策略。本机回环连接和应用自行封装的代理协议有各自的路由边界。系统 DNS 由底层网络提供。规则和代理凭据存于 Android 应用私有目录中，已关闭系统备份。

构建需要 Go 1.26、JDK 17、Gradle 8.11.1、Android SDK 35 和 NDK 28。运行 `android/build.sh` 生成 APK。Android 使用 [tun2socks](https://github.com/xjasonlyu/tun2socks/tree/v2.7.0) 和 [Android VpnService](https://developer.android.com/develop/connectivity/vpn)。测试用两个独立 UID 的小程序在模拟器中验证出口，覆盖同一对 TCP / UDP 套接字持续通信期间的应用切换。
