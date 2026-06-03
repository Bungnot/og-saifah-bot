import os
import re
import json
import time
import uuid
import tempfile
import threading
import hashlib
from decimal import Decimal, InvalidOperation
from datetime import datetime
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from dotenv import load_dotenv
from flask import Flask, request, abort
import requests

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer,
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent,
    PostbackEvent,
    FollowEvent,
    JoinEvent,
    MemberJoinedEvent,
)

load_dotenv()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
ADMIN_USER_IDS = set(
    x.strip() for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()
)
# กลุ่มหลังบ้านที่อนุญาตให้ใช้คำสั่งระบบ เช่น CK / ยอดกำไร / $+ $- / ล้างออเดอร์
# ใส่ BACKOFFICE_GROUP_ID ใน .env ได้เหมือนเดิม และมีค่า default ตามกลุ่มหลังบ้านที่กำหนดไว้
DEFAULT_BACKOFFICE_GROUP_ID = "Cb890e7385cd34ac7b0d910bff7749540"
BACKOFFICE_GROUP_ID = os.getenv("BACKOFFICE_GROUP_ID", "").strip()
BACKOFFICE_GROUP_IDS = set(
    x.strip() for x in BACKOFFICE_GROUP_ID.split(",") if x.strip()
)
BACKOFFICE_GROUP_IDS.add(DEFAULT_BACKOFFICE_GROUP_ID)
# ใส่ groupId/roomId ของกลุ่มหน้าบ้านที่อนุญาตให้เปิด/ปิด/เล่นรอบได้ คั่นด้วย comma
# ถ้าไม่ตั้งค่าไว้ ระบบจะถือว่าทุกกลุ่ม/room ที่ไม่ใช่ BACKOFFICE_GROUP_ID เป็นหน้าบ้านได้
FRONT_GROUP_IDS = set(
    x.strip() for x in os.getenv("FRONT_GROUP_IDS", "").split(",") if x.strip()
)

USER_DB_FILE = os.getenv("USER_DB_FILE", "users.json")
PROFIT_DB_FILE = os.getenv("PROFIT_DB_FILE", "profit.json")
ORDER_DB_FILE = os.getenv("ORDER_DB_FILE", "order_state.json")
ORDER_START_NO = int(os.getenv("ORDER_START_NO", "1"))
SLIP_TOPUP_DB_FILE = os.getenv("SLIP_TOPUP_DB_FILE", "slip_topups.json")
ADMIN_DB_FILE = os.getenv("ADMIN_DB_FILE", "admins.json")
# ======================================================
# Round auto-backup settings
# สำรองข้อมูลรอบ / โพสต์แผล / คู่ที่ติดกัน ลงไฟล์อัตโนมัติ
# เพื่อกันข้อมูลหายเมื่อบอทค้าง รีสตาร์ท หรือเครื่องดับ
# ======================================================
ROUND_BACKUP_ENABLED = os.getenv("ROUND_BACKUP_ENABLED", "1") == "1"
# ใช้หยุดการ auto backup ชั่วคราวหลังคำสั่งล้าง round_backups
# กันเคสลบไฟล์แล้วบอทสร้างไฟล์ใหม่ทันทีตอน reply ข้อความกลับ LINE
ROUND_BACKUP_SUPPRESS_UNTIL = 0
# แยก backup ตามรอบ ไม่รวมทุกอย่างไว้ไฟล์เดียว
# ตัวอย่างไฟล์: round_backups/round_base1_xxxxx.json
ROUND_BACKUP_DIR = os.getenv("ROUND_BACKUP_DIR", "round_backups")
# ใช้เฉพาะอ่านไฟล์ backup แบบเก่าเพื่อ migration/fallback เท่านั้น
ROUND_BACKUP_DB_FILE = os.getenv("ROUND_BACKUP_DB_FILE", "round_backup.json")

# ======================================================
# EasySlip auto top-up settings
# ตั้งค่าใน .env:
#   EASYSLIP_API_KEY=<API Key จากหน้า EasySlip>
#   EASYSLIP_ACCOUNT_NUMBER=<เลขบัญชีผู้รับ สำหรับ checkReceiver>
# ======================================================
EASYSLIP_ENABLED = os.getenv("EASYSLIP_ENABLED", "1") == "1"
EASYSLIP_API_KEY = os.getenv("EASYSLIP_API_KEY", "").strip()
EASYSLIP_API_URL = os.getenv("EASYSLIP_API_URL", "https://developer.easyslip.com/api/v1/verify").strip()
# เลขบัญชีผู้รับที่ต้องตรงกับสลิป (ถ้าไม่ตั้งค่าไว้จะไม่เช็คบัญชีผู้รับ)
EASYSLIP_ACCOUNT_NUMBER = os.getenv("EASYSLIP_ACCOUNT_NUMBER", "").strip()
EASYSLIP_ACCOUNT_NAME_TH = os.getenv("EASYSLIP_ACCOUNT_NAME_TH", "").strip()
EASYSLIP_ACCOUNT_NAME_EN = os.getenv("EASYSLIP_ACCOUNT_NAME_EN", "").strip()
EASYSLIP_CONNECT_TIMEOUT_SECONDS = float(os.getenv("EASYSLIP_CONNECT_TIMEOUT_SECONDS", "5"))
EASYSLIP_TIMEOUT_SECONDS = float(os.getenv("EASYSLIP_TIMEOUT_SECONDS", "20"))
EASYSLIP_API_RETRIES = int(os.getenv("EASYSLIP_API_RETRIES", "2"))
EASYSLIP_API_RETRY_DELAY_SECONDS = float(os.getenv("EASYSLIP_API_RETRY_DELAY_SECONDS", "1.0"))
EASYSLIP_DEBUG_MODE = os.getenv("EASYSLIP_DEBUG_MODE", "1") == "1"
# ตรวจภาพก่อนส่งเข้า EasySlip ด้วย QR gate
# ปิดเป็นค่าเริ่มต้น เพราะรูปสลิปจาก LINE บางครั้งถูกบีบอัด/QR เล็ก ทำให้ OpenCV ตรวจไม่เจอและบอทเงียบ
SLIP_IMAGE_QR_GATE_ENABLED = os.getenv("SLIP_IMAGE_QR_GATE_ENABLED", "0") == "1"

# ── ตัวแปร Slip2Go ที่ยังถูกอ้างถึงในฟังก์ชันเก่า (stub เพื่อไม่ให้ crash) ──────
SLIP2GO_ENABLED = False
SLIP2GO_API_URL = ""
SLIP2GO_API_TOKEN = ""
SLIP2GO_AUTH_HEADER_NAME = "Authorization"
SLIP2GO_AUTH_PREFIX = "Bearer"
SLIP2GO_IMAGE_FIELD = "file"
SLIP2GO_CONNECT_TIMEOUT_SECONDS = 5.0
SLIP2GO_TIMEOUT_SECONDS = 20.0
SLIP2GO_API_RETRIES = 2
SLIP2GO_API_RETRY_DELAY_SECONDS = 1.0
SLIP2GO_REQUIRE_RECEIVER_TEXT = ""
SLIP2GO_CHECK_DUPLICATE = False
SLIP2GO_RECEIVER_ACCOUNT_NUMBER = ""
SLIP2GO_RECEIVER_ACCOUNT_TYPE = ""
SLIP2GO_RECEIVER_ACCOUNT_NAME_TH = ""
SLIP2GO_RECEIVER_ACCOUNT_NAME_EN = ""
SLIP2GO_RECEIVER_ACCOUNTS = ""
SLIP2GO_RECEIVER_ACCOUNTS_JSON = ""
SLIP2GO_DEBUG_MODE = False
SLIP2GO_NOTIFY_NOT_FOUND = False
# 1 บาท = 1 เครดิต เป็นค่าเริ่มต้น ถ้าต้องการ 1 บาท = 100 เครดิต ให้ตั้ง AUTO_TOPUP_RATE=100
AUTO_TOPUP_RATE = Decimal(os.getenv("AUTO_TOPUP_RATE", "1"))
MIN_TOPUP_AMOUNT = Decimal(os.getenv("MIN_TOPUP_AMOUNT", "1"))

COMMISSION_PERCENT = int(os.getenv("COMMISSION_PERCENT", "10"))
# อายุคำขอยืนยัน CR เพื่อกันแอดมินพิมพ์ "ยืนยัน" ผิดจังหวะแล้วล้างรอบย้อนหลัง
CLEAR_CONFIRM_TTL_SECONDS = int(os.getenv("CLEAR_CONFIRM_TTL_SECONDS", "120"))
# อายุคำขอยืนยันย้อนผล เพื่อกันแอดมินพิมพ์ยืนยันผิดจังหวะ
ROLLBACK_CONFIRM_TTL_SECONDS = int(os.getenv("ROLLBACK_CONFIRM_TTL_SECONDS", "120"))
PROFILE_REFRESH_SECONDS = int(os.getenv("PROFILE_REFRESH_SECONDS", "86400"))
PUSH_WORKERS = int(os.getenv("PUSH_WORKERS", "10"))

# ======================================================
# Named round mode
# เปิดหลายค่ายพร้อมกันได้โดยให้แอดมินเรียกใช้ชื่อค่ายแทนคำว่า ฐาน1/ฐาน2
# ภายในยังเก็บ base_no ไว้แยกรอบและกันบิลทับกัน แต่ข้อความหน้าบ้านจะแสดงชื่อค่ายเป็นหลัก
# ตั้ง USE_CAMP_NAME_LABELS=0 ถ้าต้องการกลับไปโชว์ฐานแบบเดิม
# ======================================================
USE_CAMP_NAME_LABELS = os.getenv("USE_CAMP_NAME_LABELS", "1") == "1"

# ลดอาการบอทตอบช้า: ใช้ HTTP timeout สั้นสำหรับ reply/push ไป LINE
# ถ้า LINE API หน่วง จะไม่ลาก webhook ค้างนานจนคำสั่งถัดไปแซงคำสั่งก่อนหน้า
LINE_REPLY_TIMEOUT_SECONDS = float(os.getenv("LINE_REPLY_TIMEOUT_SECONDS", "4"))
LINE_PUSH_TIMEOUT_SECONDS = float(os.getenv("LINE_PUSH_TIMEOUT_SECONDS", "6"))
# แยก connect timeout/read timeout ชัดเจน เพื่อไม่ให้ค้าง connect นานเกินไปตอน api.line.me หน่วง
LINE_CONNECT_TIMEOUT_SECONDS = float(os.getenv("LINE_CONNECT_TIMEOUT_SECONDS", "3"))
# จำนวนครั้งที่ลองส่งซ้ำเมื่อ LINE API timeout หรือ 5xx
LINE_API_RETRIES = int(os.getenv("LINE_API_RETRIES", "2"))
LINE_API_RETRY_DELAY_SECONDS = float(os.getenv("LINE_API_RETRY_DELAY_SECONDS", "0.35"))
# ดึงชื่อโปรไฟล์ LINE ด้วย HTTP timeout สั้นและมี circuit breaker กันยิงซ้ำรัว ๆ
LINE_PROFILE_ENABLED = os.getenv("LINE_PROFILE_ENABLED", "1") == "1"
LINE_PROFILE_TIMEOUT_SECONDS = float(os.getenv("LINE_PROFILE_TIMEOUT_SECONDS", "2.5"))
LINE_PROFILE_COOLDOWN_SECONDS = int(os.getenv("LINE_PROFILE_COOLDOWN_SECONDS", "120"))

# ======================================================
# Quiet group mode
# โหมดนี้ทำให้บอทสนใจเฉพาะข้อความที่เป็นแผลเล่น/ติดจับคู่/คำสั่งรอบจริง ๆ
# ลดการตอบข้อความรบกวนในกลุ่มที่ลูกค้าพิมพ์คุยกันรัว ๆ
# ======================================================
QUIET_GROUP_MODE = os.getenv("QUIET_GROUP_MODE", "1") == "1"
QUIET_IGNORE_WRONG_REPLY = os.getenv("QUIET_IGNORE_WRONG_REPLY", "1") == "1"
# ถ้าลูกค้า reply โพสต์แผล/ข้อความติดด้วยคำที่ไม่ใช่คีย์ ให้แจ้งวิธีพิมพ์ให้ถูก
# แต่ไม่บันทึก pending ใด ๆ เพื่อให้ลูกค้ากลับไป reply ด้วย ต/ติด ได้ตามปกติ
QUIET_WARN_INVALID_REPLY_TO_PLAY = os.getenv("QUIET_WARN_INVALID_REPLY_TO_PLAY", "1") == "1"
LINE_API_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_API_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_API_PROFILE_URL = "https://api.line.me/v2/bot/profile/{user_id}"
LINE_API_GROUP_MEMBER_PROFILE_URL = "https://api.line.me/v2/bot/group/{group_id}/member/{user_id}"
LINE_API_ROOM_MEMBER_PROFILE_URL = "https://api.line.me/v2/bot/room/{room_id}/member/{user_id}"
LINE_HTTP_SESSION = requests.Session()

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
app = Flask(__name__)

# ส่ง Flex / Push แบบไม่บล็อก webhook นานเกินไป
EXECUTOR = ThreadPoolExecutor(max_workers=PUSH_WORKERS)

# กัน webhook หลายรายการประมวลผล STATE พร้อมกันจนผล/ยืนยันผลสลับลำดับ
STATE_LOCK = threading.RLock()
FILE_LOCK = threading.RLock()

# กัน LINE retry / network duplicate ทำให้คำสั่งเดิมถูกคิดซ้ำ
PROCESSED_MESSAGE_IDS = {}
PROCESSED_MESSAGE_TTL_SECONDS = 600

# CLEAR ALL pending confirmation
CLEAR_ALL_PENDING = {}

# CLEAR ALL pending confirmation
CLEAR_ALL_PENDING = {}

def cleanup_processed_messages():
    while True:
        try:
            now = time.time()

            with STATE_LOCK:
                expired = []

                for msg_id, ts in list(PROCESSED_MESSAGE_IDS.items()):
                    if now - ts > PROCESSED_MESSAGE_TTL_SECONDS:
                        expired.append(msg_id)

                for msg_id in expired:
                    PROCESSED_MESSAGE_IDS.pop(msg_id, None)

            time.sleep(60)

        except Exception as e:
            print(f"CLEANUP ERROR: {e}")
            time.sleep(60)



# ดึงชื่อ LINE แบบ background เพื่อไม่ให้คำสั่ง ติด / แจ้งผล หน่วง
PROFILE_LOCK = threading.RLock()
PROFILE_FETCHING = set()
# ถ้า LINE profile API timeout หลายครั้ง ให้พักการดึงชื่อชั่วคราว ลด warning และลดอาการบอทหน่วง
PROFILE_API_FAIL_UNTIL = 0
PROFILE_API_FAIL_COUNT = 0

# ======================================================
# DEMO MODE ONLY
# ระบบนี้เป็นเครดิตจำลองเท่านั้น
# ไม่มีเงินจริง ไม่มีฝากถอน ไม่มีจ่ายเงินจริง
# USERS เก็บลง users.json เพื่อจำ UID / ชื่อ LINE / เครดิต
# POSTS และ MATCHES ทำงานใน memory ระหว่างรัน และมี round_backup.json สำรอง/กู้คืนอัตโนมัติ
# PROFIT เก็บยอดกำไรจากการหัก % ลง profit.json
# ======================================================

STATE = {
    "opened": False,
    "camp_name": None,
    "round_id": None,
    # ห้องที่เปิดรอบนี้ ใช้กันคำสั่งข้ามห้อง เช่น หลังบ้านมาปิดรอบหน้าบ้าน
    "chat_id": None,
    "base_min": None,
    "base_max": None,
    # price_mode: None = ยังไม่ได้แจ้งราคา, "normal" = ราคาช่างเป็นตัวเลข, "no_price" = ช่างไม่มีราคา
    "price_mode": None,
    "no_price_reason": None,
    # two_digit_start: None หรือ 1/2/3 สำหรับแผลเลข 2 ตัว เช่น 30-70ล500
    # เริ่มต้น1 = 100, เริ่มต้น2 = 200, เริ่มต้น3 = 300
    "two_digit_start": None,
    "closed_at": None,
    # ใช้บันทึกการเปิดให้เล่นต่อหลังปิดรอบ โดยยังเป็นรอบ/ค่ายเดิม
    "continued_at": None,
    "continue_count": 0,
    "result": None,
    "settled": False,
    "pending_result": None,
    "pending_result_at": None,
    # ใช้ยืนยันคำสั่งราคาช่างพิเศษ เช่น ราคาช่าง ไม่ต่อย / ราคาช่าง ไม่ตี
    "pending_price": None,
    "pending_price_at": None,
    # ใช้ยืนยันคำสั่ง CR ก่อนเคลียร์รอบจริง
    "pending_clear": None,
    "pending_clear_at": None,
    "pending_clear_ts": None,
    # ใช้ยืนยันคำสั่งย้อนผล ก่อนย้อนเครดิต/กำไรจริง
    "pending_rollback": None,
    "pending_rollback_at": None,
    "pending_rollback_ts": None,
}

POSTS = {}
MATCHES = {}


# ======================================================
# Multi-base round state (TEST PATCH)
# ------------------------------------------------------
# ใช้ STATE เป็นฐานที่กำลังถูกเลือกอยู่ เพื่อให้โค้ดเดิมส่วนใหญ่ทำงานต่อได้
# แต่เก็บ state จริงของแต่ละฐานไว้ใน ROUNDS เช่น ROUNDS["1"], ROUNDS["2"]
# คำสั่งที่รองรับ:
# - เปิด ฐาน1 <ชื่อค่าย>
# - ปิด ฐาน1
# - ราคาช่าง ฐาน1 330-360
# - ราคาช่าง ฐาน1 ไม่ต่อย / ไม่ตี
# - ผล ฐาน1 365 / แจ้งผล ฐาน1 365
# - ผล ฐาน1 จาวทุกแผล / ผล ฐาน1 บั้งไฟหาย
# - CK ฐาน1 / CR ฐาน1 / ยืนยัน ฐาน1
# ======================================================

STATE["base_no"] = STATE.get("base_no") or "1"
STATE["opened_at_ts"] = STATE.get("opened_at_ts") or 0
STATE["updated_at"] = STATE.get("updated_at") or None
ROUNDS = {"1": STATE}
ACTIVE_BASE_NO = "1"


def make_round_state(base_no: str):
    return {
        "opened": False,
        "camp_name": None,
        "round_id": None,
        "chat_id": None,
        "base_min": None,
        "base_max": None,
        "price_mode": None,
        "no_price_reason": None,
        "two_digit_start": None,
        "closed_at": None,
        "continued_at": None,
        "continue_count": 0,
        "result": None,
        "settled": False,
        "pending_result": None,
        "pending_result_at": None,
        "pending_price": None,
        "pending_price_at": None,
        "pending_clear": None,
        "pending_clear_at": None,
        "pending_clear_ts": None,
        "pending_rollback": None,
        "pending_rollback_at": None,
        "pending_rollback_ts": None,
        "base_no": str(base_no),
        "opened_at_ts": 0,
        "updated_at": None,
    }


def normalize_base_no(value) -> str:
    text = str(value or "").strip()
    text = text.replace("ฐาน", "")
    text = re.sub(r"\s+", "", text)
    # แปลงเลขไทยเป็นเลขอารบิก เผื่อพิมพ์ ฐาน๑ / ฐาน๒
    thai_digit_map = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
    text = text.translate(thai_digit_map)
    return text or "1"


def get_round_state(base_no: str, create: bool = True):
    base_no = normalize_base_no(base_no)
    if base_no not in ROUNDS and create:
        ROUNDS[base_no] = make_round_state(base_no)
    return ROUNDS.get(base_no)


def select_round_base(base_no: str, chat_id: str = None, create: bool = True):
    """เลือกฐานให้ STATE ชี้ไปที่ฐานนั้น"""
    global STATE, ACTIVE_BASE_NO
    base_no = normalize_base_no(base_no)
    state = get_round_state(base_no, create=create)
    if not state:
        return None
    state["base_no"] = base_no
    if chat_id and not state.get("chat_id"):
        state["chat_id"] = chat_id
    STATE = state
    ACTIVE_BASE_NO = base_no
    return STATE


def get_state_by_round_id(round_id: str):
    if not round_id:
        return None
    for st in ROUNDS.values():
        if st.get("round_id") == round_id:
            return st
    return None


def get_base_no_by_round_id(round_id: str):
    st = get_state_by_round_id(round_id)
    return st.get("base_no") if st else None


def select_round_base_by_round_id(round_id: str):
    base_no = get_base_no_by_round_id(round_id)
    if base_no:
        return select_round_base(base_no, create=False)
    return None


def select_round_base_for_match(match: dict):
    if not match:
        return None
    return select_round_base_by_round_id(match.get("round_id"))


def base_label(state=None):
    st = state or STATE
    if USE_CAMP_NAME_LABELS:
        return f"ค่าย: {st.get('camp_name') or '-'}"
    return f"ฐาน{st.get('base_no') or '-'}"


def base_label_pretty(state=None):
    """ข้อความชื่อรอบสำหรับประกาศในกลุ่ม"""
    st = state or STATE
    if USE_CAMP_NAME_LABELS:
        camp_name = st.get('camp_name') or ''
        return f"ค่าย {camp_name}" if camp_name else "รอบนี้"
    return f"ฐาน {st.get('base_no') or '-'}"




def auto_detect_two_digit_start(base_min, base_max):
    """
    เดาเลขเริ่มต้นอัตโนมัติจากราคาช่าง
    ตัวอย่าง:
    330-380 -> เริ่มต้น3
    360-420 -> เริ่มต้น3
    280-300 -> เริ่มต้น2
    """
    try:
        first = int(str(base_min)[:1])
        if first in [1, 2, 3]:
            return first
    except Exception:
        pass
    return None


def state_two_digit_start_text(st: dict) -> str:
    """ข้อความเริ่มต้นเลข 2 ตัว เช่น เริ่มต้น3 หรือ -"""
    try:
        start_no = int((st or {}).get("two_digit_start") or 0)
    except Exception:
        start_no = 0
    if start_no in {1, 2, 3}:
        return f"เริ่มต้น{start_no}"
    return "-"


def append_two_digit_start_to_price_text(text: str, st: dict) -> str:
    start_text = state_two_digit_start_text(st)
    if start_text != "-":
        return f"{text} | {start_text}"
    return text


def state_price_text(st: dict) -> str:
    if not st:
        return "-"
    if st.get("price_mode") == "no_price":
        reason = st.get("no_price_reason") or "ไม่ออก"
        return f"ช่างไม่มีราคา ({reason})"
    if st.get("base_min") is not None and st.get("base_max") is not None:
        return append_two_digit_start_to_price_text(format_price_range_text(st.get("base_min"), st.get("base_max")), st)
    return "-"


def state_public_price_text(st: dict) -> str:
    if not st:
        return "-"
    if st.get("price_mode") == "no_price":
        return st.get("no_price_reason") or "ไม่ออก"
    if st.get("base_min") is not None and st.get("base_max") is not None:
        return append_two_digit_start_to_price_text(format_price_range_text(st.get("base_min"), st.get("base_max")), st)
    return "-"


def extract_base_scoped_command(text: str):
    """
    ดึงคำสั่งแบบระบุฐาน แล้วแปลงเป็นคำสั่งเดิมให้โค้ดเดิมประมวลผลต่อ
    return: {"base_no": "1", "text": "เปิด ค่าย A"} หรือ None
    """
    raw = (text or "").strip()
    if not raw:
        return None

    patterns = [
        (r"^(เปิด)\s+ฐาน\s*([^\s]+)\s+(.+)$", lambda m: f"{m.group(1)} {m.group(3).strip()}"),
        (r"^(ปิด)\s+ฐาน\s*([^\s]+)\s*$", lambda m: m.group(1)),
        (r"^(เล่นต่อ(?:ครับ|คับ|ค่ะ|คะ)?)\s+ฐาน\s*([^\s]+)\s*$", lambda m: m.group(1)),
        (r"^(CK|ck|Cr|CR|cr|ยืนยัน)\s+ฐาน\s*([^\s]+)\s*$", lambda m: m.group(1).upper() if m.group(1).lower() in {"ck", "cr"} else m.group(1)),
        (r"^(คู่ติด|คู่รอบนี้|ใครติดใคร|รายการคู่|MATCHES|matches)\s+ฐาน\s*([^\s]+)\s*$", lambda m: m.group(1)),
        (r"^([Ll][Ii][Ss][Tt][Pp][Ll][Aa][Yy])\s+ฐาน\s*([^\s]+)\s*$", lambda m: m.group(1)),
        (r"^(ย้อนผล|ยืนยันย้อนผล|ยกเลิกย้อนผล)\s+ฐาน\s*([^\s]+)\s*$", lambda m: m.group(1)),
        (r"^(ราคาช่าง)\s+ฐาน\s*([^\s]+)\s+(.+)$", lambda m: f"{m.group(1)} {m.group(3).strip()}"),
        (r"^(เริ่มต้น)\s+ฐาน\s*([^\s]+)\s*([123])$", lambda m: f"{m.group(1)}{m.group(3)}"),
        (r"^(เริ่มต้น[123])\s+ฐาน\s*([^\s]+)\s*$", lambda m: m.group(1)),
        (r"^(แจ้งผล|ผล)\s+ฐาน\s*([^\s]+)\s+(.+)$", lambda m: f"{m.group(1)} {m.group(3).strip()}"),
        (r"^(เปลี่ยนค่าย)\s+ฐาน\s*([^\s]+)\s+(.+)$", lambda m: f"{m.group(1)} {m.group(3).strip()}"),
    ]

    for pat, rewrite in patterns:
        m = re.match(pat, raw)
        if m:
            return {
                "base_no": normalize_base_no(m.group(2)),
                "text": rewrite(m).strip(),
            }
    return None




def normalize_camp_key(value: str) -> str:
    """ทำชื่อค่ายไว้เทียบแบบต้องพิมพ์ชื่อให้ตรงกัน แต่ยอมเรื่องช่องว่างเกิน"""
    return re.sub(r"\s+", " ", str(value or "").strip())


def round_matches_camp(st: dict, camp_name: str) -> bool:
    if not isinstance(st, dict):
        return False
    return normalize_camp_key(st.get("camp_name")) == normalize_camp_key(camp_name)


def camp_name_exists_in_unsettled_rounds(camp_name: str, chat_id: str = None) -> bool:
    key = normalize_camp_key(camp_name)
    if not key:
        return False
    for _base_no, st in ROUNDS.items():
        if not isinstance(st, dict):
            continue
        if chat_id and st.get("chat_id") and st.get("chat_id") != chat_id:
            continue
        if st.get("round_id") and not st.get("settled") and normalize_camp_key(st.get("camp_name")) == key:
            return True
    return False


def used_base_numbers_for_chat(chat_id: str = None):
    used = set()
    for base_no, st in ROUNDS.items():
        if not isinstance(st, dict):
            continue
        if chat_id and st.get("chat_id") and st.get("chat_id") != chat_id:
            continue
        if st.get("round_id") and not st.get("backup_status") == "cleared":
            used.add(normalize_base_no(st.get("base_no") or base_no))
    return used


def next_available_base_no(chat_id: str = None) -> str:
    used = used_base_numbers_for_chat(chat_id)
    i = 1
    while str(i) in used:
        i += 1
    return str(i)


def select_base_for_new_round(chat_id: str = None):
    """เปิดรอบใหม่แบบไม่ต้องระบุฐาน: ถ้าฐานปัจจุบันมีรอบค้าง/รอบเก่า ให้ขยับฐานใหม่อัตโนมัติ"""
    current_has_round = bool(STATE.get("round_id"))
    current_busy = current_has_round and not STATE.get("backup_status") == "cleared"
    if not current_busy:
        return STATE
    return select_round_base(next_available_base_no(chat_id), chat_id=chat_id, create=True)


def parse_camp_result_command(text: str):
    """แจ้งผล <ชื่อค่าย> <ตัวเลข> เช่น แจ้งผล แอ๊ดเทวดา 350"""
    raw = (text or "").strip()
    m = re.match(r"^(แจ้งผล|ผล)\s+(.+?)\s+(\d+)$", raw)
    if not m:
        return None
    camp_name = normalize_camp_key(m.group(2))
    if not camp_name:
        return None
    return {"camp_name": camp_name, "result_value": int(m.group(3)), "text": f"{m.group(1)} {m.group(3)}"}


def parse_camp_special_result_command(text: str):
    """แจ้งผล <ชื่อค่าย> จาวทุกแผล / บั้งไฟหาย"""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    m = re.match(r"^(แจ้งผล|ผล)\s+(.+?)\s+(จาวทุกแผล|บั้งไฟหาย)$", raw)
    if not m:
        return None
    camp_name = normalize_camp_key(m.group(2))
    if not camp_name:
        return None
    return {"camp_name": camp_name, "reason": m.group(3), "text": f"{m.group(1)} {m.group(3)}"}


def parse_camp_rollback_result_command(text: str):
    """ย้อนผล <ชื่อค่าย> / ยืนยันย้อนผล <ชื่อค่าย> / ยกเลิกย้อนผล <ชื่อค่าย>"""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    m = re.match(r"^(ย้อนผล|ยืนยันย้อนผล|ยืนยันย้อน|ยกเลิกย้อนผล|ยกเลิกย้อน)\s+(.+)$", raw)
    if not m:
        return None
    cmd = m.group(1)
    camp_name = normalize_camp_key(m.group(2))
    if not camp_name or camp_name.startswith("ฐาน"):
        return None
    if cmd == "ย้อนผล":
        action_text = "ย้อนผล"
    elif cmd in {"ยืนยันย้อนผล", "ยืนยันย้อน"}:
        action_text = "ยืนยันย้อนผล"
    else:
        action_text = "ยกเลิกย้อนผล"
    return {"camp_name": camp_name, "text": action_text}


def _is_reserved_camp_scope_name(camp_name: str) -> bool:
    """กันคำพิเศษอย่าง CK รวม และคำว่า ฐาน ไม่ให้ถูกมองเป็นชื่อค่าย"""
    clean = re.sub(r"\s+", "", str(camp_name or "").strip()).lower()
    if not clean:
        return True
    if clean in {"รวม", "all", "ckall", "ทั้งหมด"}:
        return True
    if clean.startswith("ฐาน"):
        return True
    return False


def parse_camp_named_round_command(text: str):
    """
    คำสั่งแบบเรียกด้วยชื่อค่าย แทนการใช้ ฐาน1/ฐาน2
    ตัวอย่าง:
    - ปิด แอ๊ดเทวดา
    - ราคาช่าง แอ๊ดเทวดา 330-360
    - ราคาช่าง แอ๊ดเทวดา ไม่ตี
    - เริ่มต้น3 แอ๊ดเทวดา
    - CK แอ๊ดเทวดา
    - คู่ติด แอ๊ดเทวดา
    - listplay แอ๊ดเทวดา
    - CR แอ๊ดเทวดา / ยืนยัน แอ๊ดเทวดา
    """
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return None

    # คำสั่งฐานแบบเดิมให้ extract_base_scoped_command จัดการเหมือนเดิม
    if extract_base_scoped_command(raw):
        return None

    clean = re.sub(r"\s+", "", raw).lower()
    if clean in {"ckรวม", "ckall"}:
        return None

    def build(camp_name: str, command_text: str, cmd: str, want_settled=None):
        camp_name = normalize_camp_key(camp_name)
        if _is_reserved_camp_scope_name(camp_name):
            return None
        return {
            "camp_name": camp_name,
            "text": command_text.strip(),
            "cmd": cmd,
            "want_settled": want_settled,
        }

    # ราคาช่าง <ชื่อค่าย> <330-360|ไม่ต่อย|ไม่ตี>
    m = re.match(r"^ราคาช่าง\s+(.+?)\s+(\d+\s*[-/]\s*\d+|ไม่ต่อย|ไม่ตี)$", raw, flags=re.IGNORECASE)
    if m:
        return build(m.group(1), f"ราคาช่าง {m.group(2)}", "price", False)

    # เริ่มต้น3 <ชื่อค่าย> หรือ เริ่มต้น <ชื่อค่าย> 3
    m = re.match(r"^เริ่มต้น([123])\s+(.+)$", raw)
    if m:
        return build(m.group(2), f"เริ่มต้น{m.group(1)}", "two_digit_start", False)

    m = re.match(r"^เริ่มต้น\s+(.+?)\s*([123])$", raw)
    if m:
        return build(m.group(1), f"เริ่มต้น{m.group(2)}", "two_digit_start", False)

    # ปิด <ชื่อค่าย>
    m = re.match(r"^ปิด\s+(.+)$", raw)
    if m:
        return build(m.group(1), "ปิด", "close", False)

    # เล่นต่อ <ชื่อค่าย>
    m = re.match(r"^(เล่นต่อ(?:ครับ|คับ|ค่ะ|คะ)?)\s+(.+)$", raw)
    if m:
        return build(m.group(2), m.group(1), "continue", False)

    # CK/CR <ชื่อค่าย>
    m = re.match(r"^(CK|ck|Cr|CR|cr)\s+(.+)$", raw)
    if m:
        command = m.group(1).upper()
        # CK ดูได้ทั้งรอบที่ยังค้างและรอบที่แจ้งผลแล้ว ส่วน CR ใช้เฉพาะรอบที่ยังไม่แจ้งผล
        want_settled = False if command == "CR" else None
        return build(m.group(2), command, command.lower(), want_settled)

    # ยืนยัน <ชื่อค่าย> สำหรับยืนยัน CR / ราคาช่างไม่มีราคา ของค่ายนั้น
    m = re.match(r"^ยืนยัน\s+(.+)$", raw)
    if m:
        return build(m.group(1), "ยืนยัน", "confirm", False)

    # คู่ติด / คู่รอบนี้ / listplay <ชื่อค่าย>
    m = re.match(r"^(คู่ติด|คู่รอบนี้|ใครติดใคร|รายการคู่|MATCHES|matches)\s+(.+)$", raw)
    if m:
        return build(m.group(2), m.group(1), "matches", None)

    m = re.match(r"^([Ll][Ii][Ss][Tt][Pp][Ll][Aa][Yy])\s+(.+)$", raw)
    if m:
        return build(m.group(2), m.group(1), "listplay", None)

    return None


def is_camp_scoped_round_command(text: str) -> bool:
    return bool(
        parse_camp_result_command(text)
        or parse_camp_special_result_command(text)
        or parse_camp_rollback_result_command(text)
        or parse_camp_named_round_command(text)
    )


def _camp_candidates_for_command(camp_name: str, chat_id: str = None, want_settled: bool = None):
    """หา state จากชื่อค่ายแบบต้องตรงชื่อค่าย"""
    rows = []
    key = normalize_camp_key(camp_name)
    for base_no, st in sorted(ROUNDS.items(), key=lambda x: str(x[0])):
        if not isinstance(st, dict):
            continue
        if chat_id and st.get("chat_id") and st.get("chat_id") != chat_id:
            continue
        if not st.get("round_id"):
            continue
        if normalize_camp_key(st.get("camp_name")) != key:
            continue
        if want_settled is True and not st.get("settled"):
            continue
        if want_settled is False and st.get("settled"):
            continue
        rows.append((normalize_base_no(st.get("base_no") or base_no), st))
    return rows


def _camp_command_not_found_text(camp_name: str, want_settled: bool = None):
    if want_settled is True:
        return f"❌ ย้อนผลไม่ได้ ไม่พบรอบที่แจ้งผลแล้วของค่าย: {camp_name}\nต้องพิมพ์ชื่อค่ายให้ตรงกับตอนเปิดรอบเท่านั้น"
    if want_settled is False:
        return f"❌ แจ้งผลไม่ได้ ไม่พบรอบค้างของค่าย: {camp_name}\nต้องพิมพ์ชื่อค่ายให้ตรงกับตอนเปิดรอบเท่านั้น"
    return f"❌ ไม่พบค่าย: {camp_name}\nต้องพิมพ์ชื่อค่ายให้ตรงกับตอนเปิดรอบเท่านั้น"


def _camp_command_not_found_by_name_text(camp_name: str, command_text: str = "คำสั่ง") -> str:
    return (
        f"❌ ใช้{command_text}ไม่ได้ ไม่พบค่าย: {camp_name}\n"
        f"ต้องพิมพ์ชื่อค่ายให้ตรงกับตอนเปิดรอบเท่านั้น\n"
        f"ดูชื่อค่ายที่ยังค้างได้ด้วยคำสั่ง: CK รวม"
    )


def resolve_camp_scoped_command(text: str, chat_id: str = None):
    """
    แปลงคำสั่งที่ระบุชื่อค่ายให้ไปเลือกฐานจริงภายในระบบ
    return dict: {base_no, text} หรือ {error}
    """
    parsed = parse_camp_result_command(text) or parse_camp_special_result_command(text)
    if parsed:
        candidates = _camp_candidates_for_command(parsed.get("camp_name"), chat_id=chat_id, want_settled=False)
        if not candidates:
            return {"error": _camp_command_not_found_text(parsed.get("camp_name"), want_settled=False)}
        if len(candidates) > 1:
            return {"error": f"❌ มีค่ายชื่อ {parsed.get('camp_name')} มากกว่า 1 รอบค้างอยู่ ระบบไม่แจ้งผลให้เพื่อกันผิดรอบ\nกรุณาใช้ CK รวม ตรวจสอบก่อน"}
        return {"base_no": candidates[0][0], "text": parsed.get("text"), "camp_name": parsed.get("camp_name")}

    parsed = parse_camp_rollback_result_command(text)
    if parsed:
        candidates = _camp_candidates_for_command(parsed.get("camp_name"), chat_id=chat_id, want_settled=True)
        if not candidates:
            return {"error": _camp_command_not_found_text(parsed.get("camp_name"), want_settled=True)}
        if len(candidates) > 1:
            return {"error": f"❌ มีค่ายชื่อ {parsed.get('camp_name')} ที่แจ้งผลแล้วมากกว่า 1 รอบ ระบบไม่ย้อนผลให้เพื่อกันผิดรอบ\nกรุณาใช้ CK รวม ตรวจสอบก่อน"}
        return {"base_no": candidates[0][0], "text": parsed.get("text"), "camp_name": parsed.get("camp_name")}

    parsed = parse_camp_named_round_command(text)
    if parsed:
        candidates = _camp_candidates_for_command(
            parsed.get("camp_name"),
            chat_id=chat_id,
            want_settled=parsed.get("want_settled"),
        )
        if not candidates:
            return {"error": _camp_command_not_found_by_name_text(parsed.get("camp_name"), parsed.get("cmd") or "คำสั่ง")}
        if len(candidates) > 1:
            return {
                "error": (
                    f"❌ มีค่ายชื่อ {parsed.get('camp_name')} มากกว่า 1 รอบ ระบบไม่เลือกให้เพื่อกันผิดรอบ\n"
                    f"กรุณาใช้ CK รวม ตรวจสอบชื่อค่ายก่อน"
                )
            }
        return {"base_no": candidates[0][0], "text": parsed.get("text"), "camp_name": parsed.get("camp_name")}

    return None

def select_default_open_base(chat_id: str = None):
    """เลือกฐานที่เปิดรับอยู่ล่าสุดของห้องนี้ สำหรับข้อความเล่นที่ไม่ระบุฐาน"""
    candidates = []
    for base_no, st in ROUNDS.items():
        if chat_id and st.get("chat_id") and st.get("chat_id") != chat_id:
            continue
        if st.get("opened") and not st.get("settled"):
            candidates.append((float(st.get("opened_at_ts") or 0), base_no, st))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    return select_round_base(candidates[-1][1], chat_id=chat_id, create=False)


def select_base_from_quoted_message(reply_message_id: str):
    if not reply_message_id:
        return None

    post = POSTS.get(reply_message_id)
    if post:
        return select_round_base_by_round_id(post.get("round_id"))

    pending_post, pending_taker = find_pending_taker_by_reply_message_id(reply_message_id)
    if pending_post and pending_taker:
        return select_round_base_by_round_id(pending_post.get("round_id"))

    counter_post, counter_taker = find_counter_pending_by_reply_message_id(reply_message_id)
    if counter_post and counter_taker:
        return select_round_base_by_round_id(counter_post.get("round_id"))

    return None




def _round_sort_key(item):
    base_no, st = item
    return (float(st.get("opened_at_ts") or 0), str(base_no))


def _round_candidates_for_chat(chat_id: str = None):
    rows = []
    for base_no, st in ROUNDS.items():
        if not isinstance(st, dict):
            continue
        if chat_id and st.get("chat_id") and st.get("chat_id") != chat_id:
            continue
        if st.get("round_id") and not st.get("settled"):
            rows.append((normalize_base_no(st.get("base_no") or base_no), st))
    return rows


def select_base_for_admin_implicit_command(text: str, chat_id: str = None) -> bool:
    """
    เมื่อต้องเปิดหลายรอบโดยไม่ใช้คำว่า ฐาน ให้คำสั่งที่ไม่ใช่แจ้งผลเลือก "รอบล่าสุดที่เหมาะสม" อัตโนมัติ
    เช่น เปิดค่าย 2 แล้วปิด/แจ้งราคาช่างค่าย 2 ได้โดยไม่ต้องพิมพ์ ฐาน2
    ส่วนแจ้งผลหลายรอบยังควรใช้ชื่อค่าย เช่น แจ้งผล แอ๊ดเทวดา 350
    """
    raw = (text or "").strip()
    rows = _round_candidates_for_chat(chat_id)
    if not rows:
        return False

    candidates = []
    if raw == "ปิด":
        candidates = [(b, st) for b, st in rows if st.get("opened")]
    elif is_continue_round_command(raw):
        candidates = [(b, st) for b, st in rows if not st.get("opened")]
    elif parse_base_price(raw) or parse_no_price_command(raw):
        candidates = [(b, st) for b, st in rows if not st.get("opened")]
    elif parse_two_digit_start_command(raw) is not None:
        candidates = [
            (b, st) for b, st in rows
            if not st.get("opened") and st.get("price_mode") == "normal" and st.get("base_min") is not None and st.get("base_max") is not None
        ]
    else:
        return False

    if not candidates:
        return False

    candidates.sort(key=_round_sort_key)
    select_round_base(candidates[-1][0], chat_id=chat_id, create=False)
    return True

def select_base_for_incoming_text(event, text: str, explicit_scope=None):
    """เลือกฐานก่อนประมวลผลข้อความ"""
    chat_id = get_current_chat_id(event)

    if explicit_scope:
        return select_round_base(explicit_scope.get("base_no"), chat_id=chat_id, create=True)

    # ถ้าเป็นการ reply ให้ยึดฐานจากโพสต์/ข้อความติดที่ถูก reply ก่อน
    selected = select_base_from_quoted_message(get_reply_message_id(event))
    if selected:
        return selected

    # ถ้าเป็นโพสต์แผลใหม่ ให้เข้า base ที่เปิดรับล่าสุด
    if is_front_chat(event) and parse_offer(text):
        selected = select_default_open_base(chat_id)
        if selected:
            return selected

    return STATE


def all_rounds_report(chat_id: str = None) -> str:
    rows = []
    for base_no, st in sorted(ROUNDS.items(), key=lambda x: str(x[0])):
        if chat_id and st.get("chat_id") and st.get("chat_id") != chat_id:
            continue
        if st.get("round_id") is None:
            status = "ยังไม่เปิด"
        elif st.get("settled"):
            status = "แจ้งผลแล้ว"
        elif st.get("opened"):
            status = "เปิดรับอยู่"
        else:
            status = "ปิดแล้ว / รอผล"
        matched_count = sum(1 for m in MATCHES.values() if m.get("round_id") == st.get("round_id") and m.get("status") == "matched")
        internal_base = f" | รหัสในระบบ: ฐาน{base_no}" if not USE_CAMP_NAME_LABELS else ""
        rows.append(
            f"{status} | ค่าย: {st.get('camp_name') or '-'} | ราคา: {state_price_text(st)} | บิลรอผล: {matched_count}{internal_base}"
        )
    if not rows:
        return "ยังไม่มีข้อมูลค่าย"
    return "CK รวม | สถานะทุกค่าย\n\n" + "\n".join(rows)


def unsettled_rounds_for_chat(chat_id: str = None):
    """คืนรายการฐานที่ยังไม่จบ เพื่อใช้กันคำสั่งแอดมินไม่ระบุฐานแล้วไปลงผิดฐาน"""
    rows = []
    for base_no, st in sorted(ROUNDS.items(), key=lambda x: str(x[0])):
        if chat_id and st.get("chat_id") and st.get("chat_id") != chat_id:
            continue
        if st.get("round_id") and not st.get("settled"):
            rows.append((base_no, st))
    return rows


def admin_command_needs_explicit_base(text: str, chat_id: str = None) -> bool:
    """
    เมื่อมีมากกว่า 1 ฐานค้างอยู่ ห้ามใช้คำสั่งแอดมินแบบไม่ระบุฐาน
    เพื่อกัน ปิด/ราคา/ผล/CR/ยืนยัน ไปทำงานกับ STATE ล่าสุดผิดฐาน
    """
    raw = (text or "").strip()
    if not raw or extract_base_scoped_command(raw) or is_camp_scoped_round_command(raw):
        return False

    unsettled = unsettled_rounds_for_chat(chat_id)
    if len(unsettled) <= 1:
        return False

    clean = re.sub(r"\s+", "", raw)
    upper = raw.upper()

    if upper in {"CK", "CR"}:
        return True
    if clean in {"คู่ติด", "คู่รอบนี้", "ใครติดใคร", "รายการคู่", "matches"}:
        return True
    if clean.lower() == "listplay":
        return True
    if clean == "ยืนยัน":
        return True
    if raw == "ปิด":
        return True
    if is_continue_round_command(raw):
        return True
    # เปิดรอบใหม่ไม่ต้องระบุฐานแล้ว ระบบจะเลือกฐานว่างให้อัตโนมัติ
    if parse_open_command(raw):
        return False
    if parse_change_camp_command(raw):
        return True
    if parse_no_price_command(raw):
        return True
    if parse_base_price(raw):
        return True
    if parse_two_digit_start_command(raw) is not None:
        return True
    if parse_special_result_command(raw) is not None:
        return True
    if parse_result_command(raw) is not None:
        return True
    if parse_rollback_result_command(raw) is not None:
        return True
    if is_listplay_command(raw):
        return True
    if is_result_like_command(raw):
        return True

    return False


def explicit_base_required_text(chat_id: str = None) -> str:
    unsettled = unsettled_rounds_for_chat(chat_id)
    lines = [
        "⚠️ มีหลายค่ายค้างอยู่ กรุณาระบุชื่อค่ายในคำสั่ง",
        "",
        "ตัวอย่างคำสั่งที่ถูกต้อง:",
        "- ปิด แอ๊ดเทวดา",
        "- เล่นต่อ แอ๊ดเทวดา",
        "- ราคาช่าง แอ๊ดเทวดา 330-360",
        "- ราคาช่าง แอ๊ดเทวดา ไม่ตี",
        "- แจ้งผล แอ๊ดเทวดา 365",
        "- CK แอ๊ดเทวดา",
        "- CR แอ๊ดเทวดา",
        "- ยืนยัน แอ๊ดเทวดา",
        "- ย้อนผล แอ๊ดเทวดา",
        "- ยืนยันย้อนผล แอ๊ดเทวดา",
        "- CK รวม",
        "",
        "ค่ายที่ยังค้าง:",
    ]
    for base_no, st in unsettled:
        if st.get("opened"):
            status = "เปิดรับอยู่"
        else:
            status = "ปิดแล้ว / รอผล"
        extra = f" | รหัสในระบบ: ฐาน{base_no}" if not USE_CAMP_NAME_LABELS else ""
        lines.append(f"{status} | ค่าย: {st.get('camp_name') or '-'}{extra}")
    return "\n".join(lines)


# ======================================================
# Round auto-backup / restore
# ------------------------------------------------------
# เวอร์ชันนี้แยก backup เป็น 1 ไฟล์ต่อ 1 รอบ ไม่รวมทุกฐาน/ทุกรอบไว้ในไฟล์เดียว
# ตัวอย่างไฟล์:
#   round_backups/round_base1_8f8c1c1f-xxxx.json
# ในแต่ละไฟล์จะมีเฉพาะ:
# - state ของรอบนั้น
# - POSTS เฉพาะ round_id นั้น
# - MATCHES เฉพาะ round_id นั้น ว่าใครติดกับใคร
# ใช้ atomic write + .bak เพื่อลดโอกาสไฟล์พังถ้าบอทหยุดกลางทาง
# ======================================================

def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _safe_filename_part(value: str) -> str:
    """ทำข้อความให้ใช้เป็นชื่อไฟล์ได้ปลอดภัย"""
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("._-") or "unknown"


def _round_backup_path(round_id: str, base_no: str = None) -> str:
    round_part = _safe_filename_part(round_id)
    base_part = _safe_filename_part(normalize_base_no(base_no or "1"))
    return os.path.join(ROUND_BACKUP_DIR, f"round_base{base_part}_{round_part}.json")


def _atomic_json_dump(path: str, data: dict):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="round_backup_", suffix=".json", dir=directory)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        # เก็บไฟล์ก่อนหน้าไว้ 1 ชั้น เผื่อไฟล์ล่าสุดเสียจากเหตุสุดวิสัย
        if os.path.exists(path):
            bak_path = f"{path}.bak"
            try:
                os.replace(path, bak_path)
            except Exception as backup_error:
                print(f"ROUND BACKUP .bak ERROR: {backup_error}")

        os.replace(tmp_path, path)

    except Exception as e:
        print(f"SAVE ROUND BACKUP ERROR: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _round_ids_for_backup():
    """รวบรวม round_id ที่ควร backup โดยไม่รวมเป็นไฟล์เดียว"""
    found = {}

    # 1) รอบที่ยังอยู่ใน ROUNDS สำคัญสุด เพราะมี state ครบ
    for base_no, st in list((ROUNDS or {}).items()):
        if not isinstance(st, dict):
            continue
        rid = st.get("round_id")
        if rid:
            found[str(rid)] = {
                "base_no": normalize_base_no(st.get("base_no") or base_no),
                "state": st,
            }

    # 2) รอบที่มีโพสต์หรือคู่ติดแล้ว แม้ state จะถูกเคลียร์ไปแล้ว ก็ยังเก็บไฟล์รอบนั้นแยกไว้ดูย้อนหลัง
    for post in list((POSTS or {}).values()):
        if not isinstance(post, dict):
            continue
        rid = post.get("round_id")
        if not rid:
            continue
        rid = str(rid)
        if rid not in found:
            found[rid] = {
                "base_no": normalize_base_no(post.get("base_no") or "1"),
                "state": None,
            }

    for match in list((MATCHES or {}).values()):
        if not isinstance(match, dict):
            continue
        rid = match.get("round_id")
        if not rid:
            continue
        rid = str(rid)
        if rid not in found:
            found[rid] = {
                "base_no": normalize_base_no(match.get("base_no") or "1"),
                "state": None,
            }

    return found


def _infer_round_state_from_items(round_id: str, base_no: str, posts: dict, matches: dict) -> dict:
    """สร้าง state แบบย่อสำหรับรอบเก่าที่ไม่มี state ใน ROUNDS แล้ว"""
    first_item = None
    if posts:
        first_item = next(iter(posts.values()))
    elif matches:
        first_item = next(iter(matches.values()))

    camp_name = (first_item or {}).get("camp_name") or "-"
    chat_id = (first_item or {}).get("chat_id")

    statuses = []
    statuses.extend(str(m.get("status") or "") for m in matches.values() if isinstance(m, dict))
    statuses.extend(str(p.get("status") or "") for p in posts.values() if isinstance(p, dict))

    has_active = any(x in {"open", "closed", "pending", "matched"} for x in statuses)
    has_settled = any(x == "settled" for x in statuses)
    all_cancelled = bool(statuses) and all(x == "cancelled" for x in statuses)

    if all_cancelled:
        backup_status = "cleared"
    elif has_active:
        backup_status = "active"
    elif has_settled:
        backup_status = "settled"
    else:
        backup_status = "archived"

    return {
        "opened": bool(has_active),
        "camp_name": camp_name,
        "round_id": round_id,
        "chat_id": chat_id,
        "base_min": None,
        "base_max": None,
        "price_mode": None,
        "no_price_reason": None,
        "two_digit_start": None,
        "closed_at": None,
        "continued_at": None,
        "continue_count": 0,
        "result": None,
        "settled": bool(has_settled or all_cancelled),
        "pending_result": None,
        "pending_result_at": None,
        "pending_price": None,
        "pending_price_at": None,
        "pending_clear": None,
        "pending_clear_at": None,
        "pending_clear_ts": None,
        "pending_rollback": None,
        "pending_rollback_at": None,
        "pending_rollback_ts": None,
        "base_no": normalize_base_no(base_no),
        "opened_at_ts": 0,
        "updated_at": None,
        "backup_status": backup_status,
    }


def _build_single_round_backup(round_id: str, base_no: str = None, state: dict = None, reason: str = "auto"):
    """สร้าง payload backup สำหรับ round_id เดียวเท่านั้น"""
    if not round_id:
        return None

    round_id = str(round_id)
    round_posts = {
        k: v for k, v in (POSTS or {}).items()
        if isinstance(v, dict) and str(v.get("round_id") or "") == round_id
    }
    round_matches = {
        k: v for k, v in (MATCHES or {}).items()
        if isinstance(v, dict) and str(v.get("round_id") or "") == round_id
    }

    if isinstance(state, dict):
        round_state = dict(state)
        base_no = normalize_base_no(round_state.get("base_no") or base_no or "1")
        round_state["base_no"] = base_no
    else:
        base_no = normalize_base_no(base_no or "1")
        round_state = _infer_round_state_from_items(round_id, base_no, round_posts, round_matches)

    active_match_count = sum(
        1 for m in round_matches.values()
        if isinstance(m, dict) and m.get("status") in {"matched", "settled"}
    )
    active_amount_total = sum(
        int(m.get("amount", 0) or 0) for m in round_matches.values()
        if isinstance(m, dict) and m.get("status") in {"matched", "settled"}
    )

    return {
        "version": 2,
        "type": "single_round_backup",
        "saved_at": datetime.now().isoformat(),
        "reason": reason or "auto",
        "round_id": round_id,
        "base_no": base_no,
        "camp_name": round_state.get("camp_name") or "-",
        "chat_id": round_state.get("chat_id"),
        "state": round_state,
        "posts": round_posts,
        "matches": round_matches,
        "summary": {
            "post_count": len(round_posts),
            "match_count": len(round_matches),
            "active_match_count": active_match_count,
            "active_amount_total": active_amount_total,
        },
    }


def save_round_backup_db(reason: str = "auto"):
    """
    บันทึก backup อัตโนมัติแบบแยกไฟล์ต่อรอบ
    ไม่สร้าง round_backup.json รวมทุกอย่างอีกแล้ว
    """
    if not ROUND_BACKUP_ENABLED:
        return False

    try:
        with STATE_LOCK:
            targets = _round_ids_for_backup()
            saved = 0
            for rid, info in targets.items():
                data = _build_single_round_backup(
                    rid,
                    base_no=info.get("base_no"),
                    state=info.get("state"),
                    reason=reason,
                )
                if not data:
                    continue
                path = _round_backup_path(rid, data.get("base_no"))
                _atomic_json_dump(path, data)
                saved += 1

        return saved > 0
    except Exception as e:
        print(f"SAVE ROUND BACKUP DB ERROR: {e}")
        return False


def _load_round_backup_file(path: str):
    try:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"LOAD ROUND BACKUP FILE ERROR {path}: {e}")
    return None


def _iter_per_round_backup_files():
    """อ่านไฟล์ backup แยกต่อรอบจาก ROUND_BACKUP_DIR"""
    if not os.path.isdir(ROUND_BACKUP_DIR):
        return []

    files = []
    try:
        for name in os.listdir(ROUND_BACKUP_DIR):
            if not name.endswith(".json"):
                continue
            files.append(os.path.join(ROUND_BACKUP_DIR, name))
    except Exception as e:
        print(f"LIST ROUND BACKUP DIR ERROR: {e}")
        return []

    return sorted(files)


def _normalise_backup_payload(data: dict):
    """รองรับทั้งไฟล์แบบใหม่ 1 รอบ/ไฟล์ และไฟล์รวมแบบเก่าเพื่อ fallback"""
    if not isinstance(data, dict):
        return []

    # แบบใหม่: 1 ไฟล์ = 1 รอบ
    if data.get("type") == "single_round_backup" or data.get("round_id"):
        return [data]

    # แบบเก่า: round_backup.json รวมทุกอย่าง อ่านเฉพาะเป็น fallback ถ้ายังไม่มีไฟล์แยก
    rounds = data.get("rounds") or {}
    posts = data.get("posts") or {}
    matches = data.get("matches") or {}
    payloads = []

    if not isinstance(rounds, dict):
        rounds = {}
    if not isinstance(posts, dict):
        posts = {}
    if not isinstance(matches, dict):
        matches = {}

    round_ids = {}
    for base_no, st in rounds.items():
        if isinstance(st, dict) and st.get("round_id"):
            round_ids[str(st.get("round_id"))] = {
                "base_no": normalize_base_no(st.get("base_no") or base_no),
                "state": st,
            }
    for p in posts.values():
        if isinstance(p, dict) and p.get("round_id"):
            round_ids.setdefault(str(p.get("round_id")), {"base_no": normalize_base_no(p.get("base_no") or "1"), "state": None})
    for m in matches.values():
        if isinstance(m, dict) and m.get("round_id"):
            round_ids.setdefault(str(m.get("round_id")), {"base_no": normalize_base_no(m.get("base_no") or "1"), "state": None})

    for rid, info in round_ids.items():
        one_posts = {k: v for k, v in posts.items() if isinstance(v, dict) and str(v.get("round_id") or "") == rid}
        one_matches = {k: v for k, v in matches.items() if isinstance(v, dict) and str(v.get("round_id") or "") == rid}
        st = info.get("state") if isinstance(info.get("state"), dict) else None
        base_no = info.get("base_no")
        if not st:
            st = _infer_round_state_from_items(rid, base_no, one_posts, one_matches)
        payloads.append({
            "version": 2,
            "type": "single_round_backup",
            "saved_at": data.get("saved_at"),
            "reason": "legacy_restore",
            "round_id": rid,
            "base_no": base_no,
            "camp_name": st.get("camp_name") or "-",
            "chat_id": st.get("chat_id"),
            "state": st,
            "posts": one_posts,
            "matches": one_matches,
            "summary": {
                "post_count": len(one_posts),
                "match_count": len(one_matches),
            },
        })

    return payloads


def _round_restore_priority(payload: dict):
    """
    เลือก backup ที่ควรกู้ต่อฐาน:
    3 = รอบเปิด/ยังไม่จบ สำคัญสุด
    2 = รอบแจ้งผลแล้ว ยังควรกู้เพื่อให้ย้อนผลได้
    1 = รอบ archive
    0 = รอบเคลียร์/ยกเลิกแล้ว ไม่ควรกู้เป็นรอบค้าง
    """
    st = payload.get("state") if isinstance(payload, dict) else {}
    if not isinstance(st, dict):
        st = {}

    status = st.get("backup_status")
    if status == "cleared":
        return 0

    opened = bool(st.get("opened"))
    settled = bool(st.get("settled"))
    matches = payload.get("matches") if isinstance(payload.get("matches"), dict) else {}
    posts = payload.get("posts") if isinstance(payload.get("posts"), dict) else {}

    has_active_items = any(
        isinstance(m, dict) and m.get("status") in {"matched", "open", "pending"}
        for m in matches.values()
    ) or any(
        isinstance(p, dict) and p.get("status") in {"open", "closed", "pending"}
        for p in posts.values()
    )

    if opened or has_active_items:
        return 3
    if settled:
        return 2
    return 1


def restore_round_backup_db():
    """กู้ข้อมูลรอบ / โพสต์ / คู่ติด จากไฟล์ backup แยกต่อรอบตอนเริ่มบอท"""
    global STATE, ROUNDS, ACTIVE_BASE_NO, POSTS, MATCHES

    if not ROUND_BACKUP_ENABLED:
        return False

    payloads = []

    # 1) อ่านไฟล์แยกต่อรอบก่อน
    for path in _iter_per_round_backup_files():
        data = _load_round_backup_file(path)
        if data:
            payloads.extend(_normalise_backup_payload(data))

    # 2) ถ้ายังไม่มีไฟล์แยกเลย ค่อย fallback ไฟล์รวมแบบเก่า เพื่อไม่ให้ข้อมูลเดิมหายตอนอัปเกรด
    if not payloads:
        for legacy_path in [ROUND_BACKUP_DB_FILE, f"{ROUND_BACKUP_DB_FILE}.bak"]:
            data = _load_round_backup_file(legacy_path)
            if data:
                payloads.extend(_normalise_backup_payload(data))
                break

    if not payloads:
        return False

    try:
        # เลือก 1 รอบล่าสุด/สำคัญสุดต่อฐาน เพื่อไม่ให้รอบเก่าที่เคลียร์แล้วกลับมาปน
        selected_by_base = {}
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            rid = str(payload.get("round_id") or "").strip()
            if not rid:
                continue
            base_no = normalize_base_no(payload.get("base_no") or (payload.get("state") or {}).get("base_no") or "1")
            priority = _round_restore_priority(payload)
            if priority <= 0:
                continue
            saved_at = str(payload.get("saved_at") or "")
            key = (priority, saved_at)
            old = selected_by_base.get(base_no)
            if not old or key > old[0]:
                selected_by_base[base_no] = (key, payload)

        if not selected_by_base:
            return False

        new_rounds = {}
        new_posts = {}
        new_matches = {}

        for base_no, (_, payload) in selected_by_base.items():
            raw_state = payload.get("state") or {}
            base_state = make_round_state(base_no)
            if isinstance(raw_state, dict):
                base_state.update(raw_state)
            base_state["base_no"] = base_no
            if not base_state.get("round_id"):
                base_state["round_id"] = payload.get("round_id")
            new_rounds[base_no] = base_state

            if isinstance(payload.get("posts"), dict):
                new_posts.update(payload.get("posts") or {})
            if isinstance(payload.get("matches"), dict):
                new_matches.update(payload.get("matches") or {})

        if new_rounds:
            ROUNDS = new_rounds
            # เลือกฐาน active: ให้รอบที่ยังเปิด/ยังไม่จบมาก่อน
            active_candidates = []
            for base_no, st in ROUNDS.items():
                priority = 3 if st.get("opened") and not st.get("settled") else (2 if st.get("settled") else 1)
                active_candidates.append((priority, float(st.get("opened_at_ts") or 0), base_no))
            active_candidates.sort()
            ACTIVE_BASE_NO = active_candidates[-1][2]
            STATE = ROUNDS[ACTIVE_BASE_NO]

        POSTS.clear()
        POSTS.update(new_posts)
        MATCHES.clear()
        MATCHES.update(new_matches)

        print(
            "ROUND BACKUP RESTORED PER ROUND: "
            f"rounds={len(ROUNDS)} posts={len(POSTS)} matches={len(MATCHES)} "
            f"dir={ROUND_BACKUP_DIR}"
        )
        return True

    except Exception as e:
        print(f"RESTORE ROUND BACKUP DB ERROR: {e}")
        return False


# เรียกกู้คืนทันทีตอนโหลดไฟล์ ก่อน webhook เริ่มรับงาน
restore_round_backup_db()

# ฝั่งช่างไล่ / ชนะ
CHASE_ALIASES = ["ชล", "ช่างไล่", "ล", "ไล","ไล่"]

# ฝั่งช่างถอย / แพ้
RETREAT_ALIASES = ["ชถ", "ชย", "ยั่ง", "ถอย", "ช่างรับ", "รับช่าง","รับ", "ช่างถอย", "ยั้ง", "ช่างยั้ง", "ย", "ถ"]

# เรียงคำยาวก่อน เพื่อไม่ให้ "ถอย" โดนจับเป็น "ถ"
ALL_PLAY_ALIASES = sorted(CHASE_ALIASES + RETREAT_ALIASES, key=len, reverse=True)

# คำสั่งเล่นพิเศษแบบไม่มีจาวในช่วงราคา
# - ช่างไม่ชนะ / ช่างบ่ชนะ / ช่างบ้ชนะ: ผู้โพสต์ชนะเมื่อผลไม่เกินเลขหลังของราคาช่าง
# - ช่างแพ้: ผู้โพสต์ชนะเฉพาะเมื่อผลต่ำกว่าเลขหน้าของราคาช่าง
NO_WIN_ALIASES = ["ช่างไม่ชนะ", "ช่างบ่ชนะ", "ช่างบ้ชนะ"]
ONLY_LOSE_ALIASES = ["ช่างแพ้"]
ALL_SPECIAL_PLAY_ALIASES = sorted(NO_WIN_ALIASES + ONLY_LOSE_ALIASES, key=len, reverse=True)

# Prefix ปรับราคาช่างเฉพาะฝั่ง
# ก / เกิบ = เลขตัวแรกของราคาช่าง เช่น ราคาช่าง 330-360, ก+5ล100 หรือ เกิบ+5ล100 => 335-360
# ม / หมวก = เลขตัวหลังของราคาช่าง เช่น ราคาช่าง 330-360, ม+5ล100 หรือ หมวก+5ล100 => 330-365
PRICE_BOUND_ADJUST_PREFIXES = {
    # ก / เกิบ = ปรับเลขหน้าเท่านั้น
    "ก": "min",
    "เกิบ": "min",
    # ม / หมวก = ปรับเลขหลังเท่านั้น
    "ม": "max",
    "หมวก": "max",
    # กม = ปรับทั้งเลขหน้าและเลขหลัง เช่น กม+5ล500 = +5ล500
    # เพิ่มไว้เพื่อรองรับรูปแบบที่ลูกค้าพิมพ์จริงในกลุ่ม
    "กม": "both",
}
PRICE_BOUND_ADJUST_PREFIX_PATTERN = "|".join(
    re.escape(x) for x in sorted(PRICE_BOUND_ADJUST_PREFIXES, key=len, reverse=True)
)

# ทำคำสั่งเล่นให้ทนกับช่องว่าง/ตัวอักษรแปลกจากมือถือหรือ LINE
# เช่น +5    ล 500, เกิบ-5 ม+5  ล500, ต 300, 300 ต
PLAY_COMMAND_TRANSLATION = str.maketrans({
    "๐": "0", "๑": "1", "๒": "2", "๓": "3", "๔": "4",
    "๕": "5", "๖": "6", "๗": "7", "๘": "8", "๙": "9",
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    "＋": "+", "－": "-", "−": "-", "–": "-", "—": "-", "／": "/",
})


def compact_play_command_text(text) -> str:
    """ลบช่องว่างทุกแบบในคำสั่งเล่น/ติด และ normalize ตัวเลขก่อนเข้า regex"""
    value = str(text or "").strip().translate(PLAY_COMMAND_TRANSLATION)
    return re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", value)


# ======================================================
# JSON user storage
# ======================================================

def load_user_db():
    if not os.path.exists(USER_DB_FILE):
        return {}, 1

    try:
        with open(USER_DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        users = data.get("users", {})
        next_member_no = data.get("next_member_no")

        if not isinstance(users, dict):
            users = {}

        if not isinstance(next_member_no, int):
            max_no = 0
            for u in users.values():
                try:
                    max_no = max(max_no, int(u.get("member_no", 0)))
                except Exception:
                    pass
            next_member_no = max_no + 1

        return users, next_member_no

    except Exception as e:
        print(f"LOAD USER DB ERROR: {e}")
        return {}, 1


USERS, NEXT_MEMBER_NO = load_user_db()


def save_user_db():
    """
    เขียนไฟล์แบบ atomic ลดโอกาสไฟล์พังถ้าโปรแกรมหยุดกลางทาง
    """
    data = {
        "next_member_no": NEXT_MEMBER_NO,
        "users": USERS,
        "updated_at": datetime.now().isoformat(),
    }

    directory = os.path.dirname(os.path.abspath(USER_DB_FILE)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="users_", suffix=".json", dir=directory)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp_path, USER_DB_FILE)

    except Exception as e:
        print(f"SAVE USER DB ERROR: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def load_profit_db():
    """
    เก็บยอดกำไรของระบบจากการหัก % ผู้ชนะ
    แยกไฟล์จาก users.json เพื่อไม่ให้ข้อมูลเครดิตปนกับยอดกำไรหลังบ้าน
    """
    default = {
        "total_profit": 0,
        "rounds": [],
        "updated_at": None,
    }

    if not os.path.exists(PROFIT_DB_FILE):
        return default

    try:
        with open(PROFIT_DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return default

        data.setdefault("total_profit", 0)
        data.setdefault("rounds", [])
        data.setdefault("updated_at", None)

        if not isinstance(data.get("rounds"), list):
            data["rounds"] = []

        try:
            data["total_profit"] = int(data.get("total_profit", 0))
        except Exception:
            data["total_profit"] = 0

        return data

    except Exception as e:
        print(f"LOAD PROFIT DB ERROR: {e}")
        return default


PROFIT = load_profit_db()


def load_order_db():
    """เก็บเลขออเดอร์ถัดไป แยกไฟล์ เพื่อให้รีสตาร์ทบอทแล้วยังนับต่อได้"""
    default = {
        "next_order_no": ORDER_START_NO,
        "updated_at": None,
        "last_reset": None,
    }

    if not os.path.exists(ORDER_DB_FILE):
        return default

    try:
        with open(ORDER_DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return default

        try:
            data["next_order_no"] = int(data.get("next_order_no", ORDER_START_NO))
        except Exception:
            data["next_order_no"] = ORDER_START_NO

        if data["next_order_no"] <= 0:
            data["next_order_no"] = ORDER_START_NO

        data.setdefault("updated_at", None)
        data.setdefault("last_reset", None)
        return data

    except Exception as e:
        print(f"LOAD ORDER DB ERROR: {e}")
        return default


ORDER_STATE = load_order_db()


def save_order_db():
    data = {
        "next_order_no": int(ORDER_STATE.get("next_order_no", ORDER_START_NO)),
        "updated_at": datetime.now().isoformat(),
        "last_reset": ORDER_STATE.get("last_reset"),
    }

    directory = os.path.dirname(os.path.abspath(ORDER_DB_FILE)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="order_state_", suffix=".json", dir=directory)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp_path, ORDER_DB_FILE)

    except Exception as e:
        print(f"SAVE ORDER DB ERROR: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def get_next_order_no():
    """ออกเลขออเดอร์ถัดไปแบบ thread-safe และบันทึกลงไฟล์"""
    next_no = int(ORDER_STATE.get("next_order_no", ORDER_START_NO) or ORDER_START_NO)
    if next_no <= 0:
        next_no = ORDER_START_NO

    ORDER_STATE["next_order_no"] = next_no + 1
    save_order_db()
    return str(next_no)



def load_slip_topup_db():
    """
    เก็บประวัติสลิปที่เติมเครดิตแล้ว เพื่อกันสลิปซ้ำแม้ LINE webhook ยิงซ้ำหรือรีสตาร์ทบอท

    เวอร์ชันนี้กันไฟล์ slip_topups.json ว่าง/พังด้วย:
    - ถ้าไฟล์ว่างหรือ JSON พัง จะ backup เป็น .bad_เวลา
    - สร้างไฟล์ใหม่เป็น {"slips": {}, "updated_at": null}
    - บอทจะไม่ล้ม และไม่ขึ้น LOAD SLIP TOPUP DB ERROR ซ้ำทุกครั้งที่ restart
    """
    default = {
        "slips": {},
        "updated_at": None,
    }

    if not os.path.exists(SLIP_TOPUP_DB_FILE):
        return default

    def repair_bad_slip_file(reason: str):
        try:
            bad_path = f"{SLIP_TOPUP_DB_FILE}.bad_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                # ถ้าไฟล์มีข้อมูลเดิมอยู่ ให้เก็บสำรองไว้ก่อน
                if os.path.exists(SLIP_TOPUP_DB_FILE) and os.path.getsize(SLIP_TOPUP_DB_FILE) > 0:
                    os.replace(SLIP_TOPUP_DB_FILE, bad_path)
                    print(f"REPAIRED SLIP TOPUP DB: backup bad file to {bad_path} ({reason})")
            except Exception as backup_error:
                print(f"BACKUP BAD SLIP TOPUP DB ERROR: {backup_error}")

            directory = os.path.dirname(os.path.abspath(SLIP_TOPUP_DB_FILE)) or "."
            fd, tmp_path = tempfile.mkstemp(prefix="slip_topups_repair_", suffix=".json", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, SLIP_TOPUP_DB_FILE)
        except Exception as repair_error:
            print(f"REPAIR SLIP TOPUP DB ERROR: {repair_error}")
        return default

    try:
        # ไฟล์ว่าง 0 byte จะทำให้ json.load error line 1 column 1
        if os.path.getsize(SLIP_TOPUP_DB_FILE) == 0:
            return repair_bad_slip_file("empty file")

        with open(SLIP_TOPUP_DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return repair_bad_slip_file("root is not dict")

        data.setdefault("slips", {})
        data.setdefault("updated_at", None)

        if not isinstance(data.get("slips"), dict):
            data["slips"] = {}

        return data

    except Exception as e:
        print(f"LOAD SLIP TOPUP DB ERROR: {e}")
        return repair_bad_slip_file(str(e))


SLIP_TOPUPS = load_slip_topup_db()


def save_slip_topup_db():
    data = {
        "slips": SLIP_TOPUPS.get("slips", {}),
        "updated_at": datetime.now().isoformat(),
    }

    directory = os.path.dirname(os.path.abspath(SLIP_TOPUP_DB_FILE)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="slip_topups_", suffix=".json", dir=directory)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp_path, SLIP_TOPUP_DB_FILE)

    except Exception as e:
        print(f"SAVE SLIP TOPUP DB ERROR: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# ======================================================
# Dynamic admin storage
# ======================================================

def load_admin_db():
    """
    เก็บแอดมินที่เพิ่มผ่านคำสั่ง เพิ่มแอดมิน @ชื่อไลน์
    แยกจาก .env เพื่อให้เพิ่มแอดมินได้ทันทีและยังอยู่หลัง restart bot
    """
    default = {
        "admins": {},
        "updated_at": None,
    }

    if not os.path.exists(ADMIN_DB_FILE):
        return default

    try:
        with open(ADMIN_DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return default

        data.setdefault("admins", {})
        data.setdefault("updated_at", None)

        if not isinstance(data.get("admins"), dict):
            data["admins"] = {}

        return data

    except Exception as e:
        print(f"LOAD ADMIN DB ERROR: {e}")
        return default


DYNAMIC_ADMINS = load_admin_db()


def save_admin_db():
    data = {
        "admins": DYNAMIC_ADMINS.get("admins", {}),
        "updated_at": datetime.now().isoformat(),
    }

    directory = os.path.dirname(os.path.abspath(ADMIN_DB_FILE)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="admins_", suffix=".json", dir=directory)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp_path, ADMIN_DB_FILE)

    except Exception as e:
        print(f"SAVE ADMIN DB ERROR: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def dynamic_admin_ids():
    admins = DYNAMIC_ADMINS.get("admins", {})
    if not isinstance(admins, dict):
        return set()
    return set(admins.keys())


def save_profit_db():
    data = {
        "total_profit": int(PROFIT.get("total_profit", 0)),
        "rounds": PROFIT.get("rounds", []),
        "updated_at": datetime.now().isoformat(),
    }

    directory = os.path.dirname(os.path.abspath(PROFIT_DB_FILE)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="profit_", suffix=".json", dir=directory)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp_path, PROFIT_DB_FILE)

    except Exception as e:
        print(f"SAVE PROFIT DB ERROR: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def calculate_commission(amount: int) -> int:
    """หักจากคนที่ชนะเท่านั้น: 10% ของยอดได้ ไม่หักคนเสีย"""
    try:
        amount = int(amount)
    except Exception:
        return 0

    if amount <= 0 or COMMISSION_PERCENT <= 0:
        return 0

    # ระบบเครดิตเป็นจำนวนเต็ม จึงปัดเศษลงเป็นเครดิตเต็มหน่วย
    return (amount * COMMISSION_PERCENT) // 100


def add_profit_record(round_id: str, camp_name: str, result_value: int, profit_amount: int, order_rows: list, open_price: str = "-"):
    if profit_amount <= 0:
        return

    PROFIT["total_profit"] = int(PROFIT.get("total_profit", 0)) + int(profit_amount)
    PROFIT.setdefault("rounds", []).append({
        "round_id": round_id,
        "camp_name": camp_name or "-",
        "open_price": open_price or "-",
        "result": result_value,
        "commission_percent": COMMISSION_PERCENT,
        "profit": int(profit_amount),
        "orders": order_rows,
        # เก็บเวลาไว้ในไฟล์สำหรับอ้างอิงหลังบ้าน แต่ไม่เอาไปแสดงในคำสั่งยอดกำไร
        "created_at": now_text(),
    })
    save_profit_db()


# ======================================================
# Utility
# ======================================================

def now_text():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def is_admin(user_id: str) -> bool:
    return user_id in ADMIN_USER_IDS or user_id in dynamic_admin_ids()


def fallback_name(user_id: str):
    return f"User-{user_id[-5:]}" if user_id else "Unknown"


def get_user(user_id: str, display_name: str = None):
    global NEXT_MEMBER_NO

    if not user_id:
        return None

    changed = False

    if user_id not in USERS:
        USERS[user_id] = {
            "user_id": user_id,
            "member_no": NEXT_MEMBER_NO,
            "name": display_name or fallback_name(user_id),
            "line_name": display_name or fallback_name(user_id),
            "picture_url": None,
            "credit": 0,
            "is_friend": False,
            "last_seen_at": now_text(),
            "last_profile_at": 0,
        }
        NEXT_MEMBER_NO += 1
        changed = True
    else:
        user = USERS[user_id]
        user.setdefault("user_id", user_id)
        user.setdefault("member_no", NEXT_MEMBER_NO)
        user.setdefault("name", user.get("line_name") or fallback_name(user_id))
        user.setdefault("line_name", user.get("name") or fallback_name(user_id))
        user.setdefault("picture_url", None)
        user.setdefault("credit", 0)
        user.setdefault("is_friend", False)
        user.setdefault("last_seen_at", now_text())
        user.setdefault("last_profile_at", 0)

        if display_name and user.get("line_name") != display_name:
            user["line_name"] = display_name
            user["name"] = display_name
            changed = True

    USERS[user_id]["last_seen_at"] = now_text()

    if changed:
        save_user_db()

    return USERS[user_id]




def mark_user_friend_verified(user_id: str, reason: str = "private_message"):
    """
    บันทึกว่า user นี้เป็นเพื่อน/ทัก OA ได้แล้ว

    เหตุผลที่ต้องมีฟังก์ชันนี้:
    - LINE จะส่ง FollowEvent แค่ตอนแอดเพื่อน/ปลดบล็อกบางจังหวะ
    - ถ้า user แอดไว้ก่อนเปิดบอท หรือ webhook พลาดช่วงแอด ค่า is_friend จะยัง False
    - แต่ถ้า user ทักแชทส่วนตัวกับ OA ได้ แปลว่า OA สามารถคุยกับ user นั้นได้แล้ว
    """
    if not user_id:
        return None

    user = get_user(user_id)
    if not user:
        return None

    changed = False
    if not user.get("is_friend"):
        user["is_friend"] = True
        changed = True

    # เก็บหลักฐานไว้ดูย้อนหลังว่า mark จากอะไร/เมื่อไหร่
    user["friend_verified_at"] = now_text()
    user["friend_verified_by"] = reason
    user["last_seen_at"] = now_text()

    if reason in {"private_message", "private_postback", "private_image"}:
        user["first_private_seen_at"] = user.get("first_private_seen_at") or now_text()
        user["last_private_seen_at"] = now_text()

    save_user_db()
    return user


def friend_status_text(user: dict) -> str:
    """ข้อความสถานะเพื่อนสำหรับ UIDLIST"""
    if user and user.get("is_friend"):
        reason = user.get("friend_verified_by")
        if reason == "follow_event":
            return "✅ เพิ่มเพื่อนแล้ว"
        if reason in {"private_message", "private_postback", "private_image"}:
            return "✅ ทัก OA แล้ว"
        return "✅ เพิ่มเพื่อนแล้ว"
    return "⚠️ ยังไม่ยืนยันเพื่อน"


def find_user_by_member_no(member_no: int):
    for user in USERS.values():
        if user.get("member_no") == member_no:
            return user
    return None


def get_registered_topup_user(user_id: str):
    """
    ใช้สำหรับเติมเครดิตจากสลิปเท่านั้น
    ต้องเป็น user ที่มี ID สมาชิกอยู่แล้ว ห้ามสร้าง user ใหม่จากการส่งสลิป
    """
    if not user_id:
        return None

    user = USERS.get(user_id)
    if not isinstance(user, dict):
        return None

    try:
        member_no = int(user.get("member_no") or 0)
    except Exception:
        member_no = 0

    if member_no <= 0:
        return None

    user.setdefault("credit", 0)
    user.setdefault("name", user.get("line_name") or fallback_name(user_id))
    user.setdefault("line_name", user.get("name") or fallback_name(user_id))
    return user


def no_member_id_topup_flex():
    return slip_fail_flex(
        title="❌ ยังไม่มี ID สมาชิก",
        reason="ระบบยังไม่พบ ID สมาชิกของคุณ จึงยังไม่สามารถเติมเครดิตอัตโนมัติจากสลิปได้",
        suggestion="กรุณาพิมพ์ เช็คยอด ในแชทส่วนตัวกับบอทเพื่อรับ ID ก่อน แล้วส่งสลิปใหม่อีกครั้ง",
    )


def get_source_ids(event):
    source = event.source
    return {
        "user_id": getattr(source, "user_id", None),
        "group_id": getattr(source, "group_id", None),
        "room_id": getattr(source, "room_id", None),
        "source_type": getattr(source, "type", None),
    }


def get_line_profile(user_id: str, group_id: str = None, room_id: str = None):
    """
    ดึงชื่อ LINE จริงแบบมี timeout สั้น
    เวอร์ชันนี้ไม่ใช้ LINE SDK สำหรับ profile เพราะ SDK อาจ connect timeout=None แล้วค้าง/ขึ้น warning ยาว
    - ในกลุ่ม ใช้ /v2/bot/group/{groupId}/member/{userId}
    - ใน room ใช้ /v2/bot/room/{roomId}/member/{userId}
    - แชทส่วนตัว ใช้ /v2/bot/profile/{userId}
    ถ้าดึงไม่ได้จะคืน None และใช้ชื่อสำรอง User-xxxxx ต่อไปก่อน
    """
    global PROFILE_API_FAIL_UNTIL, PROFILE_API_FAIL_COUNT

    if not user_id or not LINE_PROFILE_ENABLED:
        return None

    now_ts = time.time()
    with PROFILE_LOCK:
        if PROFILE_API_FAIL_UNTIL and now_ts < PROFILE_API_FAIL_UNTIL:
            return None

    if group_id:
        url = LINE_API_GROUP_MEMBER_PROFILE_URL.format(group_id=group_id, user_id=user_id)
    elif room_id:
        url = LINE_API_ROOM_MEMBER_PROFILE_URL.format(room_id=room_id, user_id=user_id)
    else:
        url = LINE_API_PROFILE_URL.format(user_id=user_id)

    try:
        response = LINE_HTTP_SESSION.get(
            url,
            headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"},
            timeout=(LINE_CONNECT_TIMEOUT_SECONDS, LINE_PROFILE_TIMEOUT_SECONDS),
        )

        if 200 <= response.status_code < 300:
            data = response.json()
            with PROFILE_LOCK:
                PROFILE_API_FAIL_COUNT = 0
                PROFILE_API_FAIL_UNTIL = 0
            return SimpleNamespace(
                display_name=data.get("displayName"),
                picture_url=data.get("pictureUrl"),
            )

        # 403/404 มักเกิดจากบอทไม่มีสิทธิ์/ผู้ใช้ไม่ได้อยู่ในกลุ่ม/ไม่ได้เป็นเพื่อน ไม่ต้องถือเป็นเน็ตล่ม
        if response.status_code not in (403, 404):
            print(f"GET PROFILE HTTP {response.status_code} user_id={user_id}: {response.text[:300]}")
        return None

    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.Timeout) as e:
        with PROFILE_LOCK:
            PROFILE_API_FAIL_COUNT += 1
            if PROFILE_API_FAIL_COUNT >= 2:
                PROFILE_API_FAIL_UNTIL = time.time() + LINE_PROFILE_COOLDOWN_SECONDS
                print(
                    f"GET PROFILE TIMEOUT: {e} | pause profile refresh {LINE_PROFILE_COOLDOWN_SECONDS}s"
                )
            else:
                print(f"GET PROFILE TIMEOUT: {e}")
        return None

    except requests.exceptions.RequestException as e:
        print(f"GET PROFILE REQUEST ERROR user_id={user_id}: {e}")
        return None

    except Exception as e:
        print(f"GET PROFILE ERROR user_id={user_id}: {e}")
        return None

def mark_message_processed(message_id: str) -> bool:
    """
    คืน True ถ้า message_id นี้เคยประมวลผลแล้ว
    ใช้กัน LINE webhook retry / duplicate ที่ทำให้คำสั่งเดียวถูกยิงซ้ำ
    """
    if not message_id:
        return False

    now_ts = time.time()
    with STATE_LOCK:
        if len(PROCESSED_MESSAGE_IDS) > 1000:
            expired = [
                mid for mid, ts in PROCESSED_MESSAGE_IDS.items()
                if now_ts - ts > PROCESSED_MESSAGE_TTL_SECONDS
            ]
            for mid in expired:
                PROCESSED_MESSAGE_IDS.pop(mid, None)

        if message_id in PROCESSED_MESSAGE_IDS:
            return True

        PROCESSED_MESSAGE_IDS[message_id] = now_ts
        return False


def update_user_profile_from_line(user_id: str, group_id: str = None, room_id: str = None, now_ts: int = None):
    """
    ดึง profile จาก LINE แล้วอัปเดต USERS
    ฟังก์ชันนี้อาจช้า จึงไม่ควรเรียกแบบ sync ในคำสั่งที่ต้องเร็ว
    """
    if not user_id:
        return

    profile = get_line_profile(user_id, group_id=group_id, room_id=room_id)
    if not profile:
        # mark เวลาไว้เพื่อกันยิง get profile ซ้ำติด ๆ กันตอน LINE หน่วง และบันทึกลงไฟล์ด้วย
        with STATE_LOCK:
            user = USERS.get(user_id)
            if user:
                user["last_profile_at"] = int(time.time())
                save_user_db()
        return

    display_name = getattr(profile, "display_name", None)
    picture_url = getattr(profile, "picture_url", None)

    with STATE_LOCK:
        user = get_user(user_id)
        if not user:
            return

        if display_name:
            user["line_name"] = display_name
            user["name"] = display_name

        if picture_url:
            user["picture_url"] = picture_url

        user["last_profile_at"] = now_ts or int(time.time())
        save_user_db()


def queue_profile_refresh(user_id: str, group_id: str = None, room_id: str = None, now_ts: int = None):
    """
    ส่งงานดึงชื่อ LINE ไปทำหลังบ้าน ไม่บล็อก webhook
    """
    if not user_id:
        return

    key = f"{group_id or room_id or 'private'}:{user_id}"

    with PROFILE_LOCK:
        if key in PROFILE_FETCHING:
            return
        PROFILE_FETCHING.add(key)

    def job():
        try:
            update_user_profile_from_line(user_id, group_id=group_id, room_id=room_id, now_ts=now_ts)
        except Exception as e:
            print(f"PROFILE REFRESH JOB ERROR user_id={user_id}: {e}")
        finally:
            with PROFILE_LOCK:
                PROFILE_FETCHING.discard(key)

    EXECUTOR.submit(job)


def ensure_user_from_event(event, force_profile: bool = False):
    ids = get_source_ids(event)
    user_id = ids["user_id"]
    group_id = ids["group_id"]
    room_id = ids["room_id"]

    with STATE_LOCK:
        user = get_user(user_id)
        if not user:
            return None

        # สำคัญ: ถ้าเป็นแชทส่วนตัวกับ OA ให้ถือว่า user ใช้งาน OA ได้แล้ว
        # แก้เคส UIDLIST ยังขึ้น "ไม่ยืนยันเพื่อน" แม้ user แอด/ทักบอทแล้ว
        if is_private_chat(event):
            user["is_friend"] = True
            user["friend_verified_at"] = now_text()
            user["friend_verified_by"] = "private_message"
            user["first_private_seen_at"] = user.get("first_private_seen_at") or now_text()
            user["last_private_seen_at"] = now_text()
            user["last_seen_at"] = now_text()
            save_user_db()

        now_ts = int(time.time())
        should_refresh = (
            force_profile
            or not user.get("line_name")
            or str(user.get("line_name", "")).startswith("User-")
            or now_ts - int(user.get("last_profile_at", 0)) >= PROFILE_REFRESH_SECONDS
        )

    if should_refresh:
        if force_profile:
            update_user_profile_from_line(user_id, group_id=group_id, room_id=room_id, now_ts=now_ts)
        else:
            queue_profile_refresh(user_id, group_id=group_id, room_id=room_id, now_ts=now_ts)

    with STATE_LOCK:
        return USERS.get(user_id)

def extract_user_name(event):
    user = ensure_user_from_event(event)
    if user:
        return user.get("line_name") or user.get("name") or fallback_name(user.get("user_id"))
    uid = getattr(event.source, "user_id", "unknown")
    return fallback_name(uid)


def get_reply_message_id(event):
    return (
        getattr(event.message, "quoted_message_id", None)
        or getattr(event.message, "quotedMessageId", None)
    )


def get_message_id(event):
    return getattr(event.message, "id", None)


def get_current_chat_id(event):
    ids = get_source_ids(event)
    return ids["group_id"] or ids["room_id"] or ids["user_id"]


def is_private_chat(event) -> bool:
    """คืน True เฉพาะแชทส่วนตัวกับ OA เท่านั้น

    ไม่ผูกกับ source.type อย่างเดียว เพราะบางเคส SDK/โครงสร้าง event อาจคืน None
    ถ้ามี user_id และไม่มี group_id/room_id ให้ถือว่าเป็นแชทส่วนตัว
    """
    ids = get_source_ids(event)
    return bool(ids.get("user_id")) and not ids.get("group_id") and not ids.get("room_id")


def is_backoffice_chat(event) -> bool:
    return get_current_chat_id(event) in BACKOFFICE_GROUP_IDS


def is_group_or_room_chat(event) -> bool:
    ids = get_source_ids(event)
    return bool(ids.get("group_id") or ids.get("room_id"))


def is_front_chat(event) -> bool:
    """
    ห้องหน้าบ้าน = กลุ่ม/room ที่ใช้เล่นจริง
    - ห้ามเป็นแชทส่วนตัว
    - ห้ามเป็น BACKOFFICE_GROUP_ID
    - ถ้าตั้ง FRONT_GROUP_IDS ไว้ ต้องอยู่ในรายการที่อนุญาตเท่านั้น
    """
    chat_id = get_current_chat_id(event)
    if not is_group_or_room_chat(event):
        return False
    if is_backoffice_chat(event):
        return False
    if FRONT_GROUP_IDS:
        return chat_id in FRONT_GROUP_IDS
    return True


def current_round_chat_id():
    return STATE.get("chat_id")


def is_current_round_chat(event) -> bool:
    round_chat_id = current_round_chat_id()
    if not round_chat_id:
        # รองรับข้อมูลเก่าก่อนอัปเดต ถ้ายังไม่มี chat_id ให้ถือว่ายังไม่ล็อกห้อง
        return True
    return round_chat_id == get_current_chat_id(event)


def front_room_block_text(action: str = "ใช้คำสั่งนี้") -> str:
    if FRONT_GROUP_IDS:
        return (
            f"❌ {action}ได้เฉพาะกลุ่มหน้าบ้านที่ตั้งค่าไว้เท่านั้น\n"
            f"ห้องนี้ไม่อยู่ใน FRONT_GROUP_IDS"
        )
    return f"❌ {action}ได้เฉพาะกลุ่มหน้าบ้านเท่านั้น"


def cross_room_block_text(action: str = "ใช้คำสั่งนี้") -> str:
    return (
        f"❌ {action}ข้ามห้องไม่ได้\n"
        f"รอบนี้ต้องจัดการในกลุ่มหน้าบ้านที่เปิดรอบเท่านั้น"
    )


def can_use_backoffice_command(event, user_id: str) -> bool:
    # ใช้ได้ในกลุ่มหลังบ้าน หรือโดยแอดมินที่ระบุไว้ใน ENV
    return is_backoffice_chat(event) or is_admin(user_id)


def can_use_strict_backoffice_command(event) -> bool:
    """คำสั่งข้อมูลหลังบ้านที่ห้ามใช้ในหน้าบ้าน/แชทส่วนตัว ต้องอยู่ใน BACKOFFICE_GROUP_IDS เท่านั้น"""
    return is_backoffice_chat(event)


def strict_backoffice_only_text(command_name: str = "คำสั่งนี้") -> str:
    return (
        f"❌ {command_name} ใช้ได้เฉพาะกลุ่มหลังบ้านที่ตั้งค่าไว้เท่านั้น\n"
        "กรุณาใช้ในกลุ่ม BACKOFFICE_GROUP_ID / BACKOFFICE_GROUP_IDS"
    )


def money_text(value):
    return f"{float(value):,.2f}"




# ======================================================
# Bank account command
# ======================================================

BANK_ACCOUNT_NUMBER = "8650712584"
BANK_ACCOUNT_DISPLAY_NUMBER = "865-0712-584"
BANK_ACCOUNT_BANK = "กรุงไทย"
BANK_ACCOUNT_NAME = "กิตติพร ศักดิ์ศรี"
# ใช้บัญชีเดียวสำหรับเติมเครดิตอัตโนมัติเท่านั้น
# โค้ดจะใช้บัญชีนี้ตรวจ checkReceiver กับ Slip2Go และจะไม่รับบัญชีอื่น แม้ .env ยังมีบัญชีเก่าอยู่
SINGLE_AUTO_TOPUP_RECEIVER = {
    "bankName": BANK_ACCOUNT_BANK,
    "accountNumber": BANK_ACCOUNT_NUMBER,
    "accountNameTH": BANK_ACCOUNT_NAME,
    "accountNameEN": "",
    "accountNameENAliases": [],
}
# ดีเลคำสั่งบัญชีในกลุ่ม/ห้องเดียวกัน กันคนพิมพ์ บช/บัญชี รัว ๆ แล้วบอทตอบซ้ำ
# ปรับใน .env ได้ เช่น BANK_ACCOUNT_COOLDOWN_SECONDS=10
BANK_ACCOUNT_COOLDOWN_SECONDS = int(os.getenv("BANK_ACCOUNT_COOLDOWN_SECONDS", "10"))
BANK_ACCOUNT_COOLDOWN_CACHE = {}
BANK_BACKOFFICE_URL = os.getenv("BANK_BACKOFFICE_URL", "https://page.line.me/959grxyk").strip() or "https://page.line.me/959grxyk"


def is_bank_account_request(text: str) -> bool:
    """
    ลูกค้าขอบัญชี/เลขบัญชี/ช่องทางโอนเงิน
    รองรับคำสะกดที่พิมพ์บ่อย เช่น บช, บ/ช, บัญชี, บันชี
    """
    raw = (text or "").strip()
    clean = re.sub(r"\s+", "", raw).lower()
    if not clean:
        return False

    # คำสั้นมากให้รับเฉพาะตรงตัว เพื่อลดการชนกับคำสั่งอื่น
    exact_keywords = {
        "บช", "บ/ช", "บัญชี", "บันชี", "เลขบัญชี", "ขอบัญชี", "ขอบช", "ขอบ/ช",
        "บัญชีโอน", "บันชีโอน", "เลขโอน", "เลขบัญชีโอน", "ธนาคาร", "แบงค์", "bank", "scb",
    }
    if clean in exact_keywords:
        return True

    # ประโยคทั่วไปที่เกี่ยวกับการโอน/เติมเงิน
    contains_keywords = [
        "ขอเลขบัญชี", "ขอเลขบช", "ขอเลขบ/ช", "ส่งบัญชี", "ส่งบช", "แจ้งบัญชี",
        "บัญชีร้าน", "บันชีร้าน", "โอนเงิน", "โอนเข้า", "เติมเงิน", "ฝากเงิน",
        "เลขบัญชีร้าน", "เลขบชร้าน", "ไทยพาณิชย์", "scb",
    ]
    return any(k in clean for k in contains_keywords)


def bank_account_text() -> str:
    return (
        "📌💎⚡️สายฟ้า + Original💎💯💵\n"
        "━━━━━━━━━━━━━━\n\n"
        "🏦 แจ้งเลขบัญชีฝากเงิน\n\n"
        "🔢 เลขบัญชี : 8650712584 \n"
        "🏛 ธนาคาร : กรุงไทย\n"
        "👤 ชื่อบัญชี : กิตติพร ศักดิ์ศรี\n\n"
        "━━━━━━━━━━━━━━\n"
        "⚠️ เพื่อป้องกันมิจฉาชีพ\n"
        "ชื่อผู้ฝาก-ถอน ต้องเป็นชื่อเดียวกันเท่านั้น ✅"
    )

def bank_account_backoffice_flex():
    """Flex ปุ่มสีเขียวแบบเด้งแยกอีก 1 ข้อความ สำหรับคำสั่ง บช"""
    return {
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingTop": "8px",
            "paddingBottom": "8px",
            "paddingStart": "8px",
            "paddingEnd": "8px",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "height": "md",
                    "action": {
                        "type": "uri",
                        "label": "กดเข้าหลังบ้าน",
                        "uri": BANK_BACKOFFICE_URL,
                    },
                }
            ],
        },
    }


def can_use_bank_account_request_in_chat(event) -> bool:
    """คำสั่งบัญชีให้ใช้เฉพาะหน้าบ้านหรือแชทส่วนตัว ห้ามใช้ในกลุ่มหลังบ้าน"""
    return is_private_chat(event) or is_front_chat(event)


def should_skip_bank_account_by_cooldown(event) -> bool:
    """
    กันคำสั่ง บช/บัญชี ถูกพิมพ์รัวในแต่ละห้องหรือแชทส่วนตัว
    - กลุ่ม/room: แยกดีเลตาม group_id/room_id
    - แชทส่วนตัว: แยกดีเลตาม user_id
    - ค่าเริ่มต้นตอบได้ 1 ครั้งต่อ 10 วินาทีต่อห้อง/แชท
    """
    if BANK_ACCOUNT_COOLDOWN_SECONDS <= 0:
        return False

    chat_id = get_current_chat_id(event) or getattr(event.source, "user_id", None) or "unknown"
    key = f"bank_account:{chat_id}"
    now_ts = time.time()

    with STATE_LOCK:
        # ล้าง key เก่าบ้าง กัน dict โตไม่จำเป็น
        if len(BANK_ACCOUNT_COOLDOWN_CACHE) > 500:
            expired_keys = [
                k for k, last_ts in BANK_ACCOUNT_COOLDOWN_CACHE.items()
                if now_ts - float(last_ts or 0) > (BANK_ACCOUNT_COOLDOWN_SECONDS * 3)
            ]
            for k in expired_keys:
                BANK_ACCOUNT_COOLDOWN_CACHE.pop(k, None)

        last_ts = float(BANK_ACCOUNT_COOLDOWN_CACHE.get(key, 0) or 0)
        if now_ts - last_ts < BANK_ACCOUNT_COOLDOWN_SECONDS:
            return True

        BANK_ACCOUNT_COOLDOWN_CACHE[key] = now_ts
        return False


# ======================================================
# Withdraw / clear balance command
# ======================================================

WITHDRAWAL_COOLDOWN_SECONDS = int(os.getenv("WITHDRAWAL_COOLDOWN_SECONDS", "10"))
WITHDRAWAL_COOLDOWN_CACHE = {}


def parse_withdrawal_command(text: str):
    """
    คำสั่งลูกค้าสำหรับถอน/เคลียร์ยอด
    - ถอนทั้งหมด, เคลียร์ยอด = เคลียร์เครดิตผู้ใช้เป็น 0 แล้วส่ง Flex ยืนยัน
    - รอถอน = ส่ง Flex แจ้งสถานะ/ให้แจ้งหลังบ้าน โดยไม่แตะเครดิต
    """
    raw = (text or "").strip()
    clean = re.sub(r"\s+", "", raw).lower()
    if not clean:
        return None

    withdraw_all_keywords = {
        "ถอนทั้งหมด",
        "ถอนหมด",
        "ถอนยอดทั้งหมด",
        "ถอนเครดิตทั้งหมด",
        "เคลียร์ยอด",
        "เคลียยอด",
        "เครียร์ยอด",
        "เคลียร์เครดิต",
        "เคลียเครดิต",
    }
    wait_keywords = {
        "รอถอน",
        "รอถอนเงิน",
        "รายการรอถอน",
        "ถอนรอ",
    }

    if clean in withdraw_all_keywords:
        return "withdraw_all"
    if clean in wait_keywords:
        return "wait_withdraw"
    return None


def is_withdrawal_command(text: str) -> bool:
    return parse_withdrawal_command(text) is not None


def can_use_withdrawal_command_in_chat(event) -> bool:
    """คำสั่งถอน/เคลียร์ยอดให้ใช้เฉพาะหน้าบ้านหรือแชทส่วนตัว ห้ามใช้ในกลุ่มหลังบ้าน"""
    return is_private_chat(event) or is_front_chat(event)


def should_skip_withdrawal_by_cooldown(event) -> bool:
    """กันคำสั่งถอน/รอถอนถูกพิมพ์รัวในห้องเดียวกัน"""
    if WITHDRAWAL_COOLDOWN_SECONDS <= 0:
        return False

    chat_id = get_current_chat_id(event) or getattr(event.source, "user_id", None) or "unknown"
    key = f"withdrawal:{chat_id}"
    now_ts = time.time()

    with STATE_LOCK:
        if len(WITHDRAWAL_COOLDOWN_CACHE) > 500:
            expired_keys = [
                k for k, last_ts in WITHDRAWAL_COOLDOWN_CACHE.items()
                if now_ts - float(last_ts or 0) > (WITHDRAWAL_COOLDOWN_SECONDS * 3)
            ]
            for k in expired_keys:
                WITHDRAWAL_COOLDOWN_CACHE.pop(k, None)

        last_ts = float(WITHDRAWAL_COOLDOWN_CACHE.get(key, 0) or 0)
        if now_ts - last_ts < WITHDRAWAL_COOLDOWN_SECONDS:
            return True

        WITHDRAWAL_COOLDOWN_CACHE[key] = now_ts
        return False


def withdrawal_done_flex(amount=None, command_kind: str = "withdraw_all"):
    """Flex แจ้งทำรายการถอน/เคลียร์ยอดเรียบร้อย"""
    subtitle = "ถอนทั้งหมด / เคลียร์ยอด"
    if command_kind == "wait_withdraw":
        subtitle = "รอถอน"

    amount_text = "-"
    if amount is not None:
        try:
            amount_text = f"{int(amount):,} เครดิต"
        except Exception:
            amount_text = str(amount)

    contents = [
        {
            "type": "text",
            "text": "✅ ทำรายการถอนยอดแล้ว",
            "weight": "bold",
            "size": "lg",
            "color": "#166534",
            "wrap": True,
        },
        {
            "type": "text",
            "text": subtitle,
            "size": "sm",
            "color": "#6B7280",
            "margin": "sm",
            "wrap": True,
        },
        {
            "type": "separator",
            "margin": "md",
        },
        {
            "type": "text",
            "text": "ระบบทำรายการถอนยอดให้ทั้งหมดแล้วนะครับ หากมียอดตกหล่นแจ้งหลังบ้านได้เลย",
            "size": "md",
            "color": "#111827",
            "wrap": True,
            "margin": "md",
        },
    ]

    if amount is not None:
        contents.append({
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F3F4F6",
            "cornerRadius": "md",
            "paddingAll": "10px",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": "ยอดที่ระบบเคลียร์",
                    "size": "xs",
                    "color": "#6B7280",
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": amount_text,
                    "size": "xl",
                    "weight": "bold",
                    "color": "#111827",
                    "wrap": True,
                },
            ],
        })

    return {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "contents": contents,
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "แจ้งหลังบ้าน",
                        "uri": BANK_BACKOFFICE_URL,
                    },
                }
            ],
        },
    }


def is_admin_help_request(text: str) -> bool:
    clean = re.sub(r"\s+", "", (text or "").strip()).lower()
    return clean in {"คำสั่ง", "คําสั่ง", "command", "commands", "admincommands"}


def admin_command_help_text() -> str:
    return (
        "📌 คำสั่งแอดมินทั้งหมด\n"
        "ใช้ได้เฉพาะกลุ่มหลังบ้านเท่านั้น\n\n"
        "👤 ข้อมูล/ระบบ\n"
        "- คำสั่ง = แสดงรายการคำสั่งแอดมินทั้งหมด\n"
        "- GETID = ดู groupId / roomId ของห้องนี้\n"
        "- UID = ดู UID ของผู้พิมพ์\n"
        "- UIDLIST = ดูรายชื่อสมาชิกทั้งหมด\n"
        "- C @ชื่อไลน์ = เช็กชื่อ LINE / ID สมาชิก / ยอดเงินของคนที่แท็ก ใช้ได้ทั้งหลังบ้านและหน้าบ้าน เฉพาะแอดมิน\n"
        "- CALL = ดูรายชื่อลูกค้าที่ระบบรู้จัก/เรียกดูข้อมูลสมาชิก\n"
        "- เพิ่มแอดมิน @ชื่อไลน์ = เพิ่มแอดมินจากการ mention\n"
        "- List / เช็คแอดมิน = ดูรายชื่อแอดมินทั้งหมดในระบบ\n\n"
        "💰 เครดิต/กำไร\n"
        "- $+ เลขสมาชิก จำนวนเงิน = เพิ่มเครดิต เช่น $+ 1 1000\n"
        "- $- เลขสมาชิก จำนวนเงิน = หักเครดิต เช่น $- 1 1000\n"
        "- ยอดกำไร / กำไร / profit = ดูยอดกำไรสะสม\n"
        "- ล้างกำไร = รีเซ็ตยอดกำไรสะสม\n\n"
        "💸 คำสั่งลูกค้าเกี่ยวกับถอน\n"
        "- ถอนทั้งหมด / เคลียร์ยอด = เคลียร์เครดิตลูกค้าเป็น 0 และส่ง Flex แจ้งทำรายการถอน\n"
        "- รอถอน = ส่ง Flex แจ้งสถานะถอน โดยไม่แตะเครดิต\n"
        "หมายเหตุ: ใช้ได้เฉพาะหน้าบ้านหรือแชทส่วนตัว ไม่ทำงานในหลังบ้าน\n\n"
        "🚀 จัดการค่าย/รอบ\n"
        "- เปิด ชื่อค่าย = เปิดรอบใหม่ ระบบแยกรอบให้เอง ไม่ต้องใช้ฐาน\n"
        "- ปิด = ปิดค่ายล่าสุดที่เปิดรับอยู่\n"
        "- ปิด ชื่อค่าย = ปิดค่ายที่ระบุชื่อ\n"
        "- เล่นต่อ = เปิดให้เล่นต่อในค่ายล่าสุดที่ปิดอยู่\n"
        "- เล่นต่อ ชื่อค่าย = เปิดให้ค่ายที่ระบุเล่นต่อ\n"
        "- เปลี่ยนค่าย ชื่อค่ายใหม่ = เปลี่ยนชื่อค่ายที่เปิดผิด\n\n"
        "📊 ราคาช่าง/ผล/ตรวจรอบ\n"
        "- ราคาช่าง 330-360 = ตั้งราคาช่างค่ายล่าสุดที่ปิดอยู่\n"
        "- ราคาช่าง ชื่อค่าย 330-360 = ตั้งราคาช่างตามชื่อค่าย\n"
        "- ราคาช่าง ไม่ต่อย / ราคาช่าง ไม่ตี = ตั้งสถานะช่างไม่มีราคา แล้วต้องพิมพ์ ยืนยัน\n"
        "- ราคาช่าง ชื่อค่าย ไม่ต่อย / ไม่ตี = ตั้งสถานะช่างไม่มีราคาตามชื่อค่าย\n"
        "- ยืนยัน ชื่อค่าย = ยืนยันราคาช่างไม่มีราคา หรือยืนยัน CR ของค่ายนั้น\n"
        "- แจ้งผล 365 / ผล 365 = แจ้งผลเมื่อมีค่ายเดียวที่ยังค้างอยู่\n"
        "- แจ้งผล ชื่อค่าย 365 = แจ้งผลตามชื่อค่าย กรณีมีหลายค่าย\n"
        "- แจ้งผล ชื่อค่าย จาวทุกแผล = คืนทุนทุกแผลตามชื่อค่าย\n"
        "- แจ้งผล ชื่อค่าย บั้งไฟหาย = คืนทุนทุกแผลกรณีบั้งไฟหายตามชื่อค่าย\n"
        "- CK = ตรวจสถานะรอบปัจจุบัน เมื่อมีค่ายเดียวที่ค้างอยู่\n"
        "- CK ชื่อค่าย = ตรวจสถานะตามชื่อค่าย\n"
        "- CK รวม = ดูสถานะทุกค่าย\n"
        "- คู่ติด / คู่รอบนี้ = ดูว่ารอบปัจจุบันใครติดกับใครบ้าง\n"
        "- คู่ติด ชื่อค่าย = ดูคู่ติดตามชื่อค่าย\n"
        "- listplay = ดูสมาชิกที่เล่นกันแบบสั้น เช่น นาย A เล่น 320-350ล500 กับ นาย B\n"
        "- listplay ชื่อค่าย = ดูรายการเล่นแบบสั้นตามชื่อค่าย\n"
        "- สกอ / สกอร์ / รายการ = ดูสรุปผลค่ายที่แจ้งผลแล้วแบบ Flex\n"
        "- CR ชื่อค่าย / ยืนยัน ชื่อค่าย = เคลียร์รอบตามชื่อค่าย\n\n"
        "↩️ ย้อนผล/ล้างออเดอร์\n"
        "- ย้อนผล ชื่อค่าย = ขอคืนผลที่แจ้งผิด\n"
        "- ยืนยันย้อนผล ชื่อค่าย = ยืนยันการย้อนผล\n"
        "- ยกเลิกย้อนผล ชื่อค่าย = ยกเลิกคำขอย้อนผล\n"
        "- ล้างออเดอร์ = ล้างบิลทั้งหมดและเริ่มเลขออเดอร์ใหม่ที่ #1\n"
        "- ตั้งเลขออเดอร์ 100 = ล้างบิลและเริ่มเลขออเดอร์ใหม่ที่ #100\n"
        "- ล้าง round_backups = ล้างไฟล์สำรองรอบเก่าในโฟลเดอร์ round_backups\n\n"
        "⚠️ ถ้ามีหลายค่ายค้างอยู่ ให้ใช้ชื่อค่ายแทนฐาน เช่น แจ้งผล แอ๊ดเทวดา 350 / ราคาช่าง แอ๊ดเทวดา 330-360 / ย้อนผล แอ๊ดเทวดา"
    )


# ======================================================
# Rules / how to play command
# ======================================================

def is_rules_request(text: str) -> bool:
    clean = re.sub(r"\s+", "", (text or "").strip()).lower()
    return clean in {"กต", "กติกา"}


RULES_IMAGE_URL = "https://img2.pic.in.th/26d02e16-f7cf-403f-92ed-2a8eed65d8d1.png"


def rules_flex() -> dict:
    return {
        "type": "bubble",
        "size": "giga",
        "hero": {
            "type": "image",
            "url": RULES_IMAGE_URL,
            "size": "full",
            "aspectRatio": "2:3",
            "aspectMode": "fit",
            "backgroundColor": "#FFFFFF",
            "action": {
                "type": "uri",
                "uri": RULES_IMAGE_URL,
            },
        },
    }


def rules_text() -> str:
    return (
        "📜✨ วิธีการเล่นบั้งไฟ ✨📜\n\n"
        "✅ คีย์เวิร์ดที่ใช้\n"
        "🚀 ช่างไล่: ชล, ล, ไล่, +5ชล, +5ล, -5ชล, -5ล\n"
        "🛬 ช่างยั่ง/ถอย: ชถ, ถ, ย, ยั่ง, ถอย, ช่างรับ, รับช่าง, ช่างถอย, +5ถ, -5ถ\n"
        "🤝 ยืนยันแผล: ต, ติด, ครับ, เค, จ้า, ติดจ้า, ตต, ตด, ตอด, ตอก, จ\n\n"
        "📌 คำสั่งปรับราคาช่างเฉพาะเลขหน้า/เลขหลัง/ทั้งช่วง\n"
        "ก+5ล100 / เกิบ+5ล100 = บวกเลขหน้า เช่น 330-360 เป็น 335-360\n"
        "ม+5ล100 / หมวก+5ล100 = บวกเลขหลัง เช่น 330-360 เป็น 330-365\n"
        "กม+5ล100 / กม-5ถ100 = บวก/ลบทั้งช่วง เช่น 335-365 หรือ 325-355\n"
        "ก+5ม-10ล100 / เกิบ-5หมวก+10ย100 = ปรับเลขหน้าและเลขหลังคนละค่า\n"
        "ถ้าเลขหน้า = เลขหลัง จะเป็นราคาแผลเดียว เช่น 315\n"
        "ถ้าเกิบมากกว่าหมวก ระบบจะตีจาวและคืนยอดหลังสรุปผล\n"
        "ข้อควรระวัง: ก/เกิบ ต้องอยู่หน้า ม/หมวก เท่านั้น\n"
        "ใช้ได้ทั้ง + และ - เช่น ม-5ถ100 / ก-5ล100 / กม+5ล100\n\n"
        "📌 คำสั่งเล่นพิเศษ\n"
        "ช่างไม่ชนะ100 / ช่างบ่ชนะ100 / ช่างบ้ชนะ100\n"
        "= ได้เมื่อผลไม่เกินเลขหลังของราคาช่าง\n"
        "ช่างแพ้100 = ได้เฉพาะเมื่อผลต่ำกว่าเลขหน้าของราคาช่าง\n\n"
        "📌 เปิดราคาเอง / เล่นราคาตัวเลขเอง\n"
        "‼️ ต้องใส่ตัวเลขเป็นจำนวนเต็มเท่านั้น\n"
        "ตัวอย่างราคา: 👇🏻\n"
        "320-340ล / 320-340ถ\n"
        "320/340ล / 320/340ถ\n"
        "ตัว320-340ล / ตัว320/340ถ\n"
        "340-375ล / 340-375ถ\n"
        "ตัวอย่างส่งเล่นพร้อมยอดเงิน: 👇🏻\n"
        "320-340ล500\n"
        "320/340ถ500\n"
        "ตัว320-340ล500\n"
        "400ชล500 / 400ชถ500\n\n"
        "‼️ กรณีเล่นเผื่อช่างไม่ต่อย\n"
        "ให้พิมพ์ ชตย ไว้หลังราคา และต้องมีเครดิตเหลือสำรองด้วยนะครับ\n"
        "ตัวอย่าง: 👇🏻\n"
        "345-385ล500 ชตย\n"
        "345-385ถ500 ชตย\n"
        "360-390ล100 ชตย\n"
        "360-390ถ100 ชตย\n\n"
        "📌 การเล่นใส่ราคาตัวเงิน\n"
        "ให้ใส่ตัวเลขแบบนี้เท่านั้น\n"
        "1000 2000 3000 4000\n"
        "5000 10000\n"
        "❌ ห้ามใส่ , เด็ดขาด\n"
        "เช่น 1,000 ระบบจะจับเป็น 1 บาท\n"
        "✅ ให้พิมพ์ 1000 เท่านั้น\n\n"
        "📌 วิธียกเลิกแผล\n"
        "พิมพ์คำว่า วิธียก เพื่อดูขั้นตอนยกเลิกแผล\n\n"
        "⚠️ หมายเหตุ\n"
        "พิมพ์ราคาหรือยอดผิด ระบบอาจไม่รับรายการ กรุณาตรวจสอบก่อนยืนยันแผลนะครับ"
    )


def is_cancel_help_request(text: str) -> bool:
    clean = re.sub(r"\s+", "", (text or "").strip()).lower()
    return clean in {"วิธียก", "วิธียกเลิก", "วิธียกแผล", "ยกแผล"}


def cancel_help_text() -> str:
    return (
        "📌 วิธียกเลิกแผล 🚀\n\n"
        "ข้อความจะส่งไปยังคู่เล่นที่ติดกัน\n"
        "ถ้าคู่เล่นตอบ ยอมรับคำขอ = ยกเลิก ❌\n"
        "แต่ถ้าคู่เล่นปฏิเสธคำขอ = ได้เล่น ✅\n\n"
        "วิธีใช้งาน:\n"
        "1️⃣ กดปุ่ม แตะเพื่อขอยกเลิก ในบิลที่จับคู่สำเร็จ\n"
        "2️⃣ รอคู่เล่นกดยืนยันหรือปฏิเสธ\n\n"
        "⚠️ หมายเหตุ\n"
        "- ยกเลิกได้เฉพาะช่วงที่รอบยังเปิดอยู่\n"
        "- หลังปิดรอบแล้ว ไม่สามารถยกเลิกได้\n"
        "- ถ้าอีกฝ่ายปฏิเสธ รายการจะยังมีผลตามเดิม"
    )


# ======================================================
# New member instruction command
# ======================================================

def is_new_member_instruction_request(text: str) -> bool:
    """คำสั่งแจ้งวิธีสำหรับสมาชิกใหม่ / คนมาใหม่ / คนเข้าใหม่"""
    clean = re.sub(r"\s+", "", (text or "").strip()).lower()
    return clean in {"สมาชิกใหม่", "มาใหม่", "เข้าใหม่", "newmember"}


def new_member_instruction_text() -> str:
    return (
        "📌✨ สำหรับสมาชิกใหม่ ✨📌\n\n"
        "ก่อนใช้งาน กรุณาให้ลูกค้าทักไลน์ OA หลังบ้าน\n"
        "ชื่อ: สรุปยอดบั้งไฟสายฟ้า ก่อนนะคะ\n\n"
        "✅ เมื่อลูกค้าทัก OA แล้ว ระบบจะรู้จักสมาชิก\n"
        "✅ จึงจะสามารถเห็นรายการจับคู่ / รายการเล่นของตัวเองได้\n\n"
        "🚀 ขอบคุณค่ะ"
    )


def user_credit_amount(user: dict) -> int:
    """คืนยอดเครดิตเป็นจำนวนเต็มแบบปลอดภัย กันค่า None/string ทำให้เทียบผิด"""
    try:
        return int((user or {}).get("credit", 0) or 0)
    except Exception:
        return 0


def insufficient_credit_warning(user: dict, required_amount: int, play_text: str = None, is_chty: bool = False, action: str = "จับคู่"):
    """
    ข้อความแจ้งเตือนเมื่อเครดิตไม่พอ
    ใช้ทั้งตอนผู้ติดพิมพ์ ติด/ต300 และตอนเจ้าของโพสต์ยืนยันจับคู่
    เพื่อกันกรณีคนติดมียอดไม่พอ หรือยอดถูกใช้กับรายการอื่นไปก่อนยืนยัน
    """
    user = user or {}
    name = user.get("line_name") or user.get("name") or "ผู้เล่น"
    member_no = user.get("member_no")
    current_credit = user_credit_amount(user)

    try:
        required_amount = int(required_amount or 0)
    except Exception:
        required_amount = 0

    lines = [
        "❌ เครดิตไม่พอ ระบบไม่รับรายการนี้",
        "ยังไม่สร้างบิล และยังไม่หักเครดิตค่ะ",
        "",
        f"ผู้เล่น: {name}" + (f" | ID {member_no}" if member_no else ""),
    ]

    if play_text:
        lines.append(f"แผลเล่น: {play_text}")

    lines.extend([
        f"ยอดคงเหลือ: {current_credit:,}",
        f"ยอดที่ต้องใช้สำหรับ{action}: {required_amount:,}",
    ])

    if current_credit <= 0:
        lines.append("กรุณาเติมเครดิตก่อน แล้วตอบกลับโพสต์เดิมใหม่อีกครั้ง")
    elif current_credit < required_amount:
        lines.append(f"กรุณาเติมเครดิตด้วยนะคะ ")

    if is_chty:
        lines.append("หมายเหตุ: แผล ชตย ต้องมีเครดิตสำรองไว้ก่อนจับคู่")

    return "\n".join(lines)


def normalize_side(alias: str) -> str:
    if alias in CHASE_ALIASES:
        return "ชนะ"
    if alias in RETREAT_ALIASES:
        return "แพ้"
    if alias in NO_WIN_ALIASES:
        return "ช่างไม่ชนะ"
    if alias in ONLY_LOSE_ALIASES:
        return "ช่างแพ้"
    return ""


def opposite_side(side: str) -> str:
    if side == "ชนะ":
        return "แพ้"
    if side == "แพ้":
        return "ชนะ"
    if side == "ช่างไม่ชนะ":
        return "ช่างชนะ"
    if side == "ช่างชนะ":
        return "ช่างไม่ชนะ"
    if side == "ช่างแพ้":
        return "ช่างไม่แพ้"
    if side == "ช่างไม่แพ้":
        return "ช่างแพ้"
    return ""


def is_special_market_side(side: str) -> bool:
    return side in {"ช่างไม่ชนะ", "ช่างชนะ", "ช่างแพ้", "ช่างไม่แพ้"}


def format_play_text(
    side: str,
    plus: int,
    price_adjust_target: str = None,
    price_adjust_min=None,
    price_adjust_max=None,
) -> str:
    bound_adjust_targets = {"min", "max", "both", "bounds"}
    if side == "ชนะ":
        base = "ล" if price_adjust_target in bound_adjust_targets else "ชล"
    elif side == "แพ้":
        base = "ถ" if price_adjust_target in bound_adjust_targets else "ชถ"
    elif side == "ช่างไม่ชนะ":
        base = "ช่างไม่ชนะ"
    elif side == "ช่างชนะ":
        base = "ช่างชนะ"
    elif side == "ช่างแพ้":
        base = "ช่างแพ้"
    elif side == "ช่างไม่แพ้":
        base = "ช่างไม่แพ้"
    else:
        base = "-"

    try:
        plus = int(plus or 0)
    except Exception:
        plus = 0

    def signed_text(value):
        try:
            value = int(value or 0)
        except Exception:
            value = 0
        sign = "+" if value >= 0 else ""
        return f"{sign}{value}"

    if price_adjust_target == "bounds":
        return f"ก{signed_text(price_adjust_min)}ม{signed_text(price_adjust_max)}{base}"

    if price_adjust_target in {"min", "max", "both"}:
        prefix = "กม" if price_adjust_target == "both" else ("ม" if price_adjust_target == "max" else "ก")
        sign = "+" if plus >= 0 else ""
        return f"{prefix}{sign}{plus}{base}"

    if plus != 0:
        sign = "+" if plus > 0 else ""
        return f"{sign}{plus}{base}"
    return base

def format_offer_play_text(data: dict) -> str:
    """แสดงข้อความแผลจาก offer/post/match โดยเก็บรูปแบบเลข 2 ตัวเดิมไว้"""
    data = data or {}
    if data.get("is_two_digit_price"):
        raw_alias = data.get("raw_alias") or ("ล" if data.get("maker_side") == "ชนะ" else "ถ")
        text = f"{data.get('two_digit_min_token')}-{data.get('two_digit_max_token')}{raw_alias}"
    elif data.get("is_custom_price") and data.get("custom_price_min") is not None and data.get("custom_price_max") is not None:
        raw_alias = data.get("raw_alias") or ("ล" if data.get("maker_side") == "ชนะ" else "ถ")
        text = f"{format_price_range_text(data.get('custom_price_min'), data.get('custom_price_max'))}{raw_alias}"
    else:
        text = format_play_text(
            data.get("maker_side", ""),
            data.get("plus", 0),
            data.get("price_adjust_target"),
            data.get("price_adjust_min"),
            data.get("price_adjust_max"),
        )
    if data.get("only_when_no_price"):
        text += " ชตย"
    return text


def two_digit_token_to_offset(token) -> int:
    """
    แปลงเลข 2 ตัว/เลขย่อเป็น offset จากฐานเริ่มต้น
    - 30 -> 30
    - 70 -> 70
    - 3  -> 30
    - 7  -> 70
    - 00 -> 0
    """
    raw = str(token or "").strip()
    if not re.fullmatch(r"\d{1,2}", raw):
        raise ValueError("invalid two digit token")
    if len(raw) == 1:
        return int(raw) * 10
    return int(raw)


def two_digit_tokens_to_price_range(start_no, min_token, max_token, base_min=None, base_max=None):
    """
    เริ่มต้น1/2/3 แล้วแปลงแผลเลข 2 ตัวเป็นราคาเต็ม

    กฎเดิม:
    - เริ่มต้น3 + 30-70 = 330-370
    - เริ่มต้น3 + 50-00 = 350-400 ถ้าไม่มีราคาช่างให้เทียบ

    กฎเพิ่มสำหรับราคารูดลง:
    - ถ้ามีราคาช่างให้เทียบ ระบบจะสร้างตัวเลือกทั้งฝั่งบนและฝั่งล่างของเลขเริ่มต้น
    - ตัวอย่าง ราคาช่าง 300-330, เริ่มต้น3, ลูกค้าเล่น 80-00
      ระบบจะเทียบ 280-300 กับ 380-400 แล้วเลือก 280-300 เพราะใกล้ราคาช่างกว่า
    """
    start_no = int(start_no or 0)
    if start_no not in {1, 2, 3}:
        return None, None

    anchor = start_no * 100
    start_offset = two_digit_token_to_offset(min_token)
    end_offset = two_digit_token_to_offset(max_token)

    def build_range(base):
        price_min = base + start_offset
        price_max = base + end_offset
        if end_offset < start_offset:
            price_max += 100
        return int(price_min), int(price_max)

    # default candidate: พฤติกรรมเดิมของระบบ เช่น เริ่มต้น3 + 80-00 = 380-400
    candidates = [build_range(anchor)]

    # lower candidate: รองรับราคารูดลงเข้าหาเลขเริ่มต้น เช่น 80-00 = 280-300
    lower_base = anchor - 100
    if lower_base >= 0:
        candidates.append(build_range(lower_base))

    # ลบตัวเลือกซ้ำโดยยังคงลำดับเดิมไว้
    unique_candidates = []
    for item in candidates:
        if item not in unique_candidates:
            unique_candidates.append(item)
    candidates = unique_candidates

    # ถ้าไม่มีราคาช่างให้เทียบ ให้ใช้กฎเดิม 100% เพื่อไม่กระทบพฤติกรรมเก่า
    if base_min is None or base_max is None:
        return candidates[0]

    try:
        base_min = int(base_min)
        base_max = int(base_max)
    except Exception:
        return candidates[0]

    if base_min > base_max:
        base_min, base_max = base_max, base_min

    def interval_gap(price_min, price_max):
        """0 ถ้าช่วงราคาเล่นชน/ทับราคาช่าง, ถ้าไม่ชนให้คืนระยะห่าง"""
        if price_max < base_min:
            return base_min - price_max
        if price_min > base_max:
            return price_min - base_max
        return 0

    base_center = (base_min + base_max) / 2

    def score(candidate):
        price_min, price_max = candidate
        price_center = (price_min + price_max) / 2
        gap = interval_gap(price_min, price_max)
        center_gap = abs(price_center - base_center)

        # gap สำคัญกว่า center_gap เพื่อให้ช่วงที่แตะราคาช่างจริงชนะก่อน
        return (gap, center_gap, abs(price_min - base_min) + abs(price_max - base_max))

    return min(candidates, key=score)


def match_needs_two_digit_start(match: dict) -> bool:
    return bool((match or {}).get("is_two_digit_price"))


def round_has_two_digit_entries(round_id: str) -> bool:
    for match in list(MATCHES.values()):
        if match.get("round_id") == round_id and match.get("status") == "matched" and match_needs_two_digit_start(match):
            return True
    for post in list(POSTS.values()):
        if post.get("round_id") == round_id and post.get("status") in {"open", "closed"} and post.get("is_two_digit_price"):
            return True
    return False


def count_two_digit_matches(round_id: str) -> int:
    return sum(
        1 for match in MATCHES.values()
        if match.get("round_id") == round_id
        and match.get("status") == "matched"
        and match_needs_two_digit_start(match)
    )


def two_digit_unresolved_warning() -> str:
    if not round_has_two_digit_entries(STATE.get("round_id")):
        return ""
    if state_two_digit_start_text(STATE) != "-":
        return ""
    return (
        "ยังมีแผลเลข 2 ตัวในรอบนี้ แต่ยังไม่ได้แจ้ง เริ่มต้น1/2/3\n"
        "กรุณาแจ้งก่อนสรุปผล เช่น เริ่มต้น3"
    )


def format_price_range_text(price_min, price_max):
    if price_min is None or price_max is None:
        return "-"
    if price_min == price_max:
        return str(price_min)
    return f"{price_min}-{price_max}"


def current_price_text():
    return state_price_text(STATE)


def public_price_text():
    return state_public_price_text(STATE)


def public_result_message(result_text):
    """ข้อความประกาศผลแบบสั้นในกลุ่ม ตามรูปแบบที่ต้องการ"""
    return (
        f"ค่าย: {STATE.get('camp_name') or '-'}\n\n"
        f"ผล : {result_text}///\n"
        f"ราคา : {public_price_text()}\n\n"
        f"🚀🚀🚀🚀🚀"
    )

def public_result_status_info(result_text, st: dict = None):
    """
    สถานะประกาศผลแบบ Flex Premium Card
    - ผลมากกว่าราคาช่าง = ชนะ ✅✅
    - ผลต่ำกว่าราคาช่าง = แพ้ ❌❌
    - ผลอยู่ในช่วงราคาช่าง / ผลพิเศษ = จาว ⛔⛔

    หมายเหตุ: Flex นี้ต้องไม่มีคำว่า เริ่มต้น และไม่ใช้คำว่า เสมอ
    """
    st = st or STATE

    base_palette = {
        "word": "จาว",
        "icons": "⛔⛔",
        "main_color": "#111B4D",
        "result_color": "#4F46E5",
        "icon_color": "#DC2626",
        "page_bg": "#EEF2FF",
        "outer_bg": "#6366F1",
        "card_bg": "#FFFFFF",
        "card_border": "#C7D2FE",
        "accent_bg": "#EEF2FF",
        "accent_line": "#C4B5FD",
        "shadow_color": "#4338CA",
    }

    # ผลพิเศษ เช่น จาวทุกแผล / บั้งไฟหาย ให้ขึ้นเป็น จาว ทันที
    try:
        result_value = int(result_text)
    except Exception:
        return dict(base_palette)

    try:
        base_min = int(st.get("base_min"))
        base_max = int(st.get("base_max"))
    except Exception:
        return dict(base_palette)

    if result_value > base_max:
        info = dict(base_palette)
        info.update({
            "word": "ชนะ",
            "icons": "✅✅",
            "main_color": "#065F46",
            "result_color": "#059669",
            "icon_color": "#16A34A",
            "page_bg": "#ECFDF5",
            "outer_bg": "#10B981",
            "card_border": "#A7F3D0",
            "accent_bg": "#D1FAE5",
            "accent_line": "#6EE7B7",
            "shadow_color": "#047857",
        })
        return info

    if result_value < base_min:
        info = dict(base_palette)
        info.update({
            "word": "แพ้",
            "icons": "❌❌",
            "main_color": "#7F1D1D",
            "result_color": "#DC2626",
            "icon_color": "#DC2626",
            "page_bg": "#FFF1F2",
            "outer_bg": "#F43F5E",
            "card_border": "#FECDD3",
            "accent_bg": "#FFE4E6",
            "accent_line": "#FDA4AF",
            "shadow_color": "#BE123C",
        })
        return info

    return dict(base_palette)


def state_public_price_text_no_start(st: dict) -> str:
    """ราคาช่างสำหรับ Flex แจ้งผลเท่านั้น: ห้ามต่อท้ายคำว่า เริ่มต้น"""
    if not st:
        return "-"
    if st.get("price_mode") == "no_price":
        return st.get("no_price_reason") or "ไม่ออก"
    if st.get("base_min") is not None and st.get("base_max") is not None:
        return format_price_range_text(st.get("base_min"), st.get("base_max"))
    return "-"


def public_result_flex(result_text, st: dict = None):
    """
    Flex ประกาศผลแบบ Mobile Fit
    ปรับให้พอดีกับหน้าจอโทรศัพท์ อ่านง่าย ไม่ล้น ไม่ยัดแน่น
    แสดงเฉพาะ 3 บรรทัดตามที่กำหนด:
    1) ชนะ/แพ้/จาว + ผล + emoji
    2) 🚀 ชื่อค่าย 🚀
    3) ราคาช่าง xxx-xxx

    หมายเหตุ:
    - ไม่มีคำว่า เริ่มต้น ใน Flex นี้
    - ไม่ใช้คำว่า เสมอ ให้ใช้ จาว เท่านั้น
    """
    st = st or STATE
    info = public_result_status_info(result_text, st)
    camp_name = st.get("camp_name") or "-"
    price_text = state_public_price_text_no_start(st)
    result_display = str(result_text).strip()
    headline_word = info.get("word") or "จาว"
    headline_icons = info.get("icons") or "⛔⛔"

    if headline_word == "เสมอ":
        headline_word = "จาว"

    # รวมเป็น text เดียว เพื่อให้ LINE shrink-to-fit ทั้งบรรทัดบนมือถือ
    # แก้ปัญหาแยก 3 ช่องแล้วเบียด / ล้น / ดูไม่สมดุล
    headline_text = f"{headline_word} {result_display}{headline_icons}"

    # สีพื้นหลังนุ่มลงเล็กน้อย ให้ดูสวยแต่ไม่แสบตาบนจอโทรศัพท์
    outer_bg = info.get("outer_bg") or "#6366F1"
    card_bg = "#FFFFFF"
    headline_color = info.get("result_color") or "#4F46E5"
    border_color = info.get("card_border") or "#C7D2FE"
    accent_line = info.get("accent_line") or "#C4B5FD"

    return {
        "type": "bubble",
        # mega จะพอดีกับจอโทรศัพท์กว่า giga และยังดูใหญ่พอสำหรับประกาศผล
        "size": "mega",
        "styles": {
            "body": {"backgroundColor": outer_bg}
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "12px",
            "backgroundColor": outer_bg,
            "contents": [
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": card_bg,
                    "cornerRadius": "24px",
                    "borderWidth": "1px",
                    "borderColor": border_color,
                    "paddingAll": "18px",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": headline_text,
                            "size": "4xl",
                            "weight": "bold",
                            "color": headline_color,
                            "align": "center",
                            "wrap": False,
                            "maxLines": 1,
                            "adjustMode": "shrink-to-fit",
                        },
                        {
                            "type": "text",
                            "text": f"🚀 {camp_name} 🚀",
                            "size": "xl",
                            "weight": "bold",
                            "color": "#172554",
                            "align": "center",
                            "wrap": False,
                            "maxLines": 1,
                            "adjustMode": "shrink-to-fit",
                            "margin": "sm",
                        },
                        {
                            "type": "separator",
                            "color": accent_line,
                            "margin": "md",
                        },
                        {
                            "type": "text",
                            "text": f"ราคาช่าง {price_text}",
                            "size": "xl",
                            "weight": "bold",
                            "color": "#111827",
                            "align": "center",
                            "wrap": False,
                            "maxLines": 1,
                            "adjustMode": "shrink-to-fit",
                            "margin": "md",
                        }
                    ],
                }
            ],
        },
    }

def public_result_reply_payload(result_text, st: dict = None):
    st = st or STATE
    price_text = state_public_price_text_no_start(st)
    return {
        "_reply_type": "flex",
        "alt_text": f"แจ้งผล {st.get('camp_name') or '-'} | ผล {result_text} | ราคา {price_text}",
        "flex": public_result_flex(result_text, st),
        "fallback_text": public_result_message(result_text),
    }


def is_result_flex_reply_payload(value) -> bool:
    return isinstance(value, dict) and value.get("_reply_type") == "flex" and bool(value.get("flex"))

def is_scoreboard_command(text: str) -> bool:
    """คำสั่งดูสกอ/รายการผลรวมหลังแอดมินแจ้งผลแล้ว"""
    clean = re.sub(r"\s+", "", (text or "").strip()).lower()
    return clean in {"สกอ", "สกอร์", "score", "scores", "รายการ"}


def scoreboard_status_from_round(st: dict) -> dict:
    """คืนสถานะ ชนะ/แพ้/จาว + emoji ของค่ายจากผลและราคาช่าง"""
    st = st or {}
    result_text = st.get("result")
    info = public_result_status_info(result_text, st)
    word = info.get("word") or "จาว"
    icons = info.get("icons") or "⛔⛔"
    color = info.get("result_color") or "#4F46E5"
    if word == "เสมอ":
        word = "จาว"
    if word not in {"ชนะ", "แพ้", "จาว"}:
        word = "จาว"
    return {"word": word, "icons": icons, "color": color}


def scoreboard_rows_for_chat(chat_id: str = None):
    """รวบรวมรอบที่แจ้งผลแล้ว เพื่อแสดงในคำสั่ง สกอ/รายการ"""
    rows = []
    for base_no, st in (ROUNDS or {}).items():
        if not isinstance(st, dict):
            continue
        if chat_id and st.get("chat_id") and st.get("chat_id") != chat_id:
            continue
        if not st.get("round_id") or not st.get("settled"):
            continue
        if st.get("result") is None:
            continue

        status = scoreboard_status_from_round(st)
        try:
            opened_sort = float(st.get("opened_at_ts") or 0)
        except Exception:
            opened_sort = 0
        rows.append({
            "sort": (opened_sort, str(normalize_base_no(st.get("base_no") or base_no))),
            "base_no": normalize_base_no(st.get("base_no") or base_no),
            "camp_name": st.get("camp_name") or "-",
            "price_text": state_public_price_text_no_start(st),
            "result_text": str(st.get("result") or "-"),
            "status_word": status.get("word"),
            "status_icons": status.get("icons"),
            "status_color": status.get("color"),
        })

    rows.sort(key=lambda x: x.get("sort") or (0, ""))
    return rows


def scoreboard_flex_for_chat(chat_id: str = None, limit: int = 25):
    """Flex สรุปสกอค่าย: ชื่อค่าย / ราคาช่าง / ผล+emoji พร้อมนับ ชนะ แพ้ จาว อัตโนมัติ"""
    rows = scoreboard_rows_for_chat(chat_id)
    if not rows:
        return None

    win_count = sum(1 for r in rows if r.get("status_word") == "ชนะ")
    lose_count = sum(1 for r in rows if r.get("status_word") == "แพ้")
    jow_count = sum(1 for r in rows if r.get("status_word") == "จาว")
    today_text = datetime.now().strftime("%d/%m/%Y")

    table_contents = [
        {
            "type": "box",
            "layout": "horizontal",
            "backgroundColor": "#F3F4F6",
            "paddingAll": "6px",
            "contents": [
                {"type": "text", "text": "#", "size": "xs", "weight": "bold", "color": "#475569", "flex": 1},
                {"type": "text", "text": "ชื่อค่าย", "size": "xs", "weight": "bold", "color": "#475569", "flex": 5},
                {"type": "text", "text": "ราคาช่าง", "size": "xs", "weight": "bold", "align": "end", "color": "#475569", "flex": 3},
                {"type": "text", "text": "ผล", "size": "xs", "weight": "bold", "align": "end", "color": "#475569", "flex": 3},
            ],
        }
    ]

    for idx, row in enumerate(rows[:limit], start=1):
        table_contents.extend([
            {
                "type": "box",
                "layout": "horizontal",
                "paddingTop": "7px",
                "paddingBottom": "7px",
                "contents": [
                    {"type": "text", "text": f"{idx}.", "size": "xs", "weight": "bold", "color": "#334155", "flex": 1},
                    {"type": "text", "text": row.get("camp_name") or "-", "size": "xs", "weight": "bold", "wrap": True, "color": "#0F172A", "flex": 5},
                    {"type": "text", "text": row.get("price_text") or "-", "size": "xs", "weight": "bold", "align": "end", "color": "#0F172A", "flex": 3, "adjustMode": "shrink-to-fit", "maxLines": 1},
                    {"type": "text", "text": f"{row.get('result_text')} {row.get('status_icons')}", "size": "xs", "weight": "bold", "align": "end", "color": row.get("status_color") or "#111827", "flex": 3, "adjustMode": "shrink-to-fit", "maxLines": 1},
                ],
            },
            {"type": "separator", "color": "#E5E7EB"},
        ])

    if len(rows) > limit:
        table_contents.append({
            "type": "text",
            "text": f"มีรายการเพิ่มเติมอีก {len(rows) - limit:,} ค่าย",
            "size": "xs",
            "color": "#64748B",
            "wrap": True,
            "margin": "md",
        })

    return {
        "type": "bubble",
        "size": "giga",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "10px",
            "backgroundColor": "#FFFFFF",
            "contents": [
                {
                    "type": "text",
                    "text": "📋 ผลบั้งไฟ 📋",
                    "size": "lg",
                    "weight": "bold",
                    "align": "center",
                    "color": "#0F172A",
                },
                {
                    "type": "text",
                    "text": f"🗓️ วันที่ {today_text}",
                    "size": "xs",
                    "align": "center",
                    "color": "#64748B",
                    "margin": "xs",
                },
                {
                    "type": "text",
                    "text": f"✅ ชนะ {win_count}   ❌ แพ้ {lose_count}   ⛔ จาว {jow_count}",
                    "size": "sm",
                    "weight": "bold",
                    "align": "center",
                    "color": "#111827",
                    "margin": "md",
                    "wrap": True,
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "md",
                    "contents": table_contents,
                },
            ],
        },
    }


def scoreboard_empty_text(chat_id: str = None) -> str:
    return "ยังไม่มีรายการที่เปิดวันนี้"


def has_price_setting():
    if STATE.get("price_mode") == "no_price":
        return True
    return STATE.get("base_min") is not None and STATE.get("base_max") is not None


def format_match_play_text(match):
    return format_offer_play_text(match)


def format_post_play_text(post):
    return format_offer_play_text(post)

def format_match_price_text(match):
    price_min, price_max = get_match_price_range(match)
    if price_min is None or price_max is None:
        if match.get("is_two_digit_price"):
            return "รอเริ่มต้น1/2/3"
        if match.get("is_custom_price"):
            return "-"
        return state_price_text(get_state_by_round_id(match.get("round_id")) or STATE)
    if price_min > price_max:
        return f"{price_min}-{price_max} (จาว)"
    return format_price_range_text(price_min, price_max)


def is_waiting_two_digit_start_price_text(value) -> bool:
    """ใช้กับ Flex: ถ้ายังรอคำสั่ง เริ่มต้น1/2/3 ให้ซ่อนข้อความราคาไว้ก่อน"""
    clean = re.sub(r"\s+", "", str(value or "").strip())
    return clean in {"รอเริ่มต้น1/2/3", "รอเริ่มต้น๑/๒/๓"}


def format_match_price_text_for_flex(match) -> str:
    """ราคาแผลสำหรับ Flex เท่านั้น: ไม่แสดงคำว่า รอเริ่มต้น1/2/3"""
    price_text = format_match_price_text(match)
    if is_waiting_two_digit_start_price_text(price_text):
        return ""
    return price_text

def format_two_digit_price_token_range(match: dict) -> str:
    """คืนราคาเล่นเลข 2 ตัวตามที่ลูกค้าพิมพ์จริง เช่น 30-60"""
    match = match or {}
    if not match.get("is_two_digit_price"):
        return ""
    min_token = str(match.get("two_digit_min_token") or "").strip()
    max_token = str(match.get("two_digit_max_token") or "").strip()
    if not min_token or not max_token:
        return ""
    return f"{min_token}-{max_token}"


def format_match_price_text_for_active_list(match: dict) -> str:
    """
    ราคาแสดงในคำสั่ง "รายการ" ของลูกค้า
    - ถ้าเป็นราคาเล่นเลข 2 ตัว/เลขย่อ เช่น 30-60 หรือ 3-6
      และแอดมินแจ้ง เริ่มต้น1/2/3 แล้ว ให้โชว์ราคาเต็ม เช่น 330-360
    - ถ้ายังไม่ได้แจ้งเริ่มต้น1/2/3 ให้โชว์เลขที่ลูกค้าพิมพ์จริงไปก่อน เช่น 30-60
    - แบบอื่นใช้ราคาที่ระบบคำนวณตามเดิม
    """
    match = match or {}

    if match.get("is_two_digit_price"):
        price_min, price_max = get_match_price_range(match)
        if price_min is not None and price_max is not None:
            return format_price_range_text(price_min, price_max)

        token_price = format_two_digit_price_token_range(match)
        if token_price:
            return token_price

    return format_match_price_text(match)


def format_user_play_text_for_match(match: dict, user_id: str) -> str:
    """แสดงแผลในมุมของผู้ใช้คนนั้น สำหรับหน้า "รายการ"""
    match = match or {}
    user_side = get_user_side(match, user_id)

    # ถ้าเป็นราคาเล่นเฉพาะ/เลข 2 ตัว ให้คงราคาเล่นไว้ในแผลด้วย
    # เช่น คนโพสต์เห็น 30-60ล, คนติดเห็น 30-60ถ
    if match.get("is_custom_price") or match.get("is_two_digit_price"):
        data = dict(match)
        data["maker_side"] = user_side or match.get("maker_side")
        if user_side == "ชนะ":
            data["raw_alias"] = "ล"
        elif user_side == "แพ้":
            data["raw_alias"] = "ถ"
        return format_offer_play_text(data)

    play_text = format_play_text(
        user_side,
        match.get("plus", 0),
        match.get("price_adjust_target"),
        match.get("price_adjust_min"),
        match.get("price_adjust_max"),
    )
    if match.get("only_when_no_price"):
        play_text += " ชตย"
    return play_text


def match_price_label(match: dict) -> str:
    """ชื่อป้ายราคา: ราคาเล่น สำหรับราคาเฉพาะ/เลข 2 ตัว, ราคาช่าง สำหรับราคาอิงรอบ"""
    data = match or {}
    return "ราคาเล่น" if (data.get("is_custom_price") or data.get("is_two_digit_price")) else "ราคาช่าง"



def flex_match_detail_inline(play_text: str, price_text: str = "", *, price_label: str = "ราคา", amount=None, side_text: str = None) -> str:
    """ประกอบบรรทัด Flex ให้ซ่อนส่วนราคาถ้ายังรอเริ่มต้น1/2/3 แต่ส่วนอื่นยังขึ้นปกติ"""
    parts = [f"แผล: {play_text or '-'}"]
    if price_text and not is_waiting_two_digit_start_price_text(price_text):
        parts.append(f"{price_label}: {price_text}")
    if side_text:
        parts.append(f"คุณทาย{side_text}")
    if amount is not None:
        try:
            amount_text = f"{int(amount):,}"
        except Exception:
            amount_text = str(amount)
        parts.append(f"เล่น {amount_text}")
    return " | ".join(parts)


def flex_match_detail_multiline(play_text: str, price_text: str = "", *, price_label: str = "ราคา", amount=None, amount_label: str = "ราคาที่ติดกัน", extra_lines=None) -> str:
    """ประกอบข้อความหลายบรรทัดใน Flex โดยไม่โชว์ รอเริ่มต้น1/2/3"""
    lines = [f"แผล: {play_text or '-'}"]
    if price_text and not is_waiting_two_digit_start_price_text(price_text):
        lines.append(f"{price_label}: {price_text}")
    if amount is not None:
        try:
            amount_text = f"{int(amount):,}"
        except Exception:
            amount_text = str(amount)
        lines.append(f"{amount_label}: {amount_text}")
    for line in (extra_lines or []):
        if line:
            lines.append(str(line))
    return "\n".join(lines)


def get_match_price_range(match):
    """
    ถ้าแผลมีราคาเล่นเฉพาะ เช่น 330-360ล500 ให้ใช้ราคานั้น
    ถ้าไม่มี ให้ใช้ราคาช่างของฐาน/รอบที่ match นั้นผูกอยู่ ไม่ใช้ STATE ฐานอื่น
    """
    custom_min = match.get("custom_price_min")
    custom_max = match.get("custom_price_max")

    if custom_min is not None and custom_max is not None:
        return custom_min, custom_max

    st = get_state_by_round_id(match.get("round_id")) or STATE

    if match.get("is_two_digit_price"):
        return two_digit_tokens_to_price_range(
            st.get("two_digit_start"),
            match.get("two_digit_min_token"),
            match.get("two_digit_max_token"),
            st.get("base_min"),
            st.get("base_max"),
        )

    if st.get("base_min") is None or st.get("base_max") is None:
        return None, None

    try:
        plus = int(match.get("plus", 0) or 0)
    except Exception:
        plus = 0

    price_min = int(st["base_min"])
    price_max = int(st["base_max"])
    price_adjust_target = match.get("price_adjust_target")

    if price_adjust_target == "bounds":
        try:
            price_min += int(match.get("price_adjust_min", 0) or 0)
        except Exception:
            pass
        try:
            price_max += int(match.get("price_adjust_max", 0) or 0)
        except Exception:
            pass
    elif price_adjust_target == "min":
        price_min += plus
    elif price_adjust_target == "max":
        price_max += plus
    else:
        price_min += plus
        price_max += plus

    return price_min, price_max

def winning_side_for_result(result_value: int, price_min: int, price_max: int) -> str:
    """
    กติกาปกติ:
    - ผลอยู่ในช่วงราคาช่าง = จาว / คืนเครดิต
    - ผลมากกว่าราคาบน = ฝั่งชนะ / ช่างไล่ ได้
    - ผลต่ำกว่าราคาล่าง = ฝั่งแพ้ / ช่างถอย ได้
    """
    if price_min <= result_value <= price_max:
        return "จาว"
    if result_value > price_max:
        return "ชนะ"
    return "แพ้"


def winning_side_for_match_result(match: dict, result_value: int, price_min: int, price_max: int) -> str:
    """
    คิดฝั่งชนะต่อบิล
    - บิลปกติยังใช้กติกาเดิมและมีจาวในช่วงราคา
    - ช่างไม่ชนะ: ผู้เล่นฝั่งนี้ชนะเมื่อผลไม่เกินเลขหลังของราคาเล่น
      เช่น 330-360 ผล 330-360 หรือผลต่ำกว่า 330 = ช่างไม่ชนะชนะ, ผล 361+ = ช่างชนะชนะ
    - ช่างแพ้: ผู้เล่นฝั่งนี้ชนะเฉพาะเมื่อผลต่ำกว่าเลขหน้าของราคาเล่น
      เช่น 330-360 ผล 329 ลงไป = ช่างแพ้ชนะ, ผล 330 ขึ้นไป = ช่างไม่แพ้ชนะ
    """
    if price_min > price_max:
        return "จาว"

    maker_side = (match or {}).get("maker_side")

    if maker_side in {"ช่างไม่ชนะ", "ช่างชนะ"}:
        return "ช่างชนะ" if result_value > price_max else "ช่างไม่ชนะ"

    if maker_side in {"ช่างแพ้", "ช่างไม่แพ้"}:
        return "ช่างแพ้" if result_value < price_min else "ช่างไม่แพ้"

    return winning_side_for_result(result_value, price_min, price_max)


def get_user_side(match, user_id: str) -> str:
    maker_side = match.get("maker_side")
    if user_id == match.get("maker_id"):
        return maker_side
    if user_id == match.get("taker_id"):
        return opposite_side(maker_side)
    return ""


def get_other_user_id(match, user_id: str) -> str:
    if user_id == match.get("maker_id"):
        return match.get("taker_id")
    if user_id == match.get("taker_id"):
        return match.get("maker_id")
    return ""


def user_display_name(user_id: str):
    user = USERS.get(user_id, {})
    return user.get("line_name") or user.get("name") or fallback_name(user_id)


def match_cancel_detail_text(match):
    maker_side = match.get("maker_side", "")
    play_text = format_match_play_text(match)
    amount = match.get("amount", 0)
    maker_name = user_display_name(match.get("maker_id"))
    taker_name = user_display_name(match.get("taker_id"))
    maker_side_text = maker_side or "-"
    taker_side_text = opposite_side(maker_side) or "-"
    price_min, price_max = get_match_price_range(match)
    price_text = format_match_price_text(match)

    lines = [
        f"Order #{match.get('order_no')}",
        f"แผล: {play_text}",
    ]
    if not is_waiting_two_digit_start_price_text(price_text):
        lines.append(f"ราคาเล่น: {price_text}")
    lines.extend([
        f"ราคาที่ติดกัน: {amount:,}",
        f"{maker_name} ทาย{maker_side_text}",
        f"{taker_name} ทาย{taker_side_text}",
    ])
    return "\n".join(lines)


def has_unsettled_round():
    """
    ใช้กันเปิดรอบซ้ำ:
    ถ้ามี round_id แล้ว settled ยัง False = ยังมีรอบค้างอยู่
    """
    return bool(STATE.get("round_id")) and not STATE.get("settled")



# ======================================================
# EasySlip auto top-up
# ======================================================

def get_line_image_bytes(message_id: str):
    """ดึงไฟล์รูปภาพสลิปจาก LINE ด้วย message id"""
    if not message_id:
        return None

    with ApiClient(configuration) as api_client:
        blob_api = MessagingApiBlob(api_client)
        content = blob_api.get_message_content(message_id)

    if isinstance(content, (bytes, bytearray)):
        return bytes(content)

    # เผื่อ SDK บางเวอร์ชันคืน object ที่มี .data หรือ file-like object
    data = getattr(content, "data", None)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)

    if hasattr(content, "read"):
        return content.read()

    return None


def image_has_qr_code(image_bytes: bytes):
    """
    ตรวจว่าในรูปมี QR code หรือไม่ เพื่อคัดเฉพาะรูปที่น่าจะเป็นสลิปก่อนส่งเข้า Slip2Go
    คืนค่า:
    - True = พบ QR code
    - False = ไม่พบ QR code
    - None = ตรวจไม่ได้ เช่น ยังไม่ได้ติดตั้ง opencv-python/numpy หรือไฟล์อ่านไม่ได้
    """
    if not image_bytes:
        return False

    try:
        import cv2
        import numpy as np
    except Exception as e:
        print(f"SLIP QR GATE DISABLED: install opencv-python numpy to enable QR pre-check ({e})")
        return None

    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None

        detector = cv2.QRCodeDetector()

        def detect(candidate):
            try:
                _data, points, _straight = detector.detectAndDecode(candidate)
                return points is not None
            except Exception:
                return False

        # ตรวจภาพต้นฉบับก่อน
        if detect(img):
            return True

        # ลองขยาย/ย่อเล็กน้อย เพราะรูปจาก LINE บางครั้งถูกบีบอัดจน QR เล็ก
        h, w = img.shape[:2]
        for scale in (1.5, 2.0, 0.75):
            try:
                resized = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
                if detect(resized):
                    return True
            except Exception:
                pass

        # ลอง grayscale เพิ่มอีกชั้น
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            if detect(gray):
                return True
        except Exception:
            pass

        return False

    except Exception as e:
        print(f"SLIP QR GATE ERROR: {e}")
        return None


def is_likely_slip_image(image_bytes: bytes) -> bool:
    """
    รับเฉพาะรูปที่น่าจะเป็นสลิปก่อนตรวจ Slip2Go
    เหตุผล: รูปทั่วไป/รูป Flex เมื่อส่งเข้า Slip2Go อาจถูกตอบเป็น 200500 แล้วทำให้ลูกค้าเห็น FLEX ผิดบริบท
    """
    if not SLIP_IMAGE_QR_GATE_ENABLED:
        return True

    qr_result = image_has_qr_code(image_bytes)

    if qr_result is True:
        return True

    if qr_result is False:
        # ไม่ใช่สลิปตามเกณฑ์ QR ให้เงียบ ไม่ส่งเข้า Slip2Go และไม่ขึ้น FLEX
        return False

    # ถ้าตรวจไม่ได้ เช่น เครื่องไม่มี opencv ให้ปล่อยผ่านไว้ก่อน เพื่อไม่ให้สลิปจริงถูกบล็อก
    return True


def safe_header_preview(value: str, keep: int = 6) -> str:
    """แสดงค่า header แบบย่อเพื่อ debug โดยไม่โชว์ secret ทั้งหมด"""
    value = str(value or "")
    if len(value) <= keep * 2:
        return "***" if value else ""
    return f"{value[:keep]}...{value[-keep:]}"


def validate_http_header_latin1(name: str, value: str):
    """
    requests/urllib3 ต้อง encode header value เป็น latin-1
    ถ้า .env มีภาษาไทย เช่น ใส่_SECRET_KEY_ใหม่ของพี่ จะทำให้ UnicodeEncodeError และ webhook 500
    """
    try:
        str(name).encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(
            "ค่า SLIP2GO_AUTH_HEADER_NAME ต้องเป็นภาษาอังกฤษเท่านั้น เช่น Authorization"
        )

    try:
        str(value).encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError(
            "ค่า Authorization ของ Slip2Go มีภาษาไทยหรืออักขระที่ส่งเป็น HTTP header ไม่ได้\n"
            "ให้แก้ .env โดยใส่ Secret Key จริงล้วน ๆ เท่านั้น ห้ามใส่คำอธิบายภาษาไทย และไม่ต้องใส่คำว่า Bearer ใน token\n"
            "ตัวอย่างที่ถูกต้อง:\n"
            "SLIP2GO_API_TOKEN=45Zogv...TWnuU=\n"
            "SLIP2GO_AUTH_PREFIX=Bearer\n"
            f"ค่าที่อ่านได้ตอนนี้แบบย่อ: {safe_header_preview(value)}"
        )

    if "\r" in str(value) or "\n" in str(value):
        raise ValueError(
            "ค่า SLIP2GO_API_TOKEN / Authorization มีการขึ้นบรรทัดใหม่ ให้ใส่ token เป็นบรรทัดเดียวใน .env"
        )


def build_slip2go_headers():
    headers = {}

    if SLIP2GO_API_TOKEN and SLIP2GO_AUTH_HEADER_NAME:
        if SLIP2GO_AUTH_PREFIX:
            auth_value = f"{SLIP2GO_AUTH_PREFIX} {SLIP2GO_API_TOKEN}"
        else:
            auth_value = SLIP2GO_API_TOKEN

        validate_http_header_latin1(SLIP2GO_AUTH_HEADER_NAME, auth_value)
        headers[SLIP2GO_AUTH_HEADER_NAME] = auth_value

    return headers



def _coalesce_receiver_value(item: dict, *keys):
    """อ่านค่า receiver จาก key หลายรูปแบบ เพื่อให้รองรับทั้ง JSON และชื่อ field แบบเดิม"""
    if not isinstance(item, dict):
        return ""
    for key in keys:
        if key in item and item.get(key) not in [None, ""]:
            return str(item.get(key)).strip()
    return ""


def _split_receiver_aliases(value):
    """รองรับ alias ชื่ออังกฤษหลายแบบ เผื่อชื่อบัญชีธนาคารสะกดไม่ตรงกับ transliteration ที่ใช้ทั่วไป"""
    if value in [None, ""]:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v or "").strip()]
    return [x.strip() for x in re.split(r"[,/]", str(value)) if x.strip()]


def _normalise_receiver_config(item: dict):
    """แปลง receiver config ให้เป็นรูปแบบกลางของบอท"""
    if not isinstance(item, dict):
        return None

    receiver = {
        "accountNumber": _coalesce_receiver_value(item, "accountNumber", "account_number", "number", "account", "เลขบัญชี"),
        "accountType": _coalesce_receiver_value(item, "accountType", "account_type", "type", "ประเภทบัญชี"),
        "accountNameTH": _coalesce_receiver_value(item, "accountNameTH", "account_name_th", "nameTH", "name_th", "ชื่อไทย", "ชื่อบัญชีไทย"),
        "accountNameEN": _coalesce_receiver_value(item, "accountNameEN", "account_name_en", "nameEN", "name_en", "ชื่ออังกฤษ", "ชื่อบัญชีอังกฤษ"),
        "bankName": _coalesce_receiver_value(item, "bankName", "bank_name", "bank", "ธนาคาร"),
        "accountNameENAliases": _split_receiver_aliases(
            item.get("accountNameENAliases")
            or item.get("account_name_en_aliases")
            or item.get("nameENAliases")
            or item.get("englishAliases")
            or item.get("aliases")
        ),
    }

    if not any(receiver.get(k) for k in ["accountNumber", "accountNameTH", "accountNameEN"]):
        return None
    return receiver


def _parse_receiver_accounts_from_json(raw: str):
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"PARSE SLIP2GO_RECEIVER_ACCOUNTS_JSON ERROR: {e}")
        return []

    if isinstance(data, dict):
        data = data.get("receivers") or data.get("accounts") or [data]
    if not isinstance(data, list):
        return []

    receivers = []
    for item in data:
        receiver = _normalise_receiver_config(item)
        if receiver:
            receivers.append(receiver)
    return receivers


def _parse_receiver_accounts_from_text(raw: str):
    """
    อ่านหลายบัญชีจาก .env รูปแบบง่าย:
    SLIP2GO_RECEIVER_ACCOUNTS=account|name_th|name_en|bank|type;account|name_th|name_en|bank|type
    - bank/type ไม่บังคับ
    - ใช้ ; หรือขึ้นบรรทัดใหม่คั่นแต่ละบัญชี
    """
    if not raw:
        return []

    receivers = []
    for row in re.split(r"[;\n]+", raw):
        row = row.strip()
        if not row:
            continue
        parts = [x.strip() for x in row.split("|")]
        item = {
            "accountNumber": parts[0] if len(parts) > 0 else "",
            "accountNameTH": parts[1] if len(parts) > 1 else "",
            "accountNameEN": parts[2] if len(parts) > 2 else "",
            "bankName": parts[3] if len(parts) > 3 else "",
            "accountType": parts[4] if len(parts) > 4 else "",
        }
        receiver = _normalise_receiver_config(item)
        if receiver:
            receivers.append(receiver)
    return receivers


def _legacy_receiver_account_config():
    item = {
        "accountNumber": (SLIP2GO_RECEIVER_ACCOUNT_NUMBER or "").strip(),
        "accountType": (SLIP2GO_RECEIVER_ACCOUNT_TYPE or "").strip(),
        "accountNameTH": (SLIP2GO_RECEIVER_ACCOUNT_NAME_TH or "").strip(),
        "accountNameEN": (SLIP2GO_RECEIVER_ACCOUNT_NAME_EN or "").strip(),
    }
    receiver = _normalise_receiver_config(item)
    return [receiver] if receiver else []


def get_slip2go_receiver_configs():
    """
    คืนบัญชีผู้รับที่อนุญาตให้เติมเครดิตอัตโนมัติแบบบัญชีเดียวเท่านั้น

    หมายเหตุ:
    - เวอร์ชันนี้ตั้งใจไม่อ่าน SLIP2GO_RECEIVER_ACCOUNTS_JSON / SLIP2GO_RECEIVER_ACCOUNTS จาก .env
      เพื่อกันบัญชีเก่าหรือบัญชีที่ 2 หลุดเข้ามาเติมเครดิตได้
    - บัญชีที่รับเติมอัตโนมัติคือ 938-2633-298 ไทยพาณิชย์ ภานุพงษ์ เอี่ยมท่า เท่านั้น
    """
    receiver = _normalise_receiver_config(SINGLE_AUTO_TOPUP_RECEIVER)
    return [receiver] if receiver else []

def slip2go_receiver_payload_list():
    """
    ตัด field ภายในออก เหลือเฉพาะ field ที่ส่งให้ Slip2Go ตรวจ checkReceiver
    ถ้ามี accountNameENAliases จะขยายเป็น receiver หลายรายการของบัญชีเดียวกัน
    เพื่อให้ตรวจชื่อภาษาอังกฤษได้หลาย spelling โดยยังเป็นเลขบัญชีเดียวกัน
    """
    payload_receivers = []
    seen = set()
    for receiver in get_slip2go_receiver_configs():
        base_item = {}
        for key in ["accountNumber", "accountType", "accountNameTH"]:
            value = (receiver.get(key) or "").strip()
            if value:
                base_item[key] = value

        en_names = []
        primary_en = (receiver.get("accountNameEN") or "").strip()
        if primary_en:
            en_names.append(primary_en)
        for alias in receiver.get("accountNameENAliases") or []:
            alias = str(alias or "").strip()
            if alias and alias not in en_names:
                en_names.append(alias)

        if en_names:
            for en_name in en_names:
                item = dict(base_item)
                item["accountNameEN"] = en_name
                key = tuple(sorted(item.items()))
                if item and key not in seen:
                    payload_receivers.append(item)
                    seen.add(key)
        else:
            item = dict(base_item)
            key = tuple(sorted(item.items()))
            if item and key not in seen:
                payload_receivers.append(item)
                seen.add(key)

    return payload_receivers

def receiver_expected_values():
    """ค่าที่ใช้ fallback ตรวจชื่อ/เลขบัญชีจาก response จริงของ Slip2Go เมื่อ response ไม่มี code ชัดเจน"""
    values = []
    for receiver in get_slip2go_receiver_configs():
        values.extend([
            receiver.get("accountNumber"),
            receiver.get("accountNameTH"),
            receiver.get("accountNameEN"),
        ])
        values.extend(receiver.get("accountNameENAliases") or [])

    # ไม่ใช้ SLIP2GO_REQUIRE_RECEIVER_TEXT จาก .env แล้ว
    # เพราะต้องล็อกให้เติมอัตโนมัติได้เฉพาะบัญชีเดียวใน SINGLE_AUTO_TOPUP_RECEIVER เท่านั้น
    return [v for v in values if normalize_compare_text(v)]

def build_slip2go_payload(check_duplicate=None):
    """
    สร้าง payload ตามหน้า Slip2Go API Connect สำหรับ endpoint qr-image/info
    - checkDuplicate: ให้ Slip2Go ช่วยเช็คสลิปซ้ำ
    - checkReceiver: ส่งข้อมูลบัญชีร้านให้ Slip2Go ตรวจบัญชีผู้รับก่อนเติมเครดิต
    - ล็อกบัญชีผู้รับบัญชีเดียวผ่าน SINGLE_AUTO_TOPUP_RECEIVER

    check_duplicate:
    - None = ใช้ค่าจาก .env ตามเดิม
    - True / False = บังคับส่ง checkDuplicate ตามค่าที่กำหนด
      ใช้แก้เคส ธ.กรุงเทพ: รอบแรกได้ 200404 แล้วรอบถัดมา Slip2Go ตอบ duplicate + data:null
      ให้ยิงซ้ำโดยส่ง checkDuplicate=False แต่ยังคงตรวจบัญชีผู้รับตามเดิม

    สำคัญ: ถ้าตั้งเลขบัญชี/ชื่อบัญชีไว้ ระบบจะไม่เติมเครดิตถ้าผู้รับในสลิปไม่ตรงกับบัญชีใดบัญชีหนึ่งที่อนุญาต
    """
    payload = {}

    if check_duplicate is None:
        if SLIP2GO_CHECK_DUPLICATE:
            payload["checkDuplicate"] = True
    else:
        payload["checkDuplicate"] = bool(check_duplicate)

    receivers = slip2go_receiver_payload_list()
    if receivers:
        payload["checkReceiver"] = receivers

    return payload

def slip2go_error_text(response, data):
    try:
        body_text = json.dumps(data, ensure_ascii=False)
    except Exception:
        body_text = str(data)

    body_text = re.sub(r"\s+", " ", body_text).strip()
    if len(body_text) > 700:
        body_text = body_text[:700] + "..."

    content_type = response.headers.get("Content-Type", "") if response is not None else ""
    return body_text, content_type


def normalize_slip2go_image_url(url: str) -> str:
    """
    ลูกค้าส่งรูปสลิปจาก LINE ดังนั้นต้องใช้ endpoint รูปภาพของ Slip2Go
    ถ้าเผลอตั้งเป็น qr-code/info จะ auto เปลี่ยนเป็น qr-image/info เพื่อกัน HTTP 400
    """
    url = (url or "").strip()
    if "/verify-slip/qr-code/" in url:
        return url.replace("/verify-slip/qr-code/", "/verify-slip/qr-image/")
    return url


def short_response_detail(data, limit: int = 500):
    """ย่อข้อความ error จาก Slip2Go ให้เห็นสาเหตุจริง ไม่โชว์ยาวเกินไปใน LINE"""
    if data is None:
        return ""
    try:
        text = json.dumps(data, ensure_ascii=False)
    except Exception:
        text = str(data)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return text[:limit]


def post_slip2go_request(api_url: str, headers: dict, files: dict, form_data=None):
    """ยิง Slip2Go พร้อม retry เฉพาะเคส timeout/network ชั่วคราว"""
    attempts = max(1, int(SLIP2GO_API_RETRIES or 1))
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(
                api_url,
                headers=headers,
                files=files,
                data=form_data,
                timeout=(SLIP2GO_CONNECT_TIMEOUT_SECONDS, SLIP2GO_TIMEOUT_SECONDS),
            )
            return True, response, None

        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.Timeout) as e:
            last_error = e
            if attempt < attempts:
                print(f"SLIP2GO TIMEOUT attempt {attempt}/{attempts}: {e}")
                time.sleep(SLIP2GO_API_RETRY_DELAY_SECONDS * attempt)
                continue
            return False, None, {
                "type": "timeout",
                "message": str(e),
                "attempts": attempts,
            }

        except requests.RequestException as e:
            return False, None, {
                "type": "connection",
                "message": str(e),
                "attempts": attempt,
            }

    return False, None, {
        "type": "timeout",
        "message": str(last_error or "unknown timeout"),
        "attempts": attempts,
    }


def slip2go_network_error_payload(error_type: str, error_message: str, api_url: str, attempts: int):
    return {
        "_error_type": error_type,
        "_slip2go_debug": {
            "endpoint_used": api_url,
            "attempts": attempts,
            "error_message": error_message,
        },
    }


def is_slip2go_network_issue(message: str = "", data=None) -> bool:
    if isinstance(data, dict) and data.get("_error_type") in {"slip2go_timeout", "slip2go_connection_error"}:
        return True

    text = str(message or "").lower()
    network_keywords = [
        "read timed out",
        "connecttimeout",
        "readtimeout",
        "timeout",
        "httpsconnectionpool",
        "connection aborted",
        "temporarily unavailable",
    ]
    return any(k in text for k in network_keywords)


def is_slip2go_duplicate_with_null_data_response(data):
    """
    ตรวจเคสเฉพาะที่ Slip2Go แนะนำ:
    - response เป็นสลิปซ้ำ / code 200501
    - แต่ field data เป็น null

    เคสนี้มักเกิดกับสลิป ธ.กรุงเทพที่ส่งเร็วเกินไป:
    รอบแรกอาจได้ 200404, พอส่งใหม่ระบบ Slip2Go มองว่า duplicate แต่ยังไม่มี data ให้บอทอ่านยอดเงิน
    วิธีแก้คือยิง API ซ้ำอีกครั้ง โดยส่ง checkDuplicate=False ชั่วคราว
    """
    if not isinstance(data, dict):
        return False

    code, _ = get_slip2go_response_code(data)
    duplicate_like = code == "200501" or is_slip2go_duplicate(data)
    if not duplicate_like:
        return False

    clean_data = remove_internal_slip2go_debug(data)

    # รูปแบบที่พบบ่อย: {"response": "200501", "data": null, ...}
    if isinstance(clean_data, dict) and "data" in clean_data and clean_data.get("data") is None:
        return True

    # เผื่อ API ซ้อน data ไว้ลึกกว่าชั้นแรก
    for path, value in walk_json_values(clean_data):
        key = re.sub(r"[^a-z0-9]", "", str(path).split(".")[-1].lower())
        if key == "data" and value is None:
            return True

    return False


def call_easyslip_api(image_bytes: bytes):
    """
    ยิง EasySlip API v2 ตรวจสลิปจากรูปภาพที่ลูกค้าส่งเข้า LINE OA

    EasySlip v2 Endpoint: POST https://api.easyslip.com/v2/verify/bank
    Auth: Authorization: Bearer <key>
    Body: multipart/form-data  field=image
    Response: { success: true/false, data: { isDuplicate, rawSlip: {...} } }
    """
    if not EASYSLIP_ENABLED:
        return False, "ระบบตรวจสลิปอัตโนมัติถูกปิดอยู่", None

    if not EASYSLIP_API_KEY:
        return False, "ยังไม่ได้ตั้งค่า EASYSLIP_API_KEY ในไฟล์ .env", None

    if not image_bytes:
        return False, "ไม่พบไฟล์รูปสลิปจาก LINE", None

    # V2 endpoint และ header ใหม่
    api_url = "https://api.easyslip.com/v2/verify/bank"
    headers = {"Authorization": f"Bearer {EASYSLIP_API_KEY}"}

    attempts = max(1, int(EASYSLIP_API_RETRIES or 1))
    last_status = None
    last_data = None

    for attempt in range(1, attempts + 1):
        try:
            # V2 ใช้ field ชื่อ "image" (ไม่ใช่ "file" แบบ V1)
            files = {"image": ("slip.jpg", image_bytes, "image/jpeg")}
            form_data = {"checkDuplicate": "true"}
            response = requests.post(
                api_url,
                headers=headers,
                files=files,
                data=form_data,
                timeout=(EASYSLIP_CONNECT_TIMEOUT_SECONDS, EASYSLIP_TIMEOUT_SECONDS),
            )
            last_status = response.status_code

            try:
                last_data = response.json()
            except Exception:
                last_data = {"raw_text": response.text[:1000]}

            # V2: HTTP 200 + success=true = ผ่าน
            if response.status_code == 200 and isinstance(last_data, dict) and last_data.get("success") is True:
                return True, "ok", last_data

            # V2: HTTP 4xx + success=false = error มี error.code บอกสาเหตุ
            # 401/403 = token/IP ผิด ไม่ต้อง retry
            if response.status_code in (401, 403):
                error_code = ""
                if isinstance(last_data, dict):
                    error_code = str((last_data.get("error") or {}).get("code") or "")
                hint = "ให้ตรวจ EASYSLIP_API_KEY ใน .env และเพิ่ม IP Server ใน EasySlip"
                return False, f"EasySlip ตอบ HTTP {response.status_code} ({error_code})\n{hint}", last_data

            # 404 = สลิปไม่เจอ / BBL pending — คืนค่ากลับไปให้ caller จัดการ
            if response.status_code == 404:
                return False, "slip_not_found", last_data

            # 429/5xx retry ได้
            if response.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
                print(f"EASYSLIP RETRY HTTP {response.status_code} attempt {attempt}/{attempts}")
                time.sleep(EASYSLIP_API_RETRY_DELAY_SECONDS * attempt)
                continue

            # error อื่น ๆ
            error_msg = ""
            if isinstance(last_data, dict):
                error_msg = str((last_data.get("error") or {}).get("message") or "")
            return False, f"EasySlip ตอบกลับ HTTP {last_status}" + (f": {error_msg}" if error_msg else ""), last_data

        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.Timeout) as e:
            if attempt < attempts:
                print(f"EASYSLIP TIMEOUT attempt {attempt}/{attempts}: {e}")
                time.sleep(EASYSLIP_API_RETRY_DELAY_SECONDS * attempt)
                continue
            return (
                False,
                f"EasySlip ตอบช้า/หมดเวลา หลังลอง {attempts} ครั้ง: {e}",
                {"_easyslip_debug": {"error_type": "timeout", "message": str(e), "attempts": attempts}},
            )

        except requests.exceptions.RequestException as e:
            if attempt < attempts:
                print(f"EASYSLIP REQUEST ERROR attempt {attempt}/{attempts}: {e}")
                time.sleep(EASYSLIP_API_RETRY_DELAY_SECONDS * attempt)
                continue
            return (
                False,
                f"เชื่อมต่อ EasySlip ไม่สำเร็จ: {e}",
                {"_easyslip_debug": {"error_type": "connection_error", "message": str(e), "attempts": attempts}},
            )

    return False, f"EasySlip ตอบกลับ HTTP {last_status}", last_data


# ── stub เก่าเพื่อไม่ให้ code เก่าที่ยังอ้าง call_slip2go_api crash ──────────────
def call_slip2go_api(image_bytes: bytes):
    """Stub: โค้ดนี้ถูกแทนที่ด้วย call_easyslip_api แล้ว"""
    return call_easyslip_api(image_bytes)


def easyslip_get_raw_slip(data: dict) -> dict:
    """ดึง rawSlip object จาก EasySlip V2 response"""
    try:
        return data.get("data", {}).get("rawSlip") or {}
    except Exception:
        return {}


def easyslip_extract_amount(data: dict):
    """อ่านยอดเงินจาก EasySlip V2 response: data.rawSlip.amount.amount"""
    try:
        raw = easyslip_get_raw_slip(data)
        val = raw.get("amount", {}).get("amount")
        if val is not None:
            return parse_decimal_value(val), "data.rawSlip.amount.amount"
    except Exception:
        pass
    return extract_amount_from_slip2go(data)


def easyslip_extract_reference(data: dict, image_bytes: bytes):
    """อ่านเลข transRef จาก EasySlip V2 response: data.rawSlip.transRef"""
    try:
        raw = easyslip_get_raw_slip(data)
        ref = raw.get("transRef")
        if ref:
            return str(ref).strip(), "data.rawSlip.transRef"
    except Exception:
        pass
    return extract_reference_from_slip2go(data, image_bytes)


def easyslip_receiver_check_passed(data: dict) -> bool:
    """
    ตรวจบัญชีผู้รับจาก EasySlip V2 response
    ถ้าไม่ได้ตั้งค่าทั้ง EASYSLIP_ACCOUNT_NUMBER และ EASYSLIP_ACCOUNT_NAME_TH/EN
    จะไม่ตรวจ → รับสลิปทุกบัญชี (ไม่แนะนำ)
    """
    expected_no   = EASYSLIP_ACCOUNT_NUMBER.strip()
    expected_name_th = EASYSLIP_ACCOUNT_NAME_TH.strip()
    expected_name_en = EASYSLIP_ACCOUNT_NAME_EN.strip()

    # ถ้าไม่ตั้งค่าเลยแม้แต่อย่างเดียว → ไม่ตรวจ (ผ่านทั้งหมด)
    if not expected_no and not expected_name_th and not expected_name_en:
        return True

    norm_no = lambda s: re.sub(r"[^0-9]", "", str(s or ""))
    norm_name = lambda s: re.sub(r"\s+", "", str(s or "").lower())

    expected_no_digits = norm_no(expected_no)

    try:
        raw = easyslip_get_raw_slip(data)
        receiver = raw.get("receiver", {})
        acct = receiver.get("account", {})

        # ── 1. เทียบชื่อบัญชีก่อน (แม่นยำกว่าเลขบัญชี masked) ──────────────
        if expected_name_th or expected_name_en:
            name_th = norm_name(acct.get("name", {}).get("th") or "")
            name_en = norm_name(acct.get("name", {}).get("en") or "")

            # ตัดคำนำหน้าชื่อ ออกก่อนเทียบ
            prefixes = ["นาย", "นาง", "น.ส.", "นางสาว", "mr.", "mrs.", "ms.", "miss"]
            def strip_prefix(s):
                for p in prefixes:
                    if s.startswith(norm_name(p)):
                        s = s[len(norm_name(p)):]
                return s.strip()

            name_th_clean = strip_prefix(name_th)
            name_en_clean = strip_prefix(name_en)

            if expected_name_th:
                exp_th = strip_prefix(norm_name(expected_name_th))
                # เทียบ 2 ทิศทาง: expected ใน got หรือ got ใน expected
                if exp_th and (exp_th in name_th_clean or name_th_clean in exp_th):
                    return True
                # เทียบ partial: ตัวแรกของชื่อตรงกัน (กรณี EasySlip ตัดชื่อ)
                if exp_th and name_th_clean and (
                    exp_th[:4] == name_th_clean[:4]  # 4 ตัวแรกตรงกัน
                ):
                    return True

            if expected_name_en:
                exp_en = strip_prefix(norm_name(expected_name_en))
                if exp_en and (exp_en in name_en_clean or name_en_clean in exp_en):
                    return True
                if exp_en and name_en_clean and exp_en[:4] == name_en_clean[:4]:
                    return True

        # ── 2. เทียบเลขบัญชี (bank account) ─────────────────────────────────
        if expected_no_digits:
            bank_obj = acct.get("bank") or {}
            if isinstance(bank_obj, dict):
                acct_no_digits = norm_no(bank_obj.get("account") or "")
                if acct_no_digits:
                    # exact match
                    if acct_no_digits == expected_no_digits:
                        return True
                    # suffix match (masked เช่น xxx-x-x3329-x)
                    suffix = min(len(acct_no_digits), len(expected_no_digits), 6)
                    if suffix >= 4 and acct_no_digits[-suffix:] == expected_no_digits[-suffix:]:
                        return True

            # ── 3. เทียบ PromptPay proxy (เบอร์โทร / เลขบัตร) ───────────────
            proxy_obj = acct.get("proxy") or {}
            if isinstance(proxy_obj, dict):
                proxy_digits = norm_no(proxy_obj.get("account") or "")
                if proxy_digits:
                    if proxy_digits == expected_no_digits:
                        return True
                    suffix = min(len(proxy_digits), len(expected_no_digits), 6)
                    if suffix >= 4 and proxy_digits[-suffix:] == expected_no_digits[-suffix:]:
                        return True

            # ── 4. matchedAccount จาก EasySlip ───────────────────────────────
            matched = data.get("data", {}).get("matchedAccount") or {}
            if isinstance(matched, dict):
                matched_digits = norm_no(matched.get("bankNumber") or "")
                if matched_digits and matched_digits == expected_no_digits:
                    return True

        if EASYSLIP_DEBUG_MODE:
            try:
                acct_bank = (acct.get("bank") or {})
                acct_proxy = (acct.get("proxy") or {})
                print(
                    f"EASYSLIP RECEIVER FAIL | "
                    f"expected_no={expected_no_digits!r} "
                    f"expected_name_th={expected_name_th!r} "
                    f"expected_name_en={expected_name_en!r} | "
                    f"got_bank={norm_no(acct_bank.get('account',''))!r} "
                    f"got_proxy={norm_no(acct_proxy.get('account',''))!r} "
                    f"got_name_th={norm_name(acct.get('name',{}).get('th',''))!r}"
                )
            except Exception:
                pass

    except Exception as e:
        if EASYSLIP_DEBUG_MODE:
            print(f"EASYSLIP RECEIVER CHECK EXCEPTION: {e}")
        # exception = parse error ไม่ใช่บัญชีผิด → ให้ผ่านไม่ได้
        # คืน False เพื่อความปลอดภัย
        return False

    return False


def easyslip_is_verified(data: dict) -> bool:
    """
    EasySlip V2: ผ่านเมื่อ success=true และมี rawSlip.transRef
    """
    try:
        if data.get("success") is not True:
            return False
        raw = easyslip_get_raw_slip(data)
        return bool(raw.get("transRef"))
    except Exception:
        return False


def easyslip_is_duplicate(data: dict) -> bool:
    """EasySlip V2 มี isDuplicate field ใน data"""
    try:
        return data.get("data", {}).get("isDuplicate") is True
    except Exception:
        return False


def easyslip_is_bbl_pending(data: dict) -> bool:
    """EasySlip V2 ตอบ error.code = SLIP_PENDING สำหรับสลิป ธ.กรุงเทพที่ยังไม่ sync"""
    try:
        error_code = str((data.get("error") or {}).get("code") or "")
        return error_code == "SLIP_PENDING"
    except Exception:
        return False


def easyslip_get_error_code(data: dict) -> str:
    """อ่าน error.code จาก EasySlip V2 error response"""
    try:
        return str((data.get("error") or {}).get("code") or "")
    except Exception:
        return ""


def walk_json_values(obj, path=""):
    """คืนคู่ (path, value) จาก JSON ทุกชั้น ใช้สำหรับอ่าน response ที่แต่ละแพ็กเกจอาจตั้งชื่อ field ไม่เหมือนกัน"""
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            next_path = f"{path}.{k}" if path else str(k)
            items.extend(walk_json_values(v, next_path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            next_path = f"{path}[{i}]"
            items.extend(walk_json_values(v, next_path))
    else:
        items.append((path, obj))
    return items


def parse_decimal_value(value):
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    text = str(value).strip()
    text = text.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None

    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return None


def extract_amount_from_slip2go(data):
    """พยายามอ่านยอดเงินจาก response ของ Slip2Go โดยรองรับหลายชื่อ field"""
    amount_key_order = [
        "amount",
        "transamount",
        "transferamount",
        "transactionamount",
        "totalamount",
        "paidamount",
        "payamount",
    ]

    candidates = []
    for path, value in walk_json_values(data):
        key = re.sub(r"[^a-z0-9]", "", path.split(".")[-1].lower())
        if key in amount_key_order:
            amount = parse_decimal_value(value)
            if amount is not None:
                candidates.append((amount_key_order.index(key), path, amount))

    if not candidates:
        return None, None

    candidates.sort(key=lambda x: (x[0], len(x[1])))
    _, path, amount = candidates[0]
    return amount, path


def extract_reference_from_slip2go(data, image_bytes: bytes):
    """อ่านเลขอ้างอิงสลิปเพื่อกันเติมซ้ำ ถ้าไม่มี ref จะ fallback เป็น hash ของรูป+response"""
    preferred_keys = [
        "transref",
        "transactionref",
        "transactionid",
        "reference",
        "ref",
        "slipid",
        "slipref",
        "qrid",
        "qrcode",
    ]

    candidates = []
    for path, value in walk_json_values(data):
        key = re.sub(r"[^a-z0-9]", "", path.split(".")[-1].lower())
        if key in preferred_keys and value not in [None, ""]:
            candidates.append((preferred_keys.index(key), path, str(value).strip()))

    if candidates:
        candidates.sort(key=lambda x: (x[0], len(x[1])))
        return candidates[0][2], candidates[0][1]

    digest = hashlib.sha256()
    digest.update(image_bytes or b"")
    try:
        digest.update(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    except Exception:
        pass
    return f"hash:{digest.hexdigest()}", "sha256"


def remove_internal_slip2go_debug(obj):
    """ตัดข้อมูล debug ที่บอทใส่เองออกก่อนตรวจผู้รับ กัน payload ที่ส่งไปเองทำให้เช็คผ่านหลอก ๆ"""
    if isinstance(obj, dict):
        clean = {}
        for k, v in obj.items():
            key = str(k)
            if key.startswith("_slip2go") or key.startswith("_debug"):
                continue
            clean[key] = remove_internal_slip2go_debug(v)
        return clean
    if isinstance(obj, list):
        return [remove_internal_slip2go_debug(v) for v in obj]
    return obj


def normalize_compare_text(value):
    """ทำข้อความ/เลขบัญชีให้เทียบง่ายขึ้น: lower และลบช่องว่าง/ขีด/สัญลักษณ์ส่วนใหญ่"""
    text = str(value or "").strip().lower()
    return re.sub(r"[^0-9a-zก-๙]", "", text)


def get_slip2go_response_code(data):
    """อ่าน response code ของ Slip2Go เช่น 200200, 200401, 200501 จากทุกตำแหน่งที่เป็นไปได้"""
    preferred_keys = [
        "response", "responsecode", "response_code", "code", "statuscode", "status_code",
        "resultcode", "result_code",
    ]
    candidates = []
    clean_data = remove_internal_slip2go_debug(data)
    for path, value in walk_json_values(clean_data):
        key = re.sub(r"[^a-z0-9]", "", path.split(".")[-1].lower())
        raw = str(value).strip()
        m = re.fullmatch(r"\d{6}", raw)
        if key in [re.sub(r"[^a-z0-9]", "", k.lower()) for k in preferred_keys] and m:
            # ให้ top-level/field สั้นมาก่อน
            candidates.append((len(path), path, raw))
        elif m and raw.startswith("200"):
            # fallback เผื่อ API ใช้ชื่อ field แปลก
            candidates.append((len(path) + 100, path, raw))

    if not candidates:
        return None, None

    candidates.sort(key=lambda x: x[0])
    _, path, code = candidates[0]
    return code, path


def receiver_configured():
    return bool(get_slip2go_receiver_configs() or (SLIP2GO_REQUIRE_RECEIVER_TEXT or "").strip())


def slip2go_reject_reason(data):
    """แปลง response code/ข้อความของ Slip2Go เป็นเหตุผลที่ต้องหยุดเติมเครดิต"""
    code, code_path = get_slip2go_response_code(data)
    if code == "200401":
        return "receiver", "บัญชีผู้รับไม่ตรงกับบัญชีร้านที่ตั้งไว้ใน ระบบ"
    if code == "200402":
        return "amount", "ยอดเงินโอนไม่ตรงเงื่อนไขที่ ระบบ ตรวจสอบ"
    if code == "200403":
        return "date", "วันที่โอนไม่ตรงเงื่อนไขที่ ระบบ ตรวจสอบ"
    if code == "200404":
        return "not_found", "ไม่พบข้อมูลสลิปในระบบธนาคาร"
    if code == "200500":
        return "fraud", "ระบบ แจ้งว่าสลิปเสี่ยงเป็นสลิปปลอมหรือสลิปเสีย"
    if code == "200501":
        return "duplicate", "ระบบ แจ้งว่าสลิปนี้ถูกใช้งานแล้ว"

    # ถ้ามี response code แต่ไม่ใช่ code ที่ถือว่าตรวจผ่าน ไม่ควรเติมเครดิต
    if code and code not in ["200200"]:
        return "not_valid", f"ระบบยังไม่ได้ยืนยันสลิปนี้ (code: {code})"

    clean_text = slip2go_text_blob(remove_internal_slip2go_debug(data)).lower()
    receiver_bad_words = [
        "recipient account not match", "receiver account not match", "account not match",
        "บัญชีผู้รับไม่ถูกต้อง", "บัญชีผู้รับไม่ตรง", "ผู้รับไม่ตรง",
    ]
    if any(w in clean_text for w in receiver_bad_words):
        return "receiver", "บัญชีผู้รับไม่ตรงกับบัญชีร้านที่ตั้งไว้ใน ระบบ"

    not_found_words = [
        "slip not found", "not found slip", "not found",
        "ไม่พบข้อมูลสลิป", "ไม่พบสลิป", "ไม่มีข้อมูลสลิป",
    ]
    if any(w in clean_text for w in not_found_words):
        return "not_found", "ไม่พบข้อมูลสลิปในระบบธนาคาร"

    return None, None


def slip2go_text_blob(data):
    # ห้ามรวม _slip2go_debug เพราะในนั้นมี payload/checkReceiver ที่บอทส่งไปเอง
    data = remove_internal_slip2go_debug(data)
    values = []
    for _, value in walk_json_values(data):
        if isinstance(value, (str, int, float, bool)):
            values.append(str(value))
    return " ".join(values)

def is_slip2go_duplicate(data):
    code, _ = get_slip2go_response_code(data)
    if code == "200501":
        return True

    text = slip2go_text_blob(data).lower()
    if "duplicate" in text or "already" in text or "สลิปซ้ำ" in text or "ซ้ำ" in text:
        return True

    duplicate_keys = ["duplicate", "isduplicate", "isduplicated", "used", "alreadyused"]
    clean_data = remove_internal_slip2go_debug(data)
    for path, value in walk_json_values(clean_data):
        key = re.sub(r"[^a-z0-9]", "", path.split(".")[-1].lower())
        if key in duplicate_keys and value is True:
            return True

    return False

def is_slip2go_verified(data):
    """ตัดสินสถานะผ่านแบบเข้มงวด: ถ้ามี response code ต้องเป็น Slip is Valid = 200200 เท่านั้น"""
    reject_type, reject_msg = slip2go_reject_reason(data)
    if reject_type:
        return False

    code, _ = get_slip2go_response_code(data)
    if code:
        return code == "200200"

    # fallback เฉพาะกรณี response ไม่มี code จริง ๆ
    text = slip2go_text_blob(data).lower()
    bad_words = [
        "invalid", "failed", "fail", "error", "not found", "not match", "fraud", "duplicate",
        "ปลอม", "ไม่ถูกต้อง", "ไม่สำเร็จ", "ไม่ตรง", "ซ้ำ",
    ]
    if any(w in text for w in bad_words):
        return False

    good_words = ["success", "verified", "valid", "complete", "completed", "สำเร็จ", "ถูกต้อง", "ตรวจสอบแล้ว"]
    if any(w in text for w in good_words):
        return True

    for path, value in walk_json_values(remove_internal_slip2go_debug(data)):
        key = re.sub(r"[^a-z0-9]", "", path.split(".")[-1].lower())
        if key in ["success", "valid", "verified", "isvalid"] and value is True:
            return True
        if key in ["status", "result"] and str(value).lower() in ["success", "valid", "verified", "passed", "pass", "true"]:
            return True

    # ไม่ fallback จากยอดเงินอย่างเดียวแล้ว เพราะสลิปบัญชีผิดก็อาจมียอดเงินได้
    return False


def receiver_check_passed(data):
    """
    ถ้าตั้งบัญชีผู้รับไว้ ต้องผ่านการตรวจจาก Slip2Go เท่านั้น
    บัญชีผู้รับต้องตรงกับ SINGLE_AUTO_TOPUP_RECEIVER เท่านั้น
    แก้บัคเดิม: ห้ามเอาข้อความใน _slip2go_debug/payload ที่บอทส่งเองมาเป็นหลักฐานว่าผู้รับตรง
    """
    if not receiver_configured():
        return True

    code, _ = get_slip2go_response_code(data)
    if code == "200401":
        return False

    # ถ้าใช้ checkReceiver แล้ว Slip2Go ตอบ Slip is Valid = 200200 ให้ถือว่าบัญชีผู้รับผ่านแล้ว
    # เพราะ payload ส่ง checkReceiver เป็นบัญชีเดียวที่อนุญาตไว้
    if code == "200200":
        return True

    clean_blob = normalize_compare_text(slip2go_text_blob(remove_internal_slip2go_debug(data)))
    expected_values = [normalize_compare_text(v) for v in receiver_expected_values() if normalize_compare_text(v)]

    if not expected_values:
        return True

    # fallback สำหรับ response ที่ไม่มี code เท่านั้น ต้องพบเลขบัญชี/ชื่อไทย/ชื่ออังกฤษของบัญชีที่อนุญาตในข้อมูลจริงจาก Slip2Go
    return any(v in clean_blob for v in expected_values)

def format_topup_amount(amount: Decimal):
    if amount == amount.to_integral_value():
        return f"{int(amount):,}"
    return f"{amount:,.2f}"



def flex_text(text, size="sm", color="#111111", weight=None, align=None, wrap=True, margin=None):
    item = {
        "type": "text",
        "text": str(text if text is not None else "-"),
        "size": size,
        "color": color,
        "wrap": wrap,
    }
    if weight:
        item["weight"] = weight
    if align:
        item["align"] = align
    if margin:
        item["margin"] = margin
    return item


def flex_kv(label, value, value_color="#111111"):
    return {
        "type": "box",
        "layout": "horizontal",
        "spacing": "md",
        "contents": [
            {
                "type": "text",
                "text": str(label),
                "size": "sm",
                "color": "#6B7280",
                "flex": 3,
                "wrap": True,
            },
            {
                "type": "text",
                "text": str(value if value is not None else "-"),
                "size": "sm",
                "color": value_color,
                "weight": "bold",
                "align": "end",
                "flex": 5,
                "wrap": True,
            },
        ],
    }


def slip_status_flex(title, subtitle, status_text, color="#3B82F6", emoji="🔎", details=None, footer_text=None):
    """Flex กลางสำหรับสถานะตรวจสลิป/เติมเครดิต"""
    body_contents = [
        {
            "type": "box",
            "layout": "vertical",
            "alignItems": "center",
            "spacing": "sm",
            "contents": [
                flex_text(emoji, size="xxl", align="center", wrap=False),
                flex_text(status_text, size="xl", color=color, weight="bold", align="center"),
                flex_text(subtitle, size="sm", color="#6B7280", align="center"),
            ],
        },
        {"type": "separator", "margin": "lg"},
    ]

    for item in details or []:
        if isinstance(item, dict):
            body_contents.append(item)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            body_contents.append(flex_kv(item[0], item[1], item[2] if len(item) >= 3 else "#111111"))
        else:
            body_contents.append(flex_text(item, size="sm", color="#374151", margin="sm"))

    if footer_text:
        body_contents.extend([
            {"type": "separator", "margin": "lg"},
            flex_text(footer_text, size="xs", color="#9CA3AF", align="center", margin="md"),
        ])

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": color,
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "weight": "bold",
                    "size": "lg",
                    "color": "#FFFFFF",
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "18px",
            "spacing": "md",
            "contents": body_contents,
        },
    }


def bank_logo_url(short_code: str) -> str:
    """
    คืน URL โลโก้ธนาคารจริงจาก casperstack/thai-banks-logo (GitHub raw)
    ชื่อไฟล์เป็นตัวพิมพ์ใหญ่ เช่น KBANK.png, SCB.png
    """
    # map จาก EasySlip short code → ชื่อไฟล์ใน repo
    short_to_file = {
        "KBANK":    "KBANK",
        "SCB":      "SCB",
        "BBL":      "BBL",
        "KTB":      "KTB",
        "TTB":      "TTB",
        "BAY":      "BAY",
        "GSB":      "GSB",
        "GHB":      "GHB",
        "BAAC":     "BAAC",
        "UOB":      "UOB",
        "CIMBT":    "CIMB",
        "CITI":     "CITI",
        "KKP":      "KKP",
        "ICBCT":    "ICBC",
        "LH":       "LHB",
        "TISCO":    "TISCO",
        "ISBT":     "IBANK",
        "TCD":      "TCRB",
        "TRUEMONEY":"TrueMoney",
        "TMW":      "TrueMoney",
        "PROMPTPAY":"PromptPay",
    }
    key = short_to_file.get(str(short_code or "").upper().strip())
    if not key:
        return ""
    return f"https://raw.githubusercontent.com/casperstack/thai-banks-logo/master/icons/{key}.png"


def bank_logo_component(short_code: str, size: str = "28px") -> dict:
    """สร้าง LINE Flex image component โลโก้ธนาคาร"""
    url = bank_logo_url(short_code)
    if not url:
        return None
    return {
        "type": "image",
        "url": url,
        "size": size,
        "aspectMode": "fit",
        "aspectRatio": "1:1",
        "flex": 0,
    }


def bank_row(label: str, name: str, short_code: str, name_color: str = "#111111") -> dict:
    """
    แถว ผู้โอน/ผู้รับ พร้อมโลโก้ธนาคาร
    Layout: [label]  [โลโก้] [ชื่อ · ชื่อธนาคาร]
    """
    logo = bank_logo_component(short_code)
    name_text = name or "-"
    bank_label = short_code or ""

    name_contents = []
    if logo:
        name_contents.append(logo)
    name_contents.append({
        "type": "box", "layout": "vertical", "spacing": "none", "flex": 1,
        "contents": [
            {"type": "text", "text": name_text, "size": "sm",
             "color": name_color, "weight": "bold", "wrap": True},
            {"type": "text", "text": bank_label, "size": "xxs",
             "color": "#9CA3AF", "wrap": False} if bank_label else
            {"type": "filler"},
        ],
    })

    return {
        "type": "box", "layout": "horizontal", "spacing": "sm", "margin": "sm",
        "alignItems": "center",
        "contents": [
            {"type": "text", "text": label, "size": "sm", "color": "#6B7280",
             "flex": 3, "wrap": True},
            {
                "type": "box", "layout": "horizontal", "flex": 6,
                "spacing": "sm", "alignItems": "center",
                "contents": name_contents,
            },
        ],
    }


def slip_success_flex(target, amount, credit_to_add, old_credit, slip_ref, slip_data=None):
    member_name = target.get("line_name") or target.get("name") or "User"
    member_no   = str(target.get("member_no") or "-")
    new_credit  = int(target.get("credit", 0) or 0)

    sender_name    = ""
    sender_short   = ""
    receiver_name  = ""
    receiver_short = ""

    if isinstance(slip_data, dict):
        try:
            raw = slip_data.get("data", {}).get("rawSlip") or {}
            s = raw.get("sender", {})
            sender_name  = s.get("account", {}).get("name", {}).get("th") or s.get("account", {}).get("name", {}).get("en") or ""
            sender_short = s.get("bank", {}).get("short") or ""
            r = raw.get("receiver", {})
            receiver_name  = r.get("account", {}).get("name", {}).get("th") or r.get("account", {}).get("name", {}).get("en") or ""
            receiver_short = r.get("bank", {}).get("short") or ""
        except Exception:
            pass

    def logo_img(short_code, size="36px"):
        url = bank_logo_url(short_code)
        if not url:
            return None
        return {"type": "image", "url": url, "size": size,
                "aspectMode": "fit", "aspectRatio": "1:1", "flex": 0}

    def party_box(name, short_code):
        """กล่องแสดงโลโก้ + ชื่อ + ธนาคาร"""
        logo = logo_img(short_code, "40px")
        contents = []
        if logo:
            contents.append(logo)
        contents.append({
            "type": "box", "layout": "vertical", "spacing": "none",
            "contents": [
                {"type": "text", "text": name or "-", "size": "xs",
                 "color": "#111111", "weight": "bold", "wrap": True,
                 "maxLines": 2},
                {"type": "text", "text": short_code or "-", "size": "xxs",
                 "color": "#9CA3AF"},
            ]
        })
        return {
            "type": "box", "layout": "horizontal",
            "spacing": "sm", "alignItems": "center",
            "flex": 5, "contents": contents,
        }

    def arrow_box():
        return {
            "type": "box", "layout": "vertical", "flex": 1,
            "alignItems": "center", "justifyContent": "center",
            "contents": [{"type": "text", "text": "→", "size": "sm",
                          "color": "#9CA3AF", "align": "center"}]
        }

    def kv(label, value, val_color="#111111"):
        return {
            "type": "box", "layout": "horizontal", "spacing": "sm",
            "contents": [
                {"type": "text", "text": label, "size": "xs",
                 "color": "#9CA3AF", "flex": 4},
                {"type": "text", "text": str(value) if value else "-",
                 "size": "xs", "color": val_color, "flex": 6,
                 "align": "end", "weight": "bold", "wrap": True},
            ]
        }

    body = [
        # ── แถวผู้โอน → ผู้รับ ──
        {
            "type": "box", "layout": "horizontal",
            "spacing": "sm", "alignItems": "center",
            "margin": "none",
            "contents": [
                party_box(sender_name, sender_short),
                arrow_box(),
                party_box(receiver_name, receiver_short),
            ]
        },
        {"type": "separator", "margin": "md", "color": "#F3F4F6"},
        # ── ข้อมูล ──
        {
            "type": "box", "layout": "vertical",
            "spacing": "xs", "margin": "md",
            "contents": [
                kv("จำนวน", f"{format_topup_amount(amount)} บาท", "#16A34A"),
                kv("เครดิตที่ได้", f"+{credit_to_add:,}", "#16A34A"),
                kv("ชื่อ LINE", member_name),
                kv("ID สมาชิก", f"#{member_no}"),
                kv("เครดิตคงเหลือ", f"{new_credit:,}", "#16A34A"),
            ]
        },
    ]

    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box", "layout": "horizontal",
            "backgroundColor": "#16A34A",
            "paddingAll": "12px", "spacing": "sm",
            "contents": [
                {"type": "text", "text": "✅  ตรวจสลิปสำเร็จ",
                 "weight": "bold", "size": "sm", "color": "#FFFFFF"},
                {"type": "text", "text": f"+{credit_to_add:,} เครดิต",
                 "size": "sm", "color": "#FFFFFF", "align": "end",
                 "weight": "bold"},
            ]
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "14px", "spacing": "none",
            "contents": body,
        },
        "footer": {
            "type": "box", "layout": "vertical",
            "paddingAll": "10px", "backgroundColor": "#F9FAFB",
            "contents": [{
                "type": "text",
                "text": "เก็บประวัติสลิปแล้ว ระบบจะไม่เติมซ้ำ",
                "size": "xxs", "color": "#9CA3AF", "align": "center",
            }]
        }
    }



def slip_fail_flex(title="❌ ตรวจสลิปไม่สำเร็จ", message="ระบบยังไม่เติมเครดิต", reason=None, suggestion=None):
    details = []
    if message:
        details.append(("สถานะ", message, "#EF4444"))
    if reason:
        details.append(("สาเหตุ", reason, "#374151"))
    if suggestion:
        details.append(("คำแนะนำ", suggestion, "#6B7280"))

    return slip_status_flex(
        title=title,
        subtitle="ระบบยังไม่เติมเครดิตให้รายการนี้",
        status_text="ไม่ผ่าน",
        color="#EF4444",
        emoji="❌",
        details=details,
        footer_text="กรุณาตรวจสอบสลิปหรือส่งรูปสลิปใหม่อีกครั้ง",
    )


def slip_warning_flex(title="⚠️ ตรวจพบรายการซ้ำ", message="ระบบไม่เติมเครดิตซ้ำ", slip_ref=None, created_at=None):
    ref_short = str(slip_ref or "-")
    if len(ref_short) > 26:
        ref_short = ref_short[:12] + "..." + ref_short[-8:]

    details = [
        ("สถานะ", message, "#F59E0B"),
        ("Ref", ref_short, "#6B7280"),
    ]
    if created_at:
        details.insert(1, ("เติมเมื่อ", created_at))

    return slip_status_flex(
        title=title,
        subtitle="เพื่อป้องกันการเติมเครดิตซ้ำ",
        status_text="สลิปซ้ำ",
        color="#F59E0B",
        emoji="⚠️",
        details=details,
        footer_text="ถ้าคิดว่าระบบผิดพลาด ให้ติดต่อแอดมินพร้อมรูปสลิปนี้",
    )


def slip_config_error_flex(reason):
    return slip_status_flex(
        title="⚙️ ระบบตรวจสลิปมีปัญหา",
        subtitle="ตั้งค่าระบบตรวจสลิปยังไม่ถูกต้อง",
        status_text="ตั้งค่าไม่ครบ",
        color="#6B7280",
        emoji="⚙️",
        details=[
            ("สาเหตุ", reason, "#374151"),
            ("ให้แอดมินตรวจ", ".env / Token / Endpoint / IP Whitelist", "#6B7280"),
        ],
        footer_text="ข้อความนี้เป็นการแจ้งปัญหาการตั้งค่าระบบ ไม่ได้หมายความว่าสลิปผิด",
    )


def is_bangkok_bank_transfer(data):
    """
    ตรวจเฉพาะ "ธนาคารต้นทาง/ผู้โอน" ว่าเป็นธนาคารกรุงเทพจริงหรือไม่

    แก้บัคเดิม:
    - ห้ามใช้ keyword กว้าง ๆ เช่น "กรุงเทพ" หรือ "002" จากทั้ง response
      เพราะเลข 002 / คำกรุงเทพอาจไปอยู่ใน reference, payload, หรือข้อมูลอื่นได้
    - ห้ามเอาบัญชีปลายทาง/receiver/destination มาตัดสินว่าเป็นสลิป ธ.กรุงเทพ
    - จะถือว่าเป็นกรุงเทพเฉพาะเมื่อ field ที่เกี่ยวกับผู้โอน/ต้นทาง/ธนาคารต้นทาง
      มีค่า Bangkok Bank / BBL / ธ.กรุงเทพ / ธนาคารกรุงเทพ / bank code 002 อย่างชัดเจน
    """
    # ── EasySlip: ตรวจจาก data.sender.bank.name / data.sender.bank.short ──────
    try:
        sender_bank = data.get("data", {}).get("sender", {}).get("bank", {})
        bank_name = str(sender_bank.get("name") or sender_bank.get("short") or "").lower()
        bbl_keywords = ["กรุงเทพ", "bangkok", "bbl"]
        if any(k in bank_name for k in bbl_keywords):
            return True
    except Exception:
        pass
    clean_data = remove_internal_slip2go_debug(data)
    values = walk_json_values(clean_data)

    def norm(value):
        return normalize_compare_text(value)

    sender_path_words = [
        "sender", "from", "payer", "source", "transferor", "transferrer",
        "debit", "origin", "originator", "remitter",
        "ผู้โอน", "ต้นทาง", "ผู้จ่าย", "ธนาคารผู้โอน", "บัญชีผู้โอน",
    ]

    receiver_path_words = [
        "receiver", "recipient", "to", "destination", "beneficiary", "credit",
        "ผู้รับ", "ปลายทาง", "บัญชีผู้รับ", "ธนาคารผู้รับ",
    ]

    bank_field_words = [
        "bank", "bankname", "bank_name", "bankcode", "bank_code",
        "accounttype", "account_type", "accountbank", "account_bank",
        "ธนาคาร", "ธนาคารผู้โอน",
    ]

    bank_name_keywords = [
        "ธ.กรุงเทพ", "ธนาคารกรุงเทพ", "bangkok bank", "bangkokbank", "bbl",
    ]
    normalized_bank_names = [norm(k) for k in bank_name_keywords if norm(k)]

    for path, value in values:
        path_text = str(path or "").lower()
        value_text = str(value or "").strip()
        if not value_text:
            continue

        path_norm = norm(path_text)
        value_norm = norm(value_text)

        # ถ้า path เป็นปลายทาง/ผู้รับ ให้ข้าม ไม่เอามาคิดว่าเป็นธนาคารต้นทาง
        if any(norm(k) and norm(k) in path_norm for k in receiver_path_words):
            continue

        is_sender_path = any(norm(k) and norm(k) in path_norm for k in sender_path_words)
        is_bank_field = any(norm(k) and norm(k) in path_norm for k in bank_field_words)

        # ต้องเป็น field ที่ชี้ว่าเกี่ยวกับต้นทาง/ผู้โอน หรือเป็น field bank แบบชัดเจนเท่านั้น
        if not (is_sender_path or is_bank_field):
            continue

        # ชื่อธนาคารกรุงเทพแบบชัดเจน
        if any(k and k in value_norm for k in normalized_bank_names):
            return True

        # bank code 002/01002 ใช้ได้เฉพาะ field bank code/account type เท่านั้น
        # ห้ามเช็คจาก ref/transaction id เพราะจะ false positive ง่ายมาก
        if any(norm(k) in path_norm for k in ["bankcode", "bank_code", "accounttype", "account_type"]):
            digits = re.sub(r"\D", "", value_text)
            if digits in {"002", "01002"}:
                return True

    # fallback แบบเข้มงวด: เฉพาะกรณี response มีคำว่า Bangkok Bank/BBL/ธนาคารกรุงเทพ ชัดเจน
    # ไม่ใช้คำว่า "กรุงเทพ" คำเดียว และไม่ใช้เลข 002 จาก blob รวม
    blob = norm(slip2go_text_blob(clean_data))
    strict_blob_keywords = [norm(k) for k in ["ธนาคารกรุงเทพ", "ธกรุงเทพ", "bangkokbank", "bbl"]]
    return any(k and k in blob for k in strict_blob_keywords)

def slip_bangkok_bank_wait_flex():
    return slip_status_flex(
        title="⏳ สลิป ธ.กรุงเทพ",
        subtitle="ธนาคารอาจใช้เวลาซิงก์ข้อมูลเข้าระบบ",
        status_text="รอ 2-3 นาที",
        color="#F59E0B",
        emoji="🏦",
        details=[
            ("สถานะ", "ยังไม่เติมเครดิต", "#F59E0B"),
            ("คำแนะนำ", "โอนจาก ธ.กรุงเทพ ให้รอ 2-3 นาที แล้วส่งสลิปใหม่อีกครั้งนะคะ", "#374151"),
        ],
        footer_text="ระบบจะเติมเครดิตเมื่อส่งสลิปใหม่และตรวจผ่านเท่านั้น",
    )


def slip_pending_retry_flex(reason="EasySlip ยังไม่ยืนยันสลิปนี้"):
    """
    ใช้กรณี Slip2Go ตอบว่าสลิปซ้ำ/ยังไม่พร้อม แต่บอทยังไม่เคยเติมเครดิตจริง
    ห้ามขึ้นว่า 'สลิปซ้ำ' เพราะจะทำให้ลูกค้าเข้าใจผิดว่าเคยเติมแล้ว
    """
    return slip_status_flex(
        title="⏳ รอตรวจสลิปอีกครั้ง",
        subtitle="ระบบยังไม่เติมเครดิตให้รายการนี้",
        status_text="ยังไม่เติมเครดิต",
        color="#F59E0B",
        emoji="⏳",
        details=[
            ("สถานะ", "ยังไม่เติมเครดิต", "#F59E0B"),
            ("สาเหตุ", reason, "#374151"),
            ("คำแนะนำ", "ให้รอประมาณ 2-3 นาที แล้วส่งสลิปใหม่อีกครั้งนะคะ", "#374151"),
        ],
        footer_text="ระบบจะเติมเครดิตเมื่อตรวจสลิปผ่านเท่านั้น ถ้าเคยเติมแล้วระบบจะแจ้งว่าสลิปซ้ำ",
    )



def is_slip_not_found_response(reject_type: str = None, message: str = "") -> bool:
    """
    Slip2Go code 200404 / Slip not found แปลว่ายังตรวจไม่เจอในระบบธนาคาร
    ใช้สำหรับแจ้งให้ลูกค้ารอแล้วส่งใหม่ โดยไม่เติมเครดิตและไม่บันทึกเป็นสลิปใช้แล้ว
    """
    if reject_type == "not_found":
        return True

    msg = str(message or "").lower()
    keywords = [
        "200404",
        "slip not found",
        "not found slip",
        "ไม่พบข้อมูลสลิป",
        "ไม่พบสลิป",
        "ไม่มีข้อมูลสลิป",
        "หมดอายุ",
    ]
    return any(k.lower() in msg for k in keywords)


def slip_not_found_wait_flex():
    return slip_status_flex(
        title="⏳ รอตรวจสลิปอีกครั้ง",
        subtitle="ระบบยังไม่พบข้อมูลสลิปในระบบธนาคาร",
        status_text="ยังไม่เติมเครดิต",
        color="#F59E0B",
        emoji="⏳",
        details=[
            ("สถานะ", "ยังไม่เติมเครดิต", "#F59E0B"),
            ("สาเหตุ", "ระบบยังไม่พบข้อมูลสลิปในระบบธนาคาร", "#374151"),
            ("คำแนะนำ", "ถ้าเป็นสลิป ธ.กรุงเทพ หรือสลิปที่เพิ่งโอน ให้รอประมาณ 2-3 นาที แล้วส่งสลิปใหม่อีกครั้งนะคะ", "#374151"),
        ],
        footer_text="ระบบจะเติมเครดิตเมื่อส่งสลิปใหม่และตรวจผ่านเท่านั้น",
    )


def slip2go_reject_flex(data=None, reject_type: str = None, reject_msg: str = None):
    """
    แสดง FLEX ทุกครั้งเมื่อ Slip2Go ตรวจแล้วไม่ผ่านเงื่อนไขเติมเครดิต
    อ้างอิง response code จาก Slip2Go:
    - 200200 = Slip is Valid เท่านั้นที่เติมเครดิต
    - 200401/200402/200403/200404/200500/200501 และ code อื่น ๆ = ไม่เติมเครดิตและต้องแจ้ง FLEX
    """
    code = None
    if isinstance(data, dict):
        code, _ = get_slip2go_response_code(data)

    code = str(code or "-")
    reject_type = reject_type or "not_valid"
    reject_msg = reject_msg or "ระบบยังไม่ได้ยืนยันสลิปนี้"

    status_map = {
        "receiver": {
            "title": "❌ บัญชีผู้รับไม่ถูกต้อง",
            "status": "บัญชีไม่ตรง",
            "reason": "บัญชีผู้รับในสลิปไม่ตรงกับบัญชีร้านที่ตั้งไว้",
            "suggestion": "ตรวจว่าลูกค้าโอนเข้าบัญชีร้านถูกต้อง หรือให้ส่งสลิปของรายการที่โอนเข้าบัญชีร้านจริง",
        },
        "amount": {
            "title": "❌ ยอดโอนไม่ตรงเงื่อนไข",
            "status": "ยอดไม่ตรง",
            "reason": "ยอดเงินโอนในสลิปไม่ตรงกับเงื่อนไขที่ระบบตรวจสอบ",
            "suggestion": "ตรวจยอดโอนในสลิป หรือให้ลูกค้าส่งสลิปใหม่ที่ถูกต้อง",
        },
        "date": {
            "title": "❌ วันที่โอนไม่ตรงเงื่อนไข",
            "status": "วันที่ไม่ตรง",
            "reason": "วันที่/เวลาการโอนในสลิปไม่ตรงกับเงื่อนไขที่ระบบตรวจสอบ",
            "suggestion": "ตรวจวันที่สลิป หรือให้ลูกค้าส่งสลิปล่าสุดอีกครั้ง",
        },
        "not_found": {
            "title": "⏳ ยังไม่พบข้อมูลสลิป",
            "status": "ยังไม่เติมเครดิต",
            "reason": "ระบบยังไม่พบข้อมูลสลิปในระบบธนาคาร",
            "suggestion": "ถ้าเพิ่งโอน ให้รอ 2-3 นาที แล้วส่งสลิปใหม่อีกครั้ง",
        },
        "fraud": {
            "title": "🚫 สลิปไม่ผ่านการตรวจสอบ",
            "status": "สลิปเสี่ยง/สลิปเสีย",
            "reason": "ระบบแจ้งว่าสลิปเสี่ยงเป็นสลิปปลอมหรือสลิปเสีย",
            "suggestion": "ให้ลูกค้าส่งหลักฐานใหม่ หรือให้แอดมินตรวจสอบก่อนเติมเครดิตเอง",
        },
        "duplicate": {
            "title": "⚠️ สลิปซ้ำ",
            "status": "ไม่เติมซ้ำ",
            "reason": "ระบบแจ้งว่าสลิปนี้ถูกใช้งานหรือตรวจซ้ำแล้ว",
            "suggestion": "ตรวจประวัติการเติมเครดิต หรือให้ลูกค้าส่งสลิปอื่น",
        },
        "not_valid": {
            "title": "❌ สลิปยังไม่ผ่านเงื่อนไข",
            "status": "ยังไม่เติมเครดิต",
            "reason": reject_msg,
            "suggestion": "ระบบจะเติมเครดิตเฉพาะสลิปที่ตรวจผ่านและยืนยันแล้วเท่านั้น",
        },
    }

    info = status_map.get(reject_type, status_map["not_valid"])
    details = [
        ("สถานะ", info["status"], "#EF4444" if reject_type not in {"not_found", "duplicate"} else "#F59E0B"),
        ("สาเหตุ", info["reason"], "#374151"),
        ("คำแนะนำ", info["suggestion"], "#6B7280"),
    ]

    if reject_msg and reject_msg != info["reason"]:
        details.insert(2, ("รายละเอียด", reject_msg, "#6B7280"))

    color = "#F59E0B" if reject_type in {"not_found", "duplicate"} else "#EF4444"
    emoji = "⏳" if reject_type == "not_found" else ("⚠️" if reject_type == "duplicate" else "❌")

    return slip_status_flex(
        title=info["title"],
        subtitle="ระบบตรวจสลิปแล้ว แต่ยังไม่เติมเครดิตให้รายการนี้",
        status_text=info["status"],
        color=color,
        emoji=emoji,
        details=details,
        footer_text="ระบบเติมเครดิตอัตโนมัติเฉพาะสลิปที่ตรวจผ่านแล้วเท่านั้น",
    )

def should_silence_slip_reject(reject_type: str = None, message: str = "") -> bool:
    """
    โหมดเงียบสำหรับรูป/สลิปที่ไม่ควรเติมเครดิต
    - ไม่ใช่สลิป / ไม่พบสลิป
    - สลิปบัญชีรับเงินไม่ตรง
    - สลิปปลอม/เสีย/ไม่ valid
    - ยอด/วันที่ไม่ตรงเงื่อนไข

    หมายเหตุ: สลิปซ้ำยังตอบเตือนอยู่ เพื่อกันลูกค้าเข้าใจว่าเติมแล้วแต่เครดิตไม่เข้า
    """
    silent_reject_types = {
        "not_found",
        "receiver",
        "amount",
        "date",
        "fraud",
        "not_valid",
    }
    if reject_type in silent_reject_types:
        return True

    msg = str(message or "").lower()
    silent_keywords = [
        "200404", "slip not found", "not found slip",
        "ไม่พบข้อมูลสลิป", "ไม่พบสลิป", "ไม่มีข้อมูลสลิป",
        "200401", "recipient account not match", "receiver account not match", "account not match",
        "บัญชีผู้รับไม่ถูกต้อง", "บัญชีผู้รับไม่ตรง", "บัญชีรับเงินไม่ตรง", "ผู้รับไม่ตรง",
        "200500", "fraud", "สลิปปลอม", "สลิปเสีย",
        "200402", "transfer amount not match", "ยอดเงินโอนไม่ตรง",
        "200403", "transfer date not match", "วันที่โอนไม่ตรง",
    ]
    return any(k.lower() in msg for k in silent_keywords)


def auto_topup_credit_from_slip(event, image_bytes: bytes = None):
    """ลูกค้าส่งรูปสลิปในแชทส่วนตัวกับ OA -> ตรวจ EasySlip -> เติมเครดิตอัตโนมัติ และตอบกลับเป็น Flex"""
    if not is_private_chat(event):
        return None

    user_id = event.source.user_id

    with STATE_LOCK:
        user = get_registered_topup_user(user_id)

    if not user:
        return no_member_id_topup_flex()

    message_id = get_message_id(event)

    if image_bytes is None:
        try:
            image_bytes = get_line_image_bytes(message_id)
        except Exception as e:
            return slip_fail_flex(
                reason=f"ดึงรูปสลิปจาก LINE ไม่สำเร็จ: {e}",
                suggestion="ส่งรูปสลิปใหม่อีกครั้ง หรือรอสักครู่แล้วลองใหม่",
            )

    if not is_likely_slip_image(image_bytes):
        return None

    ok, msg, data = call_easyslip_api(image_bytes)

    if not ok:
        # Timeout / network error
        if isinstance(data, dict):
            debug = data.get("_easyslip_debug", {})
            err_type = debug.get("error_type", "")
            if err_type in ("timeout", "connection_error"):
                return slip_pending_retry_flex(
                    "EasySlip ตอบช้าหรือเชื่อมต่อไม่ทัน ระบบยังไม่เติมเครดิตและยังไม่บันทึกว่าสลิปนี้ถูกใช้แล้ว"
                )

        # V2: 404 + SLIP_PENDING = ธ.กรุงเทพยังไม่ sync
        if msg == "slip_not_found" and isinstance(data, dict):
            if easyslip_is_bbl_pending(data):
                return slip_bangkok_bank_wait_flex()
            # 404 ทั่วไป = หาสลิปไม่เจอในระบบ
            error_code = easyslip_get_error_code(data)
            if error_code == "SLIP_NOT_FOUND":
                return slip_not_found_wait_flex()
            return slip_not_found_wait_flex()

        # ปัญหาตั้งค่า API
        config_keywords = ["EASYSLIP_API_KEY", ".env", "HTTP 401", "HTTP 403", "ไม่ได้ตั้งค่า"]
        if any(k.lower() in str(msg).lower() for k in config_keywords):
            return slip_config_error_flex(msg)

        return slip_fail_flex(reason=msg, suggestion="ตรวจภาพสลิปให้ชัด หรือส่งใหม่อีกครั้ง")

    if not isinstance(data, dict):
        return slip_fail_flex(
            reason="รูปแบบข้อมูลจาก EasySlip ไม่ถูกต้อง",
            suggestion="ให้แอดมินตรวจ response/debug จาก EasySlip",
        )

    # ── V2: ตรวจสถานะ verified ──────────────────────────────────────────────────
    if not easyslip_is_verified(data):
        error_code = easyslip_get_error_code(data)
        if error_code == "SLIP_PENDING" or is_bangkok_bank_transfer(data):
            return slip_bangkok_bank_wait_flex()
        if error_code == "SLIP_NOT_FOUND":
            return slip_not_found_wait_flex()
        reason = f"ระบบยังไม่ยืนยันสลิป" + (f" ({error_code})" if error_code else "")
        return slip2go_reject_flex(data, "not_valid", reason)

    # ── V2: ตรวจสลิปซ้ำจาก EasySlip (isDuplicate) ──────────────────────────
    slip_ref, ref_path = easyslip_extract_reference(data, image_bytes)

    # ── ตรวจบัญชีผู้รับ ทุกกรณี (ทั้ง duplicate และไม่ duplicate) ────────────
    # สำคัญ: ต้องตรวจก่อนเติมเสมอ กันสลิปโอนบัญชีอื่นถูกเติมเครดิต
    if not easyslip_receiver_check_passed(data):
        if EASYSLIP_DEBUG_MODE:
            try:
                raw = data.get("data", {}).get("rawSlip") or {}
                r = raw.get("receiver", {})
                acct = r.get("account", {})
                print(f"EASYSLIP RECEIVER FAIL: bank={acct.get('bank')}, proxy={acct.get('proxy')}, name={acct.get('name')}, expected_no={EASYSLIP_ACCOUNT_NUMBER!r}, expected_name={EASYSLIP_ACCOUNT_NAME_TH!r}")
            except Exception:
                pass
        return slip2go_reject_flex(data, "receiver", "บัญชีผู้รับไม่ถูกต้องหรือไม่ตรงกับบัญชีร้าน")

    # ── ตรวจสลิปซ้ำจากฐานข้อมูลของบอทเอง ───────────────────────────────────
    with STATE_LOCK:
        old = SLIP_TOPUPS.setdefault("slips", {}).get(slip_ref)
    if old:
        return slip_warning_flex(
            title="⚠️ สลิปนี้ถูกเติมเครดิตไปแล้ว",
            message="ระบบพบประวัติเติมเครดิตของสลิปนี้แล้ว",
            slip_ref=slip_ref,
            created_at=old.get("created_at", "-"),
        )

    # ── EasySlip บอก isDuplicate แต่บอทยังไม่เคยเติม ─────────────────────────
    # (เช่น ส่งสลิปครั้งแรก error ก่อนเติม แล้วส่งซ้ำ)
    # ผ่านการตรวจบัญชีแล้ว → เติมได้ปกติ
    if easyslip_is_duplicate(data):
        if EASYSLIP_DEBUG_MODE:
            print(f"EASYSLIP DUPLICATE but not in local DB: slip_ref={slip_ref}, receiver passed, proceeding to topup")

    amount, amount_path = easyslip_extract_amount(data)
    if amount is None or amount < MIN_TOPUP_AMOUNT:
        return slip_fail_flex(
            title="❌ อ่านยอดเงินไม่ได้",
            reason=f"อ่านยอดเงินจากสลิปไม่ได้ หรือยอดต่ำกว่าขั้นต่ำ {format_topup_amount(MIN_TOPUP_AMOUNT)} บาท",
            suggestion="ส่งรูปสลิปที่ชัดกว่าเดิม เห็นยอดเงินครบถ้วน",
        )

    credit_to_add = int((amount * AUTO_TOPUP_RATE).to_integral_value(rounding="ROUND_FLOOR"))
    if credit_to_add <= 0:
        return slip_fail_flex(
            title="❌ คำนวณเครดิตไม่ได้",
            reason="ยอดเครดิตที่คำนวณได้ไม่ถูกต้อง",
            suggestion="ให้แอดมินตรวจ AUTO_TOPUP_RATE ใน .env",
        )

    # ── Sync ชื่อ LINE ล่าสุด (background, ไม่บล็อก) ────────────────────────
    queue_profile_refresh(user_id)

    with STATE_LOCK:
        slips = SLIP_TOPUPS.setdefault("slips", {})
        if slip_ref in slips:
            old = slips.get(slip_ref, {})
            return slip_warning_flex(
                title="⚠️ สลิปนี้ถูกเติมเครดิตไปแล้ว",
                message="ระบบพบประวัติเติมเครดิตของสลิปนี้แล้ว",
                slip_ref=slip_ref,
                created_at=old.get("created_at", "-"),
            )

        target = get_registered_topup_user(user_id)
        if not target:
            return no_member_id_topup_flex()

        old_credit = int(target.get("credit", 0) or 0)
        target["credit"] = old_credit + credit_to_add

        slips[slip_ref] = {
            "user_id": user_id,
            "member_no": target.get("member_no"),
            "line_name": target.get("line_name") or target.get("name"),
            "amount_baht": str(amount),
            "credit_added": credit_to_add,
            "old_credit": old_credit,
            "new_credit": target["credit"],
            "ref_path": ref_path,
            "amount_path": amount_path,
            "line_message_id": message_id,
            "created_at": now_text(),
            "easyslip_response": data,
        }

        save_user_db()
        save_slip_topup_db()

    return slip_success_flex(target, amount, credit_to_add, old_credit, slip_ref, slip_data=data)



# ======================================================
# LINE send helpers
# ======================================================

def line_text_payload(text, quote_token=None):
    payload = {"type": "text", "text": str(text)}
    if quote_token:
        payload["quoteToken"] = str(quote_token)
    return payload


def get_quote_token(event):
    """ดึง quoteToken จากข้อความที่ผู้ใช้ส่งมา เพื่อให้บอทตอบแบบ Reply/Quote ข้อความนั้นได้"""
    message = getattr(event, "message", None)
    if not message:
        return None
    return (
        getattr(message, "quote_token", None)
        or getattr(message, "quoteToken", None)
    )


def reply_problem(event, text):
    """ตอบข้อความแจ้งปัญหาโดย quote ข้อความต้นทางของคนที่พิมพ์ผิด/เครดิตไม่พอ"""
    return reply_text(
        event.reply_token,
        text,
        quote_token=get_quote_token(event),
    )


def line_flex_payload(alt_text, flex_dict):
    return {
        "type": "flex",
        "altText": str(alt_text or "Flex Message"),
        "contents": flex_dict,
    }


def _line_headers():
    return {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _post_line_api(url: str, payload: dict, timeout_seconds: float, label: str) -> bool:
    """
    ส่ง LINE API ด้วย requests + timeout แบบแยก connect/read พร้อม retry สั้น ๆ
    แก้เคส api.line.me connect timeout แล้ว webhook ค้างหรือ reply หลุด
    หมายเหตุ: ถ้าเน็ต/ไฟร์วอลล์ของเครื่องออก api.line.me ไม่ได้จริง ๆ โค้ดจะไม่ค้าง แต่ LINE จะยังส่งไม่สำเร็จ
    """
    # สำรองสถานะล่าสุดก่อนส่งข้อความออก LINE
    # ช่วยกันข้อมูลรอบ/คู่ติดหาย แม้ LINE API timeout หรือบอทถูกรีสตาร์ทหลังประมวลผลแล้ว
    # ยกเว้นช่วงสั้น ๆ หลังคำสั่งล้าง round_backups ไม่งั้น reply ของคำสั่งล้างจะสร้างไฟล์ backup กลับมาทันที
    if not ROUND_BACKUP_SUPPRESS_UNTIL or time.time() >= ROUND_BACKUP_SUPPRESS_UNTIL:
        save_round_backup_db(reason=f"before_line_send:{label}")

    if not LINE_CHANNEL_ACCESS_TOKEN:
        print(f"{label} ERROR: missing LINE_CHANNEL_ACCESS_TOKEN")
        return False

    attempts = max(1, int(LINE_API_RETRIES or 1))
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            response = LINE_HTTP_SESSION.post(
                url,
                headers=_line_headers(),
                json=payload,
                timeout=(LINE_CONNECT_TIMEOUT_SECONDS, timeout_seconds),
            )

            if 200 <= response.status_code < 300:
                return True

            # 5xx/429 ลองซ้ำได้, 400/401/403 คือ payload/token/permission ผิด ไม่ควรซ้ำ
            if response.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
                print(
                    f"{label} RETRY HTTP {response.status_code} attempt {attempt}/{attempts}: "
                    f"{response.text[:300]}"
                )
                time.sleep(LINE_API_RETRY_DELAY_SECONDS * attempt)
                continue

            print(f"{label} ERROR HTTP {response.status_code}: {response.text[:500]}")
            return False

        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.Timeout) as e:
            last_error = e
            if attempt < attempts:
                print(f"{label} TIMEOUT attempt {attempt}/{attempts}: {e}")
                time.sleep(LINE_API_RETRY_DELAY_SECONDS * attempt)
                continue
            print(f"{label} ERROR: LINE API timeout after {attempts} attempts: {e}")
            return False

        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < attempts:
                print(f"{label} REQUEST ERROR attempt {attempt}/{attempts}: {e}")
                time.sleep(LINE_API_RETRY_DELAY_SECONDS * attempt)
                continue
            print(f"{label} ERROR: {e}")
            return False

        except Exception as e:
            last_error = e
            print(f"{label} ERROR: {e}")
            return False

    if last_error:
        print(f"{label} ERROR: {last_error}")
    return False

def reply_text(reply_token, text, quote_token=None):
    if not text:
        return False

    payload = {
        "replyToken": reply_token,
        "messages": [line_text_payload(text, quote_token=quote_token)],
    }
    return _post_line_api(LINE_API_REPLY_URL, payload, LINE_REPLY_TIMEOUT_SECONDS, "REPLY TEXT")


def reply_flex(reply_token, alt_text, flex_dict):
    if not flex_dict:
        return False

    payload = {
        "replyToken": reply_token,
        "messages": [line_flex_payload(alt_text, flex_dict)],
    }
    return _post_line_api(LINE_API_REPLY_URL, payload, LINE_REPLY_TIMEOUT_SECONDS, "REPLY FLEX")


def reply_text_and_flex(reply_token, text, alt_text, flex_dict, quote_token=None):
    """ส่ง TEXT + FLEX ใน replyToken เดียวกัน เพื่อไม่ให้ LINE reject เพราะใช้ replyToken ซ้ำ"""
    if not text or not flex_dict:
        return False

    payload = {
        "replyToken": reply_token,
        "messages": [
            line_text_payload(text, quote_token=quote_token),
            line_flex_payload(alt_text, flex_dict),
        ],
    }
    return _post_line_api(LINE_API_REPLY_URL, payload, LINE_REPLY_TIMEOUT_SECONDS, "REPLY TEXT+FLEX")


def push_text(to, text):
    if not to or not text:
        return False

    payload = {
        "to": to,
        "messages": [line_text_payload(text)],
    }
    return _post_line_api(LINE_API_PUSH_URL, payload, LINE_PUSH_TIMEOUT_SECONDS, f"PUSH TEXT to={to}")


def push_flex(to, alt_text, flex_dict):
    if not to or not flex_dict:
        return False

    payload = {
        "to": to,
        "messages": [line_flex_payload(alt_text, flex_dict)],
    }
    return _post_line_api(LINE_API_PUSH_URL, payload, LINE_PUSH_TIMEOUT_SECONDS, f"PUSH FLEX to={to}")


def push_text_async(to, text):
    EXECUTOR.submit(push_text, to, text)


def push_flex_async(to, alt_text, flex_dict):
    EXECUTOR.submit(push_flex, to, alt_text, flex_dict)


# ======================================================
# Parse commands
# ======================================================

def parse_open_command(text):
    m = re.match(r"^เปิด\s+(.+)$", text.strip())
    return m.group(1).strip() if m else None


def parse_change_camp_command(text):
    """
    เปลี่ยนค่าย <ชื่อค่าย>
    ใช้เมื่อแอดมินเปิดค่ายผิด ต้องคืนบิลเดิมและเริ่มรอบใหม่ด้วยชื่อค่ายที่ถูกต้อง
    รองรับกรณีมีวรรณยุกต์/สระหลุดนำหน้าคำสั่ง เช่น ้เปลี่ยนค่าย
    """
    clean = text.strip()
    # ตัดอักขระสระ/วรรณยุกต์ไทยที่อาจหลุดมาต้นข้อความจากคีย์บอร์ด
    clean = re.sub(r"^[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]+", "", clean)
    m = re.match(r"^เปลี่ยนค่าย\s+(.+)$", clean)
    if not m:
        return None

    camp_name = m.group(1).strip()
    return camp_name or None



def is_continue_round_command(text: str) -> bool:
    """
    คำสั่งเปิดให้ลูกค้ากลับมาเล่นต่อในรอบเดิม หลังจากแอดมินปิดรอบไปแล้ว
    รองรับ: เล่นต่อครับ / เล่นต่อคับ / เล่นต่อค่ะ / เล่นต่อคะ / เล่นต่อ
    """
    clean = re.sub(r"\s+", "", (text or "").strip())
    return clean in {"เล่นต่อครับ", "เล่นต่อคับ", "เล่นต่อค่ะ", "เล่นต่อคะ", "เล่นต่อ"}


def has_price_or_result_started(state=None) -> bool:
    """คงไว้เพื่อ compatibility; คำสั่งเล่นต่อให้บล็อกเฉพาะเมื่อแจ้งผลจบแล้วเท่านั้น"""
    st = state or STATE
    return bool(st.get("settled") or st.get("result") is not None)


def continue_round_for_play(chat_id: str = None) -> str:
    """เปิดรับแผลต่อในรอบเดิม หลังปิดรอบแล้ว ใช้ได้แม้แจ้งราคาช่างแล้ว แต่ห้ามใช้หลังออกผลแล้ว"""
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบให้เล่นต่อ กรุณาเปิดรอบก่อน"

    if STATE.get("settled"):
        return "รอบนี้แจ้งผลแล้ว ไม่สามารถเล่นต่อได้"

    if STATE.get("opened"):
        return "รอบนี้เปิดให้เล่นอยู่แล้ว"

    # อนุญาตให้เล่นต่อได้แม้แอดมินแจ้งราคาช่างแล้ว
    # เงื่อนไขห้ามมีอย่างเดียวคือรอบถูกออกผล/settle ไปแล้ว
    STATE["opened"] = True
    STATE["closed_at"] = None
    STATE["continued_at"] = now_text()
    STATE["continue_count"] = int(STATE.get("continue_count", 0) or 0) + 1
    STATE["updated_at"] = now_text()
    if chat_id and not STATE.get("chat_id"):
        STATE["chat_id"] = chat_id

    return (
        f"✅ เปิดให้เล่นต่อ {base_label_pretty()} แล้ว\n\n"
        f"ชื่อค่าย :  {STATE.get('camp_name') or '-'}\n\n"
        f"ลูกค้าสามารถโพสต์แผลและติดรายการในรอบเดิมต่อได้\n"
        f"หมายเหตุ: ใช้ได้แม้แจ้งราคาช่างแล้ว แต่ใช้ไม่ได้หลังออกผลแล้ว\n"
        f"เมื่อต้องการหยุดรับ ให้แอดมินพิมพ์: ปิด {STATE.get('camp_name') or '-'}"
    )


def parse_base_price(text):
    m = re.match(r"^ราคาช่าง\s+(\d+)\s*[-/]\s*(\d+)$", text.strip())
    if not m:
        return None

    a, b = int(m.group(1)), int(m.group(2))
    if a > b:
        a, b = b, a

    return a, b


def parse_two_digit_start_command(text):
    """คำสั่งแอดมินสำหรับแปลงแผลเลข 2 ตัว: เริ่มต้น1 / เริ่มต้น2 / เริ่มต้น3"""
    clean = re.sub(r"\s+", "", (text or "").strip())
    m = re.match(r"^เริ่มต้น([123])$", clean)
    if not m:
        return None
    return int(m.group(1))


def set_two_digit_start(start_no: int) -> str:
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบ กรุณาเปิดรอบก่อน"

    if STATE.get("opened"):
        return "ยังไม่สามารถแจ้งเริ่มต้นได้ ต้องปิดรอบก่อน"

    if STATE.get("settled"):
        return "รอบนี้แจ้งผลแล้ว ไม่สามารถเปลี่ยนเริ่มต้นได้"

    price_mode = STATE.get("price_mode")

    # กรณีช่างไม่ต่อย / ไม่ตี: อนุญาตให้แจ้งเริ่มต้นได้ โดยใช้ฐาน 100/200/300 ตายตัว
    if price_mode == "no_price":
        if start_no not in {1, 2, 3}:
            return "คำสั่งเริ่มต้นใช้ได้เฉพาะ เริ่มต้น1 / เริ่มต้น2 / เริ่มต้น3"

        STATE["two_digit_start"] = int(start_no)
        STATE["pending_result"] = None
        STATE["pending_result_at"] = None
        STATE["updated_at"] = now_text()

        anchor = start_no * 100
        example_a_min, example_a_max = two_digit_tokens_to_price_range(start_no, "30", "70")
        example_b_min, example_b_max = two_digit_tokens_to_price_range(start_no, "3", "7")
        example_c_min, example_c_max = two_digit_tokens_to_price_range(start_no, "50", "00")
        no_price_reason = STATE.get("no_price_reason", "ไม่ต่อย")
        save_round_backup_db(reason="two_digit_start_set")

        return (
            f"✅ บันทึกเริ่มต้นแล้ว! (กรณีช่าง{no_price_reason})\n\n"
            f"ราคาช่าง: {no_price_reason}\n"
            f"ฐานเริ่มต้น: {anchor}\n\n"
            f"ตัวอย่างที่ระบบจะคิด:\n"
            f"30-70 = {format_price_range_text(example_a_min, example_a_max)}\n"
            f"3-7 = {format_price_range_text(example_b_min, example_b_max)}\n"
            f"50-00 = {format_price_range_text(example_c_min, example_c_max)}"
        )

    if price_mode != "normal" or STATE.get("base_min") is None or STATE.get("base_max") is None:
        return "ต้องแจ้งราคาช่างเป็นตัวเลขก่อน เช่น ราคาช่าง 300-320 แล้วค่อยพิมพ์ เริ่มต้น3"

    if start_no not in {1, 2, 3}:
        return "คำสั่งเริ่มต้นใช้ได้เฉพาะ เริ่มต้น1 / เริ่มต้น2 / เริ่มต้น3"

    STATE["two_digit_start"] = int(start_no)
    STATE["pending_result"] = None
    STATE["pending_result_at"] = None
    STATE["updated_at"] = now_text()

    example_a_min, example_a_max = two_digit_tokens_to_price_range(start_no, "30", "70")
    example_b_min, example_b_max = two_digit_tokens_to_price_range(start_no, "3", "7")
    example_c_min, example_c_max = two_digit_tokens_to_price_range(start_no, "50", "00")
    save_round_backup_db(reason="two_digit_start_set")

    return (
        f"✅ บันทึกเริ่มต้นแล้ว!\n\n"
        f"ราคาช่าง: {format_price_range_text(STATE.get('base_min'), STATE.get('base_max'))}\n\n\n"
        f"ตัวอย่างที่ระบบจะคิด:\n"
        f"30-70 = {format_price_range_text(example_a_min, example_a_max)}\n"
        f"3-7 = {format_price_range_text(example_b_min, example_b_max)}\n"
        f"50-00 = {format_price_range_text(example_c_min, example_c_max)}"
    )


def parse_no_price_command(text):
    """
    กรณีช่างไม่มีราคา
    แอดมินใช้ได้ 2 คำสั่ง:
    - ราคาช่าง ไม่ต่อย
    - ราคาช่าง ไม่ตี
    """
    m = re.match(r"^ราคาช่าง\s+(ไม่ต่อย|ไม่ตี)$", text.strip())
    if not m:
        return None
    return m.group(1)


def is_confirm_price_command(text: str) -> bool:
    """คำยืนยันสำหรับประกาศราคาช่างพิเศษที่รอตรวจทาน"""
    return re.sub(r"\s+", "", (text or "").strip()) == "ยืนยัน"


def clear_pending_price():
    STATE["pending_price"] = None
    STATE["pending_price_at"] = None


def pending_price_text():
    pending = STATE.get("pending_price")
    if not isinstance(pending, dict):
        return ""

    if pending.get("type") == "no_price":
        return f"ราคาช่าง {pending.get('reason') or '-'}"

    return str(pending)


def request_no_price_confirm(no_price_reason: str) -> str:
    """
    ขั้นตอนที่ 1 ของคำสั่ง ราคาช่าง ไม่ต่อย / ไม่ตี
    ยังไม่เปลี่ยนราคาจริง จนกว่าแอดมินจะพิมพ์ ยืนยัน
    """
    STATE["pending_price"] = {
        "type": "no_price",
        "reason": no_price_reason,
    }
    STATE["pending_price_at"] = now_text()

    confirm_text = f"ยืนยัน {STATE.get('camp_name') or '-'}" if USE_CAMP_NAME_LABELS else "ยืนยัน"
    return (
        f"⚠️ รอยืนยันราคาช่าง\n"
        f"ค่าย: {STATE.get('camp_name') or '-'}\n"
        f"ราคาที่จะประกาศ: {no_price_reason}\n\n"
        f"ถ้าถูกต้อง ให้พิมพ์คำว่า:\n"
        f"{confirm_text}\n\n"
        f"ระบบจะยังไม่บันทึก/ประกาศราคานี้ จนกว่าจะพิมพ์ {confirm_text}\n"
        f"ใช้เพื่อกันกดผิดและกันคืนบิลจากการออกผลผิด"
    )


def confirm_pending_price() -> str:
    """ยืนยันและบันทึกราคาช่างพิเศษที่รออยู่"""
    pending = STATE.get("pending_price")
    if not isinstance(pending, dict):
        return "ยังไม่มีราคาช่างที่รอยืนยัน"

    if STATE.get("round_id") is None:
        clear_pending_price()
        return "ยังไม่มีรอบ กรุณาเปิดรอบก่อน"

    if STATE.get("opened"):
        return "ยังไม่สามารถยืนยันราคาช่างได้ ต้องปิดรอบก่อน"

    if STATE.get("settled"):
        clear_pending_price()
        return "รอบนี้แจ้งผลแล้ว ไม่สามารถเปลี่ยนราคาช่างได้"

    if pending.get("type") != "no_price":
        clear_pending_price()
        return "รูปแบบราคาช่างที่รอยืนยันไม่ถูกต้อง กรุณาพิมพ์คำสั่งใหม่"

    no_price_reason = pending.get("reason")
    if no_price_reason not in ["ไม่ต่อย", "ไม่ตี"]:
        clear_pending_price()
        return "ราคาช่างที่รอยืนยันไม่ถูกต้อง กรุณาพิมพ์คำสั่งใหม่"

    STATE["base_min"] = None
    STATE["base_max"] = None
    STATE["price_mode"] = "no_price"
    STATE["no_price_reason"] = no_price_reason
    STATE["two_digit_start"] = None
    STATE["pending_result"] = None
    STATE["pending_result_at"] = None
    clear_pending_price()
    clear_pending_price()

    return (
        f"✅ บันทึกสถานะช่างไม่มีราคาแล้ว\n"
        f"ราคาช่าง: {no_price_reason}\n\n"
        f"กติกาคิดผล:\n"
        f"- แผลอิงราคาช่าง เช่น ชล500 / ชถ500 = จาวคืนทุนทั้งคู่\n"
        f"- แผลตัวเลข เช่น 330-370ล500 = คิดผลตามตัวเลขปกติ\n"
        f"- แผลที่ต่อท้าย ชตย = ได้เล่นเฉพาะกรณีช่างไม่มีราคา\n\n"
        f"แจ้งผลต้องพิมพ์ 2 ครั้ง เช่น:\n"
        f"แจ้งผล 380\n"
        f"แจ้งผล 380"
    )


def parse_result_command(text):
    m = re.match(r"^(แจ้งผล|ผล)\s+(\d+)$", text.strip())
    if not m:
        return None
    return int(m.group(2))


def parse_special_result_command(text):
    """
    คำสั่งแจ้งผลแบบคืนทุนทุกคน
    - แจ้งผล จาวทุกแผล
    - แจ้งผล บั้งไฟหาย
    """
    clean = re.sub(r"\s+", " ", text.strip())
    m = re.match(r"^(แจ้งผล|ผล)\s+(จาวทุกแผล|บั้งไฟหาย)$", clean)
    if not m:
        return None
    return m.group(2)


def parse_rollback_result_command(text):
    """
    คำสั่งย้อนผล กรณีแอดมินแจ้งผลผิด
    ใช้คู่กับ multi-base ได้ เช่น:
    - ย้อนผล ฐาน1
    - ยืนยันย้อนผล ฐาน1
    - ยกเลิกย้อนผล ฐาน1
    หลัง extract_base_scoped_command แล้ว text จะเหลือแค่คำสั่งหลัก
    """
    clean = re.sub(r"\s+", "", (text or "").strip()).lower()
    if clean in {"ย้อนผล", "undoresult", "rollbackresult"}:
        return "request"
    if clean in {"ยืนยันย้อนผล", "ยืนยันย้อน", "confirmrollback"}:
        return "confirm"
    if clean in {"ยกเลิกย้อนผล", "ยกเลิกย้อน", "cancelrollback"}:
        return "cancel"
    return None


def parse_offer(text):
    """
    ตัวอย่างที่รับได้

    แบบอิงราคาช่างแอดมิน:
    ชล500 / ล500 / ไล่500
    ชถ500 / ถ500 / ย500 / ยั่ง500 / ถอย500 / ช่างรับ500 / รับช่าง500 / ช่างถอย500
    +5ชล500 / -5ถ500  = ขยับทั้งช่วงราคา เช่น 330-360 -> 335-365

    แบบขยับเฉพาะเลขหน้า/เลขหลังของราคาช่าง:
    ก+5ล100 / เกิบ+5ล100 = ขยับเลขหน้า เช่น 330-360 -> 335-360
    ม+5ล100 / หมวก+5ล100 = ขยับเลขหลัง เช่น 330-360 -> 330-365
    กม+5ล100 / กม-5ถ100 = ขยับทั้งช่วงราคา เช่น 330-360 -> 335-365 หรือ 325-355
    ม-5ถ100 / ก-5ถ100 ก็ใช้ได้
    ก+5ม-10ล100 / เกิบ-5หมวก+10ย100 = ปรับเลขหน้าและเลขหลังคนละค่าในแผลเดียว
    - ถ้าเลขหน้า = เลขหลัง เช่น 315-315 ให้ถือเป็นราคาแผลเดียว 315
    - ถ้าเกิบ/เลขหน้า มากกว่าหมวก/เลขหลัง ให้ตีจาวและคืนยอดหลังสรุปผล
    - เกิบ/ก ต้องอยู่หน้าหมวก/ม เท่านั้น

    แบบเล่นพิเศษไม่มีจาวในช่วงราคา:
    ช่างไม่ชนะ100 / ช่างบ่ชนะ100 / ช่างบ้ชนะ100
      = ผู้โพสต์ชนะเมื่อผลไม่เกินเลขหลังของราคาช่าง, อีกฝั่งชนะเมื่อผลเกินเลขหลัง
    ช่างแพ้100
      = ผู้โพสต์ชนะเฉพาะเมื่อผลต่ำกว่าเลขหน้าของราคาช่าง, อีกฝั่งชนะเมื่อผลตั้งแต่เลขหน้าขึ้นไป

    แบบราคาเล่นเลข 2 ตัว ต้องรอแอดมินแจ้ง เริ่มต้น1/2/3 หลังราคาช่าง:
    30-70ล500 / 3-7ล500 / 4-7ล500 / 50-00ล500 / 60-10ล500
    ตัวอย่าง เริ่มต้น3: 30-70 = 330-370, 3-7 = 330-370, 50-00 = 350-400

    แบบราคาเล่นเฉพาะแบบช่วงเลข 3 ตัว:
    330-360ล500 / 330/360ล500 / ตัว330-360ล500 / ตัว330/360ล500
    330-360ชล500 / 330-360ชถ500
    330-360+5ล500 / 330/360-5ชถ500

    แบบราคาเล่นเฉพาะเลขเดียว 3 ตัว:
    400ชล500 / 400ชถ500 / 400+5ชล500 / 400-5ถ500

    แบบ ชตย = เล่นเฉพาะกรณีช่างไม่มีราคาเท่านั้น:
    330-360ล500 ชตย
    400ชล500 ชตย
    """

    clean = compact_play_command_text(text)
    alias_pattern = "|".join(re.escape(x) for x in ALL_PLAY_ALIASES)
    special_alias_pattern = "|".join(re.escape(x) for x in ALL_SPECIAL_PLAY_ALIASES)
    signed_offset_pattern = r"([+-]\d+)?"

    def offer_dict(*, plus, amount, raw_alias, maker_side, custom_price_min=None, custom_price_max=None,
                   is_custom_price=False, only_when_no_price=False, price_adjust_target=None,
                   price_adjust_min=None, price_adjust_max=None, is_two_digit_price=False,
                   two_digit_min_token=None, two_digit_max_token=None):
        return {
            "plus": plus,
            "amount": amount,
            "raw_alias": raw_alias,
            "maker_side": maker_side,
            "custom_price_min": custom_price_min,
            "custom_price_max": custom_price_max,
            "is_custom_price": is_custom_price,
            "only_when_no_price": only_when_no_price,
            "price_adjust_target": price_adjust_target,
            "price_adjust_min": price_adjust_min,
            "price_adjust_max": price_adjust_max,
            "is_two_digit_price": is_two_digit_price,
            "two_digit_min_token": two_digit_min_token,
            "two_digit_max_token": two_digit_max_token,
        }

    # แบบขยับเลขหน้าและเลขหลังคนละค่าในคำสั่งเดียว: ก+5ม-10ล100 / เกิบ-5หมวก+10ย100
    # เกิบ/ก ต้องอยู่หน้าหมวก/ม เท่านั้น
    m = re.match(rf"^({PRICE_BOUND_ADJUST_PREFIX_PATTERN})([+-]\d+)({PRICE_BOUND_ADJUST_PREFIX_PATTERN})([+-]\d+)({alias_pattern})(\d+)$", clean)
    if m:
        prefix_1, adjust_1, prefix_2, adjust_2, alias, amount = m.group(1), int(m.group(2)), m.group(3), int(m.group(4)), m.group(5), int(m.group(6))
        if PRICE_BOUND_ADJUST_PREFIXES.get(prefix_1) != "min" or PRICE_BOUND_ADJUST_PREFIXES.get(prefix_2) != "max":
            return None
        if amount <= 0:
            return None
        maker_side = normalize_side(alias)
        if not maker_side:
            return None
        return offer_dict(
            plus=0,
            amount=amount,
            raw_alias=alias,
            maker_side=maker_side,
            price_adjust_target="bounds",
            price_adjust_min=adjust_1,
            price_adjust_max=adjust_2,
        )

    # แบบขยับเลขหน้าและเลขหลังคนละค่า + คำสั่งพิเศษ: ก+5ม-10ช่างแพ้100
    m = re.match(rf"^({PRICE_BOUND_ADJUST_PREFIX_PATTERN})([+-]\d+)({PRICE_BOUND_ADJUST_PREFIX_PATTERN})([+-]\d+)({special_alias_pattern})(\d+)$", clean)
    if m:
        prefix_1, adjust_1, prefix_2, adjust_2, alias, amount = m.group(1), int(m.group(2)), m.group(3), int(m.group(4)), m.group(5), int(m.group(6))
        if PRICE_BOUND_ADJUST_PREFIXES.get(prefix_1) != "min" or PRICE_BOUND_ADJUST_PREFIXES.get(prefix_2) != "max":
            return None
        if amount <= 0:
            return None
        maker_side = normalize_side(alias)
        if not maker_side:
            return None
        return offer_dict(
            plus=0,
            amount=amount,
            raw_alias=alias,
            maker_side=maker_side,
            price_adjust_target="bounds",
            price_adjust_min=adjust_1,
            price_adjust_max=adjust_2,
        )

    # แบบขยับเลขหน้า/เลขหลัง/ทั้งช่วง: ก+5ล100 / เกิบ+5ล100 / ม+5ล100 / หมวก+5ล100 / กม+5ล100
    m = re.match(rf"^({PRICE_BOUND_ADJUST_PREFIX_PATTERN})([+-]\d+)({alias_pattern})(\d+)$", clean)
    if m:
        prefix = m.group(1)
        plus = int(m.group(2))
        alias = m.group(3)
        amount = int(m.group(4))

        if amount <= 0:
            return None

        maker_side = normalize_side(alias)
        if not maker_side:
            return None

        return offer_dict(
            plus=plus,
            amount=amount,
            raw_alias=alias,
            maker_side=maker_side,
            price_adjust_target=PRICE_BOUND_ADJUST_PREFIXES.get(prefix),
        )

    # แบบขยับเฉพาะเลขหน้า/เลขหลัง + คำสั่งพิเศษ: เกิบ+5ช่างแพ้100 / หมวก+5ช่างไม่ชนะ100
    m = re.match(rf"^({PRICE_BOUND_ADJUST_PREFIX_PATTERN})([+-]\d+)({special_alias_pattern})(\d+)$", clean)
    if m:
        prefix = m.group(1)
        plus = int(m.group(2))
        alias = m.group(3)
        amount = int(m.group(4))

        if amount <= 0:
            return None

        maker_side = normalize_side(alias)
        if not maker_side:
            return None

        return offer_dict(
            plus=plus,
            amount=amount,
            raw_alias=alias,
            maker_side=maker_side,
            price_adjust_target=PRICE_BOUND_ADJUST_PREFIXES.get(prefix),
        )

    # คำสั่งเล่นพิเศษ: ช่างไม่ชนะ100 / ช่างบ่ชนะ100 / ช่างบ้ชนะ100 / ช่างแพ้100
    m = re.match(rf"^({special_alias_pattern})(\d+)$", clean)
    if m:
        alias = m.group(1)
        amount = int(m.group(2))

        if amount <= 0:
            return None

        maker_side = normalize_side(alias)
        if not maker_side:
            return None

        return offer_dict(
            plus=0,
            amount=amount,
            raw_alias=alias,
            maker_side=maker_side,
        )

    # แบบราคาเล่นเลข 2 ตัว: 30-70ล500 / 3-7ล500 / 50-00ล500 / 60-10ล500
    # ยังไม่แปลงเป็นราคาเต็มตอนโพสต์ ต้องรอแอดมินแจ้ง เริ่มต้น1/2/3 หลังราคาช่าง
    m = re.match(rf"^(?:ตัว)?(\d{{1,2}})[-/](\d{{1,2}})({alias_pattern})(\d+)(ชตย)?$", clean)
    if m:
        min_token = m.group(1)
        max_token = m.group(2)
        alias = m.group(3)
        amount = int(m.group(4))
        only_when_no_price = bool(m.group(5))

        if amount <= 0:
            return None

        try:
            two_digit_token_to_offset(min_token)
            two_digit_token_to_offset(max_token)
        except Exception:
            return None

        maker_side = normalize_side(alias)
        if not maker_side:
            return None

        return offer_dict(
            plus=0,
            amount=amount,
            raw_alias=alias,
            maker_side=maker_side,
            is_custom_price=True,
            only_when_no_price=only_when_no_price,
            is_two_digit_price=True,
            two_digit_min_token=min_token,
            two_digit_max_token=max_token,
        )

    # แบบมีราคาเล่นเฉพาะเป็นช่วง: 330-360ล500 / 330/360ล500 / ตัว330-360ล500
    # ต่อท้าย ชตย ได้เฉพาะแผลราคาเลข เช่น 330-360ล500ชตย
    # บังคับราคาเป็นเลข 3 ตัวเท่านั้น และรองรับตัวคั่น - หรือ /
    m = re.match(rf"^(?:ตัว)?(\d{{3}})[-/](\d{{3}}){signed_offset_pattern}({alias_pattern})(\d+)(ชตย)?$", clean)
    if m:
        custom_min = int(m.group(1))
        custom_max = int(m.group(2))
        plus = int(m.group(3)) if m.group(3) else 0
        alias = m.group(4)
        amount = int(m.group(5))
        only_when_no_price = bool(m.group(6))

        if custom_min > custom_max:
            custom_min, custom_max = custom_max, custom_min

        # + / - หลังช่วงราคา ให้ขยับช่วงราคาทั้งชุด เช่น 330-360-5 = 325-355
        custom_min += plus
        custom_max += plus

        if amount <= 0:
            return None

        maker_side = normalize_side(alias)
        if not maker_side:
            return None

        return offer_dict(
            plus=plus,
            amount=amount,
            raw_alias=alias,
            maker_side=maker_side,
            custom_price_min=custom_min,
            custom_price_max=custom_max,
            is_custom_price=True,
            only_when_no_price=only_when_no_price,
        )

    # แบบราคาเล่นเฉพาะเลขเดียว 3 ตัว: 400ชล500 / 400+5ชล500 / 400-5ถ500
    # ระบบคิดเป็นช่วงเดียว เช่น 400-400; ผลเท่ากับ 400 = จาว, มากกว่า 400 = ชนะ, ต่ำกว่า 400 = แพ้
    m = re.match(rf"^(\d{{3}}){signed_offset_pattern}({alias_pattern})(\d+)(ชตย)?$", clean)
    if m:
        custom_price = int(m.group(1))
        plus = int(m.group(2)) if m.group(2) else 0
        alias = m.group(3)
        amount = int(m.group(4))
        only_when_no_price = bool(m.group(5))

        custom_price += plus

        if amount <= 0:
            return None

        maker_side = normalize_side(alias)
        if not maker_side:
            return None

        return offer_dict(
            plus=plus,
            amount=amount,
            raw_alias=alias,
            maker_side=maker_side,
            custom_price_min=custom_price,
            custom_price_max=custom_price,
            is_custom_price=True,
            only_when_no_price=only_when_no_price,
        )

    # แบบอิงราคาช่างแอดมิน: ชล500 / +5ชล500 / -5ถ500
    m = re.match(rf"^([+-]\d+)?({alias_pattern})(\d+)$", clean)
    if not m:
        return None

    plus = int(m.group(1)) if m.group(1) else 0
    alias = m.group(2)
    amount = int(m.group(3))

    if amount <= 0:
        return None

    maker_side = normalize_side(alias)
    if not maker_side:
        return None

    return offer_dict(
        plus=plus,
        amount=amount,
        raw_alias=alias,
        maker_side=maker_side,
    )

def parse_reset_order_command(text):
    """
    คำสั่งล้าง/รีเซ็ตออเดอร์
    - ล้างออเดอร์ / รีเซ็ตออเดอร์ / รีเซ็ต ID ออเดอร์ = ล้างรายการออเดอร์ทั้งหมด และเริ่มนับใหม่ที่ #1
    - ถ้าใส่เลข เช่น ตั้งเลขออเดอร์ 100 = ล้างรายการออเดอร์ทั้งหมด และเริ่มนับใหม่ที่เลขนั้น
    """
    raw = (text or "").strip()
    compact = re.sub(r"\s+", "", raw).lower()

    no_arg_commands = {
        "รีเซ็ตออเดอร์",
        "รีเซ็ตidออเดอร์",
        "รีเซ็ตไอดีออเดอร์",
        "รีเซ็ตเลขออเดอร์",
        "ล้างออเดอร์",
        "ล้างเลขออเดอร์",
        "ล้างออเดอร์ทั้งหมด",
        "ล้างบิล",
        "ล้างบิลทั้งหมด",
    }
    if compact in no_arg_commands:
        return 1

    m = re.match(r"^(?:รีเซ็ต\s*(?:id|ไอดี)?\s*ออเดอร์|รีเซ็ต\s*เลข\s*ออเดอร์|ล้าง\s*ออเดอร์|ล้าง\s*บิล|ตั้ง\s*เลข\s*ออเดอร์|ตั้ง\s*ออเดอร์)\s+(\d+)$", raw, re.IGNORECASE)
    if not m:
        return None

    next_no = int(m.group(1))
    if next_no <= 0:
        return None
    return next_no


def is_clear_round_backups_command(text: str) -> bool:
    """
    คำสั่งล้างไฟล์ backup รอบในโฟลเดอร์ round_backups
    ใช้สำหรับหลังบ้านเท่านั้น เพื่อเคลียร์ไฟล์สำรองรอบเก่าที่สะสมไว้
    """
    raw = (text or "").strip()
    compact = re.sub(r"\s+", "", raw).lower()

    return compact in {
        "ล้างround_backups",
        "เคลียร์round_backups",
        "clearround_backups",
        "ล้างroundbackup",
        "ล้างroundbackups",
        "ล้างbackupรอบ",
        "ล้างbackupsรอบ",
        "ล้างแบคอัพรอบ",
        "ล้างไฟล์backupรอบ",
        "ล้างไฟล์แบคอัพรอบ",
        "ล้างไฟล์round_backups",
    }

def parse_credit_command(text):
    """
    $+ 1 1000
    $- 1 1000
    """
    m = re.match(r"^\$(\+|-)\s+(\d+)\s+(\d+)$", text.strip())
    if not m:
        return None

    return {
        "op": m.group(1),
        "member_no": int(m.group(2)),
        "amount": int(m.group(3)),
    }


def parse_confirm_command(text):
    """
    รับคำสั่งยืนยันแผล / ติด
    - ต / ติด / ครับ / เค / จ้า / ติดจ้า / ตต / ตด / ตอด / ตอก / จ = ติดเต็มยอดที่เหลือของโพสต์
    - ต300 / ติด300 = ขอเล่นเฉพาะ 300 จากยอดโพสต์
    - 300ต / 300ติด = ขอเล่นเฉพาะ 300 จากยอดโพสต์เช่นกัน
    """
    clean = compact_play_command_text(text)
    clean = clean.replace(".", "")

    confirm_keywords = {
        "ต", "ติด", "ครับ", "เค", "จ้า", "ติดจ้า",
        "ตต", "ตด", "ตอด", "ตอก", "จ", "ติดครับ", "ติดด", "ติก",
        "ตอน","ตาม","แตก","ต้อง","ตัวเอง","ตืด","ตตต","ตื่น","ตัด",
    }

    if clean in confirm_keywords:
        return {"amount": None}

    # รองรับการติดไม่เต็มยอด เช่น ต300 / ติด300 / 300ต / 300ติด
    m = re.match(r"^(?:ติด|ต)(\d+)$", clean)
    if not m:
        m = re.match(r"^(\d+)(?:ติด|ต)$", clean)
    if not m:
        return None

    amount = int(m.group(1))
    if amount <= 0:
        return None

    return {"amount": amount}


def is_confirm_word(text):
    return parse_confirm_command(text) is not None


def is_result_like_command(text):
    """กันแอดมินพิมพ์แจ้งผลผิดรูปแบบแล้วบอทหลุดไปทำอย่างอื่น"""
    return re.match(r"^(แจ้งผล|ผล)(?:\s+|$)", text.strip()) is not None


# ======================================================
# Flex
# ======================================================

def matched_flex_for_user(match, viewer_id):
    maker = USERS.get(match["maker_id"], {})
    taker = USERS.get(match["taker_id"], {})

    viewer_side = get_user_side(match, viewer_id)

    maker_side = match.get("maker_side")
    taker_side = opposite_side(maker_side)
    play_text = format_match_play_text(match)
    price_min, price_max = get_match_price_range(match)
    price_label = "ราคาเล่น" if match.get("is_custom_price") else "ราคาช่าง"
    price_text = format_price_range_text(price_min, price_max)

    maker_name = maker.get("line_name") or maker.get("name") or "ผู้โพสต์"
    taker_name = taker.get("line_name") or taker.get("name") or "ผู้ติด"
    camp_name = match.get("camp_name") or (get_state_by_round_id(match.get("round_id")) or STATE).get("camp_name") or "-"

    is_viewer_maker = (viewer_id == match.get("maker_id"))

    is_win = viewer_side == "ชนะ"
    color_win  = "#16A34A"
    color_lose = "#EF4444"
    color_gray = "#6B7280"

    # มุมมองที่แสดงในหัว card
    if is_viewer_maker:
        viewer_role  = "ผู้โพสต์"
        viewer_side_label = f"(ทาย{maker_side})"
    else:
        viewer_role  = "ผู้ติด"
        viewer_side_label = f"(ทาย{taker_side})"

    def side_badge(side_text):
        """badge แสดงฝั่งทาย เช่น ทายแพ้ / ทายชนะ"""
        if side_text == "ชนะ":
            bg, color = "#DCFCE7", color_win
        else:
            bg, color = "#FEE2E2", color_lose
        return {
            "type": "box", "layout": "vertical",
            "backgroundColor": bg, "cornerRadius": "14px",
            "paddingStart": "10px", "paddingEnd": "10px",
            "paddingTop": "3px", "paddingBottom": "3px",
            "contents": [{"type": "text", "text": f"ทาย{side_text}",
                          "size": "xxs", "color": color, "weight": "bold"}],
        }

    def info_badge(text, bg, color):
        """badge ข้อมูลทั่วไป เช่น แผล / ราคา / เล่น"""
        return {
            "type": "box", "layout": "vertical",
            "backgroundColor": bg, "cornerRadius": "14px",
            "paddingStart": "10px", "paddingEnd": "10px",
            "paddingTop": "3px", "paddingBottom": "3px",
            "contents": [{"type": "text", "text": text, "size": "xxs",
                          "color": color, "weight": "bold", "wrap": True}],
        }

    def player_col(name, role_label, side_text, is_you, align_end=False):
        """คอลัมน์ผู้เล่น แสดงชื่อ บทบาท และฝั่งทาย"""
        align = "end" if align_end else "start"
        role_display = f"{role_label} (คุณ)" if is_you else role_label
        return {
            "type": "box", "layout": "vertical", "flex": 5,
            "paddingAll": "10px", "spacing": "sm",
            "contents": [
                {"type": "text", "text": role_display,
                 "size": "xxs", "color": "#9CA3AF", "align": align},
                {"type": "text", "text": name,
                 "size": "sm", "weight": "bold", "color": "#111111",
                 "wrap": True, "align": align},
                {
                    "type": "box", "layout": "vertical",
                    "alignItems": "flex-end" if align_end else "flex-start",
                    "contents": [side_badge(side_text)],
                },
            ],
        }

    status_msg = f"คุณทาย{viewer_side} {'ลุ้นผลได้เลย! 🎉' if is_win else 'สู้ๆ นะครับ! 💪'}"
    status_bg    = "#F0FDF4" if is_win else "#FFF1F2"
    status_color = color_win if is_win else color_lose
    status_icon  = "🎲"

    return {
        "type": "bubble",
        "size": "mega",
        # ── หัวการ์ด: มุมมอง + ชื่อ + ยอด ────────────────────────────
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#16A34A",
            "paddingAll": "0px",
            "contents": [
                # แถบบอกมุมมอง
                {
                    "type": "box", "layout": "horizontal",
                    "backgroundColor": "#15803D",
                    "paddingStart": "14px", "paddingEnd": "14px",
                    "paddingTop": "6px", "paddingBottom": "6px",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"มุมมอง{viewer_role} {viewer_side_label}",
                            "size": "xxs", "color": "#bbf7d0",
                            "align": "center",
                        }
                    ],
                },
                # แถวหลัก: ชื่อ + ยอด
                {
                    "type": "box", "layout": "horizontal",
                    "paddingStart": "14px", "paddingEnd": "14px",
                    "paddingTop": "12px", "paddingBottom": "12px",
                    "alignItems": "center",
                    "contents": [
                        {
                            "type": "box", "layout": "vertical", "flex": 1, "spacing": "none",
                            "contents": [
                                {"type": "text", "text": "✅ จับคู่สำเร็จ",
                                 "weight": "bold", "size": "md", "color": "#FFFFFF"},
                                {"type": "text", "text": f"Order #{match['order_no']}",
                                 "size": "xs", "color": "#bbf7d0"},
                            ]
                        },
                        {
                            "type": "box", "layout": "vertical", "alignItems": "flex-end",
                            "contents": [
                                {"type": "text", "text": f"{match['amount']:,}",
                                 "weight": "bold", "size": "xl", "color": "#FFFFFF"},
                                {"type": "text", "text": "บาท",
                                 "size": "xs", "color": "#bbf7d0"},
                            ]
                        },
                    ],
                },
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "14px",
            "spacing": "md",
            "contents": [
                # ── ข้อมูลค่าย + badge ──────────────────────────────────
                {
                    "type": "box", "layout": "vertical",
                    "backgroundColor": "#F8FAFC", "cornerRadius": "10px",
                    "paddingAll": "12px", "spacing": "sm",
                    "contents": [
                        {"type": "text", "text": f"⚔️  {camp_name}",
                         "weight": "bold", "size": "sm", "color": "#111111"},
                        {
                            "type": "box", "layout": "vertical",
                            "spacing": "xs",
                            "contents": [
                                {
                                    "type": "box", "layout": "horizontal",
                                    "spacing": "sm",
                                    "contents": [
                                        info_badge(f"แผล: {play_text}", "#EDE9FE", "#6D28D9"),
                                        info_badge(f"{price_label}: {price_text}", "#FEF3C7", "#B45309"),
                                        info_badge(f"เล่น {match['amount']:,}", "#D1FAE5", "#065F46"),
                                    ],
                                },
                            ],
                        },
                    ],
                },
                # ── ผู้เล่นทั้งคู่ ───────────────────────────────────────
                {
                    "type": "box", "layout": "horizontal",
                    "borderColor": "#E5E7EB", "borderWidth": "1px",
                    "cornerRadius": "10px",
                    "alignItems": "center",
                    "contents": [
                        player_col(
                            maker_name, "📌 ผู้โพสต์", maker_side,
                            is_you=is_viewer_maker, align_end=False
                        ),
                        {
                            "type": "box", "layout": "vertical",
                            "flex": 2, "paddingAll": "6px",
                            "alignItems": "center", "justifyContent": "center",
                            "contents": [
                                {"type": "text", "text": "VS",
                                 "size": "sm", "weight": "bold",
                                 "color": "#9CA3AF", "align": "center"},
                            ],
                        },
                        player_col(
                            taker_name, "🎯 ผู้ติด", taker_side,
                            is_you=not is_viewer_maker, align_end=True
                        ),
                    ],
                },
                # ── สถานะ ────────────────────────────────────────────────
                {
                    "type": "box", "layout": "horizontal",
                    "backgroundColor": status_bg, "cornerRadius": "8px",
                    "paddingAll": "10px", "spacing": "sm",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": status_icon, "size": "md", "flex": 0},
                        {
                            "type": "box", "layout": "vertical", "flex": 1,
                            "contents": [
                                {"type": "text", "text": status_msg,
                                 "size": "xs", "weight": "bold", "color": status_color},
                                {"type": "text", "text": now_text(),
                                 "size": "xxs", "color": "#9CA3AF"},
                            ]
                        },
                    ],
                },
                # ── ปุ่มยกเลิก ───────────────────────────────────────────
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "action": {
                        "type": "postback",
                        "label": "แตะเพื่อขอยกเลิก",
                        "data": f"action=request_cancel&match_id={match['match_id']}",
                        "displayText": "ขอยกเลิก",
                    },
                },
            ],
        },
    }

def backoffice_match_flex(match):
    return matched_flex_for_user(match, match["maker_id"])



def cancel_request_flex(match, requester_id):
    requester = USERS.get(requester_id, {})
    other_id = get_other_user_id(match, requester_id)
    other = USERS.get(other_id, {})
    play_text = format_match_play_text(match)
    amount = match.get("amount", 0)
    price_min, price_max = get_match_price_range(match)
    price_label = "ราคาเล่น" if match.get("is_custom_price") else "ราคาช่าง"

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F59E0B",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": "⚠️  คำขอยกเลิก",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#FFFFFF",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "18px",
            "contents": [
                {
                    "type": "text",
                    "text": f"Order #{match.get('order_no')}",
                    "align": "center",
                    "color": "#999999",
                    "size": "sm",
                },
                {
                    "type": "text",
                    "text": money_text(amount),
                    "align": "center",
                    "weight": "bold",
                    "size": "xxl",
                    "color": "#111111",
                },
                {
                    "type": "separator",
                    "margin": "md",
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": "ผู้ขอยกเลิก",
                            "size": "sm",
                            "color": "#999999",
                            "flex": 2,
                        },
                        {
                            "type": "text",
                            "text": f"🚀🚀 {requester.get('line_name') or requester.get('name') or 'User'} 🚀🚀",
                            "size": "sm",
                            "weight": "bold",
                            "align": "end",
                            "wrap": True,
                            "flex": 4,
                        },
                    ],
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "xs",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"แผล: {play_text}",
                            "size": "xs",
                            "color": "#666666",
                            "wrap": True,
                        },
                        {
                            "type": "text",
                            "text": f"ราคาที่ติดกัน: {amount:,}",
                            "size": "xs",
                            "color": "#666666",
                            "wrap": True,
                        },
                        {
                            "type": "text",
                            "text": f"คู่กรณี: {other.get('line_name') or other.get('name') or 'User'}",
                            "size": "xs",
                            "color": "#666666",
                            "wrap": True,
                        },
                    ],
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "sm",
                    "margin": "lg",
                    "contents": [
                        {
                            "type": "button",
                            "style": "primary",
                            "height": "sm",
                            "color": "#EF4444",
                            "action": {
                                "type": "postback",
                                "label": "ยืนยันยกเลิก",
                                "data": f"action=approve_cancel&match_id={match['match_id']}",
                                "displayText": "ยืนยันยกเลิก",
                            },
                            "flex": 1,
                        },
                        {
                            "type": "button",
                            "style": "secondary",
                            "height": "sm",
                            "action": {
                                "type": "postback",
                                "label": "ปฏิเสธ",
                                "data": f"action=reject_cancel&match_id={match['match_id']}",
                                "displayText": "ปฏิเสธคำขอยกเลิก",
                            },
                            "flex": 1,
                        },
                    ],
                },
            ],
        },
    }


def cancel_success_flex(match):
    amount = match.get("amount", 0)
    play_text = format_match_play_text(match)
    price_min, price_max = get_match_price_range(match)
    price_label = "ราคาเล่น" if match.get("is_custom_price") else "ราคาช่าง"

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#22C55E",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": "✓  ยกเลิกสำเร็จ",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#FFFFFF",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "18px",
            "contents": [
                {
                    "type": "text",
                    "text": f"Order #{match.get('order_no')}",
                    "align": "center",
                    "color": "#999999",
                    "size": "sm",
                },
                {
                    "type": "text",
                    "text": "ยกเลิกสำเร็จ",
                    "align": "center",
                    "weight": "bold",
                    "size": "xl",
                    "color": "#22C55E",
                },
                {
                    "type": "separator",
                    "margin": "md",
                },
                {
                    "type": "text",
                    "text": flex_match_detail_multiline(play_text, format_match_price_text_for_flex(match), price_label=price_label, amount=amount, extra_lines=["ยอดที่ถูก hold จะถูกคืนอัตโนมัติ"]),
                    "align": "center",
                    "color": "#B3B3B3",
                    "size": "xs",
                    "wrap": True,
                },
            ],
        },
    }


def cancel_reject_flex(match, rejecter_id):
    rejecter = USERS.get(rejecter_id, {})
    amount = match.get("amount", 0)
    play_text = format_match_play_text(match)
    price_min, price_max = get_match_price_range(match)
    price_label = "ราคาเล่น" if match.get("is_custom_price") else "ราคาช่าง"

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#6B7280",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": "✕  ปฏิเสธคำขอยกเลิก",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#FFFFFF",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "18px",
            "contents": [
                {
                    "type": "text",
                    "text": f"Order #{match.get('order_no')}",
                    "align": "center",
                    "color": "#999999",
                    "size": "sm",
                },
                {
                    "type": "text",
                    "text": "ปฏิเสธการยกเลิก",
                    "align": "center",
                    "weight": "bold",
                    "size": "xl",
                    "color": "#EF4444",
                },
                {
                    "type": "separator",
                    "margin": "md",
                },
                {
                    "type": "text",
                    "text": (
                        f"ผู้ปฏิเสธ: {rejecter.get('line_name') or rejecter.get('name') or 'User'}\n"
                        + flex_match_detail_multiline(
                            play_text,
                            format_match_price_text_for_flex(match),
                            price_label=price_label,
                            amount=amount,
                            extra_lines=["รายการนี้ยังมีผลตามเดิม"],
                        )
                    ),
                    "align": "center",
                    "color": "#666666",
                    "size": "xs",
                    "wrap": True,
                },
            ],
        },
    }

def balance_flex(user: dict):
    credit = user_credit_amount(user)
    active_amount = active_credit_amount_for_user((user or {}).get("user_id"))
    member_no = (user or {}).get("member_no", "-")

    def amount_row(label: str, amount: int, color: str = "#111827"):
        return {
            "type": "box",
            "layout": "horizontal",
            "margin": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": label,
                    "size": "sm",
                    "color": "#6B7280",
                    "flex": 3,
                },
                {
                    "type": "text",
                    "text": f"{money_text(amount)} บาท",
                    "size": "sm",
                    "weight": "bold",
                    "align": "end",
                    "wrap": True,
                    "flex": 4,
                    "color": color,
                },
            ],
        }

    id_row = {
        "type": "box",
        "layout": "horizontal",
        "margin": "md",
        "contents": [
            {
                "type": "text",
                "text": "ID",
                "size": "sm",
                "color": "#6B7280",
                "flex": 3,
            },
            {
                "type": "text",
                "text": str(member_no),
                "size": "sm",
                "weight": "bold",
                "align": "end",
                "wrap": True,
                "flex": 4,
                "color": "#111827",
            },
        ],
    }

    return {
        "type": "bubble",
        "size": "hecto",
        "header": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "12px",
            "backgroundColor": "#3B82F6",
            "contents": [
                {
                    "type": "text",
                    "text": "💰 ยอดเงินของคุณ",
                    "weight": "bold",
                    "size": "md",
                    "color": "#FFFFFF",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "12px",
            "spacing": "sm",
            "backgroundColor": "#FFFFFF",
            "contents": [
                {
                    "type": "text",
                    "text": money_text(credit),
                    "size": "xxl",
                    "weight": "bold",
                    "align": "center",
                    "color": "#3B82F6",
                    "margin": "sm",
                },
                {
                    "type": "text",
                    "text": "ยอดคงเหลือ",
                    "size": "sm",
                    "align": "center",
                    "color": "#3B82F6",
                    "margin": "sm",
                },
                {
                    "type": "separator",
                    "margin": "md",
                    "color": "#E5E7EB",
                },
                id_row,
                amount_row("กำลังใช้อยู่", active_amount, "#111827"),
            ],
        },
    }

def get_active_play_rows_for_user(user_id: str):
    """
    คืนรายการเล่นที่ยังรอผลของ user จากทุกฐาน/ทุกรอบที่ยังไม่แจ้งผล

    เหตุผลที่ไม่กรองด้วย STATE.get("round_id"):
    - ระบบ multi-base อาจเปิดฐาน 1 ค้างไว้ แล้วเปิดฐาน 2 ต่อ
    - STATE จะชี้ไปฐานล่าสุด ทำให้คำสั่ง "รายการ" ใน OA เห็นเฉพาะฐานล่าสุด
    - ลูกค้าควรเห็นบิล matched ของตัวเองทุกฐานที่ยังไม่ settled
    """
    rows = []

    for match in list(MATCHES.values()):
        if match.get("status") != "matched":
            continue
        if user_id not in [match.get("maker_id"), match.get("taker_id")]:
            continue

        match_round_id = match.get("round_id")
        round_state = get_state_by_round_id(match_round_id)

        # ถ้ารอบนั้นถูกแจ้งผลแล้ว ไม่ต้องแสดงใน "รายการ" อีก
        # ถ้าหา state ไม่เจอ แต่ match ยังเป็น matched ให้แสดงไว้ก่อน เพื่อกันข้อมูลหายจาก backup/restore บางจังหวะ
        if round_state and round_state.get("settled"):
            continue

        base_no = normalize_base_no(
            match.get("base_no")
            or (round_state or {}).get("base_no")
            or get_base_no_by_round_id(match_round_id)
            or "1"
        )
        camp_name = (
            match.get("camp_name")
            or (round_state or {}).get("camp_name")
            or "-"
        )

        other_id = get_other_user_id(match, user_id)
        user_side = get_user_side(match, user_id)
        user_play_text = format_user_play_text_for_match(match, user_id)

        rows.append({
            "order_no": match.get("order_no", "-"),
            "round_id": match_round_id,
            "base_no": base_no,
            "base_label": (f"ค่าย: {camp_name}" if USE_CAMP_NAME_LABELS else f"ฐาน{base_no}"),
            "camp_name": camp_name,
            "other_id": other_id,
            "other_name": user_display_name(other_id),
            "user_side": user_side,
            "play_text": user_play_text,
            "price_text": format_match_price_text_for_active_list(match),
            "price_label": match_price_label(match),
            "amount": int(match.get("amount", 0) or 0),
            "created_at": match.get("created_at") or "",
        })

    def sort_key(row):
        try:
            base_sort = int(row.get("base_no", 0) or 0)
        except Exception:
            base_sort = 0
        try:
            order_sort = int(row.get("order_no", 0) or 0)
        except Exception:
            order_sort = 0
        return (base_sort, order_sort)

    return sorted(rows, key=sort_key)


def active_plays_flex(user_id: str):
    user = USERS.get(user_id, {})
    rows = get_active_play_rows_for_user(user_id)
    total = sum(int(r.get("amount", 0) or 0) for r in rows)
    name = user.get("line_name") or user.get("name") or "User"

    row_contents = []
    for row in rows[:10]:
        row_contents.extend([
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "md",
                "contents": [
                    {
                        "type": "box",
                        "layout": "vertical",
                        "flex": 5,
                        "contents": [
                            {
                                "type": "text",
                                "text": f"#{row['order_no']} | {row.get('base_label', '-')} ⚖️ vs {row['other_name']}",
                                "size": "sm",
                                "weight": "bold",
                                "wrap": True,
                                "color": "#111111",
                            },
                            {
                                "type": "text",
                                "text": f"ค่าย: {row.get('camp_name') or '-'}",
                                "size": "xs",
                                "color": "#6B7280",
                                "wrap": True,
                                "margin": "xs",
                            },
                            {
                                "type": "text",
                                "text": flex_match_detail_inline(
                                    row['play_text'],
                                    row.get('price_text') or '',
                                    price_label=row.get('price_label') or 'ราคา',
                                    side_text=row['user_side'],
                                ),
                                "size": "xs",
                                "color": "#16A34A" if row["user_side"] == "ชนะ" else "#EF4444",
                                "wrap": True,
                                "margin": "xs",
                            },
                        ],
                    },
                    {
                        "type": "text",
                        "text": money_text(row["amount"]),
                        "size": "sm",
                        "weight": "bold",
                        "align": "end",
                        "color": "#F59E0B",
                        "flex": 2,
                    },
                ],
            },
            {"type": "separator", "margin": "md"},
        ])

    if not rows:
        row_contents.append({
            "type": "text",
            "text": "ยังไม่มีรายการเล่น",
            "size": "sm",
            "align": "center",
            "color": "#6B7280",
            "wrap": True,
            "margin": "lg",
        })
    elif len(rows) > 10:
        row_contents.append({
            "type": "text",
            "text": f"มีรายการเพิ่มเติมอีก {len(rows) - 10} รายการ",
            "size": "xs",
            "color": "#888888",
            "wrap": True,
        })

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F59E0B",
            "paddingAll": "14px",
            "contents": [
                {
                    "type": "text",
                    "text": "📋 รายการเล่น",
                    "weight": "bold",
                    "size": "md",
                    "color": "#FFFFFF",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "กำลังใช้อยู่",
                    "size": "sm",
                    "align": "center",
                    "color": "#999999",
                },
                {
                    "type": "text",
                    "text": f"{money_text(total)} บาท",
                    "size": "xxl",
                    "weight": "bold",
                    "align": "center",
                    "color": "#F59E0B",
                },
                {"type": "separator", "margin": "md"},
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": name, "size": "sm", "color": "#6B7280", "wrap": True, "flex": 4},
                        {"type": "text", "text": f"{len(rows)} รายการ", "size": "sm", "weight": "bold", "align": "end", "color": "#F59E0B", "flex": 2},
                    ],
                },
                *row_contents,
                {
                    "type": "text",
                    "text": "แสดงรายการที่จับคู่สำเร็จและยังไม่แจ้งผลจากทุกค่าย",
                    "size": "xs",
                    "align": "center",
                    "color": "#B3B3B3",
                    "wrap": True,
                    "margin": "md",
                },
            ],
        },
    }


def get_cancelled_chty_rows_for_user(matches: list, user_id: str):
    """
    เตรียมข้อมูลรายการแผล ชตย ที่ถูกยกเลิกอัตโนมัติให้แสดงแบบเดียวกับ Flex จับอยู่
    ใช้มุมมองของผู้รับ Flex เพื่อให้แผล/ฝั่งทายตรงกับของคนนั้น ไม่ใช่ฝั่งผู้โพสต์เสมอ
    """
    rows = []

    for match in matches or []:
        if user_id not in [match.get("maker_id"), match.get("taker_id")]:
            continue

        other_id = get_other_user_id(match, user_id)
        user_side = get_user_side(match, user_id)
        user_play_text = format_play_text(user_side, match.get("plus", 0), match.get("price_adjust_target"), match.get("price_adjust_min"), match.get("price_adjust_max"))
        if match.get("only_when_no_price"):
            user_play_text += " ชตย"

        rows.append({
            "order_no": match.get("order_no", "-"),
            "other_id": other_id,
            "other_name": user_display_name(other_id),
            "user_side": user_side,
            "play_text": user_play_text,
            "price_text": format_match_price_text(match),
            "amount": int(match.get("amount", 0) or 0),
        })

    def sort_key(row):
        try:
            return int(row.get("order_no", 0))
        except Exception:
            return 0

    return sorted(rows, key=sort_key)


def chty_auto_cancel_summary_flex(user_id: str, matches: list, reason: str = "ราคาช่างกลับมาตีราคา"):
    """
    Flex รวมรายการยกเลิกแผล ชตย อัตโนมัติ
    แก้จากเดิมที่ส่ง Flex แยกทีละ Order ให้รวมเป็นรายการเรียงลงมาเหมือน Flex จับอยู่
    """
    user = USERS.get(user_id, {})
    rows = get_cancelled_chty_rows_for_user(matches, user_id)
    total = sum(int(r.get("amount", 0) or 0) for r in rows)
    name = user.get("line_name") or user.get("name") or "User"

    row_contents = []
    for row in rows[:10]:
        row_contents.extend([
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "md",
                "contents": [
                    {
                        "type": "box",
                        "layout": "vertical",
                        "flex": 5,
                        "contents": [
                            {
                                "type": "text",
                                "text": f"#{row['order_no']} | {row.get('base_label', '-')} ⚖️ vs {row['other_name']}",
                                "size": "sm",
                                "weight": "bold",
                                "wrap": True,
                                "color": "#111111",
                            },
                            {
                                "type": "text",
                                "text": f"ค่าย: {row.get('camp_name') or '-'}",
                                "size": "xs",
                                "color": "#6B7280",
                                "wrap": True,
                                "margin": "xs",
                            },
                            {
                                "type": "text",
                                "text": flex_match_detail_inline(row['play_text'], row.get('price_text') or '', side_text=row['user_side']),
                                "size": "xs",
                                "color": "#16A34A" if row["user_side"] == "ชนะ" else "#EF4444",
                                "wrap": True,
                                "margin": "xs",
                            },
                        ],
                    },
                    {
                        "type": "text",
                        "text": money_text(row["amount"]),
                        "size": "sm",
                        "weight": "bold",
                        "align": "end",
                        "color": "#F59E0B",
                        "flex": 2,
                    },
                ],
            },
            {"type": "separator", "margin": "md"},
        ])

    if not rows:
        row_contents.append({
            "type": "text",
            "text": "ไม่พบรายการแผล ชตย ที่ต้องยกเลิก",
            "size": "sm",
            "align": "center",
            "color": "#6B7280",
            "wrap": True,
            "margin": "lg",
        })
    elif len(rows) > 10:
        row_contents.append({
            "type": "text",
            "text": f"มีรายการเพิ่มเติมอีก {len(rows) - 10} รายการ",
            "size": "xs",
            "color": "#888888",
            "wrap": True,
        })

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F59E0B",
            "paddingAll": "14px",
            "contents": [
                {
                    "type": "text",
                    "text": "⚠️ ยกเลิกแผล ชตย",
                    "weight": "bold",
                    "size": "md",
                    "color": "#FFFFFF",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": reason or "ราคาช่างกลับมาตีราคา",
                    "size": "sm",
                    "align": "center",
                    "color": "#999999",
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": f"{money_text(total)} บาท",
                    "size": "xxl",
                    "weight": "bold",
                    "align": "center",
                    "color": "#F59E0B",
                },
                {"type": "separator", "margin": "md"},
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": name, "size": "sm", "color": "#6B7280", "wrap": True, "flex": 4},
                        {"type": "text", "text": f"{len(rows)} รายการ", "size": "sm", "weight": "bold", "align": "end", "color": "#F59E0B", "flex": 2},
                    ],
                },
                *row_contents,
                {
                    "type": "text",
                    "text": "ยอดที่ถูก hold จากแผล ชตย จะถูกคืนอัตโนมัติ",
                    "size": "xs",
                    "align": "center",
                    "color": "#B3B3B3",
                    "wrap": True,
                    "margin": "md",
                },
            ],
        },
    }


def result_summary_flex(user_id: str, rows: list, net: int):
    user = USERS.get(user_id, {})
    camp_name = STATE.get("camp_name") or "-"
    result_value = STATE.get("result")
    price_text = current_price_text()

    header_color = "#22C55E" if net >= 0 else "#EF4444"
    total_color = "#16A34A" if net >= 0 else "#EF4444"
    total_prefix = "+" if net > 0 else ""
    header_emoji = "🎉" if net > 0 else ("😥" if net < 0 else "💎")

    row_contents = []

    for row in rows[:10]:
        other = USERS.get(row["other_id"], {})
        delta = row["delta"]
        status = row["status"]

        if status == "ชนะ":
            emoji = "✅"
            color = "#16A34A"
            delta_text = f"+{money_text(abs(delta))}"
        elif status == "แพ้":
            emoji = "❌"
            color = "#EF4444"
            delta_text = f"-{money_text(abs(delta))}"
        else:
            emoji = "➖"
            color = "#6B7280"
            delta_text = "0.00"

        row_price_text = row.get('price_text') or format_price_range_text(row.get('price_min'), row.get('price_max'))
        detail_text = f"คุณทาย: {row['user_side']}"
        if row_price_text and not is_waiting_two_digit_start_price_text(row_price_text):
            detail_text += f" | ราคา: {row_price_text}"

        row_contents.extend([
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "md",
                "contents": [
                    {
                        "type": "box",
                        "layout": "vertical",
                        "flex": 4,
                        "contents": [
                            {
                                "type": "text",
                                "text": f"#{row['order_no']} {emoji} {status} vs {other.get('line_name') or other.get('name') or 'User'}",
                                "size": "sm",
                                "weight": "bold",
                                "wrap": True,
                                "color": "#111111",
                            },
                            {
                                "type": "text",
                                "text": detail_text,
                                "size": "xs",
                                "color": "#777777",
                                "wrap": True,
                                "margin": "xs",
                            },
                        ],
                    },
                    {
                        "type": "text",
                        "text": delta_text,
                        "size": "sm",
                        "weight": "bold",
                        "align": "end",
                        "color": color,
                        "flex": 2,
                    },
                ],
            },
            {"type": "separator", "margin": "md"},
        ])

    if len(rows) > 10:
        row_contents.append({
            "type": "text",
            "text": f"มีรายการเพิ่มเติมอีก {len(rows) - 10} รายการ",
            "size": "xs",
            "color": "#888888",
            "wrap": True,
        })

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": header_color,
            "contents": [
                {
                    "type": "text",
                    "text": f"{header_emoji} ผลรอบ \"{camp_name}\"",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#FFFFFF",
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "สรุปรายการของคุณ",
                    "size": "sm",
                    "color": "#999999",
                    "align": "center",
                },
                {
                    "type": "text",
                    "text": f"{total_prefix}{money_text(net)} บาท",
                    "size": "xxl",
                    "weight": "bold",
                    "align": "center",
                    "color": total_color,
                },
                {
                    "type": "text",
                    "text": f"ผลรอบ: {result_value} | ราคาช่าง: {price_text}",
                    "size": "sm",
                    "color": "#666666",
                    "align": "center",
                },
                {"type": "separator", "margin": "lg"},
                *row_contents,
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": "คงเหลือ",
                            "size": "md",
                            "weight": "bold",
                            "color": "#111111",
                        },
                        {
                            "type": "text",
                            "text": f"{money_text(user.get('credit', 0))} บาท",
                            "size": "md",
                            "weight": "bold",
                            "align": "end",
                            "color": "#111111",
                        },
                    ],
                },
            ],
        },
    }


# ======================================================
# Core logic
# ======================================================

def handle_credit_adjust(event, cmd):
    user_id = event.source.user_id

    if not can_use_backoffice_command(event, user_id):
        return "คำสั่งนี้ใช้ได้เฉพาะหลังบ้านหรือแอดมิน"

    target = find_user_by_member_no(cmd["member_no"])
    if not target:
        return (
            f"ไม่พบสมาชิก ID {cmd['member_no']}\n"
            f"ให้ลูกค้าพิมพ์ เช็คยอด ก่อน เพื่อให้ระบบสร้าง ID"
        )

    old_balance = target["credit"]

    if cmd["op"] == "+":
        target["credit"] += cmd["amount"]
        action_text = "บวก"
        sign = "+"
    else:
        if target["credit"] < cmd["amount"]:
            return (
                f"ลบไม่สำเร็จ\n"
                f"สมาชิก ID {target['member_no']}\n"
                f"ยอดปัจจุบัน: {target['credit']:,}\n"
                f"ยอดที่ต้องการลบ: {cmd['amount']:,}\n"
                f"ยอดไม่พอ"
            )

        target["credit"] -= cmd["amount"]
        action_text = "ลบ"
        sign = "-"

    new_balance = target["credit"]
    save_user_db()

    return (
        f"✅ ปรับเครดิตสำเร็จ\n\n"
        f"สมาชิก: {target.get('line_name') or target.get('name')}\n"
        f"ID: {target['member_no']}\n"
        f"รายการ: {action_text} {cmd['amount']:,}\n"
        f"ยอดเดิม: {old_balance:,}\n"
        f"ยอดใหม่: {new_balance:,}\n\n"
        f"คำสั่ง: ${sign} {target['member_no']} {cmd['amount']}"
    )



def clear_pending_round_clear():
    """ล้างสถานะรอยืนยันคำสั่ง CR"""
    STATE["pending_clear"] = None
    STATE["pending_clear_at"] = None
    STATE["pending_clear_ts"] = None


def has_pending_round_clear() -> bool:
    pending = STATE.get("pending_clear")
    if not isinstance(pending, dict):
        return False

    pending_ts = STATE.get("pending_clear_ts")
    try:
        pending_ts = float(pending_ts or 0)
    except Exception:
        pending_ts = 0

    if pending_ts and time.time() - pending_ts > CLEAR_CONFIRM_TTL_SECONDS:
        clear_pending_round_clear()
        return False

    return True


def get_round_clear_preview(round_id: str):
    """นับรายการที่จะได้รับผลกระทบถ้าใช้ CR"""
    preview = {
        "refunded_matches": 0,
        "refunded_credit_total": 0,
        "cancelled_posts": 0,
        "cancelled_pending": 0,
        "cancelled_open_matches": 0,
    }

    for match in list(MATCHES.values()):
        if match.get("round_id") != round_id:
            continue

        status = match.get("status")
        if status == "matched":
            amount = int(match.get("amount", 0) or 0)
            preview["refunded_matches"] += 1
            # บิล matched เคยหักเครดิตทั้ง maker และ taker จึงต้องคืนให้ทั้ง 2 ฝั่ง
            preview["refunded_credit_total"] += amount * 2
        elif status in {"open", "pending"}:
            preview["cancelled_open_matches"] += 1

    for post in list(POSTS.values()):
        if post.get("round_id") != round_id:
            continue

        if post.get("status") in ["open", "closed"]:
            preview["cancelled_posts"] += 1

        for taker in post.get("takers", []):
            if is_waiting_status(taker.get("status")):
                preview["cancelled_pending"] += 1

    return preview


def request_clear_round_confirm(clear_by: str = "-", chat_id: str = None):
    """
    ขั้นตอนที่ 1 ของ CR: ยังไม่เคลียร์จริง จนกว่าแอดมินจะพิมพ์ ยืนยัน
    """
    if STATE.get("round_id") is None:
        clear_pending_round_clear()
        return "ยังไม่มีรอบให้เคลียร์"

    if STATE.get("settled"):
        clear_pending_round_clear()
        return "รอบนี้แจ้งผลแล้ว ไม่สามารถใช้ CR เคลียร์ย้อนหลังได้"

    current_round_id = STATE.get("round_id")
    camp_name = STATE.get("camp_name") or "-"
    round_chat_id = STATE.get("chat_id") or chat_id or "-"
    preview = get_round_clear_preview(current_round_id)

    STATE["pending_clear"] = {
        "round_id": current_round_id,
        "camp_name": camp_name,
        "chat_id": round_chat_id,
        "requested_by": clear_by or "-",
    }
    STATE["pending_clear_at"] = now_text()
    STATE["pending_clear_ts"] = time.time()

    confirm_text = f"ยืนยัน {camp_name}" if USE_CAMP_NAME_LABELS else "ยืนยัน"
    return (
        "⚠️ ยืนยันการเคลียร์รอบ\n\n"
        "จะเคลียร์รอบนี้ ใช่หรือไม่?\n\n"
        f"ค่าย: {camp_name}\n"
        f"ห้องรอบ: {round_chat_id}\n\n"
        f"บิลที่จะคืนเครดิต: {preview['refunded_matches']:,} รายการ\n"
        f"เครดิตที่จะคืนรวม: {preview['refunded_credit_total']:,} เครดิต\n"
        f"โพสต์ที่จะยกเลิก: {preview['cancelled_posts']:,} รายการ\n"
        f"รายการรอติดที่จะยกเลิก: {preview['cancelled_pending']:,} รายการ\n\n"
        "ถ้าใช่ ให้พิมพ์คำว่า:\n"
        f"{confirm_text}\n\n"
        f"คำยืนยันมีอายุ {CLEAR_CONFIRM_TTL_SECONDS} วินาที\n"
        "ถ้าไม่ใช่ ไม่ต้องพิมพ์ยืนยัน ระบบจะยังไม่เคลียร์รอบ"
    )


def confirm_pending_round_clear(clear_by: str = "-", chat_id: str = None):
    """ขั้นตอนที่ 2 ของ CR: พิมพ์ ยืนยัน แล้วจึงเคลียร์รอบจริง"""
    pending = STATE.get("pending_clear")
    if not isinstance(pending, dict):
        return "ยังไม่มีคำสั่ง CR ที่รอยืนยัน"

    pending_ts = STATE.get("pending_clear_ts")
    try:
        pending_ts = float(pending_ts or 0)
    except Exception:
        pending_ts = 0

    if pending_ts and time.time() - pending_ts > CLEAR_CONFIRM_TTL_SECONDS:
        clear_pending_round_clear()
        return "คำขอเคลียร์รอบหมดอายุแล้ว กรุณาพิมพ์ CR ใหม่อีกครั้ง"

    if STATE.get("round_id") != pending.get("round_id"):
        clear_pending_round_clear()
        return "รอบมีการเปลี่ยนแปลงแล้ว กรุณาพิมพ์ CR ใหม่อีกครั้ง"

    pending_chat_id = pending.get("chat_id")
    if pending_chat_id and chat_id and pending_chat_id != chat_id:
        return cross_room_block_text("ยืนยันเคลียร์รอบ")

    return clear_current_round_and_refund(clear_by or pending.get("requested_by") or "-")


def clear_current_round_and_refund(clear_by: str = "-"):
    """
    CR = เคลียร์รอบปัจจุบันที่ยังไม่แจ้งผล
    - คืนเครดิตของบิลที่จับคู่สำเร็จแล้วในรอบนี้ทั้งหมด
    - ยกเลิกโพสต์/รายการรอติดของรอบนี้ เพื่อกันการยืนยันย้อนหลัง
    - ล้าง STATE รอบปัจจุบัน เพื่อให้เปิดรอบใหม่ได้ทันที
    - ไม่รีเซ็ตเลขออเดอร์ และไม่ยุ่งกับกำไร/สลิป/ข้อมูลสมาชิก
    """
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบให้เคลียร์"

    if STATE.get("settled"):
        return "รอบนี้แจ้งผลแล้ว ไม่สามารถใช้ CR เคลียร์ย้อนหลังได้"

    old_round_id = STATE.get("round_id")
    old_camp_name = STATE.get("camp_name") or "-"
    old_chat_id = STATE.get("chat_id") or "-"
    cleared_at = now_text()
    reason = f"CR เคลียร์รอบโดย {clear_by or '-'}"

    refunded_matches = 0
    refunded_credit_total = 0
    cancelled_posts = 0
    cancelled_pending = 0
    cancelled_open_matches = 0

    for match in list(MATCHES.values()):
        if match.get("round_id") != old_round_id:
            continue

        status = match.get("status")
        if status == "matched":
            amount = int(match.get("amount", 0) or 0)
            maker = USERS.get(match.get("maker_id"))
            taker = USERS.get(match.get("taker_id"))

            if maker:
                maker["credit"] = int(maker.get("credit", 0) or 0) + amount
                refunded_credit_total += amount
            if taker:
                taker["credit"] = int(taker.get("credit", 0) or 0) + amount
                refunded_credit_total += amount

            match["status"] = "cancelled"
            match["cancelled_at"] = cleared_at
            match["cancel_reason"] = reason
            match["winning_side"] = "จาว"
            match["result"] = "CR"
            match["commission"] = 0
            match["winner_id"] = None
            refunded_matches += 1

        elif status in {"open", "pending"}:
            match["status"] = "cancelled"
            match["cancelled_at"] = cleared_at
            match["cancel_reason"] = reason
            cancelled_open_matches += 1

    for post in list(POSTS.values()):
        if post.get("round_id") != old_round_id:
            continue

        if post.get("status") in ["open", "closed"]:
            post["status"] = "cancelled"
            post["cancelled_at"] = cleared_at
            post["cancel_reason"] = reason
            cancelled_posts += 1

        for taker in post.get("takers", []):
            if is_waiting_status(taker.get("status")):
                taker["status"] = "cancelled"
                taker["cancelled_at"] = cleared_at
                taker["cancel_reason"] = reason
                cancelled_pending += 1

    # ล้างรอบปัจจุบัน เพื่อให้เปิดรอบใหม่ได้ทันที
    STATE["opened"] = False
    STATE["camp_name"] = None
    STATE["round_id"] = None
    STATE["chat_id"] = None
    STATE["base_min"] = None
    STATE["base_max"] = None
    STATE["price_mode"] = None
    STATE["no_price_reason"] = None
    STATE["two_digit_start"] = None
    STATE["closed_at"] = None
    STATE["continued_at"] = None
    STATE["continue_count"] = 0
    STATE["result"] = None
    STATE["settled"] = False
    STATE["pending_result"] = None
    STATE["pending_result_at"] = None
    clear_pending_price()
    clear_pending_round_clear()

    save_user_db()

    return (
        "✅ CR เคลียร์รอบเรียบร้อย\n\n"
        f"ค่ายที่เคลียร์: {old_camp_name}\n"
        f"ห้องรอบเดิม: {old_chat_id}\n\n"
        f"คืนบิลแล้ว: {refunded_matches:,} รายการ\n"
        f"คืนเครดิตรวม: {refunded_credit_total:,} เครดิต\n"
        f"ยกเลิกโพสต์เดิม: {cancelled_posts:,} รายการ\n"
        f"ยกเลิกรายการรอติด: {cancelled_pending:,} รายการ\n"
        f"ยกเลิกรายการค้างอื่น: {cancelled_open_matches:,} รายการ\n\n"
        "สถานะปัจจุบัน: ไม่มีรอบเปิดอยู่\n"
        "สามารถเปิดรอบใหม่ได้ทันที"
    )



def _safe_flex_text(value, default="-"):
    """ตัด/แปลงข้อความให้ปลอดภัยสำหรับ LINE Flex"""
    text = str(value if value is not None else default)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text or default


def _post_price_text_for_cancel(post: dict, state_snapshot: dict = None) -> str:
    """ราคาเล่นของโพสต์ ณ รอบที่ถูกเปลี่ยนค่าย ใช้แสดงใน Flex แจ้งยกเลิก"""
    if not isinstance(post, dict):
        return "-"

    custom_min = post.get("custom_price_min")
    custom_max = post.get("custom_price_max")
    if custom_min is not None and custom_max is not None:
        return format_price_range_text(custom_min, custom_max)

    st = get_state_by_round_id(post.get("round_id")) or state_snapshot or STATE
    return state_price_text(st)


def _change_camp_play_item(
    *,
    play_text: str = "-",
    price_text: str = "-",
    amount: int = None,
    refund_amount: int = 0,
    viewer_side: str = None,
    order_no: str = None,
    note: str = None,
    cancelled_at: str = None,
):
    """เก็บรายการเล่น 1 แผล เพื่อรวมหลายแผลเป็น Flex เดียวต่อ 1 คน"""
    amount_value = 0
    amount_text = None
    if amount is not None:
        try:
            amount_value = int(amount)
            amount_text = f"{amount_value:,}"
        except Exception:
            amount_text = str(amount)

    try:
        refund_value = int(refund_amount or 0)
    except Exception:
        refund_value = 0

    return {
        "play_text": play_text or "-",
        "price_text": price_text or "-",
        "amount": amount_value,
        "amount_text": amount_text,
        "refund_amount": refund_value,
        "viewer_side": viewer_side,
        "order_no": order_no,
        "note": note,
        "cancelled_at": cancelled_at,
    }


def _change_camp_info_row(label: str, value: str, value_weight: str = "regular"):
    return {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "contents": [
            {
                "type": "text",
                "text": _safe_flex_text(label),
                "size": "sm",
                "color": "#6B7280",
                "flex": 3,
                "wrap": True,
            },
            {
                "type": "text",
                "text": _safe_flex_text(value),
                "size": "sm",
                "color": "#111827",
                "weight": value_weight,
                "flex": 5,
                "wrap": True,
            },
        ],
    }


def _change_camp_play_box(index: int, item: dict):
    """แสดงรายการเล่นเรียงลงมาใน Flex"""
    rows = []
    order_no = item.get("order_no")
    title = f"{index}. {item.get('play_text') or '-'}"
    if order_no:
        title += f"  |  Order #{order_no}"

    rows.append({
        "type": "text",
        "text": _safe_flex_text(title),
        "size": "sm",
        "weight": "bold",
        "color": "#111827",
        "wrap": True,
    })

    sub_parts = []
    if item.get("price_text") and not is_waiting_two_digit_start_price_text(item.get("price_text")):
        sub_parts.append(f"ราคา: {item.get('price_text')}")
    if item.get("amount_text"):
        sub_parts.append(f"ยอด: {item.get('amount_text')}")
    if item.get("viewer_side"):
        sub_parts.append(f"ฝั่ง: {item.get('viewer_side')}")
    if sub_parts:
        rows.append({
            "type": "text",
            "text": _safe_flex_text(" | ".join(sub_parts)),
            "size": "xs",
            "color": "#6B7280",
            "wrap": True,
        })

    if item.get("note"):
        rows.append({
            "type": "text",
            "text": _safe_flex_text(item.get("note")),
            "size": "xs",
            "color": "#9CA3AF",
            "wrap": True,
        })

    return {
        "type": "box",
        "layout": "vertical",
        "spacing": "xs",
        "paddingAll": "10px",
        "backgroundColor": "#F9FAFB",
        "cornerRadius": "md",
        "contents": rows,
    }


def camp_change_play_list_flex(
    *,
    old_camp_name: str,
    new_camp_name: str = None,
    base_text: str = None,
    play_items: list = None,
    cancelled_at: str = None,
):
    """
    Flex แจ้งเปลี่ยนค่ายแบบหน้ารายการเล่น:
    - หัวข้อ: ระบบเปลี่ยนค่าย - คืนเครดิต
    - แสดงชื่อค่าย และรายการเล่นเรียงลงมา ถ้ามีหลายแผลจะอยู่ใน Flex เดียว
    """
    play_items = [x for x in (play_items or []) if isinstance(x, dict)]
    shown_items = play_items[:10]
    hidden_count = max(len(play_items) - len(shown_items), 0)
    refund_total = sum(int(x.get("refund_amount", 0) or 0) for x in play_items)

    def item_amount_value(item: dict) -> int:
        try:
            return int(item.get("amount", 0) or 0)
        except Exception:
            try:
                return int(str(item.get("amount_text", "0")).replace(",", ""))
            except Exception:
                return 0

    row_contents = []
    for index, item in enumerate(shown_items, start=1):
        order_no = item.get("order_no")
        order_text = f"#{order_no}" if order_no else f"รายการที่ {index}"
        refund_amount = int(item.get("refund_amount", 0) or 0)
        status_text = "คืนเครดิตแล้ว" if refund_amount > 0 else "ยังไม่ได้คิดเงิน"
        side_text = item.get("viewer_side") or "-"
        side_color = "#16A34A" if side_text == "ชนะ" else ("#EF4444" if side_text == "แพ้" else "#6B7280")

        row_contents.extend([
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "md",
                "contents": [
                    {
                        "type": "box",
                        "layout": "vertical",
                        "flex": 5,
                        "contents": [
                            {
                                "type": "text",
                                "text": _safe_flex_text(f"{order_text} | {base_text or '-'} ⚖️ {status_text}"),
                                "size": "sm",
                                "weight": "bold",
                                "wrap": True,
                                "color": "#111111",
                            },
                            {
                                "type": "text",
                                "text": _safe_flex_text(flex_match_detail_inline(item.get('play_text') or '-', item.get('price_text') or '', side_text=side_text)),
                                "size": "xs",
                                "color": side_color,
                                "wrap": True,
                                "margin": "xs",
                            },
                            {
                                "type": "text",
                                "text": _safe_flex_text(item.get("note") or status_text),
                                "size": "xs",
                                "color": "#EF4444" if refund_amount > 0 else "#9CA3AF",
                                "wrap": True,
                                "margin": "xs",
                            },
                        ],
                    },
                    {
                        "type": "text",
                        "text": money_text(item_amount_value(item)),
                        "size": "sm",
                        "weight": "bold",
                        "align": "end",
                        "color": "#F59E0B",
                        "flex": 2,
                    },
                ],
            },
            {"type": "separator", "margin": "md"},
        ])

    if not play_items:
        row_contents.append({
            "type": "text",
            "text": "ไม่มีรายการเล่นในค่ายนี้",
            "size": "sm",
            "align": "center",
            "color": "#6B7280",
            "wrap": True,
            "margin": "lg",
        })
    elif hidden_count:
        row_contents.append({
            "type": "text",
            "text": f"มีรายการเพิ่มเติมอีก {hidden_count} รายการ",
            "size": "xs",
            "color": "#888888",
            "wrap": True,
        })

    camp_line = f"ชื่อค่าย: {old_camp_name or '-'}"
    if new_camp_name:
        camp_line += f" → {new_camp_name}"

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F59E0B",
            "paddingAll": "14px",
            "contents": [
                {
                    "type": "text",
                    "text": "📋 ระบบเปลี่ยนค่าย - คืนเครดิต",
                    "weight": "bold",
                    "size": "md",
                    "color": "#FFFFFF",
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "คืนเครดิตแล้ว",
                    "size": "sm",
                    "align": "center",
                    "color": "#999999",
                },
                {
                    "type": "text",
                    "text": f"{money_text(refund_total)} บาท",
                    "size": "xxl",
                    "weight": "bold",
                    "align": "center",
                    "color": "#F59E0B",
                },
                {"type": "separator", "margin": "md"},
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": _safe_flex_text(camp_line), "size": "sm", "color": "#6B7280", "wrap": True, "flex": 4},
                        {"type": "text", "text": f"{len(play_items)} รายการ", "size": "sm", "weight": "bold", "align": "end", "color": "#F59E0B", "flex": 2},
                    ],
                },
                *row_contents,
                {
                    "type": "text",
                    "text": _safe_flex_text(f"เปลี่ยนค่ายเมื่อ {cancelled_at or '-'} หากยอดตกหล่นแจ้งหลังบ้านได้เลยครับ"),
                    "size": "xs",
                    "align": "center",
                    "color": "#B3B3B3",
                    "wrap": True,
                    "margin": "md",
                },
            ],
        },
    }


def _queue_change_camp_cancel_notification(notifications: dict, user_id: str, item: dict):
    if not user_id or not item:
        return
    notifications.setdefault(user_id, []).append(item)

def change_camp_and_refund_wrong_round(new_camp_name: str, chat_id: str = None):
    """
    ใช้เมื่อแอดมินเปิดค่ายผิด:
    - คืนเครดิตของบิลที่จับคู่สำเร็จแล้วในรอบเดิมทั้งหมด
    - ยกเลิกโพสต์/รายการรอติดของรอบเดิม เพื่อกันการยืนยันย้อนหลัง
    - ส่ง Flex แจ้งคนที่เล่นแบบรวมต่อคน มีชื่อค่ายและรายการเล่นเรียงลงมา
    - สร้าง round_id ใหม่ และเปิดค่ายใหม่ทันที
    """
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบให้เปลี่ยนค่าย กรุณาเปิดรอบก่อน"

    if STATE.get("settled"):
        return "รอบนี้แจ้งผลแล้ว ไม่สามารถเปลี่ยนค่ายย้อนหลังได้"

    new_camp_name = (new_camp_name or "").strip()
    if not new_camp_name:
        return "กรุณาระบุชื่อค่าย เช่น เปลี่ยนค่าย แอ๊ดเทวดา"

    old_round_id = STATE.get("round_id")
    old_camp_name = STATE.get("camp_name") or "-"
    old_state_snapshot = dict(STATE)
    old_base_text = base_label(old_state_snapshot)
    changed_at = now_text()
    reason = f"เปลี่ยนค่ายจาก {old_camp_name} เป็น {new_camp_name}"

    refunded_matches = 0
    refunded_credit_total = 0
    cancelled_posts = 0
    cancelled_pending = 0
    notification_count = 0
    notifications = {}
    matched_post_ids = set()

    for match in list(MATCHES.values()):
        if match.get("round_id") != old_round_id:
            continue

        if match.get("status") == "matched":
            amount = int(match.get("amount", 0) or 0)
            maker = USERS.get(match.get("maker_id"))
            taker = USERS.get(match.get("taker_id"))

            play_text = format_match_play_text(match)
            price_text = format_match_price_text(match)
            order_no = match.get("order_no")
            matched_post_ids.add(match.get("post_id"))

            for notify_user_id in [match.get("maker_id"), match.get("taker_id")]:
                _queue_change_camp_cancel_notification(
                    notifications,
                    notify_user_id,
                    _change_camp_play_item(
                        play_text=play_text,
                        price_text=price_text,
                        amount=amount,
                        refund_amount=amount,
                        viewer_side=get_user_side(match, notify_user_id),
                        order_no=order_no,
                        note="บิลนี้ถูกยกเลิกจากการเปลี่ยนค่าย และคืนเครดิตแล้ว",
                        cancelled_at=changed_at,
                    ),
                )

            if maker:
                maker["credit"] = int(maker.get("credit", 0) or 0) + amount
                refunded_credit_total += amount
            if taker:
                taker["credit"] = int(taker.get("credit", 0) or 0) + amount
                refunded_credit_total += amount

            match["status"] = "cancelled"
            match["cancelled_at"] = changed_at
            match["cancel_reason"] = reason
            match["winning_side"] = "จาว"
            match["result"] = "เปลี่ยนค่าย"
            match["commission"] = 0
            match["winner_id"] = None
            refunded_matches += 1

    for post in list(POSTS.values()):
        if post.get("round_id") != old_round_id:
            continue

        post_id = post.get("post_id")
        post_was_open = post.get("status") in ["open", "closed"]
        play_text = format_post_play_text(post)
        price_text = _post_price_text_for_cancel(post, old_state_snapshot)
        post_amount = int(post.get("amount", 0) or 0)
        waiting_takers = [
            taker for taker in post.get("takers", [])
            if is_waiting_status(taker.get("status"))
        ]

        # แจ้งรายการรอติด/รอยืนยันให้ทั้งคนโพสต์และคนมาติดรู้ว่าแผลนี้ยกเลิกแล้ว
        for taker in waiting_takers:
            take_amount = int(taker.get("amount", post_amount) or post_amount)
            for notify_user_id, viewer_side in [
                (post.get("maker_id"), post.get("maker_side")),
                (taker.get("taker_id"), opposite_side(post.get("maker_side"))),
            ]:
                _queue_change_camp_cancel_notification(
                    notifications,
                    notify_user_id,
                    _change_camp_play_item(
                        play_text=play_text,
                        price_text=price_text,
                        amount=take_amount,
                        refund_amount=0,
                        viewer_side=viewer_side,
                        order_no=None,
                        note="รายการรอติดนี้ถูกยกเลิกจากการเปลี่ยนค่าย ระบบยังไม่ได้คิดเงิน",
                        cancelled_at=changed_at,
                    ),
                )

            taker["status"] = "cancelled"
            taker["cancelled_at"] = changed_at
            taker["cancel_reason"] = reason
            cancelled_pending += 1

        # ถ้าเป็นโพสต์แผลที่ยังไม่มีบิลสมบูรณ์/ไม่มีรายการรอติด ให้แจ้งคนโพสต์ 1 ครั้ง
        # ส่วนโพสต์ที่มีบิล matched แล้ว คนโพสต์จะได้รับ Flex ตาม Order ที่ถูกคืนแล้วด้านบน
        if post_was_open and post_id not in matched_post_ids and not waiting_takers:
            _queue_change_camp_cancel_notification(
                notifications,
                post.get("maker_id"),
                _change_camp_play_item(
                    play_text=play_text,
                    price_text=price_text,
                    amount=post_amount,
                    refund_amount=0,
                    viewer_side=post.get("maker_side"),
                    order_no=None,
                    note="โพสต์แผลนี้ถูกยกเลิกจากการเปลี่ยนค่าย ระบบยังไม่ได้คิดเงิน",
                    cancelled_at=changed_at,
                ),
            )

        if post_was_open:
            post["status"] = "cancelled"
            post["cancelled_at"] = changed_at
            post["cancel_reason"] = reason
            cancelled_posts += 1

    # เปิดรอบใหม่ด้วยชื่อค่ายที่ถูกต้อง และรีเซ็ตราคา/ผลทั้งหมด
    STATE["opened"] = True
    STATE["camp_name"] = new_camp_name
    STATE["round_id"] = str(uuid.uuid4())
    STATE["chat_id"] = chat_id or STATE.get("chat_id")
    STATE["base_min"] = None
    STATE["base_max"] = None
    STATE["price_mode"] = None
    STATE["no_price_reason"] = None
    STATE["two_digit_start"] = None
    STATE["closed_at"] = None
    STATE["result"] = None
    STATE["settled"] = False
    STATE["pending_result"] = None
    STATE["pending_result_at"] = None
    clear_pending_price()
    clear_pending_round_clear()

    save_user_db()
    save_round_backup_db(reason="camp_changed")

    # ส่ง Flex หลังอัปเดตข้อมูลเรียบร้อยแล้ว ใช้ async เพื่อลดอาการหน่วงใน webhook
    # รวมหลายแผลของผู้เล่นคนเดียวไว้ใน Flex เดียว
    for notify_user_id, play_items in notifications.items():
        flex_dict = camp_change_play_list_flex(
            old_camp_name=old_camp_name,
            new_camp_name=new_camp_name,
            base_text=old_base_text,
            play_items=play_items,
            cancelled_at=changed_at,
        )
        push_flex_async(notify_user_id, f"เปลี่ยนค่าย {old_camp_name}", flex_dict)
        notification_count += 1

    return (
        f"✅ เปลี่ยนค่ายเรียบร้อย\n\n"
        f"ค่ายเดิม: {old_camp_name}\n"
        f"ค่ายใหม่: {new_camp_name}\n\n"
        f"คืนบิลแล้ว: {refunded_matches} รายการ\n"
        f"คืนเครดิตรวม: {refunded_credit_total:,}\n"
        f"ยกเลิกโพสต์เดิม: {cancelled_posts} รายการ\n"
        f"ยกเลิกรายการรอติด: {cancelled_pending} รายการ\n"
        f"ส่ง Flex แจ้งผู้เล่น: {notification_count} ข้อความ\n\n"
        f"{new_camp_name}\n\n"
        f"ช่าง ⛔️\n\n"
        f"🚀🚀🚀🚀🚀"
    )


def create_post(event, offer):
    """
    สำเร็จ = return None เพื่อให้บอทเงียบ
    error = return text เพื่อแจ้งปัญหา
    """
    user_id = event.source.user_id
    user = ensure_user_from_event(event)

    if not is_front_chat(event):
        return None

    if not is_current_round_chat(event):
        return "รายการนี้ต้องเล่นในกลุ่มหน้าบ้านที่เปิดรอบเท่านั้น"

    if not STATE["opened"]:
        return "ยังไม่เปิดรอบ จึงไม่รับโพสต์"

    if STATE.get("settled"):
        return "รอบนี้แจ้งผลแล้ว ไม่รับโพสต์เพิ่ม"

    if user_credit_amount(user) < offer["amount"]:
        play_text = format_offer_play_text(offer)
        return insufficient_credit_warning(
            user,
            offer["amount"],
            play_text=play_text,
            is_chty=bool(offer.get("only_when_no_price")),
        )

    post_id = get_message_id(event)
    if not post_id:
        return "ระบบไม่พบ message id ของโพสต์นี้"

    POSTS[post_id] = {
        "post_id": post_id,
        "round_id": STATE["round_id"],
        "base_no": STATE.get("base_no"),
        "camp_name": STATE.get("camp_name"),
        "chat_id": STATE.get("chat_id"),
        "maker_id": user_id,
        "plus": offer["plus"],
        "amount": offer["amount"],
        "remaining_amount": offer["amount"],
        "maker_side": offer["maker_side"],
        "raw_alias": offer["raw_alias"],
        "price_adjust_target": offer.get("price_adjust_target"),
        "price_adjust_min": offer.get("price_adjust_min"),
        "price_adjust_max": offer.get("price_adjust_max"),
        "custom_price_min": offer.get("custom_price_min"),
        "custom_price_max": offer.get("custom_price_max"),
        "is_two_digit_price": offer.get("is_two_digit_price", False),
        "two_digit_min_token": offer.get("two_digit_min_token"),
        "two_digit_max_token": offer.get("two_digit_max_token"),
        "is_custom_price": offer.get("is_custom_price", False),
        "only_when_no_price": offer.get("only_when_no_price", False),
        "takers": [],
        "status": "open",
        "created_at": now_text(),
    }

    # สำรองทันทีหลังรับโพสต์แผลสำเร็จ
    # กันเคสบอทรีสตาร์ท/อัปเดตโค้ดระหว่างที่ยังไม่ทันจับคู่
    save_round_backup_db(reason="post_created")

    # เงียบเมื่อรับโพสต์สำเร็จ
    return None


def find_pending_taker_by_reply_message_id(reply_message_id):
    for post in list(POSTS.values()):
        for taker in post.get("takers", []):
            if (
                taker.get("taker_reply_message_id") == reply_message_id
                and taker.get("status") == "pending"
            ):
                return post, taker
    return None, None


def find_counter_pending_by_reply_message_id(reply_message_id):
    """
    หาเคสเจ้าของโพสต์เสนอแก้ยอดกลับไปแล้ว เช่น
    A โพสต์ ชล1000 -> B ติด -> A reply B ว่า ต100 -> B reply ข้อความ ต100 ว่า ติด
    """
    if not reply_message_id:
        return None, None

    for post in list(POSTS.values()):
        for taker in post.get("takers", []):
            if (
                taker.get("counter_message_id") == reply_message_id
                and taker.get("status") == "counter_pending"
            ):
                return post, taker
    return None, None


def is_waiting_status(status: str) -> bool:
    """สถานะที่ยังเป็นรายการรอ ไม่ได้หักเครดิต และยังไม่เป็นบิลสมบูรณ์"""
    return status in {"pending", "counter_pending"}


def is_reply_to_known_play_message(reply_message_id):
    """
    ใช้เฉพาะโหมดเงียบ:
    ตรวจว่าข้อความที่ถูก reply เป็นข้อความใน flow แผลเล่นหรือไม่
    - โพสต์แผลต้นทาง เช่น ชล500 / ชถ200
    - ข้อความ ต/ติด ของคนที่มาติด ซึ่งรอเจ้าของโพสต์ยืนยัน
    """
    if not reply_message_id:
        return False

    if reply_message_id in POSTS:
        post = POSTS.get(reply_message_id) or {}
        return post.get("round_id") == STATE.get("round_id")

    pending_post, pending_taker = find_pending_taker_by_reply_message_id(reply_message_id)
    if pending_post and pending_taker:
        return pending_post.get("round_id") == STATE.get("round_id")

    counter_post, counter_taker = find_counter_pending_by_reply_message_id(reply_message_id)
    if counter_post and counter_taker:
        return counter_post.get("round_id") == STATE.get("round_id")

    return False


def invalid_play_reply_warning(event, text: str):
    """
    ลูกค้าตอบกลับโพสต์แผลด้วยคำที่ไม่ใช่คีย์ เช่น เอา / ตาม / ok
    ให้แจ้งวิธีใช้ แต่ห้ามสร้าง pending และห้ามล็อกอะไรไว้
    เพื่อให้กลับไป reply ข้อความเดิมด้วย ต/ติด แล้วเล่นต่อได้ทันที
    """
    if not (QUIET_GROUP_MODE and QUIET_WARN_INVALID_REPLY_TO_PLAY):
        return None

    if not is_front_chat(event):
        return None

    quoted_message_id = get_reply_message_id(event)
    if not quoted_message_id:
        return None

    # ถ้าเป็นคีย์ที่ถูกต้อง หรือเป็นโพสต์แผลจริง ให้ปล่อยให้ flow หลักจัดการ
    if parse_confirm_command(text) or parse_offer(text):
        return None

    # ถ้าเป็นคำสั่งแอดมิน/คำสั่งรอบ ให้ปล่อยให้ flow คำสั่งจัดการ
    user_id = getattr(event.source, "user_id", None)
    if is_round_control_command_text(text, user_id=user_id):
        return None

    # กรณีลูกค้า reply โพสต์แผลต้นทาง แต่คำไม่ใช่ ต/ติด
    post = POSTS.get(quoted_message_id)
    if post and post.get("round_id") == STATE.get("round_id"):
        if user_id == post.get("maker_id"):
            return (
                "❌ ยังไม่ใช่การยืนยันจับคู่ค่ะ\n\n"
                "เจ้าของโพสต์ต้องตอบกลับข้อความ ต/ติด ของคนที่มาติดเท่านั้น\n"
                "แล้วพิมพ์: ต หรือ ติด"
            )

        return (
            "❌ คำนี้ไม่ใช่คีย์ติดรายการค่ะ\n\n"
            "ให้ตอบกลับโพสต์แผลเดิม แล้วพิมพ์อย่างใดอย่างหนึ่ง:\n"
            "ต / ติด\n\n"
            "ถ้าจะติดบางส่วน ให้พิมพ์เช่น:\n"
            "ต300 / ติด300 / 300ต / 300ติด"
        )

    # กรณีเจ้าของโพสต์ reply ข้อความ ต/ติด ของลูกค้า แต่พิมพ์คำยืนยันผิด
    pending_post, pending_taker = find_pending_taker_by_reply_message_id(quoted_message_id)
    if pending_post and pending_taker and pending_post.get("round_id") == STATE.get("round_id"):
        if user_id == pending_post.get("maker_id"):
            return (
                "❌ คำยืนยันจับคู่ไม่ถูกต้องค่ะ\n\n"
                "ให้ตอบกลับข้อความ ต/ติด ของลูกค้า แล้วพิมพ์:\n"
                "ต หรือ ติด หรือ ต100 เพื่อเสนอเล่นบางส่วน"
            )

        return (
            "รายการนี้รอเจ้าของโพสต์ยืนยันค่ะ\n"
            "ถ้าต้องการติดรายการ ให้ตอบกลับโพสต์แผลต้นทางแล้วพิมพ์: ต หรือ ติด"
        )

    # กรณีคนที่มาติดต้อง reply ข้อความที่เจ้าของโพสต์เสนอแก้ยอด เช่น ต100
    counter_post, counter_taker = find_counter_pending_by_reply_message_id(quoted_message_id)
    if counter_post and counter_taker and counter_post.get("round_id") == STATE.get("round_id"):
        if user_id == counter_taker.get("taker_id"):
            return (
                "❌ คำยืนยันยอดที่เสนอไม่ถูกต้องค่ะ\n\n"
                "ให้ตอบกลับข้อความยอดที่เจ้าของโพสต์เสนอ แล้วพิมพ์:\n"
                "ต หรือ ติด"
            )

        return "รายการนี้รอคนที่มาติดยืนยันยอดที่เจ้าของโพสต์เสนอค่ะ"

    return None


def handle_confirm(event, quoted_message_id, requested_amount=None):
    """
    Flow:

    1. นาย A โพสต์ ชล500
    2. นาย B Reply ข้อความของนาย A แล้วพิมพ์ ติด
       -> ระบบบันทึกเป็น pending และบอทเงียบ
    3. นาย A Reply ข้อความ "ติด" ของนาย B แล้วพิมพ์ ติด
       -> ระบบสร้างแผลสมบูรณ์ และส่ง Flex หาทั้งคู่ + หลังบ้าน
       -> บอทไม่ตอบในกลุ่ม
    """
    user_id = event.source.user_id
    user = ensure_user_from_event(event)
    current_msg_id = get_message_id(event)

    if not is_front_chat(event):
        return None

    if not is_current_round_chat(event):
        return "รายการนี้ต้องเล่นในกลุ่มหน้าบ้านที่เปิดรอบเท่านั้น"

    if not STATE["opened"]:
        return "ปิดอยู่ หรือยังไม่เปิดรอบ จึงไม่สามารถติดได้"

    if STATE.get("settled"):
        return "รอบนี้แจ้งผลแล้ว ไม่สามารถติดเพิ่มได้"

    if not quoted_message_id:
        # กลุ่มลูกค้าเยอะ: ถ้าพิมพ์ ต/ติด เฉย ๆ โดยไม่ได้ reply รายการ ให้บอทเงียบ
        if QUIET_GROUP_MODE:
            return None
        return "ต้องตอบกลับข้อความที่ต้องการติดเท่านั้น"

    # B ยืนยันยอดที่ A เสนอแก้กลับมา เช่น
    # A โพสต์ ชล1000 -> B ติด -> A reply ว่า ต100 -> B reply ข้อความ ต100 ว่า ติด
    counter_post, counter_taker = find_counter_pending_by_reply_message_id(quoted_message_id)
    if counter_post and counter_taker:
        if counter_post.get("round_id") != STATE.get("round_id"):
            return "รายการนี้ไม่ใช่รอบปัจจุบัน"

        if user_id != counter_taker.get("taker_id"):
            return "รายการนี้รอคนที่มาติดยืนยันยอดที่เจ้าของโพสต์เสนอ"

        counter_amount = int(counter_taker.get("counter_amount", 0) or 0)
        post_amount = int(counter_post.get("amount", 0) or 0)

        if counter_amount <= 0:
            counter_taker["status"] = "rejected"
            return "จับคู่ไม่สำเร็จ ยอดเสนอเล่นไม่ถูกต้อง"

        if post_amount > 0 and counter_amount > post_amount:
            counter_taker["status"] = "rejected"
            return (
                f"จับคู่ไม่สำเร็จ\n"
                f"ยอดที่เสนอเล่น: {counter_amount:,}\n"
                f"ยอดที่โพสต์ไว้: {post_amount:,}"
            )

        if user_credit_amount(user) < counter_amount:
            counter_taker["status"] = "rejected_credit"
            counter_taker["rejected_at"] = now_text()
            counter_taker["reject_reason"] = "taker_insufficient_credit_after_counter_confirm"
            return insufficient_credit_warning(
                user,
                counter_amount,
                play_text=format_post_play_text(counter_post),
                is_chty=bool(counter_post.get("only_when_no_price")),
                action="ยืนยันยอด",
            )

        # เปลี่ยนยอดที่ใช้จับคู่เป็นยอดที่เจ้าของโพสต์เสนอ และค่อยสร้างบิลหลัง B ยืนยัน
        counter_taker["amount"] = counter_amount
        counter_taker["status"] = "pending"
        counter_taker["counter_confirmed_by"] = user_id
        counter_taker["counter_confirmed_message_id"] = current_msg_id
        counter_taker["updated_at"] = now_text()

        return create_match_from_pending(counter_post, counter_taker)

    # A ยืนยันโดยตอบกลับข้อความ "ติด" / "ต300" / "300ต" ของ B เท่านั้น
    # ถ้าคนอื่น เช่น นาย C ไปตอบข้อความติดของนาย B ให้เตือนทันที กันติดผิดรายการ
    pending_post, pending_taker = find_pending_taker_by_reply_message_id(quoted_message_id)
    if pending_post and pending_taker:
        if pending_post.get("round_id") != STATE.get("round_id"):
            return "รายการนี้ไม่ใช่รอบปัจจุบัน"

        if user_id != pending_post["maker_id"]:
            return "ตอบผิดกรุณาเช็คก่อนติด เพื่อผลประโยชน์ของคุณพี่นะคะ"

        # เจ้าของโพสต์ reply ข้อความ ติด ของ B ด้วย ต100 / ติด100
        # ให้ถือว่าเป็นการเสนอแก้ยอด ไม่ใช่การจับคู่ทันที ต้องรอ B reply ยืนยันอีกครั้ง
        if requested_amount is not None:
            counter_amount = int(requested_amount)
            post_amount = int(pending_post.get("amount", 0) or 0)

            if counter_amount <= 0:
                return "ยอดที่เสนอเล่นต้องมากกว่า 0"

            if post_amount > 0 and counter_amount > post_amount:
                return (
                    f"เสนอเล่นไม่สำเร็จ\n"
                    f"ยอดที่เสนอ: {counter_amount:,}\n"
                    f"ยอดที่โพสต์ไว้: {post_amount:,}"
                )

            maker = USERS.get(pending_post.get("maker_id"), {})
            if user_credit_amount(maker) < counter_amount:
                return insufficient_credit_warning(
                    maker,
                    counter_amount,
                    play_text=format_post_play_text(pending_post),
                    is_chty=bool(pending_post.get("only_when_no_price")),
                    action="เสนอแก้ยอด",
                )

            pending_taker["status"] = "counter_pending"
            pending_taker["counter_amount"] = counter_amount
            pending_taker["counter_message_id"] = current_msg_id
            pending_taker["counter_by"] = user_id
            pending_taker["updated_at"] = now_text()
            pending_taker["last_counter_text"] = getattr(event.message, "text", "")
            save_round_backup_db(reason="counter_pending_created")

            # เงียบในกลุ่ม รอ B reply ข้อความ ต100 / ติด100 ของ A แล้วพิมพ์ ติด
            return None

        return create_match_from_pending(pending_post, pending_taker)

    # B ตอบกลับโพสต์ต้นทางของ A
    post = POSTS.get(quoted_message_id)
    if not post:
        # ถ้า quote ข้อความทั่วไปในกลุ่มแล้วพิมพ์ ต/ติด ให้ถือว่าไม่ใช่แผลเล่นและเงียบ
        if QUIET_GROUP_MODE and QUIET_IGNORE_WRONG_REPLY:
            return None
        return "ไม่พบโพสต์ต้นทาง หรือโพสต์นี้ไม่ใช่รายการที่ระบบรับไว้"

    if post.get("round_id") != STATE.get("round_id"):
        return "โพสต์นี้ไม่ใช่รอบปัจจุบัน"

    # โพสต์ 1 โพสต์ใช้เป็น "ราคาแม่แบบ" ได้เรื่อย ๆ
    # หลังจับคู่สำเร็จแล้ว ห้ามปิดโพสต์อัตโนมัติ เพราะ C/D/E ต้องมาติดโพสต์เดิมต่อได้
    post_status = post.get("status", "open")
    if post_status not in ["open", "closed"]:
        return "โพสต์นี้ไม่เปิดรับแล้ว"

    if post_status == "closed":
        # รองรับโพสต์เก่าที่เคยถูกโค้ดเดิมปิดเพราะ remaining_amount = 0
        post["status"] = "open"

    if user_id == post["maker_id"]:
        # เจ้าของโพสต์ต้องไป reply ข้อความ ติด ของคนที่มาติดเท่านั้น
        # ถ้า reply โพสต์ตัวเองผิดตำแหน่ง ให้เงียบเพื่อลดข้อความรกกลุ่ม
        if QUIET_GROUP_MODE and QUIET_IGNORE_WRONG_REPLY:
            return None
        return "เจ้าของโพสต์ต้องยืนยันโดยตอบกลับข้อความ ติด ของคนที่มาติด"

    post_amount = int(post.get("amount", 0) or 0)
    if post_amount <= 0:
        return "โพสต์นี้ยอดไม่ถูกต้อง ไม่สามารถติดได้"

    take_amount = int(requested_amount) if requested_amount is not None else post_amount

    if take_amount <= 0:
        return "ยอดติดต้องมากกว่า 0"

    if take_amount > post_amount:
        return (
            f"ติดไม่สำเร็จ\n"
            f"ยอดที่ต้องการติด: {take_amount:,}\n"
            f"ยอดที่โพสต์ไว้: {post_amount:,}\n"
            f"ให้พิมพ์ใหม่ เช่น ต{post_amount}"
        )

    # เช็กเครดิตคนมาติดทันทีตั้งแต่ข้อความ ต/ติด
    # เดิม: ถ้าพิมพ์ "ติด" เฉย ๆ ระบบจะสร้าง pending ก่อน แล้วค่อยไปเช็กตอนเจ้าของโพสต์ยืนยัน
    # ใหม่: เครดิตต้องพอเท่ากับยอดที่จะติดก่อน จึงค่อยสร้าง pending เพื่อกันคนเครดิต 0 มาค้างรายการ
    current_credit = user_credit_amount(user)
    if current_credit < take_amount:
        return insufficient_credit_warning(
            user,
            take_amount,
            play_text=format_post_play_text(post),
            is_chty=bool(post.get("only_when_no_price")),
            action="ติดรายการ",
        )

    # ถ้าคนเดิมติดโพสต์เดิมซ้ำก่อนเจ้าของโพสต์ยืนยัน
    # ห้ามสร้าง pending ซ้ำ แต่ต้องอัปเดต message id เป็นข้อความล่าสุด
    # เพื่อให้เจ้าของโพสต์ Reply ข้อความ "ติด" ล่าสุดแล้วยืนยันได้จริง
    existing_pending = None
    for t in post.get("takers", []):
        if t.get("taker_id") == user_id and is_waiting_status(t.get("status")):
            existing_pending = t
            break

    if existing_pending:
        existing_pending["taker_reply_message_id"] = current_msg_id
        existing_pending["amount"] = take_amount
        existing_pending["status"] = "pending"
        # ถ้าเคยอยู่ในขั้น counter_pending แล้ว B กลับไป reply โพสต์ต้นทางใหม่ ให้เริ่มรอยืนยันใหม่
        existing_pending.pop("counter_amount", None)
        existing_pending.pop("counter_message_id", None)
        existing_pending.pop("counter_by", None)
        existing_pending.pop("last_counter_text", None)
        existing_pending["updated_at"] = now_text()
        existing_pending["last_confirm_text"] = getattr(event.message, "text", "")
        save_round_backup_db(reason="pending_updated")
        # เงียบ: ถือว่าอัปเดตรายการรอยืนยันเรียบร้อยแล้ว
        return None

    post["takers"].append({
        "taker_id": user_id,
        "taker_reply_message_id": current_msg_id,
        "amount": take_amount,
        "status": "pending",
        "created_at": now_text(),
        "last_confirm_text": getattr(event.message, "text", ""),
    })
    save_round_backup_db(reason="pending_created")

    # เงียบเมื่อรับติดสำเร็จ
    return None


def create_match_from_pending(post, taker_entry):
    """
    สำเร็จ = ส่ง Flex แล้ว return None เพื่อให้บอทไม่ตอบในกลุ่ม
    error = return text
    """
    if taker_entry.get("status") != "pending":
        return "รายการนี้ถูกดำเนินการไปแล้ว"

    # โพสต์เดิมต้องติดซ้ำได้เรื่อย ๆ แม้ก่อนหน้านี้จะจับคู่สำเร็จไปแล้ว
    # status closed จากโค้ดเดิมถือเป็นสถานะเก่าที่เกิดจากยอดเต็ม ไม่ใช่การปิดรับจริง
    post_status = post.get("status", "open")
    if post_status not in ["open", "closed"]:
        return "โพสต์นี้ไม่เปิดรับแล้ว"

    if post_status == "closed":
        post["status"] = "open"

    maker = USERS.get(post["maker_id"])
    taker = USERS.get(taker_entry["taker_id"])

    if not maker or not taker:
        return "ไม่พบข้อมูลสมาชิก"

    post_amount = int(post.get("amount", 0) or 0)
    amount = int(taker_entry.get("amount", post_amount) or 0)

    if amount <= 0:
        taker_entry["status"] = "rejected"
        return "จับคู่ไม่สำเร็จ ยอดติดไม่ถูกต้อง"

    if post_amount <= 0:
        taker_entry["status"] = "rejected"
        return "จับคู่ไม่สำเร็จ ยอดโพสต์ไม่ถูกต้อง"

    if amount > post_amount:
        taker_entry["status"] = "rejected"
        return (
            f"จับคู่ไม่สำเร็จ\n"
            f"ยอดที่ขอติด: {amount:,}\n"
            f"ยอดที่โพสต์ไว้: {post_amount:,}"
        )

    if user_credit_amount(maker) < amount:
        return insufficient_credit_warning(
            maker,
            amount,
            play_text=format_post_play_text(post),
            is_chty=bool(post.get("only_when_no_price")),
            action="ยืนยันจับคู่",
        )

    if user_credit_amount(taker) < amount:
        taker_entry["status"] = "rejected_credit"
        taker_entry["rejected_at"] = now_text()
        taker_entry["reject_reason"] = "taker_insufficient_credit_before_match"
        return insufficient_credit_warning(
            taker,
            amount,
            play_text=format_post_play_text(post),
            is_chty=bool(post.get("only_when_no_price")),
            action="ยืนยันจับคู่",
        )

    # ล็อกเครดิตทั้งสองฝั่งก่อนรอแจ้งผล
    maker["credit"] = user_credit_amount(maker) - amount
    taker["credit"] = user_credit_amount(taker) - amount
    save_user_db()

    match_id = str(uuid.uuid4())
    order_no = get_next_order_no()

    match = {
        "match_id": match_id,
        "round_id": post["round_id"],
        "base_no": post.get("base_no") or STATE.get("base_no"),
        "camp_name": post.get("camp_name") or STATE.get("camp_name"),
        "chat_id": post.get("chat_id") or STATE.get("chat_id"),
        "order_no": order_no,
        "post_id": post["post_id"],
        "posted_amount": int(post.get("amount", amount) or amount),
        "maker_id": post["maker_id"],
        "taker_id": taker_entry["taker_id"],
        # เก็บ snapshot ชื่อ/เลขสมาชิก ณ ตอนจับคู่ เพื่อให้ดูย้อนหลังได้ว่ารอบนี้ใครติดกับใคร
        # แม้ภายหลังผู้เล่นเปลี่ยนชื่อ LINE รายงานรอบนี้ยังมีข้อมูลเดิมอ้างอิงได้
        "maker_name": maker.get("line_name") or maker.get("name") or fallback_name(post["maker_id"]),
        "taker_name": taker.get("line_name") or taker.get("name") or fallback_name(taker_entry["taker_id"]),
        "maker_member_no": maker.get("member_no"),
        "taker_member_no": taker.get("member_no"),
        "maker_side": post["maker_side"],
        "raw_alias": post.get("raw_alias", ""),
        "price_adjust_target": post.get("price_adjust_target"),
        "price_adjust_min": post.get("price_adjust_min"),
        "price_adjust_max": post.get("price_adjust_max"),
        "custom_price_min": post.get("custom_price_min"),
        "custom_price_max": post.get("custom_price_max"),
        "is_two_digit_price": post.get("is_two_digit_price", False),
        "two_digit_min_token": post.get("two_digit_min_token"),
        "two_digit_max_token": post.get("two_digit_max_token"),
        "is_custom_price": post.get("is_custom_price", False),
        "only_when_no_price": post.get("only_when_no_price", False),
        "plus": post["plus"],
        "amount": amount,
        "status": "matched",
        "created_at": now_text(),
        "settled_at": None,
        "result": None,
        "winning_side": None,
        "cancel_requested": False,
        "cancel_requested_by": None,
        "cancel_requested_at": None,
        "cancel_rejected": False,
        "cancel_rejected_by": None,
        "cancel_rejected_at": None,
    }

    MATCHES[match_id] = match

    taker_entry["status"] = "matched"
    taker_entry["match_id"] = match_id
    taker_entry["matched_at"] = now_text()
    taker_entry.pop("counter_amount", None)
    taker_entry.pop("counter_message_id", None)
    taker_entry.pop("counter_by", None)

    # ไม่หัก remaining_amount และไม่ปิดโพสต์หลังจับคู่
    # 1 โพสต์ = แม่แบบรายการ สามารถให้คนอื่นมาติดซ้ำได้เรื่อย ๆ จนกว่าจะปิดรอบ/เปลี่ยนค่าย/แจ้งผล
    post["remaining_amount"] = int(post.get("amount", amount) or amount)
    post["status"] = "open"

    # สำรองทันทีหลังสร้างคู่ติดสำเร็จ กันบอทค้างก่อนส่ง Flex / ก่อนตอบกลับ LINE
    save_round_backup_db(reason="match_created")

    # ส่ง Flex หาทั้งคู่แบบ async ทันที ไม่ sync profile ก่อน
    push_flex_async(match["maker_id"], "จับคู่สำเร็จ", matched_flex_for_user(match, match["maker_id"]))
    push_flex_async(match["taker_id"], "จับคู่สำเร็จ", matched_flex_for_user(match, match["taker_id"]))

    # เงียบในกลุ่มเมื่อแผลสมบูรณ์
    return None


def request_cancel(match_id, requester_id):
    """
    เงื่อนไขขอยกเลิก:
    - ขอได้เฉพาะตอนรอบยังเปิดอยู่เท่านั้น
    - 1 Order ขอได้แค่ 1 ครั้ง
    - หลังปิดรอบแล้ว กดปุ่มขอยกเลิกไม่ได้
    """
    match = MATCHES.get(match_id)
    if not match:
        return "ไม่พบรายการ"
    select_round_base_for_match(match)

    if not STATE.get("opened"):
        return "ปิดรอบแล้ว ไม่สามารถขอยกเลิกได้"

    if match["status"] != "matched":
        return "รายการนี้ไม่อยู่ในสถานะที่ขอยกเลิกได้"

    if requester_id not in [match["maker_id"], match["taker_id"]]:
        return "คุณไม่ใช่คู่รายการนี้"

    if match.get("cancel_requested"):
        requester_name = user_display_name(match.get("cancel_requested_by"))
        return (
            f"รายการนี้มีการขอยกเลิกไปแล้ว\n"
            f"{match_cancel_detail_text(match)}\n"
            f"ผู้ขอ: {requester_name}\n"
            f"ขอยกเลิกได้แค่ 1 ครั้งต่อรายการ"
        )

    match["cancel_requested"] = True
    match["cancel_requested_by"] = requester_id
    match["cancel_requested_at"] = now_text()

    other_id = get_other_user_id(match, requester_id)

    push_flex_async(
        other_id,
        "มีคำขอยกเลิก",
        cancel_request_flex(match, requester_id),
    )

    return (
        f"ส่งคำขอยกเลิกให้อีกฝ่ายแล้ว\n"
        f"{match_cancel_detail_text(match)}"
    )


def approve_cancel(match_id, approver_id):
    match = MATCHES.get(match_id)
    if not match:
        return "ไม่พบรายการ"
    select_round_base_for_match(match)

    if not STATE.get("opened"):
        return "ปิดรอบแล้ว ไม่สามารถยกเลิกรายการได้"

    if match["status"] != "matched":
        return "รายการนี้ไม่สามารถยกเลิกได้"

    if approver_id not in [match["maker_id"], match["taker_id"]]:
        return "คุณไม่ใช่คู่รายการนี้"

    if not match.get("cancel_requested"):
        return "ยังไม่มีคำขอยกเลิกสำหรับรายการนี้"

    maker = USERS.get(match["maker_id"])
    taker = USERS.get(match["taker_id"])

    if maker:
        maker["credit"] += match["amount"]

    if taker:
        taker["credit"] += match["amount"]

    save_user_db()

    match["status"] = "cancelled"
    match["cancelled_at"] = now_text()

    cancel_detail = match_cancel_detail_text(match)
    success_flex = cancel_success_flex(match)
    push_flex_async(match["maker_id"], "ยกเลิกสำเร็จ", success_flex)
    push_flex_async(match["taker_id"], "ยกเลิกสำเร็จ", success_flex)

    return (
        f"ยกเลิกรายการสำเร็จ และคืนเครดิตแล้ว\n"
        f"{cancel_detail}"
    )



def reject_cancel(match_id, rejecter_id):
    """
    ปฏิเสธคำขอยกเลิก:
    - ต้องมีคำขอยกเลิกก่อน
    - ต้องเป็นคู่กรณีในรายการ
    - หลังปิดรอบปฏิเสธไม่ได้ เพราะปุ่มยกเลิกหมดสิทธิ์แล้ว
    - รายการยังคงเล่นตามเดิม
    - ขอได้แค่ 1 ครั้ง ดังนั้นปฏิเสธแล้วจะไม่เปิดให้ขอซ้ำ
    """
    match = MATCHES.get(match_id)
    if not match:
        return "ไม่พบรายการ"
    select_round_base_for_match(match)

    if not STATE.get("opened"):
        return "ปิดรอบแล้ว ไม่สามารถตอบคำขอยกเลิกได้"

    if match["status"] != "matched":
        return "รายการนี้ไม่อยู่ในสถานะที่ตอบคำขอยกเลิกได้"

    if rejecter_id not in [match["maker_id"], match["taker_id"]]:
        return "คุณไม่ใช่คู่รายการนี้"

    if not match.get("cancel_requested"):
        return "ยังไม่มีคำขอยกเลิกสำหรับรายการนี้"

    requester_id = match.get("cancel_requested_by")
    if requester_id == rejecter_id:
        return "ผู้ขอยกเลิกไม่สามารถปฏิเสธคำขอของตัวเองได้"

    match["cancel_rejected"] = True
    match["cancel_rejected_by"] = rejecter_id
    match["cancel_rejected_at"] = now_text()

    reject_flex = cancel_reject_flex(match, rejecter_id)
    push_flex_async(match["maker_id"], "ปฏิเสธคำขอยกเลิก", reject_flex)
    push_flex_async(match["taker_id"], "ปฏิเสธคำขอยกเลิก", reject_flex)

    return (
        f"ปฏิเสธคำขอยกเลิกแล้ว\n"
        f"{match_cancel_detail_text(match)}"
    )

def cancel_no_price_only_entries(reason: str = "ราคาช่างกลับมาตีราคา"):
    """
    ยกเลิก/จาวแผล ชตย ทันทีเมื่อแอดมินแจ้งราคาช่างเป็นตัวเลข
    - matched: คืนเครดิตทั้งสองฝั่งและส่ง Flex แจ้ง
    - open/pending post: ปิดโพสต์เพื่อกันยืนยันย้อนหลัง
    """
    current_round_id = STATE.get("round_id")
    if not current_round_id:
        return 0

    cancelled_matches = []

    for match in list(MATCHES.values()):
        if (
            match.get("round_id") == current_round_id
            and match.get("status") == "matched"
            and match.get("only_when_no_price")
        ):
            amount = match.get("amount", 0)
            maker = USERS.get(match.get("maker_id"))
            taker = USERS.get(match.get("taker_id"))

            if maker:
                maker["credit"] += amount
            if taker:
                taker["credit"] += amount

            match["status"] = "cancelled"
            match["cancelled_at"] = now_text()
            match["cancel_reason"] = reason
            match["winning_side"] = "จาว"
            cancelled_matches.append(match)

    for post in list(POSTS.values()):
        if (
            post.get("round_id") == current_round_id
            and post.get("only_when_no_price")
            and post.get("status") == "open"
        ):
            post["status"] = "cancelled"
            post["cancel_reason"] = reason
            for taker in post.get("takers", []):
                if is_waiting_status(taker.get("status")):
                    taker["status"] = "cancelled"
                    taker["cancel_reason"] = reason

    if cancelled_matches:
        save_user_db()

        lines = [
            "⚠️ ยกเลิกแผล ชตย อัตโนมัติ",
            f"เหตุผล: {reason}",
            f"จำนวน: {len(cancelled_matches)} รายการ",
            "",
        ]

        user_cancelled_matches = {}
        for match in cancelled_matches:
            for uid in [match.get("maker_id"), match.get("taker_id")]:
                if uid:
                    user_cancelled_matches.setdefault(uid, []).append(match)

        for uid, user_matches in user_cancelled_matches.items():
            push_flex_async(
                uid,
                "ยกเลิกแผล ชตย อัตโนมัติ",
                chty_auto_cancel_summary_flex(uid, user_matches, reason),
            )

        for match in cancelled_matches[:20]:
            lines.append(f"Order #{match.get('order_no')} | {format_match_play_text(match)} | {match.get('amount', 0):,}")
        if len(cancelled_matches) > 20:
            lines.append(f"...อีก {len(cancelled_matches) - 20} รายการ")

    return len(cancelled_matches)

def settle_round(result_value: int):
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบเปิดอยู่"

    if not has_price_setting():
        return "ยังไม่ได้แจ้งราคาช่าง เช่น ราคาช่าง 330-360 หรือ ราคาช่าง ไม่ต่อย"

    if STATE.get("settled"):
        return f"รอบนี้แจ้งผลไปแล้ว ผลเดิมคือ {STATE.get('result')}"

    unresolved = two_digit_unresolved_warning()
    if unresolved:
        return unresolved

    current_round_id = STATE["round_id"]
    target_matches = [
        m for m in MATCHES.values()
        if m.get("round_id") == current_round_id and m.get("status") == "matched"
    ]

    STATE["result"] = result_value
    STATE["settled"] = True
    STATE["opened"] = False
    STATE["updated_at"] = now_text()
    STATE["pending_result"] = None
    STATE["pending_result_at"] = None
    clear_pending_price()
    clear_pending_round_clear()

    if not target_matches:
        return f"แจ้งผล {result_value} แล้ว แต่ไม่มีรายการที่จับคู่สำเร็จในรอบนี้"

    user_rows = {}
    user_net = {}
    processed_count = 0
    no_price_jow_count = 0
    round_commission_total = 0
    commission_order_rows = []
    price_text = current_price_text()

    for match in target_matches:
        maker_id = match["maker_id"]
        taker_id = match["taker_id"]
        amount = match["amount"]

        maker = USERS.get(maker_id)
        taker = USERS.get(taker_id)

        maker_side = get_user_side(match, maker_id)
        taker_side = get_user_side(match, taker_id)

        price_min, price_max = get_match_price_range(match)
        match_price_text = format_match_price_text(match)

        # กรณีราคาช่างไม่ออก: แผลที่อิงราคาช่าง เช่น ชถ500 / ชล500 = จาวคืนทุน
        # แต่แผลราคาเลข เช่น 330-370ล500 หรือ 330-370ล500 ชตย ยังคิดตามเลขปกติ
        if STATE.get("price_mode") == "no_price" and not match.get("is_custom_price"):
            winning_side = "จาว"
            no_price_jow_count += 1
        else:
            if price_min is None or price_max is None:
                # กันบอทล้มกรณีข้อมูลเก่าไม่มีราคา ให้จาวและคืนเครดิตแทนการข้ามรายการ
                winning_side = "จาว"
                match_price_text = current_price_text()
            else:
                winning_side = winning_side_for_match_result(match, result_value, price_min, price_max)

        if winning_side == "จาว":
            if maker:
                maker["credit"] += amount
            if taker:
                taker["credit"] += amount

            maker_delta = 0
            taker_delta = 0
            maker_status = "จาว"
            taker_status = "จาว"

        elif winning_side == maker_side:
            commission = calculate_commission(amount)
            if maker:
                maker["credit"] += (amount * 2) - commission

            maker_delta = amount - commission
            taker_delta = -amount
            maker_status = "ชนะ"
            taker_status = "แพ้"
            round_commission_total += commission
            commission_order_rows.append({
                "order_no": match.get("order_no"),
                "winner_id": maker_id,
                "winner_name": user_display_name(maker_id),
                "amount": amount,
                "commission": commission,
                "net_win": maker_delta,
            })

        else:
            commission = calculate_commission(amount)
            if taker:
                taker["credit"] += (amount * 2) - commission

            maker_delta = -amount
            taker_delta = amount - commission
            maker_status = "แพ้"
            taker_status = "ชนะ"
            round_commission_total += commission
            commission_order_rows.append({
                "order_no": match.get("order_no"),
                "winner_id": taker_id,
                "winner_name": user_display_name(taker_id),
                "amount": amount,
                "commission": commission,
                "net_win": taker_delta,
            })

        match["status"] = "settled"
        match["settled_at"] = now_text()
        match["result"] = result_value
        match["winning_side"] = winning_side
        match["settle_price_min"] = price_min
        match["settle_price_max"] = price_max
        match["settle_price_text"] = match_price_text
        match["commission_percent"] = COMMISSION_PERCENT
        if winning_side == "จาว":
            match["commission"] = 0
            match["winner_id"] = None
        elif winning_side == maker_side:
            match["commission"] = amount - maker_delta
            match["winner_id"] = maker_id
        else:
            match["commission"] = amount - taker_delta
            match["winner_id"] = taker_id
        processed_count += 1

        user_rows.setdefault(maker_id, []).append({
            "order_no": match["order_no"],
            "other_id": taker_id,
            "user_side": maker_side,
            "status": maker_status,
            "delta": maker_delta,
            "price_min": price_min,
            "price_max": price_max,
            "price_text": match_price_text,
            "commission": (amount - maker_delta) if maker_status == "ชนะ" else 0,
        })
        user_net[maker_id] = user_net.get(maker_id, 0) + maker_delta

        user_rows.setdefault(taker_id, []).append({
            "order_no": match["order_no"],
            "other_id": maker_id,
            "user_side": taker_side,
            "status": taker_status,
            "delta": taker_delta,
            "price_min": price_min,
            "price_max": price_max,
            "price_text": match_price_text,
            "commission": (amount - taker_delta) if taker_status == "ชนะ" else 0,
        })
        user_net[taker_id] = user_net.get(taker_id, 0) + taker_delta

    if round_commission_total > 0:
        add_profit_record(
            current_round_id,
            STATE.get("camp_name"),
            result_value,
            round_commission_total,
            commission_order_rows,
            price_text,
        )

    save_user_db()

    # ส่ง Flex สรุปผลแบบ async เพื่อลดอาการหน่วง
    for uid, rows in user_rows.items():
        push_flex_async(
            uid,
            f"ผลรอบ {STATE.get('camp_name')}",
            result_summary_flex(uid, rows, user_net.get(uid, 0))
        )

    # ตอบกลับในกลุ่มด้วย Flex ใหญ่เต็มจอ พร้อมสถานะ ✅❌⛔
    return public_result_reply_payload(result_value)

def settle_round_all_jow(reason: str):
    """
    แจ้งผลแบบคืนทุนทุกคน เช่น
    - แจ้งผล จาวทุกแผล
    - แจ้งผล บั้งไฟหาย
    จะคืนเครดิตรายการ matched ทั้งหมดในรอบปัจจุบัน และไม่หักเปอร์เซ็นต์
    """
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบเปิดอยู่"

    if STATE.get("settled"):
        return f"รอบนี้แจ้งผลไปแล้ว ผลเดิมคือ {STATE.get('result')}"

    current_round_id = STATE["round_id"]
    target_matches = [
        m for m in MATCHES.values()
        if m.get("round_id") == current_round_id and m.get("status") == "matched"
    ]

    STATE["result"] = reason
    STATE["settled"] = True
    STATE["opened"] = False
    STATE["updated_at"] = now_text()
    STATE["pending_result"] = None
    STATE["pending_result_at"] = None
    clear_pending_price()
    clear_pending_round_clear()

    if not target_matches:
        return f"แจ้งผล {reason} แล้ว แต่ไม่มีรายการที่จับคู่สำเร็จในรอบนี้"

    user_rows = {}
    user_net = {}

    for match in target_matches:
        maker_id = match["maker_id"]
        taker_id = match["taker_id"]
        amount = int(match.get("amount", 0) or 0)
        maker = USERS.get(maker_id)
        taker = USERS.get(taker_id)

        if maker:
            maker["credit"] += amount
        if taker:
            taker["credit"] += amount

        maker_side = get_user_side(match, maker_id)
        taker_side = get_user_side(match, taker_id)
        price_min, price_max = get_match_price_range(match)
        match_price_text = format_match_price_text(match)

        match["status"] = "settled"
        match["settled_at"] = now_text()
        match["result"] = reason
        match["winning_side"] = "จาว"
        match["settle_price_min"] = price_min
        match["settle_price_max"] = price_max
        match["settle_price_text"] = match_price_text
        match["commission_percent"] = COMMISSION_PERCENT
        match["commission"] = 0
        match["winner_id"] = None

        user_rows.setdefault(maker_id, []).append({
            "order_no": match["order_no"],
            "other_id": taker_id,
            "user_side": maker_side,
            "status": "จาว",
            "delta": 0,
            "price_min": price_min,
            "price_max": price_max,
            "price_text": match_price_text,
            "commission": 0,
        })
        user_net[maker_id] = user_net.get(maker_id, 0)

        user_rows.setdefault(taker_id, []).append({
            "order_no": match["order_no"],
            "other_id": maker_id,
            "user_side": taker_side,
            "status": "จาว",
            "delta": 0,
            "price_min": price_min,
            "price_max": price_max,
            "price_text": match_price_text,
            "commission": 0,
        })
        user_net[taker_id] = user_net.get(taker_id, 0)

    save_user_db()

    for uid, rows in user_rows.items():
        push_flex_async(
            uid,
            f"ผลรอบ {STATE.get('camp_name')}",
            result_summary_flex(uid, rows, user_net.get(uid, 0))
        )

    # ตอบกลับในกลุ่มด้วย Flex ใหญ่เต็มจอ พร้อมสถานะจาว ⛔
    return public_result_reply_payload(reason)


def handle_special_result_with_double_confirm(reason: str):
    """
    แจ้งผลคืนทุนทุกคน ใช้การยืนยัน 2 ครั้งเหมือนแจ้งผลตัวเลข
    """
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบเปิดอยู่"

    if STATE.get("settled"):
        return f"รอบนี้แจ้งผลไปแล้ว ผลเดิมคือ {STATE.get('result')}"

    token = f"SPECIAL:{reason}"
    pending = STATE.get("pending_result")

    if pending != token:
        STATE["pending_result"] = token
        STATE["pending_result_at"] = now_text()
        return (
            f"⚠️ ยืนยันผลครั้งที่ 1: {reason}\n"
            f"กติกา: คืนทุนทุกคน / ไม่มีคนเสีย / ไม่หัก {COMMISSION_PERCENT}%\n\n"
            f"ถ้าถูกต้อง ให้พิมพ์ซ้ำอีกครั้ง:\n"
            f"แจ้งผล {STATE.get('camp_name') or '-'} {reason}"
        )

    return settle_round_all_jow(reason)


def handle_result_with_double_confirm(result_value: int):
    """
    แจ้งผลต้องพิมพ์ 2 ครั้ง:
    ครั้งที่ 1 = ตั้งค่ารอยืนยัน
    ครั้งที่ 2 = ถ้าตัวเลขตรงกัน จึงคิดผลจริง
    """
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบเปิดอยู่"

    if not has_price_setting():
        return "ยังไม่ได้แจ้งราคาช่าง เช่น ราคาช่าง 330-360 หรือ ราคาช่าง ไม่ต่อย"

    if STATE.get("settled"):
        return f"รอบนี้แจ้งผลไปแล้ว ผลเดิมคือ {STATE.get('result')}"

    unresolved = two_digit_unresolved_warning()
    if unresolved:
        return unresolved

    pending = STATE.get("pending_result")
    price_text = current_price_text()

    if pending is None:
        STATE["pending_result"] = result_value
        STATE["pending_result_at"] = now_text()
        return (
            f"⚠️ ยืนยันผลครั้งที่ 1: {result_value}\n"
            f"ราคาช่าง: {price_text}\n\n"
            f"ถ้าถูกต้อง ให้พิมพ์ซ้ำอีกครั้ง:\n"
            f"แจ้งผล {STATE.get('camp_name') or '-'} {result_value}"
        )

    if pending != result_value:
        STATE["pending_result"] = result_value
        STATE["pending_result_at"] = now_text()
        return (
            f"⚠️ ตัวเลขผลไม่ตรงกับครั้งก่อน\n"
            f"ผลเดิมที่รอยืนยัน: {pending}\n"
            f"ผลใหม่ที่รับไว้: {result_value}\n\n"
            f"ถ้าผลใหม่ถูกต้อง ให้พิมพ์ซ้ำอีกครั้ง:\n"
            f"แจ้งผล {STATE.get('camp_name') or '-'} {result_value}"
        )

    return settle_round(result_value)


def clear_pending_rollback():
    STATE["pending_rollback"] = None
    STATE["pending_rollback_at"] = None
    STATE["pending_rollback_ts"] = None


def rollback_candidate_rounds_for_chat(chat_id: str = None):
    """คืนฐานที่แจ้งผลแล้ว เพื่อใช้เลือกฐานตอนแอดมินสั่งย้อนผล"""
    rows = []
    for base_no, st in sorted(ROUNDS.items(), key=lambda x: str(x[0])):
        if chat_id and st.get("chat_id") and st.get("chat_id") != chat_id:
            continue
        if st.get("round_id") and st.get("settled"):
            rows.append((base_no, st))
    return rows


def rollback_explicit_base_required_text(chat_id: str = None) -> str:
    candidates = rollback_candidate_rounds_for_chat(chat_id)
    lines = [
        "⚠️ มีหลายค่ายที่แจ้งผลแล้ว กรุณาระบุชื่อค่ายที่จะย้อนผล",
        "",
        "ตัวอย่างคำสั่งที่ถูกต้อง:",
        "- ย้อนผล แอ๊ดเทวดา",
        "- ยืนยันย้อนผล แอ๊ดเทวดา",
        "",
        "ค่ายที่ย้อนผลได้:",
    ]
    for base_no, st in candidates:
        extra = f" | รหัสในระบบ: ฐาน{base_no}" if not USE_CAMP_NAME_LABELS else ""
        lines.append(
            f"ค่าย: {st.get('camp_name') or '-'} | "
            f"ผลเดิม: {st.get('result')} | ราคา: {state_price_text(st)}{extra}"
        )
    return "\n".join(lines)


def _rollback_debits_for_matches(target_matches):
    """
    คำนวณยอดที่ต้องดึงคืนจากเครดิตผู้เล่น เพื่อย้อนกลับไปสถานะก่อนแจ้งผล
    ตอนจับคู่สำเร็จระบบหักเครดิตทั้งสองฝั่งไว้แล้ว ดังนั้นตอนย้อนผลต้องดึงเฉพาะยอดที่ payout ตอนแจ้งผล
    """
    debits = {}
    details = []

    for match in target_matches:
        amount = int(match.get("amount", 0) or 0)
        maker_id = match.get("maker_id")
        taker_id = match.get("taker_id")
        winning_side = match.get("winning_side")
        winner_id = match.get("winner_id")
        commission = int(match.get("commission", 0) or 0)

        if winning_side == "จาว":
            for uid in (maker_id, taker_id):
                if uid:
                    debits[uid] = debits.get(uid, 0) + amount
            details.append({
                "order_no": match.get("order_no"),
                "type": "จาว",
                "debits": {maker_id: amount, taker_id: amount},
                "commission": 0,
            })
        else:
            if not winner_id:
                continue
            payout = (amount * 2) - commission
            debits[winner_id] = debits.get(winner_id, 0) + payout
            details.append({
                "order_no": match.get("order_no"),
                "type": "ชนะ/แพ้",
                "winner_id": winner_id,
                "payout": payout,
                "commission": commission,
            })

    return debits, details


def rollback_profit_for_round(round_id: str, rollback_by: str = "-"):
    rounds = PROFIT.get("rounds", []) or []
    removed = [r for r in rounds if r.get("round_id") == round_id]
    if not removed:
        return 0, 0

    removed_profit = sum(int(r.get("profit", 0) or 0) for r in removed)
    PROFIT["rounds"] = [r for r in rounds if r.get("round_id") != round_id]
    PROFIT["total_profit"] = max(0, int(PROFIT.get("total_profit", 0) or 0) - removed_profit)
    PROFIT.setdefault("rollback_logs", []).append({
        "round_id": round_id,
        "rollback_by": rollback_by or "-",
        "removed_profit": removed_profit,
        "removed_records": len(removed),
        "rolled_back_at": now_text(),
    })
    save_profit_db()
    return removed_profit, len(removed)


def rollback_round_result(rollback_by: str = "-"):
    """
    ย้อนผลรอบปัจจุบัน:
    - ดึง payout ที่เคยคืน/จ่ายตอนแจ้งผล ออกจากเครดิตผู้เล่น
    - เปลี่ยนบิล settled กลับเป็น matched เพื่อให้แจ้งผลใหม่ได้
    - ลบกำไรของรอบนั้นออกจาก profit.json
    - ตั้ง STATE กลับเป็นปิดรอบ/รอแจ้งผล
    """
    if STATE.get("round_id") is None:
        clear_pending_rollback()
        return "ยังไม่มีรอบให้ย้อนผล"

    if not STATE.get("settled"):
        clear_pending_rollback()
        return "รอบนี้ยังไม่ได้แจ้งผล จึงไม่ต้องย้อนผล"

    current_round_id = STATE.get("round_id")
    old_result = STATE.get("result")
    target_matches = [
        m for m in MATCHES.values()
        if m.get("round_id") == current_round_id and m.get("status") == "settled"
    ]

    debits, rollback_details = _rollback_debits_for_matches(target_matches)

    # ตรวจยอดก่อน mutate กันเครดิตติดลบแบบไม่ตั้งใจ
    insufficient = []
    for uid, debit in sorted(debits.items(), key=lambda x: user_display_name(x[0])):
        user = USERS.get(uid)
        if not user:
            insufficient.append(f"{user_display_name(uid)} | ไม่พบข้อมูลผู้ใช้ | ต้องดึงคืน {debit:,}")
            continue
        current_credit = int(user.get("credit", 0) or 0)
        if current_credit < debit:
            insufficient.append(
                f"{user_display_name(uid)} | เครดิตคงเหลือ {current_credit:,} | ต้องดึงคืน {debit:,}"
            )

    if insufficient:
        clear_pending_rollback()
        return (
            "❌ ย้อนผลไม่ได้ เพราะเครดิตบางคนไม่พอสำหรับดึง payout คืน\n"
            "ระบบยังไม่แก้เครดิต/ไม่แก้ผล/ไม่แก้กำไร เพื่อกันยอดเพี้ยน\n\n"
            + "\n".join(insufficient[:20])
            + (f"\n...อีก {len(insufficient) - 20} รายการ" if len(insufficient) > 20 else "")
        )

    # ดึงเครดิตที่เคย payout กลับ
    for uid, debit in debits.items():
        user = USERS.get(uid)
        if user:
            user["credit"] = int(user.get("credit", 0) or 0) - int(debit or 0)

    # เปลี่ยนบิลกลับไปรอแจ้งผลใหม่
    rollback_at = now_text()
    for match in target_matches:
        history = match.setdefault("rollback_history", [])
        history.append({
            "old_result": match.get("result"),
            "old_winning_side": match.get("winning_side"),
            "old_winner_id": match.get("winner_id"),
            "old_commission": int(match.get("commission", 0) or 0),
            "rolled_back_by": rollback_by or "-",
            "rolled_back_at": rollback_at,
        })
        match["status"] = "matched"
        for key in [
            "settled_at", "result", "winning_side", "settle_price_min", "settle_price_max",
            "settle_price_text", "commission_percent", "commission", "winner_id",
        ]:
            match.pop(key, None)
        match["rolled_back_at"] = rollback_at
        match["rolled_back_by"] = rollback_by or "-"

    removed_profit, removed_profit_records = rollback_profit_for_round(current_round_id, rollback_by=rollback_by)

    STATE["result"] = None
    STATE["settled"] = False
    STATE["opened"] = False
    STATE["updated_at"] = rollback_at
    STATE["pending_result"] = None
    STATE["pending_result_at"] = None
    clear_pending_rollback()
    clear_pending_price()
    clear_pending_round_clear()

    save_user_db()

    return (
        f"✅ ย้อนผล ค่าย {STATE.get('camp_name') or '-'} เรียบร้อย\n"
        f"กรุณาออกผลใหม่"
    )


def handle_rollback_result_command(action: str, user_id: str = None):
    if STATE.get("round_id") is None:
        clear_pending_rollback()
        return "ยังไม่มีรอบให้ย้อนผล"

    if action == "cancel":
        clear_pending_rollback()
        return "ยกเลิกคำขอย้อนผลแล้ว"

    if not STATE.get("settled"):
        clear_pending_rollback()
        return "รอบนี้ยังไม่ได้แจ้งผล จึงไม่ต้องย้อนผล"

    current_round_id = STATE.get("round_id")
    target_matches = [
        m for m in MATCHES.values()
        if m.get("round_id") == current_round_id and m.get("status") == "settled"
    ]
    debits, _details = _rollback_debits_for_matches(target_matches)
    pending = STATE.get("pending_rollback") or {}
    pending_ts = float(STATE.get("pending_rollback_ts") or 0)
    now_ts = time.time()

    if action == "request":
        STATE["pending_rollback"] = {
            "round_id": current_round_id,
            "result": STATE.get("result"),
            "base_no": STATE.get("base_no"),
            "requested_by": user_id or "-",
        }
        STATE["pending_rollback_at"] = now_text()
        STATE["pending_rollback_ts"] = now_ts
        return (
            f"⚠️ ยืนยันย้อนผลครั้งที่ 1\n"
            f"ค่าย: {STATE.get('camp_name') or '-'}\n"
            f"ผลเดิม: {STATE.get('result')}\n"
            f"บิลที่จะกลับไปรอผล: {len(target_matches):,} รายการ\n"
            f"เครดิตที่จะดึงคืนรวม: {sum(debits.values()):,} เครดิต\n\n"
            f"ถ้าถูกต้อง ให้พิมพ์ซ้ำอีกครั้ง:\n"
            f"ยืนยันย้อนผล {STATE.get('camp_name') or '-'}\n\n"
            f"ถ้าไม่ใช่ ให้พิมพ์: ยกเลิกย้อนผล {STATE.get('camp_name') or '-'}"
        )

    if action == "confirm":
        if not pending or pending.get("round_id") != current_round_id:
            return (
                "⚠️ ยังไม่มีคำขอย้อนผลที่รอยืนยัน\n"
                f"ให้พิมพ์ก่อน: ย้อนผล {STATE.get('camp_name') or '-'}"
            )
        if now_ts - pending_ts > ROLLBACK_CONFIRM_TTL_SECONDS:
            clear_pending_rollback()
            return (
                "⚠️ คำขอยืนยันย้อนผลหมดอายุแล้ว\n"
                f"ให้พิมพ์ใหม่: ย้อนผล {STATE.get('camp_name') or '-'}"
            )
        rollback_by = user_display_name(user_id) if user_id else "-"
        return rollback_round_result(rollback_by=rollback_by)

    return "รูปแบบคำสั่งย้อนผลไม่ถูกต้อง"


def current_round_report():
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบปัจจุบัน"

    current_round_id = STATE.get("round_id")
    matched_count = sum(
        1 for m in MATCHES.values()
        if m.get("round_id") == current_round_id and m.get("status") == "matched"
    )
    settled_count = sum(
        1 for m in MATCHES.values()
        if m.get("round_id") == current_round_id and m.get("status") == "settled"
    )
    cancelled_count = sum(
        1 for m in MATCHES.values()
        if m.get("round_id") == current_round_id and m.get("status") == "cancelled"
    )
    no_price_only_count = sum(
        1 for m in MATCHES.values()
        if m.get("round_id") == current_round_id
        and m.get("status") == "matched"
        and m.get("only_when_no_price")
    )
    pending_count = 0
    for p in POSTS.values():
        if p.get("round_id") == current_round_id:
            pending_count += sum(1 for t in p.get("takers", []) if is_waiting_status(t.get("status")))

    if STATE.get("settled"):
        status = "แจ้งผลแล้ว"
    elif STATE.get("opened"):
        status = "เปิดรับอยู่"
    else:
        status = "ปิดแล้ว / รอราคาช่างหรือแจ้งผล"

    pending_result = STATE.get("pending_result")
    pending_text = f"\nผลที่รอยืนยัน: {pending_result}" if pending_result is not None else ""
    pending_price = pending_price_text()
    if pending_price:
        pending_text += f"\nราคาช่างที่รอยืนยัน: {pending_price}"
    if has_pending_round_clear():
        pending_clear = STATE.get("pending_clear") or {}
        pending_text += f"\nCR ที่รอยืนยัน: ค่าย {pending_clear.get('camp_name') or '-'}"

    return (
        f"CK | สถานะรอบปัจจุบัน\n\n"
        f"ค่าย: {STATE.get('camp_name') or '-'}\n"
        f"ห้องรอบ: {STATE.get('chat_id') or '-'}\n"
        f"สถานะ: {status}\n"
        f"ราคาช่าง: {current_price_text()}\n"
        f"ผล: {STATE.get('result')}\n"
        f"แผลสมบูรณ์รอคิดผล: {matched_count}\n"
        f"แผลที่คิดผลแล้ว: {settled_count}\n"
        f"แผลรอยืนยัน: {pending_count}\n"
        f"แผล ชตย รอคิดผล: {no_price_only_count}\n"
        f"แผลยกเลิก: {cancelled_count}"
        f"{pending_text}"
    )


def is_match_list_command(text: str) -> bool:
    clean = re.sub(r"\s+", "", (text or "").strip()).lower()
    return clean in {
        "คู่ติด",
        "คู่รอบนี้",
        "ใครติดใคร",
        "รายการคู่",
        "matches",
        "matchlist",
    }


def match_party_text(match: dict, role: str) -> str:
    """คืนชื่อผู้เล่นพร้อม ID สมาชิกจากข้อมูล snapshot ตอนจับคู่ ถ้าไม่มีค่อยอ่านจาก USERS ปัจจุบัน"""
    user_id = match.get(f"{role}_id")
    name = match.get(f"{role}_name") or user_display_name(user_id)
    member_no = match.get(f"{role}_member_no")

    if not member_no and user_id in USERS:
        member_no = USERS.get(user_id, {}).get("member_no")

    if member_no:
        return f"{name} (ID {member_no})"
    return name or "-"


def current_round_match_report(limit: int = 40) -> str:
    """รายงานว่ารอบปัจจุบันใครติดกับใครบ้าง ใช้สำหรับแอดมิน/หลังบ้าน"""
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบปัจจุบัน"

    current_round_id = STATE.get("round_id")
    rows = [
        m for m in MATCHES.values()
        if m.get("round_id") == current_round_id
        and m.get("status") in {"matched", "settled", "cancelled"}
    ]

    def sort_key(match):
        try:
            return int(match.get("order_no", 0) or 0)
        except Exception:
            return 0

    rows = sorted(rows, key=sort_key)

    if not rows:
        return (
            f"📌 คู่ติดรอบนี้\n\n"
            f"ค่าย: {STATE.get('camp_name') or '-'}\n"
            "ยังไม่มีคู่ที่จับคู่สำเร็จในรอบนี้"
        )

    status_map = {
        "matched": "รอผล",
        "settled": "คิดผลแล้ว",
        "cancelled": "ยกเลิก",
    }

    total_amount = sum(int(m.get("amount", 0) or 0) for m in rows if m.get("status") != "cancelled")
    lines = [
        "📌 คู่ติดรอบนี้",
        "",
        f"ค่าย: {STATE.get('camp_name') or '-'}",
        f"จำนวนบิล: {len(rows)}",
        f"ยอดรวมที่ยังมีผล/คิดผลแล้ว: {total_amount:,}",
        "",
    ]

    for m in rows[:limit]:
        maker_id = m.get("maker_id")
        taker_id = m.get("taker_id")
        maker_side = get_user_side(m, maker_id) or m.get("maker_side") or "-"
        taker_side = get_user_side(m, taker_id) or opposite_side(maker_side) or "-"
        amount = int(m.get("amount", 0) or 0)
        status_text = status_map.get(m.get("status"), m.get("status") or "-")
        play_text = format_match_play_text(m)
        price_text = format_match_price_text(m)

        lines.extend([
            f"#{m.get('order_no', '-')} | {status_text}",
            f"โพสต์: {match_party_text(m, 'maker')} | {maker_side}",
            f"ติด: {match_party_text(m, 'taker')} | {taker_side}",
            f"แผล: {play_text} | ราคา: {price_text} | ยอด {amount:,}",
            "",
        ])

    if len(rows) > limit:
        lines.append(f"...แสดง {limit} รายการแรก จากทั้งหมด {len(rows)} รายการ")

    return "\n".join(lines).strip()



def is_listplay_command(text: str) -> bool:
    """คำสั่งแอดมิน: listplay เพื่อดูรายชื่อสมาชิกที่จับคู่เล่นกันแบบสั้น"""
    clean = re.sub(r"\s+", "", (text or "").strip()).lower()
    return clean in {"listplay", "listplays"}


def _listplay_display_name(name: str) -> str:
    """ทำชื่อให้แสดงในบรรทัด listplay โดยไม่ให้ขึ้นบรรทัดใหม่/ยาวเกินไป"""
    text = re.sub(r"\s+", " ", str(name or "-")).strip()
    if len(text) > 28:
        text = text[:28] + "..."
    return text or "-"


def format_listplay_play_text(match: dict) -> str:
    """รวมข้อความแผล + จำนวนเงิน เช่น 320-350ล500 / 3-7ล500 / ชล500"""
    play_text = (format_match_play_text(match) or "-").strip()
    amount = int((match or {}).get("amount", 0) or 0)

    # ถ้ามี suffix เช่น ชตย ให้เอาจำนวนเงินไว้ก่อน suffix เพื่ออ่านง่าย
    suffix = ""
    for candidate in [" ชตย"]:
        if play_text.endswith(candidate):
            suffix = candidate
            play_text = play_text[:-len(candidate)].rstrip()
            break

    return f"{play_text}{amount:,}{suffix}"


def current_round_listplay_report(limit: int = 80) -> str:
    """
    รายงานแบบสั้นตามที่ต้องการ:
    นาย A เล่น 320-350ล500 กับ นาย B
    นาย A เล่น 3-7ล500 กับ นาย B
    """
    if STATE.get("round_id") is None:
        return "ยังไม่มีรอบปัจจุบัน"

    current_round_id = STATE.get("round_id")
    rows = [
        m for m in MATCHES.values()
        if m.get("round_id") == current_round_id
        and m.get("status") == "matched"
    ]

    def sort_key(match):
        try:
            return int(match.get("order_no", 0) or 0)
        except Exception:
            return 0

    rows = sorted(rows, key=sort_key)

    if not rows:
        return (
            f"listplay | ค่าย: {STATE.get('camp_name') or '-'}\n\n"
            "ยังไม่มีรายการที่จับคู่สำเร็จและรอผลในรอบนี้"
        )

    lines = [
        f"listplay | ค่าย: {STATE.get('camp_name') or '-'}",
        f"จำนวนคู่รอผล: {len(rows):,}",
        "",
    ]

    for m in rows[:limit]:
        maker_name = _listplay_display_name(m.get("maker_name") or user_display_name(m.get("maker_id")))
        taker_name = _listplay_display_name(m.get("taker_name") or user_display_name(m.get("taker_id")))
        play_text = format_listplay_play_text(m)
        price_note = ""
        if m.get("is_custom_price") or m.get("is_two_digit_price"):
            custom_price_text = format_match_price_text_for_active_list(m)
            if custom_price_text and custom_price_text != "-":
                price_note = f" | ราคาเล่น {custom_price_text}"
        lines.append(f"นาย {maker_name} เล่น {play_text} กับ นาย {taker_name}{price_note}")

    if len(rows) > limit:
        lines.append(f"...อีก {len(rows) - limit:,} คู่")

    return "\n".join(lines).strip()


def users_report():
    rows = sorted(USERS.values(), key=lambda u: int(u.get("member_no", 999999)))
    total = len(rows)
    confirmed = sum(1 for u in rows if u.get("is_friend"))

    lines = [
        "UID LIST | สมาชิกที่บอทเก็บไว้",
        f"ทั้งหมด {total} คน | ยืนยันแล้ว {confirmed} คน | ยังไม่ยืนยัน {total - confirmed} คน",
        "",
    ]

    for u in rows[:80]:
        friend = friend_status_text(u)
        lines.append(
            f"ID {u.get('member_no')} | {u.get('line_name') or u.get('name')} | เครดิต {int(u.get('credit', 0) or 0):,} | {friend}"
        )

    if len(rows) > 80:
        lines.append(f"...อีก {len(rows) - 80} คน")

    lines.append("")
    lines.append("หมายเหตุ: ถ้าคนที่เคยขึ้นไม่ยืนยัน ให้เขาทักแชทส่วนตัว OA อีก 1 ข้อความ แล้วพิมพ์ UIDLIST ใหม่")

    return "\n".join(lines)


def active_credit_amount_for_user(user_id: str) -> int:
    """ยอดเครดิตที่ถูกกันไว้จากบิลที่ยังรอผลของผู้ใช้คนนั้น"""
    if not user_id:
        return 0

    total = 0
    for match in list(MATCHES.values()):
        if not isinstance(match, dict):
            continue
        if match.get("status") != "matched":
            continue
        if user_id not in [match.get("maker_id"), match.get("taker_id")]:
            continue

        round_state = get_state_by_round_id(match.get("round_id"))
        if round_state and round_state.get("settled"):
            continue

        try:
            total += int(match.get("amount", 0) or 0)
        except Exception:
            pass

    return total


def call_report():
    # CALL แสดงลูกค้าที่มีเครดิตคงเหลือ หรือมียอดที่กำลังใช้อยู่ในบิลรอผล
    all_rows = sorted(USERS.values(), key=lambda u: int(u.get("member_no", 999999)))

    rows = []
    for u in all_rows:
        credit = user_credit_amount(u)
        active_amount = active_credit_amount_for_user(u.get("user_id"))
        total_amount = credit + active_amount
        if total_amount > 0:
            rows.append((u, credit, active_amount, total_amount))

    total_credit = sum(credit for _, credit, _, _ in rows)
    total_active = sum(active_amount for _, _, active_amount, _ in rows)
    total_all = total_credit + total_active

    lines = [
        "CALL | รายชื่อลูกค้าที่มีเครดิต",
        f"จำนวนลูกค้าที่มีเครดิต/กำลังใช้อยู่: {len(rows)} คน",
        f"เครดิตคงเหลือรวม: {total_credit:,}",
        f"กำลังใช้อยู่รวม: {total_active:,}",
        f"เครดิตรวมทั้งหมด: {total_all:,}",
        "",
    ]

    if not rows:
        lines.append("ยังไม่มีลูกค้าที่มีเครดิต")
        return "\n".join(lines)

    for u, credit, active_amount, total_amount in rows[:80]:
        name = u.get("line_name") or u.get("name")
        if active_amount > 0:
            lines.append(
                f"ID {u.get('member_no')} | {name} | คงเหลือ {credit:,} | กำลังใช้ {active_amount:,} | รวม {total_amount:,}"
            )
        else:
            lines.append(
                f"ID {u.get('member_no')} | {name} | เครดิต {credit:,}"
            )

    if len(rows) > 80:
        lines.append(f"...อีก {len(rows) - 80} คน")

    return "\n".join(lines)


def profit_report():
    rounds = PROFIT.get("rounds", []) or []
    total_profit = int(PROFIT.get("total_profit", 0) or 0)
    lines = [
        "ยอดกำไร | หลังบ้าน",
        f"กำไรสะสม: {total_profit:,} เครดิต",
        f"กติกา: หัก {COMMISSION_PERCENT}% จากคนที่ได้เท่านั้น คนเสียไม่หัก %",
        f"จำนวนรอบที่มีการหัก %: {len(rounds)} รอบ",
        "",
    ]

    if rounds:
        lines.append("ล่าสุด:")
        for r in rounds[-10:][::-1]:
            open_price = r.get("open_price") or r.get("price_text") or "-"
            lines.append(
                f"ค่าย {r.get('camp_name', '-')} | "
                f"เปิด {open_price} | "
                f"ผล {r.get('result', '-')} | "
                f"กำไร {int(r.get('profit', 0)):,}"
            )

    return "\n".join(lines).strip()


def reset_profit_report(reset_by: str = "-"):
    """ล้างยอดกำไรสะสมและประวัติรอบที่มีการหัก % ใน profit.json"""
    old_total_profit = int(PROFIT.get("total_profit", 0) or 0)
    old_round_count = len(PROFIT.get("rounds", []) or [])

    PROFIT["total_profit"] = 0
    PROFIT["rounds"] = []
    PROFIT["updated_at"] = datetime.now().isoformat()
    PROFIT["last_reset"] = {
        "reset_by": reset_by or "-",
        "old_total_profit": old_total_profit,
        "old_round_count": old_round_count,
        "reset_at": now_text(),
    }
    save_profit_db()

    return (
        "✅ ล้างกำไรเรียบร้อย\n\n"
        f"ยอดกำไรก่อนล้าง: {old_total_profit:,} เครดิต\n"
        f"ประวัติรอบที่ล้าง: {old_round_count:,} รอบ\n"
        "ยอดกำไรปัจจุบัน: 0 เครดิต"
    )


def reset_order_report(reset_by: str = "-", next_order_no: int = None):
    """
    ล้างออเดอร์ทั้งหมดและเริ่มนับเลขใหม่
    - คืนเครดิตให้บิลที่ยังจับคู่/รอแจ้งผลอยู่ก่อนล้าง
    - ล้าง POSTS และ MATCHES ใน memory ทั้งหมด
    - รีเซ็ตเลขออเดอร์ถัดไปเป็น #1 หรือเลขที่แอดมินกำหนด
    - ไม่ยุ่งกับ users.json, profit.json, slip_topups.json
    """
    if next_order_no is None:
        next_order_no = 1

    try:
        next_order_no = int(next_order_no)
    except Exception:
        next_order_no = 1

    if next_order_no <= 0:
        next_order_no = 1

    refunded_orders = []
    refunded_credit_total = 0
    cleared_match_count = 0
    cleared_post_count = 0
    cleared_pending_count = 0

    with STATE_LOCK:
        # คืนเครดิตเฉพาะบิลที่ยัง matched เพราะเครดิตถูก hold ไปแล้ว
        for match in list(MATCHES.values()):
            status = match.get("status")
            if status == "matched":
                amount = int(match.get("amount", 0) or 0)
                maker = USERS.get(match.get("maker_id"))
                taker = USERS.get(match.get("taker_id"))

                if maker:
                    maker["credit"] = int(maker.get("credit", 0) or 0) + amount
                    refunded_credit_total += amount
                if taker:
                    taker["credit"] = int(taker.get("credit", 0) or 0) + amount
                    refunded_credit_total += amount

                refunded_orders.append(str(match.get("order_no", "-")))

            cleared_match_count += 1

        # pending ยังไม่หักเครดิต แค่นับเพื่อรายงาน
        for post in list(POSTS.values()):
            cleared_post_count += 1
            cleared_pending_count += sum(
                1 for taker in post.get("takers", [])
                if is_waiting_status(taker.get("status"))
            )

        MATCHES.clear()
        POSTS.clear()

        # ล้างสถานะผลที่รอยืนยัน เพื่อไม่ให้คำสั่งแจ้งผลเก่ามาต่อกับรอบที่ไม่มีบิลแล้ว
        STATE["pending_result"] = None
        STATE["pending_result_at"] = None

        old_next_order_no = int(ORDER_STATE.get("next_order_no", ORDER_START_NO) or ORDER_START_NO)
        ORDER_STATE["next_order_no"] = next_order_no
        ORDER_STATE["last_reset"] = {
            "reset_by": reset_by or "-",
            "old_next_order_no": old_next_order_no,
            "new_next_order_no": next_order_no,
            "cleared_matches": cleared_match_count,
            "cleared_posts": cleared_post_count,
            "cleared_pending": cleared_pending_count,
            "refunded_credit_total": refunded_credit_total,
            "refunded_orders": refunded_orders,
            "reset_at": now_text(),
        }

        save_user_db()
        save_order_db()
        save_round_backup_db(reason="order_reset")

    sample_orders = ", ".join(f"#{x}" for x in refunded_orders[:8])
    if len(refunded_orders) > 8:
        sample_orders += f" ...อีก {len(refunded_orders) - 8} รายการ"

    refunded_text = sample_orders if sample_orders else "ไม่มีบิลที่ต้องคืนเครดิต"

    return (
        "✅ ล้างออเดอร์ทั้งหมดเรียบร้อย\n\n"
        f"เลขออเดอร์ถัดไปเดิม: #{old_next_order_no}\n"
        f"เลขออเดอร์ถัดไปใหม่: #{next_order_no}\n\n"
        f"ล้างบิลทั้งหมด: {cleared_match_count:,} รายการ\n"
        f"ล้างโพสต์ทั้งหมด: {cleared_post_count:,} รายการ\n"
        f"ล้างรายการรอติด: {cleared_pending_count:,} รายการ\n"
        f"คืนเครดิตจากบิลค้าง: {refunded_credit_total:,} เครดิต\n"
        f"บิลที่คืนเครดิต: {refunded_text}\n\n"
        "หมายเหตุ: ไม่กระทบยอดกำไร / ประวัติสลิป / ข้อมูลสมาชิก"
    )


def clear_round_backups_report(clear_by: str = "-") -> str:
    """ล้างไฟล์ backup รอบใน ROUND_BACKUP_DIR และไฟล์ backup legacy โดยไม่แตะข้อมูลใน memory"""
    global ROUND_BACKUP_SUPPRESS_UNTIL

    deleted_files = []
    deleted_bytes = 0
    skipped_files = []
    error_files = []

    def delete_file(path: str):
        nonlocal deleted_bytes
        try:
            if not os.path.isfile(path):
                return
            size = os.path.getsize(path)
            os.remove(path)
            deleted_files.append(path)
            deleted_bytes += size
        except Exception as e:
            error_files.append(f"{path}: {e}")

    def is_round_backup_file(name: str) -> bool:
        # ไฟล์ที่ระบบนี้สร้างจริง เช่น round_base1_xxx.json และ .json.bak
        lower_name = name.lower()
        if lower_name.startswith("round_base") and (lower_name.endswith(".json") or lower_name.endswith(".json.bak") or lower_name.endswith(".bak")):
            return True
        # เผื่อมี temp/backup ตกค้างจาก atomic write ในโฟลเดอร์ round_backups
        if lower_name.startswith("round_backup_") and lower_name.endswith(".json"):
            return True
        return False

    with STATE_LOCK:
        backup_dir = ROUND_BACKUP_DIR or "round_backups"

        if os.path.isdir(backup_dir):
            for name in os.listdir(backup_dir):
                path = os.path.join(backup_dir, name)
                if os.path.isdir(path):
                    skipped_files.append(path)
                    continue
                if is_round_backup_file(name):
                    delete_file(path)
                else:
                    skipped_files.append(path)

        # ล้างไฟล์ backup แบบเก่าด้วย ถ้ายังมีอยู่จากเวอร์ชันก่อน
        for legacy_path in [ROUND_BACKUP_DB_FILE, f"{ROUND_BACKUP_DB_FILE}.bak"]:
            delete_file(legacy_path)

        # กันไม่ให้ reply ข้อความ "ล้างเรียบร้อย" สร้าง round_backup ใหม่กลับมาทันที
        ROUND_BACKUP_SUPPRESS_UNTIL = time.time() + 15

    deleted_count = len(deleted_files)
    skipped_count = len(skipped_files)
    error_count = len(error_files)

    sample_deleted = "\n".join(f"- {os.path.basename(x)}" for x in deleted_files[:8])
    if deleted_count > 8:
        sample_deleted += f"\n- ...อีก {deleted_count - 8} ไฟล์"
    if not sample_deleted:
        sample_deleted = "ไม่มีไฟล์ backup ให้ล้าง"

    msg = (
        "✅ ล้าง round_backups เรียบร้อย\n\n"
        f"ผู้สั่งล้าง: {clear_by or '-'}\n"
        f"โฟลเดอร์ backup: {backup_dir}\n"
        f"ลบไฟล์แล้ว: {deleted_count:,} ไฟล์\n"
        f"ขนาดรวมที่ลบ: {deleted_bytes:,} bytes\n"
        f"ข้ามไฟล์/โฟลเดอร์ที่ไม่ใช่ backup: {skipped_count:,} รายการ\n\n"
        f"รายการที่ลบ:\n{sample_deleted}\n\n"
        "หมายเหตุ: คำสั่งนี้ล้างเฉพาะไฟล์สำรอง round_backups ไม่ล้าง USERS / PROFIT / ORDER / เครดิตลูกค้า\n"
        "ระบบจะงด auto-backup ชั่วคราว 15 วินาทีเพื่อไม่ให้ไฟล์ถูกสร้างกลับทันทีหลังตอบข้อความนี้"
    )

    if error_count:
        sample_errors = "\n".join(f"- {x}" for x in error_files[:5])
        if error_count > 5:
            sample_errors += f"\n- ...อีก {error_count - 5} รายการ"
        msg += f"\n\n⚠️ ลบไม่สำเร็จ {error_count:,} รายการ:\n{sample_errors}"

    return msg

def is_add_admin_command(text: str) -> bool:
    """ตรวจคำสั่ง เพิ่มแอดมิน @ชื่อไลน์"""
    clean = (text or "").strip()
    # กันวรรณยุกต์/สระไทยหลุดนำหน้าข้อความจากคีย์บอร์ด
    clean = re.sub(r"^[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]+", "", clean)
    return re.match(r"^เพิ่มแอดมิน(?:\s+|$)", clean) is not None


def is_admin_list_command(text: str) -> bool:
    """ตรวจคำสั่ง List / เช็คแอดมิน เพื่อดูรายชื่อแอดมินทั้งหมด"""
    clean = re.sub(r"\s+", "", (text or "").strip()).lower()
    return clean in {
        "list",
        "adminlist",
        "listadmin",
        "admins",
        "admin",
        "เช็คแอดมิน",
        "เช็กแอดมิน",
        "รายชื่อแอดมิน",
        "ลิสต์แอดมิน",
        "ลิสแอดมิน",
    }


def admin_list_report() -> str:
    """แสดงรายชื่อแอดมินจาก .env และจาก admins.json"""
    rows = []
    seen = set()

    def display_admin_row(uid: str, source: str, info: dict = None):
        if not uid or uid in seen:
            return
        seen.add(uid)
        info = info or {}
        user = USERS.get(uid, {}) if isinstance(USERS, dict) else {}
        name = (
            info.get("line_name")
            or user.get("line_name")
            or user.get("name")
            or fallback_name(uid)
        )
        member_no = info.get("member_no") or user.get("member_no") or "-"
        added_at = info.get("added_at") or "-"
        added_by_name = info.get("added_by_name") or "-"
        uid_tail = uid[-8:] if len(uid) > 8 else uid
        rows.append(
            f"{len(rows) + 1}. {name}\n"
            f"   ID สมาชิก: {member_no} | ที่มา: {source} | UID: ...{uid_tail}\n"
            f"   เพิ่มเมื่อ: {added_at} | เพิ่มโดย: {added_by_name}"
        )

    for uid in sorted(ADMIN_USER_IDS):
        display_admin_row(uid, ".env")

    admins = DYNAMIC_ADMINS.get("admins", {}) if isinstance(DYNAMIC_ADMINS, dict) else {}
    if isinstance(admins, dict):
        for uid, info in sorted(admins.items(), key=lambda x: (x[1] or {}).get("line_name") or x[0]):
            display_admin_row(uid, "admins.json", info if isinstance(info, dict) else {})

    total_env = len([x for x in ADMIN_USER_IDS if x])
    total_dynamic = len(admins) if isinstance(admins, dict) else 0

    if not rows:
        return (
            "📋 รายชื่อแอดมิน\n\n"
            "ยังไม่มีแอดมินในระบบ\n"
            "ให้ตั้ง ADMIN_USER_IDS ใน .env หรือใช้คำสั่ง เพิ่มแอดมิน @ชื่อไลน์"
        )

    return (
        "📋 รายชื่อแอดมินทั้งหมด\n"
        f"รวม {len(rows)} คน | .env {total_env} คน | admins.json {total_dynamic} คน\n\n"
        + "\n\n".join(rows)
    )


def extract_mentioned_user_ids(event):
    """
    ดึง userId จาก mention ของ LINE
    LINE webhook ใช้ key จริงว่า mentionees แต่ SDK/เวอร์ชันบางตัวอาจ map ชื่อไม่เหมือนกัน
    จึงรองรับทั้ง mentionees / mentees และทั้ง userId / user_id
    """
    message = getattr(event, "message", None)
    mention = getattr(message, "mention", None)

    mentionees = None
    if mention:
        mentionees = (
            getattr(mention, "mentionees", None)
            or getattr(mention, "mentees", None)
            or (mention.get("mentionees") if isinstance(mention, dict) else None)
            or (mention.get("mentees") if isinstance(mention, dict) else None)
        )

    if not mentionees:
        return []

    user_ids = []
    for item in mentionees:
        uid = (
            getattr(item, "user_id", None)
            or getattr(item, "userId", None)
            or (item.get("userId") if isinstance(item, dict) else None)
            or (item.get("user_id") if isinstance(item, dict) else None)
        )
        mention_type = (
            getattr(item, "type", None)
            or (item.get("type") if isinstance(item, dict) else None)
            or "user"
        )
        # ข้าม mention ที่เป็น bot เองหรือไม่ใช่ user
        is_self = (
            getattr(item, "is_self", None)
            if getattr(item, "is_self", None) is not None
            else getattr(item, "isSelf", None)
        )
        if isinstance(item, dict):
            is_self = item.get("isSelf", item.get("is_self", is_self))

        if uid and mention_type == "user" and not is_self and uid not in user_ids:
            user_ids.append(uid)

    return user_ids


def add_admins_from_mentions(event, added_by_id: str):
    """เพิ่มแอดมินจากคนที่ถูกแท็กในข้อความ เพิ่มแอดมิน @ชื่อไลน์"""
    mentioned_user_ids = extract_mentioned_user_ids(event)
    if not mentioned_user_ids:
        return (
            "⚠️ เพิ่มแอดมินไม่สำเร็จ\n\n"
            "กรุณาแท็กชื่อ LINE ของคนที่ต้องการเพิ่ม เช่น\n"
            "เพิ่มแอดมิน @ชื่อไลน์\n\n"
            "หมายเหตุ: ต้องแท็กจริงใน LINE ไม่ใช่พิมพ์ @ เอง"
        )

    ids = get_source_ids(event)
    group_id = ids.get("group_id")
    room_id = ids.get("room_id")

    added_rows = []
    already_rows = []

    for target_user_id in mentioned_user_ids:
        # พยายามดึงชื่อ LINE จากกลุ่ม/ห้องเพื่อบันทึกให้อ่านง่าย
        profile = get_line_profile(target_user_id, group_id=group_id, room_id=room_id)
        display_name = getattr(profile, "display_name", None) if profile else None
        target_user = get_user(target_user_id, display_name=display_name)

        target_name = (
            (target_user or {}).get("line_name")
            or (target_user or {}).get("name")
            or fallback_name(target_user_id)
        )

        admins = DYNAMIC_ADMINS.setdefault("admins", {})
        if target_user_id in ADMIN_USER_IDS or target_user_id in admins:
            already_rows.append(f"- {target_name}")
            continue

        admins[target_user_id] = {
            "user_id": target_user_id,
            "line_name": target_name,
            "member_no": (target_user or {}).get("member_no"),
            "added_by": added_by_id,
            "added_by_name": user_display_name(added_by_id),
            "added_at": now_text(),
        }
        added_rows.append(f"- {target_name}")

    DYNAMIC_ADMINS["updated_at"] = datetime.now().isoformat()
    save_admin_db()

    lines = ["✅ เพิ่มแอดมินเรียบร้อย"]
    if added_rows:
        lines.extend(["", "แอดมินที่เพิ่ม:", *added_rows])
    if already_rows:
        lines.extend(["", "มีสิทธิ์แอดมินอยู่แล้ว:", *already_rows])

    lines.extend([
        "",
        "คำสั่งนี้บันทึกลง admins.json แล้ว",
        "แอดมินที่เพิ่มจะใช้คำสั่งแอดมินได้ทันที และยังอยู่หลังรีสตาร์ตบอท",
    ])
    return "\n".join(lines)


# ======================================================
# Concurrency guard
# ======================================================

def synchronized_state(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        with STATE_LOCK:
            return func(*args, **kwargs)
    return wrapper

# ฟังก์ชันที่แก้ STATE / เครดิต / รายการ ต้องเข้าคิวทีละคำสั่ง
create_post = synchronized_state(create_post)
handle_confirm = synchronized_state(handle_confirm)
create_match_from_pending = synchronized_state(create_match_from_pending)
request_cancel = synchronized_state(request_cancel)
approve_cancel = synchronized_state(approve_cancel)
reject_cancel = synchronized_state(reject_cancel)
cancel_no_price_only_entries = synchronized_state(cancel_no_price_only_entries)
settle_round = synchronized_state(settle_round)
settle_round_all_jow = synchronized_state(settle_round_all_jow)
handle_special_result_with_double_confirm = synchronized_state(handle_special_result_with_double_confirm)
handle_result_with_double_confirm = synchronized_state(handle_result_with_double_confirm)

# ======================================================
# Webhook
# ======================================================

@app.route("/", methods=["GET"])
def home():
    return "LINE OA bot is running."


# ======================================================
# Bank logo static route
# เสิร์ฟโลโก้ธนาคารเป็น SVG จาก Railway เอง
# LINE Flex รองรับ URL จาก domain เดียวกับ webhook แน่นอน
# ======================================================
_BANK_SVG_DATA = {
    "kbank":     ("#1B5E20", "#A5D6A7", "K"),
    "scb":       ("#4A148C", "#CE93D8", "SCB"),
    "bbl":       ("#1565C0", "#90CAF9", "BBL"),
    "ktb":       ("#006064", "#80DEEA", "KTB"),
    "ttb":       ("#BF360C", "#FFCCBC", "TTB"),
    "bay":       ("#F57F17", "#FFF176", "BAY"),
    "gsb":       ("#880E4F", "#F48FB1", "GSB"),
    "ghb":       ("#E65100", "#FFCC80", "GHB"),
    "baac":      ("#1B5E20", "#C8E6C9", "BAAC"),
    "uob":       ("#0D47A1", "#90CAF9", "UOB"),
    "cimbt":     ("#B71C1C", "#EF9A9A", "CIMB"),
    "tisco":     ("#1A237E", "#9FA8DA", "TISCO"),
    "kkp":       ("#01579B", "#81D4FA", "KKP"),
    "icbct":     ("#C62828", "#EF9A9A", "ICBC"),
    "tcd":       ("#C62828", "#EF9A9A", "TCD"),
    "lh":        ("#1565C0", "#90CAF9", "LH"),
    "isbt":      ("#1B5E20", "#A5D6A7", "IBANK"),
    "mhcb":      ("#B71C1C", "#EF9A9A", "MHB"),
    "scbt":      ("#01579B", "#81D4FA", "SC"),
    "citi":      ("#0D47A1", "#90CAF9", "CITI"),
    "bnpp":      ("#004D40", "#80CBC4", "BNP"),
    "boc":       ("#B71C1C", "#EF9A9A", "BOC"),
    "truemoney": ("#E65100", "#FFCC80", "TW"),
}

def _make_bank_svg(color: str, accent: str, abbr: str) -> bytes:
    fs = 14 if len(abbr) >= 5 else (16 if len(abbr) == 4 else (18 if len(abbr) == 3 else 24))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80" width="80" height="80">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{color}"/>
      <stop offset="100%" stop-color="{color}dd"/>
    </linearGradient>
    <clipPath id="clip"><rect width="80" height="80" rx="18"/></clipPath>
  </defs>
  <rect width="80" height="80" rx="18" fill="url(#bg)"/>
  <rect width="80" height="80" rx="18" fill="none" stroke="{accent}33" stroke-width="2"/>
  <circle cx="70" cy="15" r="20" fill="{accent}18"/>
  <text x="40" y="43" font-family="'Helvetica Neue',Arial,sans-serif"
    font-size="{fs}" font-weight="900" fill="#ffffff"
    text-anchor="middle" dominant-baseline="central"
    letter-spacing="0.5">{abbr}</text>
</svg>"""
    return svg.encode("utf-8")

@app.route("/banks/<bank_code>.png", methods=["GET"])
def bank_logo(bank_code):
    from flask import Response
    key = str(bank_code or "").lower().replace(".png", "")
    entry = _BANK_SVG_DATA.get(key)
    if not entry:
        color, accent, abbr = "#9CA3AF", "#ffffff", "?"
    else:
        color, accent, abbr = entry
    svg_bytes = _make_bank_svg(color, accent, abbr)
    return Response(svg_bytes, mimetype="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


ADMIN_PANEL_TOKEN = os.getenv("ADMIN_PANEL_TOKEN", "").strip()

def check_admin_token(req):
    token = req.args.get("token") or req.headers.get("X-Admin-Token", "")
    if not ADMIN_PANEL_TOKEN:
        return False
    return token == ADMIN_PANEL_TOKEN

ADMIN_HTML = r"""
<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OG Admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0a0a0f;
  --surface: #12121a;
  --surface2: #1a1a26;
  --surface3: #22223a;
  --border: rgba(255,255,255,0.07);
  --border2: rgba(255,255,255,0.12);
  --text: #f0f0ff;
  --text2: #9090b0;
  --text3: #5a5a7a;
  --accent: #7c6bff;
  --accent2: #5b4de0;
  --green: #22d37f;
  --green2: #16a34a;
  --red: #ff5e7a;
  --red2: #c0233a;
  --amber: #f5a623;
  --blue: #4bb8ff;
  --radius: 14px;
  --radius-sm: 8px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Sarabun', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; display: flex; }

/* Sidebar */
.sidebar {
  width: 220px; min-height: 100vh; background: var(--surface);
  border-right: 1px solid var(--border); display: flex; flex-direction: column;
  position: fixed; top: 0; left: 0; bottom: 0; z-index: 50;
}
.logo {
  padding: 24px 20px 20px; display: flex; align-items: center; gap: 10px;
  border-bottom: 1px solid var(--border);
}
.logo-icon { width: 34px; height: 34px; background: var(--accent); border-radius: 9px;
  display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }
.logo-text { font-family: 'Space Grotesk', sans-serif; font-size: 17px; font-weight: 700; color: var(--text); }
.logo-sub { font-size: 11px; color: var(--text3); margin-top: 1px; }
.nav { padding: 14px 10px; flex: 1; }
.nav-item {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: var(--radius-sm);
  cursor: pointer; color: var(--text2); font-size: 14px; font-weight: 500;
  transition: all .15s; margin-bottom: 2px; user-select: none; border: none; background: none; width: 100%; text-align: left;
}
.nav-item:hover { background: var(--surface2); color: var(--text); }
.nav-item.active { background: rgba(124,107,255,0.15); color: var(--accent); }
.nav-item .icon { font-size: 17px; width: 22px; text-align: center; }
.nav-badge { margin-left: auto; background: var(--accent); color: #fff; border-radius: 20px;
  font-size: 11px; font-weight: 600; padding: 1px 7px; }
.sidebar-footer { padding: 16px; border-top: 1px solid var(--border); }
.bot-status { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--text2); }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 6px var(--green); }

/* Main */
.main { margin-left: 220px; flex: 1; min-height: 100vh; }
.topbar {
  height: 60px; background: var(--surface); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 28px;
  position: sticky; top: 0; z-index: 40;
}
.page-title { font-family: 'Space Grotesk', sans-serif; font-size: 17px; font-weight: 600; }
.topbar-actions { display: flex; align-items: center; gap: 12px; }
.refresh-btn {
  background: var(--surface2); border: 1px solid var(--border2); color: var(--text2);
  padding: 7px 14px; border-radius: var(--radius-sm); font-size: 13px; cursor: pointer;
  transition: all .15s; font-family: 'Sarabun', sans-serif;
}
.refresh-btn:hover { background: var(--surface3); color: var(--text); }
.content { padding: 24px 28px; }

/* Stat cards */
.stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 24px; }
.stat-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 20px; position: relative; overflow: hidden;
}
.stat-card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: var(--accent-color, var(--accent));
}
.stat-label { font-size: 12px; color: var(--text3); font-weight: 500; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }
.stat-value { font-family: 'Space Grotesk', sans-serif; font-size: 28px; font-weight: 700; color: var(--accent-color, var(--text)); }
.stat-sub { font-size: 12px; color: var(--text3); margin-top: 4px; }
.stat-icon { position: absolute; top: 18px; right: 18px; font-size: 22px; opacity: .25; }

/* Section */
.section { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 20px; overflow: hidden; }
.section-header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
.section-title { font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.section-title .icon { font-size: 16px; }
.section-actions { display: flex; gap: 8px; }

/* Search */
.search-wrap { padding: 14px 20px; border-bottom: 1px solid var(--border); }
.search-input {
  width: 100%; background: var(--surface2); border: 1px solid var(--border); color: var(--text);
  padding: 9px 14px 9px 36px; border-radius: var(--radius-sm); font-size: 14px; outline: none;
  font-family: 'Sarabun', sans-serif; transition: border .15s;
}
.search-input:focus { border-color: var(--accent); }
.search-wrap { position: relative; padding: 14px 20px; border-bottom: 1px solid var(--border); }
.search-icon { position: absolute; left: 32px; top: 50%; transform: translateY(-50%); color: var(--text3); font-size: 15px; pointer-events: none; }

/* Table */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th { padding: 11px 18px; text-align: left; font-size: 12px; color: var(--text3); font-weight: 600;
  text-transform: uppercase; letter-spacing: .05em; white-space: nowrap; }
td { padding: 12px 18px; border-top: 1px solid var(--border); font-size: 14px; white-space: nowrap; }
tr:hover td { background: rgba(255,255,255,.02); }
.member-no { font-family: 'Space Grotesk', sans-serif; font-weight: 600; color: var(--accent); font-size: 13px; }
.credit-val { font-family: 'Space Grotesk', sans-serif; font-weight: 700; color: var(--green); }
.credit-val.zero { color: var(--text3); }
.name-cell { display: flex; align-items: center; gap: 10px; }
.avatar { width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700; flex-shrink: 0; }
.action-btns { display: flex; gap: 6px; }

/* Buttons */
.btn { padding: 6px 13px; border-radius: 6px; border: none; cursor: pointer; font-size: 13px;
  font-weight: 600; transition: all .15s; font-family: 'Sarabun', sans-serif; display: inline-flex; align-items: center; gap: 5px; }
.btn:hover { transform: translateY(-1px); }
.btn:active { transform: translateY(0); }
.btn-green { background: rgba(34,211,127,.15); color: var(--green); border: 1px solid rgba(34,211,127,.3); }
.btn-green:hover { background: rgba(34,211,127,.25); }
.btn-red { background: rgba(255,94,122,.15); color: var(--red); border: 1px solid rgba(255,94,122,.3); }
.btn-red:hover { background: rgba(255,94,122,.25); }
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { background: var(--accent2); }
.btn-ghost { background: var(--surface2); color: var(--text2); border: 1px solid var(--border2); }
.btn-ghost:hover { background: var(--surface3); }
.btn-sm { padding: 4px 10px; font-size: 12px; }

/* Badge */
.badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
.badge-green { background: rgba(34,211,127,.15); color: var(--green); }
.badge-red { background: rgba(255,94,122,.15); color: var(--red); }
.badge-amber { background: rgba(245,166,35,.15); color: var(--amber); }
.badge-blue { background: rgba(75,184,255,.15); color: var(--blue); }
.badge-purple { background: rgba(124,107,255,.15); color: var(--accent); }

/* Modal */
.overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 100; align-items: center; justify-content: center; backdrop-filter: blur(4px); }
.overlay.open { display: flex; }
.modal-box {
  background: var(--surface); border: 1px solid var(--border2); border-radius: 18px;
  padding: 28px; width: 380px; max-width: 95vw; animation: pop .2s ease;
}
@keyframes pop { from { transform: scale(.95); opacity: 0; } to { transform: scale(1); opacity: 1; } }
.modal-title { font-family: 'Space Grotesk', sans-serif; font-size: 18px; font-weight: 700; margin-bottom: 20px; }
.modal-field { margin-bottom: 16px; }
.modal-field label { font-size: 12px; color: var(--text3); font-weight: 600; text-transform: uppercase; letter-spacing: .05em; display: block; margin-bottom: 6px; }
.modal-input {
  width: 100%; background: var(--surface2); border: 1px solid var(--border); color: var(--text);
  padding: 10px 14px; border-radius: var(--radius-sm); font-size: 15px; outline: none;
  font-family: 'Sarabun', sans-serif; transition: border .15s;
}
.modal-input:focus { border-color: var(--accent); }
.modal-input[readonly] { color: var(--text2); cursor: default; }
.modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 4px; }

/* Toast */
.toast-wrap { position: fixed; bottom: 24px; right: 24px; z-index: 200; display: flex; flex-direction: column; gap: 8px; }
.toast {
  background: var(--surface); border: 1px solid var(--border2); color: var(--text);
  padding: 12px 18px; border-radius: 10px; font-size: 14px; font-weight: 500;
  display: flex; align-items: center; gap: 10px; min-width: 220px;
  animation: slideIn .25s ease; box-shadow: 0 4px 24px rgba(0,0,0,.4);
}
@keyframes slideIn { from { transform: translateX(40px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
.toast-success { border-left: 3px solid var(--green); }
.toast-error { border-left: 3px solid var(--red); }

/* Tabs */
.page { display: none; }
.page.active { display: block; }

/* Slip history */
.slip-ref { font-family: 'Space Grotesk', sans-serif; font-size: 11px; color: var(--text3); max-width: 140px; overflow: hidden; text-overflow: ellipsis; }
.amount-cell { font-family: 'Space Grotesk', sans-serif; font-weight: 600; color: var(--amber); }

/* Profit */
.profit-total { font-family: 'Space Grotesk', sans-serif; font-size: 36px; font-weight: 700; color: var(--green); }
.profit-row-camp { font-weight: 600; color: var(--blue); }

/* Loading */
.loading-row td { text-align: center; padding: 40px; color: var(--text3); }
.spin { display: inline-block; animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Empty */
.empty { text-align: center; padding: 48px 20px; color: var(--text3); }
.empty-icon { font-size: 36px; margin-bottom: 8px; }

/* Scrollbar */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--surface3); border-radius: 10px; }

@media (max-width: 900px) {
  .sidebar { width: 60px; }
  .logo-text, .logo-sub, .nav-item span:not(.icon), .nav-badge, .bot-status span { display: none; }
  .main { margin-left: 60px; }
  .stats-grid { grid-template-columns: 1fr 1fr; }
  .nav-item { justify-content: center; padding: 12px; }
}
@media (max-width: 600px) {
  .stats-grid { grid-template-columns: 1fr; }
  .content { padding: 16px; }
}
</style>
</head>
<body>

<!-- Sidebar -->
<aside class="sidebar">
  <div class="logo">
    <div class="logo-icon">💎</div>
    <div>
      <div class="logo-text">OG Admin</div>
      <div class="logo-sub">Management Panel</div>
    </div>
  </div>
  <nav class="nav">
    <button class="nav-item active" onclick="switchPage('dashboard')">
      <span class="icon">📊</span><span>ภาพรวม</span>
    </button>
    <button class="nav-item" onclick="switchPage('members')">
      <span class="icon">👥</span><span>สมาชิก</span>
      <span class="nav-badge" id="nav-members-count">-</span>
    </button>
    <button class="nav-item" onclick="switchPage('slips')">
      <span class="icon">🧾</span><span>ประวัติสลิป</span>
    </button>
    <button class="nav-item" onclick="switchPage('profit')">
      <span class="icon">💰</span><span>ยอดกำไร</span>
    </button>
  </nav>
  <div class="sidebar-footer">
    <div class="bot-status">
      <div class="status-dot"></div>
      <span>Bot Online</span>
    </div>
  </div>
</aside>

<!-- Main -->
<main class="main">
  <div class="topbar">
    <div class="page-title" id="page-title">ภาพรวม</div>
    <div class="topbar-actions">
      <button class="refresh-btn" onclick="refreshAll()">🔄 รีเฟรช</button>
    </div>
  </div>

  <!-- ── DASHBOARD ── -->
  <div class="content page active" id="page-dashboard">
    <div class="stats-grid">
      <div class="stat-card" style="--accent-color: var(--accent)">
        <div class="stat-icon">👥</div>
        <div class="stat-label">สมาชิกทั้งหมด</div>
        <div class="stat-value" id="s-total">-</div>
        <div class="stat-sub" id="s-active">โหลด...</div>
      </div>
      <div class="stat-card" style="--accent-color: var(--green)">
        <div class="stat-icon">💳</div>
        <div class="stat-label">เครดิตรวม</div>
        <div class="stat-value" id="s-credit">-</div>
        <div class="stat-sub">เครดิตในระบบ</div>
      </div>
      <div class="stat-card" style="--accent-color: var(--amber)">
        <div class="stat-icon">🧾</div>
        <div class="stat-label">สลิปทั้งหมด</div>
        <div class="stat-value" id="s-slips">-</div>
        <div class="stat-sub" id="s-slips-amount">ยอดรวม</div>
      </div>
      <div class="stat-card" style="--accent-color: var(--blue)">
        <div class="stat-icon">💰</div>
        <div class="stat-label">กำไรสะสม</div>
        <div class="stat-value" id="s-profit">-</div>
        <div class="stat-sub" id="s-profit-rounds">รอบ</div>
      </div>
    </div>

    <!-- Top members -->
    <div class="section">
      <div class="section-header">
        <div class="section-title"><span class="icon">🏆</span> Top เครดิตสูงสุด</div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>#ID</th><th>ชื่อ</th><th>เครดิต</th><th>จัดการ</th></tr></thead>
          <tbody id="top-table"><tr class="loading-row"><td colspan="4"><span class="spin">⟳</span> กำลังโหลด...</td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- Recent slips -->
    <div class="section">
      <div class="section-header">
        <div class="section-title"><span class="icon">🕐</span> สลิปล่าสุด</div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>สมาชิก</th><th>ยอดโอน</th><th>เครดิต</th><th>เวลา</th></tr></thead>
          <tbody id="recent-slips-table"><tr class="loading-row"><td colspan="4"><span class="spin">⟳</span></td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── MEMBERS ── -->
  <div class="content page" id="page-members">
    <div class="section">
      <div class="section-header">
        <div class="section-title"><span class="icon">👥</span> รายชื่อสมาชิก</div>
        <div class="section-actions">
          <button class="btn btn-primary btn-sm" onclick="openAddMember()">➕ เพิ่มสมาชิก</button>
        </div>
      </div>
      <div class="search-wrap">
        <span class="search-icon">🔍</span>
        <input class="search-input" id="member-search" placeholder="ค้นหาชื่อ หรือ ID สมาชิก..." oninput="filterMembers()">
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>ชื่อ</th><th>เครดิต</th><th>สถานะ</th><th>จัดการ</th></tr></thead>
          <tbody id="member-table"><tr class="loading-row"><td colspan="5"><span class="spin">⟳</span> กำลังโหลด...</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── SLIPS ── -->
  <div class="content page" id="page-slips">
    <div class="section">
      <div class="section-header">
        <div class="section-title"><span class="icon">🧾</span> ประวัติการเติมเครดิตจากสลิป</div>
      </div>
      <div class="search-wrap">
        <span class="search-icon">🔍</span>
        <input class="search-input" id="slip-search" placeholder="ค้นหาชื่อสมาชิก หรือยอดโอน..." oninput="filterSlips()">
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>สมาชิก</th><th>ยอดโอน</th><th>เครดิตที่ได้</th><th>เลข Ref</th><th>เวลา</th></tr></thead>
          <tbody id="slip-table"><tr class="loading-row"><td colspan="5"><span class="spin">⟳</span></td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── PROFIT ── -->
  <div class="content page" id="page-profit">
    <div class="stats-grid" style="grid-template-columns: repeat(2,1fr)">
      <div class="stat-card" style="--accent-color: var(--green)">
        <div class="stat-icon">💰</div>
        <div class="stat-label">กำไรสะสมทั้งหมด</div>
        <div class="stat-value" id="p-total">-</div>
      </div>
      <div class="stat-card" style="--accent-color: var(--blue)">
        <div class="stat-icon">🎯</div>
        <div class="stat-label">จำนวนรอบ</div>
        <div class="stat-value" id="p-rounds">-</div>
        <div class="stat-sub">รอบที่มีประวัติกำไร</div>
      </div>
    </div>
    <div class="section">
      <div class="section-header">
        <div class="section-title"><span class="icon">📜</span> ประวัติรอบ</div>
      </div>
      <div class="search-wrap">
        <span class="search-icon">🔍</span>
        <input class="search-input" id="profit-search" placeholder="ค้นหาค่าย หรือรอบ..." oninput="filterProfit()">
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ค่าย</th><th>ผล</th><th>กำไร</th><th>รอบ</th><th>เวลา</th></tr></thead>
          <tbody id="profit-table"><tr class="loading-row"><td colspan="5"><span class="spin">⟳</span></td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

</main>

<!-- Modal: เพิ่ม/แก้ไขเครดิต -->
<div class="overlay" id="modal-credit">
  <div class="modal-box">
    <div class="modal-title" id="modal-credit-title">จัดการเครดิต</div>
    <div class="modal-field">
      <label>สมาชิก</label>
      <input class="modal-input" id="mc-name" readonly>
    </div>
    <div class="modal-field">
      <label>เครดิตปัจจุบัน</label>
      <input class="modal-input" id="mc-current" readonly>
    </div>
    <div class="modal-field">
      <label>จำนวน</label>
      <input class="modal-input" id="mc-amount" type="number" min="1" placeholder="ใส่จำนวน...">
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal('modal-credit')">ยกเลิก</button>
      <button class="btn btn-primary" id="mc-confirm" onclick="confirmCredit()">ยืนยัน</button>
    </div>
  </div>
</div>

<!-- Modal: เพิ่มสมาชิก -->
<div class="overlay" id="modal-add-member">
  <div class="modal-box">
    <div class="modal-title">➕ เพิ่มสมาชิก</div>
    <div class="modal-field">
      <label>LINE User ID</label>
      <input class="modal-input" id="am-userid" placeholder="Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx">
    </div>
    <div class="modal-field">
      <label>ชื่อ (ไม่บังคับ)</label>
      <input class="modal-input" id="am-name" placeholder="ชื่อสมาชิก">
    </div>
    <div class="modal-field">
      <label>เครดิตเริ่มต้น</label>
      <input class="modal-input" id="am-credit" type="number" min="0" value="0">
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal('modal-add-member')">ยกเลิก</button>
      <button class="btn btn-primary" onclick="confirmAddMember()">เพิ่มสมาชิก</button>
    </div>
  </div>
</div>

<!-- Toasts -->
<div class="toast-wrap" id="toast-wrap"></div>

<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
let allMembers = [], allSlips = [], allProfit = [];
let creditUserId = '', creditMode = '';
const PAGES = { dashboard: 'ภาพรวม', members: 'สมาชิก', slips: 'ประวัติสลิป', profit: 'ยอดกำไร' };

/* ── Page switching ── */
function switchPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => {
    if (n.getAttribute('onclick') && n.getAttribute('onclick').includes("'" + name + "'")) n.classList.add('active');
  });
  document.getElementById('page-title').textContent = PAGES[name];
}

/* ── Avatar color ── */
const COLORS = ['#7c6bff','#22d37f','#4bb8ff','#f5a623','#ff5e7a','#a78bfa','#34d399'];
function avatarColor(name) { let h = 0; for (let c of (name||'')) h = (h * 31 + c.charCodeAt(0)) % COLORS.length; return COLORS[h]; }
function avatar(name) {
  const c = avatarColor(name); const ch = (name||'?')[0].toUpperCase();
  return `<div class="avatar" style="background:${c}22;color:${c}">${ch}</div>`;
}

/* ── API ── */
async function api(path, method='GET', body=null) {
  const opts = { method, headers: {'X-Admin-Token': TOKEN, 'Content-Type': 'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path + (path.includes('?') ? '&' : '?') + 'token=' + TOKEN, opts);
  return r.json();
}

/* ── Load all data ── */
async function loadDashboard() {
  const [u, s, p] = await Promise.all([api('/admin/api/users'), api('/admin/api/slips'), api('/admin/api/profit')]);
  allMembers = u.users || []; allSlips = s.slips || []; allProfit = p.rounds || [];

  // Stats
  document.getElementById('s-total').textContent = (u.total||0).toLocaleString();
  document.getElementById('s-active').textContent = `มีเครดิต ${(u.active_users||0).toLocaleString()} คน`;
  document.getElementById('s-credit').textContent = (u.total_credit||0).toLocaleString();
  document.getElementById('s-slips').textContent = allSlips.length.toLocaleString();
  const totalBaht = allSlips.reduce((a,x) => a + parseFloat(x.amount_baht||0), 0);
  document.getElementById('s-slips-amount').textContent = `รวม ${totalBaht.toLocaleString('th', {minimumFractionDigits:0})} บาท`;
  document.getElementById('s-profit').textContent = (p.total_profit||0).toLocaleString();
  document.getElementById('s-profit-rounds').textContent = `${allProfit.length} รอบ`;
  document.getElementById('nav-members-count').textContent = allMembers.length;

  // Top members
  const top = [...allMembers].sort((a,b) => b.credit - a.credit).slice(0, 8);
  document.getElementById('top-table').innerHTML = top.length
    ? top.map(u => `<tr>
        <td><span class="member-no">#${u.member_no}</span></td>
        <td><div class="name-cell">${avatar(u.name)}<span>${esc(u.name)}</span></div></td>
        <td><span class="credit-val${u.credit<=0?' zero':''}">${u.credit.toLocaleString()}</span></td>
        <td><div class="action-btns">
          <button class="btn btn-green btn-sm" onclick="openCredit('${u.user_id}','${esc(u.name)}',${u.credit},'add')">+</button>
          <button class="btn btn-red btn-sm" onclick="openCredit('${u.user_id}','${esc(u.name)}',${u.credit},'sub')">−</button>
        </div></td></tr>`).join('')
    : '<tr><td colspan="4" class="empty"><div class="empty-icon">👥</div>ยังไม่มีสมาชิก</td></tr>';

  // Recent slips
  const recent = [...allSlips].sort((a,b) => (b.created_at||'').localeCompare(a.created_at||'')).slice(0,8);
  document.getElementById('recent-slips-table').innerHTML = recent.length
    ? recent.map(s => `<tr>
        <td><div class="name-cell">${avatar(s.line_name)}<span>${esc(s.line_name||'?')}</span></div></td>
        <td><span class="amount-cell">${parseFloat(s.amount_baht||0).toLocaleString()} บ.</span></td>
        <td><span class="credit-val">${(s.credit_added||0).toLocaleString()}</span></td>
        <td><span style="color:var(--text3);font-size:12px">${(s.created_at||'').substring(0,16)}</span></td></tr>`).join('')
    : '<tr><td colspan="4" class="empty"><div class="empty-icon">🧾</div>ยังไม่มีสลิป</td></tr>';

  // Populate other tabs
  renderMembers(allMembers);
  renderSlips(allSlips);
  renderProfit(allProfit, p.total_profit||0);

  // Profit stats
  document.getElementById('p-total').textContent = (p.total_profit||0).toLocaleString();
  document.getElementById('p-rounds').textContent = allProfit.length.toLocaleString();
}

/* ── Members ── */
function renderMembers(list) {
  document.getElementById('member-table').innerHTML = list.length
    ? list.map(u => `<tr>
        <td><span class="member-no">#${u.member_no}</span></td>
        <td><div class="name-cell">${avatar(u.name)}<span>${esc(u.name)}</span></div></td>
        <td><span class="credit-val${u.credit<=0?' zero':''}">${u.credit.toLocaleString()}</span></td>
        <td><span class="badge ${u.credit>0?'badge-green':'badge-red'}">${u.credit>0?'มีเครดิต':'ไม่มีเครดิต'}</span></td>
        <td><div class="action-btns">
          <button class="btn btn-green btn-sm" onclick="openCredit('${u.user_id}','${esc(u.name)}',${u.credit},'add')">➕ บวก</button>
          <button class="btn btn-red btn-sm" onclick="openCredit('${u.user_id}','${esc(u.name)}',${u.credit},'sub')">➖ ลบ</button>
        </div></td></tr>`).join('')
    : '<tr><td colspan="5" class="empty"><div class="empty-icon">👥</div>ไม่พบสมาชิก</td></tr>';
}
function filterMembers() {
  const q = document.getElementById('member-search').value.toLowerCase();
  renderMembers(allMembers.filter(u => (u.name||'').toLowerCase().includes(q) || String(u.member_no).includes(q)));
}

/* ── Slips ── */
function renderSlips(list) {
  const sorted = [...list].sort((a,b) => (b.created_at||'').localeCompare(a.created_at||''));
  document.getElementById('slip-table').innerHTML = sorted.length
    ? sorted.map(s => `<tr>
        <td><div class="name-cell">${avatar(s.line_name)}<span>${esc(s.line_name||'?')} <span class="badge badge-purple">#${s.member_no||'-'}</span></span></div></td>
        <td><span class="amount-cell">${parseFloat(s.amount_baht||0).toLocaleString()} บาท</span></td>
        <td><span class="credit-val">${(s.credit_added||0).toLocaleString()}</span></td>
        <td><span class="slip-ref" title="${esc(s.slip_ref||'')}">${(s.slip_ref||'-').substring(0,18)}…</span></td>
        <td><span style="color:var(--text3);font-size:12px">${(s.created_at||'').substring(0,16)}</span></td></tr>`).join('')
    : '<tr><td colspan="5" class="empty"><div class="empty-icon">🧾</div>ยังไม่มีประวัติสลิป</td></tr>';
}
function filterSlips() {
  const q = document.getElementById('slip-search').value.toLowerCase();
  renderSlips(allSlips.filter(s => (s.line_name||'').toLowerCase().includes(q) || String(s.amount_baht||'').includes(q)));
}

/* ── Profit ── */
function renderProfit(list, total) {
  const sorted = [...list].sort((a,b) => (b.created_at||'').localeCompare(a.created_at||''));
  document.getElementById('profit-table').innerHTML = sorted.length
    ? sorted.map(r => `<tr>
        <td><span class="profit-row-camp">${esc(r.camp_name||'-')}</span></td>
        <td><span class="badge badge-blue">${r.result_value??'-'}</span></td>
        <td><span class="credit-val">+${(r.profit_amount||0).toLocaleString()}</span></td>
        <td><span style="color:var(--text3);font-size:12px">${(r.round_id||'-').substring(0,12)}</span></td>
        <td><span style="color:var(--text3);font-size:12px">${(r.created_at||'').substring(0,16)}</span></td></tr>`).join('')
    : '<tr><td colspan="5" class="empty"><div class="empty-icon">💰</div>ยังไม่มีประวัติกำไร</td></tr>';
}
function filterProfit() {
  const q = document.getElementById('profit-search').value.toLowerCase();
  renderProfit(allProfit.filter(r => (r.camp_name||'').toLowerCase().includes(q) || String(r.round_id||'').includes(q)));
}

/* ── Credit modal ── */
function openCredit(userId, name, current, mode) {
  creditUserId = userId; creditMode = mode;
  const isAdd = mode === 'add';
  document.getElementById('modal-credit-title').textContent = isAdd ? '💚 บวกเครดิต' : '❤️ ลบเครดิต';
  document.getElementById('mc-name').value = name;
  document.getElementById('mc-current').value = current.toLocaleString() + ' เครดิต';
  document.getElementById('mc-amount').value = '';
  document.getElementById('mc-confirm').style.background = isAdd ? 'var(--green2)' : 'var(--red2)';
  document.getElementById('mc-confirm').textContent = isAdd ? 'บวกเครดิต' : 'ลบเครดิต';
  openModal('modal-credit');
  setTimeout(() => document.getElementById('mc-amount').focus(), 150);
}
async function confirmCredit() {
  const amount = parseInt(document.getElementById('mc-amount').value);
  if (!amount || amount <= 0) { toast('ใส่จำนวนให้ถูกต้อง', 'error'); return; }
  const r = await api('/admin/api/credit', 'POST', {user_id: creditUserId, amount, mode: creditMode});
  if (r.ok) {
    closeModal('modal-credit');
    toast(`${creditMode==='add'?'บวก':'ลบ'} ${amount.toLocaleString()} เครดิตสำเร็จ ✓`);
    await loadDashboard();
  } else toast(r.error || 'เกิดข้อผิดพลาด', 'error');
}

/* ── Add member modal ── */
function openAddMember() { openModal('modal-add-member'); }
async function confirmAddMember() {
  const userId = document.getElementById('am-userid').value.trim();
  const name = document.getElementById('am-name').value.trim();
  const credit = parseInt(document.getElementById('am-credit').value||0);
  if (!userId) { toast('ใส่ LINE User ID', 'error'); return; }
  const r = await api('/admin/api/add_member', 'POST', {user_id: userId, name, credit});
  if (r.ok) { closeModal('modal-add-member'); toast('เพิ่มสมาชิกสำเร็จ ✓'); await loadDashboard(); }
  else toast(r.error || 'เกิดข้อผิดพลาด', 'error');
}

/* ── Modal helpers ── */
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
document.querySelectorAll('.overlay').forEach(o => o.addEventListener('click', e => { if (e.target === o) o.classList.remove('open'); }));

/* ── Toast ── */
function toast(msg, type='success') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = (type==='success'?'✓':'✕') + ' ' + msg;
  document.getElementById('toast-wrap').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

/* ── Refresh ── */
async function refreshAll() { await loadDashboard(); toast('รีเฟรชข้อมูลแล้ว'); }

/* ── Utils ── */
function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }

/* ── Keyboard ── */
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.querySelectorAll('.overlay.open').forEach(o => o.classList.remove('open'));
  if (e.key === 'Enter' && document.getElementById('modal-credit').classList.contains('open')) confirmCredit();
});

loadDashboard();
</script>
</body>
</html>"""

@app.route("/admin", methods=["GET"])
def admin_panel():
    if not check_admin_token(request):
        return "Unauthorized", 401
    return ADMIN_HTML


@app.route("/admin/api/users", methods=["GET"])
def admin_api_users():
    if not check_admin_token(request):
        return {"error": "Unauthorized"}, 401
    with STATE_LOCK:
        users_list = []
        for uid, u in USERS.items():
            users_list.append({
                "user_id": uid,
                "member_no": u.get("member_no", 0),
                "name": u.get("name") or u.get("line_name") or uid,
                "credit": int(u.get("credit", 0) or 0),
            })
        users_list.sort(key=lambda x: x["member_no"])
        total_credit = sum(u["credit"] for u in users_list)
        active = sum(1 for u in users_list if u["credit"] > 0)
    return {
        "ok": True,
        "users": users_list,
        "total": len(users_list),
        "total_credit": total_credit,
        "active_users": active,
    }


@app.route("/admin/api/credit", methods=["POST"])
def admin_api_credit():
    if not check_admin_token(request):
        return {"error": "Unauthorized"}, 401
    data = request.get_json() or {}
    user_id = data.get("user_id", "").strip()
    amount = int(data.get("amount", 0) or 0)
    mode = data.get("mode", "add")
    if not user_id or amount <= 0:
        return {"ok": False, "error": "ข้อมูลไม่ครบ"}, 400
    with STATE_LOCK:
        user = USERS.get(user_id)
        if not user:
            return {"ok": False, "error": "ไม่พบสมาชิก"}, 404
        old = int(user.get("credit", 0) or 0)
        user["credit"] = old + amount if mode == "add" else max(0, old - amount)
        try:
            save_user_db()
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500
    return {"ok": True, "new_credit": user["credit"]}


@app.route("/admin/api/add_member", methods=["POST"])
def admin_api_add_member():
    if not check_admin_token(request):
        return {"error": "Unauthorized"}, 401
    data = request.get_json() or {}
    user_id = data.get("user_id", "").strip()
    name = data.get("name", "").strip()
    credit = int(data.get("credit", 0) or 0)
    if not user_id:
        return {"ok": False, "error": "ต้องระบุ LINE User ID"}, 400
    with STATE_LOCK:
        if user_id in USERS:
            return {"ok": False, "error": "มีสมาชิก User ID นี้แล้ว"}, 400
        member_no = max((u.get("member_no", 0) for u in USERS.values()), default=0) + 1
        USERS[user_id] = {
            "user_id": user_id,
            "member_no": member_no,
            "name": name or f"สมาชิก#{member_no}",
            "line_name": name or f"สมาชิก#{member_no}",
            "credit": max(0, credit),
            "created_at": datetime.now().isoformat(),
        }
        try:
            save_user_db()
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500
    return {"ok": True, "member_no": member_no}


@app.route("/admin/api/slips", methods=["GET"])
def admin_api_slips():
    if not check_admin_token(request):
        return {"error": "Unauthorized"}, 401
    with STATE_LOCK:
        slips_dict = SLIP_TOPUPS.get("slips", {})
        slips_list = []
        for ref, s in slips_dict.items():
            slips_list.append({
                "slip_ref": ref,
                "member_no": s.get("member_no"),
                "line_name": s.get("line_name") or s.get("name") or "-",
                "amount_baht": s.get("amount_baht", "0"),
                "credit_added": s.get("credit_added", 0),
                "created_at": s.get("created_at", ""),
            })
    slips_list.sort(key=lambda x: x["created_at"], reverse=True)
    total_baht = sum(float(s["amount_baht"] or 0) for s in slips_list)
    return {"ok": True, "slips": slips_list, "total": len(slips_list), "total_baht": total_baht}


@app.route("/admin/api/profit", methods=["GET"])
def admin_api_profit():
    if not check_admin_token(request):
        return {"error": "Unauthorized"}, 401
    with STATE_LOCK:
        rounds = list(PROFIT.get("rounds", []) or [])
        total = int(PROFIT.get("total_profit", 0) or 0)
    rounds.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"ok": True, "rounds": rounds, "total_profit": total}


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(FollowEvent)
def handle_follow(event):
    """
    เมื่อมีคนแอด OA เป็นเพื่อน:
    - เก็บ UID
    - ดึงชื่อ LINE
    - mark is_friend=True
    """
    ids = get_source_ids(event)
    user_id = ids["user_id"]

    user = get_user(user_id)
    if user:
        profile = get_line_profile(user_id)
        if profile:
            display_name = getattr(profile, "display_name", None)
            picture_url = getattr(profile, "picture_url", None)

            if display_name:
                user["line_name"] = display_name
                user["name"] = display_name

            if picture_url:
                user["picture_url"] = picture_url

        user["is_friend"] = True
        user["friend_verified_at"] = now_text()
        user["friend_verified_by"] = "follow_event"
        user["last_profile_at"] = int(time.time())
        user["last_seen_at"] = now_text()
        save_user_db()


@handler.add(JoinEvent)
def handle_join(event):
    # เมื่อบอทถูกเชิญเข้ากลุ่ม เก็บ groupId ไว้ใน log
    ids = get_source_ids(event)
    print(f"BOT JOINED source_type={ids.get('source_type')} group_id={ids.get('group_id')} room_id={ids.get('room_id')}")


@handler.add(MemberJoinedEvent)
def handle_member_joined(event):
    """
    เมื่อมีสมาชิกเข้ากลุ่ม ถ้า LINE ส่ง userId มา จะพยายามเก็บ UID และชื่อไว้
    """
    ids = get_source_ids(event)
    group_id = ids.get("group_id")
    room_id = ids.get("room_id")

    joined_members = getattr(event, "joined", None)
    members = getattr(joined_members, "members", []) if joined_members else []

    for member in members:
        user_id = getattr(member, "user_id", None)
        if not user_id:
            continue

        user = get_user(user_id)
        profile = get_line_profile(user_id, group_id=group_id, room_id=room_id)
        if profile:
            display_name = getattr(profile, "display_name", None)
            picture_url = getattr(profile, "picture_url", None)

            if display_name:
                user["line_name"] = display_name
                user["name"] = display_name

            if picture_url:
                user["picture_url"] = picture_url

            user["last_profile_at"] = int(time.time())

        user["last_seen_at"] = now_text()

    save_user_db()


# ======================================================
# Fast message filter for busy groups
# ======================================================



def is_credit_check_mention_command(text: str) -> bool:
    """ตรวจคำสั่ง C @ชื่อไลน์ สำหรับแอดมินเช็กเครดิตสมาชิกจาก mention"""
    raw = (text or "").strip()
    # กันวรรณยุกต์/สระไทยหลุดนำหน้าข้อความจากคีย์บอร์ด
    raw = re.sub(r"^[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]+", "", raw)
    # รับทั้ง C @ชื่อ, C@ชื่อ, c @ชื่อ แต่ไม่ชน CALL / CK / CR
    return re.match(r"^[Cc](?:\s+|@)", raw) is not None


def credit_check_mentions_report(event) -> str:
    """แสดงชื่อ LINE / ID สมาชิก / ยอดเงิน ของ user ที่ถูกแท็กด้วยคำสั่ง C @ชื่อไลน์"""
    mentioned_user_ids = extract_mentioned_user_ids(event)
    if not mentioned_user_ids:
        return (
            "⚠️ เช็กเครดิตไม่สำเร็จ\n\n"
            "กรุณาแท็กชื่อ LINE ของคนที่ต้องการเช็ก เช่น\n"
            "C @ชื่อไลน์\n\n"
            "หมายเหตุ: ต้องแท็กจริงใน LINE ไม่ใช่พิมพ์ @ เอง"
        )

    ids = get_source_ids(event)
    group_id = ids.get("group_id")
    room_id = ids.get("room_id")

    lines = ["🔎 เช็กข้อมูลสมาชิก", ""]

    for target_user_id in mentioned_user_ids:
        # พยายามดึงชื่อ LINE สดจากกลุ่ม/ห้อง เพื่อให้ชื่อที่แสดงตรงกับ LINE ปัจจุบัน
        profile = get_line_profile(target_user_id, group_id=group_id, room_id=room_id)
        display_name = getattr(profile, "display_name", None) if profile else None

        with STATE_LOCK:
            target_user = get_user(target_user_id, display_name=display_name)

            if display_name:
                target_user["line_name"] = display_name
                target_user["name"] = display_name

            picture_url = getattr(profile, "picture_url", None) if profile else None
            if picture_url:
                target_user["picture_url"] = picture_url

            target_user["last_seen_at"] = now_text()
            save_user_db()

            target_name = target_user.get("line_name") or target_user.get("name") or fallback_name(target_user_id)
            member_no = target_user.get("member_no")
            credit = user_credit_amount(target_user)

        lines.append(f"ชื่อ LINE: {target_name}")
        lines.append(f"ID: {member_no}")
        lines.append(f"ยอดเงิน: {credit:,} บาท")
        lines.append("")

    return "\n".join(lines).rstrip()

def is_round_control_command_text(text: str, user_id: str = None) -> bool:
    """
    คืน True เฉพาะข้อความที่เป็นคำสั่งควบคุมรอบจริง ๆ
    ใช้ใน quiet mode เพื่อกันบอทไปสนใจข้อความคุยเล่นในกลุ่ม
    """
    raw = (text or "").strip()
    scoped = extract_base_scoped_command(raw)
    if scoped or is_camp_scoped_round_command(raw):
        return is_admin(user_id or "")
    clean = re.sub(r"\s+", "", raw)
    upper = raw.upper()

    # คำสั่งตรวจสอบสถานะ/เคลียร์รอบ ใช้ได้เฉพาะแอดมินในกลุ่ม
    if upper in {"CK", "CR"} or clean.upper() in {"CKรวม", "CKALL"}:
        return is_admin(user_id or "")

    # คำว่า ยืนยัน ต้องผ่าน quiet mode เมื่อมี CR หรือราคาช่างที่รอยืนยัน
    if is_confirm_price_command(raw) and (has_pending_round_clear() or STATE.get("pending_price")):
        return is_admin(user_id or "")

    # คำสั่งควบคุมรอบทั้งหมดให้ผ่านเฉพาะแอดมินเท่านั้น
    if not is_admin(user_id or ""):
        return False

    if parse_open_command(raw):
        return True
    if parse_change_camp_command(raw):
        return True
    if raw == "ปิด":
        return True
    if is_continue_round_command(raw):
        return True
    if parse_no_price_command(raw):
        return True
    if parse_base_price(raw):
        return True
    if parse_two_digit_start_command(raw) is not None:
        return True
    if parse_special_result_command(raw) is not None:
        return True
    if parse_result_command(raw) is not None:
        return True
    if parse_rollback_result_command(raw) is not None:
        return True
    if is_result_like_command(raw):
        return True

    # ยืนยันราคาช่างพิเศษ เช่น ราคาช่าง ไม่ต่อย / ไม่ตี
    if is_confirm_price_command(raw) and STATE.get("pending_price"):
        return True

    return False


def is_backoffice_relevant_text(text: str, user_id: str = None) -> bool:
    """ข้อความที่ควรให้บอทสนใจในกลุ่มหลังบ้านเท่านั้น"""
    raw = (text or "").strip()
    scoped = extract_base_scoped_command(raw)
    if scoped or is_camp_scoped_round_command(raw):
        return True
    clean = re.sub(r"\s+", "", raw)
    upper = raw.upper()

    if upper in {"GETID", "UID"}:
        return True

    # หลังบ้าน/แอดมินยังใช้คำสั่งจัดการระบบได้
    if is_admin_help_request(raw):
        return True
    # คำสั่ง บช/บัญชี และคำสั่งเกี่ยวกับบัญชี ห้ามใช้ในกลุ่มหลังบ้าน
    # จึงไม่ปล่อยผ่าน quiet mode สำหรับหลังบ้าน/คำสั่งแอดมิน
    if is_add_admin_command(raw):
        return True
    if is_admin_list_command(raw):
        return True
    if is_credit_check_mention_command(raw):
        return True
    if upper in {"UIDLIST", "CALL", "CK", "CR"} or clean.upper() in {"CKรวม", "CKALL"}:
        return True
    if is_listplay_command(raw):
        return True
    if is_scoreboard_command(raw):
        return True
    if clean.lower() in {"ยอดกำไร", "กำไร", "profit", "ล้างกำไร", "ล้างกำร"}:
        return True
    if parse_reset_order_command(raw) is not None:
        return True
    if is_clear_round_backups_command(raw):
        return True
    if parse_credit_command(raw):
        return True
    if upper == "CLEAR ALL":
        return True

    return False


def should_process_text_message(event, text: str) -> bool:
    """
    ตัวกรองด่านแรก:
    - แชทส่วนตัว: ให้ทำงานตามปกติ เพราะใช้เช็คยอด/ส่งสลิป/ดูรายการ
    - หลังบ้าน: สนใจเฉพาะคำสั่งหลังบ้าน
    - หน้าบ้าน: สนใจเฉพาะแผลเล่น, การติดแบบ reply, และคำสั่งรอบของแอดมิน
    - กลุ่มอื่น: ให้ตอบเฉพาะ GETID เพื่อเอาไอดีไปตั้งค่า
    """
    if not QUIET_GROUP_MODE:
        return True

    raw = (text or "").strip()
    upper = raw.upper()
    user_id = getattr(event.source, "user_id", None)

    if is_private_chat(event):
        return True

    # ให้ใช้ GETID ได้ทุกกลุ่มเพื่อเอา groupId/roomId ไปใส่ .env
    if upper == "GETID":
        return True

    # คำสั่งหลังบ้านของแอดมินให้ผ่านได้ทุกกลุ่ม
    # กันเคส BACKOFFICE_GROUP_ID ใน .env ยังไม่ตรง/ยังไม่ได้ restart แล้วบอทเงียบใน quiet mode
    if is_admin(user_id) and is_backoffice_relevant_text(raw, user_id=user_id):
        return True

    if is_backoffice_chat(event):
        return is_backoffice_relevant_text(raw, user_id=user_id)

    if is_front_chat(event):
        # คำสั่งข้อมูลทั่วไปที่ให้ลูกค้าใช้ในกลุ่มหน้าบ้านได้
        # ต้องปล่อยผ่านด่าน quiet mode ก่อน ไม่อย่างนั้น handler ด้านล่างจะไม่มีทางเห็นคำสั่ง
        if (
            is_rules_request(raw)
            or is_cancel_help_request(raw)
            or is_new_member_instruction_request(raw)
            or is_bank_account_request(raw)
            or is_withdrawal_command(raw)
            or is_scoreboard_command(raw)
        ):
            return True

        # คำสั่งเปิด/ปิด/ราคาช่าง/แจ้งผล/CK/CR ของแอดมิน
        if is_round_control_command_text(raw, user_id=user_id):
            return True

        # โพสต์แผลเล่น เช่น ชล500, ชถ500, 320-350ล500
        if parse_offer(raw):
            return True

        # คำว่า ต/ติด ให้บอทสนใจเฉพาะเมื่อ reply ข้อความเท่านั้น
        # ถ้าลูกค้าพิมพ์ ต เฉย ๆ ในกลุ่ม บอทจะเงียบ
        if parse_confirm_command(raw) and get_reply_message_id(event):
            return True

        # ถ้า reply ข้อความใน flow แผลเล่นด้วยคำที่ไม่ใช่คีย์
        # ให้ปล่อยเข้า handler เพื่อแจ้งวิธีพิมพ์ให้ถูก แต่ไม่สร้างรายการใด ๆ
        if QUIET_WARN_INVALID_REPLY_TO_PLAY and get_reply_message_id(event):
            if is_reply_to_known_play_message(get_reply_message_id(event)):
                return True

        return False

    # กลุ่มที่ไม่ได้ตั้งเป็นหน้าบ้าน/หลังบ้าน ให้เงียบทั้งหมด ยกเว้น GETID ด้านบน
    return False


# ======================================================
# CLEAR ALL — ล้างสกอ / รอบทุกรอบ / Backup ทั้งหมด
# ใช้ได้เฉพาะแอดมิน และต้องยืนยัน 2 ครั้ง
# ======================================================
def handle_clear_all(event, user_id):
    global STATE, ROUNDS, ACTIVE_BASE_NO, POSTS, MATCHES, CLEAR_ALL_PENDING

    now = time.time()

    # ครั้งแรก — รอยืนยัน
    if user_id not in CLEAR_ALL_PENDING or now - CLEAR_ALL_PENDING[user_id] > 60:
        CLEAR_ALL_PENDING[user_id] = now
        reply_text(
            event.reply_token,
            "⚠️ CLEAR ALL จะล้างทุกอย่างต่อไปนี้:\n"
            "- สกอและคู่ทั้งหมด\n"
            "- รอบทุกรอบ (ทุกฐาน)\n"
            "- Backup ทั้งหมด\n"
            "- ออเดอร์ทั้งหมด\n\n"
            "⚠️ พิมพ์ CLEAR ALL อีกครั้งภายใน 60 วินาที เพื่อยืนยัน"
        )
        return

    # ครั้งที่ 2 — ยืนยันแล้ว ล้างจริง
    CLEAR_ALL_PENDING.pop(user_id, None)

    with STATE_LOCK:
        # ล้าง POSTS และ MATCHES ใน memory
        POSTS.clear()
        MATCHES.clear()

        # ล้าง ROUNDS ทุกฐาน
        for base_no in list(ROUNDS.keys()):
            ROUNDS[base_no] = make_round_state(base_no)

        # รีเซ็ต STATE กลับเป็นฐาน 1
        ACTIVE_BASE_NO = "1"
        STATE = ROUNDS["1"]

        # รีเซ็ต ORDER
        ORDER_STATE["next_order_no"] = ORDER_START_NO
        ORDER_STATE["last_reset"] = datetime.now().isoformat()
        try:
            save_order_db()
        except Exception as e:
            print(f"CLEAR ALL save_order_db error: {e}")

        # ล้าง round_backups
        try:
            if os.path.exists(ROUND_BACKUP_DIR):
                import shutil
                shutil.rmtree(ROUND_BACKUP_DIR)
            os.makedirs(ROUND_BACKUP_DIR, exist_ok=True)
        except Exception as e:
            print(f"CLEAR ALL backup dir error: {e}")

        # ล้าง slip_topups
        try:
            SLIP_TOPUPS["slips"] = {}
            SLIP_TOPUPS["updated_at"] = datetime.now().isoformat()
            save_slip_topup_db()
        except Exception as e:
            print(f"CLEAR ALL slip_topup error: {e}")

    reply_text(
        event.reply_token,
        "✅ CLEAR ALL เสร็จสิ้น\n"
        "ล้างสกอ / รอบทุกรอบ / Backup / ออเดอร์ ทั้งหมดแล้ว\n"
        "พร้อมเปิดรอบใหม่ได้เลย"
    )


# ======================================================
# CLEAR ALL — ล้างสกอ / รอบทุกรอบ / Backup ทั้งหมด
# ======================================================
def handle_clear_all(event, user_id):
    global STATE, ROUNDS, ACTIVE_BASE_NO, POSTS, MATCHES, CLEAR_ALL_PENDING
    now = time.time()
    if user_id not in CLEAR_ALL_PENDING or now - CLEAR_ALL_PENDING[user_id] > 60:
        CLEAR_ALL_PENDING[user_id] = now
        reply_text(event.reply_token,
            "⚠️ CLEAR ALL จะล้างทุกอย่างต่อไปนี้:\n"
            "- คืนเครดิตลูกค้าทุกบิลที่จับคู่อยู่\n"
            "- สกอและคู่ทั้งหมด\n"
            "- รอบทุกรอบ (ทุกฐาน)\n"
            "- Backup และออเดอร์ทั้งหมด\n\n"
            "⚠️ พิมพ์ CLEAR ALL อีกครั้งภายใน 60 วินาที เพื่อยืนยัน")
        return
    CLEAR_ALL_PENDING.pop(user_id, None)
    total_refunded_matches = 0
    total_refunded_credit = 0
    with STATE_LOCK:
        reason = "CLEAR ALL โดยแอดมิน"
        for base_no, st in list(ROUNDS.items()):
            if not isinstance(st, dict): continue
            round_id = st.get("round_id")
            if not round_id or st.get("settled"): continue
            for match in list(MATCHES.values()):
                if match.get("round_id") != round_id: continue
                if match.get("status") == "matched":
                    amount = int(match.get("amount", 0) or 0)
                    maker = USERS.get(match.get("maker_id"))
                    taker = USERS.get(match.get("taker_id"))
                    if maker:
                        maker["credit"] = int(maker.get("credit", 0) or 0) + amount
                        total_refunded_credit += amount
                    if taker:
                        taker["credit"] = int(taker.get("credit", 0) or 0) + amount
                        total_refunded_credit += amount
                    match["status"] = "cancelled"
                    match["cancel_reason"] = reason
                    total_refunded_matches += 1
                elif match.get("status") in {"open", "pending"}:
                    match["status"] = "cancelled"
                    match["cancel_reason"] = reason
        try:
            save_user_db()
        except Exception as e:
            print(f"CLEAR ALL save_user_db error: {e}")
        POSTS.clear()
        MATCHES.clear()
        for base_no in list(ROUNDS.keys()):
            ROUNDS[base_no] = make_round_state(base_no)
        ACTIVE_BASE_NO = "1"
        STATE = ROUNDS["1"]
        ORDER_STATE["next_order_no"] = ORDER_START_NO
        ORDER_STATE["last_reset"] = datetime.now().isoformat()
        try:
            save_order_db()
        except Exception as e:
            print(f"CLEAR ALL save_order_db error: {e}")
        try:
            if os.path.exists(ROUND_BACKUP_DIR):
                import shutil
                shutil.rmtree(ROUND_BACKUP_DIR)
            os.makedirs(ROUND_BACKUP_DIR, exist_ok=True)
        except Exception as e:
            print(f"CLEAR ALL backup dir error: {e}")
        try:
            SLIP_TOPUPS["slips"] = {}
            SLIP_TOPUPS["updated_at"] = datetime.now().isoformat()
            save_slip_topup_db()
        except Exception as e:
            print(f"CLEAR ALL slip_topup error: {e}")
    reply_text(event.reply_token,
        "✅ CLEAR ALL เสร็จสิ้น\n\n"
        f"💰 คืนเครดิตลูกค้าแล้ว: {total_refunded_matches:,} บิล\n"
        f"💰 เครดิตคืนรวม: {total_refunded_credit:,} เครดิต\n\n"
        "🗑️ ล้างสกอ / รอบทุกรอบ / Backup / ออเดอร์ ทั้งหมดแล้ว\n"
        "พร้อมเปิดรอบใหม่ได้เลย")


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()

    # กัน LINE retry / duplicate message ไม่ให้คำสั่งเดิมถูกคิดซ้ำ
    message_id = get_message_id(event)
    if mark_message_processed(message_id):
        return

    user_id = event.source.user_id

    # Multi-base: ถ้าคำสั่งระบุฐาน ให้เลือกฐานก่อนเข้า quiet filter และแปลงข้อความกลับเป็นรูปแบบคำสั่งเดิม
    base_scope = extract_base_scoped_command(text)
    camp_scope = None
    filter_text = base_scope.get("text") if base_scope else text
    if base_scope and is_admin(user_id):
        select_round_base(base_scope.get("base_no"), chat_id=get_current_chat_id(event), create=True)
    elif is_admin(user_id):
        # คำสั่งแบบระบุชื่อค่าย เช่น แจ้งผล แอ๊ดเทวดา 350 / ย้อนผล แอ๊ดเทวดา
        camp_scope = resolve_camp_scoped_command(text, get_current_chat_id(event))
        if camp_scope and camp_scope.get("base_no"):
            select_round_base(camp_scope.get("base_no"), chat_id=get_current_chat_id(event), create=False)
            filter_text = camp_scope.get("text") or text

    # โหมดกลุ่มคนเยอะ: ข้ามข้อความคุยเล่นทันที ไม่ต้อง parse คำสั่งยาว ๆ ไม่ต้องดึงโปรไฟล์
    if not should_process_text_message(event, filter_text):
        return

    if camp_scope and camp_scope.get("error"):
        reply_problem(event, camp_scope.get("error"))
        return

    text = filter_text
    implicit_scope = False
    if not base_scope and not camp_scope:
        select_base_for_incoming_text(event, text)
        if is_admin(user_id) and is_front_chat(event):
            implicit_scope = select_base_for_admin_implicit_command(text, get_current_chat_id(event))

    # ถ้ามีหลายฐานค้างอยู่ ห้ามแอดมินใช้คำสั่งรอบแบบไม่ระบุฐาน/ชื่อค่าย
    # เช่น ปิด / ราคาช่าง / แจ้งผล / CK / CR / ยืนยัน เพราะจะเสี่ยงลงผิดฐาน
    if (
        not base_scope
        and not camp_scope
        and not implicit_scope
        and is_admin(user_id)
        and is_front_chat(event)
        and admin_command_needs_explicit_base(text, get_current_chat_id(event))
    ):
        reply_problem(event, explicit_base_required_text(get_current_chat_id(event)))
        return

    # โหลดข้อมูลผู้ใช้แบบ lazy เฉพาะคำสั่งที่จำเป็นต้องใช้จริง
    # คำสั่งแอดมินอย่าง CK / CR / ราคาช่าง / แจ้งผล จะไม่ต้องรอดึงโปรไฟล์หรือเขียน users.json
    user = None

    # แชทส่วนตัวต้องบันทึกทันที แม้ user พิมพ์ข้อความทั่วไปที่ไม่ใช่คำสั่ง
    # เพื่อให้ UIDLIST เปลี่ยนจาก "ยังไม่ยืนยันเพื่อน" เป็น "ทัก OA แล้ว"
    if is_private_chat(event):
        user = ensure_user_from_event(event)

    def current_user():
        nonlocal user
        if user is None:
            user = ensure_user_from_event(event)
        return user

    # ถ้าลูกค้า reply โพสต์แผล/ข้อความติดด้วยคำที่ไม่ใช่คีย์
    # ให้แจ้งวิธีใช้ทันทีและไม่บันทึกสถานะ เพื่อให้กลับไป reply ด้วย ต/ติด ได้ตามปกติ
    invalid_reply_msg = invalid_play_reply_warning(event, text)
    if invalid_reply_msg:
        reply_problem(event, invalid_reply_msg)
        return

    if is_add_admin_command(text):
        if not can_use_backoffice_command(event, user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะหลังบ้านหรือแอดมิน")
            return

        reply_text(event.reply_token, add_admins_from_mentions(event, user_id))
        return

    if is_admin_list_command(text):
        if not can_use_strict_backoffice_command(event):
            reply_text(event.reply_token, strict_backoffice_only_text("List / เช็คแอดมิน"))
            return

        reply_text(event.reply_token, admin_list_report())
        return

    if is_credit_check_mention_command(text):
        if not is_admin(user_id):
            reply_text(event.reply_token, "❌ คำสั่ง C @ชื่อไลน์ ใช้ได้เฉพาะแอดมินเท่านั้น")
            return

        if not is_group_or_room_chat(event):
            reply_text(event.reply_token, "❌ คำสั่ง C @ชื่อไลน์ ต้องใช้ในกลุ่มหลังบ้านหรือกลุ่มหน้าบ้านเท่านั้น")
            return

        reply_text(event.reply_token, credit_check_mentions_report(event))
        return

    if is_admin_help_request(text):
        if not can_use_strict_backoffice_command(event):
            reply_text(event.reply_token, strict_backoffice_only_text("คำสั่ง"))
            return

        reply_text(event.reply_token, admin_command_help_text())
        return

    # UID / GETID / เช็คยอด
    if text.upper() == "UID":
        reply_text(
            event.reply_token,
            f"UID ของคุณคือ:\n{user_id}\n\n"
            f"ชื่อ LINE:\n{current_user().get('line_name') or current_user().get('name')}\n\n"
            f"ID สมาชิก:\n{current_user().get('member_no')}"
        )
        return

    if text.upper() == "GETID":
        ids = get_source_ids(event)
        current_chat_id = get_current_chat_id(event)

        reply_text(
            event.reply_token,
            f"ข้อมูล ID ห้องนี้\n\n"
            f"source type:\n{ids.get('source_type')}\n\n"
            f"userId:\n{ids.get('user_id')}\n\n"
            f"groupId:\n{ids.get('group_id')}\n\n"
            f"roomId:\n{ids.get('room_id')}\n\n"
            f"ใช้ค่านี้สำหรับห้องปัจจุบัน:\n{current_chat_id}"
        )
        return

    if text.upper() == "UIDLIST":
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        reply_text(event.reply_token, users_report())
        return

    if text.upper() == "CALL":
        if not can_use_strict_backoffice_command(event):
            reply_text(event.reply_token, strict_backoffice_only_text("CALL"))
            return

        reply_text(event.reply_token, call_report())
        return

    profit_clean = re.sub(r"\s+", "", text).lower()
    if profit_clean in {"ยอดกำไร", "กำไร", "profit"}:
        if not can_use_strict_backoffice_command(event):
            reply_text(event.reply_token, strict_backoffice_only_text("ยอดกำไร"))
            return

        reply_text(event.reply_token, profit_report())
        return

    if profit_clean in {"ล้างกำไร", "ล้างกำร"}:
        if not can_use_strict_backoffice_command(event):
            reply_text(event.reply_token, strict_backoffice_only_text("ล้างกำไร"))
            return

        reply_text(event.reply_token, reset_profit_report(user_display_name(user_id)))
        return

    reset_order_no = parse_reset_order_command(text)
    if reset_order_no is not None:
        if not can_use_backoffice_command(event, user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะหลังบ้านหรือแอดมิน")
            return

        reply_text(event.reply_token, reset_order_report(user_display_name(user_id), reset_order_no))
        return

    if is_clear_round_backups_command(text):
        if not can_use_strict_backoffice_command(event):
            reply_text(event.reply_token, strict_backoffice_only_text("ล้าง round_backups"))
            return

        reply_text(event.reply_token, clear_round_backups_report(user_display_name(user_id)))
        return

    if text.replace(" ", "").upper() in {"CKรวม", "CKALL"}:
        if not can_use_backoffice_command(event, user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะหลังบ้านหรือแอดมิน")
            return

        reply_text(event.reply_token, all_rounds_report(get_current_chat_id(event)))
        return

    if text.upper() == "CK":
        if not can_use_backoffice_command(event, user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะหลังบ้านหรือแอดมิน")
            return

        reply_text(event.reply_token, current_round_report())
        return

    if is_match_list_command(text):
        if not can_use_backoffice_command(event, user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะหลังบ้านหรือแอดมิน")
            return

        reply_text(event.reply_token, current_round_match_report())
        return

    if is_listplay_command(text):
        if not can_use_backoffice_command(event, user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะหลังบ้านหรือแอดมิน")
            return

        reply_text(event.reply_token, current_round_listplay_report())
        return

    score_clean = re.sub(r"\s+", "", (text or "").strip()).lower()
    if is_scoreboard_command(text) and not (is_private_chat(event) and score_clean == "รายการ"):
        flex = scoreboard_flex_for_chat(get_current_chat_id(event))
        if flex:
            reply_flex(event.reply_token, "สกอค่าย", flex)
        else:
            reply_text(event.reply_token, scoreboard_empty_text(get_current_chat_id(event)))
        return

    if text.upper() == "CR":
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("เคลียร์รอบ"))
            return

        if STATE.get("round_id") is not None and not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("เคลียร์รอบ"))
            return

        with STATE_LOCK:
            msg = request_clear_round_confirm(user_display_name(user_id), get_current_chat_id(event))

        reply_text(event.reply_token, msg)
        return

    # ยืนยัน CR: ต้องมีคำสั่ง CR ที่รอยืนยันก่อนเท่านั้น
    if is_confirm_price_command(text) and has_pending_round_clear():
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("ยืนยันเคลียร์รอบ"))
            return

        if STATE.get("round_id") is not None and not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("ยืนยันเคลียร์รอบ"))
            return

        with STATE_LOCK:
            msg = confirm_pending_round_clear(user_display_name(user_id), get_current_chat_id(event))

        reply_text(event.reply_token, msg)
        return

    if is_rules_request(text):
        reply_flex(event.reply_token, "วิธีการเล่นบั้งไฟ", rules_flex())
        return

    if is_cancel_help_request(text):
        reply_text(event.reply_token, cancel_help_text())
        return

    if is_new_member_instruction_request(text):
        reply_text(event.reply_token, new_member_instruction_text())
        return

    if is_bank_account_request(text):
        # ห้ามใช้คำสั่ง บช/บัญชี หรือคำสั่งเกี่ยวกับบัญชีในกลุ่มหลังบ้าน/กลุ่มอื่น
        # ให้ตอบเฉพาะหน้าบ้านหรือแชทส่วนตัวกับ OA เท่านั้น
        if not can_use_bank_account_request_in_chat(event):
            return

        if should_skip_bank_account_by_cooldown(event):
            return

        # ส่ง 2 อย่างใน reply token เดียวกัน:
        # 1) ข้อความบัญชีแบบ TEXT
        # 2) FLEX ปุ่มสีเขียวสำหรับกดเข้าหลังบ้าน
        reply_text_and_flex(
            event.reply_token,
            bank_account_text(),
            "กดเข้าหลังบ้าน",
            bank_account_backoffice_flex(),
        )
        return


    withdrawal_kind = parse_withdrawal_command(text)
    if withdrawal_kind:
        # ห้ามใช้คำสั่งถอน/เคลียร์ยอดในกลุ่มหลังบ้าน/กลุ่มอื่น
        # ให้ตอบเฉพาะหน้าบ้านหรือแชทส่วนตัวกับ OA เท่านั้น
        if not can_use_withdrawal_command_in_chat(event):
            return

        if should_skip_withdrawal_by_cooldown(event):
            return

        cleared_amount = None
        if withdrawal_kind == "withdraw_all":
            target_user = current_user()
            with STATE_LOCK:
                cleared_amount = user_credit_amount(target_user)
                target_user["credit"] = 0
                target_user["last_withdraw_all_at"] = now_text()
                target_user["last_withdraw_all_amount"] = int(cleared_amount)
                save_user_db()

        reply_flex(
            event.reply_token,
            "ระบบทำรายการถอนยอดแล้ว",
            withdrawal_done_flex(amount=cleared_amount, command_kind=withdrawal_kind),
        )
        return

    if text.replace(" ", "") == "รายการ":
        # ข้อมูลรายการเล่นเป็นข้อมูลส่วนตัว จึงตอบเฉพาะแชทส่วนตัวกับ OA
        if not is_private_chat(event):
            return

        reply_flex(event.reply_token, "รายการเล่นของคุณ", active_plays_flex(user_id))
        return

    if text in ["เช็คยอด", "เครดิต", "ยอด", "เงิน"]:
        # เช็คยอดใช้ได้เฉพาะแชทส่วนตัวกับ OA; ถ้าพิมพ์ในกลุ่มบอทเงียบ
        if not is_private_chat(event):
            return

        reply_flex(event.reply_token, "ยอดเงินของคุณ", balance_flex(current_user()))
        return

    # บวก/ลบเครดิต
    credit_cmd = parse_credit_command(text)
    if credit_cmd:
        msg = handle_credit_adjust(event, credit_cmd)
        reply_text(event.reply_token, msg)
        return

    # เปลี่ยนค่ายเมื่อเปิดผิด และคืนบิลเดิมของค่ายที่เปิดผิด
    change_camp_name = parse_change_camp_command(text)
    if change_camp_name:
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("เปลี่ยนค่าย"))
            return

        if STATE.get("round_id") is not None and not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("เปลี่ยนค่าย"))
            return

        with STATE_LOCK:
            msg = change_camp_and_refund_wrong_round(change_camp_name, get_current_chat_id(event))

        reply_text(event.reply_token, msg)
        return

    # เปิดรอบ
    camp_name = parse_open_command(text)
    if camp_name:
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("เปิดรอบ"))
            return

        chat_id = get_current_chat_id(event)
        if camp_name_exists_in_unsettled_rounds(camp_name, chat_id=chat_id):
            reply_text(
                event.reply_token,
                f"❌ เปิดรอบไม่ได้\n\n"
                f"ค่ายนี้ยังมีรอบค้างอยู่: {camp_name}\n"
                f"บิลห้ามทับชื่อค่ายเดิม เพื่อกันแจ้งผล/ย้อนผลผิดรอบ\n"
                f"ให้ใช้ชื่อค่ายใหม่ หรือแจ้งผลค่ายเดิมให้จบก่อน"
            )
            return

        with STATE_LOCK:
            # เปิดรอบใหม่อัตโนมัติในฐานว่าง ไม่ต้องให้แอดมินพิมพ์ ฐาน1/ฐาน2
            select_base_for_new_round(chat_id)
            STATE["opened"] = True
            STATE["camp_name"] = camp_name
            STATE["round_id"] = str(uuid.uuid4())
            STATE["base_no"] = STATE.get("base_no") or ACTIVE_BASE_NO
            STATE["chat_id"] = get_current_chat_id(event)
            STATE["opened_at_ts"] = time.time()
            STATE["updated_at"] = now_text()
            STATE["base_min"] = None
            STATE["base_max"] = None
            STATE["price_mode"] = None
            STATE["no_price_reason"] = None
            STATE["two_digit_start"] = None
            STATE["closed_at"] = None
            STATE["continued_at"] = None
            STATE["continue_count"] = 0
            STATE["result"] = None
            STATE["settled"] = False
            STATE["pending_result"] = None
            STATE["pending_result_at"] = None
            clear_pending_price()
            clear_pending_round_clear()

        reply_text(
            event.reply_token,
            f"🚀🔥 {base_label_pretty()} คุยกันเลย 🔥🚀\n\n"
            f"ชื่อค่าย :  {camp_name}\n\n"
            f"ช่างราคา      ⛔️\n\n"
            f"🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀"
        )
        return

    # ปิดรอบ
    if text == "ปิด":
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("ปิดรอบ"))
            return

        if STATE.get("round_id") is None:
            reply_text(event.reply_token, "ยังไม่มีรอบให้ปิด")
            return

        if not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("ปิดรอบ"))
            return

        if not STATE["opened"]:
            reply_text(event.reply_token, "รอบนี้ปิดอยู่แล้ว")
            return

        with STATE_LOCK:
            STATE["opened"] = False
            STATE["closed_at"] = now_text()
            STATE["updated_at"] = now_text()
            camp = STATE["camp_name"] or "-"

        reply_text(
            event.reply_token,
            f"❌❌ ปิด {base_label_pretty()} แล้ว ❌❌\n\n"
            f"3  2  1 ไป๊!! 🚀🚀🚀\n\n"
            f"⛔ หลังปิดไม่ติดทุกกรณี ⛔ \n"
            f"ถ้าต้องการเปิดให้เล่นต่อ ให้แอดมินพิมพ์: เล่นต่อ {camp}\n"
            f"🔘 {camp}"
        )
        return

    # เล่นต่อหลังปิดรอบ: เปิดรับแผลต่อในรอบเดิม ใช้ได้แม้แจ้งราคาช่างแล้ว แต่ห้ามหลังออกผลแล้ว
    if is_continue_round_command(text):
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("เล่นต่อ"))
            return

        if STATE.get("round_id") is not None and not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("เล่นต่อ"))
            return

        with STATE_LOCK:
            msg = continue_round_for_play(get_current_chat_id(event))

        reply_text(event.reply_token, msg)
        return

    # ราคาช่างไม่มีราคา: ราคาช่าง ไม่ต่อย / ราคาช่าง ไม่ตี
    # ต้องพิมพ์ "ยืนยัน" อีกครั้งก่อนบันทึก/ประกาศจริง เพื่อกันกดผิด
    no_price_reason = parse_no_price_command(text)
    if no_price_reason:
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("แจ้งราคาช่าง"))
            return

        if STATE.get("round_id") is None:
            reply_text(event.reply_token, "ยังไม่มีรอบ กรุณาเปิดรอบก่อน")
            return

        if not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("แจ้งราคาช่าง"))
            return

        if STATE.get("opened"):
            reply_text(event.reply_token, "ยังไม่สามารถแจ้งราคาช่างได้ ต้องปิดรอบก่อน")
            return

        if STATE.get("settled"):
            reply_text(event.reply_token, "รอบนี้แจ้งผลแล้ว ไม่สามารถเปลี่ยนราคาช่างได้")
            return

        with STATE_LOCK:
            msg = request_no_price_confirm(no_price_reason)

        reply_text(event.reply_token, msg)
        return

    # ยืนยันราคาช่างพิเศษที่รออยู่ เช่น ราคาช่าง ไม่ต่อย / ราคาช่าง ไม่ตี
    if is_confirm_price_command(text):
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("ยืนยันราคาช่าง"))
            return

        if STATE.get("round_id") is not None and not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("ยืนยันราคาช่าง"))
            return

        with STATE_LOCK:
            msg = confirm_pending_price()

        reply_text(event.reply_token, msg)
        return

    # ราคาช่าง ใช้ได้เฉพาะหลังปิดรอบและก่อนแจ้งผล
    base_price = parse_base_price(text)
    if base_price:
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("แจ้งราคาช่าง"))
            return

        if STATE.get("round_id") is None:
            reply_text(event.reply_token, "ยังไม่มีรอบ กรุณาเปิดรอบก่อน")
            return

        if not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("แจ้งราคาช่าง"))
            return

        if STATE.get("opened"):
            reply_text(event.reply_token, "ยังไม่สามารถแจ้งราคาช่างได้ ต้องปิดรอบก่อน")
            return

        if STATE.get("settled"):
            reply_text(event.reply_token, "รอบนี้แจ้งผลแล้ว ไม่สามารถเปลี่ยนราคาช่างได้")
            return

        with STATE_LOCK:
            STATE["base_min"], STATE["base_max"] = base_price
            STATE["price_mode"] = "normal"
            STATE["no_price_reason"] = None
            STATE["two_digit_start"] = None
            STATE["pending_result"] = None
            STATE["pending_result_at"] = None
            cancelled_choty = cancel_no_price_only_entries("ราคาช่างกลับมาตีราคา")

        reply_text(
            event.reply_token,
            f"✅ {STATE.get('camp_name') or '-'}\n\n"
            f"🚀🚀 ราคาช่าง: {STATE['base_min']}-{STATE['base_max']} 🚀🚀\n\n"
            f"กติกาคิดผล:\n"
            f"- ผลอยู่ในช่วง = จาว\n"
            f"- ผลมากกว่า {STATE['base_max']} = ฝั่งชนะ/ไล่ ได้\n"
            f"- ผลต่ำกว่า {STATE['base_min']} = ฝั่งแพ้/ถอย ได้\n"
            f"- แผล ชตย เล่นเฉพาะตอนช่างไม่มีราคา ถ้ามีราคาช่างจะจาวทันที"
        )
        return

    # คำสั่งเริ่มต้นเลข 2 ตัว: เริ่มต้น1 / เริ่มต้น2 / เริ่มต้น3
    two_digit_start = parse_two_digit_start_command(text)
    if two_digit_start is not None:
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("แจ้งเริ่มต้น"))
            return

        if STATE.get("round_id") is None:
            reply_text(event.reply_token, "ยังไม่มีรอบ กรุณาเปิดรอบก่อน")
            return

        if not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("แจ้งเริ่มต้น"))
            return

        with STATE_LOCK:
            msg = set_two_digit_start(two_digit_start)

        reply_text(event.reply_token, msg)
        return

    # ย้อนผล กรณีแอดมินแจ้งผลผิด ต้องยืนยัน 2 ครั้ง
    rollback_action = parse_rollback_result_command(text)
    if rollback_action is not None:
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("ย้อนผล"))
            return

        if not base_scope and not camp_scope:
            rollback_candidates = rollback_candidate_rounds_for_chat(get_current_chat_id(event))
            if len(rollback_candidates) > 1:
                reply_text(event.reply_token, rollback_explicit_base_required_text(get_current_chat_id(event)))
                return
            if len(rollback_candidates) == 1:
                select_round_base(rollback_candidates[0][0], chat_id=get_current_chat_id(event), create=False)

        if STATE.get("round_id") is not None and not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("ย้อนผล"))
            return

        msg = handle_rollback_result_command(rollback_action, user_id=user_id)
        reply_text(event.reply_token, msg)
        return

    # แจ้งผลแบบคืนทุนทุกคน ต้องยืนยัน 2 ครั้ง
    special_result = parse_special_result_command(text)
    if special_result is not None:
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("แจ้งผล"))
            return

        if STATE.get("round_id") is not None and not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("แจ้งผล"))
            return

        msg = handle_special_result_with_double_confirm(special_result)
        if is_result_flex_reply_payload(msg):
            reply_flex(event.reply_token, msg.get("alt_text"), msg.get("flex"))
        else:
            reply_text(event.reply_token, msg)
        return

    # แจ้งผลตัวเลข ต้องยืนยัน 2 ครั้ง
    result_value = parse_result_command(text)
    if result_value is not None:
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("แจ้งผล"))
            return

        if STATE.get("round_id") is not None and not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("แจ้งผล"))
            return

        msg = handle_result_with_double_confirm(result_value)
        if is_result_flex_reply_payload(msg):
            reply_flex(event.reply_token, msg.get("alt_text"), msg.get("flex"))
        else:
            reply_text(event.reply_token, msg)
        return

    # ถ้าขึ้นต้นว่า แจ้งผล/ผล แต่ไม่เข้าเงื่อนไขที่ระบบรองรับ ให้หยุดไว้ ไม่คิดผล ไม่สรุปผล กันบัค
    if is_result_like_command(text):
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return

        if not is_front_chat(event):
            reply_text(event.reply_token, front_room_block_text("แจ้งผล"))
            return

        if STATE.get("round_id") is not None and not is_current_round_chat(event):
            reply_text(event.reply_token, cross_room_block_text("แจ้งผล"))
            return

        reply_problem(
            event,
            "⚠️ คำสั่งแจ้งผลไม่ถูกต้อง ระบบยังไม่คิดผลและไม่สรุปผล\n\n"
            "รูปแบบที่ใช้ได้เท่านั้น:\n"
            "- แจ้งผล 365\n"
            "- แจ้งผล แอ๊ดเทวดา 365\n"
            "- แจ้งผล จาวทุกแผล\n"
            "- แจ้งผล แอ๊ดเทวดา จาวทุกแผล\n"
            "- แจ้งผล บั้งไฟหาย"
        )
        return

    # ======================================================
    # CLEAR ALL — ล้างสกอ / รอบ / Backup ทั้งหมด
    # ======================================================
    if text.strip().upper() == "CLEAR ALL":
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return
        handle_clear_all(event, user_id)
        return

    # CLEAR ALL
    if text.strip().upper() == "CLEAR ALL":
        if not is_admin(user_id):
            reply_text(event.reply_token, "คำสั่งนี้ใช้ได้เฉพาะแอดมิน")
            return
        handle_clear_all(event, user_id)
        return

    # ลูกค้าโพสต์ เช่น ชล500 / ชถ500
    offer = parse_offer(text)
    if offer:
        msg = create_post(event, offer)
        if msg:
            reply_problem(event, msg)
        return

    # ตอบติด / ยืนยัน เช่น ต, ติด, ต300, ติด300, 300ต, 300ติด
    confirm_cmd = parse_confirm_command(text)
    if confirm_cmd:
        quoted_message_id = get_reply_message_id(event)
        msg = handle_confirm(event, quoted_message_id, confirm_cmd.get("amount"))
        if msg:
            reply_problem(event, msg)
        return


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    # กัน LINE retry / duplicate image ไม่ให้สลิปเดิมถูกเติมซ้ำ
    message_id = get_message_id(event)
    if mark_message_processed(message_id):
        return

    # รับสลิปเฉพาะแชทส่วนตัวกับ OA; ถ้าส่งในกลุ่มบอทเงียบ
    if not is_private_chat(event):
        return

    user_id = event.source.user_id

    # กันคนที่ยังไม่มี ID สมาชิกส่งสลิปเติมเครดิต
    # จุดนี้เช็กก่อนดึงรูป/ก่อนส่งเข้า Slip2Go เพื่อลดค่า API และไม่สร้าง user ใหม่
    with STATE_LOCK:
        if not get_registered_topup_user(user_id):
            reply_flex(
                event.reply_token,
                "ยังไม่มี ID สมาชิก",
                no_member_id_topup_flex(),
            )
            return

    # ดึงรูปจาก LINE ก่อนส่งตรวจ Slip2Go
    try:
        image_bytes = get_line_image_bytes(message_id)
    except Exception as e:
        reply_flex(
            event.reply_token,
            "ตรวจสลิปไม่สำเร็จ",
            slip_fail_flex(
                title="❌ ดึงรูปไม่สำเร็จ",
                reason=f"ดึงรูปจาก LINE ไม่สำเร็จ: {e}",
                suggestion="ส่งรูปสลิปใหม่อีกครั้ง หรือรอสักครู่แล้วลองใหม่",
            ),
        )
        return

    if not is_likely_slip_image(image_bytes):
        # ถ้าเปิด QR gate แล้วตรวจไม่เจอ QR จะไม่ส่งเข้า Slip2Go
        # ค่าเริ่มต้นในเวอร์ชันแก้ไขนี้ปิด QR gate แล้ว เพื่อกันบอทเงียบกับสลิปจริงที่ QR เล็ก/ภาพเบลอ
        return

    # ตอบกลับทันทีเพื่อไม่ให้ replyToken หมดอายุระหว่างรอ Slip2Go/LINE API
    reply_text(event.reply_token, "กำลังดำเนินการค่ะ")

    def job():
        try:
            msg = auto_topup_credit_from_slip(event, image_bytes=image_bytes)
            if isinstance(msg, dict):
                push_flex(user_id, "ผลตรวจสลิป", msg)
            elif msg:
                push_text(user_id, msg)
            else:
                # ถ้าหลุดมาถึงเคสนี้หลังผ่าน QR แล้ว แปลว่ารูปคล้ายสลิปแต่ยังไม่เข้าเงื่อนไขตรวจ
                push_flex(
                    user_id,
                    "ผลตรวจสลิป",
                    slip_fail_flex(
                        title="❌ ตรวจสลิปไม่สำเร็จ",
                        reason="ระบบตรวจสลิปแล้วแต่ยังไม่ผ่านเงื่อนไขการเติมเครดิต",
                        suggestion="ตรวจว่าบัญชีผู้รับตรง ยอดโอนถูกต้อง และส่งสลิปจริงที่ชัดเจน",
                    ),
                )
        except Exception as e:
            push_flex(
                user_id,
                "ผลตรวจสลิป",
                slip_fail_flex(
                    title="❌ ระบบตรวจสลิปขัดข้อง",
                    reason=f"เกิดข้อผิดพลาดระหว่างตรวจสลิป: {e}",
                    suggestion="ส่งสลิปใหม่อีกครั้ง หรือให้แอดมินตรวจสอบ",
                ),
            )

    EXECUTOR.submit(job)



@handler.add(PostbackEvent)
def handle_postback(event):
    ids = get_source_ids(event)
    user_id = ids.get("user_id")
    if is_private_chat(event):
        mark_user_friend_verified(user_id, reason="private_postback")
    else:
        get_user(user_id)

    data = event.postback.data
    params = dict(x.split("=", 1) for x in data.split("&") if "=" in x)
    action = params.get("action")
    match_id = params.get("match_id")

    if action == "request_cancel":
        msg = request_cancel(match_id, user_id)
        reply_text(event.reply_token, msg)
        return

    if action == "approve_cancel":
        msg = approve_cancel(match_id, user_id)
        reply_text(event.reply_token, msg)
        return

    if action == "reject_cancel":
        msg = reject_cancel(match_id, user_id)
        reply_text(event.reply_token, msg)
        return


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)

threading.Thread(
    target=cleanup_processed_messages,
    daemon=True
).start()
