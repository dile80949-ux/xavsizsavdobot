"""
AI_HELPER.PY - Google GenAI SDK (Gemini AI) va Kuchli Intellektual Tahlil Tizimi
1. Foydalanuvchi ma'lumotlarini chuqur tekshirish va xatolarni to'g'irlash (Input Validation).
2. Chatdagi yozishmalarni real-vaqtda nazorat qiluvchi AI Qorovul (AI Chat Guardian).
3. Savdo arizalari va tilxatlarni tahlil qilib to'g'ri bo'lsa qabul qiluvchi AI (AI Statement Validator).
4. Savdolardagi nizolar va muammolarga xolis yechim topuvchi AI Arbitr (AI Dispute Solver).
"""
import asyncio
import logging
import re
from typing import Optional, Tuple, List, Dict, Any
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODELS, esc_html

logger = logging.getLogger("kafil_ai")

# Google GenAI mijozini xavfsiz ishga tushirish
_client: Optional[genai.Client] = None

def get_genai_client() -> Optional[genai.Client]:
    global _client
    if _client is None:
        try:
            if GEMINI_API_KEY and GEMINI_API_KEY.strip():
                _client = genai.Client(api_key=GEMINI_API_KEY)
                logger.info("Google GenAI mijozi muvaffaqiyatli ishga tushirildi.")
        except Exception as e:
            logger.warning(f"GenAI mijozini yaratishda xato: {e}")
    return _client


# =====================================================================
# 1. FOYDALANUVCHI MA'LUMOTLARINI TEKSHIRISH VA TO'G'IRLASH (VALIDATION)
# =====================================================================

