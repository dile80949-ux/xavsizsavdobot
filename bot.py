"""
BOT.PY - Kafil / Garant (Escrow Middleman) Telegram Boti
Aiogram 3.x asosida to'liq, xatosiz, modulli arxitektura va TolovAPI rasmiy integratsiyasi.
"""
import os
import sys
import re
import json
import random
import string
import logging
import asyncio
import requests
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from aiohttp import web, ClientSession, ClientTimeout
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    BufferedInputFile,
    ChatMemberUpdated,
    TelegramObject
)

from config import (
    BOT_TOKEN, ADMIN_IDS, DEFAULT_CARD_NUMBER, DEFAULT_CARD_OWNER,
    TOLOV_API_URL, SHOP_ID, SHOP_KEY, PORT, TZ, esc_html, fmt, is_admin, format_card
)
import database as db
from ai_helper import (
    ask_gemini_ai,
    check_chat_safety,
    analyze_deals_for_admin,
    validate_user_input,
    ai_chat_guardian,
    verify_statement_with_ai,
    verify_statement_with_ai_async,
    ai_dispute_solver,
    calculate_payout_details
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger("kafil_bot")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ============================ MAJBURIY OBUNA MIDDLEWARE ============================
class MandatorySubMiddleware(BaseMiddleware):
    """
    Kanalga a'zo bo'lmagan foydalanuvchilar uchun botni to'liq bloklovchi middleware.
    Faqat barcha majburiy kanallarga a'zo bo'lgandagina bot ishlaydi.
    """
    async def __call__(
        self,
        handler: Any,
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        uid = user.id

        # Admin panel boshqaruvi va kanallarni sozlash uchun adminga ruxsat berish
        if is_admin(uid):
            if isinstance(event, Message) and event.text and (event.text.startswith("/admin") or "Admin" in event.text or "👑" in event.text):
                return await handler(event, data)
            if isinstance(event, CallbackQuery) and event.data and event.data.startswith("adm_"):
                return await handler(event, data)

        # check_sub callbackiga ruxsat berish
        if isinstance(event, CallbackQuery) and event.data == "check_sub":
            return await handler(event, data)

        # Guruhdagi xabarlarni bloklamaymiz (faqat bot shaxsiy chati bloklanadi)
        chat = data.get("event_chat")
        if chat and chat.type in ("group", "supergroup"):
            return await handler(event, data)

        # Obunani tekshirish
        is_sub, missing = await check_user_subscribed(uid)
        if not is_sub:
            kb = []
            for ch in missing:
                kb.append([InlineKeyboardButton(text=f"📢 {ch['channel_title']}", url=ch["invite_link"])])
            kb.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")])

            text = (
                f"⚠️ <b>DIQQAT: Botdan foydalanish uchun rasmiy kanallarga a'zo bo'ling!</b>\n\n"
                f"Kafil botining barcha xizmatlari va kafolatli savdolardan to'liq foydalanish uchun quyidagi kanallarga obuna bo'lishingiz shart:\n\n"
                f"<i>(A'zo bo'lgach, «✅ Obunani tekshirish» tugmasini bosing)</i>"
            )

            if isinstance(event, Message):
                await event.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
            elif isinstance(event, CallbackQuery):
                await event.answer("⚠️ Avval barcha kanallarga a'zo bo'ling!", show_alert=True)
                try:
                    await event.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
                except Exception:
                    pass
            return

        return await handler(event, data)

dp.message.middleware(MandatorySubMiddleware())
dp.callback_query.middleware(MandatorySubMiddleware())

router = Router()
dp.include_router(router)

# ============================ 1. UNIKAL KOD GENERATORLARI ============================
def generate_deal_id() -> str:
    chars = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = "K" + "".join(random.choice(chars) for _ in range(6))
        exists = db.get_deal(code)
        if not exists:
            return code
    return "K" + str(random.randint(100000, 999999))

def generate_group_code() -> str:
    return "KAFIL-" + "".join(random.choice("0123456789ABCDEF") for _ in range(5))

# ============================ 2. ASOSIY MENYU TUGMALARI ============================
def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="📜 𝗧𝗮𝗿𝗶𝘅"), KeyboardButton(text="🆘 𝗦𝗢𝗦 (𝗬𝗼𝗿𝗱𝗮𝗺)")],
        [KeyboardButton(text="👥 𝗥𝗲𝗳𝗲𝗿𝗮𝗹 𝗧𝗶𝘇𝗶𝗺𝗶"), KeyboardButton(text="🤝 𝗬𝗮𝗻𝗴𝗶 𝗦𝗮𝘃𝗱𝗼 𝗢𝗰𝗵𝗶𝘀𝗵")],
        [KeyboardButton(text="🚫 𝗙𝗶𝗿𝗶𝗯𝗴𝗮𝗿𝗹𝗮𝗿 𝗥𝗼'𝘆𝘅𝗮𝘁𝗶"), KeyboardButton(text="💾 𝗭𝗮𝘅𝗶𝗿𝗮 𝗡𝘂𝘀𝘅𝗮 (𝗕𝗮𝗰𝗸𝘂𝗽)")],
        [KeyboardButton(text="📑 𝗔𝗿𝗶𝘇𝗮𝗹𝗮𝗿𝗶𝗺"), KeyboardButton(text="👑 𝗔𝗱𝗺𝗶𝗻 𝗣𝗮𝗻𝗲𝗹")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ============================ 3. FSM HOLATLARI ============================
class CreateDealFSM(StatesGroup):
    mode = State()
    title = State()
    seller = State()
    amount = State()
    group_link = State()

class SOSFSM(StatesGroup):
    ai_query = State()
    complaint_seller = State()
    complaint_reason = State()
    deal_complaint_reason = State()

class WithdrawFSM(StatesGroup):
    card_number = State()
    card_owner = State()

class BackupFSM(StatesGroup):
    upload_file = State()

class AdminFSM(StatesGroup):
    broadcast = State()
    add_black_user = State()
    add_black_reason = State()
    add_channel_id = State()
    add_channel_title = State()
    add_channel_link = State()
    change_card = State()
    change_owner = State()
    change_commission = State()
    ai_chat = State()
    reply_ticket_id = State()
    reply_ticket_text = State()

# ============================ 4. TOLOVAPI.UZ INTEGRATSIYASI ============================
_active_checking_deals = set()

def tolovapi_sync_request(path: str, params: dict, method: str = "POST", timeout: int = 15) -> dict:
    """TolovAPI.uz rasmiy API mijozi (X-Shop-Key sarlavhasi bilan)."""
    url = f"{TOLOV_API_URL.rstrip('/')}/{path.lstrip('/')}"
    full_params = {"shop_id": str(SHOP_ID), **params}
    headers = {"X-Shop-Key": str(SHOP_KEY)}
    try:
        if method.upper() == "GET":
            r = requests.get(url, params=full_params, headers=headers, timeout=timeout)
        else:
            r = requests.post(url, json=full_params, headers=headers, timeout=timeout)
        return r.json()
    except Exception as exc:
        logger.error(f"TolovAPI xatosi ({path}): {exc}")
        return {"ok": False, "error": str(exc)}

async def tolovapi_request(path: str, params: dict, method: str = "POST", timeout_sec: int = 15) -> dict:
    return await asyncio.to_thread(tolovapi_sync_request, path, params, method, timeout_sec)

async def create_payment_async(amount: int, order_id: Optional[str] = None, user_id: Optional[int] = None) -> Optional[dict]:
    """TolovAPI orqali yangi to'lov hisobini ochish."""
    oid = str(order_id) if order_id else f"ORD{random.randint(100000, 999999)}"
    res = await tolovapi_request("payment/create", {"amount": int(amount), "order_id": oid}, method="POST", timeout_sec=15)

    if res.get("error_code") == "amount_busy" or res.get("error") == "amount_busy":
        suggested = res.get("suggested_amount")
        if suggested and int(suggested) != amount:
            logger.info(f"TolovAPI: {amount} summa band, taklif: {suggested} bilan ochilmoqda...")
            res = await tolovapi_request("payment/create", {"amount": int(suggested), "order_id": oid}, method="POST", timeout_sec=15)

    if res.get("ok") or res.get("payment_id"):
        return {
            "ok": True,
            "payment_id": str(res.get("payment_id", "")),
            "amount": int(res.get("amount", amount)),
            "card_number": res.get("card_number") or db.get_setting("card_number", DEFAULT_CARD_NUMBER),
            "card_owner": DEFAULT_CARD_OWNER,
            "expires_in": res.get("expires_in", 300),
            "payment_url": res.get("payment_url", ""),
            "raw": res
        }
    logger.warning(f"TolovAPI create muvaffaqiyatsiz bo'ldi: {res}")
    return None

async def check_payment_status_async(payment_id: str) -> Optional[dict]:
    """TolovAPI orqali to'lov holatini tekshirish (GET orqali)."""
    if not payment_id:
        return None
    res = await tolovapi_request("payment/status", {"payment_id": str(payment_id)}, method="GET", timeout_sec=10)
    st = str(res.get("status", "")).lower()
    return {
        "ok": res.get("ok", False) or (st in ("paid", "pending", "success")),
        "paid": (st in ("paid", "success")),
        "status": st,
        "amount": res.get("amount"),
        "card_number": res.get("card_number"),
        "raw": res
    }

async def cancel_payment_async(payment_id: str) -> bool:
    """TolovAPI orqali to'lovni bekor qilish."""
    if not payment_id:
        return False
    res = await tolovapi_request("payment/cancel", {"payment_id": str(payment_id)}, method="POST")
    return res.get("ok", False) or res.get("status") == "cancel"

# ============================ 5. MAJBURIY OBUNA VA REFERALLAR ============================
async def check_user_subscribed(user_id: int) -> Tuple[bool, List[dict]]:
    channels = db.q("SELECT * FROM mandatory_channels")
    if not channels:
        return True, []
    unsubscribed = []
    for ch in channels:
        try:
            raw_cid = ch["channel_id"]
            cid = int(raw_cid) if str(raw_cid).lstrip("-").isdigit() else raw_cid
            member = await bot.get_chat_member(chat_id=cid, user_id=user_id)
            if member.status in ("creator", "administrator", "member", "restricted"):
                continue
            else:
                unsubscribed.append(dict(ch))
        except Exception as e:
            # Agar foydalanuvchi kanalda bo'lmasa yoki Telegram xatolik qaytarsa:
            logger.warning(f"Kanalga a'zolikni tekshirish xatosi (user={user_id}, ch={ch.get('channel_title')}): {e}")
            unsubscribed.append(dict(ch))
    return (len(unsubscribed) == 0), unsubscribed

async def reward_referral_if_eligible(referred_id: int):
    u = db.get_user(referred_id)
    if not u or not u["referred_by"]:
        return
    ref_row = db.q("SELECT * FROM referrals WHERE referred_id=? AND status='pending'", (referred_id,), one=True)
    if not ref_row:
        return

    referrer_id = u["referred_by"]
    today_prefix = datetime.now(TZ).strftime("%Y-%m-%d")
    today_count = db.q("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND status='rewarded' AND created_at LIKE ?",
                       (referrer_id, f"{today_prefix}%"), one=True)["c"]

    if today_count >= 20:
        logger.info(f"Referrer {referrer_id} kunlik 20 ta limitga yetgan.")
        return

    db.q("UPDATE users SET referral_balance=referral_balance+100 WHERE user_id=?", (referrer_id,), commit=True)
    db.q("UPDATE referrals SET status='rewarded' WHERE id=?", (ref_row["id"],), commit=True)

    try:
        await bot.send_message(
            referrer_id,
            f"🎉 <b>YANGI REFERAL MUKOFOTI!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Siz taklif qilgan foydalanuvchi (@{esc_html(u['username'] or str(referred_id))}) botdan to'liq ro'yxatdan o'tdi.\n"
            f"💰 Hisobingizga <b>+100 so'm</b> qo'shildi!\n"
            f"Bugun qo'shgan do'stlaringiz: <b>{today_count + 1}/20 ta</b>"
        )
    except Exception:
        pass

async def penalize_referral_if_left(user_id: int):
    ref = db.q("SELECT * FROM referrals WHERE referred_id=? AND status='rewarded'", (user_id,), one=True)
    if not ref:
        return
    referrer_id = ref["referrer_id"]
    referrer = db.get_user(referrer_id)
    if not referrer:
        return

    new_bal = referrer["referral_balance"] - 200
    db.q("UPDATE users SET referral_balance=? WHERE user_id=?", (new_bal, referrer_id), commit=True)
    db.q("UPDATE referrals SET status='penalized' WHERE id=?", (ref["id"],), commit=True)

    try:
        left_u = db.get_user(user_id)
        tag = f"@{left_u['username']}" if left_u and left_u['username'] else f"ID {user_id}"
        await bot.send_message(
            referrer_id,
            f"⚠️ <b>REFERAL JARIMASI: -200 so'm!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Siz taklif qilgan {tag} kanaldan chiqib ketdi.\n"
            f"Qoidalarga muvofiq hisobingizdan <b>200 so'm</b> jarima ayirildi.\n"
            f"Joriy balansingiz: <b>{fmt(new_bal)} so'm</b>"
        )
    except Exception:
        pass

# ============================ 6. ASINXRON FON TAYMERLARI ============================
async def activate_paid_deal(deal_id: str, edit_call: Optional[CallbackQuery] = None, state: Optional[FSMContext] = None) -> bool:
    deal = db.get_deal(deal_id)
    if not deal or deal["status"] != "waiting_payment":
        return False

    now_s = datetime.now(TZ).isoformat()
    db.q("UPDATE deals SET status='in_trade', updated_at=? WHERE deal_id=?", (now_s, deal_id), commit=True)

    buyer_id = deal["buyer_id"]
    seller_tag = deal["seller_username"]
    title = deal["title"]
    d_id = deal["deal_id"]
    total = deal["total_to_pay"]

    if deal["mode"] == "group":
        if state:
            await state.set_state(CreateDealFSM.group_link)
            await state.update_data(active_deal_id=d_id)
        else:
            try:
                buyer_state = dp.fsm.get_context(bot=bot, chat_id=buyer_id, user_id=buyer_id)
                await buyer_state.set_state(CreateDealFSM.group_link)
                await buyer_state.update_data(active_deal_id=d_id)
            except Exception:
                pass

        text = (
            f"✅ <b>TO'LOV MUVAFFAQIYATLI QABUL QILINDI! (#{d_id})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>To'langan summa:</b> <b>{fmt(total)} so'm</b> (Muzlatildi 🔒)\n"
            f"📦 <b>Mahsulot:</b> <b>{esc_html(title)}</b>\n\n"
            f"Endi meni guruhingizga qo'shishingiz va menga <b>ADMIN HUQUQINI</b> berishingiz kerak.\n\n"
            f"👉 <b>Menga guruhingizning taklif havolasini (linkini) yuboring:</b>\n"
            f"<i>(Masalan: https://t.me/+xxxxxx yoki https://t.me/guruh_nomi)</i>"
        )
        if edit_call:
            try:
                await edit_call.message.edit_text(text)
            except Exception:
                await bot.send_message(buyer_id, text)
        else:
            await bot.send_message(buyer_id, text)
    else:
        bot_info = await bot.get_me()
        invite_link = f"https://t.me/{bot_info.username}?start=deal_{d_id}"
        text = (
            f"✅ <b>TO'LOV MUVAFFAQIYATLI QABUL QILINDI VA MUZLATILDI! (#{d_id})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>To'langan summa:</b> <b>{fmt(total)} so'm</b> (Kafil hisobida xavfsiz muzlatildi 🔒)\n"
            f"📦 <b>Mahsulot:</b> <b>{esc_html(title)}</b>\n\n"
            f"Endi sotuvchi @{esc_html(seller_tag)} ga quyidagi ulanish havolasini yuboring:\n"
            f"👉 <code>{invite_link}</code>\n"
            f"<i>(Havola ustiga bosing — avtomatik nusxalanadi!)</i>\n\n"
            f"Sotuvchi havola orqali botga kirishi bilan bot ichidagi xavfsiz chat ishga tushadi."
        )
        if edit_call:
            try:
                await edit_call.message.edit_text(text)
            except Exception:
                await bot.send_message(buyer_id, text)
        else:
            await bot.send_message(buyer_id, text)

    # Sotuvchiga ham xabarnoma berish (agar uning IDsi ma'lum bo'lsa)
    if deal.get("seller_id"):
        try:
            await bot.send_message(
                deal["seller_id"],
                f"🎉 <b>Xaridor to'lovni amalga oshirdi! (#{d_id})</b>\n"
                f"Pul kafil hisobida xavfsiz muzlatildi 🔒.\n"
                f"Tovar/xizmatni topshirishga tayyor turing."
            )
        except Exception:
            pass
    return True

async def deal_auto_payment_watcher(deal_id: str):
    """
    To'lov hisobi ochilgach, 5 daqiqa (300 soniya) davomida har 3 soniyada
    TolovAPI.uz dan to'lov tushganini avtomatik tekshiruvchi tezyurar fon taymeri.
    Pul kartaga tushishi bilanoq xaridor tugmani bosishini kutmasdan darhol savdoni faollashtiradi!
    """
    start_time = time.time()
    while time.time() - start_time < 300:
        await asyncio.sleep(3)
        deal = db.get_deal(deal_id)
        if not deal or deal["status"] != "waiting_payment":
            return  # Bitim allaqachon to'langan yoki bekor qilingan

        payment_id = deal.get("payment_id")
        if payment_id:
            try:
                chk = await check_payment_status_async(payment_id)
                if chk and chk.get("paid"):
                    paid_amt = int(chk.get("amount") or chk.get("raw", {}).get("amount", 0) or deal["total_to_pay"])
                    if paid_amt >= int(deal["total_to_pay"]):
                        logger.info(f"Avtomatik tezkor to'lov aniqlandi: #{deal_id}, Summa: {paid_amt}")
                        await activate_paid_deal(deal_id)
                        return
            except Exception as e:
                logger.error(f"Watcher tekshiruv xatosi (#{deal_id}): {e}")

    # Agar 5 daqiqa ichida to'lov qilinmasa - bekor qilinadi
    deal = db.get_deal(deal_id)
    if deal and deal["status"] == "waiting_payment":
        now_s = datetime.now(TZ).isoformat()
        db.q("UPDATE deals SET status='cancelled', updated_at=? WHERE deal_id=?", (now_s, deal_id), commit=True)
        if deal.get("payment_id"):
            await cancel_payment_async(deal["payment_id"])
        try:
            await bot.send_message(
                deal["buyer_id"],
                f"⏱ <b>5 DAQIQA VAQT TUGADI! (#{deal_id})</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Belgilangan 5 daqiqa ichida to'lov qilinmagani sababli to'lov hisobi avtomatik <b>bekor qilindi</b>."
            )
        except Exception:
            pass

deal_5min_timer = deal_auto_payment_watcher

async def complaints_auto_expire_loop():
    """5 kunda arizaga javob bermagan sotuvchini avtomatik qora ro'yxatga kirituvchi loop."""
    while True:
        try:
            await asyncio.sleep(600)
            now_s = datetime.now(TZ).isoformat()
            expired = db.q("SELECT * FROM complaints WHERE status='pending' AND deadline_at <= ?", (now_s,))
            for c in expired:
                db.q("UPDATE complaints SET status='blacklisted' WHERE id=?", (c["id"],), commit=True)
                db.add_to_blacklist(
                    user_id=c["accused_id"],
                    username=c["accused_username"],
                    reason=f"Ariza #{c['id']} bo'yicha 5 kunda javob bermagan: {c['reason']}",
                    deal_id=c["deal_id"] or "",
                    added_by="complaint_deadline"
                )
                try:
                    await bot.send_message(
                        c["complainant_id"],
                        f"⚖️ <b>ARIZA BO'YICHA QAROR: #{c['id']}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"Sotuvchi @{esc_html(c['accused_username'])} 5 kun ichida javob bermadi va "
                        f"butunlay <b>QORA RO'YXATGA</b> kiritildi!"
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"complaints loop xatosi: {e}")

async def referral_leave_checker_loop():
    """Kanaldan chiqib ketgan referallarni tekshiruvchi loop."""
    while True:
        try:
            await asyncio.sleep(1800)
            channels = db.q("SELECT * FROM mandatory_channels")
            if not channels:
                continue
            active_refs = db.q("SELECT * FROM referrals WHERE status='rewarded'")
            for r in active_refs:
                uid = r["referred_id"]
                is_sub, _ = await check_user_subscribed(uid)
                if not is_sub:
                    await penalize_referral_if_left(uid)
        except Exception as e:
            logger.error(f"referral checker loop xatosi: {e}")

# ============================ 7. BUYRUQLAR VA BEKOR QILISH ============================
@router.message(StateFilter("*"), Command("cancel"))
@router.message(StateFilter("*"), F.text.contains("Bekor") | F.text.contains("𝗕𝗲𝗸𝗼𝗿") | F.text.startswith("❌"))
async def universal_cancel_handler(msg: Message, state: FSMContext):
    """Har qanday amaliyotni bekor qilib asosiy menyuga qaytaruvchi universal handler."""
    await state.clear()
    await msg.answer(
        "❌ <b>Barcha amallar bekor qilindi.</b>\n\n"
        "Siz <b>Asosiy Menyu</b>dasiz. Kerakli bo'limni tanlang:",
        reply_markup=main_menu_keyboard(msg.from_user.id)
    )

@router.callback_query(F.data == "cancel_fsm")
async def cancel_fsm_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Bekor qilindi.")
    await call.message.edit_text("❌ <b>Amaliyot bekor qilindi.</b>")

@router.message(StateFilter("*"), CommandStart())
async def start_cmd(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    username = msg.from_user.username or ""
    full_name = msg.from_user.full_name or "Foydalanuvchi"

    args = msg.text.split(maxsplit=1)[1] if len(msg.text.split()) > 1 else ""
    referred_by = None

    if args.startswith("ref_"):
        ref_raw = args.replace("ref_", "").strip()
        if ref_raw.isdigit() and int(ref_raw) != uid:
            referred_by = int(ref_raw)
            exists = db.q("SELECT id FROM referrals WHERE referred_id=?", (uid,), one=True)
            if not exists:
                db.q("INSERT INTO referrals (referrer_id, referred_id, status, created_at) VALUES (?, ?, 'pending', ?)",
                     (referred_by, uid, datetime.now(TZ).isoformat()), commit=True)

    db.ensure_user(uid, username, full_name, referred_by)

    # Sotuvchi bitim havolasi orqali kirgan holat: /start deal_K12345
    if args.startswith("deal_"):
        d_id = args.replace("deal_", "").strip()
        deal = db.get_deal(d_id)
        if deal:
            if deal["buyer_id"] == uid:
                await msg.answer("⚠️ <b>Siz ushbu bitimning xaridorisiz. Sotuvchi ushbu havola orqali ulanishi kerak.</b>", reply_markup=main_menu_keyboard(uid))
                return
            now_s = datetime.now(TZ).isoformat()
            clean_un = (username or "").lstrip("@")
            db.q("UPDATE deals SET seller_id=?, seller_username=?, updated_at=? WHERE deal_id=?", (uid, clean_un, now_s, d_id), commit=True)
            await msg.answer(
                f"🤝 <b>SIZ SAVDOGA SOTUVCHI SIFATIDA QO'SHILDINGIZ! (#{d_id})</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 <b>Tovar:</b> <b>{esc_html(deal['title'])}</b>\n"
                f"💵 <b>Siz oladigan sof summa:</b> <b>{fmt(deal['amount'])} so'm</b>\n"
                f"🛒 <b>Xaridor:</b> @{esc_html(deal['buyer_username'])}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🔒 <b>Pul kafil hisobida xavfsiz muzlatilgan!</b>\n"
                f"Siz bemalol tovaringizni (akkaunt/kanal/kod) xaridorga taqdim etishingiz mumkin.\n"
                f"Xaridor tovarni tekshirib olgach rozilik berishi bilan pul kartangizga o'tkaziladi.",
                reply_markup=main_menu_keyboard(uid)
            )
            try:
                await bot.send_message(
                    deal["buyer_id"],
                    f"🎉 <b>Sotuvchi @{esc_html(clean_un)} savdoga qo'shildi! (#{d_id})</b>\n"
                    f"Sotuvchi bilan bot ichidagi xavfsiz chat orqali muloqot qilishingiz mumkin."
                )
            except Exception:
                pass
            return

    # Majburiy obuna tekshiruvi
    is_sub, missing = await check_user_subscribed(uid)
    if not is_sub:
        kb = []
        for ch in missing:
            kb.append([InlineKeyboardButton(text=f"📢 {ch['channel_title']}", url=ch["invite_link"])])
        kb.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")])
        await msg.answer(
            f"👋 <b>Assalomu alaykum, {esc_html(full_name)}!</b>\n\n"
            f"Botdan to'liq foydalanish va kafolatli savdolarni amalga oshirish uchun quyidagi rasmiy kanallarga a'zo bo'ling:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
        return

    await reward_referral_if_eligible(uid)

    # Qora ro'yxat tekshiruvi
    banned, b_reason = db.is_blacklisted(user_id=uid, username=username)
    if banned:
        await msg.answer(
            f"🚫 <b>DIQQAT: Sizning hisobingiz QORA RO'YXATGA tushgan!</b>\n"
            f"Sabab: <i>{esc_html(b_reason)}</i>\n\n"
            f"Sizga ushbu bot xizmatlaridan foydalanish qat'iyan taqiqlanadi."
        )
        return

    text = (
        f"🛡 <b>KAFOLATLI SAVDO (ESCROW / GARANT) TIZIMI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 <b>Assalomu alaykum, {esc_html(full_name)}!</b>\n\n"
        f"Bu yerda siz har qanday <b>Telegram kanal</b>, <b>akkaunt</b>, <b>kodlar</b> yoki <b>onlayn xizmatlarni</b> 100% ishonchli va kafolatlangan holatda xavfsiz sotib olishingiz yoki sotishingiz mumkin.\n\n"
        f"💎 <b>BOT IMTIYOZLARI VA QULAYLIKLARI:</b>\n"
        f"├ 🎁 <b>1-savdongiz:</b> <b>MUTLAQO BEPUL (0% komissiya)</b>\n"
        f"├ 🤖 <b>AI Qorovul:</b> <b>Gemini 2.5 Flash intellekti nazoratida</b>\n"
        f"├ 👥 <b>Referal tizimi:</b> <b>Do'stlaringiz uchun har safar +100 so'm</b>\n"
        f"└ ⚡️ <b>Tezkor to'lovlar:</b> <b>TolovAPI.uz orqali to'liq avtomatlashgan</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <b>Quyidagi menyudan kerakli bo'limni tanlang:</b>"
    )
    await msg.answer(text, reply_markup=main_menu_keyboard(uid))

@router.callback_query(F.data == "check_sub")
async def callback_check_sub(call: CallbackQuery):
    uid = call.from_user.id
    is_sub, missing = await check_user_subscribed(uid)
    if not is_sub:
        await call.answer("⚠️ Hali barcha kanallarga a'zo bo'lmadingiz! Iltimos, ko'rsatilgan kanallarga a'zo bo'lib qayta bosing.", show_alert=True)
        return
    await call.answer("✅ Rahmat! Obuna tasdiqlandi.", show_alert=False)
    await reward_referral_if_eligible(uid)
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer(
        "🎉 <b>Obuna muvaffaqiyatli tasdiqlandi!</b>\nKafil botining barcha imkoniyatlaridan to'liq foydalanishingiz mumkin.",
        reply_markup=main_menu_keyboard(uid)
    )

# ============================ 8. 1-TUGMA: 📜 TARIX ============================
@router.message(StateFilter("*"), F.text.contains("Tarix") | F.text.contains("𝗧𝗮𝗿𝗶𝘅"))
async def history_handler(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    deals = db.q("SELECT * FROM deals WHERE buyer_id=? OR seller_id=? ORDER BY updated_at DESC LIMIT 10", (uid, uid))
    if not deals:
        await msg.answer(
            "📂 <b>Sizda hali hech qanday savdolar tarixi mavjud emas.</b>\n\n"
            "Yangi xavfsiz bitim boshlash uchun pastdagi <b>«🤝 𝗬𝗮𝗻𝗴𝗶 𝗦𝗮𝘃𝗱𝗼 𝗢𝗰𝗵𝗶𝘀𝗵»</b> tugmasini bosing.",
            reply_markup=main_menu_keyboard(uid)
        )
        return

    text = "📜 <b>SIZNING SAVDOLARINGIZNING TO'LIQ TARIXI:</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    kb = []
    status_uz = {
        "created": "🆕 Yaratildi",
        "waiting_payment": "💳 To'lov kutilmoqda",
        "in_trade": "🔒 Muzlatilgan (Topshirilmoqda)",
        "completed": "✅ Muvaffaqiyatli yakunlandi",
        "dispute": "🚨 Nizoli / Shikoyat holatida",
        "cancelled": "❌ Bekor qilindi",
        "refunded": "↩️ Pul qaytarildi"
    }
    for d in deals:
        role = "🛒 <b>Xaridor</b>" if d["buyer_id"] == uid else "💼 <b>Sotuvchi</b>"
        st = status_uz.get(d["status"], d["status"])
        created_date = (d["created_at"] or "")[:16].replace("T", " ")
        text += (
            f"🔹 <b>Bitim ID:</b> <code>#{d['deal_id']}</code> | <b>{st}</b>\n"
            f"├ 📦 <b>Tovar:</b> <b>{esc_html(d['title'])}</b>\n"
            f"├ 💵 <b>Summa:</b> <b>{fmt(d['amount'])} so'm</b> ({role})\n"
            f"└ 📅 <b>Sana:</b> <b>{created_date}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        row = [
            InlineKeyboardButton(text=f"🔎 𝗕𝗮𝘁𝗮𝗳𝘀𝗶𝗹: #{d['deal_id']}", callback_data=f"deal_detail:{d['deal_id']}")
        ]
        if d["status"] in ("in_trade", "completed", "dispute"):
            row.append(InlineKeyboardButton(text="⚖️ 𝗔𝗿𝗶𝘇𝗮 𝗯𝗲𝗿𝗶𝘀𝗵", callback_data=f"file_comp:{d['deal_id']}"))
        kb.append(row)

    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("deal_detail:"))
async def deal_detail_callback(call: CallbackQuery):
    await call.answer()
    d_id = call.data.split(":")[1]
    deal = db.get_deal(d_id)
    if not deal:
        await call.message.answer("⚠️ Bitim topilmadi.")
        return

    messages = db.get_deal_messages(d_id)
    chat_log = ""
    if messages:
        for m in messages:
            time_part = (m["created_at"] or "")[11:16]
            chat_log += f"[{time_part}] <b>{esc_html(m['sender_tag'])}:</b> {esc_html(m['message_text'])}\n"
    else:
        chat_log = "<i>(Chat yozishmalari mavjud emas)</i>\n"

    created_date = (deal["created_at"] or "")[:19].replace("T", " ")
    updated_date = (deal["updated_at"] or "")[:19].replace("T", " ")

    text = (
        f"📄 <b>SAVDO TAFSILOTLARI VA YOZISHMALAR LOGI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>Bitim ID:</b> <code>#{deal['deal_id']}</code>\n"
        f"📦 <b>Tovar / Xizmat:</b> <b>{esc_html(deal['title'])}</b>\n"
        f"💵 <b>Kelishilgan summa:</b> <b>{fmt(deal['amount'])} so'm</b>\n"
        f"💼 <b>Kafil komissiyasi:</b> <b>{fmt(deal['fee'])} so'm</b>\n"
        f"💰 <b>Jami to'langan depozit:</b> <b>{fmt(deal['total_to_pay'])} so'm</b>\n"
        f"🔒 <b>Holati:</b> <b>{deal['status'].upper()}</b>\n"
        f"📅 <b>Boshlangan vaqti:</b> <b>{created_date}</b>\n"
        f"🔄 <b>Oxirgi yangilanish:</b> <b>{updated_date}</b>\n"
        f"🛒 <b>Xaridor:</b> @{esc_html(deal['buyer_username'] or 'mavjud emas')} (ID: <code>{deal['buyer_id']}</code>)\n"
        f"💼 <b>Sotuvchi:</b> @{esc_html(deal['seller_username'] or 'mavjud emas')} (ID: <code>{deal['seller_id'] or 'nomalum'}</code>)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 <b>CHAT YOZISHMALARI ARXIVI:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{chat_log}"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = [
        [InlineKeyboardButton(text="⚖️ 𝗨𝘀𝗵𝗯𝘂 𝘀𝗮𝘃𝗱𝗼 𝗯𝗼'𝘆𝗶𝗰𝗵𝗮 𝗮𝗿𝗶𝘇𝗮 𝗯𝗲𝗿𝗶𝘀𝗵", callback_data=f"file_comp:{d_id}")]
    ]
    await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("file_comp:"))
async def file_comp_deal_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    d_id = call.data.split(":")[1]
    deal = db.get_deal(d_id)
    if not deal:
        await call.message.answer("Bitim topilmadi.")
        return

    uid = call.from_user.id
    is_buyer = (uid == deal["buyer_id"])
    target_username = deal["seller_username"] if is_buyer else deal["buyer_username"]
    target_id = deal["seller_id"] if is_buyer else deal["buyer_id"]

    await state.set_state(SOSFSM.deal_complaint_reason)
    await state.update_data(comp_deal_id=d_id, comp_target_un=target_username, comp_target_id=target_id)

    await call.message.answer(
        f"🚨 <b>#{d_id} RAQAMLI BITIM BO'YICHA ARIZA QOLDIRISH</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Savdo: <b>{esc_html(deal['title'])}</b>\n"
        f"Summa: <b>{fmt(deal['amount'])} so'm</b>\n"
        f"Ikkinchi tomon: @{esc_html(target_username or 'nomalum')}\n\n"
        f"📝 <b>Ushbu savdo bo'yicha qanday muammo yoki aldov yuz berdi? Batafsil yozib bering:</b>",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ 𝗕𝗲𝗸𝗼𝗿 𝗾𝗶𝗹𝗶𝘀𝗵")]], resize_keyboard=True)
    )

@router.message(SOSFSM.deal_complaint_reason)
async def process_deal_comp_reason(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    # Foydalanuvchi ma'lumotini tekshirish (AI / Validation)
    is_valid, err_msg, clean_reason = validate_user_input("complaint_reason", msg.text)
    if not is_valid:
        await msg.answer(err_msg)
        return

    data = await state.get_data()
    d_id = data["comp_deal_id"]
    target_un = data["comp_target_un"]
    target_id = data["comp_target_id"]
    await state.clear()

    comp_id = db.create_complaint(
        deal_id=d_id,
        complainant_id=msg.from_user.id,
        complainant_username=msg.from_user.username or "",
        accused_id=target_id,
        accused_username=target_un,
        reason=clean_reason
    )
    now_s = datetime.now(TZ).isoformat()
    db.q("UPDATE deals SET status='dispute', updated_at=? WHERE deal_id=?", (now_s, d_id), commit=True)

    deal = db.get_deal(d_id) or {"title": "Savdo", "amount": 0, "buyer_username": msg.from_user.username, "seller_username": target_un}
    ai_solution = ai_dispute_solver(deal, clean_reason)

    await msg.answer(
        f"🚨 <b>ARIZANGIZ QABUL QILINDI! (#{comp_id})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Bitim: <b>#{d_id}</b> nizoli (dispute) holatga o'tkazildi va pul kafil hisobida xavfsiz muzlatildi 🔒.\n\n"
        f"{ai_solution['full_message']}",
        reply_markup=main_menu_keyboard(msg.from_user.id)
    )

    if target_id:
        try:
            await bot.send_message(
                target_id,
                f"⚠️ <b>DIQQAT: SIZNING USTINGIZDAN #{d_id} BITIM BO'YICHA ARIZA TUSHDI! (#{comp_id})</b>\n\n"
                f"{ai_solution['full_message']}"
            )
        except Exception:
            pass

    if deal.get("group_chat_id"):
        try:
            await bot.send_message(
                deal["group_chat_id"],
                f"🚨 <b>GURUH SAVDOSI BO'YICHA ARIZA QABUL QILINDI! (#{comp_id})</b>\n\n"
                f"{ai_solution['full_message']}"
            )
        except Exception:
            pass

    for a_id in ADMIN_IDS:
        try:
            await bot.send_message(
                a_id,
                f"🚨 <b>YANGI ARIZA (Bitim #{d_id}): #{comp_id}</b>\n"
                f"Kimdan: @{esc_html(msg.from_user.username or '')} (ID: <code>{msg.from_user.id}</code>)\n"
                f"Kimga: @{esc_html(target_un)} (ID: <code>{target_id or 'nomalum'}</code>)\n\n"
                f"{ai_solution['full_message']}"
            )
        except Exception:
            pass

# ============================ 9. 2-TUGMA: 🆘 SOS (YORDAM) ============================
@router.message(StateFilter("*"), F.text.contains("SOS") | F.text.contains("𝗦𝗢𝗦") | F.text.contains("Yordam") | F.text.contains("𝗬𝗼𝗿𝗱𝗮𝗺"))
async def sos_menu_handler(msg: Message, state: FSMContext):
    await state.clear()
    text = (
        "🆘 <b>XAVFSIZLIK VA TEZKOR YORDAM MARKAZI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Assalomu alaykum! Sizga yordam berishdan mamnunmiz 🛡.\n\n"
        "<b>Biz qanday xizmatlarni taqdim etamiz?</b>\n"
        "├ 🤖 <b>Gemini 2.5 Flash AI:</b> <b>24/7 rejimida har qanday savolingizga javob beradi</b>\n"
        "├ 🚨 <b>Firibgarlik arizasi:</b> <b>Aldov holatlarida tekshiruv boshlaydi</b>\n"
        "└ 👨‍💼 <b>Garant Admin:</b> <b>Insoniy professional yordam ko'rsatadi</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>Quyidagi xizmat turidan birini tanlang:</b>"
    )
    kb = [
        [InlineKeyboardButton(text="💬 𝗚𝗲𝗺𝗶𝗻𝗶 𝗔𝗜 𝗯𝗶𝗹𝗮𝗻 𝘀𝗮𝘃𝗼𝗹-𝗷𝗮𝘃𝗼𝗯", callback_data="sos_ask_ai")],
        [InlineKeyboardButton(text="🚨 𝗠𝗲𝗻𝗶 𝘀𝗼𝘁𝘂𝘃𝗰𝗵𝗶 𝗮𝗹𝗱𝗮𝗱𝗶 (𝗔𝗿𝗶𝘇𝗮)", callback_data="sos_file_complaint")],
        [InlineKeyboardButton(text="📞 𝗥𝗮𝘀𝗺𝗶𝘆 𝗔𝗱𝗺𝗶𝗻 𝗯𝗶𝗹𝗮𝗻 𝗯𝗼𝗴'𝗹𝗮𝗻𝗶𝘀𝗵", callback_data="sos_contact_admin")]
    ]
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "sos_ask_ai")
async def sos_ask_ai_click(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(SOSFSM.ai_query)
    await call.message.answer(
        "🤖 <b>GEMINI AI ASSISTENTI:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Qanday yordam bera olaman? Muammoingizni yoki savolingizni batafsil yozib qoldiring:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ 𝗕𝗲𝗸𝗼𝗿 𝗾𝗶𝗹𝗶𝘀𝗵")]], resize_keyboard=True)
    )

