import os
import re
import json
import time
import random
import asyncio
import aiohttp
import functools
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestWebViewRequest
from telethon.tl.functions.channels import JoinChannelRequest

# Direct flush for GitHub Actions logs
print = functools.partial(print, flush=True)

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

API_ID = int(os.getenv("TG_API_ID") or 0)
API_HASH = os.getenv("TG_API_HASH", "")
NOTIFIER_BOT_TOKEN = os.getenv("NOTIFIER_BOT_TOKEN", "")
NOTIFIER_CHAT_ID = os.getenv("NOTIFIER_CHAT_ID", "")

BOT_USERNAME = "dogs_sk_bot"
MAIN_REF_CODE = "7928564136"
BASE_APP_URL = "https://dogs-sk-bot.ih0st.app/?tid=2506"
API_BASE = "https://dogs-sk-bot.ih0st.app/miniapp/packdrop"

ACCOUNTS_FILE = Path(__file__).resolve().parent / "accounts.json"
STATE_FILE = Path(__file__).resolve().parent / "dogs_sk_state.json"

MANDATORY_TARGETS = [
    "promotereferralink",
    "REFXREF2026",
    "refxrefx",
    "spamallowed4",
    "UsdtWatchEarn"
]

def load_json(file_path):
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def extract_init_data(full_url: str) -> str:
    parsed = urllib.parse.urlparse(full_url)
    params = urllib.parse.parse_qs(parsed.fragment)
    return params.get("tgWebAppData", [""])[0]

def format_http_proxy(proxy_cfg):
    if not proxy_cfg or not isinstance(proxy_cfg, dict):
        return None
    addr = proxy_cfg.get("addr") or proxy_cfg.get("hostname")
    port = proxy_cfg.get("port")
    user = proxy_cfg.get("username")
    pwd = proxy_cfg.get("password")
    if not addr or not port:
        return None
    if user and pwd:
        return f"http://{user}:{pwd}@{addr}:{port}"
    return f"http://{addr}:{port}"