def validate_user_input(field_type: str, raw_value: str, extra: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Any]:
    """
    Foydalanuvchi kiritgan ma'lumotlarni tekshiradi.
    Agar ma'lumot xato bo'lsa: (False, "Xatolik sababi va to'g'irlash namunasi", None)
    Agar ma'lumot to'g'ri bo'lsa: (True, "", tozalangan_qiymat)
    """
    extra = extra or {}
    text = (raw_value or "").strip()

    # 1.1 SOTUVCHI USERNAME YOKI TELEGRAM ID
    if field_type in ("seller_username", "username"):
        # URL dan tozalash (masalan https://t.me/username yoki t.me/username)
        clean = re.sub(r"^(https?:\/\/)?(www\.)?t\.me\/", "", text, flags=re.IGNORECASE)
        clean = clean.lstrip("@").strip()

        # O'z profilini kiritganini tekshirish
        self_un = (extra.get("self_username") or "").lower().lstrip("@")
        self_id = str(extra.get("self_id") or "")

        if (clean.lower() == self_un and self_un != "") or clean == self_id:
            return (
                False,
                "⚠️ <b>Siz o'zingizning profilingizni kiritdingiz!</b>\n\n"
                "Kafolatli savdo yaratish uchun sizga tovar sotayotgan <b>ikkinchi tomonning (sotuvchining)</b> "
                "Telegram @username yoki ID raqamini kiritishingiz kerak.\n\n"
                "👉 <i>Iltimos, sotuvchining ma'lumotlarini to'g'rilab qaytadan yuboring:</i>",
                None
            )

        # Raqamli Telegram ID (masalan 6125160021)
        if clean.isdigit() and len(clean) >= 5:
            return True, "", clean

        # Telegram Username tekshiruvi: 4-32 belgi, faqat harf, raqam va pastki chiziq
        if re.match(r"^[a-zA-Z0-9_]{4,32}$", clean):
            return True, "", clean

        # Noto'g'ri format bo'lsa tushuntirish berish
        return (
            False,
            "⚠️ <b>Noto'g'ri username yoki ID kiritildi!</b>\n\n"
            "Telegram username kamida 5 ta harf yoki raqamdan iborat bo'lishi lozim (bo'sh joy va ortiqcha belgilar bo'lmasligi kerak).\n"
            "📌 <b>To'g'ri namunalar:</b>\n"
            "• <code>@alisher_savdo</code>\n"
            "• <code>6125160021</code> (Telegram ID)\n\n"
            "👉 <i>Iltimos, ma'lumotni to'g'rilab qaytadan yuboring:</i>",
            None
        )

    # 1.2 MAHSULOT YOKI XIZMAT NOMI (TITLE)
    elif field_type in ("deal_title", "title"):
        if len(text) < 3:
            return (
                False,
                "⚠️ <b>Mahsulot yoki xizmat nomi juda qisqa!</b>\n\n"
                "Iltimos, sotilayotgan tovar yoki xizmat nomini aniq va tushunarli qilib yozing (kamida 3 ta belgi).\n"
                "📌 <b>Namunalar:</b>\n"
                "• <i>Telegram Kanal (50k obunachi)</i>\n"
                "• <i>PUBG Mobile Akkaunt (M416 Lednik)</i>\n"
                "• <i>Telegram bot yaratish xizmati</i>\n\n"
                "👉 <i>Iltimos, to'g'rilab qaytadan yuboring:</i>",
                None
            )
        # Faqat belgilardan iborat bo'lsa
        if not re.search(r"[a-zA-Zа-яА-Я0-9o'g'shchO'G'SHCH]", text):
            return (
                False,
                "⚠️ <b>Mahsulot nomi faqat belgilardan iborat bo'lishi mumkin emas!</b>\n\n"
                "Iltimos, mahsulot nomini so'zlar bilan aniq qilib yozing.\n"
                "👉 <i>Iltimos, to'g'rilab qaytadan yuboring:</i>",
                None
            )
        return True, "", text[:120]

    # 1.3 TO'LOV SUMMASI (AMOUNT)
    elif field_type in ("deal_amount", "amount"):
        raw_s = text.lower().replace(" ", "").replace(",", "").replace(".", "")
        # Masalan: 50k -> 50000, 100k -> 100000, 25ming -> 25000
        if raw_s.endswith("k"):
            num_part = raw_s[:-1]
            if num_part.isdigit():
                raw_s = str(int(num_part) * 1000)
        elif raw_s.endswith("ming"):
            num_part = raw_s[:-4]
            if num_part.isdigit():
                raw_s = str(int(num_part) * 1000)

        if not raw_s.isdigit():
            return (
                False,
                "⚠️ <b>Kiritilgan summa noto'g'ri!</b>\n\n"
                "Iltimos, to'lov summasini faqat butun raqamlarda kiriting (harflarsiz).\n"
                "📌 <b>Namunalar:</b> <code>15000</code> yoki <code>50000</code>\n"
                "(Minimal summa: <b>1 000 so'm</b>, maksimal: <b>100 000 so'm</b>)\n\n"
                "👉 <i>Iltimos, summani to'g'rilab qaytadan yuboring:</i>",
                None
            )

        amount = int(raw_s)
        min_limit = extra.get("min_limit", 1000)
        max_limit = extra.get("max_limit", 100000)

        if amount < min_limit or amount > max_limit:
            return (
                False,
                f"⚠️ <b>Summa belgilangan chegaradan tashqarida!</b>\n\n"
                f"Kafolatli savdo uchun minimal summa: <b>{min_limit:,} so'm</b>, "
                f"maksimal summa: <b>{max_limit:,} so'm</b>.\n"
                f"Siz kiritgan summa: <b>{amount:,} so'm</b>.\n\n"
                f"👉 <i>Iltimos, belgilangan me'yorga mos summani to'g'rilab qaytadan yuboring:</i>",
                None
            )

        return True, "", amount

    # 1.4 KARTA RAQAMI (16 XONALI)
    elif field_type in ("card_number", "card"):
        card_digits = re.sub(r"\D", "", text)
        if len(card_digits) != 16:
            return (
                False,
                f"⚠️ <b>Karta raqami xato kiritildi!</b>\n\n"
                f"Plastik karta raqami aynan <b>16 xonali</b> raqamlardan iborat bo'lishi lozim (Uzcard yoki Humo).\n"
                f"Siz kiritgan raqamlar soni: <b>{len(card_digits)} ta</b>.\n\n"
                f"📌 <b>Namuna:</b> <code>8600 1234 5678 9012</code> yoki <code>9860 0000 0000 0000</code>\n\n"
                f"👉 <i>Iltimos, 16 xonali karta raqamingizni to'g'rilab qaytadan yuboring:</i>",
                None
            )
        return True, "", card_digits

    # 1.5 KARTA EGASINING ISM-FAMILIYASI
    elif field_type in ("card_owner", "owner"):
        words = text.split()
        # Kamida 2 ta so'z (Ism va Familiya) bo'lishi, raqamlar bo'lmasligi kerak
        if len(words) < 2 or any(char.isdigit() for char in text) or len(text) < 5:
            return (
                False,
                "⚠️ <b>Karta egasining ism-familiyasi to'liq emas!</b>\n\n"
                "Iltimos, karta egasining haqiqiy to'liq Ism va Familiyasini kiriting (kamida 2 ta so'z, faqat harflar).\n"
                "📌 <b>Namuna:</b> <code>KARIMOV ANVAR</code> yoki <code>TILLAYEVA NASIBA</code>\n\n"
                "👉 <i>Iltimos, ism-familiyani to'g'rilab qaytadan yuboring:</i>",
                None
            )
        return True, "", text.upper()

    # 1.6 ARIZA / SHIKOYAT SABABI
    elif field_type in ("complaint_reason", "reason"):
        if len(text) < 15 or len(text.split()) < 3:
            return (
                False,
                "⚠️ <b>Ariza matni juda qisqa yoki yetarli emas!</b>\n\n"
                "Muammoni adolatli hal qilishimiz va sizga yordam bera olishimiz uchun nima sababdan shikoyat qilayotganingizni batafsil yozib bering (kamida 15 ta harf).\n"
                "📌 <b>Namuna:</b> <i>Sotuvchi to'lov qilinganidan so'ng va'da qilingan akkaunt parolini bermadi va aloqani uzdi.</i>\n\n"
                "👉 <i>Iltimos, arizangizni to'g'rilab batafsil yozib yuboring:</i>",
                None
            )
        return True, "", text

    # Standart
    return True, "", text


# =====================================================================
# 2. REAL-VAQTDA AI CHAT QOROVULI (AI CHAT GUARDIAN)
# =====================================================================