@router.message(SOSFSM.ai_query)
async def process_ai_query(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("❌ <b>Amaliyot bekor qilindi.</b>", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    user_query = msg.text.strip()
    wait_msg = await msg.answer("🤖 <i>Kafil AI assistenti javob tayyorlamoqda...</i>")

    ai_resp = await ask_gemini_ai(user_query)
    try:
        await wait_msg.delete()
    except Exception:
        pass

    ticket_id = db.q(
        "INSERT INTO ai_tickets (user_id, user_name, question, status, created_at) VALUES (?, ?, ?, 'open', ?)",
        (msg.from_user.id, msg.from_user.full_name or "Foydalanuvchi", user_query, datetime.now(TZ).isoformat()),
        commit=True
    )

    # Agar AI javobida ADMIN_GA_UZATISH bo'lsa (foydalanuvchi qat'iy adminga yozishni so'raganida)
    if "ADMIN_GA_UZATISH" in ai_resp:
        clean_resp = ai_resp.replace("ADMIN_GA_UZATISH", "").strip()
        await msg.answer(
            f"{clean_resp}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <i>Murojaatingiz administratorga yetkazildi. Admin javob berishi bilan bot sizga xabar yo'llaydi.</i>",
            reply_markup=main_menu_keyboard(msg.from_user.id)
        )
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ 𝗝𝗮𝘃𝗼𝗯 𝗯𝗲𝗿𝗶𝘀𝗵", callback_data=f"adm_reply_ticket:{ticket_id}")]
        ])
        for a_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    a_id,
                    f"🆘 <b>YANGI MUROJAAT (Adminga uzatildi): #{ticket_id}</b>\n"
                    f"Foydalanuvchi: <b>{esc_html(msg.from_user.full_name)}</b> (ID: <code>{msg.from_user.id}</code>)\n"
                    f"Murojaat:\n<i>{esc_html(user_query)}</i>",
                    reply_markup=admin_kb
                )
            except Exception:
                pass
        await state.clear()
        return

    # Normal javob bo'lsa: Foydalanuvchiga to'liq va chiroyli javob beriladi
    kb = [
        [InlineKeyboardButton(text="👨‍💼 𝗔𝗱𝗺𝗶𝗻𝗴𝗮 𝘆𝘂𝗯𝗼𝗿𝗶𝘀𝗵", callback_data=f"fwd_admin:{ticket_id}")],
        [InlineKeyboardButton(text="✅ 𝗧𝘂𝘀𝗵𝘂𝗻𝗮𝗿𝗹𝗶, 𝗿𝗮𝗵𝗺𝗮𝘁", callback_data="ai_resolved")]
    ]
    await msg.answer(
        f"{ai_resp}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Agar javob sizni qoniqtirmasa yoki qo'shimcha yordam kerak bo'lsa, quyidagi tugma orqali adminga yo'llashingiz mumkin.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await state.clear()

@router.callback_query(F.data == "ai_resolved")
async def ai_resolved_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Yordam berganimizdan xursandmiz!")
    await call.message.edit_text("✅ <b>Murojaatingiz yakunlandi.</b>\nYana qandaydir savollaringiz bo'lsa bemalol murojaat qiling!")

@router.callback_query(F.data.startswith("fwd_admin:"))
async def fwd_admin_cb(call: CallbackQuery, state: FSMContext):
    await call.answer("Murojaat adminga yuborildi.")
    t_id = int(call.data.split(":")[1])
    ticket = db.q("SELECT * FROM ai_tickets WHERE id=?", (t_id,), one=True)
    if not ticket:
        await call.message.answer("Murojaat topilmadi.")
        return

    await call.message.edit_text(
        "✅ <b>Murojaatingiz administratorga yetkazildi!</b>\n"
        "Admin javob yozishi bilan bot sizga xabarni darhol yetkazadi. Iltimos, kuting."
    )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Javob yozish", callback_data=f"adm_reply_ticket:{t_id}")]
    ])
    for a_id in ADMIN_IDS:
        try:
            await bot.send_message(
                a_id,
                f"🆘 <b>YORDAM SO'ROVI: #{t_id}</b>\n"
                f"Foydalanuvchi: <b>{esc_html(ticket['user_name'])}</b> (ID: <code>{ticket['user_id']}</code>)\n"
                f"Savol / Muammo:\n<i>{esc_html(ticket['question'])}</i>",
                reply_markup=admin_kb
            )
        except Exception:
            pass

