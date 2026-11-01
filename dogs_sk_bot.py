import os
import re
import json
import time
import random
import asyncio
import aiohttp
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestWebViewRequest
from telethon.tl.functions.channels import JoinChannelRequest

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

API_ID = int(os.getenv("TG_API_ID") or 0)
API_HASH = os.getenv("TG_API_HASH", "")
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
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def extract_init_data(full_url: str) -> str:
    parsed = urllib.parse.urlparse(full_url)
    fragment = parsed.fragment
    params = urllib.parse.parse_qs(fragment)
    if "tgWebAppData" in params:
        return params["tgWebAppData"][0]
    return ""

async def get_dogs_price_usd(session) -> float:
    fallback_price = 0.000033
    url = "https://api.binance.com/api/v3/ticker/price?symbol=DOGSUSDT"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return float(data.get("price", fallback_price))
    except Exception:
        pass
    return fallback_price

async def get_account_init_data(client, account_name):
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

    print(f"[{account_name}] New account detected! Running initial setup...")
    bot_peer = await client.get_input_entity(BOT_USERNAME)

    if not acc_data.get("referral_applied"):
        if not acc_data.get("is_main_account"):
            await client.send_message(bot_peer, f"/start {MAIN_REF_CODE}")
        else:
            await client.send_message(bot_peer, "/start")
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
    print(f"[{account_name}] Initial registration completed.")

def get_real_balance(state_dict: dict) -> int:
    candidates = []
    if "balance" in state_dict and isinstance(state_dict["balance"], (int, float)):
        candidates.append(int(state_dict["balance"]))
    if "state" in state_dict and isinstance(state_dict["state"], dict):
        sub_bal = state_dict["state"].get("balance")
        if isinstance(sub_bal, (int, float)):
            candidates.append(int(sub_bal))
    return max(candidates) if candidates else 0

