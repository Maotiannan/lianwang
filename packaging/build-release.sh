#!/bin/zsh
set -eu
setopt PIPE_FAIL
root=${0:A:h:h}
src="$root/macos"
version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$src/Info.plist")
outdir=${1:-"$root/dist"}
mkdir -p "$outdir"
outdir=${outdir:A}
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
product="$scratch/联网-macOS"
payload="$product/资源"
mkdir -p "$payload/MacOS" "$payload/Resources"
export CLANG_MODULE_CACHE_PATH="$scratch/module-cache"
xcrun swiftc -parse-as-library -swift-version 5 -O -target arm64-apple-macosx14.0 \
  -framework AppKit -framework SwiftUI "$src/NetworkEgress.swift" -o "$payload/MacOS/NetworkEgress"
xcrun swiftc -swift-version 5 -O -target arm64-apple-macosx14.0 \
  -framework AppKit "$src/EngineLauncher.swift" -o "$payload/MacOS/EngineLauncher"
codesign --force --sign - "$payload/MacOS/EngineLauncher"
cp "$src/"*.py "$src/routing.js" "$src/Assets/AppIcon.icns" "$payload/Resources/"
cp "$src/Info.plist" "$payload/"
cp "$src/local.network-egress.login.plist" "$root/packaging/"*.command "$product/"
ditto -c -k --norsrc --noextattr --noqtn --keepParent "$product" "$scratch/release.zip"
mv "$scratch/release.zip" "$outdir/Lianwang-macOS-arm64-$version.zip"
(cd "$outdir" && shasum -a 256 "Lianwang-macOS-arm64-$version.zip" > SHA256SUMS.txt)