async def ai_chat_guardian(message_text: str, context: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, str]:
    """
    Guruhdagi va shaxsiy chatdagi savdo yozishmalarini real-vaqtda nazorat qiladi.
    Qaytaradi: (xavf_bormi: bool, xavf_turi: str, ogohlantirish_matni: str)
    """
    p = message_text.lower().strip()
    if not p:
        return False, "empty", ""

    # 2.1 BOTDAN TASHQARIDA TO'LOV QILISHGA UNDASH (Eng xavfli firibgarlik!)
    off_platform_patterns = [
        "kartamga tashla", "kartamga o'tkaz", "kartamga ur", "to'g'ridan to'g'ri tashla",
        "botga to'lama", "komissiyasi bor botni", "garantsiz qilaylik", "garantsiz savdo",
        "lichkaga o't", "lichkaga yoz", "shaxsiyga o'taylik", "directga yoz",
        "bot kerakmas", "botni o'chir", "tashqarida qilaylik"
    ]
    if any(pat in p for pat in off_platform_patterns):
        return (
            True,
            "off_platform",
            "🚨 <b>DIQQAT: AI XAVFSIZLIK QOROVULI OGOHLANTIRISHI!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Chatda savdoni kafil botidan tashqariga olib chiqish yoki to'g'ridan-to'g'ri kartaga to'lash taklifi sezildi!\n\n"
            "⚠️ <b>QAT'IY OGOHLANTIRISH:</b>\n"
            "Agar siz pulni bot hisobidan tashqarida to'lasangiz yoki shaxsiy chatga o'tsangiz, "
            "bot sizning mablag'ingizga <b>KAFOLAT BERMAYDI</b> va aldansangiz pulni qaytarib bo'lmaydi!\n\n"
            "🛡 <i>Xavfsizligingiz uchun faqat botning o'zi orqali savdo qiling!</i>"
        )

    # 2.2 MAXFIY MA'LUMOTLARNI TALAB QILISH (PHISHING)
    phishing_patterns = [
        "sms kod", "telefoningga kod", "sms keldi", "kodni ayt", "kodni ber",
        "kartang muddati", "cvv", "cvc", "parolingni ayt", "parolni ber"
    ]
    if any(pat in p for pat in phishing_patterns):
        return (
            True,
            "phishing",
            "🚨 <b>DIQQAT: MAXFIY MA'LUMOT TALAB QILINMOQDA!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Hech qachon bank kartangizga kelgan <b>SMS tasdiqlash kodini</b>, <b>CVV kodini</b> yoki "
            "shaxsiy parollaringizni begonalarga bermang!\n\n"
            "🛡 <i>Kafil boti hech qachon shaxsiy SMS tasdiqlash kodlarini so'ramaydi.</i>"
        )

    # 2.3 SOXTA CHEK YOKI TO'LOV DA'VOSI (Savdo to'lanmagan holatda)
    fake_pay_patterns = [
        "pulni tashladim tekshir", "tashlab bo'ldim qara", "chek mana",
        "to'ladim pul tushdimi", "otpravil smotri"
    ]
    deal_status = (context or {}).get("deal_status", "")
    if deal_status == "waiting_payment" and any(pat in p for pat in fake_pay_patterns):
        return (
            True,
            "payment_check",
            "⚠️ <b>AI QOROVULI ESLATMASI (Sotuvchi diqqatiga!):</b>\n"
            "Xaridor to'lov qilganini aytmoqda. Ammo bot hisobiga to'lov hali to'liq tushmadi!\n\n"
            "❗️ <b>QOIDA:</b> Faqat botning o'zi <b>«To'lov kafolatlandi va muzlatildi»</b> deb rasmiy xabar bergandagina "
            "mahsulotni topshiring. Boshqa hech qanday rasm yoki skrinshotlarga ishonmang!"
        )

    # 2.4 SAVDODA MUAMMO / NIZO ALOMATI (AI YECHIM TAVSIYASI)
    dispute_triggers = [
        "parol noto'g'ri", "parol xato", "login xato", "kirib bo'lmadi",
        "kod kelmayapti", "tovarni bermadi", "aldadi", "aldandim",
        "ishlamayapti akkaunt", "akkaunt bloklangan", "muammo bo'ldi"
    ]
    if any(pat in p for pat in dispute_triggers):
        return (
            True,
            "dispute_alert",
            "🤖 <b>AI MEDIATOR: SAVDODA MUAMMO ANIQLANDI!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Sizlarda kelishmovchilik yoki texnik muammo yuzaga kelgan ko'rinadi. "
            "Xavotir olmang, mablag' kafil hisobida xavfsiz muzlatilgan 🔒.\n\n"
            "💡 <b>AI TAVSIYA ETAYOTGAN TEZKOR YECHIMLAR:</b>\n"
            "1️⃣ <b>Sotuvchi:</b> Ma'lumotlarni qayta tekshirib, to'g'ri login/parolni yuboring.\n"
            "2️⃣ <b>Xaridor:</b> Muammoni skrinshot orqali aniq ko'rsating.\n"
            "3️⃣ Agar kelishuv imkonsiz bo'lsa, xaridor «⚖️ Arizalarim» orqali ariza qoldirsin — pul to'liq qaytariladi."
        )

    # 2.5 SO'KINISH, HAQORAT VA ODOBSIZLIK (PROFANITY DETECTION)
    profanity_words = [
        "jalap", "jallap", "onangni", "onangdi", "enangni", "skay", "sikey", "sikay", "sike",
        "sikish", "siktir", "suka", "blya", "blyad", "nahuy", "naxuy", "pizda", "pidar",
        "pidaraz", "chmo", "dalbayob", "dalbaeb", "gandon", "gandoncha", "haromi",
        "it emgan", "ammig", "amcha", "kuting", "koting", "qotoq", "kotak", "shilta"
    ]
    words_clean = set(re.findall(r"[a-zA-Zа-яА-Яo'g'shchO'G'SHCH]+", p))
    has_curse = any(w in words_clean for w in profanity_words) or any(
        phrase in p for phrase in ["onangni s", "onangdi s", "it emgan", "haromi padar", "dalbayob", "kutingga", "kotingga"]
    )
    if has_curse:
        sender_name = (context or {}).get("sender_tag", "Foydalanuvchi")
        return (
            True,
            "profanity",
            f"🚨 <b>AI QOROVULI OGOHLANTIRISHI: SO'KINISH QAT'IYAN TAQIQLANADI!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>{esc_html(sender_name)}</b>, siz savdo chatida odobsizlik va haqoratli so'z ishlatdingiz!\n\n"
            f"⚠️ <b>QAT'IY OGOHLANTIRISH:</b>\n"
            f"Kafolatli savdo qoidalariga binoan, chatda haqorat qilgan foydalanuvchilar <b>savdo yakunlanishi bilan butunlay QORA RO'YXATGA (Blacklist)</b> kiritiladi va botdan umrbod bloklanadi!\n\n"
            f"<i>Iltimos, o'zingizni madaniyatli tuting va boshqalarni hurmat qiling!</i>"
        )

    return False, "safe", ""


