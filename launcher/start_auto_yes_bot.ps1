#Requires -Version 5.1
<#
auto_yes_bot 원클릭 런처 (Windows 로컬 PC용).

  start_auto_yes_bot.bat 더블클릭 -> 이 스크립트 실행:
    1. 이전에 띄운 런처/relay.py/터널(-R) 프로세스를 정리
    2. GitHub에서 최신 코드(daemon/, tools/, launcher/) 받기 (config/는 안 건드림)
    3. 파이썬 의존성 확인, 없으면 설치
    4. relay.py 실행 + 감시(죽으면 재시작)
    5. 각 원격 서버에서 git pull
    6. 서버마다 SSH 터널(-R)을 열고 감시: 끊기면 자동 재연결, 기본 포트가
       유령 리스너에 막혀 있으면 예비 포트로 넘어가고, 그 서버의
       config/hook.json의 relay_port를 원격에서 자동으로 맞춘다.

  종료: 이 창에서 Ctrl+C, 또는 stop_auto_yes_bot.bat
#>
param([switch]$Stop, [switch]$NoUpdate)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$StateDir = Join-Path $Root '.state'
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

function Log([string]$msg, [string]$color = 'Gray') {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg) -ForegroundColor $color
}

function Read-LauncherConfig {
    $path = Join-Path $Root 'config\launcher.json'
    if (-not (Test-Path $path)) { $path = Join-Path $Root 'config\launcher.example.json' }
    if (-not (Test-Path $path)) { return $null }
    return (Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Read-LocalPort {
    $yaml = Join-Path $Root 'config\relay.yaml'
    if (-not (Test-Path $yaml)) { return $null }
    $m = Select-String -Path $yaml -Pattern '^\s*listen_port:\s*(\d+)' | Select-Object -First 1
    if ($m) { return [int]$m.Matches[0].Groups[1].Value }
    return $null
}

function Test-LocalPort([int]$port) {
    $c = New-Object Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(500) -and $c.Connected) { return $true }
        return $false
    } catch { return $false } finally { $c.Close() }
}