@router.callback_query(F.data == "sos_contact_admin")
async def sos_contact_admin_cb(call: CallbackQuery):
    await call.answer()
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else 6125160021
    await call.message.answer(
        f"📞 <b>ADMIN BILAN TO'G'RIDAN-TO'G'RI BOG'LANISH:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Garant admin bilan to'g'ridan-to'g'ri bog'lanish uchun havola:\n"
        f"👉 <a href=\"tg://user?id={admin_id}\">Rasmiy Garant Admin</a>",
        disable_web_page_preview=True
    )

# SOS orqali "Meni sotuvchi aldadi" arizasi
@router.callback_query(F.data == "sos_file_complaint")
async def sos_complaint_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(SOSFSM.complaint_seller)
    await call.message.answer(
        "🚨 <b>FIRIBGARLIK BO'YICHA ARIZA QOLDIRISH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Sizni qaysi sotuvchi aldadi?\n\n"
        "👉 <b>O'sha sotuvchining @username'ini kiriting:</b>\n"
        "<i>(Masalan: @sotuvchi_username)</i>",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ 𝗕𝗲𝗸𝗼𝗿 𝗾𝗶𝗹𝗶𝘀𝗵")]], resize_keyboard=True)
    )

@router.message(SOSFSM.complaint_seller)
async def process_comp_seller(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    is_valid, err_msg, clean_seller = validate_user_input(
        "seller_username",
        msg.text,
        {"self_username": msg.from_user.username, "self_id": msg.from_user.id}
    )
    if not is_valid:
        await msg.answer(err_msg)
        return

    await state.update_data(target_seller=clean_seller)
    await state.set_state(SOSFSM.complaint_reason)
    await msg.answer(
        f"Sotuvchi: <b>@{esc_html(clean_seller)}</b>\n\n"
        f"📝 <b>Endi qanday aldov sodir bo'lganini batafsil yozib bering:</b>\n"
        f"<i>(Masalan: Pul to'langanidan so'ng akkaunt parolini bermadi yoki noto'g'ri berdi)</i>"
    )

@router.message(SOSFSM.complaint_reason)
async def process_comp_reason(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    is_valid, err_msg, clean_reason = validate_user_input("complaint_reason", msg.text)
    if not is_valid:
        await msg.answer(err_msg)
        return

    data = await state.get_data()
    seller_tag = data["target_seller"]
    await state.clear()

    seller_u = db.get_user_by_username(seller_tag)
    accused_id = seller_u["user_id"] if seller_u else None

    deadline = (datetime.now(TZ) + timedelta(days=5)).isoformat()
    comp_id = db.create_complaint(
        complainant_id=msg.from_user.id,
        complainant_username=msg.from_user.username or "",
        accused_username=seller_tag,
        accused_id=accused_id,
        reason=clean_reason,
        deal_id=""
    )
    db.q("UPDATE complaints SET deadline_at=? WHERE id=?", (deadline, comp_id), commit=True)

    fake_deal = {"title": "Xarid", "amount": 0, "buyer_username": msg.from_user.username, "seller_username": seller_tag}
    ai_solution = ai_dispute_solver(fake_deal, clean_reason)

    await msg.answer(
        f"🚨 <b>ARIZANGIZ QABUL QILINDI! (#{comp_id})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Sotuvchi <b>@{esc_html(seller_tag)}</b> ga rasmiy ariza xabarnomasi yuborildi.\n"
        f"Unga masalani hal qilish uchun <b>5 kun</b> muhlat berildi.\n\n"
        f"{ai_solution['full_message']}",
        reply_markup=main_menu_keyboard(msg.from_user.id)
    )

    if accused_id:
        try:
            await bot.send_message(
                accused_id,
                f"⚠️ <b>DIQQAT: SIZNING USTINGIZDAN ARIZA TUSHDI! (#{comp_id})</b>\n\n"
                f"{ai_solution['full_message']}"
            )
        except Exception:
            pass

    for a_id in ADMIN_IDS:
        try:
            await bot.send_message(
                a_id,
                f"🚨 <b>YANGI ARIZA (Umumiy): #{comp_id}</b>\n"
                f"Kimdan: @{esc_html(msg.from_user.username or '')} (ID: <code>{msg.from_user.id}</code>)\n"
                f"Kimga: @{esc_html(seller_tag)} (ID: <code>{accused_id or 'nomalum'}</code>)\n\n"
                f"{ai_solution['full_message']}"
            )
        except Exception:
            pass
        try:
            await bot.send_message(
                a_id,
                f"🚨 <b>YANGI ARIZA: #{comp_id}</b>\n"
                f"Kimdan: @{esc_html(msg.from_user.username or '')} (ID: <code>{msg.from_user.id}</code>)\n"
                f"Kimga: @{esc_html(seller_tag)} (ID: <code>{accused_id or 'nomalum'}</code>)\n"
                f"Sabab: <i>{esc_html(reason)}</i>"
            )
        except Exception:
            pass

# ============================ 10. 3-TUGMA: 👥 REFERAL TIZIMI ============================
@router.message(StateFilter("*"), F.text.contains("Referal") | F.text.contains("𝗥𝗲𝗳𝗲𝗿𝗮𝗹"))
async def referral_menu_handler(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    u = db.get_user(uid)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{uid}"

    today_prefix = datetime.now(TZ).strftime("%Y-%m-%d")
    today_count = db.q("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND status='rewarded' AND created_at LIKE ?",
                       (uid, f"{today_prefix}%"), one=True)["c"]
    total_refs = db.q("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND status='rewarded'", (uid,), one=True)["c"]

    ref_bal = u["referral_balance"] if u else 0
    text = (
        f"👥 <b>DO'STLARNI TAKLIF QILISH VA PUL ISHLASH TIZIMI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Sizning referal balansingiz:</b> <b>{fmt(ref_bal)} so'm</b>\n"
        f"👥 <b>Jami taklif qilingan do'stlar:</b> <b>{total_refs} ta</b>\n"
        f"📊 <b>Bugun tasdiqlanganlar:</b> <b>{today_count}/20 ta</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>REFERAL DASTURI SHARTLARI:</b>\n"
        f"├ ➕ <b>Mukofot:</b> Har bir qo'shilgan do'stingiz uchun <b>+100 so'm</b>\n"
        f"├ 📢 <b>Shart:</b> Do'stingiz botga kirib kanallarga a'zo bo'lishi shart\n"
        f"├ ⏳ <b>Kunlik chegara:</b> Kuniga ko'pi bilan <b>20 ta</b> do'st\n"
        f"├ ⚠️ <b>Jarima:</b> Do'stingiz kanaldan chiqib ketsa: <b>-200 so'm</b>\n"
        f"└ 💳 <b>Minimal yechish summasi:</b> <b>10 000 so'm</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <b>SIZNING SHAXSIY TAKLIF HAVOLANGIZ:</b>\n"
        f"👉 <code>{ref_link}</code>\n"
        f"<i>(Havola ustiga bosing — avtomatik nusxalanadi!)</i>"
    )
    kb = [
        [InlineKeyboardButton(text="💸 𝗣𝘂𝗹𝗻𝗶 𝘆𝗲𝗰𝗵𝗶𝗯 𝗼𝗹𝗶𝘀𝗵 (𝗞𝗮𝗿𝘁𝗮𝗴𝗮)", callback_data="ref_withdraw")],
        [InlineKeyboardButton(text="📊 𝗠𝗲𝗻𝗶𝗻𝗴 𝗿𝗲𝗳𝗲𝗿𝗮𝗹𝗹𝗮𝗿𝗶𝗺 𝗿𝗼'𝘆𝘅𝗮𝘁𝗶", callback_data="ref_stats")]
    ]
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "ref_stats")
async def ref_stats_callback(call: CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    refs = db.q("SELECT * FROM referrals WHERE referrer_id=? ORDER BY created_at DESC LIMIT 10", (uid,))
    if not refs:
        await call.message.answer("Sizda hali referallar mavjud emas.")
        return
    text = "👥 <b>OXIRGI TAKLIF QILINGANLAR:</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for r in refs:
        date_s = (r["created_at"] or "")[:16].replace("T", " ")
        st = "✅ Mukofot berilgan" if r["status"] == "rewarded" else ("⚠️ Jarima" if r["status"] == "penalized" else "⏳ Kutilmoqda")
        text += f"👤 ID: <code>{r['referred_id']}</code> | {st} ({date_s})\n"
    await call.message.answer(text)

@router.callback_query(F.data == "ref_withdraw")
async def ref_withdraw_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    uid = call.from_user.id
    u = db.get_user(uid)
    bal = u["referral_balance"] if u else 0

    if bal < 10000:
        await call.message.answer(
            f"⚠️ <b>MABLAG' YETARLI EMAS!</b>\n\n"
            f"Minimal yechish summasi: <b>10 000 so'm</b>.\n"
            f"Sizning balansingiz: <b>{fmt(bal)} so'm</b>.\n\n"
            f"Mablag'ni yechish uchun do'stlaringizni taklif qilib balansingizni 10 000 so'mga yetkazing."
        )
        return

    await state.set_state(WithdrawFSM.card_number)
    await state.update_data(withdraw_amount=bal)
    await call.message.answer(
        f"💸 <b>PULNI YECHIB OLISH: {fmt(bal)} so'm</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"To'lovni qabul qiluvchi <b>16 xonali plastik karta raqamini</b> kiriting:\n"
        f"<i>(Masalan: 8600 0000 0000 0000 yoki 9860 ...)</i>",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ 𝗕𝗲𝗸𝗼𝗿 𝗾𝗶𝗹𝗶𝘀𝗵")]], resize_keyboard=True)
    )

@router.message(WithdrawFSM.card_number)
async def withdraw_proc_card(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    is_valid, err_msg, clean_card = validate_user_input("card_number", msg.text)
    if not is_valid:
        await msg.answer(err_msg)
        return

    await state.update_data(card_number=clean_card)
    await state.set_state(WithdrawFSM.card_owner)
    await msg.answer(
        f"Karta: <code>{format_card(clean_card)}</code>\n\n"
        f"👤 <b>Karta egasining Ism-Familiyasini kiriting:</b>\n"
        f"<i>(Masalan: ALIYEV VALI)</i>"
    )

@router.message(WithdrawFSM.card_owner)
async def withdraw_proc_owner(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    is_valid, err_msg, clean_owner = validate_user_input("card_owner", msg.text)
    if not is_valid:
        await msg.answer(err_msg)
        return

    owner = clean_owner
    data = await state.get_data()
    amount = data["withdraw_amount"]
    card = data["card_number"]
    uid = msg.from_user.id
    await state.clear()

    u = db.get_user(uid)
    if not u or u["referral_balance"] < amount:
        await msg.answer("Xatolik: Balans yetarli emas.", reply_markup=main_menu_keyboard(uid))
        return

    db.q("UPDATE users SET referral_balance=referral_balance-? WHERE user_id=?", (amount, uid), commit=True)
    w_id = db.create_withdrawal(uid, amount, card, owner)

    await msg.answer(
        f"✅ <b>SO'ROV ADMINGA YUBORILDI!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"So'rov raqami: <b>#{w_id}</b>\n"
        f"Yechilayotgan summa: <b>{fmt(amount)} so'm</b>\n"
        f"Karta: <code>{format_card(card)}</code> ({owner})\n\n"
        f"Admin tez orada pulingizni tashlab beradi. Rahmat!",
        reply_markup=main_menu_keyboard(uid)
    )

    for a_id in ADMIN_IDS:
        try:
            adm_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ To'landi deb belgilash", callback_data=f"adm_pay_w:{w_id}")]
            ])
            await bot.send_message(
                a_id,
                f"💸 <b>YANGI PUL YECHISH SO'ROVI: #{w_id}</b>\n"
                f"Foydalanuvchi: ID <code>{uid}</code> (@{esc_html(msg.from_user.username or '')})\n"
                f"Summa: <b>{fmt(amount)} so'm</b>\n"
                f"Karta: <code>{format_card(card)}</code> ({owner})",
                reply_markup=adm_kb
            )
        except Exception:
            pass

# ============================ 11. 4-TUGMA: 🤝 YANGI SAVDO OCHISH ============================
@router.message(StateFilter("*"), F.text.contains("Yangi Savdo") | F.text.contains("𝗬𝗮𝗻𝗴𝗶 𝗦𝗮𝘃𝗱𝗼") | F.text.contains("Savdo") | F.text.contains("𝗦𝗮𝘃𝗱𝗼"))
async def create_deal_menu_handler(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id

    # Faol to'lov kutilayotgan bitim bor-yo'qligini tekshirish
    active = db.q("SELECT * FROM deals WHERE buyer_id=? AND status='waiting_payment'", (uid,), one=True)
    if active:
        await msg.answer(
            f"⚠️ <b>Sizda to'lov kutilayotgan bitim mavjud:</b> <code>#{active['deal_id']}</code>.\n\n"
            f"Avval ushbu bitimni yakunlang yoki bekor qiling:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ 𝗨𝘀𝗵𝗯𝘂 𝗯𝗶𝘁𝗶𝗺𝗻𝗶 𝗯𝗲𝗸𝗼𝗿 𝗾𝗶𝗹𝗶𝘀𝗵", callback_data=f"cancel_deal:{active['deal_id']}")]
            ])
        )
        return

    text = (
        "🤝 <b>YANGI KAFOLATLI SAVDO BOSHQARUVI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Savdo jarayonini qaysi xavfsiz rejimda o'tkazmoqchisiz?\n\n"
        "1️⃣ <b>👥 AI YORDAMIDA GURUHDA:</b>\n"
        "├ Bot va <b>Gemini 2.5 Flash AI</b> qorovuli guruhingizga qo'shiladi;\n"
        "├ Guruhdagi barcha kelishuvlar, xabarlar va arizalar avtomatik nazorat qilinadi;\n"
        "└ Nojo'ya so'kinish va firibgarlik harakatlari zudlik bilan bartaraf etiladi.\n\n"
        "2️⃣ <b>🤖 BOTNING O'ZIDA (MAXFIY CHAT):</b>\n"
        "├ Savdo to'g'ridan-to'g'ri bot ichidagi xavfsiz shifrlangan chatda o'tkaziladi;\n"
        "└ Xaridor va sotuvchi to'liq anonim va kafolatlangan holda muloqot qiladi.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>O'zingizga qulay rejimni tanlang:</b>"
    )
    kb = [
        [InlineKeyboardButton(text="👥 𝗔𝗜 𝘆𝗼𝗿𝗱𝗮𝗺𝗶𝗱𝗮 𝗴𝘂𝗿𝘂𝗵𝗱𝗮", callback_data="deal_mode_group")],
        [InlineKeyboardButton(text="🤖 𝗕𝗼𝘁𝗻𝗶𝗻𝗴 𝗼'𝘇𝗶𝗱𝗮 (𝗠𝗮𝘅𝗳𝗶𝘆 𝗰𝗵𝗮𝘁)", callback_data="deal_mode_bot")]
    ]
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.in_(["deal_mode_group", "deal_mode_bot"]))
async def select_deal_mode_cb(call: CallbackQuery, state: FSMContext):
    await call.answer()
    mode = "group" if call.data == "deal_mode_group" else "bot"
    await state.set_state(CreateDealFSM.title)
    await state.update_data(trade_mode=mode)
    await call.message.answer(
        "📦 <b>Mahsulot yoki xizmat nomini kiriting:</b>\n"
        "<i>(Masalan: Telegram Kanal, PUBG Akkaunt, Python bot kodi yoki dizayn xizmati)</i>",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ 𝗕𝗲𝗸𝗼𝗿 𝗾𝗶𝗹𝗶𝘀𝗵")]], resize_keyboard=True)
    )

