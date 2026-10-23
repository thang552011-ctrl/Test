import asyncio
import base64
import json
import os
import time
import logging
import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands

# ── Logging Setup ──
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KandaBot")

# ── Cấu hình Bot (Railway Token Fallback) ──
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()

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
        except Exception:
            pass
    return {
        "github_token": "",
        "railway_token": "",
        "app_path": r"C:\Users\Thang\Desktop\AutoTool\Tool.exe",
        "keywords": ["Login", "START"],
        "data_mode": "chung",
        "loop_enabled": False,
        "target_rdp_count": 0
    }

def save_user_config(user_id: int, config: dict):
    path = get_user_config_path(user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

async def fetch_github_username(gh_token: str) -> str:
    if not gh_token: return "Chưa liên kết"
    url = "https://api.github.com/user"
    headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("login", "Không rõ")
    except Exception:
        pass
    return "Lỗi Token"

async def scan_and_clean_rdp(gh_token: str, discord_user_id: str) -> list:
    """Quét các repo, xóa repo ngưng hoạt động, trả về danh sách repo đang chạy."""
    if not gh_token: return []
    headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}
    active_instances = []
    
    try:
        async with aiohttp.ClientSession() as session:
            repos_url = "https://api.github.com/user/repos?per_page=100&sort=updated"
            async with session.get(repos_url, headers=headers) as resp:
                if resp.status != 200: return []
                repos = await resp.json()
                
                for repo in repos:
                    repo_name = repo.get("name", "")
                    owner = repo.get("owner", {}).get("login", "")
                    
                    if not (repo_name.startswith("ci-worker-") or str(discord_user_id) in repo_name):
                        continue

                    runs_url = f"https://api.github.com/repos/{owner}/{repo_name}/actions/runs?per_page=1"
                    is_active = False
                    
                    async with session.get(runs_url, headers=headers) as run_resp:
                        if run_resp.status == 200:
                            run_data = await run_resp.json()
                            runs = run_data.get("workflow_runs", [])
                            if runs:
                                status = runs[0].get("status")
                                if status in ["in_progress", "queued", "pending"]:
                                    is_active = True
                    
                    # Nếu không hoạt động -> Tự động xóa Repo
                    if not is_active:
                        del_url = f"https://api.github.com/repos/{owner}/{repo_name}"
                        await session.delete(del_url, headers=headers)
                        logger.info(f"[Auto-Clean] Đã xóa repo không hoạt động: {repo_name}")
                        continue
                        
                    # Nếu hoạt động -> Lấy Port
                    ports_info = []
                    ports_dir_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/rdp-ports"
                    async with session.get(ports_dir_url, headers=headers) as port_resp:
                        if port_resp.status == 200:
                            files = await port_resp.json()
                            if isinstance(files, list):
                                for f_obj in files:
                                    f_url = f_obj.get("download_url")
                                    if f_url:
                                        async with session.get(f_url, headers=headers) as f_res:
                                            if f_res.status == 200:
                                                b64_data = await f_res.text()
                                                try:
                                                    dec = base64.b64decode(b64_data.strip()).decode('utf-8')
                                                    ports_info.append(dec)
                                                except:
                                                    ports_info.append(b64_data.strip())

                    active_instances.append({
                        "repo": f"{owner}/{repo_name}",
                        "ports": ports_info
                    })
    except Exception as e:
        logger.error(f"[Scan] Lỗi: {e}")
        
    return active_instances