# =====================================================================
# 3. SAVDO BITGACH YOZILGAN ARIZALAR / TILXATLARNI TEKSHIRUVCHI AI
# =====================================================================

def verify_statement_with_ai(statement_text: str, role: str, deal_info: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """
    Savdo yakunida xaridor yoki sotuvchi yozgan tilxat/arizani o'qiydi.
    Agar haqiqatda mahsulot olinganini va roziligini bildirgan bo'lsa -> (True, "Xulosa")
    Agar e'tiroz, aldov yoki rad javobi bo'lsa -> (False, "Rad sababi")
    """
    text = (statement_text or "").strip().lower()
    role = role.lower()

    if len(text) < 3:
        return False, "⚠️ <b>AI Tahlili:</b> Tilxat-ariza matni juda qisqa yoki bo'sh."

    # XARIDOR ARIZASI TEKSHIRUVI
    if role in ("buyer", "xaridor", "oluvchi"):
        # E'tiroz, shikoyat yoki rad alomatlari
        rejection_words = [
            "yo'q", "olmadim", "bermadi", "xato", "noto'g'ri", "aldadi",
            "ishlamadi", "da'voyim bor", "noroziman", "qaytaring", "ishlamayapti", "parol xato"
        ]
        if any(w in text for w in rejection_words):
            return (
                False,
                "⚠️ <b>AI Tahlili:</b> Xaridor mahsulot to'liq olinmaganini yoki e'tirozi borligini bildirdi! "
                "Savdo yakunlanmadi va mablag' kafil hisobida xavfsiz saqlanmoqda."
            )

        # Ijobiy tasdiq alomatlari (keng qamrovli)
        approval_words = [
            "oldim", "qabul qildim", "tekshirdim", "rozi", "roziman", "da'voyim yo'q", "da'vom yo'q",
            "bot javob bermaydi", "hammasi joyida", "hammasi to'g'ri", "o'tdi", "raxmat", "rahmat",
            "to'liq qabul", "muammo yo'q", "ha oldim", "ishladi", "parol to'g'ri", "akkaunt oldim",
            "kod keldi", "tasdiqlayman", "hammasi zo'r", "ha oldim rahmat"
        ]
        if any(w in text for w in approval_words):
            return (
                True,
                "✅ <b>AI Tasdiqladi:</b> Xaridor mahsulotni to'liq qabul qilib olganini va e'tirozi yo'qligini tasdiqladi."
            )

        return (
            False,
            "⚠️ <b>AI Tahlili:</b> Kiritilgan matnda mahsulotni qabul qilib olganingiz aniq ifodalanmagan.\n"
            "Iltimos, «Men mahsulotni to'liq qabul qilib oldim, e'tirozim yo'q» deb yozing."
        )

    # SOTUVCHI ARIZASI TEKSHIRUVI
    elif role in ("seller", "sotuvchi"):
        warranty_words = [
            "qora ro'yxat", "qaytarib olsam", "aldov qilsam", "halol",
            "topshirdim", "berdim", "kafolat beraman", "roziman", "javob beraman",
            "qaytarib olmayman", "aldamayman", "kafolat"
        ]
        if any(w in text for w in warranty_words):
            return (
                True,
                "✅ <b>AI Tasdiqladi:</b> Sotuvchi mahsulot topshirilganini va halollik kafolatini tasdiqladi."
            )

        return (
            False,
            "⚠️ <b>AI Tahlili:</b> Sotuvchi kafolati aniq emas. Iltimos, «Agar mahsulotni qaytarib olsam yoki aldov qilsam, qora ro'yxatga solishingizga roziman» deb tasdiqlang."
        )

    return True, "Tasdiqlandi."

async def verify_statement_with_ai_async(statement_text: str, role: str, deal_info: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Gemini AI va Smart Local Brain orqali arizani tahlil qilish."""
    client = get_genai_client()
    if client:
        try:
            d_title = deal_info.get("title", "Tovar") if deal_info else "Tovar"
            prompt = (
                f"Siz kafolatli savdo boti moderatorisiz. Foydalanuvchi roli: '{role}'. Tovar: '{d_title}'.\n"
                f"Foydalanuvchi yozgan xabar: \"{statement_text}\"\n\n"
                f"Savol: Foydalanuvchi mahsulotni/akkauntni to'liq olganini yoki kafolat berganini tasdiqlayaptimi?\n"
                f"Javob faqat bitta qatorda bo'lsin: TASDIQLANDI yoki RAD_ETILDI, va qisqa sababi."
            )
            res = await asyncio.to_thread(client.models.generate_content, model=GEMINI_MODEL, contents=prompt)
            if res and res.text:
                if "TASDIQLANDI" in res.text.upper():
                    return True, f"✅ <b>AI Tasdiqladi:</b> {role.capitalize()} arizasi Gemini AI tomonidan tekshirilib, tasdiqlandi."
                elif "RAD_ETILDI" in res.text.upper():
                    return False, f"⚠️ <b>AI Tahlili:</b> Ariza rad etildi. {res.text}"
        except Exception as e:
            logger.warning(f"verify_statement_with_ai_async xatosi: {e}")

    return verify_statement_with_ai(statement_text, role, deal_info)

def calculate_payout_details(deal: Dict[str, Any], buyer_completed_deals: int = 0) -> Dict[str, Any]:
    """
    Savdo yakunlanganda sotuvchiga o'tkazilishi kerak bo'lgan summani aniq hisoblaydi.
    1-savdo bo'lsa: 0% komissiya.
    Keyingi savdo bo'lsa: 10% komissiya (yoki bazadagi sozlama).
    Sotuvchi oladigan sof summa = deal['amount'].
    """
    amount = int(deal.get("amount", 0))
    is_first = (buyer_completed_deals == 0)
    fee_pct = 0 if is_first else 10
    fee = 0 if is_first else int(amount * (fee_pct / 100.0))
    total_paid = int(deal.get("total_to_pay") or (amount + fee))
    net_to_seller = amount

    return {
        "amount": amount,
        "fee": fee,
        "fee_pct": fee_pct,
        "total_paid": total_paid,
        "net_to_seller": net_to_seller,
        "is_first": is_first
    }


# =====================================================================
# 4. SAVDODAGI MUAMMOLARGA YECHIM TOPUVCHI AI ARBITR (AI DISPUTE SOLVER)
# =====================================================================

def ai_dispute_solver(deal_info: Dict[str, Any], complaint_reason: str) -> Dict[str, Any]:
    """
    Savdoda muammo chiqqanda har ikkala tomon va Admin uchun xolis yechim va reja ishlab chiqadi.
    """
    d_title = deal_info.get("title", "Mahsulot")
    d_amount = deal_info.get("amount", 0)
    buyer_un = deal_info.get("buyer_username", "Xaridor")
    seller_un = deal_info.get("seller_username", "Sotuvchi")
    c_lower = complaint_reason.lower()

    issue_type = "Umumiy kelishmovchilik"
    solution_plan = []
    verdict_suggestion = ""

    if any(w in c_lower for w in ["parol", "login", "kod", "kirib bo'lmadi", "noto'g'ri", "xato"]):
        issue_type = "Akkaunt ma'lumotlari xatosi (Login/Parol muammosi)"
        solution_plan = [
            "🔹 <b>1-Qadam (Tekshirish):</b> Sotuvchi 24 soat ichida haqiqiy va ishlaydigan login/parolni qayta taqdim etishi shart.",
            "🔹 <b>2-Qadam (Skrinshot):</b> Xaridor akkauntga kirishdagi aniq xatolik skrinshotini taqdim etsin.",
            "🔹 <b>3-Qadam (Murosa):</b> Agar ma'lumotlar ishlamasa, savdo bekor qilinadi va 100% pul xaridorga qaytariladi."
        ]
        verdict_suggestion = "Sotuvchiga to'g'ri ma'lumotlarni berish uchun 24 soat berilsin. Berilmasa — pul xaridorga qaytarilsin."

    elif any(w in c_lower for w in ["bermadi", "tovarni bermadi", "yo'qolib qoldi", "javob bermayapti", "aloqaga chiqmayapti"]):
        issue_type = "Tovarni topshirmaslik va aloqani uzish"
        solution_plan = [
            "🔹 <b>1-Qadam (Tezkor ogohlantirish):</b> Sotuvchiga zudlik bilan tovarni topshirish talabi yuborilsin.",
            "🔹 <b>2-Qadam (Muhlat):</b> Sotuvchiga javob berish uchun qat'iy 5 kun muhlat belgilanadi.",
            "🔹 <b>3-Qadam (Qora ro'yxat va Refund):</b> 5 kun ichida javob bo'lmasa, sotuvchi firibgar deb topilib, doimiy QORA RO'YXATGA kiritiladi va pul xaridorga 100% qaytariladi."
        ]
        verdict_suggestion = "Sotuvchiga ogohlantirish yuborilsin. Tovar topshirilmasa, xaridorga to'liq to'lov qaytarilsin."

    else:
        issue_type = "Shartnoma shartlarining buzilishi"
        solution_plan = [
            "🔹 <b>1-Qadam (Muloqot):</b> Tomonlar kafil ishtirokida muammoni o'zaro kelishib hal qilishlari mumkin.",
            "🔹 <b>2-Qadam (Kelishuv asosida bekor qilish):</b> Har ikki tomon rozi bo'lsa, bitim zararsiz bekor qilinadi.",
            "🔹 <b>3-Qadam (Admin aralashuvi):</b> Nizo Garant Admini tomonidan hujjatlar asosida ko'rib chiqiladi."
        ]
        verdict_suggestion = "Tomonlarning dalillari asosida ko'rib chiqilib, pul kafil depozitida muzlatib turilsin."

    full_message = (
        f"🤖 <b>AI NIZOLAR EKSPERTIZASI VA XOLIS YECHIM</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Bitim:</b> {esc_html(d_title)} (<b>{d_amount:,} so'm</b>)\n"
        f"🛒 <b>Xaridor:</b> @{esc_html(buyer_un)}\n"
        f"💼 <b>Sotuvchi:</b> @{esc_html(seller_un)}\n"
        f"📌 <b>Muammo turi:</b> <b>{issue_type}</b>\n\n"
        f"📝 <b>Ariza sababi:</b>\n<i>«{esc_html(complaint_reason)}»</i>\n\n"
        f"⚖️ <b>AI TAKLIF ETAYOTGAN ADOLATLI YECHIM REJASI:</b>\n"
        + "\n".join(solution_plan) + "\n\n"
        f"💡 <b>Admin uchun tavsiya:</b> {verdict_suggestion}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Kafil boti har ikki tomonning manfaatlarini to'liq himoya qiladi!</i>"
    )

    return {
        "issue_type": issue_type,
        "solution_plan": solution_plan,
        "verdict_suggestion": verdict_suggestion,
        "full_message": full_message
    }


# =====================================================================
# 5. SMART LOCAL BRAIN & GEMINI AI INTEGRATSIYASI
# =====================================================================

def smart_local_brain(prompt: str) -> str:
    """
    O'zbek tilidagi eng keng qamrovli Kafil / Garant intellektual bilimlar bazasi.
    """
    p = prompt.lower().strip()

    # 1. ADMIN TRIGGER
    admin_explicit_triggers = [
        "adminga yoz", "adminga yubor", "adminga jonat", "adminga jo'nat",
        "admin bilan gaplash", "operator bilan", "inson bilan", "operatorga",
        "arizamni adminga", "shikoyatni adminga", "admin raqami", "admin lichkasi"
    ]
    if any(t in p for t in admin_explicit_triggers):
        return (
            "ADMIN_GA_UZATISH\n"
            "👨‍💼 <b>Sizning murojaatingiz Garant Administratsiyasiga uzatilmoqda.</b>\n"
            "Admin tez orada xabaringizni ko'rib chiqadi va javob yo'llaydi."
        )

    # 2. QANDAY ISHLAYDI
    if any(w in p for w in ["qanday ishlaydi", "kafil nima", "garant nima", "tizim qanday", "qoidalar", "bot vazifasi", "sxema"]):
        return (
            "🛡 <b>KAFIL / GARANT BOTI QANDAY ISHLAYDI?</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Bizning bot — O'zbekistondagi eng xavfsiz va ishonchli <b>Kafolatli Savdo (Escrow)</b> tizimidir. "
            "U xaridor va sotuvchi o'rtasidagi firibgarlikning oldini oladi:\n\n"
            "1️⃣ <b>Savdo yaratish:</b> Xaridor «🤝 Yangi Savdo Ochish» tugmasini bosib, tovar nomi, narxi va sotuvchining @username'ini kiritadi.\n"
            "2️⃣ <b>To'lov va muzlatish:</b> Xaridor kafil kartasiga to'lov qiladi. Mablag' sotuvchiga emas, balki kafil xavfsiz hisobida <b>muzlatiladi (bloklanadi)</b>.\n"
            "3️⃣ <b>Tovar topshirish:</b> Sotuvchi tovar yoki xizmatni xaridorga to'liq topshiradi.\n"
            "4️⃣ <b>Tekshiruv va tasdiq:</b> Xaridor tovarni to'liq tekshirib, rozilik berganidan keyingina pul sotuvchiga o'tkazib beriladi.\n\n"
            "🎁 <b>BIRINCHI SAVDO — 0% MUTLAQO BEPUL!</b>\n"
            "Keyingi savdolarda esa atigi 10% komissiya olinadi yoki 10% keshbek beriladi."
        )

    # 3. KESHBEK VA KOMISSIYA
    if any(w in p for w in ["keshbek", "cashback", "bepul", "tekin", "komissiya", "foiz", "0%", "10%"]):
        return (
            "💎 <b>KESHBEK VA KOMISSIYA TIZIMI:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🎁 <b>Birinchi savdo:</b>\n"
            "Har bir yangi foydalanuvchi uchun birinchi bitim <b>MUTLAQO BEPUL (0% komissiya)</b>!\n\n"
            "💰 <b>10% Keshbek:</b>\n"
            "Keyingi barcha muvaffaqiyatli yakunlangan savdolardan so'ng, tizim tomonidan sizning "
            "ichki balansingizga savdo summasidan <b>10% Keshbek</b> qo'shib beriladi!\n\n"
            "🛍 Keshbekingizni keyingi savdolarda chegirma sifatida bemalol ishlatishingiz mumkin."
        )

    # 4. TO'LOV VA KARTA
    if any(w in p for w in ["to'lov", "tulov", "pul o'tkazish", "karta", "pul tushmadi", "5 minut", "5 daqiqa", "qanday to'layman"]):
        return (
            "💳 <b>TO'LOV QILISH TARTIBI VA QOIDALARI:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "To'lovlar avtomatlashtirilgan xavfsiz <b>TolovAPI.uz</b> orqali qabul qilinadi:\n\n"
            "📌 <b>Asosiy qoidalar:</b>\n"
            "• Bot sizga to'lov uchun 16 xonali karta raqami va aniq summani chiqarib beradi.\n"
            "• ⚠️ <b>DIQQAT:</b> Ko'rsatilgan summadan 1 so'm ham kam yoki ortiq to'lamang! Aynan belgilangan summani o'tkazing.\n"
            "• ⏱ To'lov uchun <b>5 daqiqa</b> vaqt beriladi. 5 daqiqa ichida to'lov qilinmasa, bitim avtomatik bekor bo'ladi.\n"
            "• Pulni o'tkazganingizdan so'ng, «✅ To'lov qildim (Tekshirish)» tugmasini bossangiz, tizim to'lovni tasdiqlaydi."
        )

    # 5. FIRIBGARLIK VA NIZOLAR
    if any(w in p for w in ["aldadi", "shikoyat", "nizo", "firibgar", "mashenik", "tovarni bermadi", "aldandim", "qora ro'yxat"]):
        return (
            "🚨 <b>FIRIBGARLIK VA NIZOLARNI HAL QILISH:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Agar sotuvchi sizni aldasa, tovarni bermasa yoki kelishuvni buzsa:\n\n"
            "1️⃣ Xaridor «✅ Tovarni oldim» deb tasdiqlamaguncha, <b>pul sotuvchiga o'tmaydi</b> va kafil hisobida xavfsiz saqlanadi.\n"
            "2️⃣ «🆘 SOS (Yordam)» bo'limidagi <b>«🚨 Meni sotuvchi aldadi»</b> tugmasi orqali sotuvchining @username'ini yozib ariza qoldiring.\n"
            "3️⃣ Bot sotuvchiga darhol ogohlantirish yuboradi. Sotuvchiga masalani hal qilish uchun <b>5 kun</b> vaqt beriladi.\n"
            "4️⃣ Agar 5 kun ichida muammoni hal qilmasa, uning Telegram ID raqami butunlay <b>QORA RO'YXATGA</b> kiritiladi va pul xaridorga qaytarib (refund) beriladi!"
        )

    # 6. REFERAL
    if any(w in p for w in ["referal", "do'st", "pul ishlash", "100 so'm", "10000", "yechish", "pul yechish", "taklif"]):
        return (
            "👥 <b>REFERAL TIZIMI VA PUL YECHISH:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Siz do'stlaringizni taklif qilib sof daromad topishingiz mumkin:\n\n"
            "💵 <b>Har bir do'st uchun:</b> +100 so'm beriladi.\n"
            "📌 <b>Shart:</b> Taklif qilingan do'stingiz botga kirib, majburiy homiy kanallarga to'liq a'zo bo'lishi lozim.\n"
            "⚡️ <b>Kunlik limit:</b> Kuniga maksimal 20 tagacha do'stingizni qo'sha olasiz.\n"
            "⚠️ <b>Jarima:</b> Agar chaqirgan odamingiz kanaldan chiqib ketsa, balansingizdan -200 so'm jarima ayiriladi.\n"
            "💳 <b>Pul yechish:</b> Minimal summa qat'iy <b>10 000 so'm</b>. Unga yetgach «💸 Pulni yechish» tugmasi orqali kartangizga chiqarib olasiz."
        )

    # 7. GURUHDA SAVDO
    if any(w in p for w in ["guruh", "chat", "kafil-", "kod", "guruhda savdo", "admin qilish"]):
        return (
            "🤝 <b>GURUHDA AI YORDAMIDA SAVDO QILISH:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "1️⃣ Botda «🤝 Yangi Savdo Ochish» -> «👥 AI yordamida guruhda» bandini tanlang.\n"
            "2️⃣ Bot sizga unikal <b>KAFIL-XXXXX</b> kodini beradi.\n"
            "3️⃣ Botni o'zingizning savdo guruhingizga qo'shing va unga <b>Adminlik huquqini</b> bering.\n"
            "4️⃣ Guruhga bot bergan <b>KAFIL-XXXXX</b> kodini yuboring.\n"
            "5️⃣ Bot guruhda faollashadi, barcha xabarlarni kuzatadi va savdoni xavfsiz yakunlaydi!"
        )

    # 8. SALOMLASHISH
    if any(w in p for w in ["salom", "assalom", "qalaysiz", "kim bu", "yordam ber", "start"]):
        return (
            "👋 <b>Assalomu alaykum!</b>\n"
            "Men <b>Kafil (Garant)</b> rasmiy intellektual assistentiman 🤖\n\n"
            "Qanday savolingiz bor? Men sizga quyidagilar bo'yicha to'liq yordam bera olaman:\n"
            "• 🤝 Xavfsiz savdo ochish va yakunlash tartibi\n"
            "• 💳 To'lov qilish va kafil hisobida pulni muzlatish\n"
            "• 🎁 0% tekin birinchi savdo va 10% keshbek\n"
            "• 👥 Referal orqali pul ishlash va kartaga yechish\n"
            "• 🚨 Firibgarlardan himoyalanish va ariza qoldirish\n\n"
            "<i>Savolingizni bemalol yozib qoldiring!</i>"
        )

    # 9. BOSHQACHA SAVOLLAR
    return (
        f"🤖 <b>KAFIL ASSISTENTI JAVOBI:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Sizning savolingiz: <i>«{prompt}»</i>\n\n"
        f"💡 Bizning Kafolatli Savdo tizimimizda barcha bitimlar 100% xavfsiz o'tkaziladi. "
        f"Xaridor pul to'laganda mablag' kafil hisobida xavfsiz muzlatiladi va faqat tovar to'liq tekshirib "
        f"olingachgina sotuvchiga beriladi.\n\n"
        f"📌 Agar sizga botning aniq qoidalari kerak bo'lsa:\n"
        f"• <b>Savdo bo'yicha:</b> «🤝 Yangi Savdo Ochish» menyusidan foydalaning.\n"
        f"• <b>Shikoyat bo'yicha:</b> «🚨 Meni sotuvchi aldadi» tugmasini bosing.\n"
        f"• <b>Admin yordami kerak bo'lsa:</b> Quyidagi «👨‍💼 Adminga yuborish» tugmasini bosing."
    )

def _call_gemini_sync(prompt: str, system_instruction: str = "") -> str:
    client = get_genai_client()
    if not client:
        return smart_local_brain(prompt)

    models_to_try = [GEMINI_MODEL] + GEMINI_FALLBACK_MODELS
    for m in models_to_try:
        try:
            full_prompt = prompt
            if system_instruction:
                full_prompt = f"System Instruction: {system_instruction}\n\nUser Question: {prompt}"
            res = client.models.generate_content(model=m, contents=full_prompt)
            if res and res.text:
                return res.text.strip()
        except Exception as e:
            logger.warning(f"Model {m} bilan xato: {e}")

    return smart_local_brain(prompt)

async def ask_gemini_ai(prompt: str, system_instruction: str = "") -> str:
    """Asinxron Gemini AI va Smart Local Brain integratsiyasi."""
    sys_inst = (
        "Siz O'zbekistondagi eng mashhur Kafil / Garant (Escrow) botining rasmiy aqlli assistentisiz. "
        "Foydalanuvchilarga do'stona, aniq, qalin sarlavhalar va chiroyli emojilar bilan to'liq o'zbek tilida javob bering. "
        "Matnlarda ortiqcha kod (pre/code) ishlatmang. "
        "Faqat foydalanuvchi qat'iy adminga yozishni so'raganida yoki jiddiy nizo bo'lganda 'ADMIN_GA_UZATISH' kalit so'zini kiriting."
    )
    if system_instruction:
        sys_inst = f"{sys_inst} {system_instruction}"

    return await asyncio.to_thread(_call_gemini_sync, prompt, sys_inst)

async def check_chat_safety(message_text: str) -> Tuple[bool, str]:
    """Eski muvofiqlik uchun sodda funksiya."""
    is_alert, alert_type, alert_msg = await ai_chat_guardian(message_text)
    if is_alert:
        return False, alert_msg
    return True, "Xavfsiz"

async def analyze_deals_for_admin() -> str:
    """Admin uchun savdolar statistikasi hisoboti."""
    import database as db
    total_u = db.q("SELECT COUNT(*) c FROM users", one=True)["c"]
    total_d = db.q("SELECT COUNT(*) c FROM deals", one=True)["c"]
    comp_d = db.q("SELECT COUNT(*) c FROM deals WHERE status='completed'", one=True)["c"]
    active_d = db.q("SELECT COUNT(*) c FROM deals WHERE status IN ('in_trade', 'waiting_payment')", one=True)["c"]
    total_sum = db.q("SELECT SUM(amount) s FROM deals WHERE status='completed'", one=True)["s"] or 0

    return (
        "📊 <b>KAFIL BOTI STATISTIK HISOBOTI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Jami a'zolar:</b> {total_u} nafar\n"
        f"📦 <b>Jami yaratilgan bitimlar:</b> {total_d} ta\n"
        f"✅ <b>Muvaffaqiyatli yakunlangan:</b> {comp_d} ta\n"
        f"🔒 <b>Hozirda faol/muzlatilgan:</b> {active_d} ta\n"
        f"💰 <b>Aylangan umumiy summa:</b> <b>{total_sum:,} so'm</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡️ Tizim to'liq barqaror ishlamoqda."
    )