@router.message(CreateDealFSM.title)
async def process_deal_title(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    is_valid, err_msg, clean_title = validate_user_input("deal_title", msg.text)
    if not is_valid:
        await msg.answer(err_msg)
        return

    await state.update_data(deal_title=clean_title)
    await state.set_state(CreateDealFSM.seller)
    await msg.answer(
        f"Mahsulot: <b>{esc_html(clean_title)}</b>\n\n"
        f"🛒 <b>Siz xaridorsiz! Sotuvchining @username yoki Telegram ID raqamini kiriting:</b>\n"
        f"<i>(Masalan: @sotuvchi_user yoki 6125160021)</i>"
    )

@router.message(CreateDealFSM.seller)
async def process_deal_seller(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    is_valid, err_msg, clean_seller = validate_user_input(
        "seller_username",
        msg.text,
        {"self_username": msg.from_user.username, "self_id": msg.from_user.id}
    )
    if not is_valid:
        await msg.answer(err_msg)
        return

    # Qora ro'yxatdan tekshirish
    banned, reason = db.is_blacklisted(username=clean_seller)
    if banned:
        await msg.answer(
            f"🚫 <b>DIQQAT: @{esc_html(clean_seller)} foydalanuvchisi QORA RO'YXATDA!</b>\n\n"
            f"Sababi: <i>{esc_html(reason)}</i>\n"
            f"Ushbu sotuvchi bilan savdo qilish xavfli va qat'iyan taqiqlanadi!",
            reply_markup=main_menu_keyboard(msg.from_user.id)
        )
        await state.clear()
        return

    await state.update_data(seller_username=clean_seller)
    await state.set_state(CreateDealFSM.amount)

    comm_pct = int(db.get_setting("commission_percent", 10))
    await msg.answer(
        f"Sotuvchi: <b>@{esc_html(clean_seller)}</b> (Xavfsiz ✅)\n\n"
        f"💵 <b>To'lov summasini kiriting (so'mda):</b>\n"
        f"<i>(Eng kam summa: 1 000 so'm, eng ko'p summa: 5 000 000 so'm)</i>\n\n"
        f"ℹ️ Xizmat komissiyasi: {comm_pct}%"
    )

@router.message(CreateDealFSM.amount)
async def process_deal_amount(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    is_valid, err_msg, clean_amount = validate_user_input(
        "deal_amount",
        msg.text,
        {"min_limit": 1000, "max_limit": 5000000000}
    )
    if not is_valid:
        await msg.answer(err_msg)
        return

    amount = clean_amount

    data = await state.get_data()
    title = data["deal_title"]
    seller_tag = data["seller_username"]
    mode = data["trade_mode"]
    uid = msg.from_user.id

    comm_pct = int(db.get_setting("commission_percent", 10))
    fee = int(amount * comm_pct / 100)
    total_to_pay = amount + fee

    deal_id = generate_deal_id()
    pay_res = await create_payment_async(total_to_pay, order_id=deal_id, user_id=uid)
    if not pay_res or not pay_res.get("payment_id"):
        await asyncio.sleep(1)
        pay_res = await create_payment_async(total_to_pay, order_id=deal_id, user_id=uid)

    exact_amount = total_to_pay
    payment_id = None
    if pay_res and pay_res.get("payment_id"):
        payment_id = pay_res.get("payment_id")
        exact_amount = int(pay_res.get("amount", total_to_pay))
    else:
        await msg.answer(
            "⚠️ <b>To'lov tizimida vaqtinchalik uzilish yuz berdi.</b>\n"
            "Iltimos, 1 daqiqadan so'ng qayta urinib ko'ring yoki administrator bilan bog'laning.",
            reply_markup=main_menu_keyboard(uid)
        )
        await state.clear()
        return

    db.create_deal(
        deal_id=deal_id,
        buyer_id=uid,
        buyer_username=msg.from_user.username or "",
        seller_username=seller_tag,
        title=title,
        amount=amount,
        fee=fee,
        total_to_pay=exact_amount,
        mode=mode,
        payment_id=payment_id
    )

    await state.clear()
    asyncio.create_task(deal_auto_payment_watcher(deal_id))

    card_num = format_card((pay_res and pay_res.get("card_number")) or db.get_setting("card_number", DEFAULT_CARD_NUMBER))
    card_own = db.get_setting("card_owner", DEFAULT_CARD_OWNER)
    fee_label = f"{fmt(fee)} so'm ({comm_pct}%)"

    payment_card = (
        f"💳 <b>KAFOLATLI TO'LOV HISOBI OCHILDI! (#{deal_id})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Mahsulot:</b> <b>{esc_html(title)}</b>\n"
        f"👤 <b>Sotuvchi:</b> @{esc_html(seller_tag)}\n"
        f"💵 <b>Kelishilgan summa:</b> <b>{fmt(amount)} so'm</b>\n"
        f"💼 <b>Komissiya ({comm_pct}%):</b> <b>{fee_label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>TO'LOV KARTASI:</b>\n"
        f"👉 <code>{card_num}</code>\n"
        f"<i>(Karta ustiga bosing — avtomatik nusxalanadi!)</i>\n"
        f"👤 <b>Qabul qiluvchi:</b> <b>{esc_html(card_own)}</b>\n\n"
        f"💰 <b>O'TKAZILADIGAN ANIQ SUMMA (Komissiya bilan):</b>\n"
        f"👉 <code>{fmt(exact_amount)} so'm</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <b>DIQQAT! JUDA MUHIM:</b>\n"
        f"Aynan <b>{fmt(exact_amount)} so'm</b>ni aniq o'tkazing! Kam yoki ortiq to'lasangiz, tizim to'lovni avtomatik tasdiqlay olmaydi.\n\n"
        f"⚡️ <i>To'lov o'tkazilishi bilan bot avtomatik aniqlaydi. Agar o'tmasa, quyidagi «✅ To'lov qildim» tugmasini bosing (bot 20 soniya davomida faol tekshiradi).</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = []
    if pay_res and pay_res.get("payment_url"):
        kb.append([InlineKeyboardButton(text="💳 Havola orqali to'lash", url=pay_res["payment_url"])])
    kb.append([InlineKeyboardButton(text="✅ To'lov qildim (Tekshirish)", callback_data=f"i_paid:{deal_id}")])
    kb.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"cancel_deal:{deal_id}")])

    await msg.answer(payment_card, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("i_paid:"))
async def i_paid_callback_step(call: CallbackQuery, state: FSMContext):
    d_id = call.data.split(":")[1]
    deal = db.get_deal(d_id)
    if not deal:
        await call.answer("⚠️ Bitim topilmadi.", show_alert=True)
        return

    # Agar allaqachon to'langan bo'lsa
    if deal["status"] in ("in_trade", "goods_delivered", "completed", "buyer_statement", "seller_statement", "buyer_confirmed"):
        await call.answer("✅ Ushbu bitim uchun to'lov allaqachon qabul qilingan!", show_alert=True)
        return

    if deal["status"] != "waiting_payment":
        await call.answer("⚠️ Bitim muddati tugagan yoki bekor qilingan.", show_alert=True)
        return

    payment_id = deal.get("payment_id")
    if not payment_id:
        await call.answer("⚠️ To'lov hisob raqami (payment_id) topilmadi.", show_alert=True)
        return

    if d_id in _active_checking_deals:
        await call.answer("⏳ To'lov tekshirilmoqda, 20 soniya kuting...", show_alert=True)
        return

    _active_checking_deals.add(d_id)
    try:
        # 1. Tezkor birinchi tekshiruv (0-soniya)
        chk = await check_payment_status_async(payment_id)
        if chk and chk.get("paid"):
            paid_amt = int(chk.get("amount") or chk.get("raw", {}).get("amount", 0) or deal["total_to_pay"])
            if paid_amt >= int(deal["total_to_pay"]):
                await call.answer("✅ To'lov muvaffaqiyatli qabul qilindi!", show_alert=True)
                await activate_paid_deal(d_id, edit_call=call, state=state)
                return

        # 2. Agar hali tushmagan bo'lsa: 20 soniyalik monitoring
        await call.answer("🔍 To'lov tekshirilmoqda (20 soniya davomida)...", show_alert=False)

        status_msg = await call.message.answer(
            f"⏳ <b>TO'LOV TIZIMI TEKSHIRILMOQDA... (#{d_id})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Kartangizga <b>{fmt(deal['total_to_pay'])} so'm</b> tushgani qidirilmoqda...\n"
            f"<i>Bank va SMS xabarlari yetib kelishi uchun 20 soniya davomida har 2 soniyada uzluksiz tekshiriladi. Iltimos, kuting...</i>"
        )

        start_time = time.time()
        is_success = False

        while (time.time() - start_time) < 20:
            await asyncio.sleep(2)
            curr = db.get_deal(d_id)
            if curr and curr["status"] != "waiting_payment":
                is_success = True
                break

            chk = await check_payment_status_async(payment_id)
            if chk and chk.get("paid"):
                paid_amt = int(chk.get("amount") or chk.get("raw", {}).get("amount", 0) or deal["total_to_pay"])
                if paid_amt >= int(deal["total_to_pay"]):
                    is_success = True
                    break

        try:
            await status_msg.delete()
        except Exception:
            pass

        if is_success:
            await activate_paid_deal(d_id, edit_call=call, state=state)
            try:
                await call.message.answer(
                    f"🎉 <b>TO'LOVINGIZ QABUL QILINDI! (#{d_id})</b>\n\n"
                    f"To'lov tizimi muvaffaqiyatli tasdiqladi. Mablag' kafil hisobida xavfsiz muzlatildi 🔒."
                )
            except Exception:
                pass
        else:
            api_status = chk.get("status") if chk else "javob berilmadi"
            exp_in = chk.get("raw", {}).get("expires_in") if chk else None
            time_txt = f"{exp_in} soniya qoldi" if exp_in and exp_in > 0 else "muddati tugagan (5 daqiqa o'tib ketgan)"
            card_show = format_card(db.get_setting("card_number", DEFAULT_CARD_NUMBER))
            await call.message.answer(
                f"❌ <b>20 SONIYA DAVOMIDA TO'LOV ANIQLANMADI! (#{d_id})</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>TolovAPI tizimidan olingan ma'lumot:</b>\n"
                f"• <b>Tizim holati:</b> <code>{api_status}</code>\n"
                f"• <b>Kutilayotgan summa:</b> <b>{fmt(deal['total_to_pay'])} so'm</b>\n"
                f"• <b>Hisob muddati:</b> <i>{time_txt}</i>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📌 <b>Nega to'lov tasdiqlanmayapti?</b>\n"
                f"1️⃣ <b>Summa farqi:</b> Kartaga aynan <b>{fmt(deal['total_to_pay'])} so'm</b> o'tkazildimi? (Agar komissiyasiz yoki boshqa summa o'tkazilsa, TolovAPI hisobni bog'lay olmaydi).\n"
                f"2️⃣ <b>5 daqiqalik vaqt:</b> Agar hisob ochilganiga 5 daqiqadan oshgan bo'lsa, TolovAPI hisobni avtomatik bekor qiladi.\n"
                f"3️⃣ <b>Karta:</b> Aynan ko'rsatilgan <code>{card_show}</code> kartasiga o'tkazildimi?\n\n"
                f"<i>💡 Agar pul kartadan yechilgan bo'lsa, birozdan so'ng «🔄 Qayta tekshirish» tugmasini bosing yoki yangi savdo oching.</i>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Qayta tekshirish (20 soniya)", callback_data=f"i_paid:{d_id}")],
                    [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"cancel_deal:{d_id}")]
                ])
            )
    finally:
        _active_checking_deals.discard(d_id)