async def deploy_rdp_instance(gh_token: str, rdp_count: int, user_id: str, data_mode: str):
    headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}
    repo_name = f"ci-worker-{user_id}-{int(time.time())}"
    
    # Xác định thẻ Data (Chung hoặc Riêng)
    tag_name = "kanda-state-chung" if data_mode == "chung" else f"kanda-state-{user_id}"

    async with aiohttp.ClientSession() as session:
        # Lấy Owner
        owner = "octocat"
        async with session.get("https://api.github.com/user", headers=headers) as r:
            if r.status == 200: owner = (await r.json()).get("login", owner)

        # Tạo Repo
        await session.post("https://api.github.com/user/repos", headers=headers, json={"name": repo_name, "private": True})
        await asyncio.sleep(3)

        # Nội dung Workflow (Sử dụng tk mk Thang/Thang12345@)
        wf_content = f"""name: Kanda RDP Watchdog
on:
  workflow_dispatch:
    inputs:
      rdp_count:
        default: "{rdp_count}"
jobs:
  setup:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{{{ steps.mk.outputs.matrix }}}}
    steps:
      - id: mk
        run: |
          $n = [int]"${{{{ inputs.rdp_count }}}}"; if ($n -lt 1) {{ $n = 1 }}; if ($n -gt 20) {{ $n = 20 }}
          $inc = @(); for ($i=1; $i -le $n; $i++){{ $inc += @{{ id = $i }} }}
          "matrix=$(@{{ include = $inc }} | ConvertTo-Json -Compress)" | Out-File $env:GITHUB_OUTPUT -Append -Encoding utf8
  rdp:
    needs: setup
    runs-on: windows-latest
    strategy:
      matrix: ${{{{ fromJson(needs.setup.outputs.matrix) }}}}
      max-parallel: 4
    timeout-minutes: 360
    env:
      RDP_USER: Thang
      RDP_PASS: Thang12345@
      TAG_NAME: {tag_name}
    steps:
      - name: Setup User & Firewall
        run: |
          $u=$env:RDP_USER; $p=$env:RDP_PASS; $sec = ConvertTo-SecureString $p -AsPlainText -Force
          New-LocalUser -Name $u -Password $sec -AccountNeverExpires -EA SilentlyContinue
          Add-LocalGroupMember -Group "Administrators" -Member $u -EA SilentlyContinue
          Add-LocalGroupMember -Group "Remote Desktop Users" -Member $u -EA SilentlyContinue
          Set-ItemProperty "HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server" -Name fDenyTSConnections -Value 0
          Enable-NetFirewallRule -DisplayGroup "Remote Desktop" | Out-Null
      - name: Download & Run Bore
        env:
          GH_TOKEN: ${{{{ github.token }}}}
        run: |
          Invoke-WebRequest -Uri "https://github.com/ekzhang/bore/releases/download/v0.6.0/bore-v0.6.0-x86_64-pc-windows-msvc.zip" -OutFile "$env:TEMP\\b.zip"
          Expand-Archive -Path "$env:TEMP\\b.zip" -DestinationPath "$env:TEMP\\b" -Force
          $exe = (Get-ChildItem "$env:TEMP\\b" -Recurse -Filter bore.exe)[0].FullName
          $p = Start-Process -FilePath $exe -ArgumentList "local","3389","--to","bore.pub" -PassThru -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP\\o.txt"
          Start-Sleep -Seconds 3
          $m = Select-String -Path "$env:TEMP\\o.txt" -Pattern "bore\\.pub[\\s:]+([0-9]+)" | Select-Object -First 1
          if ($m) {{
            $port = $m.Matches[0].Groups[1].Value
            $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("bore.pub:$port"))
            $api = "https://api.github.com/repos/$env:GITHUB_REPOSITORY/contents/rdp-ports/${{{{ matrix.id }}}}.txt"
            $body = @{{ message="P"; content=$b64 }} | ConvertTo-Json
            Invoke-RestMethod -Method Put -Uri $api -Headers @{{ Authorization="Bearer $env:GH_TOKEN"; Accept="application/vnd.github+json" }} -Body $body -ContentType "application/json"
          }}
          Start-Sleep -Seconds 21000
"""
        # Đẩy Workflow lên Repo
        wf_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/.github/workflows/rdp.yml"
        await session.put(wf_url, headers=headers, json={
            "message": "Add WF", "content": base64.b64encode(wf_content.encode()).decode()
        })
        await asyncio.sleep(3)

        # Trigger Workflow
        trigger_url = f"https://api.github.com/repos/{owner}/{repo_name}/actions/workflows/rdp.yml/dispatches"
        await session.post(trigger_url, headers=headers, json={"ref": "main", "inputs": {"rdp_count": str(rdp_count)}})

