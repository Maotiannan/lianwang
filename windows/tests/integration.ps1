$ErrorActionPreference='Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') { throw 'Disposable GitHub-hosted runner required' }
$dir = Join-Path $env:RUNNER_TEMP 'lw-test'
New-Item -ItemType Directory -Force $dir | Out-Null
$profileDir = Join-Path $env:APPDATA 'io.github.clash-verge-rev.clash-verge-rev/profiles'
New-Item -ItemType Directory -Force $profileDir | Out-Null
$hook = "$profileDir/Script.js"
$before = 'function main(config) { config.rules.push("MATCH,DIRECT"); return config; }'
[IO.File]::WriteAllText($hook,$before)
$exe = "$env:ProgramFiles/Lianwang/Lianwang.exe"
function RunGui($arguments) {
    $process = Start-Process $exe -ArgumentList $arguments -PassThru -Wait -NoNewWindow -RedirectStandardOutput "$dir/result.json" -RedirectStandardError "$dir/error.txt"
    if($process.ExitCode -ne 0) { throw "Command failed ($arguments): $(Get-Content $dir/result.json) $(Get-Content $dir/error.txt)" }
}
$children = @()
try {
    $installer = Start-Process ./out/Lianwang-Windows-x64-Setup.exe '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -PassThru -Wait
    if($installer.ExitCode) { throw "Installer failed: $($installer.ExitCode)" }
    RunGui '--prepare'
    RunGui '--list'
    $state = Get-Content -Raw "$dir/result.json" | ConvertFrom-Json
    if($state.Message) { throw $state.Message }
    $url='https://github.com/MetaCubeX/mihomo/releases/download/v1.19.34/mihomo-windows-amd64-v1-v1.19.34.zip'
    Invoke-WebRequest $url -OutFile "$dir/core.zip"
    if((Get-FileHash "$dir/core.zip").Hash -ne '703e325bfeee582f874b06d054eaae55ab7c194645d1835411685aafa85da5ea') {throw 'Mihomo hash mismatch'}
    Expand-Archive "$dir/core.zip" "$dir/core"
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $hash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($sid))).ToLowerInvariant()
    $base=@{mode='rule';'mixed-port'=29181;'external-controller-pipe'="\\.\pipe\verge-mihomo-sidecar-release-$hash";rules=@();proxies=@(@{name='Fixture';type='socks5';server='127.0.0.1';port=29182;udp=$true})}
    [IO.File]::WriteAllText("$dir/base.json",($base | ConvertTo-Json -Depth 8))
    node -e "const fs=require('fs'),vm=require('vm');const f=vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8')+';main');fs.writeFileSync(process.argv[3],JSON.stringify(f(JSON.parse(fs.readFileSync(process.argv[2],'utf8')))))" $hook "$dir/base.json" "$dir/config.yaml"
    $children += Start-Process python -ArgumentList "$(Resolve-Path windows/tests/socks_fixture.py)" -PassThru
    $core = (Get-ChildItem "$dir/core" -Recurse -Filter '*.exe')[0].FullName
    $children += Start-Process $core -ArgumentList "-d $dir -f $dir/config.yaml" -RedirectStandardOutput "$dir/mihomo.log" -RedirectStandardError "$dir/mihomo.err" -PassThru
    $ready=$false
    for($i=0; $i -lt 80; $i++) {
        try { $s=[Net.Sockets.TcpClient]::new('127.0.0.1',29181); $s.Dispose(); $ready=$true; break } catch { Start-Sleep -Milliseconds 250 }
    }
    if(!$ready) {throw "Mihomo startup failed: $(Get-Content $dir/mihomo.log -Tail 5)"}
    $app = "$dir/target.exe"
    Copy-Item "$env:SystemRoot/System32/curl.exe" $app
    $other="$dir/guard.exe"
    & "$env:WINDIR/Microsoft.NET/Framework64/v4.0.30319/csc.exe" /nologo "/out:$other" (Resolve-Path windows/tests/Probe.cs).Path
    if($LASTEXITCODE) {throw 'Probe build failed'}
    $guard=$null
    function Choose($file,$mode) { RunGui "--set `"$file`" $mode" }
    function Explicit($expect) {
        $response=& $app --noproxy never.match --proxy http://127.0.0.1:29181 --silent --show-error --max-time 6 http://127.0.0.1:29184/
        if($LASTEXITCODE -or $response -ne $expect) {throw "Expected $expect via system proxy, got $response"}
    }
    # Windows uses backslashes for process identities.
    $app=(Get-Item $app).FullName; $other=(Get-Item $other).FullName
    Explicit 'LOCAL'
    Choose $other 'proxy'
    $guard=Start-Process $other "$dir/heartbeat" -PassThru -RedirectStandardError "$dir/guard.err"
    $children += $guard
    for($i=0;$i -lt 60 -and !(Test-Path "$dir/heartbeat.ready");$i++) {Start-Sleep -Milliseconds 250}
    if(!(Test-Path "$dir/heartbeat.ready")) {Get-Content "$dir/mihomo.log" -Tail 14 | ForEach-Object { $_ -replace '[0-9a-f]{64}','[redacted]' }; throw "TCP/UDP session did not start: $(Get-Content $dir/guard.err)"}
    foreach($mode in 'proxy','direct','auto','proxy','auto') {
        Choose $app $mode
        if($mode -eq 'proxy') {
            Explicit 'PROXY'
            $body=& $app --noproxy '*' --max-time 6 --silent --show-error http://192.0.2.81:29185/
            if($LASTEXITCODE -or $body -ne 'PROXY') {throw 'Raw socket bypassed proxy'}
        } else {Explicit 'LOCAL'}
        if($guard.HasExited) {throw 'Unrelated TCP/UDP connection was interrupted'}
    }
    New-Item "$dir/heartbeat.done" | Out-Null
    if(!$guard.WaitForExit(7000) -or $guard.ExitCode -ne 0) {throw 'Unrelated sockets failed to finish cleanly'}
    Choose $other 'auto'
    RunGui '--remove-hook'
    if([IO.File]::ReadAllText($hook) -cne $before) {throw 'Original Clash script was not restored'}
    Write-Host 'Signed driver loaded; native TCP + UDP, Clash explicit proxy, direct, auto and independent session continuity passed.'
} finally {
    foreach($p in $children) { if(!$p.HasExited) { $p.Kill() } }
    if(Test-Path "$env:ProgramFiles/Lianwang/unins000.exe") {
        Start-Process "$env:ProgramFiles/Lianwang/unins000.exe" '/VERYSILENT /NORESTART' -Wait
    }
}
