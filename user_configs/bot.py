import asyncio
import base64
import json
import os
import random
import time
import logging
import aiohttp
from pathlib import Path
import discord
from discord.ext import commands
from discord import app_commands

# ── Logging Setup ──
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KandaBot")

# ── Token Cố Định Theo Yêu Cầu ──
TOKEN = "MTU1NzAzMjMzNDk0MTg4ODYzMw.GFxzAH.jpVUwILSnWlMBYSxv-p5neHai90GYwdcHahRvU"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

CONFIG_DIR = "./user_configs"
os.makedirs(CONFIG_DIR, exist_ok=True)

def get_user_config_path(user_id: int) -> str:
    return os.path.join(CONFIG_DIR, f"{user_id}.json")

def load_user_config(user_id: int) -> dict:
    path = get_user_config_path(user_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {
        "github_token": "",
        "railway_token": "",
        "app_path": r"C:\Users\Kandagawa\Desktop\AutoTool\Tool.exe",
        "keywords": ["Login", "Bot Control", "START"],
        "license_key": "",
        "threads": 1,
        "repo_index": 1,
        "data_mode": "chung"
    }

def save_user_config(user_id: int, config: dict):
    path = get_user_config_path(user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

async def fetch_github_username(gh_token: str) -> str:
    if not gh_token:
        return "Chưa liên kết"
    url = "https://api.github.com/user"
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("login", "Không rõ")
    except Exception as e:
        logger.warning(f"[GitHub] Lỗi lấy thông tin username: {e}")
    return "Token không hợp lệ hoặc hết hạn"

async def scan_active_rdp_instances(gh_token: str, discord_user_id: str) -> list:
    """Quét các repository và lấy thông tin RDP port trực tiếp qua API (CHỈ QUÉT REPO THỎA MÃN QUY ƯỚC: ci-worker- hoặc chứa Discord UID)"""
    if not gh_token:
        return []
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    active_instances = []
    try:
        async with aiohttp.ClientSession() as session:
            repos_url = "https://api.github.com/user/repos?per_page=100&sort=updated"
            async with session.get(repos_url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    repos = await resp.json()
                    for repo in repos:
                        repo_name = repo.get("name", "")
                        owner = repo.get("owner", {}).get("login", "")
                        
                        # BỘ LỌC NGHIÊM NGẶT: Chỉ hiển thị repo bắt đầu bằng ci-worker- hoặc chứa Discord UID
                        is_match_convention = repo_name.startswith("ci-worker-") or str(discord_user_id) in repo_name
                        if not is_match_convention:
                            continue

                        # Kiểm tra workflow runs & lấy trạng thái
                        runs_url = f"https://api.github.com/repos/{owner}/{repo_name}/actions/runs?per_page=1"
                        status = "Không hoạt động"
                        conclusion = "unknown"
                        async with session.get(runs_url, headers=headers, timeout=5) as run_resp:
                            if run_resp.status == 200:
                                run_data = await run_resp.json()
                                runs = run_data.get("workflow_runs", [])
                                if runs:
                                    status = runs[0].get("status", "unknown")
                                    conclusion = runs[0].get("conclusion", "unknown")

                        # Lấy port RDP trực tiếp từ thư mục rdp-ports/*.txt
                        ports_info = []
                        ports_dir_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/rdp-ports"
                        async with session.get(ports_dir_url, headers=headers, timeout=5) as port_resp:
                            if port_resp.status == 200:
                                files = await port_resp.json()
                                if isinstance(files, list):
                                    for file_obj in files:
                                        file_url = file_obj.get("download_url")
                                        if file_url:
                                            async with session.get(file_url, headers=headers, timeout=5) as f_res:
                                                if f_res.status == 200:
                                                    b64_data = await f_res.text()
                                                    try:
                                                        decoded_port = base64.b64decode(b64_data.strip()).decode('utf-8')
                                                        ports_info.append({
                                                            "slot": file_obj.get("name").replace(".txt", ""),
                                                            "address": decoded_port
                                                        })
                                                    except:
                                                        ports_info.append({
                                                            "slot": file_obj.get("name").replace(".txt", ""),
                                                            "address": b64_data.strip()
                                                        })

                        active_instances.append({
                            "repo": f"{owner}/{repo_name}",
                            "status": status,
                            "conclusion": conclusion,
                            "ports": ports_info
                        })
    except Exception as e:
        logger.warning(f"[GitHub API] Lỗi quét RDP instances: {e}")
    return active_instances

async def deploy_rdp_real(gh_token: str, repo_name: str, rdp_count: int = 1, user_id: str = "default"):
    """Tạo repository thực tế trên GitHub của khách, đẩy đúng chuẩn workflow rdp-bore.yml và KÍCH HOẠT WORKFLOW CHẠY THẬT"""
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    owner = "octocat"
    async with aiohttp.ClientSession() as session:
        # 1. Lấy username chuẩn của chủ tài khoản GitHub
        user_url = "https://api.github.com/user"
        async with session.get(user_url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                owner = data.get("login", owner)

        # 2. Tạo repository mới trên GitHub (private)
        create_url = "https://api.github.com/user/repos"
        payload = {
            "name": repo_name,
            "private": True,
            "description": "Kanda RDP Automated Workspace"
        }
        async with session.post(create_url, headers=headers, json=payload) as resp:
            if resp.status not in [200, 201]:
                logger.warning(f"[GitHub] Không thể tạo repo {repo_name} (Có thể đã tồn tại)")

        await asyncio.sleep(4) # Chờ repo sẵn sàng trên GitHub API

        # 3. Đọc file workflow rdp-bore.yml từ thư mục dự án bot hoặc dùng template chuẩn
        workflow_content = """name: Kanda RDP Zero-Touch 24/7 (Watchdog & Defender Bypass)

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
      RDP_USER: Kandagawa
      RDP_PASS: Kandagw@12345
      STATE_DIR: C:\KandaState
    steps:
      - name: Enable RDP user + Admin Rights
        run: |
          $secFile = "$env:TEMP\\secedit.cfg"; $secNew = "$env:TEMP\\secedit_new.cfg"
          secedit /export /cfg $secFile /quiet 2>$null
          if (Test-Path $secFile) {
            (Get-Content $secFile) -replace 'PasswordComplexity = 1','PasswordComplexity = 0' -replace 'MinimumPasswordLength = \\d+','MinimumPasswordLength = 0' | Set-Content $secNew
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
          Set-ItemProperty "HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server" -Name fDenyTSConnections -Value 0
          Enable-NetFirewallRule -DisplayGroup "Remote Desktop" | Out-Null

      - name: Ultimate Performance & Total Windows Defender Bypass
        run: |
          try {
            reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f | Out-Null
            reg add "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\PriorityControl" /v Win32PrioritySeparation /t REG_DWORD /d 38 /f | Out-Null
            reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize" /v EnableTransparency /t REG_DWORD /d 0 /f | Out-Null
            
            Set-MpPreference -DisableRealtimeMonitoring $true -EA SilentlyContinue
            Set-MpPreference -DisableBehaviorMonitoring $true -EA SilentlyContinue
            Set-MpPreference -DisableScanOnRealtimeEnable $true -EA SilentlyContinue
            Set-MpPreference -DisableIOAVProtection $true -EA SilentlyContinue
            Set-MpPreference -DisableScriptScanning $true -EA SilentlyContinue
            Set-MpPreference -DisableArchiveScanning $true -EA SilentlyContinue
            Add-MpPreference -ExclusionPath "C:\\" -EA SilentlyContinue
            Stop-Service -Name WinDefend -Force -EA SilentlyContinue
            Stop-Service -Name WdNisSvc -Force -EA SilentlyContinue
            Set-Service -Name WinDefend -StartupType Disabled -EA SilentlyContinue
            Set-Service -Name WdNisSvc -StartupType Disabled -EA SilentlyContinue
          } catch {}

      - name: Download bore
        run: |
          $zip = "$env:TEMP\\bore.zip"
          Invoke-WebRequest -Uri "https://github.com/ekzhang/bore/releases/download/v0.6.0/bore-v0.6.0-x86_64-pc-windows-msvc.zip" -OutFile $zip
          Expand-Archive -Path $zip -DestinationPath "$env:TEMP\\bore" -Force
          $exe = Get-ChildItem "$env:TEMP\\bore" -Recurse -Filter bore.exe | Select-Object -First 1
          Copy-Item $exe.FullName "$env:TEMP\\bore.exe" -Force

      - name: Bore tunnel 3389 -> bore.pub + report port
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          $deadline = (Get-Date).AddMinutes(355)
          $exe = "$env:TEMP\\bore.exe"
          while ((Get-Date) -lt $deadline) {
            $alive = $false
            if (Test-Path "$env:TEMP\\bore.pid") {
              $pid2 = Get-Content "$env:TEMP\\bore.pid" -EA SilentlyContinue
              if (Get-Process -Id $pid2 -EA SilentlyContinue) { $alive = $true }
            }
            if (-not $alive) {
              Remove-Item "$env:TEMP\\bore.out","$env:TEMP\\bore.err" -Force -EA SilentlyContinue
              $p = Start-Process -FilePath $exe -ArgumentList "local","3389","--to","bore.pub" -PassThru -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP\\bore.out" -RedirectStandardError "$env:TEMP\\bore.err"
              $p.Id | Out-File "$env:TEMP\\bore.pid" -Encoding ascii
              $port = $null
              for ($i=0; $i -lt 20; $i++) {
                Start-Sleep -Seconds 1
                if (Test-Path "$env:TEMP\\bore.out") {
                  $m = Select-String -Path "$env:TEMP\\bore.out" -Pattern "bore\\.pub[\\s:]+(\d+)" | Select-Object -First 1
                  if ($m) { $port = $m.Matches[0].Groups[1].Value; break }
                }
                if ($p.HasExited) { break }
              }
              if ($port) {
                try {
                  $rid = "${{ matrix.id }}"
                  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("bore.pub:$port"))
                  $hdr = @{ Authorization = "Bearer $env:GH_TOKEN"; Accept = "application/vnd.github+json" }
                  $api = "https://api.github.com/repos/$env:GITHUB_REPOSITORY/contents/rdp-ports/$env:GITHUB_RUN_ID-$rid.txt"
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
"""

        # 4. Đẩy file workflow lên đúng đường dẫn .github/workflows/rdp-bore.yml của repo vừa tạo
        wf_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/.github/workflows/rdp-bore.yml"
        b64_content = base64.b64encode(workflow_content.encode("utf-8")).decode("utf-8")
        wf_payload = {
            "message": "Add Kanda RDP Workflow",
            "content": b64_content
        }
        async with session.put(wf_url, headers=headers, json=wf_payload) as resp:
            pass

        await asyncio.sleep(4) # Chờ GitHub đồng bộ file workflow

        # 5. KÍCH HOẠT WORKFLOW CHẠY THẬT (workflow_dispatch API) đúng tên file rdp-bore.yml
        dispatch_url = f"https://api.github.com/repos/{owner}/{repo_name}/actions/workflows/rdp-bore.yml/dispatches"
        dispatch_payload = {
            "ref": "main",
            "inputs": {
                "rdp_count": str(rdp_count),
                "user_id": str(user_id)
            }
        }
        async with session.post(dispatch_url, headers=headers, json=dispatch_payload) as resp:
            logger.info(f"[GitHub] Trigger workflow status: {resp.status}")

    return {
        "ok": True,
        "repo_full": f"{owner}/{repo_name}"
    }

class SettingsModal(discord.ui.Modal, title="⚙️ Cài Đặt Cấu Hình"):
    app_path = discord.ui.TextInput(
        label="Đường dẫn phần mềm trên RDP",
        placeholder=r"C:\Users\Kandagawa\Desktop\Tool\tool.exe",
        default=r"C:\Users\Kandagawa\Desktop\Tool\tool.exe",
        style=discord.TextStyle.short
    )
    keywords = discord.ui.TextInput(
        label="Từ khóa vượt menu",
        placeholder="Login, Bot Control, START",
        default="Login, Bot Control, START",
        style=discord.TextStyle.short
    )
    threads = discord.ui.TextInput(
        label="Số luồng chạy",
        placeholder="1",
        default="1",
        style=discord.TextStyle.short
    )
    license_key = discord.ui.TextInput(
        label="Key bản quyền (nếu có)",
        placeholder="Để trống nếu không có",
        required=False,
        style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["app_path"] = self.app_path.value.strip()
        config["keywords"] = [k.strip() for k in self.keywords.value.split(",") if k.strip()]
        config["threads"] = int(self.threads.value or 1)
        config["license_key"] = self.license_key.value.strip() if self.license_key.value else ""
        save_user_config(user_id, config)

        await interaction.response.send_message(
            content="✅ **Đã lưu cài đặt cấu hình thành công!**",
            ephemeral=True
        )

class DataModeSelect(discord.ui.Select):
    def __init__(self, count: int):
        self.count = count
        options = [
            discord.SelectOption(label="📁 1. RDP Data Chung", description="Lấy chung dữ liệu & cấu hình từ 1 RDP", value="chung"),
            discord.SelectOption(label="📂 2. RDP Data Riêng Lẻ", description="Mỗi máy RDP chạy độc lập riêng", value="rieng")
        ]
        super().__init__(placeholder="Chọn chế độ đồng bộ Data...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        data_mode = self.values[0]
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["data_mode"] = data_mode
        save_user_config(user_id, config)

        await interaction.response.edit_message(
            content=f"🖥️ **Đang kết nối GitHub và dựng {self.count} máy RDP thực tế...**",
            view=None
        )

        gh_token = config.get("github_token")
        stealth_name = f"ci-worker-{user_id}-{int(time.time())}"
        
        try:
            res = await deploy_rdp_real(gh_token, stealth_name, rdp_count=self.count, user_id=str(user_id))
            repo_full = res.get("repo_full")
            await interaction.followup.send(
                f"🎉 **Khởi tạo thành công {self.count} máy RDP trên repo:** `{repo_full}`!\nThông tin kết nối `mstsc` sẽ tự động cập nhật về lệnh `/rdp` sau 1-2 phút khi runner khởi động xong.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ **Lỗi khi tạo RDP trên GitHub:** `{e}`",
                ephemeral=True
            )

class DataModeView(discord.ui.View):
    def __init__(self, count: int):
        super().__init__(timeout=60)
        self.add_item(DataModeSelect(count))

class RDPCountSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=f"{i} máy", description=f"Dựng {i} slot RDP (1-20)", emoji="🟢") for i in range(1, 21)]
        super().__init__(placeholder="Chọn số lượng máy (1-20)...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        count = int(self.values[0].split(" ")[0])
        await interaction.response.edit_message(
            content=f"⚙️ **Chọn chế độ hoạt động dữ liệu cho {count} máy RDP:**",
            view=DataModeView(count)
        )

class RDPCountView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(RDPCountSelect())

class RDPMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
        btn_create = discord.ui.Button(label="Tạo RDP", style=discord.ButtonStyle.primary, emoji="🖥️")
        btn_create.callback = self.create_rdp_callback
        self.add_item(btn_create)

        btn_settings = discord.ui.Button(label="Cài đặt", style=discord.ButtonStyle.secondary, emoji="⚙️")
        btn_settings.callback = self.settings_callback
        self.add_item(btn_settings)

        btn_logout = discord.ui.Button(label="Đăng xuất", style=discord.ButtonStyle.danger, emoji="🚪")
        btn_logout.callback = self.logout_callback
        self.add_item(btn_logout)

    async def create_rdp_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            content="**Chọn số lượng máy muốn dựng (1-20):**",
            view=RDPCountView(),
            ephemeral=True
        )

    async def settings_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SettingsModal())

    async def logout_callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["github_token"] = ""
        save_user_config(user_id, config)

        await interaction.response.send_message(
            "🚪 **Đã đăng xuất tài khoản GitHub thành công!**",
            ephemeral=True
        )

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"[+] Đã đồng bộ {len(synced)} slash commands thành công!")
    except Exception as e:
        print(f"[!] Lỗi đồng bộ command: {e}")
    print(f"[+] Bot đã sẵn sàng: {bot.user}")

@bot.tree.command(name="rdp", description="Mở bảng điều khiển quản lý RDP 24/7")
async def rdp_menu(interaction: discord.Interaction):
    user_id = interaction.user.id
    config = load_user_config(user_id)
    gh_token = config.get("github_token")

    if not gh_token:
        await interaction.response.send_message(
            "🔒 **Chưa liên kết tài khoản GitHub!** Vui lòng dùng lệnh `/stk` để nhập Token.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    github_username = await fetch_github_username(gh_token)
    active_instances = await scan_active_rdp_instances(gh_token, str(user_id))

    instances_text = ""
    if active_instances:
        instances_text = "\n🟢 **Các máy RDP của bạn đang hoạt động trên GitHub:**\n"
        for inst in active_instances:
            status_icon = "🔄 Đang chạy" if inst['status'] == 'in_progress' else ("✅ Hoàn tất" if inst['conclusion'] == 'success' else f"📌 {inst['status']}")
            instances_text += f"• `{inst['repo']}` — {status_icon}\n"
            if inst.get('ports'):
                for p in inst['ports']:
                    instances_text += f"  └─ 🖥️ **Slot {p['slot']}** | 🌐 `mstsc`: `{p['address']}` | 👤 `Kandagawa` | 🔑 `Kandagw@12345`\n"
            else:
                instances_text += f"  └─ ⏳ *Đang khởi tạo cổng kết nối (Chờ 1-2 phút)...*\n"
    else:
        instances_text = "\n📂 *Không tìm thấy máy RDP nào theo đúng quy ước đang hoạt động.*"

    menu_content = (
        "```ansi\n\x1b[1;36m🖥️ RDP — Menu Quản Lý\x1b[0m\n```\n"
        f"👤 **Tài khoản GitHub:** `{github_username}`\n"
        f"{instances_text}"
    )

    view = RDPMenuView()
    await interaction.followup.send(content=menu_content, view=view, ephemeral=True)

@bot.tree.command(name="stk", description="Cung cấp Token GitHub bảo mật tuyệt đối")
@app_commands.describe(github_token="GitHub Personal Access Token (PAT)", railway_token="Railway API Token")
async def stk_command(interaction: discord.Interaction, github_token: str, railway_token: str = ""):
    user_id = interaction.user.id
    config = load_user_config(user_id)
    config["github_token"] = github_token.strip()
    config["railway_token"] = railway_token.strip() if railway_token else ""
    save_user_config(user_id, config)

    github_username = await fetch_github_username(config["github_token"])

    await interaction.response.send_message(
        f"🔒 **Đã cập nhật Token bảo mật thành công!**\n"
        f"👤 **Tài khoản GitHub xác thực:** `{github_username}`\n"
        f"Bây giờ bạn có thể gõ lệnh `/rdp` để mở menu quản lý thực tế.",
        ephemeral=True
    )

if __name__ == "__main__":
    bot.run(TOKEN)