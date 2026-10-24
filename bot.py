name: Kanda RDP Zero-Touch 24/7

on:
  workflow_dispatch:
    inputs:
      rdp_count:
        description: "How many RDP instances (1-20)"
        required: false
        default: "1"
      user_id:
        description: "Workspace Bucket ID (UID Discord)"
        required: false
        default: "default_user"
      data_mode:
        description: "Chế độ Data (chung / rieng)"
        required: false
        default: "chung"

permissions:
  contents: write
  actions: write

defaults:
  run:
    shell: pwsh

jobs:
  setup:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.mk.outputs.matrix }}
    steps:
      - id: mk
        run: |
          $n = [int]"${{ inputs.rdp_count }}"; if ($n -lt 1) { $n = 1 }; if ($n -gt 20) { $n = 20 }
          $inc = @(); for ($i=1; $i -le $n; $i++){ $inc += @{ id = $i } }
          "matrix=$(@{ include = $inc } | ConvertTo-Json -Compress)" | Out-File $env:GITHUB_OUTPUT -Append -Encoding utf8

  rdp:
    needs: setup
    runs-on: windows-latest
    strategy:
      matrix: ${{ fromJson(needs.setup.outputs.matrix) }}
      max-parallel: 4
    timeout-minutes: 370
    env:
      RDP_USER: Thang
      RDP_PASS: Thang12345@
      STATE_DIR: C:\KandaState
    steps:
      - name: Enable RDP user + Admin Rights
        run: |
          $secFile = "$env:TEMP\secedit.cfg"; $secNew = "$env:TEMP\secedit_new.cfg"
          secedit /export /cfg $secFile /quiet 2>$null
          if (Test-Path $secFile) {
            (Get-Content $secFile) -replace 'PasswordComplexity = 1','PasswordComplexity = 0' -replace 'MinimumPasswordLength = \d+','MinimumPasswordLength = 0' | Set-Content $secNew
            secedit /configure /db secedit.sdb /cfg $secNew /quiet 2>$null
          }
          $u=$env:RDP_USER; $p=$env:RDP_PASS
          $sec = ConvertTo-SecureString $p -AsPlainText -Force
          if (-not (Get-LocalUser -Name $u -EA SilentlyContinue)) {
            New-LocalUser -Name $u -Password $sec -AccountNeverExpires
            Add-LocalGroupMember -Group "Administrators" -Member $u
            Add-LocalGroupMember -Group "Remote Desktop Users" -Member $u
          } else {
            Set-LocalUser -Name $u -Password $sec -AccountNeverExpires
            Enable-LocalUser -Name $u
            Add-LocalGroupMember -Group "Administrators" -Member $u -EA SilentlyContinue
            Add-LocalGroupMember -Group "Remote Desktop Users" -Member $u -EA SilentlyContinue
          }
          Set-ItemProperty "HKLM:\System\CurrentControlSet\Control\Terminal Server" -Name fDenyTSConnections -Value 0
          Enable-NetFirewallRule -DisplayGroup "Remote Desktop" | Out-Null

      - name: Ultimate Performance & Total Windows Defender Bypass
        run: |
          try {
            reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f | Out-Null
            Set-MpPreference -DisableRealtimeMonitoring $true -EA SilentlyContinue
            Set-MpPreference -DisableBehaviorMonitoring $true -EA SilentlyContinue
            Add-MpPreference -ExclusionPath "C:\" -EA SilentlyContinue
            Stop-Service -Name WinDefend -Force -EA SilentlyContinue
            Set-Service -Name WinDefend -StartupType Disabled -EA SilentlyContinue
          } catch {}

      - name: Zero-Touch Data Restore (Chung / Riêng)
        env:
          GH_TOKEN: ${{ github.token }}
          USER_ID: ${{ inputs.user_id }}
          DATA_MODE: ${{ inputs.data_mode }}
        run: |
          $tagName = if ("${{ inputs.data_mode }}" -eq "chung") { "kanda-state-chung" } else { "kanda-state-${{ inputs.user_id }}" }
          $zipFile = "$env:TEMP\kanda_state.7z"
          Write-Host "[+] Đang kiểm tra dữ liệu từ tag: $tagName"
          try {
              $releaseInfo = gh release view $tagName 2>&1
              if ($LASTEXITCODE -eq 0) {
                  gh release download $tagName --pattern "kanda_state.7z" --dir "$env:TEMP" --clobber 2>$null
                  if (Test-Path $zipFile) { 
                      7z x $zipFile -o"C:\" -y | Out-Null
                      Write-Host "[+] Khôi phục Data thành công!"
                  }
              } else {
                  Write-Host "[i] Chưa có bản backup cũ trên Release, tiến hành tạo mới sạch sẽ."
              }
          } catch {
              Write-Host "[i] Bỏ qua khôi phục dữ liệu cũ, tiếp tục khởi động máy."
          }

      - name: Continuous Data Snapshot Daemon (Watchdog)
        env:
          GH_TOKEN: ${{ github.token }}
          USER_ID: ${{ inputs.user_id }}
          DATA_MODE: ${{ inputs.data_mode }}
        run: |
          $daemonScript = @"
          `$tagName = if (`"$env:DATA_MODE`" -eq `"chung`") { `"kanda-state-chung`" } else { `"kanda-state-$env:USER_ID`" }
          `$zipFile = `"$env:TEMP\kanda_state.7z`"
          `$userProfile = `"C:\Users\$env:RDP_USER`"
          while (`$true) {
              Start-Sleep -Seconds 300
              try {
                  Remove-Item `$zipFile -Force -EA SilentlyContinue
                  7z a -t7z `$zipFile `"`$userProfile\Desktop`" `"`$userProfile\Downloads`" `"`$userProfile\Documents`" -spf -mx=1 -ssw | Out-Null
                  if (Test-Path `$zipFile) {
                      gh release view `$tagName 2>`$null | Out-Null
                      if (`$LASTEXITCODE -ne 0) {
                          gh release create `$tagName `$zipFile --title `"Auto State`" --notes `"Backup`" 2>`$null | Out-Null
                      } else {
                          gh release upload `$tagName `$zipFile --clobber 2>`$null | Out-Null
                      }
                  }
              } catch {}
          }
          "@
          $daemonPath = "C:\KandaState\snapshot_daemon.ps1"
          New-Item -ItemType Directory -Force -Path "C:\KandaState" | Out-Null
          $daemonScript | Set-Content -Path $daemonPath -Encoding UTF8
          Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$daemonPath`"" -WindowStyle Hidden

      - name: Download bore
        run: |
          $zip = "$env:TEMP\bore.zip"
          Invoke-WebRequest -Uri "https://github.com/ekzhang/bore/releases/download/v0.6.0/bore-v0.6.0-x86_64-pc-windows-msvc.zip" -OutFile $zip
          Expand-Archive -Path $zip -DestinationPath "$env:TEMP\bore" -Force
          $exe = Get-ChildItem "$env:TEMP\bore" -Recurse -Filter bore.exe | Select-Object -First 1
          Copy-Item $exe.FullName "$env:TEMP\bore.exe" -Force

      - name: Bore tunnel 3389 -> bore.pub + report port
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          $deadline = (Get-Date).AddMinutes(355)
          $exe = "$env:TEMP\bore.exe"
          while ((Get-Date) -lt $deadline) {
            $alive = $false
            if (Test-Path "$env:TEMP\bore.pid") {
              $pid2 = Get-Content "$env:TEMP\bore.pid" -EA SilentlyContinue
              if (Get-Process -Id $pid2 -EA SilentlyContinue) { $alive = $true }
            }
            if (-not $alive) {
              Remove-Item "$env:TEMP\bore.out","$env:TEMP\bore.err" -Force -EA SilentlyContinue
              $p = Start-Process -FilePath $exe -ArgumentList "local","3389","--to","bore.pub" -PassThru -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP\bore.out" -RedirectStandardError "$env:TEMP\bore.err"
              $p.Id | Out-File "$env:TEMP\bore.pid" -Encoding ascii
              $port = $null
              for ($i=0; $i -lt 20; $i++) {
                Start-Sleep -Seconds 1
                if (Test-Path "$env:TEMP\bore.out") {
                  $m = Select-String -Path "$env:TEMP\bore.out" -Pattern "bore\.pub[\s:]+(\d+)" | Select-Object -First 1
                  if ($m) { $port = $m.Matches[0].Groups[1].Value; break }
                }
                if ($p.HasExited) { break }
              }
              if ($port) {
                try {
                  $rid = "${{ matrix.id }}"
                  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("bore.pub:$port"))
                  $hdr = @{ Authorization = "Bearer $env:GH_TOKEN"; Accept = "application/vnd.github+json" }
                  $api = "https://api.github.com/repos/$env:GITHUB_REPOSITORY/contents/rdp-ports/$rid.txt"
                  for ($try=1; $try -le 6; $try++) {
                    $sha = $null
                    try { $sha = (Invoke-RestMethod -Uri $api -Headers $hdr -ErrorAction SilentlyContinue).sha } catch {}
                    $body = @{ message = "port slot $rid = $port"; content = $b64 }
                    if ($sha) { $body.sha = $sha }
                    try { Invoke-RestMethod -Method Put -Uri $api -Headers $hdr -Body ($body | ConvertTo-Json) -ContentType "application/json" -ErrorAction Stop | Out-Null; break } catch { Start-Sleep -Seconds 5 }
                  }
                } catch {}
              }
            }
            Start-Sleep -Seconds 15
          }
