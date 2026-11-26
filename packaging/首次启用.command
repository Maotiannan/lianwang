#!/bin/zsh
set -eu
app=/Applications/联网.app
release=${0:A:h}
backend="$app/Contents/Resources/backend.py"
[[ -f "$backend" ]] || { print -u2 '请先双击「安装联网.command」，将「联网」安装在「应用程序」中。'; exit 1; }
expected=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$release/资源/Info.plist")
installed=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$app/Contents/Info.plist")
[[ "$expected" == "$installed" ]] || { print -u2 '已安装版本与本下载包不同，请使用对应版本。'; exit 1; }
[[ -x /opt/homebrew/bin/yq || -x /usr/local/bin/yq ]] || { print -u2 '请先安装 Mike Farah 的 yq 4，例如 brew install yq。'; exit 1; }
[[ -x /opt/homebrew/bin/node || -x /usr/local/bin/node ]] || { print -u2 '请先安装 Node.js，例如 brew install node。'; exit 1; }
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3,9))' || { print -u2 '请安装 Apple 命令行工具后重试。'; exit 1; }
print '确认 Clash Verge 使用规则模式、TUN 关闭、系统代理已开启。首次启用会向 Clash 写入分流规则、加载配置，并请求 macOS 网络扩展授权，现有远程连接可能短暂中断。'
printf '现在启用？输入 y 再回车，其他输入退出：'
read -r answer
[[ "$answer" == [Yy] ]] || exit 0
result=$(/usr/bin/python3 -B "$backend" setup) || { print -u2 -r -- "$result"; exit 1; }
extension_state=$(print -r -- "$result" | /usr/bin/python3 -c 'import sys,json; print(json.load(sys.stdin).get("state","unknown"))')
if [[ "$extension_state" != on ]]; then
  print -u2 "网络扩展状态为 $extension_state。请在「系统设置」允许 MClash 的网络扩展；若系统要求重启，完成后再次双击本脚本。"
  exit 1
fi
agents="$HOME/Library/LaunchAgents"
label=local.network-egress.login
file="$agents/$label.plist"
mkdir -p "$agents"
if [[ ! -e "$file" ]]; then
  /bin/cp -n "$release/$label.plist" "$file"
fi
/usr/bin/cmp -s "$file" "$release/$label.plist" || { print -u2 "登录项 $file 已有其他内容，请先核对。"; exit 1; }
domain="gui/$(id -u)"
if ! launchctl print "$domain/$label" >/dev/null 2>&1; then
  launchctl bootstrap "$domain" "$file"
else
  /usr/bin/open -g "$app"
fi
print '已启用。点击菜单栏箭头，给应用选择「自动」「直连」或「代理」。'