function Stop-Previous([int]$localPort) {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
        Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'start_auto_yes_bot\.ps1' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
        Where-Object { $_.CommandLine -match 'daemon[\\/]relay\.py' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    # Antigravity 자체 연결(ssh -D ... "bash -s")은 건드리지 않고, 우리 터널(-R ...:127.0.0.1:<local>)만 정리
    Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" |
        Where-Object { $_.CommandLine -match "\s-R\s+\d+:127\.0\.0\.1:$localPort\b" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Update-FromGitHub([string]$url) {
    $tmp = Join-Path $env:TEMP ("say_yes_" + [guid]::NewGuid().ToString('N'))
    $zip = "$tmp.zip"
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 30
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $src = (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName
        foreach ($d in 'daemon', 'tools', 'launcher') {
            if (Test-Path "$src\$d") {
                New-Item -ItemType Directory -Force -Path "$Root\$d" | Out-Null
                Copy-Item "$src\$d\*" "$Root\$d\" -Recurse -Force
            }
        }
        foreach ($f in 'start_auto_yes_bot.bat', 'stop_auto_yes_bot.bat', 'requirements-relay.txt') {
            if (Test-Path "$src\$f") { Copy-Item "$src\$f" "$Root\$f" -Force }
        }
        Get-ChildItem "$src\config" -Filter '*.example.*' -ErrorAction SilentlyContinue |
            ForEach-Object { Copy-Item $_.FullName "$Root\config\" -Force }
        return $true
    } catch {
        Log "GitHub 업데이트 실패 (기존 코드로 계속): $($_.Exception.Message)" Yellow
        return $false
    } finally {
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Remote([string]$sshHost, [string]$command) {
    # 원격(리눅스)은 UTF-8로 출력하는데 한글 Windows의 PowerShell 5.1은 CP949로 읽어서 한글이 깨진다
    $prevEnc = [Console]::OutputEncoding
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    try {
        $out = & ssh -o BatchMode=yes -o ConnectTimeout=10 $sshHost $command 2>&1 | ForEach-Object { "$_" } | Out-String
        $code = $LASTEXITCODE
    } finally {
        [Console]::OutputEncoding = $prevEnc
    }
    return @{ Code = $code; Out = $out.Trim() }
}

function Start-Relay {
    foreach ($n in 'relay.log', 'relay.err.log') {
        $p = Join-Path $StateDir $n
        if (Test-Path $p) { Move-Item $p "$p.prev" -Force }
        if ($script:TailPos) { $script:TailPos.Remove($p) }
    }
    $env:PYTHONIOENCODING = 'utf-8'
    $p = Start-Process -FilePath $Python -ArgumentList '-u daemon\relay.py --config config\relay.yaml' `
        -WorkingDirectory $Root -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $StateDir 'relay.log') `
        -RedirectStandardError (Join-Path $StateDir 'relay.err.log')
    $null = $p.Handle
    return $p
}

function Show-NewLines([string]$path, [string]$prefix, [string]$color) {
    if (-not (Test-Path $path)) { return }
    if ($null -eq $script:TailPos) { $script:TailPos = @{} }
    $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    try {
        if (-not $script:TailPos.ContainsKey($path) -or $script:TailPos[$path] -gt $fs.Length) { $script:TailPos[$path] = 0 }
        $fs.Seek($script:TailPos[$path], 'Begin') | Out-Null
        $reader = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)
        $text = $reader.ReadToEnd()
        $script:TailPos[$path] = $fs.Length
    } finally { $fs.Close() }
    foreach ($line in ($text -split "`r?`n")) {
        if (-not $line.Trim()) { continue }
        # relay.py는 이미 [relay]/[ntfy] 태그를 붙여 찍으니, 태그 없는 줄(트레이스백 등)에만 붙인다
        if ($line -match '^\[') { Log $line $color } else { Log "$prefix $line" $color }
    }
}

function Start-Tunnel($st, [int]$port) {
    $log = Join-Path $StateDir ("tunnel_{0}.log" -f $st.Host)
    if (Test-Path $log) { Remove-Item $log -Force -ErrorAction SilentlyContinue }
    # -v: 로그에 "remote forward success"가 찍혀야 포워딩이 실제로 열린 것으로 판정한다.
    $argLine = "-v -n -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 " +
               "-o ServerAliveCountMax=3 -o ConnectTimeout=10 -E `"$log`" " +
               "-R ${port}:127.0.0.1:$LocalPort $($st.Host)"
    $p = Start-Process -FilePath 'ssh' -ArgumentList $argLine -NoNewWindow -PassThru
    $null = $p.Handle
    $st.Proc = $p
    $st.Port = $port
    $st.StartedAt = Get-Date
    $st.Up = $false
}

function Get-TunnelLog($st) {
    $log = Join-Path $StateDir ("tunnel_{0}.log" -f $st.Host)
    if (-not (Test-Path $log)) { return '' }
    $fs = [IO.File]::Open($log, 'Open', 'Read', 'ReadWrite')
    try { return (New-Object IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Close() }
}

function Last-TunnelError([string]$log) {
    $lines = $log -split "`r?`n" | Where-Object { $_.Trim() -and $_ -notmatch '^(debug\d|OpenSSH_)' }
    return (($lines | Select-Object -Last 2) -join ' / ')
}

function Step-Tunnel($st, [datetime]$now) {
    if ($null -eq $st.Proc) {
        if ($now -ge $st.NextTry) {
            Log "$($st.Host): 터널 연결 시도 (원격 $($st.Ports[$st.Idx]) -> 로컬 $LocalPort)"
            Start-Tunnel $st $st.Ports[$st.Idx]
        }
        return
    }

    $log = Get-TunnelLog $st

    if ($st.Proc.HasExited) {
        if ($st.Up) {
            Log "$($st.Host): 터널 끊김 — 5초 후 재연결" Yellow
            $st.Idx = 0; $st.Fails = 0; $st.NextTry = $now.AddSeconds(5)
        } elseif ($log -match 'remote port forwarding failed') {
            # 그 원격 포트를 다른(유령) 리스너가 쥐고 있다 -> 예비 포트로
            Log "$($st.Host): 원격 포트 $($st.Port)이(가) 이미 사용 중 — 예비 포트로 전환" Yellow
            $st.Fails++
            $st.Idx = ($st.Idx + 1) % $st.Ports.Count
            if ($st.Fails -ge $st.Ports.Count) {
                Log "$($st.Host): 준비된 포트가 모두 사용 중 — 30초 후 재시도" Red
                $st.Fails = 0; $st.NextTry = $now.AddSeconds(30)
            } else {
                $st.NextTry = $now
            }
        } else {
            # 서버 접속 자체 실패(꺼짐/네트워크/인증) -> 포트를 바꿔도 소용없으니 같은 포트로 대기 후 재시도
            Log "$($st.Host): 서버 접속 실패 — 30초 후 재시도 ($(Last-TunnelError $log))" Red
            $st.NextTry = $now.AddSeconds(30)
        }
        $st.Proc = $null
        return
    }

    if ($st.Up) { return }

    if ($log -match 'remote forward success') {
        $st.Up = $true; $st.Fails = 0
        $r = Invoke-Remote $st.Host "python3 $($st.Dir)/tools/set_relay_port.py $($st.Port)"
        if ($r.Code -eq 0) { Log "$($st.Host): 연결 완료 — $($r.Out)" Green }
        else { Log "$($st.Host): 터널은 열렸지만 확인 실패 — $($r.Out)" Red }
    } elseif (($now - $st.StartedAt).TotalSeconds -ge 40) {
        Log "$($st.Host): 40초 동안 응답 없음 — 다시 시도" Yellow
        Stop-Process -Id $st.Proc.Id -Force -ErrorAction SilentlyContinue
        $st.Proc = $null
        $st.NextTry = $now.AddSeconds(5)
    }
}

# ---------------------------------------------------------------- 시작

$LocalPort = Read-LocalPort
if (-not $LocalPort) {
    Log "config\relay.yaml이 없거나 listen_port가 없습니다. relay.example.yaml을 복사해서 채워주세요." Red
    exit 1
}

Stop-Previous $LocalPort
if ($Stop) {
    Log "auto_yes_bot 종료 완료 (relay.py, 터널)" Green
    exit 0
}

Log "auto_yes_bot 시작 (로컬 relay 포트 $LocalPort)" Cyan

$Cfg = Read-LauncherConfig
if ($Cfg -and $Cfg.update_from_github -and -not $NoUpdate) {
    Log "GitHub에서 최신 코드 받는 중..."
    if (Update-FromGitHub $Cfg.github_zip) {
        Log "최신 코드 반영 완료" Green
        $Cfg = Read-LauncherConfig
    }
}
if (-not $Cfg) {
    Log "config\launcher.example.json이 없습니다." Red
    exit 1
}
$Python = if ($Cfg.python) { $Cfg.python } else { 'python' }
foreach ($exe in $Python, 'ssh') {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
        Log "'$exe' 명령을 찾을 수 없습니다. 설치/PATH를 확인하세요." Red
        exit 1
    }
}

# ssh config에 RemoteForward가 남아 있으면 Antigravity 연결이 포트를 가로챈다 (2026-10-15 원인 분석)
$sshCfg = Join-Path $env:USERPROFILE '.ssh\config'
if ((Test-Path $sshCfg) -and (Select-String -Path $sshCfg -Pattern '^\s*RemoteForward' -Quiet)) {
    Log "경고: ~/.ssh/config에 RemoteForward 줄이 있습니다. Antigravity 연결이 포트를 가로채니 지워주세요." Yellow
}

$dep = Start-Process -FilePath $Python -ArgumentList '-c "import aiohttp, yaml, truststore"' -NoNewWindow -Wait -PassThru
if ($dep.ExitCode -ne 0) {
    Log "파이썬 패키지 설치 중 (requirements-relay.txt)..." Yellow
    & $Python -m pip install -q -r requirements-relay.txt
}

try {
    $null = Invoke-WebRequest -Uri 'https://ntfy.sh/v1/health' -UseBasicParsing -TimeoutSec 8
} catch {
    Log "경고: 이 PC에서 ntfy.sh에 접속이 안 됩니다. 폰 알림이 안 갑니다 (학교 유선망 차단이면 Wi-Fi로 바꿔보세요)." Yellow
}

for ($i = 0; $i -lt 10 -and (Test-LocalPort $LocalPort); $i++) { Start-Sleep -Milliseconds 500 }
if (Test-LocalPort $LocalPort) {
    $owner = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $name = if ($owner) { (Get-Process -Id $owner.OwningProcess -ErrorAction SilentlyContinue).ProcessName } else { '?' }
    Log "로컬 포트 ${LocalPort}을(를) 다른 프로그램(${name})이 쓰고 있습니다. relay.yaml의 listen_port를 바꿔주세요." Red
    exit 1
}

$Relay = Start-Relay
for ($i = 0; $i -lt 30 -and -not (Test-LocalPort $LocalPort); $i++) { Start-Sleep -Milliseconds 500 }
Show-NewLines (Join-Path $StateDir 'relay.log') '[relay]' DarkGray
Show-NewLines (Join-Path $StateDir 'relay.err.log') '[relay]' Red
if (-not (Test-LocalPort $LocalPort)) {
    Log "relay.py가 포트 ${LocalPort}에서 뜨지 않았습니다. 위 로그를 확인하세요." Red
    exit 1
}
Log "relay.py 실행됨 (127.0.0.1:$LocalPort)" Green

$Tunnels = @()
foreach ($s in $Cfg.servers) {
    $Tunnels += [pscustomobject]@{
        Host = $s.host; Dir = $s.remote_dir; Ports = @($s.ports)
        Idx = 0; Port = 0; Proc = $null; StartedAt = $null; Up = $false
        Fails = 0; NextTry = Get-Date
    }
}

if ($Cfg.pull_on_servers) {
    foreach ($st in $Tunnels) {
        $r = Invoke-Remote $st.Host "cd $($st.Dir) && git pull --ff-only -q"
        if ($r.Code -eq 0) { Log "$($st.Host): 코드 최신화 (git pull)" Green }
        else { Log "$($st.Host): git pull 실패 — 기존 코드로 계속: $($r.Out)" Yellow }
    }
}

Log "감시 중. 종료하려면 Ctrl+C (창을 그냥 닫으면 백그라운드에 남습니다 — 그땐 stop_auto_yes_bot.bat)" Cyan

try {
    while ($true) {
        if ($Relay.HasExited) {
            Show-NewLines (Join-Path $StateDir 'relay.err.log') '[relay]' Red
            Log "relay.py가 종료됨 (exit $($Relay.ExitCode)) — 재시작" Yellow
            $Relay = Start-Relay
            Start-Sleep -Seconds 3
        }
        Show-NewLines (Join-Path $StateDir 'relay.log') '[relay]' DarkGray
        Show-NewLines (Join-Path $StateDir 'relay.err.log') '[relay]' Red

        $now = Get-Date
        foreach ($st in $Tunnels) { Step-Tunnel $st $now }
        Start-Sleep -Seconds 2
    }
} finally {
    Log "종료 중..." Cyan
    foreach ($st in $Tunnels) {
        if ($st.Proc -and -not $st.Proc.HasExited) { Stop-Process -Id $st.Proc.Id -Force -ErrorAction SilentlyContinue }
    }
    if ($Relay -and -not $Relay.HasExited) { Stop-Process -Id $Relay.Id -Force -ErrorAction SilentlyContinue }
}