async def process_miniapp_tasks(session, init_data, user_agent, account_name, acc_state, state):
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referrer": BASE_APP_URL,
        "user-agent": user_agent
    }
    payload = {"initData": init_data}

    # ۱. دریافت State پایه
    state_url = f"{API_BASE}/state?tid=2506"
    current_state = None
    server_now = int(time.time() * 1000)

    try:
        async with session.post(state_url, json=payload, headers=headers) as resp:
            data = await resp.json()
            if data.get("ok"):
                current_state = data
                server_now = data.get("now", server_now)
            else:
                print(f"  [Error] Failed to load state: {data}")
                return 0
    except Exception as e:
        print(f"  [Error] API request failed: {e}")
        return 0

    today_str = time.strftime("%Y-%m-%d", time.gmtime(server_now / 1000))
    daily_done_in_state = acc_state.get("daily_completed_date") == today_str

    # ۲. کلیم دوره‌ای (Claim) با بررسی هوشمند تایمر زیر ۱ دقیقه
    claim_info = current_state.get("claim", {})
    next_claim_at = claim_info.get("nextAt", 0)
    diff_sec = int((next_claim_at - server_now) / 1000)

    # اگر تایمر زیر ۶۰ ثانیه مانده باشد، مکث می‌کنیم تا تکمیل شود
    if 0 < diff_sec <= 60:
        print(f"  [Claim] Only {diff_sec}s remaining. Waiting to claim...")
        await asyncio.sleep(diff_sec + 2)
        server_now = int(time.time() * 1000)

    if server_now >= next_claim_at:
        claim_url = f"{API_BASE}/claim?tid=2506"
        try:
            async with session.post(claim_url, json=payload, headers=headers) as c_resp:
                c_data = await c_resp.json()
                if c_data.get("ok"):
                    rew = c_data.get("reward", 50)
                    print(f"  [Claim] Successfully claimed: +{rew} DOGS")
                    if "state" in c_data:
                        current_state = c_data["state"]
                else:
                    print(f"  [Claim] Notice: {c_data}")
        except Exception as e:
            print(f"  [Claim] Error: {e}")
    else:
        rem_sec = max(0, int((next_claim_at - server_now) / 1000))
        m, s = divmod(rem_sec, 60)
        print(f"  [Claim] Active timer -> Next in {m}m {s}s")

    # ۳. تسک‌های روزانه (چک‌این، گردونه، استخوان)
    if not daily_done_in_state:
        # Check-in
        checkin_info = current_state.get("checkin", {})
        if not checkin_info.get("doneToday", False):
            checkin_url = f"{API_BASE}/checkin?tid=2506"
            try:
                async with session.post(checkin_url, json=payload, headers=headers) as ch_resp:
                    ch_data = await ch_resp.json()
                    if ch_data.get("ok"):
                        day = ch_data.get("day", 1)
                        rew = ch_data.get("reward", 10)
                        print(f"  [Check-in] Day {day} claimed (+{rew} DOGS)")
                        if "state" in ch_data:
                            current_state = ch_data["state"]
            except Exception as e:
                print(f"  [Check-in] Error: {e}")
        else:
            day_curr = checkin_info.get("day", 1)
            print(f"  [Check-in] Already claimed today (Day {day_curr})")

        # Wheel
        spins_left = current_state.get("spinsLeft", 0)
        if spins_left > 0:
            print(f"  [Wheel] Spinning {spins_left} available chances...")
            spin_url = f"{API_BASE}/spin?tid=2506"
            while spins_left > 0:
                await asyncio.sleep(random.uniform(3.2, 4.8))
                try:
                    async with session.post(spin_url, json=payload, headers=headers) as sp_resp:
                        if sp_resp.status != 200:
                            await asyncio.sleep(3)
                            continue
                        sp_data = await sp_resp.json()
                        if sp_data.get("ok"):
                            reward = sp_data.get("reward", 0)
                            sub_state = sp_data.get("state", {})
                            spins_left = sub_state.get("spinsLeft", spins_left - 1)
                            print(f"  [Wheel] Won: +{reward:<3} DOGS | Remaining: {spins_left}")
                            current_state = sub_state
                        else:
                            break
                except Exception:
                    await asyncio.sleep(2)
        else:
            print("  [Wheel] 0 spins left.")

        # Bones
        bones_left = current_state.get("bonesLeft", 0)
        if bones_left > 0:
            print(f"  [Bones] Found {bones_left} round. Starting game...")
            start_url = f"{API_BASE}/bones/start?tid=2506"
            finish_url = f"{API_BASE}/bones/finish?tid=2506"
            try:
                async with session.post(start_url, json=payload, headers=headers) as b_start_resp:
                    b_start = await b_start_resp.json()
                    if b_start.get("ok"):
                        game_seconds = b_start.get("seconds", 10)
                        print(f"  [Bones] Game running ({game_seconds}s round)...")
                        await asyncio.sleep(game_seconds + random.uniform(0.6, 1.2))

                        caught_count = random.randint(14, 17)
                        finish_payload = {"initData": init_data, "caught": caught_count}
                        async with session.post(finish_url, json=finish_payload, headers=headers) as b_fin_resp:
                            b_fin = await b_fin_resp.json()
                            if b_fin.get("ok"):
                                b_rew = b_fin.get("reward", 0)
                                print(f"  [Bones] Caught: {b_fin.get('caught', caught_count)} bones (+{b_rew} DOGS)")
                                if "state" in b_fin:
                                    current_state = b_fin["state"]
            except Exception as e:
                print(f"  [Bones] Error during game: {e}")
        else:
            print("  [Bones] No rounds left today.")

        acc_state["daily_completed_date"] = today_str
        save_json(STATE_FILE, state)
    else:
        print(f"  [Daily Tasks] Wheel, Bones & Check-in already finished for today ({today_str}).")

    # ۴. تسک‌های لینکی (Link Tasks) با فیلتر تسک‌های انجام‌نشده و آماده‌به‌تکرار
    tasks_list = current_state.get("tasks", [])
    now_ts = int(time.time() * 1000)
    eligible_tasks = []

    for t in tasks_list:
        is_done = t.get("done", False)
        avail_at = t.get("availableAt", 0)
        
        if not is_done:
            eligible_tasks.append(t)
        elif avail_at > 0:
            rem_t_sec = int((avail_at - now_ts) / 1000)
            if rem_t_sec <= 0:
                eligible_tasks.append(t)
            elif rem_t_sec <= 60:
                # اگر تایمر تسک زیر ۶۰ ثانیه مانده باشد، در لیست نگه می‌داریم تا بعد از مکث انجام شود
                t["_wait_seconds"] = rem_t_sec
                eligible_tasks.append(t)

    if eligible_tasks:
        print(f"  [Tasks] Found {len(eligible_tasks)} available tasks. Processing...")
        task_open_url = f"{API_BASE}/open?tid=2506"
        task_claim_url = f"{API_BASE}/task?tid=2506"

        for task in eligible_tasks:
            t_id = task.get("id")
            wait_sec = task.get("_wait_seconds", 0)
            if wait_sec > 0:
                print(f"  [Tasks] Task #{t_id} unlocks in {wait_sec}s. Waiting...")
                await asyncio.sleep(wait_sec + 2)

            success = False
            for attempt in range(2):
                try:
                    t_payload = {"taskId": t_id, "initData": init_data}
                    async with session.post(task_open_url, json=t_payload, headers=headers) as op_resp:
                        op_data = await op_resp.json()
                        if not op_data.get("ok"):
                            break

                    await asyncio.sleep(10.5)

                    async with session.post(task_claim_url, json=t_payload, headers=headers) as cl_resp:
                        cl_data = await cl_resp.json()
                        if cl_data.get("ok"):
                            r_rew = cl_data.get("reward", 1)
                            print(f"  [Tasks] Task #{t_id} completed (+{r_rew} DOGS)")
                            if "state" in cl_data:
                                current_state = cl_data["state"]
                            success = True
                            break
                        else:
                            print(f"  [Tasks] Task #{t_id} notice: {cl_data.get('error')}")
                            break
                except Exception as e:
                    if attempt == 0:
                        await asyncio.sleep(3)
                    else:
                        print(f"  [Tasks] Error on task #{t_id}: {e}")
                if success:
                    break
            await asyncio.sleep(random.uniform(1.0, 2.0))
    else:
        print(f"  [Tasks] All link tasks are currently on cooldown/completed.")

    # ۵. استخراج و نمایش بالانس در پایان کار هر اکانت
    final_balance = 0
    try:
        async with session.post(state_url, json=payload, headers=headers) as f_resp:
            final_data = await f_resp.json()
            if final_data.get("ok"):
                final_balance = get_real_balance(final_data)
    except Exception:
        final_balance = get_real_balance(current_state)

    print(f"  >>> [Account Balance]: {final_balance:,} DOGS")
    return final_balance