@router.message(CreateDealFSM.group_link)
async def process_deal_group_link(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    link = msg.text.strip()
    data = await state.get_data()
    deal_id = data["active_deal_id"]
    await state.clear()

    group_code = generate_group_code()
    db.q("UPDATE deals SET group_code=? WHERE deal_id=?", (group_code, deal_id), commit=True)

    await msg.answer(
        f"✅ <b>GURUH SAVDOSI BO'YICHA BIR MARTALIK KODINGIZ TAYYOR:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"1️⃣ Botni o'sha guruhga admin qilib qo'shing.\n"
        f"2️⃣ Guruh chatiga quyidagi maxsus kodni yuboring:\n\n"
        f"👉 <code>{group_code}</code>\n"
        f"<i>(Kod ustiga bosing, avtomatik nusxalanadi!)</i>\n\n"
        f"Kodni yuborganingizdan so'ng, Kafil boti va Gemini AI qorovuli guruhda savdoni nazorat qilishni boshlaydi!",
        reply_markup=main_menu_keyboard(msg.from_user.id)
    )

# ============================ 12. GURUHDA SAVDO VA AI QOROVUL ============================
@router.message(F.chat.type.in_(["group", "supergroup"]))
async def group_trade_moderator(msg: Message):
    chat_id = msg.chat.id
    text = (msg.text or "").strip()

    # Bir martalik kod orqali faollashtirish
    if text.startswith("KAFIL-"):
        code = text.split()[0].upper()
        deal = db.q("SELECT * FROM deals WHERE group_code=? AND status='in_trade'", (code,), one=True)
        if not deal:
            return

        try:
            bot_member = await bot.get_chat_member(chat_id=chat_id, user_id=bot.id)
            if bot_member.status not in ("administrator", "creator"):
                await msg.answer("⚠️ <b>Siz meni ushbu guruhda admin qilmadingiz!</b>\nIltimos, avval menga adminlik huquqini bering.")
                return
        except Exception:
            await msg.answer("⚠️ Bot admin ekanligini tekshirib bo'lmadi.")
            return

        db.q("UPDATE deals SET group_chat_id=?, group_code=NULL WHERE deal_id=?", (chat_id, deal["deal_id"]), commit=True)
        await msg.answer(
            f"🛡 <b>KAFIL BOTI VA GEMINI AI QOROVULI GURUHGA MUVOFIQ ULANDI! (#{deal['deal_id']})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Tovar:</b> <b>{esc_html(deal['title'])}</b>\n"
            f"🛒 <b>Xaridor:</b> @{esc_html(deal['buyer_username'])}\n"
            f"💼 <b>Sotuvchi:</b> @{esc_html(deal['seller_username'])}\n"
            f"💰 <b>Kafolatlangan summa:</b> <b>{fmt(deal['amount'])} so'm</b> (Muzlatilgan 🔒)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Savdo boshlaymizmi? Ikkalangiz ham <b>«ha»</b> yoki <b>«tayyorman»</b> deb yozingchi, ikkalangiz ham shu yerdamisiz?"
        )
        return

    # Guruhdagi faol savdoni topish
    deal = db.q("SELECT * FROM deals WHERE group_chat_id=? AND status IN ('in_trade', 'goods_delivered', 'buyer_statement', 'seller_statement', 'buyer_confirmed')", (chat_id,), one=True)
    if not deal:
        return

    # Gemini AI xavfsizlik nazorati (Real-vaqtda chat guardian)
    sender_tag = f"@{msg.from_user.username}" if msg.from_user.username else (msg.from_user.full_name or f"ID {msg.from_user.id}")
    is_alert, alert_type, alert_msg = await ai_chat_guardian(text, {"deal_status": deal["status"], "sender_tag": sender_tag})
    if is_alert:
        await msg.reply(alert_msg)
        if alert_type == "profanity":
            profane_str = deal.get("profane_users") or ""
            profane_list = [x for x in profane_str.split(",") if x]
            if str(msg.from_user.id) not in profane_list:
                profane_list.append(str(msg.from_user.id))
                db.q("UPDATE deals SET profane_users=? WHERE deal_id=?", (",".join(profane_list), deal["deal_id"]), commit=True)

    # Savdoda muammo / nizo yechimi so'ralganda
    if text.startswith("/ai_yechim") or text.startswith("/yordam") or text.startswith("/nizo"):
        ai_res = ai_dispute_solver(deal, text)
        await msg.answer(ai_res["full_message"])
        return

    user_un = (msg.from_user.username or "").lower()
    is_buyer = (msg.from_user.id == deal["buyer_id"] or user_un == (deal["buyer_username"] or "").lower())
    is_seller = (msg.from_user.id == deal["seller_id"] or user_un == (deal["seller_username"] or "").lower())

    # Ikki tomonning tayyorligini tekshirish
    if deal["status"] == "in_trade" and (deal["group_buyer_ready"] == 0 or deal["group_seller_ready"] == 0):
        words = text.lower().split()
        if any(w in words for w in ["ha", "xa", "tayyor", "boshladik", "start"]) or any(p in text.lower() for p in ["ha tayyorman", "tayyorman", "boshlaylik", "shu yerdaman", "ha shu yerdaman"]):
            if is_buyer:
                db.q("UPDATE deals SET group_buyer_ready=1 WHERE deal_id=?", (deal["deal_id"],), commit=True)
            if is_seller:
                db.q("UPDATE deals SET group_seller_ready=1 WHERE deal_id=?", (deal["deal_id"],), commit=True)

            deal = db.get_deal(deal["deal_id"])
            if deal["group_buyer_ready"] == 1 and deal["group_seller_ready"] == 1:
                await msg.answer(
                    f"🔒 <b>To'lov kafolatlangan va kafil hisobida ushlab turilibdi.</b>\n"
                    f"Oluvchi @{esc_html(deal['buyer_username'])} <b>{fmt(deal['amount'])} so'm</b> to'lagan.\n"
                    f"Sotuvchi @{esc_html(deal['seller_username'])}, siz bemalol mahsulotni topshirishingiz mumkin. "
                    f"Men xavfsizligingizni tekshirib turaman."
                )
            else:
                missing = "sotuvchi" if deal["group_buyer_ready"] == 1 else "oluvchi"
                await msg.answer(f"Sizda {missing} tayyorligini bildirmagan, u yozgandagina savdo boshlanadi.")
            return

    # Mahsulot topshirilgach xaridor tasdig'i ("oldim")
    if is_buyer and any(w in text.lower() for w in ["men oldim", "oldim", "tekshirdim", "qabul qildim", "parol to'g'ri", "ishladi"]):
        db.q("UPDATE deals SET status='buyer_statement' WHERE deal_id=?", (deal["deal_id"],), commit=True)
        await msg.answer(
            f"✅ <b>Xaridor mahsulot olinganini tasdiqladi!</b>\n\n"
            f"1️⃣ <b>OLUVCHIDAN RASMIY TILXAT-ARIZA:</b>\n"
            f"Xaridor @{esc_html(deal['buyer_username'])}, quyidagi matnni yozib yuboring:\n\n"
            f"✍️ <i>«Men {esc_html(deal['title'])}ni to'liq qabul qilib oldim, da'voyim yo'q, agar keyinchalik sotuvchi qaytarib olsa yoki aldasa, bot javob bermaydi»</i>"
        )
        return

    # Oluvchi arizasi AI orqali qabul qilinishi
    if deal["status"] == "buyer_statement" and is_buyer:
        is_valid_stmt, stmt_summary = await verify_statement_with_ai_async(text, "buyer", deal)
        if is_valid_stmt:
            db.q("UPDATE deals SET buyer_statement=?, status='seller_statement' WHERE deal_id=?", (text, deal["deal_id"]), commit=True)
            await msg.answer(
                f"{stmt_summary}\n\n"
                f"2️⃣ <b>SOTUVCHIDAN RASMIY KAFOLAT-TILXAT:</b>\n"
                f"Sotuvchi @{esc_html(deal['seller_username'])}, mahsulot bo'yicha kafolat arizangizni yozib yuboring:\n\n"
                f"✍️ <i>«Men {esc_html(msg.from_user.full_name)} agar sotilgan narsani qaytarib olsam yoki aldov qilsam, qora ro'yxatga solishingizga roziman»</i>"
            )
            return
        else:
            await msg.reply(stmt_summary)
            return

    # Sotuvchi arizasi AI orqali qabul qilinishi
    if deal["status"] == "seller_statement" and is_seller:
        is_valid_stmt, stmt_summary = await verify_statement_with_ai_async(text, "seller", deal)
        if is_valid_stmt:
            db.q("UPDATE deals SET seller_statement=?, status='buyer_confirmed' WHERE deal_id=?", (text, deal["deal_id"]), commit=True)
            await msg.answer(
                f"{stmt_summary}\n\n"
                f"Xaridor @{esc_html(deal['buyer_username'])}, savdo to'liq yakunlandimi? "
                f"Pulni sotuvchiga o'tkazishga rozimisiz? Rozilik uchun <b>«ha roziman»</b> deb yozing:"
            )
            return
        else:
            await msg.reply(stmt_summary)
            return

    # Xaridor yakuniy roziligi
    if deal["status"] == "buyer_confirmed" and is_buyer and ("roziman" in text.lower() or "ha" in text.lower()):
        db.q("UPDATE deals SET status='completed' WHERE deal_id=?", (deal["deal_id"],), commit=True)
        db.q("UPDATE users SET completed_deals=completed_deals+1 WHERE user_id=?", (deal["buyer_id"],), commit=True)
        if deal["seller_id"]:
            db.q("UPDATE users SET completed_deals=completed_deals+1 WHERE user_id=?", (deal["seller_id"],), commit=True)

        # Savdo davomida so'kinganlarni avtomatik qora ro'yxatga kiritish
        profane_str = deal.get("profane_users") or ""
        if profane_str:
            for bad_id in profane_str.split(","):
                if bad_id.strip():
                    u_bad = db.get_user(int(bad_id.strip()))
                    bad_un = u_bad["username"] if u_bad else ""
                    db.add_to_blacklist(
                        user_id=int(bad_id.strip()),
                        username=bad_un,
                        reason=f"Savdo #{deal['deal_id']} chatida so'kingan va haqorat qilgan (AI qorovul)",
                        deal_id=deal["deal_id"],
                        added_by="AI_Chat_Guardian"
                    )
                    await msg.answer(
                        f"🚫 <b>AVTOMATIK QORA RO'YXATGA KIRITILDI!</b>\n"
                        f"Foydalanuvchi @{esc_html(bad_un or bad_id)} savdo davomida so'kingani va qoidalarni buzgani sababli "
                        f"<b>QORA RO'YXATGA (Blacklist)</b> kiritildi va botdan bloklandi!"
                    )

        await msg.answer(
            f"🎉 <b>XARIDOR ROZILIK BILDIRDI! SAVDO MUVAFFAQIYATLI YAKUNLANDI.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Sotuvchi @{esc_html(deal['seller_username'])}, kelishilgan <b>{fmt(deal['amount'])} so'm</b> mablag'ni olish uchun "
            f"<b>16 xonali plastik karta raqamingiz va Ism-Familiyangizni</b> shu yerga yozing:\n"
            f"<i>(Masalan: 8600 1234 5678 9012 ALIYEV VALI)</i>"
        )
        return

    # Sotuvchi karta raqamini berganda
    if deal["status"] == "completed" and is_seller:
        raw_digits = re.sub(r"\D", "", text)
        if len(raw_digits) >= 16:
            card_num = raw_digits[:16]
            owner_name = re.sub(r"[0-9\s]+", " ", text).strip().upper()
            if not owner_name or len(owner_name) < 3:
                owner_name = "SOTUVCHI"
            db.q("UPDATE deals SET seller_card=?, seller_card_owner=? WHERE deal_id=?", (card_num, owner_name, deal["deal_id"]), commit=True)

            buyer = db.get_user(deal["buyer_id"])
            buyer_deals = buyer["completed_deals"] if buyer else 1
            payout = calculate_payout_details(deal, buyer_deals - 1)

            await msg.answer(
                f"💳 <b>SOTUVCHI KARTASI QABUL QILINDI VA TEKSHIRILDI:</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Plastik karta: <code>{format_card(card_num)}</code>\n"
                f"Karta egasi: <b>{esc_html(owner_name)}</b>\n"
                f"O'tkaziladigan sof summa: <b>{fmt(payout['net_to_seller'])} so'm</b>\n\n"
                f"🤖 <b>AI XULOSASI:</b>\n"
                f"Moliyaviy hisob-kitoblar to'liq tekshirildi. Mablag' Garant Administratsiyasiga to'lov uchun yuborildi.\n"
                f"Admin pulni kartangizga o'tkazishi bilan ushbu chatga tasdiq xabarnomasi yuboriladi!"
            )

            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Sotuvchiga pul tashlandi", callback_data=f"adm_paid_seller:{deal['deal_id']}")]
            ])

            for a_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        a_id,
                        f"💰 <b>GURUH SAVDOSI YAKUNLANDI! SOTUVCHIGA PUL O'TKAZING:</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"Bitim raqami: <b>#{deal['deal_id']}</b>\n"
                        f"Tovar: <b>{esc_html(deal['title'])}</b>\n"
                        f"Xaridor: @{esc_html(deal['buyer_username'])} (ID: <code>{deal['buyer_id']}</code>)\n"
                        f"Sotuvchi: @{esc_html(deal['seller_username'])} (ID: <code>{deal['seller_id']}</code>)\n\n"
                        f"💳 <b>Sotuvchi kartasi:</b> <code>{format_card(card_num)}</code>\n"
                        f"👤 <b>Karta egasi:</b> <b>{esc_html(owner_name)}</b>\n\n"
                        f"📊 <b>AI MOLIYAVIY HISOB-KITOBI:</b>\n"
                        f"• Savdo summasi: <b>{fmt(deal['amount'])} so'm</b>\n"
                        f"• Kafil komissiyasi ({payout['fee_pct']}%): <b>{fmt(payout['fee'])} so'm</b>\n"
                        f"• Jami to'langan depozit: <b>{fmt(payout['total_paid'])} so'm</b>\n"
                        f"• 💵 <b>SOTUVCHIGA O'TKAZILADIGAN SUMMA:</b> <code>{fmt(payout['net_to_seller'])} so'm</code>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"Sotuvchi kartasiga pulni tashlab, quyidagi tugmani bosing:",
                        reply_markup=admin_kb
                    )
                except Exception as e:
                    logger.error(f"Adminga yuborishda xato: {e}")
            return
        elif any(char.isdigit() for char in text):
            await msg.reply(
                "⚠️ <b>Karta raqami to'liq emas!</b>\n"
                "Iltimos, 16 xonali plastik karta raqamingiz va Ism-Familiyangizni to'g'ri kiriting:\n"
                "<i>Misol: 8600 1234 5678 9012 ALIYEV VALI</i>"
            )
            return

