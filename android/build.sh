#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mobile=v0.0.0-20261117202452-47699113848a
go install "golang.org/x/mobile/cmd/gomobile@$mobile" "golang.org/x/mobile/cmd/gobind@$mobile"
export PATH="$(go env GOPATH)/bin:$PATH"
mkdir -p app/libs
(cd core && gomobile bind -androidapi 29 -target=android/arm64,android/amd64 -o ../app/libs/core.aar .)
gradle :app:assembleRelease :app:assembleDebug :app:assembleDebugAndroidTest :probe:assembleDebug --console=plain
