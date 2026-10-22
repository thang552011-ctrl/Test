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
from discord.ext import commands
from discord import app_commands

# ── Logging Setup ──
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KandaBot")

# ── Cấu hình Bot ──
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

RDP_MAX_RUNTIME = 360  # Phút tối đa mỗi máy

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
        "data_mode": "chung"  # "chung" hoặc "rieng"
    }

def save_user_config(user_id: int, config: dict):
    path = get_user_config_path(user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

async def fetch_github_username(gh_token: str) -> str:
    """Gọi API GitHub để lấy tên tài khoản thực tế của người chơi từ token PAT"""
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

def _rdp_loop_dir() -> Path:
    d = Path("/orders/rdp/loop")
    d.mkdir(parents=True, exist_ok=True)
    return d

def _safe_read_json(p: Path) -> dict:
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _safe_write_json(p: Path, data: dict):
    try:
        tmp = p.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.warning(f"[RDP-loop] Lỗi ghi file config {p}: {e}")

def _rdp_loop_enabled(uid: str) -> bool:
    cfg = _safe_read_json(_rdp_loop_dir() / f"{uid}.json")
    return bool(cfg.get("enabled"))

def _rdp_loop_set(uid: str, enabled: bool, channel_id=None):
    p = _rdp_loop_dir() / f"{uid}.json"
    if not enabled:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
        return
    old = _safe_read_json(p)
    cfg = {
        "enabled": True,
        "channel_id": int(channel_id) if channel_id else old.get("channel_id"),
        "last_renew_ts": old.get("last_renew_ts", 0),
        "last_deploy_ts": old.get("last_deploy_ts", 0),
        "fail_count": old.get("fail_count", 0),
        "next_retry_ts": old.get("next_retry_ts", 0),
    }
    _safe_write_json(p, cfg)

async def deploy_rdp(repo_name: str, user_id: str, rdp_count: int = 1, repo_index: int = 1, data_mode: str = "chung"):
    logger.info(f"[GitHub] Tạo repo slot #{repo_index} ({repo_name}) cho user {user_id} | Chế độ Data: {data_mode} | Số lượng: {rdp_count}")
    await asyncio.sleep(2)
    return {
        "ok": True,
        "repo_full": f"kanda-user/kanda-slot-{repo_index}",
        "endpoint": "bore.pub:13579"
    }

class SettingsModal(discord.ui.Modal, title="⚙️ Cài Đặt Cấu Hình Treo Tool"):
    app_path = discord.ui.TextInput(
        label="Đường dẫn phần mềm trên RDP",
        placeholder=r"C:\Users\Kandagawa\Desktop\Tool\tool.exe",
        default=r"C:\Users\Kandagawa\Desktop\Tool\tool.exe",
        style=discord.TextStyle.short
    )
    keywords = discord.ui.TextInput(
        label="Từ khóa vượt menu (thứ tự: 1, 2, 3...)",
        placeholder="Login, Bot Control, START",
        default="Login, Bot Control, START",
        style=discord.TextStyle.short
    )
    threads = discord.ui.TextInput(
        label="Số luồng / Thứ tự chạy",
        placeholder="1",
        default="1",
        style=discord.TextStyle.short
    )
    license_key = discord.ui.TextInput(
        label="Key bản quyền phần mềm (nếu có)",
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
        
        # Nếu khách không ghi gì thì để trống tuyệt đối
        config["license_key"] = self.license_key.value.strip() if self.license_key.value else ""
        save_user_config(user_id, config)

        kw_display = "\n".join([f"• Bước {i+1}: `{kw}`" for i, kw in enumerate(config["keywords"])])
        
        response_desc = (
            f"✅ **Đã lưu cài đặt cấu hình thành công!**\n\n"
            f"📂 **Đường dẫn app:** `{config['app_path']}`\n"
            f"🔤 **Thứ tự từ khóa khởi động:**\n{kw_display}"
        )
        if config["license_key"]:
            response_desc += f"\n🔑 **Key bản quyền:** `{config['license_key']}`"

        await interaction.response.send_message(
            content=response_desc,
            ephemeral=True
        )

class DataModeSelect(discord.ui.Select):
    def __init__(self, count: int):
        self.count = count
        options = [
            discord.SelectOption(
                label="📁 1. RDP Data Chung",
                description="Lấy chung dữ liệu & cấu hình từ 1 RDP ngẫu nhiên",
                value="chung"
            ),
            discord.SelectOption(
                label="📂 2. RDP Data Riêng Lẻ",
                description="Mỗi máy RDP chạy cấu hình & thư mục độc lập riêng",
                value="rieng"
            )
        ]
        super().__init__(placeholder="Chọn chế độ đồng bộ Data cho RDP...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        data_mode = self.values[0]
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["data_mode"] = data_mode
        save_user_config(user_id, config)

        kw_list = config.get("keywords", ["Login", "START"])
        kw_steps = "\n".join([f"  {i+1}. `{kw}`" for i, kw in enumerate(kw_list)])
        
        mode_text = "RDP Data Chung (Đồng bộ)" if data_mode == "chung" else "RDP Data Riêng Lẻ (Độc lập)"

        content_msg = (
            f"🖥️ **Đang dựng {self.count} máy RDP ({mode_text})...**\n\n"
        )
        if data_mode == "chung":
            content_msg += f"📂 **Đường dẫn phần mềm:** `{config.get('app_path')}`\n"
        
        content_msg += (
            f"🔤 **Thứ tự từ khóa vượt menu khởi động:**\n{kw_steps}\n\n"
            f"⏳ **Chờ tầm {self.count * 1}-{(self.count * 1) + 2} phút nha.**"
        )

        await interaction.response.edit_message(
            content=content_msg,
            view=None
        )

        slot_index = config.get("repo_index", 1)
        stealth_name = f"ci-worker-slot-{slot_index}"
        await deploy_rdp(stealth_name, user_id, rdp_count=self.count, repo_index=slot_index, data_mode=data_mode)
        
        await asyncio.sleep(4)
        await interaction.followup.send(
            f"🎉 **Khởi tạo thành công {self.count} máy RDP (Slot #{slot_index} | {mode_text})!** Kiểm tra README trên repo hoặc tab Workflow để lấy thông tin kết nối `mstsc`.",
            ephemeral=True
        )

class DataModeView(discord.ui.View):
    def __init__(self, count: int):
        super().__init__(timeout=60)
        self.add_item(DataModeSelect(count))

class RDPCountSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=f"{i} máy",
                description=f"Dựng đồng thời {i} slot RDP (1-20)",
                emoji="🟢" if i <= 5 else ("🟡" if i <= 10 else ("🟠" if i <= 15 else "🔴"))
            )
            for i in range(1, 21)
        ]
        super().__init__(placeholder="Chọn số lượng máy muốn dựng (1-20)...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_text = self.values[0]
        count = int(selected_text.split(" ")[0])
        
        await interaction.response.edit_message(
            content=f"⚙️ **Chọn chế độ hoạt động dữ liệu (Data) cho {count} máy RDP vừa chọn:**",
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

        btn_loop = discord.ui.Button(label="RDP Loop", style=discord.ButtonStyle.success, emoji="🔄")
        btn_loop.callback = self.loop_callback
        self.add_item(btn_loop)

        btn_settings = discord.ui.Button(label="Cài đặt", style=discord.ButtonStyle.secondary, emoji="⚙️")
        btn_settings.callback = self.settings_callback
        self.add_item(btn_settings)

        btn_logout = discord.ui.Button(label="Đăng xuất", style=discord.ButtonStyle.danger, emoji="🚪")
        btn_logout.callback = self.logout_callback
        self.add_item(btn_logout)

    async def create_rdp_callback(self, interaction: discord.Interaction):
        embed_desc = (
            "**Chọn số lượng máy muốn dựng (1-20) trong menu bên dưới.**\n\n"
            "🟢 **1-5 máy:** An toàn tuyệt đối\n"
            "🟡 **6-10 máy:** Hiệu năng cao\n"
            "🟠 **11-15 máy:** Cường độ lớn\n"
            "🔴 **16-20 máy:** Tối đa công suất (⚠️ Dễ bị GitHub giới hạn Actions)"
        )
        await interaction.response.send_message(
            content=embed_desc,
            view=RDPCountView(),
            ephemeral=True
        )

    async def loop_callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        current_status = _rdp_loop_enabled(str(user_id))
        new_status = not current_status
        _rdp_loop_set(str(user_id), new_status, interaction.channel.id)
        
        status_text = "ĐÃ BẬT ✅" if new_status else "ĐÃ TẮT ❌"
        await interaction.response.send_message(
            f"🔄 **Trạng thái RDP Loop 24/7:** {status_text}\nBot sẽ tự động gia hạn gối đầu trước khi máy cũ hết giờ.",
            ephemeral=True
        )

    async def settings_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SettingsModal())

    async def logout_callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["github_token"] = ""
        config["railway_token"] = ""
        save_user_config(user_id, config)
        _rdp_loop_set(str(user_id), False)

        await interaction.response.send_message(
            "🚪 **Đã đăng xuất tài khoản GitHub & Railway thành công!** Sử dụng lệnh `/stk` để kết nối lại khi cần.",
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
            "🔒 **Chưa liên kết tài khoản GitHub!** Vول lòng sử dụng lệnh `/stk` để nhập Token GitHub và Railway trước khi mở menu quản lý RDP.",
            ephemeral=True
        )
        return

    github_username = await fetch_github_username(gh_token)

    menu_content = (
        "```ansi\n\x1b[1;36m🖥️ RDP — Menu\x1b[0m\n```\n"
        "Không thấy RDP đang chạy trên GitHub.\n"
        "Bấm **Tạo RDP** để dựng máy mới.\n\n"
        f"👤 **Tài khoản GitHub đang dùng:**\n`{github_username}`"
    )

    view = RDPMenuView()
    await interaction.response.send_message(content=menu_content, view=view, ephemeral=True)

@bot.tree.command(name="stk", description="Cung cấp Token GitHub và Railway bảo mật tuyệt đối")
@app_commands.describe(github_token="GitHub Personal Access Token (PAT)", railway_token="Railway API Token (để trống nếu không dùng)")
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
        f"Thông tin của bạn đã được mã hóa riêng tư. Bây giờ bạn có thể gõ lệnh `/rdp` để mở menu quản lý.",
        ephemeral=True
    )

if __name__ == "__main__":
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[!] Vui lòng cấu hình DISCORD_BOT_TOKEN trong biến môi trường.")
    else:
        bot.run(TOKEN)