@router.chat_member()
async def group_member_left_monitor(event: ChatMemberUpdated):
    chat_id = event.chat.id
    deal = db.q("SELECT * FROM deals WHERE group_chat_id=? AND status IN ('in_trade', 'goods_delivered', 'buyer_statement', 'seller_statement')", (chat_id,), one=True)
    if not deal:
        return

    left_user = event.old_chat_member.user
    if event.new_chat_member.status in ("left", "kicked"):
        is_seller = (left_user.id == deal["seller_id"] or (left_user.username or "").lower() == (deal["seller_username"] or "").lower())
        if is_seller:
            kb = [
                [InlineKeyboardButton(text="🚨 Ha, aldamoqchi bo'ldi (Firibgar)", callback_data=f"scam_confirm:{deal['deal_id']}:{left_user.id}")],
                [InlineKeyboardButton(text="Yo'q, adashib chiqdi", callback_data=f"scam_cancel:{deal['deal_id']}")]
            ]
            await bot.send_message(
                chat_id,
                f"🚨 <b>DIQQAT! SOTUVCHI GURUHNI TARK ETDI!</b>\n"
                f"Sotuvchi @{esc_html(left_user.username or str(left_user.id))} guruhdan chiqib ketdi.\n\n"
                f"Xaridor @{esc_html(deal['buyer_username'])}, sotuvchi sizni aldamoqchi bo'ldimi?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
            )

@router.callback_query(F.data.startswith("scam_confirm:"))
async def scam_confirm_cb(call: CallbackQuery):
    await call.answer()
    parts = call.data.split(":")
    d_id = parts[1]
    seller_uid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    deal = db.get_deal(d_id)
    if not deal:
        return

    db.add_to_blacklist(
        user_id=seller_uid,
        username=deal["seller_username"],
        reason=f"Guruhdagi savdoni tugatmay qochib ketgan (Bitim #{d_id})",
        deal_id=d_id,
        added_by="group_monitor"
    )
    now_s = datetime.now(TZ).isoformat()
    db.q("UPDATE deals SET status='dispute', updated_at=? WHERE deal_id=?", (now_s, d_id), commit=True)

    await call.message.edit_text(
        f"🚫 <b>SOTUVCHI QORA RO'YXATGA KIRITILDI VA BOT BLOKLANDI!</b>\n"
        f"Foydalanuvchi: @{esc_html(deal['seller_username'])} (ID: <code>{seller_uid}</code>)\n\n"
        f"Xaridorning puli muzlatilgan va refund qilib qaytarib beriladi."
    )

@router.callback_query(F.data.startswith("scam_cancel:"))
async def scam_cancel_cb(call: CallbackQuery):
    await call.answer("Ogohlantirish bekor qilindi.")
    await call.message.edit_text("ℹ️ Sotuvchi qayta qo'shilishi kutilmoqda.")

# ============================ 13. 5-TUGMA: 🚫 FIRIBGARLAR RO'YXATI ============================
@router.message(StateFilter("*"), F.text.contains("Firibgar") | F.text.contains("𝗙𝗶𝗿𝗶𝗯𝗴𝗮𝗿") | F.text.contains("Mashenik") | F.text.contains("Qora ro'yxat"))
async def blacklist_menu_handler(msg: Message, state: FSMContext):
    await state.clear()
    rows = db.get_all_blacklisted(25)
    if not rows:
        await msg.answer(
            "🛡 <b>FIRIBGARLAR VA MASHENNIKLAR RO'YXATI (BLACKLIST):</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ <b>Hozircha tizimda firibgarlar aniqlanmagan!</b>\n\n"
            "Kafil bot platformasi barcha savdolarni 100% xavfsiz nazorat qiladi. "
            "Agar kimdir kelishuvni buzsa, tovar bermasa yoki nojo'ya harakat qilsa, uning hisobi bir umrga ushbu ro'yxatga tushadi.",
            reply_markup=main_menu_keyboard(msg.from_user.id)
        )
        return

    text = "🚫 <b>FIRIBGARLAR VA BLOKLANGANLAR RO'YXATI (BLACKLIST):</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for r in rows:
        u_tag = f"@{esc_html(r['username'])}" if r["username"] else "mavjud emas"
        uid_tag = f"<code>{r['user_id']}</code>" if r["user_id"] else "nomalum"
        date_str = (r["created_at"] or "")[:10]
        text += (
            f"👤 <b>Foydalanuvchi:</b> <b>{u_tag}</b>\n"
            f"🆔 <b>Telegram ID:</b> {uid_tag}\n"
            f"📝 <b>Qoidabuzarlik sababi:</b> <i>{esc_html(r['reason'])}</i>\n"
            f"📅 <b>Bloklangan sana:</b> <b>{date_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
    text += "\n⚠️ <b>DIQQAT:</b> <i>Ushbu shaxslar bilan Telegram orqali har qanday savdo qilish qat'iyan xavfli hisoblanadi!</i>"
    await msg.answer(text, reply_markup=main_menu_keyboard(msg.from_user.id))

# ============================ 14. 6-TUGMA: 💾 ZAXIRA NUSXA (BACKUP) ============================
@router.message(StateFilter("*"), F.text.contains("Zaxira") | F.text.contains("𝗭𝗮𝘅𝗶𝗿𝗮") | F.text.contains("Backup") | F.text.contains("𝗕𝗮𝗰𝗸𝘂𝗽"))
async def backup_menu_handler(msg: Message, state: FSMContext):
    await state.clear()
    text = (
        "💾 <b>MA'LUMOTLAR XAVFSIZLIGI VA ZAXIRA (BACKUP)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Siz o'z hisobingizdagi barcha savdolar, arizalar va yozishmalar tarixini <b>JSON formatida</b> xavfsiz yuklab olishingiz "
        "yoki avval saqlangan zaxira faylingizni yuklab tiklashingiz mumkin.\n\n"
        "👇 <b>Quyidagi amallardan birini tanlang:</b>"
    )
    kb = [
        [InlineKeyboardButton(text="📥 𝗭𝗮𝘅𝗶𝗿𝗮 𝗻𝘂𝘀𝘅𝗮𝗻𝗶 𝘆𝘂𝗸𝗹𝗮𝗯 𝗼𝗹𝗶𝘀𝗵", callback_data="backup_download")],
        [InlineKeyboardButton(text="📤 𝗭𝗮𝘅𝗶𝗿𝗮 𝗳𝗮𝘆𝗹𝗶𝗻𝗶 𝘁𝗶𝗸𝗹𝗮𝘀𝗵", callback_data="backup_restore")]
    ]
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "backup_download")
async def backup_download_cb(call: CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    json_bytes = db.export_user_backup_json(uid)
    now_str = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    doc = BufferedInputFile(json_bytes, filename=f"kafil_backup_{uid}_{now_str}.json")
    await call.message.answer_document(
        document=doc,
        caption=(
            f"✅ <b>ZAXIRA NUSXANGIZ TAYYOR!</b>\n"
            f"Fayl: <code>kafil_backup_{uid}_{now_str}.json</code>\n\n"
            f"Ushbu fayl orqali botdagi tarixingiz va ma'lumotlaringizni xohlagan vaqtda qayta tiklashingiz mumkin."
        )
    )

@router.callback_query(F.data == "backup_restore")
async def backup_restore_cb(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(BackupFSM.upload_file)
    await call.message.answer(
        "📤 <b>ZAXIRA NUSXANI TIKLASH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Avvalgi <b>.json</b> kengaytmali zaxira faylingizni shu yerga hujjat (file) sifatida yuboring:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ 𝗕𝗲𝗸𝗼𝗿 𝗾𝗶𝗹𝗶𝘀𝗵")]], resize_keyboard=True)
    )

@router.message(BackupFSM.upload_file, F.document)
async def process_backup_doc(msg: Message, state: FSMContext):
    doc = msg.document
    if not doc.file_name.endswith(".json"):
        await msg.answer("Iltimos, faqat .json fayl yuboring:")
        return

    file_obj = await bot.get_file(doc.file_id)
    file_bytes = await bot.download_file(file_obj.file_path)
    ok, count = db.import_user_backup_json(msg.from_user.id, file_bytes.read())
    await state.clear()
    if ok:
        await msg.answer(f"✅ <b>Muvaffaqiyatli tiklandi!</b>\n{count} ta bitim qayta yuklandi.", reply_markup=main_menu_keyboard(msg.from_user.id))
    else:
        await msg.answer("❌ Fayl formati yaroqsiz.", reply_markup=main_menu_keyboard(msg.from_user.id))

# ============================ 15. 7-TUGMA: 📑 ARIZALARIM ============================
@router.message(StateFilter("*"), F.text.contains("Arizalarim") | F.text.contains("𝗔𝗿𝗶𝘇𝗮𝗹𝗮𝗿𝗶𝗺") | F.text.contains("Ariza") | F.text.contains("𝗔𝗿𝗶𝘇𝗮"))
async def my_complaints_handler(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    comps = db.q("SELECT * FROM complaints WHERE complainant_id=? ORDER BY created_at DESC LIMIT 10", (uid,))
    if not comps:
        await msg.answer(
            "📑 <b>Sizda hali hech qanday shikoyat yoki arizalar mavjud emas.</b>\n\n"
            "Kafil tizimi barcha savdolarni halol va xavfsiz o'tishini to'liq kafolatlaydi.",
            reply_markup=main_menu_keyboard(uid)
        )
        return

    text = "📑 <b>SIZNING ARIZALARINGIZ VA ULARNING HOLATI:</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    kb = []
    st_map = {
        "pending": "⏳ Ko'rib chiqilmoqda (5 kunlik muhlat)",
        "resolved": "✅ Ijobiy hal qilindi",
        "blacklisted": "🚫 Sotuvchi qora ro'yxatga kiritildi",
        "withdrawn": "↩️ Qaytarib olingan (Bekor)"
    }
    for c in comps:
        st = st_map.get(c["status"], c["status"])
        c_date = (c["created_at"] or "")[:10]
        text += (
            f"🔹 <b>Ariza raqami:</b> <code>#{c['id']}</code> | <b>{st}</b>\n"
            f"├ 👤 <b>Sotuvchi:</b> @{esc_html(c['accused_username'])}\n"
            f"├ 📝 <b>Sababi:</b> <i>{esc_html(c['reason'])}</i>\n"
            f"└ 📅 <b>Sana:</b> <b>{c_date}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        if c["status"] == "pending":
            kb.append([InlineKeyboardButton(text=f"↩️ 𝗔𝗿𝗶𝘇𝗮𝗻𝗶 𝗾𝗮𝘆𝘁𝗮𝗿𝗶𝗯 𝗼𝗹𝗶𝘀𝗵: #{c['id']}", callback_data=f"withdraw_comp:{c['id']}")])

    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb) if kb else main_menu_keyboard(uid))

@router.callback_query(F.data.startswith("withdraw_comp:"))
async def withdraw_comp_cb(call: CallbackQuery):
    await call.answer()
    comp_id = int(call.data.split(":")[1])
    comp = db.q("SELECT * FROM complaints WHERE id=?", (comp_id,), one=True)
    if not comp or comp["complainant_id"] != call.from_user.id:
        await call.message.answer("⚠️ Ariza topilmadi.")
        return

    db.q("UPDATE complaints SET status='withdrawn' WHERE id=?", (comp_id,), commit=True)
    await call.message.edit_text(
        f"✅ <b>ARIZA QAYTARIB OLINDI VA BEKOR BO'LDI! (#{comp_id})</b>\n"
        f"Sotuvchi ustidan yuborilgan shikoyat bekor qilindi."
    )
    if comp["accused_id"]:
        try:
            await bot.send_message(
                comp["accused_id"],
                f"ℹ️ @{esc_html(comp['complainant_username'])} sizning ustingizdan berilgan <b>#{comp_id}</b> arizani qaytarib oldi."
            )
        except Exception:
            pass

# ============================ 16. 8-TUGMA: 👑 ADMIN PANEL ============================
@router.message(StateFilter("*"), Command("id"))
async def get_my_id_cmd(msg: Message):
    uid = msg.from_user.id
    await msg.answer(f"🆔 <b>Sizning Telegram ID raqamingiz:</b> <code>{uid}</code>\n<i>(Nusxalash uchun ID ustiga bosing)</i>")

