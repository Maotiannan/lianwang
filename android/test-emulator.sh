#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 ../windows/tests/socks_fixture.py &
fixture=$!
trap 'kill "$fixture" 2>/dev/null || true' EXIT
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
for kind in guard target; do adb install -r "probe/build/outputs/apk/$kind/debug/probe-$kind-debug.apk"; done
pkg=io.github.maotiannan.lianwang
adb shell appops set "$pkg" ACTIVATE_VPN allow
adb shell am start -W -n "$pkg/.MainActivity"
result=$(adb shell am instrument -w "$pkg.test/$pkg.RoutingChecks")
echo "$result"
[[ "$result" == *"passed real app UID routing"* && "$result" == *"INSTRUMENTATION_CODE: -1"* ]]
