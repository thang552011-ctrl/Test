import asyncio
import base64
import json
import os
import random
import time
import uuid
import logging
import aiohttp
from pathlib import Path
import discord
from discord.ext import commands, tasks
from discord import app_commands

# ── Logging Setup ──
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KandaBot")

# ── Cấu hình Bot (Hỗ trợ đọc từ biến môi trường Railway) ──
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
        except Exception as e:
            logger.warning(f"[Config] Lỗi đọc config user {user_id}: {e}")
    return {
        "github_token": "",
        "railway_token": "",
        "app_path": r"C:\Users\Thang\Desktop\AutoTool\Tool.exe",
        "keywords": ["Login", "START"],
        "license_key": "",
        "threads": 1,
        "repo_index": 1,
        "data_mode": "chung",
        "loop_enabled": False,
        "target_rdp_count": 0
    }

def save_user_config(user_id: int, config: dict):
    path = get_user_config_path(user_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[Config] Lỗi ghi config user {user_id}: {e}")

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
                    
                    is_match_convention = repo_name.startswith("ci-worker-") or str(discord_user_id) in repo_name
                    if not is_match_convention:
                        continue

                    runs_url = f"https://api.github.com/repos/{owner}/{repo_name}/actions/runs?per_page=1"
                    is_active = False
                    status = "Không hoạt động"
                    conclusion = "unknown"
                    
                    try:
                        async with session.get(runs_url, headers=headers) as run_resp:
                            if run_resp.status == 200:
                                run_data = await run_resp.json()
                                runs = run_data.get("workflow_runs", [])
                                if runs:
                                    status = runs[0].get("status")
                                    conclusion = runs[0].get("conclusion", "unknown")
                                    if status in ["in_progress", "queued", "pending"]:
                                        is_active = True
                    except Exception:
                        pass
                    
                    # Nếu không hoạt động -> Tự động xóa Repo
                    if not is_active:
                        del_url = f"https://api.github.com/repos/{owner}/{repo_name}"
                        await session.delete(del_url, headers=headers)
                        logger.info(f"[Auto-Clean] Đã xóa repo không hoạt động: {repo_name}")
                        continue
                        
                    # Nếu hoạt động -> Lấy Port
                    ports_info = []
                    ports_dir_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/rdp-ports"
                    try:
                        async with session.get(ports_dir_url, headers=headers) as port_resp:
                            if port_resp.status == 200:
                                files = await port_resp.json()
                                if isinstance(files, list):
                                    for file_obj in files:
                                        file_url = file_obj.get("download_url")
                                        if file_url:
                                            async with session.get(file_url, headers=headers) as f_res:
                                                if f_res.status == 200:
                                                    b64_data = await f_res.text()
                                                    try:
                                                        decoded_port = base64.b64decode(b64_data.strip()).decode('utf-8')
                                                        ports_info.append({
                                                            "slot": file_obj.get("name").replace(".txt", ""),
                                                            "address": decoded_port
                                                        })
                                                    except Exception:
                                                        ports_info.append({
                                                            "slot": file_obj.get("name").replace(".txt", ""),
                                                            "address": b64_data.strip()
                                                        })
                    except Exception:
                        pass

                    active_instances.append({
                        "repo": f"{owner}/{repo_name}",
                        "status": status,
                        "conclusion": conclusion,
                        "ports": ports_info
                    })
    except Exception as e:
        logger.error(f"[Scan] Lỗi: {e}")
        
    return active_instances

async def deploy_rdp_real(gh_token: str, rdp_count: int, user_id: str, data_mode: str):
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    repo_name = f"ci-worker-{user_id}-{int(time.time())}"
    owner = "octocat"
    
    async with aiohttp.ClientSession() as session:
        user_url = "https://api.github.com/user"
        async with session.get(user_url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                owner = data.get("login", owner)

        create_url = "https://api.github.com/user/repos"
        payload = {
            "name": repo_name,
            "private": True,
            "description": "Kanda RDP Automated Workspace"
        }
        async with session.post(create_url, headers=headers, json=payload) as resp:
            if resp.status not in [200, 201]:
                logger.warning(f"[GitHub] Không thể tạo repo {repo_name} (Có thể đã tồn tại)")

        await asyncio.sleep(4)

        # Đọc nội dung file workflow trực tiếp từ thư mục của server
        try:
            with open(".github/workflows/rdp-bore.yml", "r", encoding="utf-8") as f:
                workflow_content = f.read()
        except Exception:
            logger.error("Không tìm thấy file template .github/workflows/rdp-bore.yml, đang sử dụng fallback...")
            return {"ok": False, "error": "Missing template file"}

        wf_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/.github/workflows/rdp-bore.yml"
        b64_content = base64.b64encode(workflow_content.encode("utf-8")).decode("utf-8")
        wf_payload = {
            "message": "Add Kanda RDP Workflow",
            "content": b64_content
        }
        async with session.put(wf_url, headers=headers, json=wf_payload) as resp:
            pass

        await asyncio.sleep(4)

        dispatch_url = f"https://api.github.com/repos/{owner}/{repo_name}/actions/workflows/rdp-bore.yml/dispatches"
        dispatch_payload = {
            "ref": "main",
            "inputs": {
                "rdp_count": str(rdp_count),
                "user_id": str(user_id),
                "data_mode": str(data_mode)
            }
        }
        async with session.post(dispatch_url, headers=headers, json=dispatch_payload) as resp:
            logger.info(f"[GitHub] Trigger workflow status: {resp.status}")

    return {
        "ok": True,
        "repo_full": f"{owner}/{repo_name}"
    }

# ── Background Task: Auto Loop & Clean ──
@tasks.loop(minutes=2)
async def auto_loop_task():
    for filename in os.listdir(CONFIG_DIR):
        if not filename.endswith(".json"): continue
        user_id = filename.replace(".json", "")
        config = load_user_config(int(user_id))
        
        gh_token = config.get("github_token")
        if not gh_token or not config.get("loop_enabled"): continue
        
        active_instances = await scan_and_clean_rdp(gh_token, user_id)
        current_count = len(active_instances)
        target = config.get("target_rdp_count", 0)
        
        if current_count < target:
            needed = target - current_count
            if current_count + needed > 20: needed = 20 - current_count
            if needed > 0:
                logger.info(f"[Auto-Loop] User {user_id} hụt {needed} máy. Đang dựng lại...")
                asyncio.create_task(deploy_rdp_real(gh_token, needed, user_id, config.get("data_mode", "chung")))

@auto_loop_task.before_loop
async def before_auto_loop():
    await bot.wait_until_ready()


class SettingsModal(discord.ui.Modal, title="⚙️ Cài Đặt Cấu Hình"):
    app_path = discord.ui.TextInput(
        label="Đường dẫn phần mềm trên RDP",
        placeholder=r"C:\Users\Thang\Desktop\Tool\tool.exe",
        default=r"C:\Users\Thang\Desktop\Tool\tool.exe",
        style=discord.TextStyle.short
    )
    keywords = discord.ui.TextInput(
        label="Từ khóa vượt menu",
        placeholder="Login, START",
        default="Login, START",
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
        try:
            config["threads"] = int(self.threads.value or 1)
        except ValueError:
            config["threads"] = 1
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
        config["target_rdp_count"] = self.count
        save_user_config(user_id, config)

        await interaction.response.edit_message(
            content=f"🖥️ **Đang kết nối GitHub và dựng {self.count} máy RDP thực tế...**",
            view=None
        )

        gh_token = config.get("github_token")
        
        active_list = await scan_and_clean_rdp(gh_token, str(user_id))
        if len(active_list) + self.count > 20:
            await interaction.followup.send(f"❌ **Không thể tạo thêm!** Bạn đang chạy {len(active_list)} máy. Tối đa chỉ 20 máy.", ephemeral=True)
            return

        try:
            res = await deploy_rdp_real(gh_token, rdp_count=self.count, user_id=str(user_id), data_mode=data_mode)
            if res.get("ok"):
                repo_full = res.get("repo_full")
                await interaction.followup.send(
                    f"🎉 **Khởi tạo thành công {self.count} máy RDP trên repo:** `{repo_full}`!\nThông tin kết nối `mstsc` sẽ tự động cập nhật về lệnh `/rdp` sau 1-2 phút khi runner khởi động xong.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send("❌ **Lỗi: Không tìm thấy file template `rdp-bore.yml` trên Server.**", ephemeral=True)
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
    def __init__(self, user_config: dict):
        super().__init__(timeout=None)
        self.user_config = user_config

    @discord.ui.button(label="Tạo RDP", style=discord.ButtonStyle.success, emoji="🖥️")
    async def btn_create(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.send_message(
            content="**Chọn số lượng máy muốn dựng (1-20):**",
            view=RDPCountView(),
            ephemeral=True
        )

    @discord.ui.button(label="Cài đặt", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def btn_settings(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.send_modal(SettingsModal())

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

    @discord.ui.button(label="Đăng xuất", style=discord.ButtonStyle.danger, emoji="🚪")
    async def btn_logout(self, interaction: discord.Interaction, btn: discord.ui.Button):
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["github_token"] = ""
        save_user_config(user_id, config)

        await interaction.response.send_message(
            "🚪 **Đã đăng xuất tài khoản GitHub thành công!**",
            ephemeral=True
        )


async def send_rdp_menu(interaction: discord.Interaction, is_refresh=False):
    user_id = interaction.user.id
    config = load_user_config(user_id)
    gh_token = config.get("github_token")

    if not gh_token:
        msg = "🔒 **Chưa liên kết tài khoản GitHub!** Vui lòng dùng lệnh `/stk` để nhập Token."
        if is_refresh:
            await interaction.response.edit_message(content=msg, view=None)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    if not is_refresh:
        await interaction.response.defer(ephemeral=True)

    github_username = await fetch_github_username(gh_token)
    active_instances = await scan_and_clean_rdp(gh_token, str(user_id))

    instances_text = ""
    if active_instances:
        instances_text = "\n🟢 **Các máy RDP của bạn đang hoạt động trên GitHub:**\n"
        for inst in active_instances:
            status_icon = "🔄 Đang chạy" if inst['status'] == 'in_progress' else ("✅ Hoàn tất" if inst['conclusion'] == 'success' else f"📌 {inst['status']}")
            instances_text += f"• `{inst['repo']}` — {status_icon}\n"
            if inst.get('ports'):
                for p in inst['ports']:
                    instances_text += f"  └─ 🖥️ **Slot {p['slot']}** | 🌐 `mstsc`: `{p['address']}` | 👤 `Thang` | 🔑 `Thang12345@`\n"
            else:
                instances_text += f"  └─ ⏳ *Đang khởi tạo cổng kết nối (Chờ 1-2 phút)...*\n"
    else:
        instances_text = "\n📂 *Không tìm thấy máy RDP nào đang hoạt động.*"

    loop_st = "🟢 BẬT" if config.get("loop_enabled") else "🔴 TẮT"

    menu_content = (
        "
```ansi\n\x1b[1;36m🖥️ RDP — Menu Quản Lý\x1b[0m\n```\n"
        f"👤 **Tài khoản GitHub:** `{github_username}`\n"
        f"🔁 **Auto Loop:** {loop_st} | 📊 **Tổng máy đang chạy:** {len(active_instances)}/20\n"
        f"{instances_text}"
    )

    view = RDPMenuView(config)
    
    if is_refresh:
        await interaction.edit_original_response(content=menu_content, view=view)
    else:
        await interaction.followup.send(content=menu_content, view=view, ephemeral=True)


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

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"[+] Đã đồng bộ {len(synced)} slash commands thành công!")
    except Exception as e:
        print(f"[!] Lỗi đồng bộ command: {e}")
    
    auto_loop_task.start()
    print(f"[+] Bot đã sẵn sàng: {bot.user}")

@bot.tree.command(name="rdp", description="Mở bảng điều khiển quản lý RDP 24/7")
async def rdp_menu_command(interaction: discord.Interaction):
    await send_rdp_menu(interaction)

@bot.tree.command(name="stk", description="Cung cấp Token GitHub bảo mật tuyệt đối")
async def stk_command(interaction: discord.Interaction):
    embed = discord.Embed(title="🔐 Bạn đã có token chưa?", color=0x2b2d31)
    embed.add_field(name="🐙 GitHub Token:", value="[Lấy token GitHub](https://github.com/settings/tokens)\n*(Cần bật quyền: repo, delete_repo, workflow)*", inline=False)
    embed.add_field(name="🚂 Railway Token:", value="[Lấy token Railway](https://railway.app/account/tokens)", inline=False)
    embed.set_footer(text="Nhấn nút bên dưới để nhập an toàn.")
    await interaction.response.send_message(embed=embed, view=STKView(), ephemeral=True)

if __name__ == "__main__":
    if not TOKEN:
        print("[!] Cảnh báo: Biến môi trường DISCORD_BOT_TOKEN chưa được thiết lập trên Railway!")
    else:
        bot.run(TOKEN)
```eof