@router.message(StateFilter("*"), Command("auth_admin"))
async def auth_admin_cmd(msg: Message):
    uid = msg.from_user.id
    custom = db.get_setting("extra_admins", "")
    admins_list = [x.strip() for x in custom.split(",") if x.strip()]
    if str(uid) not in admins_list:
        admins_list.append(str(uid))
        db.set_setting("extra_admins", ",".join(admins_list))
    await msg.answer(
        f"✅ <b>Tabriklaymiz!</b> Siz (ID: <code>{uid}</code>) muvaffaqiyatli bot administratori qilib tayinlandingiz!\n\n"
        f"Endi pastdagi «👑 𝗔𝗱𝗺𝗶𝗻 𝗣𝗮𝗻𝗲𝗹» tugmasini bosib boshqaruv paneliga kirishingiz mumkin.",
        reply_markup=main_menu_keyboard(uid)
    )

@router.message(StateFilter("*"), Command("admin"))
@router.message(StateFilter("*"), F.text.lower().contains("admin") | F.text.contains("𝗔𝗱𝗺𝗶𝗻"))
async def admin_panel_handler(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    if not is_admin(uid):
        await msg.answer(
            f"👑 <b>ADMIN BOSHQARUV PANELI</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <b>Kechirasiz, siz ushbu botning administratori emassiz!</b>\n\n"
            f"🆔 <b>Sizning Telegram ID raqamingiz:</b> <code>{uid}</code>\n"
            f"<i>(ID ustiga bosing — avtomatik nusxalanadi)</i>\n\n"
            f"📌 <b>Admin huquqini darhol faollashtirish uchun:</b>\n"
            f"Botga <code>/auth_admin</code> buyrug'ini yuboring.",
            reply_markup=main_menu_keyboard(uid)
        )
        return

    total_u = db.q("SELECT COUNT(*) c FROM users", one=True)["c"]
    total_d = db.q("SELECT COUNT(*) c FROM deals", one=True)["c"]
    black_c = db.q("SELECT COUNT(*) c FROM blacklist", one=True)["c"]
    pend_w = db.q("SELECT COUNT(*) c FROM withdrawals WHERE status='pending'", one=True)["c"]

    card = db.get_setting("card_number", DEFAULT_CARD_NUMBER)
    owner = db.get_setting("card_owner", DEFAULT_CARD_OWNER)
    comm = db.get_setting("commission_percent", "10")

    text = (
        f"👑 <b>BOSH ADMIN BOSHQARUV MARKAZI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>TIZIMNING UMUMIY STATISTIKASI:</b>\n"
        f"├ 👥 <b>Foydalanuvchilar:</b> <b>{total_u} ta</b>\n"
        f"├ 📦 <b>Jami amalga oshgan bitimlar:</b> <b>{total_d} ta</b>\n"
        f"├ 🚫 <b>Bloklangan firibgarlar:</b> <b>{black_c} ta</b>\n"
        f"└ 💸 <b>Kutilayotgan pul yechishlar:</b> <b>{pend_w} ta</b>\n\n"
        f"💳 <b>MOLIYAVIY SOZLAMALAR:</b>\n"
        f"├ 💳 <b>Kafil to'lov kartasi:</b> <code>{format_card(card)}</code>\n"
        f"├ 👤 <b>Karta egasi:</b> <b>{owner}</b>\n"
        f"└ 💼 <b>Kafil komissiyasi:</b> <b>{comm}%</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ <b>Kerakli boshqaruv bo'limini tanlang:</b>"
    )
    kb = [
        [InlineKeyboardButton(text="📦 𝗦𝗮𝘃𝗱𝗼𝗹𝗮𝗿 𝗺𝗼𝗻𝗶𝘁𝗼𝗿𝗶𝗻𝗴𝗶", callback_data="adm_view_deals"),
         InlineKeyboardButton(text="🚫 𝗙𝗶𝗿𝗶𝗯𝗴𝗮𝗿𝗹𝗮𝗿 𝗯𝗼𝘀𝗵𝗾𝗮𝗿𝘂𝘃𝗶", callback_data="adm_view_scammers")],
        [InlineKeyboardButton(text="📢 𝗫𝗮𝗯𝗮𝗿 𝘁𝗮𝗿𝗾𝗮𝘁𝗶𝘀𝗵", callback_data="adm_broadcast"),
         InlineKeyboardButton(text="📢 𝗠𝗮𝗷𝗯𝘂𝗿𝗶𝘆 𝗸𝗮𝗻𝗮𝗹𝗹𝗮𝗿", callback_data="adm_channels")],
        [InlineKeyboardButton(text="📊 𝗔𝗜 𝗧𝗮𝗵𝗹𝗶𝗹𝗶𝘆 𝗵𝗶𝘀𝗼𝗯𝗼𝘁", callback_data="adm_ai_chat"),
         InlineKeyboardButton(text="💸 𝗥𝗲𝗳𝗲𝗿𝗮𝗹 𝘀𝗼'𝗿𝗼𝘃𝗹𝗮𝗿𝗶", callback_data="adm_withdrawals")],
        [InlineKeyboardButton(text="💳 𝗞𝗮𝗿𝘁𝗮 𝘀𝗼𝘇𝗹𝗮𝘀𝗵", callback_data="adm_edit_card"),
         InlineKeyboardButton(text="💼 𝗞𝗼𝗺𝗶𝘀𝘀𝗶𝘆𝗮 𝘀𝗼𝘇𝗹𝗮𝘀𝗵", callback_data="adm_edit_pct")]
    ]
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "adm_view_deals")
async def adm_view_deals_cb(call: CallbackQuery):
    await call.answer()
    deals = db.q("SELECT * FROM deals ORDER BY updated_at DESC LIMIT 10")
    if not deals:
        await call.message.answer("Savdolar mavjud emas.")
        return
    text = "📦 <b>OXIRGI SAVDOLAR:</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for d in deals:
        text += f"🆔 <b>#{d['deal_id']}</b> | {d['status']} | <b>{fmt(d['amount'])} so'm</b>\n"
    await call.message.answer(text)

@router.callback_query(F.data == "adm_view_scammers")
async def adm_view_scammers_cb(call: CallbackQuery):
    await call.answer()
    kb = [
        [InlineKeyboardButton(text="➕ 𝗙𝗶𝗿𝗶𝗯𝗴𝗮𝗿 𝗾𝗼'𝘀𝗵𝗶𝘀𝗵", callback_data="adm_add_scammer")],
        [InlineKeyboardButton(text="📋 𝗥𝗼'𝘆𝘅𝗮𝘁𝗻𝗶 𝗸𝗼'𝗿𝗶𝘀𝗵", callback_data="adm_list_scammers")]
    ]
    await call.message.answer("🚫 <b>Firibgarlar boshqaruvi:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "adm_add_scammer")
async def adm_add_scammer_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.add_black_user)
    await call.message.answer("Bloklanadigan shaxsning Telegram ID yoki @username'ini kiriting:")

@router.message(AdminFSM.add_black_user)
async def adm_proc_scammer_user(msg: Message, state: FSMContext):
    val = msg.text.strip().lstrip("@")
    await state.update_data(black_target=val)
    await state.set_state(AdminFSM.add_black_reason)
    await msg.answer("Bloklash sababini kiriting:")

@router.message(AdminFSM.add_black_reason)
async def adm_proc_scammer_reason(msg: Message, state: FSMContext):
    reason = msg.text.strip()
    data = await state.get_data()
    target = data["black_target"]
    await state.clear()

    uid = int(target) if target.isdigit() else None
    un = target if not target.isdigit() else ""
    db.add_to_blacklist(user_id=uid, username=un, reason=reason, added_by="admin")
    await msg.answer(f"✅ <b>{target}</b> qora ro'yxatga qo'shildi va bloklandi.")

@router.callback_query(F.data == "adm_list_scammers")
async def adm_list_scammers_cb(call: CallbackQuery):
    await call.answer()
    rows = db.get_all_blacklisted(20)
    if not rows:
        await call.message.answer("Qora ro'yxat bo'sh.")
        return
    text = "🚫 <b>QORA RO'YXAT:</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for r in rows:
        text += f"ID: <code>{r['user_id']}</code> | @{r['username']} | <i>{r['reason']}</i>\n"
    await call.message.answer(text)

@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.broadcast)
    await call.message.answer(
        "📢 Barcha bot foydalanuvchilariga yuboriladigan xabarni (matn, rasm yoki post) yuboring:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ 𝗕𝗲𝗸𝗼𝗿 𝗾𝗶𝗹𝗶𝘀𝗵")]], resize_keyboard=True)
    )

@router.message(AdminFSM.broadcast)
async def adm_proc_broadcast(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    users = db.q("SELECT user_id FROM users")
    await state.clear()
    sent, failed = 0, 0
    prog = await msg.answer(f"Xabar yuborilmoqda... 0/{len(users)}")
    for u in users:
        try:
            await msg.copy_to(chat_id=u["user_id"])
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await prog.edit_text(f"✅ Xabar tarqatish yakunlandi!\nYetkazildi: {sent} ta\nYetib bormadi: {failed} ta")

@router.callback_query(F.data == "adm_channels")
async def adm_channels_menu(call: CallbackQuery):
    await call.answer()
    channels = db.q("SELECT * FROM mandatory_channels")
    text = (
        "📢 <b>MAJBURIY OBUNA KANALLARI SOZLAMASI:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Foydalanuvchilar ushbu kanallarga obuna bo'lmaguncha bot ishlamaydi!\n\n"
    )
    kb = []
    for c in channels:
        text += f"• <b>{esc_html(c['channel_title'])}</b> (<code>{c['channel_id']}</code>) — <a href=\"{c['invite_link']}\">Havola</a>\n"
        kb.append([InlineKeyboardButton(text=f"❌ O'chirish: {c['channel_title'][:20]}", callback_data=f"adm_del_ch:{c['id']}")])

    if not channels:
        text += "<i>Hozircha majburiy kanallar belgilanmagan (Bot erkin ishlaydi).</i>\n"

    kb.append([InlineKeyboardButton(text="➕ 𝗬𝗮𝗻𝗴𝗶 𝗸𝗮𝗻𝗮𝗹 𝗾𝗼'𝘀𝗵𝗶𝘀𝗵", callback_data="adm_add_ch")])
    if channels:
        kb.append([InlineKeyboardButton(text="🗑 𝗕𝗮𝗿𝗰𝗵𝗮 𝗸𝗮𝗻𝗮𝗹𝗹𝗮𝗿𝗻𝗶 𝘁𝗼𝘇𝗮𝗹𝗮𝘀𝗵", callback_data="adm_clear_ch")])

    await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), disable_web_page_preview=True)

@router.callback_query(F.data.startswith("adm_del_ch:"))
async def adm_del_ch_single(call: CallbackQuery):
    await call.answer("Kanal o'chirildi.")
    ch_id = call.data.split(":")[1]
    db.q("DELETE FROM mandatory_channels WHERE id=?", (ch_id,), commit=True)
    await adm_channels_menu(call)

@router.callback_query(F.data == "adm_clear_ch")
async def adm_clear_ch_cb(call: CallbackQuery):
    await call.answer()
    db.q("DELETE FROM mandatory_channels", commit=True)
    await call.message.answer("Barcha majburiy kanallar o'chirildi.")
    await adm_channels_menu(call)

@router.callback_query(F.data == "adm_add_ch")
async def adm_add_ch_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.add_channel_id)
    await call.message.answer(
        "📢 <b>Kanal qo'shish:</b>\n\n"
        "Kanalning @username'ini (masalan: <code>@mening_kanalim</code>) yoki ID raqamini (<code>-100...</code>) yuboring:\n\n"
        "<i>⚠️ DIQQAT: Bot ushbu kanalda admin bo'lishi shart, aks holda a'zolarni tekshira olmaydi!</i>",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ 𝗕𝗲𝗸𝗼𝗿 𝗾𝗶𝗹𝗶𝘀𝗵")]], resize_keyboard=True)
    )

@router.message(AdminFSM.add_channel_id)
async def adm_proc_ch_id(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return

    raw = msg.text.strip()
    clean = re.sub(r"^(https?:\/\/)?(www\.)?t\.me\/", "", raw, flags=re.IGNORECASE).strip()
    clean = clean.split("/")[0].split("?")[0].strip()

    if clean.lstrip("-").isdigit():
        target_chat = int(clean)
    elif clean.startswith("@"):
        target_chat = clean
    else:
        target_chat = f"@{clean}"

    ch_title = ""
    ch_link = ""
    cid = str(target_chat)

    try:
        chat = await bot.get_chat(target_chat)
        ch_title = chat.title or str(target_chat)
        if chat.username:
            ch_link = f"https://t.me/{chat.username}"
        elif chat.invite_link:
            ch_link = chat.invite_link
        else:
            try:
                ch_link = await bot.export_chat_invite_link(chat.id)
            except Exception:
                ch_link = f"https://t.me/{clean.lstrip('@')}"
        cid = str(chat.id)

        member = await bot.get_chat_member(chat_id=chat.id, user_id=bot.id)
        if member.status not in ("administrator", "creator"):
            await msg.answer(
                "⚠️ <b>DIQQAT: Bot ushbu kanalda admin emas!</b>\n"
                "Iltimos, avval botni kanalingizga admin qiling va qaytadan username yoki ID yuboring:"
            )
            return
    except Exception as e:
        logger.warning(f"Kanalni aniqlashda xato ({target_chat}): {e}")
        await msg.answer(
            f"⚠️ <b>Kanal topilmadi yoki bot admin emas!</b>\n"
            f"Xatolik: <i>{esc_html(str(e))}</i>\n\n"
            f"1. Botni avval kanalingizga <b>ADMIN</b> qilib qo'shing.\n"
            f"2. Kanalning to'g'ri @username'ini (masalan: <code>@mening_kanalim</code>) yuboring."
        )
        return

    await state.update_data(ch_id=cid, ch_title=ch_title, ch_link=ch_link)
    db.q("INSERT INTO mandatory_channels (channel_id, channel_title, invite_link) VALUES (?, ?, ?)",
         (cid, ch_title, ch_link), commit=True)
    await state.clear()
    await msg.answer(
        f"✅ <b>Kanal muvaffaqiyatli qo'shildi!</b>\n\n"
        f"📢 Nomi: <b>{esc_html(ch_title)}</b>\n"
        f"🆔 ID: <code>{cid}</code>\n"
        f"🔗 Link: {ch_link}\n\n"
        f"<i>Endi ushbu kanalga obuna bo'lmagan foydalanuvchilar botdan foydalana olmaydi.</i>",
        reply_markup=main_menu_keyboard(msg.from_user.id)
    )

@router.message(AdminFSM.add_channel_title)
async def adm_proc_ch_title(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return
    title = msg.text.strip()
    await state.update_data(ch_title=title)
    await state.set_state(AdminFSM.add_channel_link)
    await msg.answer("Kanalga taklif havolasini (link) kiriting:")

@router.message(AdminFSM.add_channel_link)
async def adm_proc_ch_link(msg: Message, state: FSMContext):
    if any(w in msg.text.lower() for w in ["bekor", "cancel"]) or msg.text.startswith("❌"):
        await state.clear()
        await msg.answer("Bekor qilindi.", reply_markup=main_menu_keyboard(msg.from_user.id))
        return
    link = msg.text.strip()
    data = await state.get_data()
    await state.clear()
    db.q("INSERT INTO mandatory_channels (channel_id, channel_title, invite_link) VALUES (?, ?, ?)",
         (data["ch_id"], data["ch_title"], link), commit=True)
    await msg.answer(
        f"✅ <b>Kanal muvaffaqiyatli qo'shildi:</b> <b>{data['ch_title']}</b>\n"
        f"<i>Ushbu kanalga obuna bo'lmagan foydalanuvchilar uchun bot ishlamaydi.</i>",
        reply_markup=main_menu_keyboard(msg.from_user.id)
    )

# ============================ SOTUVCHIGA PUL TO'LASHNI TASDIQLASH (ADMIN) ============================
@router.callback_query(F.data.startswith("adm_paid_seller:"))
async def adm_paid_seller_cb(call: CallbackQuery):
    await call.answer("To'lov tasdiqlanmoqda...")
    d_id = call.data.split(":")[1]
    deal = db.get_deal(d_id)
    if not deal:
        await call.message.answer("Bitim topilmadi.")
        return

    db.q("UPDATE deals SET admin_payout_status='paid' WHERE deal_id=?", (d_id,), commit=True)
    card_f = format_card(deal["seller_card"] or "")
    amt_f = fmt(deal["amount"])

    # Guruh chatiga xabar berish
    if deal.get("group_chat_id"):
        try:
            await bot.send_message(
                deal["group_chat_id"],
                f"🎉 <b>MABLAG' SOTUVCHI KARTASIGA MUVAFFAQIYATLI O'TKAZILDI!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Garant Admini sotuvchi @{esc_html(deal['seller_username'])} ning <code>{card_f}</code> kartasiga "
                f"<b>{amt_f} so'm</b> pulni to'liq o'tkazib berdi!\n\n"
                f"🛡 Kafil botidan foydalanganingiz uchun rahmat! Savdo 100% xavfsiz va muvaffaqiyatli yakunlandi."
            )
        except Exception:
            pass

    # Sotuvchiga to'g'ridan-to'g'ri xabar berish
    if deal.get("seller_id"):
        try:
            await bot.send_message(
                deal["seller_id"],
                f"🎉 <b>TO'LOV KARTANGIZGA O'TKAZILDI! (#{d_id})</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Karta: <code>{card_f}</code> ({esc_html(deal['seller_card_owner'] or '')})\n"
                f"Summa: <b>{amt_f} so'm</b>\n\n"
                f"Kafil xizmatidan foydalanganingiz uchun rahmat!"
            )
        except Exception:
            pass

    try:
        await call.message.edit_text(
            call.message.html_text + f"\n\n✅ <b>ADMIN TASDIQLADI:</b> Pul sotuvchi kartasiga muvaffaqiyatli o'tkazildi!",
            reply_markup=None
        )
    except Exception:
        pass

@router.callback_query(F.data == "adm_ai_chat")
async def adm_ai_chat_report(call: CallbackQuery):
    await call.answer()
    report = await analyze_deals_for_admin()
    await call.message.answer(report)

@router.callback_query(F.data == "adm_withdrawals")
async def adm_withdrawals_cb(call: CallbackQuery):
    await call.answer()
    rows = db.q("SELECT * FROM withdrawals WHERE status='pending' ORDER BY created_at ASC LIMIT 10")
    if not rows:
        await call.message.answer("Kutilayotgan pul yechish so'rovlari yo'q.")
        return
    for w in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ To'landi deb belgilash", callback_data=f"adm_pay_w:{w['id']}")]
        ])
        await call.message.answer(
            f"💸 <b>SO'ROV #{w['id']}</b>\n"
            f"Foydalanuvchi: ID <code>{w['user_id']}</code>\n"
            f"Summa: <b>{fmt(w['amount'])} so'm</b>\n"
            f"Karta: <code>{format_card(w['card_number'])}</code> ({w['card_owner']})",
            reply_markup=kb
        )

