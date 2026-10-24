import asyncio
import base64
import json
import os
import re
import time
import logging
from datetime import datetime, timezone
import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands

# ── Logging Setup ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("KandaBot")

# ── Cấu hình Bot ──
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
CONFIG_DIR = "./user_configs"
os.makedirs(CONFIG_DIR, exist_ok=True)

RDP_BORE_LIFETIME_MIN = 355
RDP_PREWARM_THRESHOLD_MIN = 10

_rdp_loop_busy = set()


class KandaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: aiohttp.ClientSession = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        try:
            synced = await self.tree.sync()
            logger.info(f"[+] Đã đồng bộ {len(synced)} slash commands thành công!")
        except Exception as e:
            logger.error(f"[!] Lỗi đồng bộ command: {e}")

    async def close(self):
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()


bot = KandaBot()


def get_user_config_path(user_id: int) -> str:
    return os.path.join(CONFIG_DIR, f"{user_id}.json")


def load_user_config(user_id: int) -> dict:
    path = get_user_config_path(user_id)
    default_config = {
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
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_config.update(data)
                return default_config
        except Exception as e:
            logger.warning(f"[Config] Lỗi đọc config user {user_id}: {e}")
    return default_config


def save_user_config(user_id: int, config: dict):
    path = get_user_config_path(user_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[Config] Lỗi ghi config user {user_id}: {e}")


async def get_session() -> aiohttp.ClientSession:
    if bot.http_session is None or bot.http_session.closed:
        bot.http_session = aiohttp.ClientSession()
    return bot.http_session


async def fetch_github_username(gh_token: str) -> str:
    if not gh_token:
        return "Chưa liên kết"
    url = "https://api.github.com/user"
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    try:
        session = await get_session()
        async with session.get(url, headers=headers, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("login", "Không rõ")
    except Exception as e:
        logger.warning(f"[GitHub] Lỗi lấy thông tin username: {e}")
    return "Token không hợp lệ hoặc hết hạn"


async def fetch_single_port_file(session: aiohttp.ClientSession, file_obj: dict, headers: dict):
    file_url = file_obj.get("download_url")
    slot_name = file_obj.get("name", "").replace(".txt", "")
    if not file_url:
        return None
    try:
        async with session.get(file_url, headers=headers, timeout=5) as f_res:
            if f_res.status == 200:
                b64_data = (await f_res.text()).strip()
                try:
                    decoded_port = base64.b64decode(b64_data).decode("utf-8").strip()
                except Exception:
                    decoded_port = b64_data
                return {
                    "slot": slot_name,
                    "address": decoded_port
                }
    except Exception:
        pass
    return None


def calculate_remain_minutes(run_started_at_iso: str) -> int:
    if not run_started_at_iso:
        return RDP_BORE_LIFETIME_MIN
    try:
        dt = datetime.fromisoformat(run_started_at_iso.replace("Z", "+00:00"))
        elapsed_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
        return max(0, int(RDP_BORE_LIFETIME_MIN - elapsed_min))
    except Exception:
        return RDP_BORE_LIFETIME_MIN


async def inspect_single_repo(session: aiohttp.ClientSession, repo: dict, headers: dict):
    repo_name = repo.get("name", "")
    owner = repo.get("owner", {}).get("login", "")
    description = repo.get("description") or ""

    expected_slots = 1
    match = re.search(r"\[SLOTS:(\d+)\]", description)
    if match:
        expected_slots = int(match.group(1))

    runs_url = f"https://api.github.com/repos/{owner}/{repo_name}/actions/runs?per_page=1"
    status = "unknown"
    conclusion = None
    run_id = None
    remain_min = RDP_BORE_LIFETIME_MIN

    try:
        async with session.get(runs_url, headers=headers, timeout=6) as run_resp:
            if run_resp.status == 200:
                run_data = await run_resp.json()
                runs = run_data.get("workflow_runs", [])
                if runs:
                    latest_run = runs[0]
                    run_id = latest_run.get("id")
                    status = latest_run.get("status", "unknown")
                    conclusion = latest_run.get("conclusion")
                    started_at = latest_run.get("run_started_at") or latest_run.get("created_at")
                    remain_min = calculate_remain_minutes(started_at)
                else:
                    created_at = repo.get("created_at", "")
                    status = "queued" if created_at else "completed"
    except Exception:
        return None

    is_active = status in ["in_progress", "queued", "waiting", "requested", "pending"]
    if not is_active:
        return {
            "repo": f"{owner}/{repo_name}",
            "repo_name": repo_name,
            "owner": owner,
            "run_id": run_id,
            "status": status,
            "conclusion": conclusion,
            "remain_min": 0,
            "ports": [],
            "slot_count": expected_slots,
            "is_active": False
        }

    ports_info = []
    ports_dir_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/rdp-ports"
    try:
        async with session.get(ports_dir_url, headers=headers, timeout=6) as port_resp:
            if port_resp.status == 200:
                files = await port_resp.json()
                if isinstance(files, list):
                    port_tasks = [
                        fetch_single_port_file(session, file_obj, headers)
                        for file_obj in files
                    ]
                    port_results = await asyncio.gather(*port_tasks)
                    ports_info = [p for p in port_results if p is not None]
                    ports_info.sort(key=lambda x: int(x["slot"]) if str(x["slot"]).isdigit() else 999)
    except Exception:
        pass

    actual_slots = max(len(ports_info), expected_slots)

    return {
        "repo": f"{owner}/{repo_name}",
        "repo_name": repo_name,
        "owner": owner,
        "run_id": run_id,
        "status": status,
        "conclusion": conclusion,
        "remain_min": remain_min,
        "ports": ports_info,
        "slot_count": actual_slots,
        "is_active": True
    }


async def scan_rdp_instances(gh_token: str, discord_user_id: str, include_inactive: bool = False) -> list:
    if not gh_token:
        return []
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    try:
        session = await get_session()
        repos_url = "https://api.github.com/user/repos?per_page=100&sort=updated"
        async with session.get(repos_url, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                return []
            repos = await resp.json()

        tasks_list = []
        for repo in repos:
            repo_name = repo.get("name", "")
            if repo_name.startswith("ci-worker-") or str(discord_user_id) in repo_name:
                tasks_list.append(inspect_single_repo(session, repo, headers))

        if not tasks_list:
            return []

        results = await asyncio.gather(*tasks_list)
        valid_results = [r for r in results if r is not None]

        if include_inactive:
            return valid_results
        return [r for r in valid_results if r.get("is_active")]
    except Exception as e:
        logger.warning(f"[GitHub API] Lỗi quét RDP instances: {e}")
        return []


async def trigger_workflow_on_repo(
    gh_token: str,
    repo_full: str,
    rdp_count: int,
    user_id: str,
    data_mode: str,
    cancel_run_id: int = None
) -> bool:
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    session = await get_session()
    user_config = load_user_config(int(user_id)) if str(user_id).isdigit() else load_user_config(0)

    if cancel_run_id:
        cancel_url = f"https://api.github.com/repos/{repo_full}/actions/runs/{cancel_run_id}/cancel"
        try:
            async with session.post(cancel_url, headers=headers, timeout=8) as _:
                pass
            await asyncio.sleep(2)
        except Exception:
            pass

    patch_url = f"https://api.github.com/repos/{repo_full}"
    try:
        await session.patch(
            patch_url,
            headers=headers,
            json={"description": f"Kanda RDP Automated Workspace [SLOTS:{rdp_count}]"},
            timeout=6
        )
    except Exception:
        pass

    dispatch_url = f"https://api.github.com/repos/{repo_full}/actions/workflows/rdp-bore.yml/dispatches"
    keywords_str = ",".join(user_config.get("keywords", ["Login", "START"]))

    full_inputs = {
        "rdp_count": str(rdp_count),
        "user_id": str(user_id),
        "data_mode": str(data_mode),
        "app_path": str(user_config.get("app_path", "")),
        "keywords": keywords_str,
        "threads": str(user_config.get("threads", 1)),
        "license_key": str(user_config.get("license_key", ""))
    }

    async with session.post(dispatch_url, headers=headers, json={"ref": "main", "inputs": full_inputs}) as resp:
        if resp.status == 422:
            basic_inputs = {
                "rdp_count": str(rdp_count),
                "user_id": str(user_id),
                "data_mode": str(data_mode)
            }
            async with session.post(dispatch_url, headers=headers, json={"ref": "main", "inputs": basic_inputs}) as retry_resp:
                return retry_resp.status in [201, 204]
        return resp.status in [201, 204]


async def deploy_rdp_real(
    gh_token: str,
    rdp_count: int = 1,
    user_id: str = "default",
    data_mode: str = "chung",
    force_new_repo: bool = False
):
    if not force_new_repo:
        all_repos = await scan_rdp_instances(gh_token, str(user_id), include_inactive=True)
        inactive_repos = [r for r in all_repos if not r.get("is_active")]

        if inactive_repos:
            reuse_repo = inactive_repos[0]["repo"]
            logger.info(f"[Zero-Touch] Tái sử dụng repo {reuse_repo} để khôi phục dữ liệu từ Release...")
            ok = await trigger_workflow_on_repo(
                gh_token=gh_token,
                repo_full=reuse_repo,
                rdp_count=rdp_count,
                user_id=str(user_id),
                data_mode=data_mode
            )
            if ok:
                return {"ok": True, "repo_full": reuse_repo, "reused": True}

    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    repo_name = f"ci-worker-{user_id}-{int(time.time())}"
    session = await get_session()

    create_url = "https://api.github.com/user/repos"
    payload = {
        "name": repo_name,
        "private": True,
        "description": f"Kanda RDP Automated Workspace [SLOTS:{rdp_count}]"
    }
    async with session.post(create_url, headers=headers, json=payload) as resp:
        if resp.status not in [200, 201]:
            err_text = await resp.text()
            logger.error(f"[GitHub] Không thể tạo repo {repo_name}: {resp.status} - {err_text}")
            return {"ok": False, "error": err_text}
        repo_data = await resp.json()
        owner = repo_data.get("owner", {}).get("login", "")

    await asyncio.sleep(3)

    try:
        with open(".github/workflows/rdp-bore.yml", "r", encoding="utf-8") as f:
            workflow_content = f.read()
    except Exception:
        logger.error("Không tìm thấy file template .github/workflows/rdp-bore.yml")
        return {"ok": False, "error": "Missing template file (.github/workflows/rdp-bore.yml)"}

    wf_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/.github/workflows/rdp-bore.yml"
    b64_content = base64.b64encode(workflow_content.encode("utf-8")).decode("utf-8")
    wf_payload = {
        "message": "Add Kanda RDP Workflow",
        "content": b64_content
    }
    async with session.put(wf_url, headers=headers, json=wf_payload) as resp:
        if resp.status not in [200, 201]:
            err_wf = await resp.text()
            logger.warning(f"[GitHub] Đẩy file workflow không thành công: {resp.status} - {err_wf}")
            return {"ok": False, "error": f"Upload workflow lỗi: {resp.status}"}

    await asyncio.sleep(3)

    repo_full = f"{owner}/{repo_name}"
    await trigger_workflow_on_repo(
        gh_token=gh_token,
        repo_full=repo_full,
        rdp_count=rdp_count,
        user_id=str(user_id),
        data_mode=data_mode
    )

    return {
        "ok": True,
        "repo_full": repo_full,
        "reused": False
    }


async def stop_all_rdp_instances(gh_token: str, discord_user_id: str) -> dict:
    active_instances = await scan_rdp_instances(gh_token, discord_user_id, include_inactive=False)
    if not active_instances:
        return {"stopped_repos": 0, "stopped_slots": 0}

    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    session = await get_session()

    async def cancel_repo_runs(inst):
        repo_full = inst["repo"]
        stopped = False
        if inst.get("run_id"):
            cancel_url = f"https://api.github.com/repos/{repo_full}/actions/runs/{inst['run_id']}/cancel"
            try:
                async with session.post(cancel_url, headers=headers, timeout=8) as resp:
                    if resp.status in [202, 200, 204]:
                        stopped = True
            except Exception:
                pass

        for st in ["in_progress", "queued", "waiting"]:
            runs_url = f"https://api.github.com/repos/{repo_full}/actions/runs?status={st}&per_page=10"
            try:
                async with session.get(runs_url, headers=headers, timeout=6) as r_resp:
                    if r_resp.status == 200:
                        r_data = await r_resp.json()
                        for run_obj in r_data.get("workflow_runs", []):
                            rid = run_obj.get("id")
                            if rid:
                                c_url = f"https://api.github.com/repos/{repo_full}/actions/runs/{rid}/cancel"
                                async with session.post(c_url, headers=headers, timeout=6) as c_resp:
                                    if c_resp.status in [202, 200, 204]:
                                        stopped = True
            except Exception:
                pass
        return inst.get("slot_count", 1) if stopped else 0

    results = await asyncio.gather(*(cancel_repo_runs(inst) for inst in active_instances))
    stopped_repos = sum(1 for slots in results if slots > 0)
    stopped_slots = sum(results)
    return {
        "stopped_repos": stopped_repos,
        "stopped_slots": stopped_slots
    }


async def delete_all_rdp_repos(gh_token: str, discord_user_id: str) -> int:
    all_instances = await scan_rdp_instances(gh_token, discord_user_id, include_inactive=True)
    if not all_instances:
        return 0

    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    session = await get_session()

    async def delete_repo(inst):
        del_url = f"https://api.github.com/repos/{inst['repo']}"
        try:
            async with session.delete(del_url, headers=headers, timeout=8) as resp:
                return resp.status in [204, 200]
        except Exception:
            return False

    del_results = await asyncio.gather(*(delete_repo(inst) for inst in all_instances))
    return sum(1 for success in del_results if success)


async def cleanup_inactive_repos(gh_token: str, discord_user_id: str, keep_latest_one: bool = True) -> int:
    all_instances = await scan_rdp_instances(gh_token, discord_user_id, include_inactive=True)
    inactive_list = [inst for inst in all_instances if not inst.get("is_active")]

    if keep_latest_one and len(inactive_list) > 0:
        inactive_list = inactive_list[1:]

    if not inactive_list:
        return 0

    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json"
    }
    session = await get_session()

    async def delete_repo(inst):
        del_url = f"https://api.github.com/repos/{inst['repo']}"
        try:
            async with session.delete(del_url, headers=headers, timeout=8) as resp:
                return resp.status in [204, 200]
        except Exception:
            return False

    del_results = await asyncio.gather(*(delete_repo(inst) for inst in inactive_list))
    return sum(1 for success in del_results if success)


async def _process_user_rdp_loop(user_id_str: str, config: dict):
    gh_token = config.get("github_token")
    data_mode = config.get("data_mode", "chung")
    target = min(20, max(0, int(config.get("target_rdp_count", 0))))
    if not gh_token or target <= 0:
        return

    all_instances = await scan_rdp_instances(gh_token, user_id_str, include_inactive=True)
    active_instances = [inst for inst in all_instances if inst.get("is_active")]
    inactive_instances = [inst for inst in all_instances if not inst.get("is_active")]

    for inst in active_instances:
        if inst["status"] == "in_progress" and inst.get("remain_min", 355) <= RDP_PREWARM_THRESHOLD_MIN:
            slots = inst.get("slot_count", 1)
            logger.info(
                f"[Auto-Loop] Repo {inst['repo']} của user {user_id_str} chỉ còn {inst['remain_min']} phút. "
                f"Đang xoay vòng (Re-dispatch) ngay trên repo này để giữ Data..."
            )
            await trigger_workflow_on_repo(
                gh_token=gh_token,
                repo_full=inst["repo"],
                rdp_count=slots,
                user_id=user_id_str,
                data_mode=data_mode,
                cancel_run_id=inst.get("run_id")
            )

    current_count = sum(inst.get("slot_count", 1) for inst in active_instances)

    if current_count < target:
        needed = min(20 - current_count, target - current_count)
        if needed <= 0:
            return

        if inactive_instances:
            reuse_inst = inactive_instances[0]
            logger.info(
                f"[Auto-Loop] User {user_id_str} thiếu {needed} máy. "
                f"Đang kích hoạt lại trên repo cũ {reuse_inst['repo']} (Zero-Touch Data Restore)..."
            )
            await trigger_workflow_on_repo(
                gh_token=gh_token,
                repo_full=reuse_inst["repo"],
                rdp_count=needed,
                user_id=user_id_str,
                data_mode=data_mode
            )
        else:
            logger.info(f"[Auto-Loop] User {user_id_str} thiếu {needed} máy và chưa có repo cũ. Đang tạo repo mới...")
            await deploy_rdp_real(
                gh_token=gh_token,
                rdp_count=needed,
                user_id=str(user_id),
                data_mode=data_mode
            )

    await cleanup_inactive_repos(gh_token, user_id_str, keep_latest_one=True)


@tasks.loop(seconds=60)
async def auto_loop_task():
    for filename in os.listdir(CONFIG_DIR):
        if not filename.endswith(".json"):
            continue
        user_id_str = filename.replace(".json", "")
        if not user_id_str.isdigit() or user_id_str in _rdp_loop_busy:
            continue

        config = load_user_config(int(user_id_str))
        if not config.get("github_token") or not config.get("loop_enabled"):
            continue

        _rdp_loop_busy.add(user_id_str)

        async def _runner(uid=user_id_str, cfg=config):
            try:
                await _process_user_rdp_loop(uid, cfg)
            except Exception as e:
                logger.warning(f"[Auto-Loop] Lỗi xử lý user {uid}: {e}")
            finally:
                _rdp_loop_busy.discard(uid)

        asyncio.create_task(_runner())


@auto_loop_task.before_loop
async def before_auto_loop():
    await bot.wait_until_ready()


class SettingsModal(discord.ui.Modal, title="⚙️ Cài Đặt Cấu Hình"):
    def __init__(self, current_config: dict):
        super().__init__()
        current_keywords = ", ".join(current_config.get("keywords", ["Login", "START"]))

        self.app_path = discord.ui.TextInput(
            label="Đường dẫn phần mềm trên RDP",
            placeholder=r"C:\Users\Thang\Desktop\Tool\tool.exe",
            default=current_config.get("app_path", r"C:\Users\Thang\Desktop\AutoTool\Tool.exe"),
            style=discord.TextStyle.short
        )
        self.keywords = discord.ui.TextInput(
            label="Từ khóa vượt menu (cách nhau dấu phẩy)",
            placeholder="Login, START",
            default=current_keywords,
            style=discord.TextStyle.short
        )
        self.threads = discord.ui.TextInput(
            label="Số luồng chạy",
            placeholder="1",
            default=str(current_config.get("threads", 1)),
            style=discord.TextStyle.short
        )
        self.license_key = discord.ui.TextInput(
            label="Key bản quyền (nếu có)",
            placeholder="Để trống nếu không có",
            default=current_config.get("license_key", ""),
            required=False,
            style=discord.TextStyle.short
        )

        self.add_item(self.app_path)
        self.add_item(self.keywords)
        self.add_item(self.threads)
        self.add_item(self.license_key)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["app_path"] = self.app_path.value.strip()
        config["keywords"] = [k.strip() for k in self.keywords.value.split(",") if k.strip()]
        try:
            config["threads"] = max(1, int(self.threads.value.strip() or 1))
        except ValueError:
            config["threads"] = 1
        config["license_key"] = self.license_key.value.strip() if self.license_key.value else ""
        save_user_config(user_id, config)

        await interaction.response.send_message(
            content=(
                "✅ **Đã lưu cài đặt cấu hình thành công!**\n"
                f"• **Đường dẫn:** `{config['app_path']}`\n"
                f"• **Từ khóa:** `{', '.join(config['keywords'])}`\n"
                f"• **Số luồng:** `{config['threads']}`"
            ),
            ephemeral=True
        )


class DataModeSelect(discord.ui.Select):
    def __init__(self, count: int):
        self.count = count
        options = [
            discord.SelectOption(label="📁 1. RDP Data Chung", description="Lấy chung dữ liệu & cấu hình từ 1 RDP", value="chung"),
            discord.SelectOption(label="📂 2. RDP Data Riêng Lẻ", description="Mỗi máy RDP chạy & lưu backup độc lập riêng", value="rieng")
        ]
        super().__init__(placeholder="Chọn chế độ đồng bộ Data...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        data_mode = self.values[0]
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["data_mode"] = data_mode
        config["target_rdp_count"] = self.count
        config["loop_enabled"] = True  # Tự động bật loop khi tạo máy
        save_user_config(user_id, config)

        gh_token = config.get("github_token")
        active_list = await scan_rdp_instances(gh_token, str(user_id))
        current_running_slots = sum(inst.get("slot_count", 1) for inst in active_list)

        if current_running_slots + self.count > 20:
            await interaction.edit_original_response(
                content=f"❌ **Không thể tạo thêm!** Bạn đang chạy **{current_running_slots}** máy. Tối đa chỉ cho phép 20 máy.",
                view=None
            )
            return

        await interaction.edit_original_response(
            content=f"🖥️ **Đang kết nối GitHub và dựng {self.count} máy RDP (Chế độ: {data_mode.upper()})...**",
            view=None
        )

        try:
            res = await deploy_rdp_real(gh_token, rdp_count=self.count, user_id=str(user_id), data_mode=data_mode)
            if res.get("ok"):
                repo_full = res.get("repo_full")
                reused_note = (
                    "\n♻️ **Đã tái sử dụng Repo cũ:** Tự động khôi phục dữ liệu từ bản sao lưu GitHub Release (`kanda_state.7z`)!"
                    if res.get("reused") else ""
                )
                await interaction.followup.send(
                    f"🎉 **Khởi tạo thành công {self.count} máy RDP trên repo:** `{repo_full}`!{reused_note}\n"
                    f"Thông tin kết nối `mstsc` sẽ tự động cập nhật trong bảng `/rdp` sau 1-2 phút khi runner khởi động xong.",
                    ephemeral=True
                )
            else:
                err_msg = res.get("error", "Lỗi không xác định")
                await interaction.followup.send(f"❌ **Lỗi khi tạo repo trên GitHub:** `{err_msg}`", ephemeral=True)
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
        options = [
            discord.SelectOption(label=f"{i} máy", description=f"Dựng {i} slot RDP (1-20)", emoji="🟢")
            for i in range(1, 21)
        ]
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

    @discord.ui.button(label="Tạo RDP", style=discord.ButtonStyle.success, emoji="🖥️", row=0)
    async def btn_create(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.send_message(
            content="**Chọn số lượng máy muốn dựng (1-20):**",
            view=RDPCountView(),
            ephemeral=True
        )

    @discord.ui.button(label="Tái Tạo RDP", style=discord.ButtonStyle.primary, emoji="♻️", row=0)
    async def btn_recreate(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        conf = load_user_config(uid)
        gh_token = conf.get("github_token")
        if not gh_token:
            await interaction.followup.send("❌ Bạn chưa liên kết GitHub Token!", ephemeral=True)
            return

        active_list = await scan_rdp_instances(gh_token, str(uid), include_inactive=False)
        running_slots = sum(inst.get("slot_count", 1) for inst in active_list)
        recreate_count = conf.get("target_rdp_count", 0) or running_slots or 1
        recreate_count = min(20, max(1, int(recreate_count)))
        data_mode = conf.get("data_mode", "chung")

        await interaction.followup.send(
            f"♻️ **Đang xóa toàn bộ RDP cũ và tái tạo mới `{recreate_count}` máy RDP (Chế độ: `{data_mode.upper()}`)...**",
            ephemeral=True
        )

        deleted_count = await delete_all_rdp_repos(gh_token, str(uid))
        await asyncio.sleep(2)

        res = await deploy_rdp_real(
            gh_token=gh_token,
            rdp_count=recreate_count,
            user_id=str(uid),
            data_mode=data_mode,
            force_new_repo=True
        )
        if res.get("ok"):
            conf["target_rdp_count"] = recreate_count
            conf["loop_enabled"] = True  # Đảm bảo bật loop sau khi tái tạo
            save_user_config(uid, conf)
            await interaction.followup.send(
                f"✅ **Tái tạo RDP hoàn tất!**\n"
                f"• Đã xóa sạch `{deleted_count}` repository cũ.\n"
                f"• Đã dựng mới **{recreate_count} máy RDP** tại repo: `{res.get('repo_full')}`.\n"
                f"• Nhấn nút **🔄 Làm Mới** sau 1-2 phút để nhận cổng `mstsc` mới!",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ **Đã xóa `{deleted_count}` repo cũ nhưng gặp lỗi khi tạo repo mới:** `{res.get('error')}`",
                ephemeral=True
            )

    @discord.ui.button(label="Dừng Tất Cả RDP", style=discord.ButtonStyle.danger, emoji="🛑", row=0)
    async def btn_stop_all(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        conf = load_user_config(uid)
        gh_token = conf.get("github_token")
        if not gh_token:
            await interaction.followup.send("❌ Bạn chưa liên kết GitHub Token!", ephemeral=True)
            return

        conf["loop_enabled"] = False
        save_user_config(uid, conf)

        stop_res = await stop_all_rdp_instances(gh_token, str(uid))
        stopped_repos = stop_res.get("stopped_repos", 0)
        stopped_slots = stop_res.get("stopped_slots", 0)

        if stopped_repos > 0:
            await interaction.followup.send(
                f"🛑 **Đã gửi lệnh dừng toàn bộ `{stopped_slots}` máy RDP** trên `{stopped_repos}` cụm repository!\n"
                f"*(Đã tự động **TẮT Auto Loop** để máy không tự bật lại)*.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"ℹ️ **Hiện không có máy RDP nào đang chạy để dừng.**\n*(Đã tắt Auto Loop)*.",
                ephemeral=True
            )

    @discord.ui.button(label="Làm Mới", style=discord.ButtonStyle.secondary, emoji="🔄", row=0)
    async def btn_refresh(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await send_rdp_menu(interaction, is_refresh=True)

    @discord.ui.button(label="Cài đặt", style=discord.ButtonStyle.secondary, emoji="⚙️", row=1)
    async def btn_settings(self, interaction: discord.Interaction, btn: discord.ui.Button):
        fresh_config = load_user_config(interaction.user.id)
        await interaction.response.send_modal(SettingsModal(fresh_config))

    @discord.ui.button(label="Auto Loop: Bật/Tắt", style=discord.ButtonStyle.secondary, emoji="🔁", row=1)
    async def btn_loop(self, interaction: discord.Interaction, btn: discord.ui.Button):
        uid = interaction.user.id
        conf = load_user_config(uid)
        conf["loop_enabled"] = not conf.get("loop_enabled", False)
        save_user_config(uid, conf)
        status = "BẬT 🟢 (Tự động xoay vòng tại chỗ & khôi phục Data mỗi chu kỳ)" if conf["loop_enabled"] else "TẮT 🔴"
        await interaction.response.send_message(f"Cơ chế Auto Loop đã **{status}**.", ephemeral=True)

    @discord.ui.button(label="Dọn Repo Dư", style=discord.ButtonStyle.secondary, emoji="🧹", row=1)
    async def btn_cleanup(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        conf = load_user_config(uid)
        gh_token = conf.get("github_token")
        if not gh_token:
            await interaction.followup.send("❌ Bạn chưa liên kết GitHub Token!", ephemeral=True)
            return

        deleted = await cleanup_inactive_repos(gh_token, str(uid), keep_latest_one=True)
        if deleted > 0:
            await interaction.followup.send(
                f"🧹 **Đã dọn dẹp `{deleted}` repository cũ dư thừa!**\n"
                f"*(Bot vẫn giữ lại 1 repo gốc để bảo toàn bản sao lưu dữ liệu `kanda_state.7z` trên Release)*.",
                ephemeral=True
            )
        else:
            await interaction.followup.send("✨ **Tài khoản gọn gàng!** Không có repository rác nào cần dọn dẹp.", ephemeral=True)

    @discord.ui.button(label="Đăng xuất", style=discord.ButtonStyle.danger, emoji="🚪", row=1)
    async def btn_logout(self, interaction: discord.Interaction, btn: discord.ui.Button):
        user_id = interaction.user.id
        config = load_user_config(user_id)
        config["github_token"] = ""
        save_user_config(user_id, config)

        await interaction.response.send_message(
            "🚪 **Đã đăng xuất tài khoản GitHub thành công!**",
            ephemeral=True
        )


async def send_rdp_menu(interaction: discord.Interaction, is_refresh: bool = False):
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

    if is_refresh:
        await interaction.response.defer()
    else:
        await interaction.response.defer(ephemeral=True)

    github_username, active_instances = await asyncio.gather(
        fetch_github_username(gh_token),
        scan_rdp_instances(gh_token, str(user_id))
    )

    total_running_slots = sum(inst.get("slot_count", 1) for inst in active_instances)

    instances_text = ""
    if active_instances:
        instances_text = "\n🟢 **Các cụm máy RDP của bạn đang hoạt động trên GitHub:**\n"
        for inst in active_instances:
            remain_min = inst.get("remain_min", 355)
            status_icon = (
                f"🔄 Đang chạy (Còn ~{remain_min} phút)"
                if inst["status"] == "in_progress"
                else f"⏳ Khởi tạo ({inst['status']})"
            )
            slot_count = inst.get("slot_count", 1)
            instances_text += f"• `{inst['repo']}` ({slot_count} máy) — {status_icon}\n"
            if inst.get("ports"):
                for p in inst["ports"]:
                    instances_text += f"  └─ 🖥️ **Slot {p['slot']}** | 🌐 `mstsc`: `{p['address']}` | 👤 `Thang` | 🔑 `Thang12345@`\n"
            else:
                instances_text += "  └─ ⏳ *Đang khôi phục Data & mở cổng Bore (Chờ 1-2 phút)...*\n"
    else:
        instances_text = "\n📂 *Không tìm thấy máy RDP nào đang hoạt động.*"

    loop_st = "🟢 BẬT" if config.get("loop_enabled") else "🔴 TẮT"
    target_cnt = config.get("target_rdp_count", 0)
    mode_label = "Chung" if config.get("data_mode", "chung") == "chung" else "Riêng từng Slot"

    menu_content = (
        "```ansi\n"
        "\x1b[1;36m🖥️ RDP — Menu Quản Lý Zero-Touch 24/7\x1b[0m\n"
        "```\n"
        f"👤 **Tài khoản GitHub:** `{github_username}` | 💾 **Chế độ Data:** `{mode_label}`\n"
        f"🔁 **Auto Loop:** {loop_st} (Mục tiêu: {target_cnt} máy) | 📊 **Tổng máy đang chạy:** `{total_running_slots}/20`\n"
        f"{instances_text}"
    )

    view = RDPMenuView(config)
    await interaction.edit_original_response(content=menu_content, view=view)


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
        await interaction.response.send_message("✅ **Lưu Token thành công!** Hãy dùng lệnh `/rdp`.", ephemeral=True)


class STKView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Nhập Token", style=discord.ButtonStyle.primary, emoji="✏️")
    async def enter_token(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TokenModal())


@bot.event
async def on_ready():
    if not auto_loop_task.is_running():
        auto_loop_task.start()
    logger.info(f"[+] Bot đã sẵn sàng: {bot.user}")


@bot.tree.command(name="rdp", description="Mở bảng điều khiển quản lý RDP 24/7")
async def rdp_menu_command(interaction: discord.Interaction):
    await send_rdp_menu(interaction)


@bot.tree.command(name="stk", description="Cung cấp Token GitHub bảo mật tuyệt đối")
async def stk_command(interaction: discord.Interaction):
    embed = discord.Embed(title="🔐 Bạn đã có token chưa?", color=0x2b2d31)
    embed.add_field(
        name="🐙 GitHub Token:",
        value="[Lấy token GitHub](https://github.com/settings/tokens)\n*(Cần bật quyền: repo, delete_repo, workflow)*",
        inline=False
    )
    embed.add_field(
        name="🚂 Railway Token:",
        value="[Lấy token Railway](https://railway.app/account/tokens)",
        inline=False,
    )
    embed.set_footer(text="Nhấn nút bên dưới để nhập an toàn.")
    await interaction.response.send_message(embed=embed, view=STKView(), ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        logger.warning("[!] Cảnh báo: Biến môi trường DISCORD_BOT_TOKEN chưa được thiết lập!")
    else:
        bot.run(TOKEN)