# ── Background Task: Auto Loop & Clean ──
@tasks.loop(minutes=2)
async def auto_loop_task():
    for filename in os.listdir(CONFIG_DIR):
        if not filename.endswith(".json"): continue
        user_id = filename.replace(".json", "")
        config = load_user_config(int(user_id))
        
        gh_token = config.get("github_token")
        if not gh_token or not config.get("loop_enabled"): continue
        
        # Quét và dọn dẹp
        active_instances = await scan_and_clean_rdp(gh_token, user_id)
        current_count = len(active_instances)
        target = config.get("target_rdp_count", 0)
        
        # Nếu hụt máy do bị xóa -> Tự động tạo bù
        if current_count < target:
            needed = target - current_count
            logger.info(f"[Auto-Loop] User {user_id} hụt {needed} máy. Đang dựng lại...")
            asyncio.create_task(deploy_rdp_instance(gh_token, needed, user_id, config.get("data_mode", "chung")))

@auto_loop_task.before_loop
async def before_auto_loop():
    await bot.wait_until_ready()

# ── Giao Diện (Modals & Views) ──
class TokenModal(discord.ui.Modal, title="🔒 Set Token"):
    gh_token = discord.ui.TextInput(
        label="🐙 GitHub Token *",
        placeholder="Repo + Delete_repo + Workflow",
        style=discord.TextStyle.short
    )
    rw_token = discord.ui.TextInput(
        label="🚂 Railway Account Token *",
        placeholder="No Workspace",
        style=discord.TextStyle.short,
        required=False
    )
    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["github_token"] = self.gh_token.value.strip()
        config["railway_token"] = self.rw_token.value.strip() if self.rw_token.value else ""
        save_user_config(user_id, config)
        await interaction.response.send_message("✅ **Lưu Token thành công!** Hãy dùng `/rdp`.", ephemeral=True)

class STKView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="Nhập Token", style=discord.ButtonStyle.primary, emoji="✏️")
    async def enter_token(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TokenModal())

