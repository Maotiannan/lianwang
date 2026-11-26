#!/bin/zsh
set -eu
setopt PIPE_FAIL
release=${0:A:h}
# An optional existing directory also allows assembly without writing /Applications.
dest=${1:-/Applications}
dest=${dest:A}
if [[ "$(/usr/bin/uname -m)" != arm64 || "$(/usr/bin/sw_vers -productVersion | cut -d. -f1)" -lt 14 ]]; then
  print -u2 '此版本需要配备 Apple 芯片、运行 macOS 14 或更高版本的 Mac。'; exit 1
fi
if [[ -e "$dest/联网.app" || -L "$dest/联网.app" ]]; then
  print -u2 "已存在 $dest/联网.app。此安装程序只用于新装；已在使用联网的 Mac 可以继续使用现有版本。"; exit 1
fi
if [[ ! -w "$dest" || ! -d "$dest" ]]; then
  print -u2 "无法写入 $dest。可以在终端把一个可写的文件夹路径作为参数传给此安装程序，再用访达将生成的应用拖入「应用程序」。"; exit 1
fi
stage=$(mktemp -d "$dest/.lianwang-install.XXXXXXXX")
trap 'rm -rf "$stage"' EXIT
filename=MClash-1.6.2-macos-arm64.zip
archive="$release/$filename"
if [[ ! -f "$archive" ]]; then
  print '正在从 MClash 官方发布页获取 1.6.2 版后台组件…'
  archive="$stage/$filename"
  proxy_opts=()
  if [[ -z "${https_proxy:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-}" ]]; then
    proxyinfo=$(/usr/sbin/scutil --proxy)
    enabled=$(awk '$1=="HTTPSEnable"{print $3}' <<< "$proxyinfo")
    proxyhost=$(awk '$1=="HTTPSProxy"{print $3}' <<< "$proxyinfo")
    proxyport=$(awk '$1=="HTTPSPort"{print $3}' <<< "$proxyinfo")
    if [[ "$enabled" == 1 && "$proxyhost" == (127.0.0.1|localhost) && "$proxyport" == <1-65535> ]]; then
      proxy_opts=(--proxy "http://$proxyhost:$proxyport")
    fi
  fi
  /usr/bin/curl -q "${proxy_opts[@]}" --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
    --retry 2 --connect-timeout 12 --max-time 180 -o "$archive" \
    "https://github.com/leaperone/MClash/releases/download/v1.6.2/$filename" || {
      print -u2 "下载失败。可用浏览器从 MClash 官方 v1.6.2 发布页下载 $filename，放在本脚本旁边再打开。"; exit 1
    }
fi
digest=$(/usr/bin/shasum -a 256 "$archive" | /usr/bin/awk '{print $1}')
[[ "$digest" == c1fbf44b059f96a4caca4cd89f2340e91c11a266a2a062f2b09cf71be38805db ]] || { print -u2 'MClash 下载文件的 SHA-256 不匹配。'; exit 1; }
/usr/bin/ditto -x -k "$archive" "$stage/vendor"
helper="$stage/vendor/MClash.app"
requirement='=identifier "one.leaper.mclash" and anchor apple generic and certificate leaf[subject.OU] = "5UAHRS482C"'
/usr/bin/codesign --verify --deep --strict -R "$requirement" "$helper"
app="$stage/联网.app"
/usr/bin/ditto "$release/资源" "$app/Contents"
mkdir -p "$app/Contents/Helpers"
/bin/mv "$helper" "$app/Contents/Helpers/MClash.app"
/usr/bin/codesign --force --sign - --identifier local.network-egress.menu "$app"
/usr/bin/codesign --verify --deep --strict "$app"
/bin/mv -n "$app" "$dest/"
[[ ! -e "$app" ]] || { print -u2 '目标已被其他安装占用。'; exit 1; }
print "已生成 $dest/联网.app。将应用放入「应用程序」后，双击「首次启用.command」配置菜单和登录启动。"