async def get_dogs_price_usd(session) -> float:
    fallback_price = 0.000033
    sources = [
        {"url": "https://api.mexc.com/api/v3/ticker/price?symbol=DOGSUSDT", "key": "price"},
        {"url": "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=DOGS_USDT", "key": "last"},
        {"url": "https://api.binance.com/api/v3/ticker/price?symbol=DOGSUSDT", "key": "price"}
    ]
    for src in sources:
        try:
            async with session.get(src["url"], timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    val = data[0].get(src["key"]) if isinstance(data, list) and len(data) > 0 else data.get(src["key"]) if isinstance(data, dict) else None
                    if val:
                        return float(val)
        except Exception:
            continue
    return fallback_price

async def send_telegram_report(session, message_text: str):
    if not NOTIFIER_BOT_TOKEN or not NOTIFIER_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{NOTIFIER_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": NOTIFIER_CHAT_ID,
        "text": message_text,
        "parse_mode": "Markdown"
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                print("  [Notifier] Final report successfully sent to Telegram.")
            else:
                err_text = await resp.text()
                print(f"  [Notifier Error] Telegram API responded: {err_text}")
    except Exception as e:
        print(f"  [Notifier Error] Network exception: {e}")

async def get_account_init_data(client):
    bot_peer = await client.get_input_entity(BOT_USERNAME)
    web_view = await client(RequestWebViewRequest(
        peer=bot_peer,
        bot=bot_peer,
        platform="weba",
        from_bot_menu=False,
        url=BASE_APP_URL
    ))
    return extract_init_data(web_view.url)

async def join_and_verify_if_new(client, account_name, acc_data, state):
    if acc_data.get("mandatory_joined") and acc_data.get("referral_applied"):
        return

    print(f"[{account_name}] Onboarding new account...")
    bot_peer = await client.get_input_entity(BOT_USERNAME)

    if not acc_data.get("referral_applied"):
        cmd = "/start" if acc_data.get("is_main_account") else f"/start {MAIN_REF_CODE}"
        await client.send_message(bot_peer, cmd)
        acc_data["referral_applied"] = True
        await asyncio.sleep(2)

    for target in MANDATORY_TARGETS:
        try:
            await client(JoinChannelRequest(target))
        except Exception:
            pass
        await asyncio.sleep(1)

    messages = await client.get_messages(bot_peer, limit=5)
    for msg in messages:
        if msg.buttons:
            for r_idx, row in enumerate(msg.buttons):
                for c_idx, btn in enumerate(row):
                    if "verify" in (btn.text or "").lower():
                        try:
                            await msg.click(r_idx, c_idx)
                        except Exception:
                            pass
                        break

    acc_data["mandatory_joined"] = True
    acc_data["joined_targets"] = MANDATORY_TARGETS
    state[account_name] = acc_data
    save_json(STATE_FILE, state)
    print(f"[{account_name}] Initial onboarding completed.")

def get_real_balance(state_dict: dict) -> int:
    candidates = []
    if "balance" in state_dict and isinstance(state_dict["balance"], (int, float)):
        candidates.append(int(state_dict["balance"]))
    if "state" in state_dict and isinstance(state_dict["state"], dict):
        sub_bal = state_dict["state"].get("balance")
        if isinstance(sub_bal, (int, float)):
            candidates.append(int(sub_bal))
    return max(candidates) if candidates else 0

def get_standard_headers(user_agent: str):
    return {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referrer": BASE_APP_URL,
        "user-agent": user_agent
    }

async def ensure_valid_init_data(acc, acc_state, state):
    """بررسی و بازیابی توکن تلگرام در صورت خالی بودن یا منقضی شدن"""
    account_name = acc.get("name") or acc.get("account")
    init_data = acc_state.get("cached_init_data", "")

    if not init_data or not acc_state.get("mandatory_joined"):
        client = TelegramClient(StringSession(acc["session"]), API_ID, API_HASH)
        await client.connect()
        try:
            await join_and_verify_if_new(client, account_name, acc_state, state)
            init_data = await get_account_init_data(client)
            acc_state["cached_init_data"] = init_data
            save_json(STATE_FILE, state)
        finally:
            await client.disconnect()

    return init_data

async def execute_account_claim(session, init_data, user_agent, proxy_url, account_name):
    """فاز اول: کلیم سریع یک ساعته بدون توقف روی تسک‌های زمان‌بر"""
    headers = get_standard_headers(user_agent)
    payload = {"initData": init_data}
    state_url = f"{API_BASE}/state?tid=2506"
    server_now = int(time.time() * 1000)

    try:
        async with session.post(state_url, json=payload, headers=headers, proxy=proxy_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
            if not data.get("ok"):
                return None, False
            current_state = data
            server_now = data.get("now", server_now)
    except Exception as e:
        print(f"  [{account_name}] [State Error]: {e}")
        return None, False

    claim_info = current_state.get("claim", {})
    next_claim_at = claim_info.get("nextAt", 0)
    diff_sec = int((next_claim_at - server_now) / 1000)

    # اگر زیر ۴۵ ثانیه تا کلیم مانده بود صبر می‌کند
    if 0 < diff_sec <= 45:
        print(f"  [{account_name}] Claim timer: {diff_sec}s remaining. Waiting...")
        await asyncio.sleep(diff_sec + 2)
        server_now = int(time.time() * 1000)

    if server_now >= next_claim_at:
        claim_url = f"{API_BASE}/claim?tid=2506"
        try:
            async with session.post(claim_url, json=payload, headers=headers, proxy=proxy_url, timeout=aiohttp.ClientTimeout(total=10)) as c_resp:
                c_data = await c_resp.json()
                if c_data.get("ok"):
                    rew = c_data.get("reward", 50)
                    print(f"  [{account_name}] Claim Success: +{rew} DOGS")
                    if "state" in c_data:
                        current_state = c_data["state"]
                else:
                    print(f"  [{account_name}] Claim notice: {c_data}")
        except Exception as e:
            print(f"  [{account_name}] Claim Request Error: {e}")
    else:
        rem_sec = max(0, int((next_claim_at - server_now) / 1000))
        m, s = divmod(rem_sec, 60)
        print(f"  [{account_name}] Claim active -> Next in {m}m {s}s")

    return current_state, True

async def execute_heavy_tasks(session, init_data, user_agent, proxy_url, account_name, acc_state, state, current_state):
    """فاز دوم: گردونه، بازی استخوان، چک‌این روزانه و تسک‌های لینکی"""
    headers = get_standard_headers(user_agent)
    payload = {"initData": init_data}
    server_now = current_state.get("now", int(time.time() * 1000))
    today_str = time.strftime("%Y-%m-%d", time.gmtime(server_now / 1000))
    daily_done = acc_state.get("daily_completed_date") == today_str

    # ۱. بخش روزانه (چک‌این، گردونه، بازی استخوان)
    if not daily_done:
        # Check-in
        checkin_info = current_state.get("checkin", {})
        if not checkin_info.get("doneToday", False):
            try:
                async with session.post(f"{API_BASE}/checkin?tid=2506", json=payload, headers=headers, proxy=proxy_url) as ch_resp:
                    ch_data = await ch_resp.json()
                    if ch_data.get("ok"):
                        print(f"  [{account_name}] Check-in Day {ch_data.get('day', 1)} (+{ch_data.get('reward', 10)} DOGS)")
                        if "state" in ch_data:
                            current_state = ch_data["state"]
            except Exception as e:
                print(f"  [{account_name}] Check-in Error: {e}")

        # Wheel Spins
        spins_left = current_state.get("spinsLeft", 0)
        if spins_left > 0:
            print(f"  [{account_name}] Spinning {spins_left} available chances...")
            spin_url = f"{API_BASE}/spin?tid=2506"
            while spins_left > 0:
                await asyncio.sleep(random.uniform(2.8, 4.2))
                try:
                    async with session.post(spin_url, json=payload, headers=headers, proxy=proxy_url) as sp_resp:
                        if sp_resp.status != 200:
                            break
                        sp_data = await sp_resp.json()
                        if sp_data.get("ok"):
                            reward = sp_data.get("reward", 0)
                            sub_state = sp_data.get("state", {})
                            spins_left = sub_state.get("spinsLeft", spins_left - 1)
                            print(f"  [{account_name}] Wheel Won: +{reward} DOGS | Left: {spins_left}")
                            current_state = sub_state
                        else:
                            break
                except Exception:
                    break

        # Bones Game
        bones_left = current_state.get("bonesLeft", 0)
        if bones_left > 0:
            print(f"  [{account_name}] Starting Bones game ({bones_left} round)...")
            try:
                async with session.post(f"{API_BASE}/bones/start?tid=2506", json=payload, headers=headers, proxy=proxy_url) as b_start_resp:
                    b_start = await b_start_resp.json()
                    if b_start.get("ok"):
                        sec = b_start.get("seconds", 10)
                        await asyncio.sleep(sec + random.uniform(0.6, 1.2))
                        caught = random.randint(14, 17)
                        finish_payload = {"initData": init_data, "caught": caught}
                        async with session.post(f"{API_BASE}/bones/finish?tid=2506", json=finish_payload, headers=headers, proxy=proxy_url) as b_fin_resp:
                            b_fin = await b_fin_resp.json()
                            if b_fin.get("ok"):
                                print(f"  [{account_name}] Bones caught: {b_fin.get('caught', caught)} (+{b_fin.get('reward', 0)} DOGS)")
                                if "state" in b_fin:
                                    current_state = b_fin["state"]
            except Exception as e:
                print(f"  [{account_name}] Bones Error: {e}")

        acc_state["daily_completed_date"] = today_str
        save_json(STATE_FILE, state)

    # ۲. تسک‌های لینکی (Link Tasks) با مدیریت بهینه Cooldown
    tasks_list = current_state.get("tasks", [])
    now_ts = int(time.time() * 1000)
    eligible_tasks = []

    for t in tasks_list:
        if not t.get("done", False):
            eligible_tasks.append(t)
        elif t.get("availableAt", 0) > 0:
            rem_sec = int((t["availableAt"] - now_ts) / 1000)
            if rem_sec <= 0:
                eligible_tasks.append(t)
            elif rem_sec <= 30:
                t["_wait_seconds"] = rem_sec
                eligible_tasks.append(t)

    if eligible_tasks:
        print(f"  [{account_name}] Running {len(eligible_tasks)} link tasks...")
        for task in eligible_tasks:
            t_id = task.get("id")
            wait_sec = task.get("_wait_seconds", 0)
            if wait_sec > 0:
                print(f"  [{account_name}] Task #{t_id} unlocks in {wait_sec}s. Waiting...")
                await asyncio.sleep(wait_sec + 1.5)

            t_payload = {"taskId": t_id, "initData": init_data}
            try:
                async with session.post(f"{API_BASE}/open?tid=2506", json=t_payload, headers=headers, proxy=proxy_url) as op_resp:
                    op_data = await op_resp.json()
                    if not op_data.get("ok"):
                        continue

                # وقفه سروری ۱۰.۵ ثانیه برای ثبت کلیک لینک
                await asyncio.sleep(10.5)

                async with session.post(f"{API_BASE}/task?tid=2506", json=t_payload, headers=headers, proxy=proxy_url) as cl_resp:
                    cl_data = await cl_resp.json()
                    if cl_data.get("ok"):
                        print(f"  [{account_name}] Task #{t_id} completed (+{cl_data.get('reward', 1)} DOGS)")
                        if "state" in cl_data:
                            current_state = cl_data["state"]
            except Exception as e:
                print(f"  [{account_name}] Task #{t_id} Error: {e}")
            await asyncio.sleep(random.uniform(1.0, 1.8))

    # ۳. دریافت بالانس نهایی
    final_balance = 0
    try:
        async with session.post(f"{API_BASE}/state?tid=2506", json=payload, headers=headers, proxy=proxy_url) as f_resp:
            final_data = await f_resp.json()
            if final_data.get("ok"):
                final_balance = get_real_balance(final_data)
    except Exception:
        final_balance = get_real_balance(current_state)

    print(f"  >>> [{account_name} Balance]: {final_balance:,} DOGS")
    return final_balance

async def main():
    if not ACCOUNTS_FILE.exists():
        raise FileNotFoundError("accounts.json not found!")

    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        accounts = json.load(f)

    state = load_json(STATE_FILE)
    balances_report = {}

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        dogs_price = await get_dogs_price_usd(session)
        print(f">>> Live DOGS Price: ${dogs_price:.8f} USD")

        account_contexts = []

        # ==========================================
        # مرحله ۱: آماده‌سازی توکن و سشن اکانت‌ها
        # ==========================================
        for acc in accounts:
            acc_name = acc.get("name") or acc.get("account")
            ua = acc.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            proxy_url = format_http_proxy(acc.get("proxy"))

            acc_state = state.setdefault(acc_name, {
                "account_name": acc_name,
                "tg_id": str(acc.get("id", "")),
                "is_main_account": (acc_name == "Account 1"),
                "referral_applied": (acc_name == "Account 1"),
                "mandatory_joined": False,
                "joined_channels": [],
                "joined_groups": [],
                "completed_tasks": [],
                "joined_targets": [],
                "cached_init_data": ""
            })

            init_data = await ensure_valid_init_data(acc, acc_state, state)
            account_contexts.append({
                "raw_acc": acc,
                "name": acc_name,
                "ua": ua,
                "proxy": proxy_url,
                "state_ref": acc_state,
                "init_data": init_data,
                "app_state": None
            })

        # ==========================================
        # مرحله ۲: کلیم سریع و اولویت‌دار همه اکانت‌ها
        # ==========================================
        print("\n==================================================")
        print(">>> [PHASE 1] Fast Priority Hourly Claims (All Accounts)")
        print("==================================================")

        for ctx in account_contexts:
            app_state, ok = await execute_account_claim(
                session, ctx["init_data"], ctx["ua"], ctx["proxy"], ctx["name"]
            )

            # اگر توکن منقضی بود یک‌بار رفرش و مجدد تست می‌شود
            if not ok:
                print(f"  [{ctx['name']}] Token expired. Refreshing...")
                client = TelegramClient(StringSession(ctx["raw_acc"]["session"]), API_ID, API_HASH)
                await client.connect()
                try:
                    ctx["init_data"] = await get_account_init_data(client)
                    ctx["state_ref"]["cached_init_data"] = ctx["init_data"]
                    save_json(STATE_FILE, state)
                    app_state, _ = await execute_account_claim(
                        session, ctx["init_data"], ctx["ua"], ctx["proxy"], ctx["name"]
                    )
                finally:
                    await client.disconnect()

            ctx["app_state"] = app_state
            await asyncio.sleep(0.5)

        # ==========================================
        # مرحله ۳: تسک‌های روزانه و تسک‌های زمان‌بر لینکی
        # ==========================================
        print("\n==================================================")
        print(">>> [PHASE 2] Routine Tasks & Link Actions Pipeline")
        print("==================================================")

        for ctx in account_contexts:
            if not ctx["app_state"]:
                continue

            print(f"\n--- Processing: {ctx['name']} ---")
            bal = await execute_heavy_tasks(
                session, ctx["init_data"], ctx["ua"], ctx["proxy"],
                ctx["name"], ctx["state_ref"], state, ctx["app_state"]
            )
            balances_report[ctx["name"]] = bal

        # ==========================================
        # مرحله ۴: گزارش نهایی تلگرام
        # ==========================================
        total_dogs = sum(balances_report.values())
        total_usd = total_dogs * dogs_price

        print("\n==================================================")
        print(">>> FINAL REPORT")
        print("==================================================")
        report_lines = [
            "🐶 *Dogs SK Automation Report*",
            f"💵 *DOGS Price:* `${dogs_price:.8f} USD`\n"
        ]

        for name, bal in balances_report.items():
            usd_val = bal * dogs_price
            print(f"• {name:<12}: {bal:>7,} DOGS  (~${usd_val:>6.4f})")
            report_lines.append(f"• `{name}`: *{bal:,}* DOGS (~`${usd_val:.4f}`)")

        print("--------------------------------------------------")
        print(f"★ TOTAL BALANCE: {total_dogs:>7,} DOGS  (~${total_usd:>6.4f})")
        print("==================================================")

        report_lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        report_lines.append(f"🏆 *Total DOGS:* `{total_dogs:,}`")
        report_lines.append(f"💰 *Total USD:* `${total_usd:.4f}`")
        report_lines.append(f"⏰ *Time:* `{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}`")

        await send_telegram_report(session, "\n".join(report_lines))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n[!] Execution stopped cleanly by user (Ctrl+C).")