async def main():
    if not ACCOUNTS_FILE.exists():
        raise FileNotFoundError("accounts.json not found!")

    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        accounts = json.load(f)

    state = load_json(STATE_FILE)
    balances_report = {}

    print("==================================================")
    print(">>> Starting Full Automation: Claim, Daily, Wheel, Bones & Tasks")
    print("==================================================")

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        dogs_price = await get_dogs_price_usd(session)
        print(f">>> Current DOGS Price: ${dogs_price:.8f} USD")

        for idx, acc in enumerate(accounts, 1):
            acc_name = acc.get("name") or acc.get("account")
            ua = acc.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

            print(f"\n--- [{idx}/{len(accounts)}] {acc_name} ---")

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

            init_data = acc_state.get("cached_init_data", "")

            # اتصال Telethon صرفاً در صورت عدم وجود کش یا عدم تایید اولیه
            if not init_data or not acc_state.get("mandatory_joined"):
                client = TelegramClient(StringSession(acc["session"]), API_ID, API_HASH)
                await client.connect()

                await join_and_verify_if_new(client, acc_name, acc_state, state)

                try:
                    init_data = await get_account_init_data(client, acc_name)
                    acc_state["cached_init_data"] = init_data
                    save_json(STATE_FILE, state)
                except Exception as e:
                    print(f"  [Error] Could not retrieve initData: {e}")
                    await client.disconnect()
                    continue

                await client.disconnect()

            bal = await process_miniapp_tasks(session, init_data, ua, acc_name, acc_state, state)
            balances_report[acc_name] = bal

            await asyncio.sleep(0.5)

    total_dogs = sum(balances_report.values())
    total_usd = total_dogs * dogs_price

    print("\n==================================================")
    print(">>> FINAL ACCOUNTS BALANCE REPORT")
    print("==================================================")
    for name, bal in balances_report.items():
        usd_val = bal * dogs_price
        print(f"• {name:<12}: {bal:>7,} DOGS  (~${usd_val:>6.4f})")
    print("--------------------------------------------------")
    print(f"★ TOTAL BALANCE: {total_dogs:>7,} DOGS  (~${total_usd:>6.4f})")
    print("==================================================")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n[!] Execution stopped cleanly by user (Ctrl+C).")