# Run from a Visual Studio x64 native-tools PowerShell, at the repository root.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$root = (Resolve-Path "$PSScriptRoot/../..").Path
Set-Location $root
$deps = "$root/deps"
$out = "$root/out/windows"
New-Item -ItemType Directory -Force $deps,$out | Out-Null
function Fetch($url, $file, $digest) {
    if (!(Test-Path $file)) { Invoke-WebRequest $url -OutFile $file }
    if ((Get-FileHash $file -Algorithm SHA256).Hash -ne $digest) { throw "SHA256 mismatch: $file" }
}
$version = '8cb9dceeed582fcd7c2baed8d20eec34934732fc'
$pb = "$deps/proxybridge"
if (!(Test-Path "$pb/.git")) {
    git init -q $pb
    git -C $pb fetch -q --depth 1 https://github.com/InterceptSuite/ProxyBridge.git $version
    git -C $pb checkout -q --detach FETCH_HEAD
}
if ((git -C $pb rev-parse HEAD) -ne $version) { throw 'Unexpected ProxyBridge source revision' }
Fetch 'https://github.com/basil00/WinDivert/releases/download/v2.2.2/WinDivert-2.2.2-A.zip' "$deps/driver.zip" '63cb41763bb4b20f600b6de04e991a9c2be73279e317d4d82f237b150c5f3f15'
Expand-Archive "$deps/driver.zip" $deps -Force
$wd = "$deps/WinDivert-2.2.2-A"
if ((Get-AuthenticodeSignature "$wd/x64/WinDivert64.sys").Status -ne 'Valid') { throw 'Invalid WinDivert kernel signature' }
$src = "$deps/native"
New-Item -ItemType Directory -Force $src | Out-Null
Copy-Item "$pb/Windows/src/*.*" $src -Force
# The tray never opens an all-profiles inbound firewall exception. A connection already in
# progress when the first override is added must pass unchanged until a new handshake.
$core = [IO.File]::ReadAllText("$src/ProxyBridge.c")
$fw = 'g_fw_thread = CreateThread(NULL, 0, fw_ensure_thread, NULL, 0, NULL);'
if (!$core.Contains($fw)) { throw 'Unrecognized firewall initialization' }
$core = $core.Replace($fw, '/* Respect the existing Windows Firewall policy. */')
foreach ($comment in 'Teardown of an untracked flow:', 'Teardown of a flow we are not tracking:') {
    $pos = $core.IndexOf($comment)
    if ($pos -lt 0) { throw 'Unrecognized TCP connection path' }
    $index = $core.IndexOf('if (tcp_header->Fin || tcp_header->Rst)', $pos)
    if ($index -lt 0 -or $index - $pos -gt 600) { throw 'Unrecognized TCP connection guard' }
    $core = $core.Substring(0,$index) + 'if (!tcp_header->Syn || tcp_header->Ack || tcp_header->Fin || tcp_header->Rst)' + $core.Substring($index + 'if (tcp_header->Fin || tcp_header->Rst)'.Length)
}
# Only WinDivert's locally redirected packets may reach the two relay ports from
# a non-loopback source. Fresh packets arriving from a physical NIC are discarded.
$anchor = '        if (ip_header == NULL)'
if ([regex]::Matches($core,[regex]::Escape($anchor)).Count -ne 1) { throw 'Unexpected packet dispatch layout' }
$guard = @'
        if (!addr.Outbound && !addr.Loopback &&
            ((tcp_header && tcp_header->DstPort == htons(g_local_relay_port)) ||
             (udp_header && udp_header->DstPort == htons(LOCAL_UDP_RELAY_PORT))))
            continue;

'@
$core = $core.Replace($anchor, $guard + $anchor)
[IO.File]::WriteAllText("$src/ProxyBridge.c", $core)
Push-Location $src
try {
    $sources = (Get-ChildItem *.c).Name
    cl /nologo /O2 /MT /LD /D_CRT_SECURE_NO_WARNINGS /D_WINSOCK_DEPRECATED_NO_WARNINGS /DPROXYBRIDGE_EXPORTS "/I$wd/include" @sources /link "/LIBPATH:$wd/x64" WinDivert.lib ws2_32.lib iphlpapi.lib advapi32.lib "/OUT:$out/ProxyBridgeCore.dll"
} finally { Pop-Location }
dotnet publish windows/src/Lianwang.csproj -c Release -r win-x64 --self-contained true -o $out '-p:DebugType=None'
Copy-Item "$wd/x64/WinDivert.dll","$wd/x64/WinDivert64.sys" $out
Copy-Item "$wd/LICENSE" "$out/WinDivert-LICENSE.txt"
Copy-Item "$pb/LICENSE" "$out/ProxyBridge-LICENSE.txt"
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
& $iscc windows/packaging/setup.iss