@router.callback_query(F.data.startswith("adm_pay_w:"))
async def adm_pay_w_cb(call: CallbackQuery):
    await call.answer()
    w_id = int(call.data.split(":")[1])
    db.q("UPDATE withdrawals SET status='completed' WHERE id=?", (w_id,), commit=True)
    w = db.q("SELECT * FROM withdrawals WHERE id=?", (w_id,), one=True)
    await call.message.edit_text(f"✅ So'rov #{w_id} to'landi deb belgilandi.")
    if w:
        try:
            await bot.send_message(
                w["user_id"],
                f"🎉 <b>PULINGIZ O'TKAZIB BERILDI! (#{w_id})</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Summa: <b>{fmt(w['amount'])} so'm</b>\n"
                f"Karta: <code>{format_card(w['card_number'])}</code>\n\n"
                f"Kafil xizmatidan foydalanganingiz uchun rahmat!"
            )
        except Exception:
            pass

@router.callback_query(F.data.startswith("adm_reply_ticket:"))
async def adm_reply_ticket_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    t_id = int(call.data.split(":")[1])
    await state.set_state(AdminFSM.reply_ticket_text)
    await state.update_data(reply_ticket_id=t_id)
    await call.message.answer(f"Murojaat #{t_id} uchun javob matnini kiriting:")

@router.message(AdminFSM.reply_ticket_text)
async def adm_proc_ticket_reply(msg: Message, state: FSMContext):
    data = await state.get_data()
    t_id = data["reply_ticket_id"]
    reply_text = msg.text.strip()
    await state.clear()

    ticket = db.q("SELECT * FROM ai_tickets WHERE id=?", (t_id,), one=True)
    if ticket:
        db.q("UPDATE ai_tickets SET status='answered', admin_response=? WHERE id=?", (reply_text, t_id), commit=True)
        try:
            await bot.send_message(
                ticket["user_id"],
                f"👨‍💼 <b>ADMINISTRATOR JAVOBI:</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{reply_text}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Murojaatingiz: «{esc_html(ticket['question'])}»</i>"
            )
            await msg.answer("✅ Javob foydalanuvchiga muvaffaqiyatli yetkazildi.")
        except Exception as e:
            await msg.answer(f"Yetkazishda xatolik: {e}")

@router.callback_query(F.data == "adm_edit_card")
async def adm_edit_card_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.change_card)
    await call.message.answer("Yangi 16 xonali karta raqamini kiriting:")

@router.message(AdminFSM.change_card)
async def adm_proc_new_card(msg: Message, state: FSMContext):
    card = re.sub(r"\D", "", msg.text.strip())
    await state.update_data(new_card=card)
    await state.set_state(AdminFSM.change_owner)
    await msg.answer("Karta egasining Ism-Familiyasini kiriting:")

@router.message(AdminFSM.change_owner)
async def adm_proc_new_owner(msg: Message, state: FSMContext):
    owner = msg.text.strip().upper()
    data = await state.get_data()
    await state.clear()
    db.set_setting("card_number", data["new_card"])
    db.set_setting("card_owner", owner)
    await msg.answer(f"✅ Karta yangilandi:\n<code>{format_card(data['new_card'])}</code> ({owner})")

@router.callback_query(F.data == "adm_edit_pct")
async def adm_edit_pct_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminFSM.change_commission)
    await call.message.answer("Yangi komissiya foizini kiriting (masalan: 10):")

@router.message(AdminFSM.change_commission)
async def adm_proc_new_pct(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    if not txt.isdigit():
        await msg.answer("Faqat butun son kiriting:")
        return
    db.set_setting("commission_percent", txt)
    await state.clear()
    await msg.answer(f"✅ Komissiya foizi {txt}% ga o'zgartirildi.")

# ============================ 17. BEKOR QILISH CALLBACK ============================
@router.callback_query(F.data.startswith("cancel_deal:"))
async def cancel_deal_cb(call: CallbackQuery):
    await call.answer("Bitim bekor qilinmoqda...")
    d_id = call.data.split(":")[1]
    deal = db.get_deal(d_id)
    if not deal:
        await call.message.answer("Bitim topilmadi.")
        return
    if deal["status"] not in ("created", "waiting_payment"):
        await call.message.answer("Muzlatilgan savdoni bekor qilib bo'lmaydi.")
        return
    now_s = datetime.now(TZ).isoformat()
    db.q("UPDATE deals SET status='cancelled', updated_at=? WHERE deal_id=?", (now_s, d_id), commit=True)
    if deal.get("payment_id"):
        await cancel_payment_async(deal["payment_id"])
    await call.message.edit_text(f"❌ <b>#{d_id}</b> raqamli bitim bekor qilindi.")

# ============================ 18. BOT ICHIDAGI SHIFRLANGAN CHAT ============================
@router.message(F.chat.type == "private", F.text)
async def private_trade_chat_router(msg: Message, state: FSMContext):
    cur_st = await state.get_state()
    if cur_st is not None or msg.text.startswith("/"):
        return

    # Asosiy menyu tugmalari bo'lsa o'tkazib yuborish
    if any(k in msg.text for k in ["Tarix", "SOS", "Yordam", "Referal", "Savdo", "Firibgar", "Backup", "Zaxira", "Ariza", "Admin", "Bekor"]):
        return

    uid = msg.from_user.id
    deal = db.q("SELECT * FROM deals WHERE (buyer_id=? OR seller_id=?) AND mode='bot' AND status IN ('in_trade', 'goods_delivered', 'buyer_statement', 'seller_statement', 'buyer_confirmed') ORDER BY updated_at DESC LIMIT 1", (uid, uid), one=True)
    if not deal:
        await msg.answer(
            "Assalomu alaykum! Kerakli bo'limni tanlash uchun pastdagi menyudan foydalaning:",
            reply_markup=main_menu_keyboard(uid)
        )
        return

    is_buyer = (uid == deal["buyer_id"])
    target_id = deal["seller_id"] if is_buyer else deal["buyer_id"]
    role = "Xaridor" if is_buyer else "Sotuvchi"
    sender_tag = f"@{msg.from_user.username}" if msg.from_user.username else f"{role} ({uid})"
    text_content = msg.text.strip()
    now_s = datetime.now(TZ).isoformat()

    db.log_deal_message(deal["deal_id"], uid, sender_tag, role, text_content)

    # Real-vaqtda AI chat qorovuli (Xavfsizlik va odob nazorati)
    is_alert, alert_type, alert_msg = await ai_chat_guardian(text_content, {"deal_status": deal["status"], "sender_tag": sender_tag})
    if is_alert:
        await msg.reply(alert_msg)
        if alert_type == "profanity":
            profane_str = deal.get("profane_users") or ""
            profane_list = [x for x in profane_str.split(",") if x]
            if str(uid) not in profane_list:
                profane_list.append(str(uid))
                db.q("UPDATE deals SET profane_users=? WHERE deal_id=?", (",".join(profane_list), deal["deal_id"]), commit=True)
        if target_id:
            try:
                await bot.send_message(target_id, alert_msg)
            except Exception:
                pass

    if target_id:
        try:
            await bot.send_message(
                target_id,
                f"💬 <b>{role} ({esc_html(sender_tag)}):</b>\n{esc_html(text_content)}"
            )
        except Exception:
            pass

    # Sotuvchi bitim yakunlangach karta raqamini berganda
    if not is_buyer and deal["status"] in ("completed", "goods_delivered"):
        raw_digits = re.sub(r"\D", "", text_content)
        if len(raw_digits) >= 16:
            card_num = raw_digits[:16]
            owner_name = re.sub(r"[0-9\s]+", " ", text_content).strip().upper()
            if not owner_name or len(owner_name) < 3:
                owner_name = "SOTUVCHI"
            db.q("UPDATE deals SET seller_card=?, seller_card_owner=? WHERE deal_id=?", (card_num, owner_name, deal["deal_id"]), commit=True)

            buyer = db.get_user(deal["buyer_id"])
            buyer_deals = buyer["completed_deals"] if buyer else 1
            payout = calculate_payout_details(deal, buyer_deals - 1)

            await msg.answer(
                f"💳 <b>KARTANGIZ QABUL QILINDI:</b> <code>{format_card(card_num)}</code> ({esc_html(owner_name)})\n"
                f"O'tkaziladigan sof summa: <b>{fmt(payout['net_to_seller'])} so'm</b>\n\n"
                f"🤖 <b>AI XULOSASI:</b> Hisob-kitoblar to'liq tekshirildi. Mablag' Garant Administratsiyasiga yuborildi.\n"
                f"Admin pulni kartangizga o'tkazishi bilan xabarnoma olasiz!"
            )

            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Sotuvchiga pul tashlandi", callback_data=f"adm_paid_seller:{deal['deal_id']}")]
            ])

            for a_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        a_id,
                        f"💰 <b>SAVDO YAKUNLANDI! SOTUVCHIGA PUL O'TKAZING:</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"Bitim: <b>#{deal['deal_id']}</b>\n"
                        f"Tovar: <b>{esc_html(deal['title'])}</b>\n"
                        f"Xaridor: @{esc_html(deal['buyer_username'])} (ID: <code>{deal['buyer_id']}</code>)\n"
                        f"Sotuvchi: @{esc_html(deal['seller_username'])} (ID: <code>{deal['seller_id']}</code>)\n\n"
                        f"💳 <b>Sotuvchi kartasi:</b> <code>{format_card(card_num)}</code>\n"
                        f"👤 <b>Karta egasi:</b> <b>{esc_html(owner_name)}</b>\n\n"
                        f"📊 <b>AI MOLIYAVIY HISOB-KITOBI:</b>\n"
                        f"• Savdo summasi: <b>{fmt(deal['amount'])} so'm</b>\n"
                        f"• Kafil komissiyasi ({payout['fee_pct']}%): <b>{fmt(payout['fee'])} so'm</b>\n"
                        f"• Jami to'langan depozit: <b>{fmt(payout['total_paid'])} so'm</b>\n"
                        f"• 💵 <b>SOTUVCHIGA O'TKAZILADIGAN SUMMA:</b> <code>{fmt(payout['net_to_seller'])} so'm</code>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"Sotuvchi kartasiga pulni tashlab, quyidagi tugmani bosing:",
                        reply_markup=admin_kb
                    )
                except Exception:
                    pass
            return

    if is_buyer and any(w in text_content.lower() for w in ["men oldim", "oldim", "tekshirdim", "qabul qildim", "parol to'g'ri", "ishladi"]):
        db.q("UPDATE deals SET status='completed', updated_at=? WHERE deal_id=?", (now_s, deal["deal_id"]), commit=True)
        db.q("UPDATE users SET completed_deals=completed_deals+1 WHERE user_id=?", (deal["buyer_id"],), commit=True)
        if deal["seller_id"]:
            db.q("UPDATE users SET completed_deals=completed_deals+1 WHERE user_id=?", (deal["seller_id"],), commit=True)

        # Savdo davomida so'kinganlarni avtomatik qora ro'yxatga kiritish
        profane_str = deal.get("profane_users") or ""
        if profane_str:
            for bad_id in profane_str.split(","):
                if bad_id.strip():
                    u_bad = db.get_user(int(bad_id.strip()))
                    bad_un = u_bad["username"] if u_bad else ""
                    db.add_to_blacklist(
                        user_id=int(bad_id.strip()),
                        username=bad_un,
                        reason=f"Savdo #{deal['deal_id']} chatida so'kingan va haqorat qilgan (AI qorovul)",
                        deal_id=deal["deal_id"],
                        added_by="AI_Chat_Guardian"
                    )
                    await msg.answer(
                        f"🚫 <b>AVTOMATIK QORA RO'YXATGA KIRITILDI!</b>\n"
                        f"Foydalanuvchi @{esc_html(bad_un or bad_id)} savdo davomida so'kingani sababli "
                        f"<b>QORA RO'YXATGA (Blacklist)</b> kiritildi va botdan bloklandi!"
                    )

        finish_text = (
            f"🎉 <b>SAVDO MUVAFFAQIYATLI YAKUNLANDI! (#{deal['deal_id']})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Tovar: <b>{esc_html(deal['title'])}</b>\n"
            f"Summa: <b>{fmt(deal['amount'])} so'm</b>\n\n"
            f"Kafolatli Garant xizmatidan foydalanganingiz uchun rahmat!"
        )
        await msg.answer(finish_text, reply_markup=main_menu_keyboard(uid))
        if deal["seller_id"]:
            try:
                seller_finish = (
                    f"🎉 <b>XARIDOR TOVARNI QABUL QILDI! SAVDO YAKUNLANDI. (#{deal['deal_id']})</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Sizga o'tkaziladigan summa: <b>{fmt(deal['amount'])} so'm</b>\n\n"
                    f"💳 To'lovni olish uchun 16 xonali plastik karta raqamingiz va Ism-Familiyangizni shu yerga yozing:"
                )
                await bot.send_message(deal["seller_id"], seller_finish)
            except Exception:
                pass

# ============================ 19. RENDER DUMMY WEB SERVER ============================
async def run_render_web_server():
    try:
        async def handle_ping(request):
            return web.Response(text="OK: Kafil Escrow Bot with Gemini AI is running smoothly on Render!\n")

        app = web.Application()
        app.router.add_get("/", handle_ping)
        app.router.add_get("/health", handle_ping)
        app.router.add_get("/ping", handle_ping)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info(f"Render HTTP server '0.0.0.0':{PORT} da muvaffaqiyatli ishga tushdi.")
    except Exception as e:
        logger.warning(f"Render HTTP server ishga tushirishda xato: {e}")

# ============================ 20. ASOSIY ISHGA TUSHIRISH (MAIN) ============================
async def main():
    await run_render_web_server()
    asyncio.create_task(complaints_auto_expire_loop())
    asyncio.create_task(referral_leave_checker_loop())
    logger.info(f"Kafil / Garant Boti to'liq ishga tushdi! Adminlar: {ADMIN_IDS}")
    while True:
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            break
        except Exception as e:
            if "Unauthorized" in str(e):
                logger.critical("Bot tokeni yaroqsiz! Telegram @BotFather orqali tekshiring.")
                break
            logger.error(f"Telegram polling uzilishi: {e}. 5 soniyadan so'ng qayta ulanadi...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