class DeployModeView(discord.ui.View):
    def __init__(self, count: int, max_limit=20):
        super().__init__(timeout=60)
        self.count = count
        if self.count > max_limit: self.count = max_limit
    
    @discord.ui.button(label="Data Chung", style=discord.ButtonStyle.primary, emoji="📁")
    async def btn_chung(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await self.start_deploy(interaction, "chung")
        
    @discord.ui.button(label="Data Riêng Lẻ", style=discord.ButtonStyle.secondary, emoji="📂")
    async def btn_rieng(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await self.start_deploy(interaction, "rieng")

    async def start_deploy(self, interaction: discord.Interaction, mode: str):
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["data_mode"] = mode
        config["target_rdp_count"] = self.count # Cập nhật target cho Auto Loop
        save_user_config(user_id, config)

        gh_token = config.get("github_token")
        await interaction.response.edit_message(content=f"⏳ Đang dựng {self.count} RDP ({mode.upper()})...", view=None)
        
        # Check limit
        active_list = await scan_and_clean_rdp(gh_token, str(user_id))
        if len(active_list) >= 20:
            await interaction.followup.send("❌ Đã vượt quá 20 máy RDP đang chạy!", ephemeral=True)
            return

        await deploy_rdp_instance(gh_token, self.count, str(user_id), mode)
        await interaction.followup.send(f"✅ Đã gửi lệnh tạo {self.count} máy lên GitHub!", ephemeral=True)

class RDPMenuView(discord.ui.View):
    def __init__(self, user_config: dict):
        super().__init__(timeout=None)
        self.user_config = user_config

    @discord.ui.button(label="Tạo RDP", style=discord.ButtonStyle.success, emoji="🖥️")
    async def btn_create(self, interaction: discord.Interaction, btn: discord.ui.Button):
        # Đơn giản hóa: Mặc định tạo 1 máy mỗi lần bấm cho nhanh, hoặc làm Select Menu
        view = DeployModeView(1)
        await interaction.response.send_message("Chọn chế độ Data cho máy mới:", view=view, ephemeral=True)

    @discord.ui.button(label="Làm Mới", style=discord.ButtonStyle.primary, emoji="🔄")
    async def btn_refresh(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await send_rdp_menu(interaction, is_refresh=True)
        
    @discord.ui.button(label="Auto Loop: Bật/Tắt", style=discord.ButtonStyle.secondary, emoji="🔁")
    async def btn_loop(self, interaction: discord.Interaction, btn: discord.ui.Button):
        uid = interaction.user.id
        conf = load_user_config(uid)
        conf["loop_enabled"] = not conf.get("loop_enabled", False)
        save_user_config(uid, conf)
        status = "BẬT 🟢" if conf["loop_enabled"] else "TẮT 🔴"
        await interaction.response.send_message(f"Cơ chế Auto Loop đã **{status}**.", ephemeral=True)

# ── Logic Giao diện Chính ──
async def send_rdp_menu(interaction: discord.Interaction, is_refresh=False):
    user_id = interaction.user.id
    config = load_user_config(user_id)
    gh_token = config.get("github_token")

    if not gh_token:
        msg = "🔒 **Chưa liên kết tài khoản!** Vui lòng dùng lệnh `/stk`."
        if is_refresh: await interaction.response.edit_message(content=msg, embed=None, view=None)
        else: await interaction.response.send_message(msg, ephemeral=True)
        return

    if not is_refresh: await interaction.response.defer(ephemeral=True)

    uname = await fetch_github_username(gh_token)
    instances = await scan_and_clean_rdp(gh_token, str(user_id))
    
    embed = discord.Embed(title="🖥️ Kanda RDP Management", color=0x2b2d31)
    loop_st = "🟢 BẬT" if config.get("loop_enabled") else "🔴 TẮT"
    embed.description = f"**GitHub:** `{uname}`\n**Auto Loop:** {loop_st}\n**Tổng máy đang chạy:** {len(instances)}/20"
    
    if not instances:
        embed.add_field(name="Trạng thái", value="📂 Không có máy RDP nào đang hoạt động.", inline=False)
    else:
        for i, inst in enumerate(instances, 1):
            repo = inst['repo']
            ports = inst['ports']
            val = f"`{repo}`\n"
            if not ports:
                val += "⏳ *Đang khởi tạo (chờ 1-2 phút)...*"
            else:
                for p in ports:
                    val += f"🌐 Host: `{p}`\n👤 TK: `Thang` | 🔑 MK: `Thang12345@`"
            embed.add_field(name=f"RDP Slot {i}", value=val, inline=False)

    view = RDPMenuView(config)
    if is_refresh:
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

# ── Lệnh Slash ──
@bot.tree.command(name="stk", description="Cung cấp Token GitHub và Railway bảo mật")
async def stk_command(interaction: discord.Interaction):
    embed = discord.Embed(title="🔐 Bạn đã có token chưa?", color=0x2b2d31)
    embed.add_field(name="🐙 GitHub Token:", value="[Lấy token GitHub](https://github.com/settings/tokens)", inline=False)
    embed.add_field(name="🚂 Railway Token:", value="[Lấy token Railway](https://railway.app/account/tokens)", inline=False)
    embed.set_footer(text="GitHub: repo, delete_repo, workflow")
    await interaction.response.send_message(embed=embed, view=STKView(), ephemeral=True)

@bot.tree.command(name="rdp", description="Mở bảng điều khiển quản lý RDP 24/7")
async def rdp_menu(interaction: discord.Interaction):
    await send_rdp_menu(interaction)

@bot.event
async def on_ready():
    await bot.tree.sync()
    auto_loop_task.start()
    print(f"[+] Bot đã sẵn sàng: {bot.user}")

if __name__ == "__main__":
    if not TOKEN:
        print("[!] Cảnh báo: Biến môi trường DISCORD_BOT_TOKEN chưa được thiết lập!")
    else:
        bot.run(TOKEN)
