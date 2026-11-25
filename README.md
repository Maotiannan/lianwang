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

手动代理默认使用订阅中第一个可用类型的代理节点，专用组排除直连和拒绝节点。节点的连通性和 UDP 能力取决于订阅。独立进程（例如应用的辅助进程）按各自的 .exe 选择。Windows DNS 客户端、Docker 和 cloudflared 保留原路径。界面显示已选策略。退出托盘后后台继续运行，登录后托盘自动出现。卸载后需要在 Clash Verge 重新应用原订阅。

Windows 安装程序和管理界面目前未作代码签名。企业驱动策略可阻止 WinDivert 加载。原厂内核驱动签名在构建阶段校验。未在使用个人远程连接的设备上做驱动安装测试。

### 构建

Windows x64，Visual Studio C++ 工具链、.NET 10 SDK 和 Inno Setup 6。在 x64 开发者 PowerShell 运行 `windows/packaging/build.ps1`。该脚本锁定 [ProxyBridge 4.3.0 源码](https://github.com/InterceptSuite/ProxyBridge/tree/v4.3.0_Windows) 和 [WinDivert 2.2.2 发布包](https://github.com/basil00/WinDivert/releases/tag/v2.2.2)，只编译用户态转发库，保留系统防火墙策略。许可证随安装包分发。Windows 集成测试只在一次性 GitHub Actions 虚拟机执行。
