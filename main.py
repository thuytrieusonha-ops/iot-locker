from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import smtplib
import time
from asyncio import to_thread
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import format_datetime, formataddr, make_msgid, localtime
from email.message import EmailMessage
from html import escape
from io import BytesIO
from pathlib import Path
from re import fullmatch
from threading import Lock, Thread

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy import desc, func, select, update
from sqlalchemy.orm import Session

from config import build_local_url, env_int, env_str
from database import SessionLocal, init_db, is_database_configured
from locker_hardware import (
    HARDWARE_ENABLED,
    LockerHardwareError,
    close_hardware,
    mark_locker_empty,
    open_locker,
    open_locker_for_dropoff,
    start_hardware,
)
from model import AdminCommand, AdminCommandLocker, LockerAccessToken, LockerOrder, UserAccount
from order_camera import OrderPhotoError, find_order_photo, save_order_photo


app = FastAPI(title="Smart Locker UI", version="1.0.0")

LOCKER_COUNT = max(1, env_int("SMARTLOCKER_LOCKER_COUNT", 8))
state_lock = Lock()


@dataclass
class LockerRecord:
    locker_id: int
    phone: str
    pickup_code: str
    flow: str
    created_at: datetime
    order_code: str | None = None
    recipient_email: str | None = None
    email_delivery_status: str | None = None
    email_delivery_note: str | None = None
    email_link_base_url: str | None = None
    email_sent_at: datetime | None = None
    status: str = "stored"


@dataclass
class UserAccountRecord:
    phone: str
    email: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class AccessTokenRecord:
    order_id: int
    locker_id: int
    phone: str
    email: str
    token_hash: str
    status: str
    expires_at: datetime
    created_at: datetime
    used_at: datetime | None = None


lockers: dict[int, LockerRecord | None] = {locker_id: None for locker_id in range(1, LOCKER_COUNT + 1)}
history: list[LockerRecord] = []
registered_users: dict[str, UserAccountRecord] = {}
access_tokens: dict[str, AccessTokenRecord] = {}
rate_limit_events: dict[str, list[datetime]] = {}
pickup_handoff_request: dict[str, object] = {"id": 0, "token": "", "requested_at": None}


FLOW_LABELS = {
    "user_dropoff": "Giao hàng",
    "shipper_dropoff": "Giao hàng",
}

ADMIN_ACTION_LABELS = {
    "unlock_all_lockers": "Mở tất cả tủ",
    "unlock_single_locker": "Mở tủ đã chọn",
}

ISSUE_TYPE_OPTIONS = {
    "locker_not_open": "Cửa tủ không mở",
    "forgot_pickup_code": "Quên mã mở tủ",
    "screen_slow": "Màn hình phản hồi chậm",
    "wrong_locker_state": "Hiển thị sai trạng thái tủ",
    "cannot_receive_email": "Không nhận được email",
    "other_support": "Cần nhân viên hỗ trợ",
}

PICKUP_TOKEN_TTL_HOURS = max(1, int(os.getenv("SMARTLOCKER_PICKUP_TOKEN_TTL_HOURS", "24") or "24"))
RATE_LIMIT_WINDOW_SECONDS = max(30, int(os.getenv("SMARTLOCKER_RATE_LIMIT_WINDOW_SECONDS", "300") or "300"))
RATE_LIMIT_MAX_ATTEMPTS = max(3, int(os.getenv("SMARTLOCKER_RATE_LIMIT_MAX_ATTEMPTS", "6") or "6"))
SMTP_HOST = os.getenv("SMARTLOCKER_SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMARTLOCKER_SMTP_PORT", "587") or "587")
SMTP_USERNAME = os.getenv("SMARTLOCKER_SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMARTLOCKER_SMTP_PASSWORD", "").strip()
SMTP_FROM_EMAIL = os.getenv("SMARTLOCKER_SMTP_FROM_EMAIL", SMTP_USERNAME).strip()
SMTP_FROM_NAME = os.getenv("SMARTLOCKER_SMTP_FROM_NAME", "Smart Locker").strip() or "Smart Locker"
SMTP_USE_TLS = os.getenv("SMARTLOCKER_SMTP_USE_TLS", "true").strip().lower() not in {"0", "false", "no"}
SMTP_INCLUDE_QR = os.getenv("SMARTLOCKER_SMTP_INCLUDE_QR", "false").strip().lower() in {"1", "true", "yes"}
SMTP_RETRY_ATTEMPTS = max(1, int(os.getenv("SMARTLOCKER_SMTP_RETRY_ATTEMPTS", "3") or "3"))
SMTP_RETRY_DELAY_SECONDS = max(5, int(os.getenv("SMARTLOCKER_SMTP_RETRY_DELAY_SECONDS", "20") or "20"))
BASE_URL = os.getenv("SMARTLOCKER_BASE_URL", "").strip().rstrip("/")
MONITOR_URL = os.getenv("SMARTLOCKER_MONITOR_URL", "").strip().rstrip("/")
MONITOR_USER_PORTAL_URL = os.getenv(
    "SMARTLOCKER_USER_PORTAL_URL",
    "https://app.smartlockerhcmute.dpdns.org/dang-ky-email",
).strip().rstrip("/")
APP_HOST = env_str("SMARTLOCKER_APP_HOST", "0.0.0.0") or "0.0.0.0"
APP_PORT = env_int("SMARTLOCKER_APP_PORT", 8000)
KIOSK_STATE_FILE = Path(__file__).resolve().parent / ".kiosk_state.json"


@app.on_event("startup")
def startup() -> None:
    try:
        init_db()
    except SQLAlchemyError as exc:
        print(f"[smartlocker] Database startup warning: {exc}")
    if HARDWARE_ENABLED:
        print("[smartlocker] Locker MQTT hardware enabled.")
        start_hardware()


@app.on_event("shutdown")
def shutdown() -> None:
    close_hardware()


def database_unavailable_page() -> str:
    return page_template(
        "Loi ket noi du lieu",
        """
        <section class="hero">
            <div>
                <h1>Khong the tai du lieu kiosk</h1>
                <p>He thong hien khong ket noi duoc den co so du lieu. Vui long kiem tra MySQL, file .env va khoi dong lai ung dung.</p>
            </div>
            <a class="home-link" href="/">Thu tai lai</a>
        </section>
        <section class="panel">
            <div class="result-panel error">
                <strong>Nguyen nhan thuong gap</strong>
                <ul>
                    <li>Dich vu MySQL chua chay hoac bi mat ket noi.</li>
                    <li>Thong tin `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` trong `.env` khong dung.</li>
                    <li>Kiosk dang mo truoc khi database san sang.</li>
                </ul>
            </div>
        </section>
        <script>
            (() => {
                const retryDelayMs = 5000;
                window.setTimeout(() => {
                    window.location.replace("/");
                }, retryDelayMs);
            })();
        </script>
        """,
    )


@app.exception_handler(OperationalError)
@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: Exception) -> HTMLResponse:
    return HTMLResponse(database_unavailable_page(), status_code=503)


@app.exception_handler(RequestValidationError)
async def form_validation_error_handler(request: Request, exc: RequestValidationError) -> HTMLResponse:
    field_labels = {
        "phone": "Số điện thoại",
        "email": "Email",
        "order_code": "Mã đơn hàng",
        "pickup_code": "Mã mở tủ",
        "phone_last4": "4 số cuối số điện thoại",
        "issue_type": "Loại sự cố",
    }
    missing_fields: list[str] = []
    for error in exc.errors():
        location = error.get("loc", ())
        field_name = str(location[-1]) if location else ""
        label = field_labels.get(field_name, field_name.replace("_", " ").strip().title())
        if label and label not in missing_fields:
            missing_fields.append(label)

    detail = (
        "Vui lòng nhập đầy đủ: " + ", ".join(missing_fields) + "."
        if missing_fields
        else "Vui lòng kiểm tra và nhập đầy đủ các thông tin bắt buộc."
    )
    retry_path = request.url.path
    return HTMLResponse(
        page_template(
            "Thiếu thông tin",
            f"""
            <section class="panel">
                <h2>Thông tin chưa đầy đủ</h2>
                <p>Hãy quay lại biểu mẫu và bổ sung các ô còn thiếu.</p>
                <a class="nav-link primary" href="{escape(retry_path)}">Quay lại nhập thông tin</a>
            </section>
            {result_panel("Chưa thể tiếp tục", [detail], tone="error")}
            """,
            enable_admin_command_polling=False,
        ),
        status_code=422,
    )


def read_dotenv_value(name: str, filename: str = ".env") -> str:
    dotenv_path = Path(__file__).resolve().parent / filename
    if not dotenv_path.exists():
        return ""

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key.strip() != name:
            continue

        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
            cleaned = cleaned[1:-1]
        return cleaned.strip()

    return ""


def current_configured_url(name: str, fallback: str = "") -> str:
    dotenv_value = read_dotenv_value(name).rstrip("/")
    if dotenv_value:
        return dotenv_value
    env_value = os.getenv(name, "").strip().rstrip("/")
    if env_value:
        return env_value
    return fallback.rstrip("/")


def now_text(value: datetime) -> str:
    return value.strftime("%d/%m/%Y %H:%M:%S")


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 9 or len(digits) > 11:
        raise HTTPException(status_code=400, detail="Số điện thoại phải có từ 9 đến 11 chữ số.")
    return digits


def normalize_required(value: str, field_name: str) -> str:
    cleaned = value.strip().upper()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{field_name} không được để trống.")
    return cleaned


def normalize_issue_contact(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return "Không cung cấp"
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if 7 <= len(digits) <= 15:
        return digits
    return cleaned[:50]


def normalize_issue_reference(value: str) -> str:
    cleaned = value.strip().upper()
    return cleaned[:60] if cleaned else "Không có"


def normalize_text(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{field_name} không được để trống.")
    return cleaned


def normalize_phone_last4(phone_last4: str) -> str:
    digits = "".join(ch for ch in phone_last4 if ch.isdigit())
    if len(digits) != 4:
        raise HTTPException(status_code=400, detail="Cần nhập đúng 4 số cuối số điện thoại.")
    return digits


def normalize_email(email: str) -> str:
    cleaned = email.strip().lower()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Email không được để trống.")
    if not fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cleaned):
        raise HTTPException(status_code=400, detail="Email không hợp lệ.")
    return cleaned


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def enforce_rate_limit(scope: str, max_attempts: int = RATE_LIMIT_MAX_ATTEMPTS) -> None:
    now = datetime.now()
    window_start = now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
    recent = [item for item in rate_limit_events.get(scope, []) if item >= window_start]
    if len(recent) >= max_attempts:
        raise HTTPException(
            status_code=429,
            detail="Bạn thao tác quá nhanh. Vui lòng chờ vài phút rồi thử lại.",
        )
    recent.append(now)
    rate_limit_events[scope] = recent


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not local or not domain:
        return email
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def get_base_url(request: Request | None = None) -> str:
    configured_base_url = current_configured_url("SMARTLOCKER_BASE_URL", BASE_URL)
    if configured_base_url:
        return configured_base_url
    if APP_HOST:
        return build_local_url(APP_HOST, APP_PORT).rstrip("/")
    if request is None:
        return "http://127.0.0.1:8000"
    return str(request.base_url).rstrip("/")


def email_delivery_enabled() -> bool:
    return bool(SMTP_HOST and SMTP_FROM_EMAIL)


def build_pickup_code_qr_url(pickup_code: str, request: Request | None = None) -> str:
    return f"{get_base_url(request)}/qr/pickup-code/{pickup_code}.svg"


def get_monitor_user_portal_url() -> str:
    direct_portal_url = current_configured_url("SMARTLOCKER_USER_PORTAL_URL", MONITOR_USER_PORTAL_URL)
    if direct_portal_url:
        return direct_portal_url
    monitor_url = current_configured_url("SMARTLOCKER_MONITOR_URL", MONITOR_URL)
    if monitor_url:
        return f"{monitor_url}/portal"
    return ""


def qrcode_available() -> bool:
    try:
        import qrcode  # noqa: F401
        from qrcode.image.svg import SvgPathImage  # noqa: F401
        return True
    except ModuleNotFoundError:
        return False


def build_pickup_code_qr_png(pickup_code: str) -> bytes | None:
    try:
        import qrcode
        from PIL import Image  # noqa: F401
    except ModuleNotFoundError:
        return None

    try:
        image = qrcode.make(pickup_code)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return None


def build_svg_qr_data_uri(value: str) -> str | None:
    if not value or not qrcode_available():
        return None

    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ModuleNotFoundError:
        return None

    try:
        image = qrcode.make(value, image_factory=SvgPathImage)
        buffer = BytesIO()
        image.save(buffer)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"
    except Exception:
        return None


def send_pickup_email(
    email: str,
    pickup_url: str,
    locker_id: int,
    expires_at: datetime,
    pickup_code: str,
    flow: str,
    order_code: str | None = None,
    order_photo_path: Path | None = None,
) -> tuple[bool, str]:
    if not email_delivery_enabled():
        return False, "Chưa cấu hình SMTP nên chưa thể gửi link tự động."

    locker_line = f"Tủ {locker_id}"
    if flow == "shipper_dropoff":
        subject = f"{locker_line} - Link nhận hàng"
        title = f"Nhận hàng tại {locker_line}"
        intro = f"Link này dùng để nhận hàng ở {locker_line}."
        code_label = "Mã nhận hàng dự phòng"
        order_lines = [f"Mã đơn hàng: {order_code}"] if order_code else []
    else:
        subject = f"{locker_line} - Link nhận đồ"
        title = f"Nhận đồ tại {locker_line}"
        intro = f"Link này dùng để nhận đồ ở {locker_line}."
        code_label = "Mã mở tủ dự phòng"
        order_lines = []
    order_html = f"<p><strong>Mã đơn hàng:</strong> {escape(order_code)}</p>" if order_code and flow == "shipper_dropoff" else ""
    photo_available = order_photo_path is not None and order_photo_path.is_file()
    photo_lines = ["Ảnh chụp đơn hàng được đính kèm trong email này."] if photo_available else []
    photo_html = "<p><strong>Ảnh đơn hàng:</strong> Xem tệp JPEG đính kèm trong email.</p>" if photo_available else ""
    qr_png = build_pickup_code_qr_png(pickup_code) if SMTP_INCLUDE_QR else None
    qr_html = ""
    if qr_png:
        qr_html = f"""
                <p><strong>QR mã mở tủ:</strong></p>
                <p>
                    <img src="cid:pickup-code-qr" alt="QR mã mở tủ {pickup_code}" width="220" height="220" style="border: 1px solid #d8e6f7; border-radius: 12px; padding: 8px; background: #ffffff;">
                </p>
        """

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM_EMAIL))
    message["Reply-To"] = SMTP_FROM_EMAIL
    message["To"] = email
    message["Date"] = format_datetime(localtime())
    message["Message-ID"] = make_msgid(domain=SMTP_FROM_EMAIL.split("@", 1)[1] if "@" in SMTP_FROM_EMAIL else None)
    message["X-Auto-Response-Suppress"] = "All"
    text_lines = [
        title + ".",
        "",
        f"Tủ: {locker_line}",
        f"Link nhận đồ: {pickup_url}",
        *order_lines,
        *photo_lines,
        f"{code_label}: {pickup_code}",
        f"Hết hạn: {now_text(expires_at)}",
        "",
        "Mở link rồi nhập 4 số cuối số điện thoại để xác nhận.",
        "Nếu link không mở được, dùng mã trên tại kiosk.",
        "QR chứa mã mở tủ 6 số." if qr_html else "Mail đang dùng bản rút gọn để dễ đọc trên điện thoại.",
    ]
    message.set_content("\n".join(text_lines))
    message.add_alternative(
        f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #16324f; line-height: 1.6;">
                <h2 style="margin-bottom: 12px;">{escape(title)}</h2>
                <p>{escape(intro)}</p>
                <p><strong>Tủ:</strong> {escape(locker_line)}</p>
                {order_html}
                {photo_html}
                <p><strong>{escape(code_label)}:</strong> {escape(pickup_code)}</p>
                <p><strong>Hết hạn:</strong> {escape(now_text(expires_at))}</p>
                <p>
                    <a href="{escape(pickup_url)}" style="display: inline-block; padding: 12px 18px; border-radius: 10px; background: #1565c0; color: #ffffff; text-decoration: none;">
                        Mở {escape(locker_line)}
                    </a>
                </p>
                {qr_html}
                <p>Mở link rồi nhập 4 số cuối số điện thoại để xác nhận.</p>
                <p>Nếu link không mở được, dùng mã trên tại kiosk.</p>
                <p style="font-size: 14px; color: #4a6480;">Mail hệ thống tự động từ Smart Locker.</p>
                <p style="font-size: 14px; color: #4a6480;">Nếu không thấy mail, hãy kiểm tra Thư rác / Spam.</p>
            </body>
        </html>
        """,
        subtype="html",
    )
    if qr_png:
        html_part = message.get_payload()[-1]
        html_part.add_related(qr_png, maintype="image", subtype="png", cid="<pickup-code-qr>")
    photo_attached = False
    if photo_available and order_photo_path is not None:
        photo_bytes = order_photo_path.read_bytes()
        if photo_bytes:
            message.add_attachment(
                photo_bytes,
                maintype="image",
                subtype="jpeg",
                filename=order_photo_path.name,
            )
            photo_attached = True

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        smtp.ehlo()
        if SMTP_USE_TLS:
            smtp.starttls()
            smtp.ehlo()
        if SMTP_USERNAME:
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(message)
    if photo_attached:
        return True, "Đã gửi link nhận đồ và ảnh chụp đơn hàng qua email đã đăng ký."
    return True, "Đã gửi link nhận đồ qua email đã đăng ký."


def deliver_pickup_email(
    record: LockerRecord,
    email: str,
    request: Request | None = None,
    order_photo_path: Path | None = None,
) -> tuple[LockerRecord, bool, str]:
    if order_photo_path is None:
        order_photo_path = find_order_photo(record.pickup_code)
    active_base_url = get_base_url(request)
    _, pickup_link, expires_at = issue_pickup_access(record, email, request)
    sent, delivery_note = send_pickup_email(
        email,
        pickup_link,
        record.locker_id,
        expires_at,
        record.pickup_code,
        record.flow,
        record.order_code,
        order_photo_path,
    )
    updated_record = update_record_email_delivery(
        record,
        email,
        "sent" if sent else "smtp_missing",
        delivery_note,
        datetime.now() if sent else None,
        active_base_url,
    )
    return updated_record, sent, delivery_note


def is_retryable_email_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, OSError)):
        return True
    if isinstance(exc, smtplib.SMTPException):
        smtp_code = getattr(exc, "smtp_code", None)
        if smtp_code is None:
            return True
        try:
            return int(smtp_code) >= 400
        except (TypeError, ValueError):
            return True
    return False


def queue_pickup_email_delivery(
    record: LockerRecord,
    email: str,
    request: Request | None = None,
    order_photo_path: Path | None = None,
) -> tuple[LockerRecord, str]:
    if order_photo_path is None:
        order_photo_path = find_order_photo(record.pickup_code)
    active_base_url = get_base_url(request)
    _, pickup_link, expires_at = issue_pickup_access(record, email, request)
    queued_record = update_record_email_delivery(
        record,
        email,
        "pending",
        "Hệ thống đang gửi email. Vui lòng kiểm tra hộp thư trong ít phút.",
        None,
        active_base_url,
    )

    def worker() -> None:
        last_error = ""
        for attempt in range(1, SMTP_RETRY_ATTEMPTS + 1):
            try:
                if attempt > 1:
                    update_record_email_delivery(
                        queued_record,
                        email,
                        "pending",
                        f"Đang thử gửi lại email lần {attempt}/{SMTP_RETRY_ATTEMPTS}.",
                        None,
                        active_base_url,
                    )
                sent, delivery_note = send_pickup_email(
                    email,
                    pickup_link,
                    record.locker_id,
                    expires_at,
                    record.pickup_code,
                    record.flow,
                    record.order_code,
                    order_photo_path,
                )
                update_record_email_delivery(
                    queued_record,
                    email,
                    "sent" if sent else "smtp_missing",
                    delivery_note,
                    datetime.now() if sent else None,
                    active_base_url,
                )
                return
            except (OSError, smtplib.SMTPException, TimeoutError) as exc:
                last_error = str(exc)
                if attempt >= SMTP_RETRY_ATTEMPTS or not is_retryable_email_error(exc):
                    break
                time.sleep(SMTP_RETRY_DELAY_SECONDS)
            except Exception as exc:
                last_error = str(exc) or exc.__class__.__name__
                break

        update_record_email_delivery(
            queued_record,
            email,
            "failed",
            f"Gửi email thất bại sau {SMTP_RETRY_ATTEMPTS} lần. {last_error}".strip(),
            None,
            active_base_url,
        )

    Thread(target=worker, daemon=True).start()
    if order_photo_path is not None:
        return queued_record, "Hệ thống đang gửi link nhận đồ kèm ảnh chụp đơn hàng qua email. Vui lòng kiểm tra hộp thư và thư rác trong ít phút."
    return queued_record, "Hệ thống đang gửi link nhận đồ qua email. Vui lòng kiểm tra hộp thư và thư rác trong ít phút."


def retry_email_delivery_for_phone(phone: str, email: str, request: Request | None = None) -> tuple[int, int]:
    if not using_database():
        return 0, 0

    assert SessionLocal is not None
    with SessionLocal() as session:
        orders = session.scalars(
            select(LockerOrder)
            .where(
                LockerOrder.phone == phone,
                LockerOrder.status == "stored",
                LockerOrder.recipient_email == email,
            )
            .order_by(desc(LockerOrder.created_at))
        ).all()

    attempted = 0
    delivered = 0
    for order in orders:
        attempted += 1
        try:
            _, sent, _ = deliver_pickup_email(to_record(order), email, request)
            if sent:
                delivered += 1
        except Exception as exc:
            update_record_email_delivery(to_record(order), email, "failed", str(exc))

    return attempted, delivered


def generate_pickup_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def user_error_message(exc: HTTPException | LockerHardwareError) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc)


def to_record(item: LockerOrder) -> LockerRecord:
    return LockerRecord(
        locker_id=item.locker_id,
        phone=item.phone,
        pickup_code=item.pickup_code,
        flow=item.flow,
        created_at=item.created_at,
        order_code=item.order_code,
        recipient_email=item.recipient_email,
        email_delivery_status=item.email_delivery_status,
        email_delivery_note=item.email_delivery_note,
        email_link_base_url=getattr(item, "email_link_base_url", None),
        email_sent_at=item.email_sent_at,
        status=item.status,
    )


def to_user_record(item: UserAccount) -> UserAccountRecord:
    return UserAccountRecord(
        phone=item.phone,
        email=item.email,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def using_database() -> bool:
    return is_database_configured()


def get_or_create_user_account(session: Session, phone: str) -> UserAccount:
    account = session.scalar(select(UserAccount).where(UserAccount.phone == phone))
    if account is not None:
        return account

    now = datetime.now()
    account = UserAccount(phone=phone, email=None, created_at=now, updated_at=now)
    session.add(account)
    session.flush()
    return account


def get_registered_user(phone: str) -> UserAccountRecord | None:
    if not using_database():
        return registered_users.get(phone)

    assert SessionLocal is not None
    with SessionLocal() as session:
        item = session.scalar(
            select(UserAccount).where(
                UserAccount.phone == phone,
                UserAccount.email.is_not(None),
            )
        )
        return to_user_record(item) if item is not None else None


def build_pickup_link(raw_token: str, request: Request | None = None) -> str:
    return f"{get_base_url(request)}/nhan-do/kiosk/{raw_token}"


def build_pickup_code_url(pickup_code: str, request: Request | None = None) -> str:
    return f"{get_base_url(request)}/nhan-do/ma-bao-mat/{pickup_code}"


def request_pickup_handoff(raw_token: str) -> int:
    if not using_database():
        with state_lock:
            next_id = int(pickup_handoff_request.get("id", 0)) + 1
            pickup_handoff_request["id"] = next_id
            pickup_handoff_request["token"] = raw_token
            pickup_handoff_request["requested_at"] = datetime.now()
            return next_id

    assert SessionLocal is not None
    with SessionLocal() as session:
        command = AdminCommand(
            action="pickup_handoff",
            status="pending",
            note=f"token={raw_token}",
            created_at=datetime.now(),
        )
        session.add(command)
        session.commit()
        session.refresh(command)
        return command.id


def consume_pickup_handoff() -> dict[str, object]:
    if not using_database():
        with state_lock:
            token = str(pickup_handoff_request.get("token", ""))
            handoff_id = int(pickup_handoff_request.get("id", 0))
            if not token or not handoff_id:
                return {"has_pending": False, "id": 0, "redirect_url": ""}

            pickup_handoff_request["token"] = ""
            return {
                "has_pending": True,
                "id": handoff_id,
                "redirect_url": f"/nhan-do/link/{token}?source=kiosk",
            }

    assert SessionLocal is not None
    with SessionLocal() as session:
        command = session.scalar(
            select(AdminCommand)
            .where(
                AdminCommand.action == "pickup_handoff",
                AdminCommand.status == "pending",
            )
            .order_by(desc(AdminCommand.created_at))
            .limit(1)
        )
        if command is None:
            return {"has_pending": False, "id": 0, "redirect_url": ""}

        raw_token = ""
        for segment in [part.strip() for part in (command.note or "").split("|")]:
            if segment.startswith("token="):
                raw_token = segment.split("=", 1)[1].strip()
                break

        if not raw_token:
            command.status = "completed"
            command.completed_at = datetime.now()
            session.commit()
            return {"has_pending": False, "id": 0, "redirect_url": ""}

        command.status = "completed"
        command.completed_at = datetime.now()
        session.commit()
        return {
            "has_pending": True,
            "id": command.id,
            "redirect_url": f"/nhan-do/link/{raw_token}?source=kiosk",
        }


def revoke_active_tokens(order_id: int, session: Session | None = None) -> None:
    if not using_database():
        for token_hash, item in list(access_tokens.items()):
            if item.order_id == order_id and item.status == "active":
                item.status = "revoked"
                access_tokens[token_hash] = item
        return

    assert session is not None
    session.execute(
        update(LockerAccessToken)
        .where(LockerAccessToken.order_id == order_id, LockerAccessToken.status == "active")
        .values(status="revoked")
    )


def issue_pickup_access(record: LockerRecord, email: str, request: Request | None = None) -> tuple[str, str, datetime]:
    raw_token = secrets.token_urlsafe(24)
    token_hash = hash_token(raw_token)
    expires_at = datetime.now() + timedelta(hours=PICKUP_TOKEN_TTL_HOURS)

    with state_lock:
        if not using_database():
            # Order id is not available in memory mode, so fall back to locker id + timestamp uniqueness.
            synthetic_order_id = int(record.created_at.timestamp() * 1000)
            revoke_active_tokens(synthetic_order_id)
            access_tokens[token_hash] = AccessTokenRecord(
                order_id=synthetic_order_id,
                locker_id=record.locker_id,
                phone=record.phone,
                email=email,
                token_hash=token_hash,
                status="active",
                expires_at=expires_at,
                created_at=datetime.now(),
            )
        else:
            assert SessionLocal is not None
            with SessionLocal() as session:
                order = session.scalar(
                    select(LockerOrder).where(
                        LockerOrder.locker_id == record.locker_id,
                        LockerOrder.phone == record.phone,
                        LockerOrder.pickup_code == record.pickup_code,
                        LockerOrder.status == "stored",
                    )
                )
                if order is None:
                    raise HTTPException(status_code=404, detail="Không tìm thấy đơn để cấp link nhận đồ.")
                revoke_active_tokens(order.id, session)
                session.add(
                    LockerAccessToken(
                        order_id=order.id,
                        locker_id=record.locker_id,
                        phone=record.phone,
                        email=email,
                        token_hash=token_hash,
                        status="active",
                        delivery_channel="email",
                        expires_at=expires_at,
                        created_at=datetime.now(),
                    )
                )
                session.commit()

    return raw_token, build_pickup_link(raw_token, request), expires_at


def update_record_email_delivery(
    record: LockerRecord,
    recipient_email: str | None,
    delivery_status: str,
    delivery_note: str,
    email_sent_at: datetime | None = None,
    email_link_base_url: str | None = None,
) -> LockerRecord:
    with state_lock:
        if not using_database():
            for locker_id, locker_record in lockers.items():
                if locker_record and locker_record.phone == record.phone and locker_record.pickup_code == record.pickup_code:
                    locker_record.recipient_email = recipient_email
                    locker_record.email_delivery_status = delivery_status
                    locker_record.email_delivery_note = delivery_note
                    locker_record.email_link_base_url = email_link_base_url
                    locker_record.email_sent_at = email_sent_at
                    lockers[locker_id] = locker_record
                    return locker_record
            record.recipient_email = recipient_email
            record.email_delivery_status = delivery_status
            record.email_delivery_note = delivery_note
            record.email_link_base_url = email_link_base_url
            record.email_sent_at = email_sent_at
            return record

        assert SessionLocal is not None
        with SessionLocal() as session:
            order = session.scalar(
                select(LockerOrder).where(
                    LockerOrder.pickup_code == record.pickup_code,
                    LockerOrder.status == "stored",
                )
            )
            if order is None:
                return record

            order.recipient_email = recipient_email
            order.email_delivery_status = delivery_status
            order.email_delivery_note = delivery_note
            order.email_link_base_url = email_link_base_url
            order.email_sent_at = email_sent_at
            session.commit()
            session.refresh(order)
            return to_record(order)


def resolve_pickup_access(raw_token: str) -> tuple[LockerRecord, AccessTokenRecord | LockerAccessToken]:
    token_hash = hash_token(raw_token)
    now = datetime.now()

    if not using_database():
        token = access_tokens.get(token_hash)
        if token is None or token.status != "active" or token.expires_at < now:
            raise HTTPException(status_code=404, detail="Link nhận đồ không hợp lệ hoặc đã hết hạn.")

        record = next(
            (
                item
                for item in history
                if int(item.created_at.timestamp() * 1000) == token.order_id and item.status == "stored"
            ),
            None,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Đơn nhận đồ không còn khả dụng.")
        return record, token

    assert SessionLocal is not None
    with SessionLocal() as session:
        token = session.scalar(
            select(LockerAccessToken).where(
                LockerAccessToken.token_hash == token_hash,
                LockerAccessToken.status == "active",
            )
        )
        if token is None or token.expires_at < now:
            raise HTTPException(status_code=404, detail="Link nhận đồ không hợp lệ hoặc đã hết hạn.")
        order = session.scalar(
            select(LockerOrder).where(
                LockerOrder.id == token.order_id,
                LockerOrder.status == "stored",
            )
        )
        if order is None:
            raise HTTPException(status_code=404, detail="Đơn nhận đồ không còn khả dụng.")
        return to_record(order), token


def mark_pickup_access_used(raw_token: str, phone_last4: str) -> LockerRecord:
    token_hash = hash_token(raw_token)
    with state_lock:
        record, token = resolve_pickup_access(raw_token)
        if record.phone[-4:] != phone_last4:
            raise HTTPException(status_code=403, detail="4 số cuối số điện thoại không khớp.")

        if not using_database():
            access_item = access_tokens[token_hash]
            access_item.status = "used"
            access_item.used_at = datetime.now()
            access_tokens[token_hash] = access_item
            for locker_id, locker_record in lockers.items():
                if locker_record and locker_record.phone == record.phone and locker_record.pickup_code == record.pickup_code:
                    locker_record.status = "collected"
                    lockers[locker_id] = None
                    return locker_record
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn phù hợp để mở tủ.")

        assert SessionLocal is not None
        with SessionLocal() as session:
            access_item = session.scalar(
                select(LockerAccessToken).where(
                    LockerAccessToken.token_hash == token_hash,
                    LockerAccessToken.status == "active",
                )
            )
            order = session.scalar(
                select(LockerOrder).where(
                    LockerOrder.id == access_item.order_id if access_item is not None else 0,
                    LockerOrder.status == "stored",
                )
            )
            if access_item is None or order is None:
                raise HTTPException(status_code=404, detail="Link nhận đồ không còn khả dụng.")
            if order.phone[-4:] != phone_last4:
                raise HTTPException(status_code=403, detail="4 số cuối số điện thoại không khớp.")
            access_item.status = "used"
            access_item.used_at = datetime.now()
            order.status = "collected"
            session.commit()
            session.refresh(order)
            return to_record(order)


def parse_unlock_command_note(note: str | None) -> tuple[list[int], str]:
    if not note:
        return [], ""

    locker_ids: list[int] = []
    detail_parts: list[str] = []
    for segment in [part.strip() for part in note.split("|")]:
        if segment.startswith("locker_ids="):
            raw_values = segment.split("=", 1)[1].strip()
            locker_ids = [int(value) for value in raw_values.split(",") if value.strip().isdigit()]
        elif segment.startswith("locker_id="):
            raw_value = segment.split("=", 1)[1].strip()
            if raw_value.isdigit():
                locker_ids = [int(raw_value)]
        elif segment:
            detail_parts.append(segment)

    return list(dict.fromkeys(locker_ids)), " | ".join(detail_parts)


def fetch_admin_command_locker_ids(command_id: int) -> list[int]:
    if not using_database() or SessionLocal is None:
        return []

    with SessionLocal() as session:
        return session.scalars(
            select(AdminCommandLocker.locker_id)
            .where(AdminCommandLocker.command_id == command_id)
            .order_by(AdminCommandLocker.locker_id)
        ).all()


def unlock_command_locker_ids(command: AdminCommand) -> list[int]:
    linked_ids = fetch_admin_command_locker_ids(command.id)
    if linked_ids:
        return linked_ids

    parsed_ids, _ = parse_unlock_command_note(command.note)
    return parsed_ids


def fetch_pending_unlock_command() -> AdminCommand | None:
    if not using_database():
        return None

    assert SessionLocal is not None
    with SessionLocal() as session:
        return session.scalar(
            select(AdminCommand)
            .where(
                AdminCommand.action.in_(("unlock_all_lockers", "unlock_single_locker")),
                AdminCommand.status == "pending",
            )
            .order_by(desc(AdminCommand.created_at))
            .limit(1)
        )


def load_kiosk_state() -> dict[str, int]:
    try:
        if not KIOSK_STATE_FILE.exists():
            return {}
        data = json.loads(KIOSK_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(key): int(value) for key, value in data.items()}
    except (OSError, ValueError, TypeError):
        return {}
    return {}


def save_kiosk_state(state: dict[str, int]) -> None:
    try:
        KIOSK_STATE_FILE.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass


def last_seen_admin_command_id() -> int:
    return int(load_kiosk_state().get("last_seen_admin_command_id", 0))


def mark_admin_command_seen(command_id: int) -> None:
    state = load_kiosk_state()
    previous = int(state.get("last_seen_admin_command_id", 0))
    if command_id <= previous:
        return
    state["last_seen_admin_command_id"] = command_id
    save_kiosk_state(state)


def claim_pending_unlock_command_for_display() -> AdminCommand | None:
    command = fetch_pending_unlock_command()
    if command is None:
        return None
    if command.id <= last_seen_admin_command_id():
        return None
    mark_admin_command_seen(command.id)
    return command


def complete_unlock_command(command_id: int, note: str) -> None:
    if not using_database():
        return

    assert SessionLocal is not None
    with SessionLocal() as session:
        command = session.get(AdminCommand, command_id)
        if command is None:
            return
        command.status = "completed"
        command.note = " | ".join(part for part in [command.note or "", note] if part)
        command.completed_at = datetime.now()
        session.commit()


def execute_admin_unlock_command(command: AdminCommand) -> str:
    locker_ids, _ = parse_unlock_command_note(command.note)
    if command.action == "unlock_all_lockers":
        target_ids = list(range(1, LOCKER_COUNT + 1))
    else:
        target_ids = [locker_id for locker_id in locker_ids if 1 <= locker_id <= LOCKER_COUNT]

    if not target_ids:
        message = "Không có tủ hợp lệ để mở."
        complete_unlock_command(command.id, f"kiosk_result={message}")
        return message

    opened_ids: list[int] = []
    failed_messages: list[str] = []
    for locker_id in target_ids:
        try:
            open_locker(locker_id)
            opened_ids.append(locker_id)
        except LockerHardwareError as exc:
            failed_messages.append(f"Tủ {locker_id}: {exc}")

    if failed_messages:
        message = "; ".join(failed_messages)
    else:
        message = "Đã gửi lệnh mở " + ", ".join(f"Tủ {locker_id}" for locker_id in opened_ids)

    complete_unlock_command(command.id, f"kiosk_result={message}")
    return message


def admin_command_banner() -> str:
    return ""


def render_admin_command_modal(command: AdminCommand) -> str:
    created_text = now_text(command.created_at)
    _, note_text = parse_unlock_command_note(command.note)
    locker_ids = unlock_command_locker_ids(command)
    if command.action == "unlock_single_locker" and locker_ids:
        action_label = "Mở nhiều tủ" if len(locker_ids) > 1 else f"Mở Tủ {locker_ids[0]}"
        locker_line = f"<p>Yêu cầu mở: <strong>{', '.join(f'Tủ {locker_id}' for locker_id in locker_ids)}</strong>.</p>"
    else:
        action_label = ADMIN_ACTION_LABELS.get(command.action, command.action)
        locker_line = "<p>Yêu cầu mở toàn bộ các tủ.</p>"
    note = f"<p class=\"admin-alert-note\">Ghi chú: {escape(note_text)}</p>" if note_text else ""
    return f"""
    <div class="admin-alert-backdrop" data-admin-alert data-command-id="{command.id}">
        <section class="admin-alert" role="alert" aria-live="assertive" aria-labelledby="admin-alert-title">
            <div class="admin-alert-badge">Lệnh từ monitor</div>
            <h2 id="admin-alert-title">{escape(action_label)}</h2>
            <p>Có lệnh quản trị từ xa vừa được gửi tới kiosk.</p>
            <p>Thời gian phát lệnh: {escape(created_text)}</p>
            {locker_line}
            <p>Hãy kiểm tra và xử lý theo quy trình vận hành hoặc dùng lệnh này cho bộ điều khiển khóa khi tích hợp Raspberry Pi.</p>
            {note}
        </section>
    </div>
    """


def admin_command_modal() -> str:
    command = claim_pending_unlock_command_for_display()
    if command is None:
        return ""
    execute_admin_unlock_command(command)
    return render_admin_command_modal(command)


def admin_command_payload() -> dict[str, object]:
    command = claim_pending_unlock_command_for_display()
    if command is None:
        return {"has_pending": False, "html": "", "modal_html": ""}

    hardware_result = execute_admin_unlock_command(command)
    locker_ids = unlock_command_locker_ids(command)
    return {
        "has_pending": True,
        "id": command.id,
        "action": command.action,
        "action_label": ADMIN_ACTION_LABELS.get(command.action, command.action),
        "created_at": now_text(command.created_at),
        "note": command.note or "",
        "hardware_result": hardware_result,
        "locker_ids": locker_ids,
        "html": "",
        "modal_html": render_admin_command_modal(command),
    }


def get_active_records() -> list[LockerRecord]:
    if not using_database():
        return sorted([record for record in lockers.values() if record], key=lambda item: item.created_at)

    assert SessionLocal is not None
    with SessionLocal() as session:
        records = session.scalars(
            select(LockerOrder).where(LockerOrder.status == "stored").order_by(LockerOrder.created_at.asc())
        ).all()
    return [to_record(record) for record in records]


def get_history_records(limit: int = 12) -> list[LockerRecord]:
    if not using_database():
        return sorted(history[-limit:], key=lambda item: item.created_at, reverse=True)

    assert SessionLocal is not None
    with SessionLocal() as session:
        records = session.scalars(select(LockerOrder).order_by(desc(LockerOrder.created_at)).limit(limit)).all()
    return [to_record(record) for record in records]


def find_empty_locker(session: Session | None = None) -> int | None:
    if not using_database():
        for locker_id, record in lockers.items():
            if record is None:
                return locker_id
        return None

    assert session is not None
    occupied = {
        locker_id
        for locker_id in session.scalars(
            select(LockerOrder.locker_id).where(LockerOrder.status == "stored")
        ).all()
    }
    for locker_id in range(1, LOCKER_COUNT + 1):
        if locker_id not in occupied:
            return locker_id
    return None


def create_record(
    phone: str,
    flow: str,
    order_code: str | None = None,
    recipient_email: str | None = None,
    email_delivery_status: str | None = None,
    email_delivery_note: str | None = None,
) -> LockerRecord:
    with state_lock:
        if not using_database():
            locker_id = find_empty_locker()
            if locker_id is None:
                raise HTTPException(status_code=409, detail=f"Hiện tại {LOCKER_COUNT} tủ đã đầy, vui lòng thử lại sau.")

            pickup_code = generate_pickup_code()
            while any(record and record.pickup_code == pickup_code for record in lockers.values()):
                pickup_code = generate_pickup_code()

            record = LockerRecord(
                locker_id=locker_id,
                phone=phone,
                pickup_code=pickup_code,
                flow=flow,
                created_at=datetime.now(),
                order_code=order_code,
                recipient_email=recipient_email,
                email_delivery_status=email_delivery_status,
                email_delivery_note=email_delivery_note,
            )
            lockers[locker_id] = record
            history.append(record)
            return record

        assert SessionLocal is not None
        with SessionLocal() as session:
            account = get_or_create_user_account(session, phone)
            locker_id = find_empty_locker(session)
            if locker_id is None:
                raise HTTPException(status_code=409, detail=f"Hiện tại {LOCKER_COUNT} tủ đã đầy, vui lòng thử lại sau.")

            pickup_code = generate_pickup_code()
            while session.scalar(select(func.count()).select_from(LockerOrder).where(LockerOrder.pickup_code == pickup_code)):
                pickup_code = generate_pickup_code()

            item = LockerOrder(
                user_id=account.id,
                locker_id=locker_id,
                phone=phone,
                pickup_code=pickup_code,
                flow=flow,
                created_at=datetime.now(),
                order_code=order_code,
                recipient_email=recipient_email,
                email_delivery_status=email_delivery_status,
                email_delivery_note=email_delivery_note,
                status="stored",
            )
            session.add(item)
            session.commit()
            session.refresh(item)
            return to_record(item)


def release_record(record: LockerRecord) -> None:
    with state_lock:
        if not using_database():
            for locker_id, locker_record in lockers.items():
                if locker_record and locker_record.phone == record.phone and locker_record.pickup_code == record.pickup_code:
                    locker_record.status = "collected"
                    lockers[locker_id] = None
                    return
            return

        assert SessionLocal is not None
        with SessionLocal() as session:
            item = session.scalar(
                select(LockerOrder).where(
                    LockerOrder.locker_id == record.locker_id,
                    LockerOrder.phone == record.phone,
                    LockerOrder.pickup_code == record.pickup_code,
                    LockerOrder.status == "stored",
                )
            )
            if item is not None:
                item.status = "collected"
                session.commit()


def get_record_by_phone_pickup(phone: str, pickup_code: str) -> LockerRecord:
    if not using_database():
        for record in lockers.values():
            if record and record.phone == phone and record.pickup_code == pickup_code and record.status == "stored":
                return record
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn phù hợp với số điện thoại và mã mở tủ.")

    assert SessionLocal is not None
    with SessionLocal() as session:
        item = session.scalar(
            select(LockerOrder).where(
                LockerOrder.phone == phone,
                LockerOrder.pickup_code == pickup_code,
                LockerOrder.status == "stored",
            )
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn phù hợp với số điện thoại và mã mở tủ.")
        return to_record(item)


def collect_record(phone: str, pickup_code: str) -> LockerRecord:
    with state_lock:
        if not using_database():
            for locker_id, record in lockers.items():
                if record and record.phone == phone and record.pickup_code == pickup_code:
                    record.status = "collected"
                    lockers[locker_id] = None
                    return record
        else:
            assert SessionLocal is not None
            with SessionLocal() as session:
                item = session.scalar(
                    select(LockerOrder).where(
                        LockerOrder.phone == phone,
                        LockerOrder.pickup_code == pickup_code,
                        LockerOrder.status == "stored",
                    )
                )
                if item is not None:
                    item.status = "collected"
                    session.commit()
                    session.refresh(item)
                    return to_record(item)
    raise HTTPException(status_code=404, detail="Không tìm thấy đơn phù hợp với số điện thoại và mã mở tủ.")


def collect_record_by_last4(pickup_code: str, phone_last4: str) -> LockerRecord:
    with state_lock:
        if not using_database():
            for locker_id, record in lockers.items():
                if record and record.pickup_code == pickup_code and record.phone[-4:] == phone_last4:
                    record.status = "collected"
                    lockers[locker_id] = None
                    return record
        else:
            assert SessionLocal is not None
            with SessionLocal() as session:
                item = session.scalar(
                    select(LockerOrder).where(
                        LockerOrder.pickup_code == pickup_code,
                        LockerOrder.status == "stored",
                    )
                )
                if item is not None and item.phone[-4:] == phone_last4:
                    item.status = "collected"
                    session.commit()
                    session.refresh(item)
                    return to_record(item)
    raise HTTPException(status_code=404, detail="Không tìm thấy đơn phù hợp với mã bảo mật và 4 số cuối số điện thoại.")


def get_record_by_pickup_code(pickup_code: str) -> LockerRecord:
    if not using_database():
        for record in lockers.values():
            if record and record.pickup_code == pickup_code and record.status == "stored":
                return record
        raise HTTPException(status_code=404, detail="Mã mở tủ không hợp lệ hoặc đã hết hiệu lực.")

    assert SessionLocal is not None
    with SessionLocal() as session:
        item = session.scalar(
            select(LockerOrder).where(
                LockerOrder.pickup_code == pickup_code,
                LockerOrder.status == "stored",
            )
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Mã mở tủ không hợp lệ hoặc đã hết hiệu lực.")
        return to_record(item)


def locker_cards() -> str:
    active_by_locker = {record.locker_id: record for record in get_active_records()}
    cards: list[str] = []
    for locker_id in range(1, LOCKER_COUNT + 1):
        record = active_by_locker.get(locker_id)
        is_busy = record is not None
        status_class = "busy" if is_busy else "free"
        status_text = "Đang sử dụng" if is_busy else "Sẵn sàng"
        phone = record.phone if record else "---"
        code = record.pickup_code if record else "---"
        flow = FLOW_LABELS.get(record.flow, "---") if record else "---"
        cards.append(
            f"""
            <div class="locker-card {status_class}">
                <div class="locker-top">
                    <span class="locker-name">Tủ {locker_id}</span>
                    <span class="locker-badge">{status_text}</span>
                </div>
                <div class="locker-meta">Khách: {escape(phone)}</div>
                <div class="locker-meta">Mã mở tủ: {escape(code)}</div>
                <div class="locker-meta">Loại đơn: {escape(flow)}</div>
            </div>
            """
        )
    return "".join(cards)


def active_records_table() -> str:
    active_records = get_active_records()
    if not active_records:
        return '<div class="empty-box">Chưa có đơn nào trong hệ thống.</div>'

    rows: list[str] = []
    for record in active_records:
        order_code = record.order_code or "---"
        rows.append(
            f"""
            <tr>
                <td>{record.locker_id}</td>
                <td>{escape(record.phone)}</td>
                <td>{escape(record.pickup_code)}</td>
                <td>{escape(order_code)}</td>
                <td>{escape(FLOW_LABELS.get(record.flow, record.flow))}</td>
                <td>{now_text(record.created_at)}</td>
            </tr>
            """
        )

    return f"""
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Tủ</th>
                    <th>SĐT</th>
                    <th>Mã mở</th>
                    <th>Mã đơn</th>
                    <th>Loại</th>
                    <th>Thời gian</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
            </tbody>
        </table>
    </div>
    """


def recent_history_table() -> str:
    recent_records = get_history_records()
    if not recent_records:
        return '<div class="empty-box">Lịch sử đơn sẽ hiện tại đây.</div>'

    rows: list[str] = []
    for record in recent_records:
        status_text = "Đã nhận" if record.status == "collected" else "Đang lưu"
        rows.append(
            f"""
            <tr>
                <td>{record.locker_id}</td>
                <td>{escape(record.phone)}</td>
                <td>{escape(record.pickup_code)}</td>
                <td>{escape(record.order_code or "---")}</td>
                <td>{escape(FLOW_LABELS.get(record.flow, record.flow))}</td>
                <td>{escape(status_text)}</td>
            </tr>
            """
        )

    return f"""
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Tủ</th>
                    <th>SĐT</th>
                    <th>Mã mở</th>
                    <th>Mã đơn</th>
                    <th>Loại</th>
                    <th>Trạng thái</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
            </tbody>
        </table>
    </div>
    """


def result_panel(
    title: str,
    lines: list[str],
    highlights: list[tuple[str, str]] | None = None,
    stacked_highlights: bool = False,
    tone: str = "success",
    redirect_url: str | None = None,
    redirect_delay_ms: int = 2500,
    extra_html: str = "",
) -> str:
    if not lines:
        return ""

    rows = "".join(f"<li>{escape(line)}</li>" for line in lines)
    highlight_html = ""
    if highlights:
        cards = "".join(
            f"""
            <div class="result-highlight-card">
                <span class="result-highlight-label">{escape(label)}</span>
                <strong class="result-highlight-value">{escape(value)}</strong>
            </div>
            """
            for label, value in highlights
        )
        highlight_class = "result-highlight-grid stacked" if stacked_highlights else "result-highlight-grid"
        highlight_html = f'<div class="{highlight_class}">{cards}</div>'

    redirect_attrs = ""
    if redirect_url:
        redirect_attrs = (
            f' data-redirect-url="{escape(redirect_url)}"'
            f' data-redirect-delay="{redirect_delay_ms}"'
        )

    return f"""
    <div class="result-modal-backdrop" data-result-modal{redirect_attrs}>
        <section class="result-modal {escape(tone)}" role="dialog" aria-modal="true" aria-labelledby="result-title">
            <button type="button" class="result-modal-close" data-close-result aria-label="Đóng">×</button>
            <h3 id="result-title">{escape(title)}</h3>
            {highlight_html}
            <ul>{rows}</ul>
            {extra_html}
            <button type="button" class="result-modal-button" data-close-result>
                {"Về trang chủ" if redirect_url else "Đã hiểu"}
            </button>
        </section>
    </div>
    """


def virtual_keyboard() -> str:
    full_keys = [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
        "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P",
        "A", "S", "D", "F", "G", "H", "J", "K", "L",
        "Z", "X", "C", "V", "B", "N", "M",
    ]
    number_keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
    email_keys = [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
        "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P",
        "A", "S", "D", "F", "G", "H", "J", "K", "L",
        "Z", "X", "C", "V", "B", "N", "M",
    ]
    full_buttons = "".join(
        (
            f'<button type="button" class="key" data-letter="{key.lower()}">{key}</button>'
            if key.isalpha()
            else f'<button type="button" class="key" data-key="{key}">{key}</button>'
        )
        for key in full_keys
    )
    number_buttons = "".join(
        f'<button type="button" class="key" data-key="{key}">{key}</button>' for key in number_keys
    )
    email_buttons = "".join(
        (
            f'<button type="button" class="key" data-letter="{key.lower()}">{key}</button>'
            if key.isalpha()
            else f'<button type="button" class="key" data-key="{key}">{key}</button>'
        )
        for key in email_keys
    )
    return f"""
    <div class="keyboard-shell">
        <div class="keyboard-topbar">
            <div class="keyboard-header">Bàn phím ảo</div>
            <button type="button" class="keyboard-close" data-action="hide-keyboard">Thu gọn</button>
        </div>
        <div class="keyboard-grid keyboard-grid-numeric" data-keyboard-mode="numeric">
            {number_buttons}
            <button type="button" class="key action backspace-key" data-action="backspace">⌫ Xóa 1 ký tự</button>
            <button type="button" class="key action" data-action="clear">Làm trống</button>
            <button type="button" class="key action" data-action="next">Ô kế tiếp</button>
        </div>
        <div class="keyboard-grid keyboard-grid-full" data-keyboard-mode="full">
            {full_buttons}
            <button type="button" class="key action wide" data-action="toggle-case">Chữ HOA</button>
            <button type="button" class="key wide" data-key="-">-</button>
            <button type="button" class="key wide" data-key=" ">Khoảng trắng</button>
            <button type="button" class="key action backspace-key" data-action="backspace">⌫ Xóa 1 ký tự</button>
            <button type="button" class="key action" data-action="clear">Làm trống</button>
            <button type="button" class="key action" data-action="next">Ô kế tiếp</button>
        </div>
        <div class="keyboard-grid keyboard-grid-email" data-keyboard-mode="email">
            {email_buttons}
            <button type="button" class="key action wide" data-action="toggle-case">Chữ HOA</button>
            <button type="button" class="key" data-key="@">@</button>
            <button type="button" class="key" data-key=".">.</button>
            <button type="button" class="key" data-key="_">_</button>
            <button type="button" class="key" data-key="-">-</button>
            <button type="button" class="key wide" data-key=".com">.com</button>
            <button type="button" class="key action backspace-key" data-action="backspace">⌫ Xóa 1 ký tự</button>
            <button type="button" class="key action" data-action="clear">Làm trống</button>
            <button type="button" class="key action" data-action="next">Ô kế tiếp</button>
        </div>
    </div>
    """


def page_template(
    title: str,
    content: str,
    show_keyboard: bool = False,
    enable_pickup_handoff_polling: bool = True,
    enable_admin_command_polling: bool = False,
) -> str:
    keyboard = virtual_keyboard() if show_keyboard else ""
    page_class = "page with-keyboard" if show_keyboard else "page"
    admin_modal_html = admin_command_modal() if enable_admin_command_polling else ""
    pickup_handoff_script = """
            const refreshPickupHandoff = async () => {
                try {
                    const response = await fetch("/api/pickup-handoff", { cache: "no-store" });
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}`);
                    }
                    const payload = await response.json();
                    const handoffId = Number(payload.id || 0);
                    if (!payload.has_pending || !payload.redirect_url || !handoffId || handoffId === lastPickupHandoffId) {
                        return;
                    }
                    lastPickupHandoffId = handoffId;
                    window.sessionStorage.setItem("smartlocker_pickup_handoff_id", String(handoffId));
                    window.location.href = payload.redirect_url;
                } catch (error) {
                    // Ignore transient polling failures.
                }
            };
    """ if enable_pickup_handoff_polling else ""
    page_script = """
    <script>
        (() => {
            const params = new URLSearchParams(window.location.search);
            const kioskModeRequested = params.has("_kiosk");
            const kioskModeActive = kioskModeRequested || window.sessionStorage.getItem("smartlocker_kiosk_mode") === "1";
            if (kioskModeActive) {
                window.sessionStorage.setItem("smartlocker_kiosk_mode", "1");
                document.documentElement.classList.add("kiosk-performance");
                document.body.classList.add("kiosk-performance");
            }

            const fields = Array.from(document.querySelectorAll("[data-touch-input='true']"));
            const keyboard = document.querySelector(".keyboard-shell");
            const closeButton = document.querySelector("[data-action='hide-keyboard']");
            const resultModal = document.querySelector("[data-result-modal]");
            const keyboardGrids = Array.from(document.querySelectorAll("[data-keyboard-mode]"));
            const letterButtons = Array.from(document.querySelectorAll(".keyboard-shell [data-letter]"));
            const caseToggleButtons = Array.from(document.querySelectorAll(".keyboard-shell [data-action='toggle-case']"));
            const resultRedirectUrl = resultModal?.dataset.redirectUrl || "";
            const resultRedirectDelay = Number(resultModal?.dataset.redirectDelay || 0);
            const liveClock = document.querySelector("[data-live-clock]");
            const liveDate = document.querySelector("[data-live-date]");
            const adminAlertHost = document.querySelector("[data-admin-alert-host]");
            const scrollControl = document.querySelector("[data-scroll-control]");
            const scrollTrack = document.querySelector("[data-scroll-track]");
            const scrollThumb = document.querySelector("[data-scroll-thumb]");
            const scrollUpButton = document.querySelector("[data-scroll-up]");
            const scrollDownButton = document.querySelector("[data-scroll-down]");
            const cameraModal = document.querySelector("[data-order-camera-modal]");
            const cameraVideo = document.querySelector("[data-order-camera-video]");
            const cameraStatus = document.querySelector("[data-order-camera-status]");
            const cameraCountdown = document.querySelector("[data-order-camera-countdown]");
            const cameraCaptureButton = document.querySelector("[data-capture-order-photo]");
            const cameraOpenButton = document.querySelector("[data-open-order-camera]");
            const cameraPhotoData = document.querySelector("[data-order-photo-data]");
            const cameraPreview = document.querySelector("[data-order-camera-preview]");
            const cameraPreviewImage = document.querySelector("[data-order-camera-preview-image]");
            let activeField = null;
            let redirectTimer = null;
            let keyboardUppercase = false;
            let scannerBuffer = "";
            let scannerLastKeyAt = 0;
            const scannerTarget = document.querySelector("[data-scanner-input='true']");
            const scannerMaxKeyGapMs = 120;
            let scrollDragStartY = 0;
            let scrollDragStartTop = 0;
            let cameraStream = null;
            let cameraCountdownTimer = null;
            let lastAdminAlertId = Number(window.sessionStorage.getItem("smartlocker_admin_alert_id") || 0);
            let lastPickupHandoffId = Number(window.sessionStorage.getItem("smartlocker_pickup_handoff_id") || 0);
            let scrollControlFrame = 0;
            const liveClockOptions = kioskModeActive
                ? { hour: "2-digit", minute: "2-digit" }
                : { hour: "2-digit", minute: "2-digit", second: "2-digit" };
            const liveClockIntervalMs = kioskModeActive ? 30000 : 1000;
            const pickupHandoffPollIntervalMs = kioskModeActive ? 5000 : 2000;
            const adminCommandPollIntervalMs = kioskModeActive ? 7000 : 3000;
            const scrollBehavior = kioskModeActive ? "auto" : "smooth";

            const updateLiveTime = () => {
                const now = new Date();
                if (liveClock) {
                    liveClock.textContent = now.toLocaleTimeString("vi-VN", liveClockOptions);
                }
                if (liveDate) {
                    liveDate.textContent = now.toLocaleDateString("vi-VN", {
                        weekday: "long",
                        day: "2-digit",
                        month: "2-digit",
                        year: "numeric",
                    });
                }
            };

            const updateKeyboardSpace = () => {
                if (!keyboard) {
                    document.documentElement.style.setProperty("--keyboard-space", "0px");
                    return;
                }
                const height = keyboard.classList.contains("visible") ? keyboard.offsetHeight : 0;
                document.documentElement.style.setProperty("--keyboard-space", `${height + 28}px`);
            };

            const updateScrollControl = () => {
                if (!scrollControl || !scrollTrack || !scrollThumb) return;
                const pageHeight = document.documentElement.scrollHeight;
                const maxScroll = Math.max(0, pageHeight - window.innerHeight);
                scrollControl.classList.toggle("is-hidden", maxScroll <= 2);
                if (maxScroll <= 2) return;

                const trackHeight = scrollTrack.clientHeight;
                const thumbHeight = Math.max(72, trackHeight * (window.innerHeight / pageHeight));
                const maxThumbTop = Math.max(0, trackHeight - thumbHeight);
                const thumbTop = maxScroll ? (window.scrollY / maxScroll) * maxThumbTop : 0;
                scrollThumb.style.height = `${Math.min(trackHeight, thumbHeight)}px`;
                scrollThumb.style.transform = `translateY(${thumbTop}px)`;
            };

            const scrollOnePage = (direction) => {
                window.scrollBy({ top: direction * window.innerHeight * 0.72, behavior: scrollBehavior });
            };

            if (scrollControl && scrollTrack && scrollThumb) {
                scrollControl.addEventListener("pointerdown", (event) => event.stopPropagation());
                scrollUpButton?.addEventListener("click", () => scrollOnePage(-1));
                scrollDownButton?.addEventListener("click", () => scrollOnePage(1));

                scrollThumb.addEventListener("pointerdown", (event) => {
                    event.preventDefault();
                    scrollDragStartY = event.clientY;
                    const transform = new DOMMatrixReadOnly(getComputedStyle(scrollThumb).transform);
                    scrollDragStartTop = transform.m42;
                    scrollThumb.setPointerCapture(event.pointerId);
                });

                scrollThumb.addEventListener("pointermove", (event) => {
                    if (!scrollThumb.hasPointerCapture(event.pointerId)) return;
                    event.preventDefault();
                    const maxThumbTop = Math.max(0, scrollTrack.clientHeight - scrollThumb.offsetHeight);
                    const nextTop = Math.max(0, Math.min(maxThumbTop, scrollDragStartTop + event.clientY - scrollDragStartY));
                    const maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
                    window.scrollTo(0, maxThumbTop ? (nextTop / maxThumbTop) * maxScroll : 0);
                });

                scrollTrack.addEventListener("pointerdown", (event) => {
                    if (event.target === scrollThumb) return;
                    const rect = scrollTrack.getBoundingClientRect();
                    const ratio = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
                    const maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
                    window.scrollTo({ top: ratio * maxScroll, behavior: scrollBehavior });
                });
            }

            const stopOrderCamera = () => {
                if (cameraCountdownTimer) {
                    window.clearInterval(cameraCountdownTimer);
                    cameraCountdownTimer = null;
                }
                if (cameraStream) {
                    cameraStream.getTracks().forEach((track) => track.stop());
                    cameraStream = null;
                }
                if (cameraVideo) cameraVideo.srcObject = null;
                cameraCountdown?.classList.add("is-hidden");
                if (cameraCaptureButton) cameraCaptureButton.disabled = false;
            };

            const closeOrderCamera = () => {
                stopOrderCamera();
                cameraModal?.classList.remove("visible");
                cameraModal?.setAttribute("aria-hidden", "true");
            };

            const openOrderCamera = async () => {
                if (!cameraModal || !cameraVideo || !cameraStatus || !cameraCaptureButton) return;
                hideKeyboard();
                cameraModal.classList.add("visible");
                cameraModal.setAttribute("aria-hidden", "false");
                cameraStatus.textContent = "Đang kết nối camera USB...";
                cameraCaptureButton.disabled = true;

                try {
                    if (!navigator.mediaDevices?.getUserMedia) {
                        throw new Error("Trình duyệt không hỗ trợ truy cập camera.");
                    }
                    cameraStream = await navigator.mediaDevices.getUserMedia({
                        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
                        audio: false,
                    });
                    cameraVideo.srcObject = cameraStream;
                    await cameraVideo.play();
                    cameraStatus.textContent = "Camera đã sẵn sàng. Nhấn nút bên dưới để chụp sau 5 giây.";
                    cameraCaptureButton.disabled = false;
                } catch (error) {
                    cameraStatus.textContent = `Không mở được camera: ${error?.message || "hãy kiểm tra kết nối và quyền camera."}`;
                }
            };

            const captureOrderPhoto = () => {
                if (!cameraVideo || !cameraPhotoData || !cameraPreview || !cameraPreviewImage) return;
                const sourceWidth = cameraVideo.videoWidth;
                const sourceHeight = cameraVideo.videoHeight;
                if (!sourceWidth || !sourceHeight) {
                    if (cameraStatus) cameraStatus.textContent = "Camera chưa có hình ảnh. Vui lòng thử lại.";
                    if (cameraCaptureButton) cameraCaptureButton.disabled = false;
                    return;
                }

                const targetMaxWidth = kioskModeActive ? 960 : 1280;
                const jpegQuality = kioskModeActive ? 0.74 : 0.86;
                const scale = Math.min(1, targetMaxWidth / sourceWidth);
                const canvas = document.createElement("canvas");
                canvas.width = Math.round(sourceWidth * scale);
                canvas.height = Math.round(sourceHeight * scale);
                canvas.getContext("2d").drawImage(cameraVideo, 0, 0, canvas.width, canvas.height);
                const photoData = canvas.toDataURL("image/jpeg", jpegQuality);
                cameraPhotoData.value = photoData;
                cameraPreviewImage.src = photoData;
                cameraPreview.classList.remove("is-hidden");
                if (cameraOpenButton) cameraOpenButton.textContent = "📷 Chụp lại ảnh đơn hàng";
                closeOrderCamera();
            };

            cameraOpenButton?.addEventListener("click", openOrderCamera);
            document.querySelectorAll("[data-close-order-camera]").forEach((button) => {
                button.addEventListener("click", closeOrderCamera);
            });
            cameraCaptureButton?.addEventListener("click", () => {
                if (!cameraStream || cameraCountdownTimer || !cameraCountdown) return;
                let secondsLeft = 5;
                cameraCaptureButton.disabled = true;
                cameraCountdown.textContent = String(secondsLeft);
                cameraCountdown.classList.remove("is-hidden");
                if (cameraStatus) cameraStatus.textContent = "Giữ đơn hàng trước camera...";
                cameraCountdownTimer = window.setInterval(() => {
                    secondsLeft -= 1;
                    cameraCountdown.textContent = String(Math.max(0, secondsLeft));
                    if (secondsLeft <= 0) {
                        window.clearInterval(cameraCountdownTimer);
                        cameraCountdownTimer = null;
                        captureOrderPhoto();
                    }
                }, 1000);
            });
            document.querySelector("[data-remove-order-photo]")?.addEventListener("click", () => {
                if (cameraPhotoData) cameraPhotoData.value = "";
                if (cameraPreviewImage) cameraPreviewImage.removeAttribute("src");
                cameraPreview?.classList.add("is-hidden");
                if (cameraOpenButton) cameraOpenButton.textContent = "📷 Chụp đơn hàng";
            });
            window.addEventListener("beforeunload", stopOrderCamera);

            const showKeyboard = () => {
                if (!keyboard) return;
                keyboard.classList.add("visible");
                document.body.classList.add("keyboard-visible");
                updateKeyboardSpace();
            };

            const hideKeyboard = () => {
                if (!keyboard) return;
                keyboard.classList.remove("visible");
                document.body.classList.remove("keyboard-visible");
                fields.forEach((item) => item.classList.remove("active-input"));
                activeField = null;
                updateKeyboardSpace();
            };

            const closeResultModal = () => {
                if (resultModal) {
                    resultModal.remove();
                }
                if (redirectTimer) {
                    window.clearTimeout(redirectTimer);
                    redirectTimer = null;
                }
                if (resultRedirectUrl) {
                    window.location.href = resultRedirectUrl;
                }
            };

            const fieldLabel = (field) => {
                const knownLabels = {
                    phone: "Số điện thoại",
                    email: "Email",
                    order_code: "Mã đơn hàng",
                    pickup_code: "Mã mở tủ",
                    phone_last4: "4 số cuối số điện thoại",
                    issue_type: "Loại sự cố",
                };
                const explicitLabel = field.id
                    ? document.querySelector(`label[for="${CSS.escape(field.id)}"]`)?.textContent?.trim()
                    : "";
                return explicitLabel || knownLabels[field.name] || field.name || "Thông tin bắt buộc";
            };

            const showFormValidationModal = (labels, firstField) => {
                document.querySelector("[data-form-validation-modal]")?.remove();
                hideKeyboard();

                const backdrop = document.createElement("div");
                backdrop.className = "result-modal-backdrop";
                backdrop.dataset.formValidationModal = "true";

                const dialog = document.createElement("section");
                dialog.className = "result-modal error";
                dialog.setAttribute("role", "alertdialog");
                dialog.setAttribute("aria-modal", "true");

                const title = document.createElement("h3");
                title.textContent = "Chưa nhập đủ thông tin";
                dialog.appendChild(title);

                const list = document.createElement("ul");
                const item = document.createElement("li");
                item.textContent = `Vui lòng nhập: ${labels.join(", ")}.`;
                list.appendChild(item);
                dialog.appendChild(list);

                const closeButton = document.createElement("button");
                closeButton.type = "button";
                closeButton.className = "result-modal-button";
                closeButton.textContent = "Quay lại nhập";
                dialog.appendChild(closeButton);
                backdrop.appendChild(dialog);

                const close = () => {
                    backdrop.remove();
                    if (firstField?.matches("[data-touch-input='true']")) {
                        setActive(firstField);
                    } else {
                        firstField?.focus();
                    }
                };
                closeButton.addEventListener("click", close);
                backdrop.addEventListener("click", (event) => {
                    if (event.target === backdrop) close();
                });
                document.body.appendChild(backdrop);
            };

            document.querySelectorAll("form").forEach((form) => {
                // Keep validation inside the kiosk UI instead of navigating to FastAPI's JSON 422 response.
                form.noValidate = true;
                form.addEventListener("submit", (event) => {
                    const missing = [];
                    const checkedGroups = new Set();
                    form.querySelectorAll("[required]").forEach((field) => {
                        const groupKey = field.type === "radio" ? `radio:${field.name}` : `field:${field.name || field.id}`;
                        if (checkedGroups.has(groupKey)) return;
                        checkedGroups.add(groupKey);

                        const isMissing = field.type === "radio"
                            ? !form.querySelector(`input[type="radio"][name="${CSS.escape(field.name)}"]:checked`)
                            : !String(field.value || "").trim();
                        if (isMissing) missing.push(field);
                    });

                    if (!missing.length) return;
                    event.preventDefault();
                    const labels = [...new Set(missing.map(fieldLabel))];
                    showFormValidationModal(labels, missing[0]);
                });
            });

            const ensureFieldVisible = (field) => {
                if (!field) return;
                window.requestAnimationFrame(() => {
                    field.scrollIntoView({ behavior: scrollBehavior, block: "center", inline: "nearest" });
                });
            };

            const applyKeyboardMode = (field) => {
                if (!keyboard) return;
                const requestedMode = field?.dataset.keyboardMode || "numeric";
                const mode = requestedMode === "full" || requestedMode === "email" ? requestedMode : "numeric";
                keyboard.dataset.activeMode = mode;
                keyboardGrids.forEach((grid) => {
                    grid.classList.toggle("active", grid.dataset.keyboardMode === mode);
                });
            };

            const updateLetterKeys = () => {
                letterButtons.forEach((button) => {
                    const letter = button.dataset.letter || "";
                    button.textContent = keyboardUppercase ? letter.toUpperCase() : letter.toLowerCase();
                });
                caseToggleButtons.forEach((button) => {
                    button.textContent = keyboardUppercase ? "Chữ HOA" : "chữ thường";
                });
            };

            const setActive = (field) => {
                if (!field) return;
                fields.forEach((item) => item.classList.remove("active-input"));
                activeField = field;
                activeField.classList.add("active-input");
                applyKeyboardMode(activeField);
                showKeyboard();
                ensureFieldVisible(activeField);
            };

            fields.forEach((field) => {
                field.addEventListener("pointerdown", (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setActive(field);
                });
                field.addEventListener("click", (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                });
                field.addEventListener("focus", () => setActive(field));
            });

            // Most USB/Bluetooth barcode and QR scanners act like a keyboard:
            // they type the decoded value quickly and finish with Enter or Tab.
            // Kiosk inputs remain readonly to suppress the system keyboard, so
            // collect that key stream here and place it in the scanner field.
            document.addEventListener("keydown", (event) => {
                if (!scannerTarget || event.ctrlKey || event.metaKey || event.altKey) return;

                const now = performance.now();
                if (event.key === "Enter" || event.key === "Tab") {
                    if (scannerBuffer.length >= 2) {
                        event.preventDefault();
                        scannerTarget.value = scannerBuffer.trim();
                        scannerTarget.dispatchEvent(new Event("input", { bubbles: true }));
                        scannerTarget.dispatchEvent(new Event("change", { bubbles: true }));
                        scannerTarget.classList.add("scanner-filled");
                        window.setTimeout(() => scannerTarget.classList.remove("scanner-filled"), 900);
                    }
                    scannerBuffer = "";
                    scannerLastKeyAt = 0;
                    return;
                }

                if (event.key.length === 1) {
                    if (scannerLastKeyAt && now - scannerLastKeyAt > scannerMaxKeyGapMs) {
                        scannerBuffer = "";
                    }
                    scannerBuffer += event.key;
                    scannerLastKeyAt = now;
                }
            });

            if (keyboard) {
                keyboard.addEventListener("pointerdown", (event) => {
                    event.stopPropagation();
                });
                keyboard.addEventListener("click", (event) => {
                    event.stopPropagation();
                });
            }

            document.querySelectorAll(".keyboard-shell .key").forEach((button) => {
                button.addEventListener("click", () => {
                    if (!activeField) return;
                    const action = button.dataset.action;
                    const key = button.dataset.letter
                        ? (keyboardUppercase ? button.dataset.letter.toUpperCase() : button.dataset.letter.toLowerCase())
                        : (button.dataset.key || "");

                    if (action === "backspace") {
                        activeField.value = activeField.value.slice(0, -1);
                    } else if (action === "clear") {
                        activeField.value = "";
                    } else if (action === "toggle-case") {
                        keyboardUppercase = !keyboardUppercase;
                        updateLetterKeys();
                    } else if (action === "next") {
                        const index = fields.indexOf(activeField);
                        const nextField = fields[(index + 1) % fields.length];
                        setActive(nextField);
                    } else {
                        activeField.value += key;
                    }

                    activeField.dispatchEvent(new Event("input", { bubbles: true }));
                    ensureFieldVisible(activeField);
                });
            });

            if (closeButton) {
                closeButton.addEventListener("click", () => hideKeyboard());
            }

            if (resultModal) {
                hideKeyboard();
                resultModal.querySelectorAll("[data-close-result]").forEach((button) => {
                    button.addEventListener("click", () => closeResultModal());
                });
                resultModal.addEventListener("click", (event) => {
                    if (event.target === resultModal) {
                        closeResultModal();
                    }
                });
                if (resultRedirectUrl && resultRedirectDelay > 0) {
                    redirectTimer = window.setTimeout(() => {
                        window.location.href = resultRedirectUrl;
                    }, resultRedirectDelay);
                }
            }

            document.addEventListener("pointerdown", (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                if (target.closest(".keyboard-shell") || target.closest("[data-touch-input='true']") || target.closest(".result-modal") || target.closest("[data-scroll-control]")) return;
                hideKeyboard();
            });

            const queueScrollControlUpdate = () => {
                if (scrollControlFrame) return;
                scrollControlFrame = window.requestAnimationFrame(() => {
                    scrollControlFrame = 0;
                    updateScrollControl();
                });
            };

            window.addEventListener("scroll", queueScrollControlUpdate, { passive: true });
            window.addEventListener("resize", () => {
                updateKeyboardSpace();
                queueScrollControlUpdate();
            });
            if (window.visualViewport) {
                window.visualViewport.addEventListener("resize", updateKeyboardSpace);
            }

            updateLiveTime();
            window.setInterval(updateLiveTime, liveClockIntervalMs);
            updateLetterKeys();
            updateKeyboardSpace();
            updateScrollControl();
            queueScrollControlUpdate();

""" + ("""
            const refreshAdminCommand = async () => {
                if (document.visibilityState === "hidden") return;
                try {
                    const response = await fetch("/api/admin-command", { cache: "no-store" });
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}`);
                    }
                    const payload = await response.json();
                    if (adminAlertHost) {
                        const commandId = Number(payload.id || 0);
                        const shouldShowModal = Boolean(payload.has_pending && payload.modal_html && commandId && commandId !== lastAdminAlertId);
                        adminAlertHost.innerHTML = shouldShowModal ? payload.modal_html : "";
                        if (shouldShowModal) {
                            lastAdminAlertId = commandId;
                            window.sessionStorage.setItem("smartlocker_admin_alert_id", String(commandId));
                            hideKeyboard();
                        }
                    }
                } catch (error) {
                    // Keep the last visible state if polling fails.
                }
            };
""" if enable_admin_command_polling else "") + """
""" + pickup_handoff_script + """
""" + ("""
            refreshAdminCommand();
            window.setInterval(refreshAdminCommand, adminCommandPollIntervalMs);
""" if enable_admin_command_polling else "") + """
""" + ("""
            const refreshPickupHandoffWithVisibility = async () => {
                if (document.visibilityState === "hidden") return;
                await refreshPickupHandoff();
            };
            refreshPickupHandoffWithVisibility();
            window.setInterval(refreshPickupHandoffWithVisibility, pickupHandoffPollIntervalMs);
""" if enable_pickup_handoff_polling else "") + """
        })();
    </script>
    """

    return f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
        <title>{escape(title)}</title>
        <style>
            :root {{
                --bg-main: #ffffff;
                --keyboard-space: 0px;
                --panel: #ffffff;
                --panel-strong: #f2f8ff;
                --border: rgba(20, 86, 170, 0.16);
                --text: #0b4fae;
                --muted: #4f79b5;
                --accent: #0b4fae;
                --free: #ffffff;
                --busy: #eef6ff;
                --shadow: 0 18px 40px rgba(10, 67, 138, 0.08);
            }}

            * {{
                box-sizing: border-box;
            }}

            *:not(input):not(textarea) {{
                -webkit-user-select: none;
                user-select: none;
                -webkit-tap-highlight-color: transparent;
            }}

            html {{
                scroll-behavior: smooth;
                touch-action: pan-y;
                scrollbar-width: none;
            }}

            html.kiosk-performance {{
                scroll-behavior: auto;
            }}

            html::-webkit-scrollbar {{
                display: none;
            }}

            body {{
                margin: 0;
                font-family: "Aptos", "Segoe UI Variable", "Segoe UI", Tahoma, sans-serif;
                color: var(--text);
                background:
                    radial-gradient(circle at top left, rgba(209, 232, 255, 0.9), transparent 24%),
                    radial-gradient(circle at right 12%, rgba(133, 193, 255, 0.16), transparent 22%),
                    linear-gradient(180deg, #fbfdff 0%, #eef5ff 100%);
                min-height: 100dvh;
                overflow-x: hidden;
                touch-action: pan-y;
                padding-right: 72px;
            }}

            body.kiosk-performance {{
                background: #eef5ff;
            }}

            .kiosk-performance * {{
                animation: none !important;
            }}

            .kiosk-scroll-control {{
                position: fixed;
                z-index: 90;
                top: 18px;
                right: 8px;
                bottom: 18px;
                width: 58px;
                display: flex;
                flex-direction: column;
                gap: 10px;
                padding: 6px;
                border: 2px solid rgba(11, 79, 174, 0.2);
                border-radius: 24px;
                background: rgba(255, 255, 255, 0.96);
                box-shadow: 0 12px 30px rgba(10, 67, 138, 0.18);
                touch-action: none;
            }}

            .kiosk-performance .kiosk-scroll-control {{
                background: #ffffff;
                box-shadow: none;
            }}

            .kiosk-scroll-control.is-hidden {{
                display: none;
            }}

            .kiosk-scroll-button {{
                flex: 0 0 48px;
                width: 100%;
                border: 0;
                border-radius: 16px;
                background: #0b4fae;
                color: #ffffff;
                font-size: 1.45rem;
                font-weight: 900;
                line-height: 1;
                cursor: pointer;
                touch-action: manipulation;
            }}

            .kiosk-scroll-button:active {{
                background: #073b82;
                transform: scale(0.96);
            }}

            .kiosk-scroll-track {{
                position: relative;
                flex: 1 1 auto;
                min-height: 120px;
                border-radius: 18px;
                background: #dbeaff;
                overflow: hidden;
                touch-action: none;
            }}

            .kiosk-scroll-thumb {{
                position: absolute;
                top: 0;
                left: 4px;
                right: 4px;
                min-height: 72px;
                border-radius: 14px;
                background: linear-gradient(180deg, #2e87ef, #0b4fae);
                box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.32);
                cursor: grab;
                touch-action: none;
            }}

            .kiosk-performance .kiosk-scroll-thumb {{
                background: #0b4fae;
                box-shadow: none;
            }}

            .kiosk-scroll-thumb:active {{
                cursor: grabbing;
            }}

            .page {{
                max-width: 1180px;
                min-height: 100dvh;
                margin: 0 auto;
                padding: 14px clamp(18px, 2.5vw, 30px) 22px;
                display: flex;
                flex-direction: column;
                touch-action: pan-y;
            }}

            .page.with-keyboard {{
                padding-bottom: calc(32px + var(--keyboard-space));
            }}

            .topbar {{
                flex: 0 0 auto;
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 16px;
                margin-bottom: 10px;
            }}

            .admin-alert-backdrop {{
                position: fixed;
                inset: 0;
                z-index: 120;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 28px;
                background: rgba(11, 34, 69, 0.36);
                backdrop-filter: blur(6px);
            }}

            .kiosk-performance .admin-alert-backdrop {{
                backdrop-filter: none;
            }}

            .admin-alert {{
                width: min(760px, 100%);
                border-radius: 32px;
                padding: 30px 28px;
                background: linear-gradient(180deg, #fff8f8 0%, #ffe9e9 100%);
                border: 2px solid rgba(220, 38, 38, 0.24);
                color: #7f1d1d;
                box-shadow: 0 28px 64px rgba(15, 23, 42, 0.28);
                text-align: center;
            }}

            .admin-alert-badge {{
                display: inline-block;
                padding: 10px 16px;
                border-radius: 999px;
                background: rgba(220, 38, 38, 0.14);
                color: #b91c1c;
                font-weight: 800;
                margin-bottom: 16px;
            }}

            .admin-alert h2 {{
                margin: 0 0 14px;
                font-size: clamp(2rem, 4vw, 3rem);
            }}

            .admin-alert p {{
                margin: 10px 0 0;
                font-size: 1.08rem;
                line-height: 1.55;
            }}

            .admin-alert-note {{
                font-weight: 700;
            }}

            .brand-chip, .clock-card {{
                border: 1px solid rgba(20, 86, 170, 0.14);
                background: rgba(255, 255, 255, 0.86);
                box-shadow: var(--shadow);
                backdrop-filter: blur(10px);
                border-radius: 18px;
                padding: 12px 16px;
            }}

            .kiosk-performance .brand-chip,
            .kiosk-performance .clock-card,
            .kiosk-performance .panel,
            .kiosk-performance .home-link,
            .kiosk-performance .assist-card {{
                backdrop-filter: none;
                box-shadow: none;
                background: #ffffff;
            }}

            .brand-chip {{
                font-weight: 800;
                letter-spacing: 0.04em;
                text-transform: uppercase;
                color: #0b4fae;
            }}

            .clock-card {{
                min-width: 220px;
                text-align: right;
            }}

            .clock-time {{
                display: block;
                font-size: clamp(1.4rem, 2.6vw, 2.2rem);
                font-weight: 800;
                line-height: 1.1;
            }}

            .clock-date {{
                display: block;
                margin-top: 6px;
                color: var(--muted);
                text-transform: capitalize;
            }}

            .hero {{
                display: flex;
                justify-content: space-between;
                gap: 14px;
                align-items: center;
                margin-bottom: 16px;
            }}

            .hero.centered {{
                justify-content: center;
                text-align: center;
            }}

            .hero h1 {{
                margin: 0;
                font-size: clamp(1.7rem, 3.6vw, 2.9rem);
                line-height: 1.06;
            }}

            .hero p {{
                margin: 6px 0 0;
                color: var(--muted);
                font-size: 0.98rem;
                line-height: 1.5;
            }}

            .home-link {{
                text-decoration: none;
                color: #0b4fae;
                background: rgba(255, 255, 255, 0.9);
                padding: 12px 16px;
                border-radius: 16px;
                font-weight: 700;
                border: 1px solid rgba(20, 86, 170, 0.14);
                box-shadow: var(--shadow);
            }}

            .panel {{
                background: rgba(255, 255, 255, 0.92);
                border: 1px solid var(--border);
                box-shadow: var(--shadow);
                border-radius: 22px;
                padding: 18px;
            }}

            .button-grid {{
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 14px;
                margin: 16px auto 14px;
                width: 100%;
                max-width: 1040px;
                align-items: stretch;
            }}

            .action-send {{
                grid-column: auto;
            }}

            .action-deliver {{
                grid-column: auto;
            }}

            .action-receive {{
                grid-column: auto;
            }}

            .main-button {{
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: flex-start;
                text-align: center;
                text-decoration: none;
                color: #ffffff;
                border-radius: 24px;
                padding: 18px 18px 20px;
                min-height: 160px;
                background: linear-gradient(180deg, #1f80db, #0d56b4);
                border: 1px solid rgba(11, 79, 174, 0.2);
                box-shadow: var(--shadow);
                height: 100%;
                position: relative;
                overflow: hidden;
            }}

            .kiosk-performance .main-button,
            .kiosk-performance .action-send,
            .kiosk-performance .action-deliver,
            .kiosk-performance .action-receive,
            .kiosk-performance .main-button.secondary {{
                background: #0b4fae;
                box-shadow: none;
            }}

            .kiosk-performance .main-button.secondary,
            .kiosk-performance .main-button.secondary span,
            .kiosk-performance .main-button.secondary .button-icon svg {{
                color: #ffffff;
                stroke: #ffffff;
            }}

            .kiosk-performance .main-button::after,
            .kiosk-performance .button-icon,
            .kiosk-performance .assist-card-icon {{
                background: rgba(255, 255, 255, 0.12);
                box-shadow: none;
            }}

            .main-button::after {{
                content: "";
                position: absolute;
                inset: auto -18% -26% auto;
                width: 120px;
                height: 120px;
                border-radius: 999px;
                background: rgba(255, 255, 255, 0.08);
                pointer-events: none;
            }}

            .action-send {{
                background: linear-gradient(180deg, #1f8fca, #1266b2);
            }}

            .action-deliver {{
                background: linear-gradient(180deg, #177bc5, #0b4fae);
            }}

            .action-receive {{
                background: linear-gradient(180deg, #0f6fc6, #0a489c);
            }}

            .main-button.secondary {{
                color: #0b4fae;
                background: linear-gradient(180deg, #ffffff, #edf5ff);
            }}

            .main-button.secondary .button-icon {{
                background: rgba(11, 79, 174, 0.08);
                border-color: rgba(11, 79, 174, 0.12);
            }}

            .main-button.secondary .button-icon svg {{
                stroke: #0b4fae;
            }}

            .button-icon {{
                width: 58px;
                height: 58px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                border-radius: 18px;
                margin-bottom: 12px;
                background: rgba(255, 255, 255, 0.16);
                border: 1px solid rgba(255, 255, 255, 0.2);
            }}

            .button-icon svg {{
                width: 30px;
                height: 30px;
                stroke: #ffffff;
                stroke-width: 1.9;
                fill: none;
                stroke-linecap: round;
                stroke-linejoin: round;
            }}

            .main-button strong {{
                display: block;
                font-size: 1.6rem;
                margin-bottom: 0;
                letter-spacing: -0.02em;
            }}

            .main-button span {{
                margin-top: 6px;
                font-size: 0.95rem;
                line-height: 1.4;
                opacity: 0.92;
            }}

            .main-button.secondary span {{
                color: #4f79b5;
            }}

            .kiosk-center {{
                flex: 1 1 auto;
                min-height: 0;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: clamp(12px, 2.8vh, 24px) 0 clamp(10px, 2.4vh, 20px);
            }}

            .compact-home {{
                width: 100%;
                max-width: 1180px;
                margin: 0 auto;
            }}

            .home-hero {{
                max-width: 620px;
                margin: 0 auto;
            }}

            .home-hero h1 {{
                font-size: clamp(2.1rem, 4vw, 3.1rem);
            }}

            .home-actions {{
                grid-template-columns: repeat(2, minmax(320px, 420px));
                justify-content: center;
                max-width: 900px;
            }}

            .assist-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(240px, 340px));
                justify-content: center;
                gap: 12px;
                width: 100%;
                max-width: 900px;
                margin: 10px auto 0;
                align-items: stretch;
            }}

            .assist-card {{
                text-decoration: none;
                color: #0b4fae;
                background: rgba(255, 255, 255, 0.92);
                border: 1px solid rgba(20, 86, 170, 0.14);
                box-shadow: var(--shadow);
                border-radius: 18px;
                padding: 14px 16px;
                min-height: 74px;
                display: flex;
                flex-direction: row;
                align-items: center;
                gap: 12px;
                justify-content: flex-start;
            }}

            .assist-card-icon {{
                width: 42px;
                height: 42px;
                border-radius: 14px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                background: linear-gradient(180deg, #eff6ff, #dbeafe);
                border: 1px solid rgba(20, 86, 170, 0.12);
                flex-shrink: 0;
            }}

            .assist-card-icon svg {{
                width: 20px;
                height: 20px;
                stroke: currentColor;
                stroke-width: 1.9;
                fill: none;
                stroke-linecap: round;
                stroke-linejoin: round;
            }}

            .assist-card-copy {{
                min-width: 0;
            }}

            .assist-card strong {{
                display: block;
                font-size: 1rem;
                margin-bottom: 4px;
            }}

            .assist-card span {{
                color: var(--muted);
                line-height: 1.45;
                font-size: 0.92rem;
            }}

            .portal-panel {{
                margin: 10px auto 0;
                width: min(1000px, 100%);
                display: grid;
                grid-template-columns: minmax(0, 1.35fr) 220px;
                gap: 18px;
                align-items: center;
                padding: 18px 20px;
            }}

            .portal-copy {{
                min-width: 0;
                display: flex;
                flex-direction: column;
                justify-content: center;
            }}

            .portal-copy h2 {{
                margin: 0 0 8px;
                font-size: 1.12rem;
            }}

            .portal-copy p {{
                margin: 0;
                color: var(--muted);
                line-height: 1.45;
                font-size: 0.93rem;
            }}

            .portal-note {{
                margin-top: 10px;
                padding: 10px 12px;
                border-radius: 16px;
                background: #f6faff;
                border: 1px solid rgba(20, 86, 170, 0.1);
                color: #0b4fae;
                line-height: 1.45;
                font-size: 0.9rem;
            }}

            .portal-note strong {{
                display: block;
                margin-bottom: 6px;
            }}

            .portal-qr-shell {{
                display: flex;
                justify-content: center;
                align-items: center;
            }}

            .portal-qr-card {{
                width: min(100%, 290px);
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 8px;
                padding: 12px;
                border-radius: 18px;
                background: #eef6ff;
                border: 1px solid rgba(20, 86, 170, 0.14);
                text-align: center;
                color: #4f79b5;
                line-height: 1.4;
                font-size: 0.86rem;
            }}

            .portal-qr-image {{
                width: min(138px, 100%);
                height: auto;
                border-radius: 14px;
                background: #ffffff;
                padding: 7px;
                border: 1px solid rgba(20, 86, 170, 0.14);
            }}

            .compact-home .portal-panel {{
                grid-template-columns: minmax(0, 1.15fr) 200px;
                gap: 16px;
            }}

            .compact-home .portal-copy h2 {{
                margin-bottom: 6px;
            }}

            .compact-home .portal-note {{
                margin-top: 8px;
            }}

            .compact-home .portal-qr-card {{
                width: 200px;
                padding: 12px;
            }}

            .compact-home .portal-qr-image {{
                width: 124px;
            }}

            .layout {{
                display: grid;
                grid-template-columns: 1.15fr 0.85fr;
                gap: 16px;
                margin-top: 16px;
            }}

            .home-layout {{
                align-items: start;
            }}

            .flow-layout {{
                grid-template-columns: minmax(0, 1fr) minmax(320px, 0.82fr);
                align-items: start;
            }}

            .locker-grid {{
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 12px;
            }}

            .locker-card {{
                border-radius: 18px;
                padding: 12px;
                border: 1px solid rgba(20, 86, 170, 0.12);
                min-height: 122px;
            }}

            .locker-card.free {{
                background: #ffffff;
            }}

            .locker-card.busy {{
                background: #eef6ff;
            }}

            .locker-top {{
                display: flex;
                justify-content: space-between;
                gap: 12px;
                align-items: center;
                margin-bottom: 10px;
            }}

            .locker-name {{
                font-size: 1.08rem;
                font-weight: 700;
            }}

            .locker-badge {{
                font-size: 0.82rem;
                padding: 6px 10px;
                border-radius: 999px;
                background: #e8f2ff;
            }}

            .locker-meta {{
                color: var(--muted);
                margin-top: 10px;
                line-height: 1.45;
            }}

            .stack {{
                display: grid;
                gap: 16px;
            }}

            .panel h2, .panel h3 {{
                margin-top: 0;
            }}

            .panel h2 {{
                font-size: 1.4rem;
                margin-bottom: 12px;
            }}

            .form-grid {{
                display: grid;
                gap: 14px;
                margin-top: 14px;
            }}

            .form-actions {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 12px;
            }}

            .is-hidden {{
                display: none !important;
            }}

            .order-camera-field {{
                display: grid;
                gap: 10px;
            }}

            .order-camera-label {{
                font-size: 0.96rem;
                font-weight: 700;
            }}

            .order-camera-open {{
                width: 100%;
                min-height: 68px;
                border: 2px dashed rgba(11, 79, 174, 0.42);
                border-radius: 18px;
                background: #eef6ff;
                color: #0b4fae;
                font-size: 1.15rem;
                font-weight: 800;
                cursor: pointer;
            }}

            .order-camera-preview {{
                display: grid;
                grid-template-columns: minmax(160px, 240px) 1fr;
                align-items: center;
                gap: 16px;
                padding: 12px;
                border: 2px solid rgba(21, 148, 71, 0.24);
                border-radius: 18px;
                background: #effcf3;
                color: #126b36;
            }}

            .order-camera-preview img {{
                width: 100%;
                max-height: 150px;
                border-radius: 12px;
                object-fit: cover;
                background: #dbeaff;
            }}

            .order-camera-preview strong {{
                display: block;
                margin-bottom: 10px;
            }}

            .order-camera-preview button {{
                min-height: 44px;
                padding: 8px 16px;
                border: 0;
                border-radius: 12px;
                background: #dff5e7;
                color: #126b36;
                font-weight: 800;
            }}

            .order-camera-backdrop {{
                position: fixed;
                inset: 0;
                z-index: 150;
                display: none;
                align-items: center;
                justify-content: center;
                padding: 22px;
                background: rgba(3, 24, 57, 0.82);
            }}

            .order-camera-backdrop.visible {{
                display: flex;
            }}

            .order-camera-dialog {{
                width: min(920px, 100%);
                max-height: calc(100vh - 28px);
                overflow-y: auto;
                padding: 20px;
                border-radius: 26px;
                background: #ffffff;
                box-shadow: 0 24px 60px rgba(0, 0, 0, 0.36);
            }}

            .kiosk-performance .order-camera-dialog,
            .kiosk-performance .result-modal,
            .kiosk-performance .keyboard-shell {{
                box-shadow: none;
            }}

            .order-camera-head {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 16px;
                margin-bottom: 14px;
            }}

            .order-camera-head h3 {{
                margin: 0;
                font-size: 1.55rem;
            }}

            .order-camera-head button {{
                width: 52px;
                height: 52px;
                border: 0;
                border-radius: 50%;
                background: #dcebff;
                color: #0b4fae;
                font-size: 1.8rem;
            }}

            .order-camera-view {{
                position: relative;
                overflow: hidden;
                min-height: 360px;
                border-radius: 20px;
                background: #071529;
            }}

            .order-camera-view video {{
                display: block;
                width: 100%;
                height: min(58vh, 540px);
                object-fit: contain;
            }}

            .order-camera-countdown {{
                position: absolute;
                inset: 0;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #ffffff;
                background: rgba(3, 24, 57, 0.3);
                font-size: clamp(7rem, 24vw, 14rem);
                font-weight: 900;
                text-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
            }}

            .order-camera-dialog > p {{
                margin: 14px 0;
                text-align: center;
                font-weight: 700;
            }}

            .order-camera-actions {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 12px;
            }}

            label {{
                display: block;
                font-size: 0.96rem;
                margin-bottom: 8px;
                font-weight: 600;
            }}

            input {{
                width: 100%;
                border-radius: 16px;
                border: 2px solid rgba(20, 86, 170, 0.12);
                padding: 18px 16px;
                font-size: 1.22rem;
                background: #ffffff;
                color: #07315d;
                outline: none;
                scroll-margin-bottom: calc(var(--keyboard-space) + 24px);
                -webkit-user-select: text;
                user-select: text;
            }}

            input.active-input {{
                border-color: var(--accent);
                box-shadow: 0 0 0 4px rgba(11, 79, 174, 0.12);
            }}

            input.scanner-filled {{
                border-color: #159447;
                box-shadow: 0 0 0 4px rgba(21, 148, 71, 0.16);
            }}

            .submit-button {{
                width: 100%;
                border: 1px solid rgba(20, 86, 170, 0.14);
                border-radius: 18px;
                padding: 18px;
                font-size: 1.18rem;
                font-weight: 800;
                color: #ffffff;
                background: linear-gradient(180deg, #1678d8, #0b4fae);
                cursor: pointer;
            }}

            .submit-button.secondary {{
                color: #0b4fae;
                background: #eef6ff;
            }}

            .result-panel {{
                margin-top: 16px;
                border-radius: 18px;
                padding: 16px 18px;
                background: rgba(255, 255, 255, 0.16);
                border: 1px solid var(--border);
            }}

            .result-panel ul {{
                margin: 0;
                padding-left: 22px;
                line-height: 1.6;
            }}

            .issue-options-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 14px;
                margin-top: 8px;
            }}

            .issue-option {{
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 18px 16px;
                border-radius: 18px;
                border: 2px solid rgba(20, 86, 170, 0.12);
                background: #ffffff;
                cursor: pointer;
                margin-bottom: 0;
                font-weight: 700;
                color: #07315d;
            }}

            .issue-option input {{
                width: 22px;
                height: 22px;
                margin: 0;
                padding: 0;
                flex: 0 0 auto;
            }}

            .issue-option:has(input:checked) {{
                border-color: var(--accent);
                box-shadow: 0 0 0 4px rgba(11, 79, 174, 0.12);
                background: #f6fbff;
            }}

            .result-modal-backdrop {{
                position: fixed;
                inset: 0;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 24px;
                background: rgba(11, 79, 174, 0.12);
                backdrop-filter: blur(4px);
                z-index: 30;
            }}

            .kiosk-performance .result-modal-backdrop {{
                backdrop-filter: none;
            }}

            .result-modal {{
                position: relative;
                width: min(760px, 100%);
                border-radius: 28px;
                padding: 28px 26px 24px;
                background: linear-gradient(180deg, rgba(255, 255, 255, 1), rgba(242, 248, 255, 0.98));
                box-shadow: 0 24px 60px rgba(10, 67, 138, 0.12);
                color: #0b4fae;
            }}

            .result-modal.success {{
                border: 3px solid rgba(34, 125, 215, 0.28);
            }}

            .result-modal.error {{
                border: 3px solid rgba(191, 70, 70, 0.18);
            }}

            .result-modal h3 {{
                margin: 0 40px 18px 0;
                font-size: clamp(1.6rem, 3vw, 2.3rem);
            }}

            .result-highlight-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 14px;
                margin-bottom: 18px;
            }}

            .result-highlight-grid.stacked {{
                grid-template-columns: minmax(0, 1fr);
            }}

            .result-highlight-card {{
                border-radius: 22px;
                padding: 18px;
                background: #eef6ff;
                border: 1px solid rgba(20, 86, 170, 0.14);
                color: #0b4fae;
                text-align: center;
            }}

            .result-highlight-label {{
                display: block;
                font-size: 0.95rem;
                opacity: 0.9;
                margin-bottom: 8px;
            }}

            .result-highlight-value {{
                display: block;
                font-size: clamp(1.8rem, 4vw, 2.7rem);
                letter-spacing: 0.04em;
            }}

            .result-modal ul {{
                margin: 0;
                padding-left: 22px;
                line-height: 1.7;
                color: #225791;
                font-size: 1.02rem;
            }}

            .result-modal-close {{
                position: absolute;
                top: 16px;
                right: 16px;
                width: 42px;
                height: 42px;
                border: 0;
                border-radius: 999px;
                background: #dcebff;
                color: #0b4fae;
                font-size: 1.6rem;
                cursor: pointer;
            }}

            .result-modal-button {{
                margin-top: 20px;
                width: 100%;
                border: 1px solid rgba(20, 86, 170, 0.14);
                border-radius: 18px;
                padding: 18px;
                background: linear-gradient(180deg, #1678d8, #0b4fae);
                color: #ffffff;
                font-size: 1.15rem;
                font-weight: 800;
                cursor: pointer;
            }}

            .result-qr-block {{
                margin-top: 18px;
            }}

            .result-qr-card {{
                display: grid;
                grid-template-columns: 220px 1fr;
                gap: 18px;
                align-items: center;
                padding: 18px;
                border-radius: 22px;
                background: #eef6ff;
                border: 1px solid rgba(20, 86, 170, 0.14);
            }}

            .result-qr-image {{
                width: 220px;
                height: 220px;
                border-radius: 16px;
                background: #ffffff;
                padding: 8px;
                border: 1px solid rgba(20, 86, 170, 0.14);
            }}

            .result-qr-fallback {{
                width: 220px;
                min-height: 220px;
                border-radius: 16px;
                background: #ffffff;
                border: 1px dashed rgba(20, 86, 170, 0.24);
                color: #4f79b5;
                display: flex;
                align-items: center;
                justify-content: center;
                text-align: center;
                padding: 18px;
            }}

            .result-qr-copy {{
                display: flex;
                flex-direction: column;
                gap: 10px;
            }}

            .result-qr-label {{
                font-size: 0.95rem;
                color: #4f79b5;
            }}

            .result-qr-value {{
                font-size: 1.35rem;
                letter-spacing: 0.08em;
                color: #0b4fae;
                word-break: break-word;
            }}

            .result-qr-link {{
                color: #0b4fae;
                font-weight: 700;
                text-decoration: none;
            }}

            .table-wrap {{
                overflow: auto;
                border-radius: 18px;
                background: #f7fbff;
                border: 1px solid rgba(20, 86, 170, 0.08);
                -webkit-overflow-scrolling: touch;
                touch-action: pan-x pan-y;
            }}

            .kiosk-user-summary {{
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 12px;
            }}

            .kiosk-summary-card {{
                border-radius: 18px;
                padding: 14px 16px;
                background: linear-gradient(180deg, #ffffff 0%, #f4f9ff 100%);
                border: 1px solid rgba(20, 86, 170, 0.12);
            }}

            .kiosk-summary-card span {{
                display: block;
                color: var(--muted);
                font-size: 0.88rem;
                margin-bottom: 8px;
            }}

            .kiosk-summary-card strong {{
                display: block;
                color: var(--accent);
                font-size: 1.18rem;
                line-height: 1.35;
                overflow-wrap: anywhere;
            }}

            .kiosk-user-orders td {{
                vertical-align: top;
            }}

            .kiosk-user-stack {{
                display: grid;
                gap: 8px;
            }}

            .kiosk-user-stack > div {{
                padding: 10px 12px;
                border-radius: 14px;
                background: #ffffff;
                border: 1px solid rgba(18, 93, 160, 0.08);
            }}

            .kiosk-user-stack-label {{
                display: inline-block;
                font-size: 0.78rem;
                font-weight: 700;
                letter-spacing: 0.02em;
                text-transform: uppercase;
                color: var(--muted);
            }}

            .kiosk-user-stack strong {{
                display: block;
                margin-top: 4px;
                line-height: 1.45;
                color: #0d3b66;
                word-break: break-word;
            }}

            .kiosk-user-time {{
                white-space: nowrap;
                color: var(--muted);
                font-weight: 600;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                min-width: 540px;
            }}

            th, td {{
                padding: 12px 10px;
                text-align: left;
                border-bottom: 1px solid rgba(20, 86, 170, 0.08);
                font-size: 0.92rem;
            }}

            th {{
                background: #eef6ff;
            }}

            .empty-box {{
                border-radius: 18px;
                padding: 18px;
                background: #f7fbff;
                color: var(--muted);
                border: 1px solid rgba(20, 86, 170, 0.08);
            }}

            .note {{
                margin-top: 16px;
                color: var(--muted);
                line-height: 1.6;
            }}

            .info-list {{
                display: grid;
                gap: 14px;
                margin-top: 18px;
            }}

            .info-item {{
                border-radius: 18px;
                padding: 16px 18px;
                background: #f6faff;
                border: 1px solid rgba(20, 86, 170, 0.1);
            }}

            .info-item strong {{
                display: block;
                margin-bottom: 6px;
            }}

            .keyboard-topbar {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 12px;
                margin-bottom: 8px;
                position: sticky;
                top: 0;
                z-index: 1;
                padding-bottom: 8px;
                background: #f4f9ff;
            }}

            .keyboard-shell {{
                position: fixed;
                left: 16px;
                right: 16px;
                bottom: 16px;
                padding: 12px 16px calc(16px + env(safe-area-inset-bottom, 0px));
                background: #ffffff;
                border: 1px solid rgba(20, 86, 170, 0.14);
                border-radius: 24px;
                max-height: min(420px, calc(100vh - 24px));
                overflow: hidden;
                display: flex;
                flex-direction: column;
                transform: translateY(calc(100% + 16px));
                opacity: 0;
                pointer-events: none;
                transition: transform 0.22s ease, opacity 0.22s ease;
                z-index: 60;
                box-shadow: 0 20px 48px rgba(3, 24, 57, 0.32);
            }}

            .kiosk-performance .keyboard-shell {{
                transition: none;
            }}

            .keyboard-shell.visible {{
                transform: translateY(0);
                opacity: 1;
                pointer-events: auto;
            }}

            .keyboard-header {{
                font-weight: 700;
                color: #0b4fae;
            }}

            .keyboard-close {{
                border: 0;
                border-radius: 999px;
                padding: 10px 14px;
                background: #eef6ff;
                color: #0b4fae;
                font-weight: 700;
                cursor: pointer;
            }}

            .keyboard-grid {{
                display: none;
                gap: 10px;
                overflow-y: auto;
                padding-right: 2px;
                -webkit-overflow-scrolling: touch;
                touch-action: pan-y;
            }}

            .keyboard-grid.active {{
                display: grid;
            }}

            .keyboard-grid-full {{
                grid-template-columns: repeat(10, minmax(0, 1fr));
            }}

            .keyboard-grid-email {{
                grid-template-columns: repeat(10, minmax(0, 1fr));
            }}

            .keyboard-grid-numeric {{
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }}

            .keyboard-grid-numeric .key:last-child {{
                grid-column: span 3;
            }}

            .keyboard-grid-numeric .key {{
                min-height: 84px;
                font-size: 1.9rem;
                border-radius: 18px;
                box-shadow: inset 0 -2px 0 rgba(11, 79, 174, 0.08);
            }}

            .keyboard-grid-numeric .key.action {{
                min-height: 64px;
                font-size: 1.08rem;
                font-weight: 800;
            }}

            .key {{
                border: 0;
                border-radius: 16px;
                min-height: 66px;
                font-size: 1.42rem;
                font-weight: 800;
                background: #ffffff;
                color: #0b4fae;
                cursor: pointer;
            }}

            .key.backspace-key {{
                font-size: 1rem;
                line-height: 1.15;
            }}

            .key.wide {{
                grid-column: span 2;
            }}

            .key.action {{
                background: #eef6ff;
            }}

            button, a, input {{
                touch-action: manipulation;
            }}

            @media (max-height: 860px) {{
                .page {{
                    padding-top: 16px;
                    padding-bottom: 18px;
                }}

                .topbar {{
                    margin-bottom: 12px;
                }}

                .kiosk-center {{
                    padding: 18px 0 22px;
                }}

                .hero {{
                    margin-bottom: 14px;
                }}

                .hero h1 {{
                    font-size: clamp(1.7rem, 3.6vw, 2.8rem);
                }}

                .hero p {{
                    font-size: 0.96rem;
                }}

                .panel {{
                    padding: 18px;
                    border-radius: 22px;
                }}

                .panel h2 {{
                    font-size: 1.35rem;
                    margin-bottom: 10px;
                }}

                .button-grid {{
                    gap: 12px;
                    margin: 12px auto 10px;
                }}

                .main-button {{
                    min-height: 138px;
                    padding: 16px 14px;
                }}

                .button-icon {{
                    width: 54px;
                    height: 54px;
                    margin-bottom: 10px;
                    border-radius: 16px;
                }}

                .button-icon svg {{
                    width: 28px;
                    height: 28px;
                }}

                .main-button strong {{
                    font-size: 1.35rem;
                }}

                .main-button span {{
                    display: none;
                }}

                .assist-card {{
                    min-height: 70px;
                    padding: 14px 16px;
                }}

                .assist-card span,
                .portal-note,
                .portal-qr-card span {{
                    display: none;
                }}

                .portal-panel {{
                    margin-top: 10px;
                    grid-template-columns: minmax(0, 1fr) 170px;
                    gap: 10px;
                    padding: 14px 16px;
                }}

                .compact-home .portal-panel {{
                    grid-template-columns: minmax(0, 1fr) 160px;
                }}

                .portal-copy h2 {{
                    font-size: 1.08rem;
                    margin-bottom: 4px;
                }}

                .portal-copy p {{
                    font-size: 0.9rem;
                }}

                .portal-qr-image {{
                    width: min(120px, 100%);
                }}

                .compact-home .portal-qr-card {{
                    width: 160px;
                }}

                .layout {{
                    gap: 14px;
                    margin-top: 14px;
                }}

                .locker-grid {{
                    gap: 10px;
                }}

                .locker-card {{
                    min-height: 116px;
                    padding: 12px;
                    border-radius: 18px;
                }}

                .locker-name {{
                    font-size: 1.05rem;
                }}

                .locker-badge {{
                    font-size: 0.82rem;
                    padding: 6px 10px;
                }}

                .locker-meta {{
                    margin-top: 8px;
                    font-size: 0.88rem;
                    line-height: 1.35;
                }}

                label {{
                    margin-bottom: 8px;
                    font-size: 0.96rem;
                }}

                input {{
                    padding: 18px 16px;
                    font-size: 1.25rem;
                }}

                .submit-button {{
                    padding: 18px;
                    font-size: 1.2rem;
                }}

                .stack {{
                    gap: 14px;
                }}

                .kiosk-user-summary {{
                    grid-template-columns: 1fr;
                }}

                table {{
                    min-width: 460px;
                }}

                th, td {{
                    padding: 10px 8px;
                    font-size: 0.84rem;
                }}

                .keyboard-shell {{
                    max-height: min(470px, calc(100vh - 20px));
                    padding: 10px 12px calc(12px + env(safe-area-inset-bottom, 0px));
                }}

                .keyboard-topbar {{
                    padding-bottom: 6px;
                }}

                .keyboard-grid {{
                    gap: 8px;
                }}

                .key {{
                    min-height: 64px;
                    font-size: 1.35rem;
                    border-radius: 12px;
                }}

                .keyboard-grid-numeric .key {{
                    min-height: 82px;
                    font-size: 1.8rem;
                    border-radius: 16px;
                }}

                .keyboard-grid-numeric .key.action {{
                    min-height: 56px;
                    font-size: 1rem;
                }}

                .keyboard-close {{
                    padding: 8px 12px;
                }}
            }}

            @media (max-width: 1280px) and (max-height: 820px) {{
                .flow-layout {{
                    grid-template-columns: 1fr;
                }}

                .flow-layout > .stack {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
            }}

            @media (max-width: 1024px) {{
                .layout {{
                    grid-template-columns: 1fr;
                }}

                .portal-panel {{
                    grid-template-columns: 1fr;
                }}

                .topbar {{
                    flex-direction: row;
                    align-items: center;
                    flex-wrap: wrap;
                    gap: 12px;
                }}

                .brand-chip {{
                    flex: 1 1 320px;
                }}

                .clock-card {{
                    text-align: right;
                    min-width: 220px;
                    flex: 0 1 280px;
                }}

                .locker-grid {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}

                .keyboard-grid {{
                    gap: 5px;
                }}

                .keyboard-grid-full {{
                    grid-template-columns: repeat(5, minmax(0, 1fr));
                }}

                .keyboard-grid-email {{
                    grid-template-columns: repeat(5, minmax(0, 1fr));
                }}

                .keyboard-grid-numeric {{
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                }}

                .keyboard-grid-numeric .key {{
                    min-height: 60px;
                    font-size: 1.18rem;
                }}

                .page.with-keyboard {{
                    padding-bottom: calc(24px + var(--keyboard-space));
                }}

                .main-button {{
                    min-height: 144px;
                    padding: 18px 14px;
                }}

                .main-button strong {{
                    font-size: 1.35rem;
                }}

                .main-button span {{
                    font-size: 0.9rem;
                    line-height: 1.35;
                }}

                .button-icon {{
                    width: 58px;
                    height: 58px;
                    margin-bottom: 10px;
                }}

                .button-icon svg {{
                    width: 30px;
                    height: 30px;
                }}

                .button-grid {{
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                    max-width: 100%;
                    gap: 14px;
                    margin-left: auto;
                    margin-right: auto;
                }}

                .action-send,
                .action-deliver,
                .action-receive {{
                    grid-column: auto;
                }}

                .assist-grid {{
                    grid-template-columns: repeat(2, minmax(180px, 1fr));
                    max-width: 100%;
                }}

                .kiosk-user-summary {{
                    grid-template-columns: 1fr;
                }}
            }}

            @media (max-width: 760px) {{
                .home-actions {{
                    grid-template-columns: 1fr;
                    max-width: 420px;
                }}

                .assist-grid {{
                    grid-template-columns: 1fr;
                    max-width: 420px;
                }}

                .compact-home .portal-panel {{
                    grid-template-columns: 1fr;
                    max-width: 420px;
                }}
            }}

            @media (max-width: 640px) {{
                .page {{
                    padding: 18px 16px 24px;
                }}

                .form-actions {{
                    grid-template-columns: 1fr;
                }}

                .admin-alert-backdrop {{
                    padding: 16px;
                }}

                .admin-alert {{
                    padding: 24px 18px;
                    border-radius: 24px;
                }}

                .page.with-keyboard {{
                    padding-bottom: calc(24px + var(--keyboard-space));
                }}

                .hero {{
                    flex-direction: column;
                    align-items: flex-start;
                }}

                .hero.centered {{
                    align-items: center;
                }}

                .button-grid {{
                    gap: 12px;
                }}

                .locker-grid {{
                    grid-template-columns: 1fr;
                }}

                .keyboard-grid {{
                    gap: 5px;
                }}

                .keyboard-grid-full {{
                    grid-template-columns: repeat(4, minmax(0, 1fr));
                }}

                .keyboard-grid-email {{
                    grid-template-columns: repeat(4, minmax(0, 1fr));
                }}

                .keyboard-grid-numeric {{
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                }}

                .keyboard-shell {{
                    left: 10px;
                    right: 10px;
                    bottom: 10px;
                    max-height: min(300px, calc(100vh - 20px));
                    border-radius: 20px;
                }}

                .result-modal-backdrop {{
                    padding: 16px;
                }}

                .result-highlight-grid {{
                    grid-template-columns: 1fr;
                }}

                .result-qr-card {{
                    grid-template-columns: 1fr;
                }}

                .result-qr-image {{
                    width: min(220px, 100%);
                    height: auto;
                    justify-self: center;
                }}

                .button-icon {{
                    width: 54px;
                    height: 54px;
                    margin-bottom: 12px;
                }}

                .button-icon svg {{
                    width: 28px;
                    height: 28px;
                }}

                .main-button {{
                    min-height: 145px;
                    padding: 16px;
                    border-radius: 20px;
                }}

                .main-button strong {{
                    font-size: 1.15rem;
                    margin-bottom: 0;
                }}

                .main-button span {{
                    font-size: 0.9rem;
                }}
            }}
        </style>
    </head>
    <body>
        <main class="{page_class}">
            <section class="topbar">
                <div class="brand-chip">Smart Locker Kiosk</div>
                <div class="clock-card">
                    <span class="clock-time" data-live-clock>--:--:--</span>
                    <span class="clock-date" data-live-date>Đang tải...</span>
                </div>
            </section>
            {content}
        </main>
        <nav class="kiosk-scroll-control is-hidden" data-scroll-control aria-label="Điều khiển cuộn trang">
            <button class="kiosk-scroll-button" type="button" data-scroll-up aria-label="Cuộn lên">▲</button>
            <div class="kiosk-scroll-track" data-scroll-track>
                <div class="kiosk-scroll-thumb" data-scroll-thumb role="scrollbar" aria-label="Kéo để cuộn trang"></div>
            </div>
            <button class="kiosk-scroll-button" type="button" data-scroll-down aria-label="Cuộn xuống">▼</button>
        </nav>
        <div data-admin-alert-host>{admin_modal_html}</div>
        {keyboard}
        {page_script}
    </body>
    </html>
    """


def home_page() -> str:
    user_portal_url = get_monitor_user_portal_url()
    user_portal_qr = build_svg_qr_data_uri(user_portal_url) if user_portal_url else None
    user_portal_card = f"""
        <section class="panel portal-panel">
            <div class="portal-copy">
                <h2>Đăng ký email nhận mã</h2>
                <div class="portal-note">
                    <strong>Link đang dùng</strong>
                    <a class="result-qr-link" href="{escape(user_portal_url)}">{escape(user_portal_url)}</a>
                </div>
            </div>
            <div class="portal-qr-shell">
                <div class="portal-qr-card">
                    <img class="portal-qr-image" src="{user_portal_qr}" alt="QR mở cổng người dùng Smart Locker">
                    <span>Quét để mở cổng người dùng và đăng ký email nhận thông báo.</span>
                    <a class="result-qr-link" href="/dang-ky-email">Mở ngay trên kiosk</a>
                </div>
            </div>
        </section>
    """ if user_portal_url and user_portal_qr else (
        """
        <section class="panel portal-panel">
            <div class="portal-copy">
                <h2>Đăng ký email nhận mã</h2>
                <p>Kiosk chưa có link public của monitor để tạo QR cho điện thoại.</p>
                <div class="portal-note">
                    <strong>Cần cấu hình</strong>
                    Đặt <code>SMARTLOCKER_MONITOR_URL</code> bằng Quick Tunnel hoặc URL public của monitor rồi mở lại kiosk.
                </div>
            </div>
            <div class="portal-qr-shell">
                <div class="portal-qr-card">
                    <div class="result-qr-fallback">Chưa tạo được QR cổng người dùng.</div>
                    <a class="result-qr-link" href="/dang-ky-email">Mở ngay trên kiosk</a>
                </div>
            </div>
        </section>
        """
    )

    return page_template(
        "Smart Locker",
        f"""
        <section class="kiosk-center compact-home">
        <section class="hero centered home-hero">
            <div>
                <h1>Smart Locker</h1>
                <p>Chọn thao tác cần dùng.</p>
            </div>
        </section>

        <section class="button-grid home-actions">
            <a class="main-button action-deliver" href="/giao-do">
                <div class="button-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24">
                        <path d="M3.5 7.5h10.5v7.25H3.5Z" />
                        <path d="M14 10h3.1l2.4 2.6v2.15H14Z" />
                        <path d="M7 15.5v-3" />
                        <path d="M10 15.5v-5" />
                        <circle cx="8" cy="18" r="1.75" />
                        <circle cx="17.5" cy="18" r="1.75" />
                    </svg>
                </div>
                <strong>Giao hàng</strong>
                <span>Tạo đơn giao hàng và lưu vào tủ cho khách nhận.</span>
            </a>
            <a class="main-button action-receive" href="/nhan-do">
                <div class="button-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24">
                        <rect x="5" y="10.5" width="14" height="10" rx="2.5" />
                        <path d="M8.5 10.5V8.5a3.5 3.5 0 1 1 7 0v2" />
                        <circle cx="12" cy="15.5" r="1.1" />
                        <path d="M12 16.6v1.9" />
                    </svg>
                </div>
                <strong>Nhận hàng</strong>
                <span>Nhập mã để xác nhận và mở tủ nhận hàng.</span>
            </a>
        </section>

        {user_portal_card}
        <section class="assist-grid">
            <a class="assist-card" href="/ho-tro">
                <span class="assist-card-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24">
                        <circle cx="12" cy="12" r="8"></circle>
                        <path d="M9.75 9.25a2.4 2.4 0 1 1 4.3 1.45c-.62.72-1.55 1.18-1.55 2.3"></path>
                        <path d="M12 16.6h.01"></path>
                    </svg>
                </span>
                <div class="assist-card-copy">
                    <strong>Hỗ trợ</strong>
                    <span>Xem hướng dẫn nhanh.</span>
                </div>
            </a>
            <a class="assist-card" href="/bao-cao-su-co">
                <span class="assist-card-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24">
                        <path d="M12 4.5 19 17H5L12 4.5Z"></path>
                        <path d="M12 9v4.2"></path>
                        <path d="M12 15.8h.01"></path>
                    </svg>
                </span>
                <div class="assist-card-copy">
                    <strong>Báo cáo sự cố</strong>
                    <span>Gửi lỗi cho nhân viên.</span>
                </div>
            </a>
        </section>
        </section>
        """,
        enable_admin_command_polling=True,
    )


def flow_page(
    title: str,
    subtitle: str,
    action: str,
    fields: list[tuple[str, str, str, str, str]],
    submit_label: str,
    result_html: str = "",
    enable_order_camera: bool = False,
) -> str:
    form_fields = "".join(
        f"""
        <div>
            <label for="{field_id}">{escape(label)}</label>
            <input
                id="{field_id}"
                name="{field_name}"
                placeholder="{escape(placeholder)}"
                autocomplete="off"
                inputmode="none"
                readonly
                data-touch-input="true"
                data-keyboard-mode="{keyboard_mode}"
                {'data-scanner-input="true"' if field_name in {'order_code', 'pickup_code'} else ''}
                required
            >
        </div>
        """
        for field_id, field_name, label, placeholder, keyboard_mode in fields
    )
    camera_html = """
        <section class="order-camera-field">
            <span class="order-camera-label">Ảnh đơn hàng</span>
            <input type="hidden" name="order_photo_data" data-order-photo-data>
            <button class="order-camera-open" type="button" data-open-order-camera>
                📷 Chụp đơn hàng
            </button>
            <div class="order-camera-preview is-hidden" data-order-camera-preview>
                <img alt="Ảnh đơn hàng vừa chụp" data-order-camera-preview-image>
                <div>
                    <strong>Đã chụp ảnh đơn hàng</strong>
                    <button type="button" data-remove-order-photo>Xóa ảnh</button>
                </div>
            </div>
        </section>
        <div class="order-camera-backdrop" data-order-camera-modal aria-hidden="true">
            <section class="order-camera-dialog" role="dialog" aria-modal="true" aria-label="Chụp ảnh đơn hàng">
                <div class="order-camera-head">
                    <h3>Chụp ảnh đơn hàng</h3>
                    <button type="button" data-close-order-camera aria-label="Đóng camera">×</button>
                </div>
                <div class="order-camera-view">
                    <video data-order-camera-video autoplay playsinline muted></video>
                    <div class="order-camera-countdown is-hidden" data-order-camera-countdown></div>
                </div>
                <p data-order-camera-status>Nhấn “Bắt đầu chụp” để đếm ngược 5 giây.</p>
                <div class="order-camera-actions">
                    <button class="submit-button secondary" type="button" data-close-order-camera>Hủy</button>
                    <button class="submit-button" type="button" data-capture-order-photo>Bắt đầu chụp (5 giây)</button>
                </div>
            </section>
        </div>
    """ if enable_order_camera else ""

    return page_template(
        title,
        f"""
        <section class="hero">
            <div>
                <h1>{escape(title)}</h1>
                <p>{escape(subtitle)}</p>
            </div>
            <a class="home-link" href="/">Trang chủ</a>
        </section>

        <section class="layout flow-layout">
            <section class="panel">
                <h2>Nhập thông tin</h2>
                <form method="post" action="{escape(action)}" class="form-grid">
                    {form_fields}
                    {camera_html}
                    <button class="submit-button" type="submit">{escape(submit_label)}</button>
                </form>
                {result_html}
            </section>
        </section>
        """,
        show_keyboard=True,
        enable_admin_command_polling=True,
    )


def kiosk_user_history_table(orders: list[LockerOrder]) -> str:
    if not orders:
        return '<div class="empty-box">Chưa có đơn hàng nào gắn với số điện thoại này.</div>'

    rows: list[str] = []
    for item in orders:
        order_info = f"""
        <div class="kiosk-user-stack">
            <div><span class="kiosk-user-stack-label">Tủ</span><strong>Tủ {item.locker_id}</strong></div>
            <div><span class="kiosk-user-stack-label">Mã đơn</span><strong>{escape(item.order_code or "---")}</strong></div>
        </div>
        """
        email_info = f"""
        <div class="kiosk-user-stack">
            <div><span class="kiosk-user-stack-label">Email</span><strong>{escape(item.recipient_email or "---")}</strong></div>
            <div><span class="kiosk-user-stack-label">Gửi mail</span><strong>{escape(item.email_delivery_status or "---")}</strong></div>
        </div>
        """
        status_text = "Đã nhận" if item.status == "collected" else "Đang lưu"
        rows.append(
            f"""
            <tr>
                <td>{order_info}</td>
                <td>{email_info}</td>
                <td><strong>{escape(status_text)}</strong></td>
                <td class="kiosk-user-time">{escape(now_text(item.created_at))}</td>
            </tr>
            """
        )

    return f"""
    <div class="table-wrap">
        <table class="kiosk-user-orders">
            <thead>
                <tr>
                    <th>Đơn hàng</th>
                    <th>Email</th>
                    <th>Trạng thái</th>
                    <th>Thời gian</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
            </tbody>
        </table>
    </div>
    """


def sync_kiosk_user_email(phone: str, email: str, force_resend: bool = False) -> tuple[str, list[LockerOrder]]:
    if not using_database() or SessionLocal is None:
        raise HTTPException(status_code=503, detail="Kiosk chưa kết nối được cơ sở dữ liệu.")

    with SessionLocal() as session:
        existing_email = session.scalar(select(UserAccount).where(UserAccount.email == email, UserAccount.phone != phone))
        if existing_email is not None:
            raise HTTPException(status_code=409, detail="Email này đã được dùng cho số điện thoại khác.")

        account = session.scalar(select(UserAccount).where(UserAccount.phone == phone))
        now = datetime.now()
        previous_email = account.email if account is not None else None
        if account is None:
            account = UserAccount(phone=phone, email=email, created_at=now, updated_at=now)
            session.add(account)
            session.flush()
            action = "đăng ký mới"
        else:
            account.email = email
            account.updated_at = now
            action = "cập nhật"

        email_changed = previous_email is None or previous_email != email
        if account is not None and not email_changed:
            action = "tra cứu"
        current_base_url = current_configured_url("SMARTLOCKER_BASE_URL", BASE_URL)
        orders = session.scalars(select(LockerOrder).where(LockerOrder.phone == phone).order_by(desc(LockerOrder.created_at))).all()
        queued_count = 0
        for item in orders:
            if item.user_id is None:
                item.user_id = account.id

            if item.status != "stored":
                continue

            if not email_changed and not force_resend:
                continue

            queued_count += 1
            item.recipient_email = email
            item.email_delivery_status = "pending"
            item.email_delivery_note = (
                "Người dùng yêu cầu gửi lại mail mở tủ từ kiosk."
                if force_resend and not email_changed
                else "Đã cập nhật email mới từ kiosk, chuẩn bị gửi lại mail cho đơn đang còn trong tủ."
            )
            item.email_link_base_url = current_base_url or None
            item.email_sent_at = None

        session.commit()

    attempted = 0
    delivered = 0
    if email_changed or force_resend:
        attempted, delivered = retry_email_delivery_for_phone(phone, email)

    with SessionLocal() as session:
        refreshed_orders = session.scalars(
            select(LockerOrder).where(LockerOrder.phone == phone).order_by(desc(LockerOrder.created_at))
        ).all()

    if force_resend:
        if queued_count == 0:
            action = f"{action}; không có đơn đang lưu cần gửi lại email"
        elif delivered == attempted and attempted == queued_count:
            action = f"{action}; đã gửi lại email cho {delivered} đơn đang lưu"
        elif delivered > 0:
            action = f"{action}; đã gửi lại email cho {delivered}/{queued_count} đơn đang lưu"
        else:
            action = f"{action}; chưa gửi lại được email, kiểm tra lại SMTP Gmail"
    elif not email_changed:
        action = f"{action}; email trùng với email đã đăng ký gần nhất nên không gửi lại mail"
    elif queued_count == 0:
        action = f"{action}; không có đơn đang lưu cần gửi lại email"
    elif delivered == attempted and attempted == queued_count:
        action = f"{action}; đã gửi lại email cho {delivered} đơn đang lưu"
    elif delivered > 0:
        action = f"{action}; đã gửi lại email cho {delivered}/{queued_count} đơn đang lưu"
    else:
        action = f"{action}; chưa gửi được email, kiểm tra lại SMTP Gmail"

    return action, refreshed_orders


def kiosk_user_portal_page(
    result_html: str = "",
    phone: str = "",
    email: str = "",
    orders: list[LockerOrder] | None = None,
) -> str:
    orders = orders or []
    active_count = sum(1 for item in orders if item.status == "stored")
    history_html = kiosk_user_history_table(orders)
    return page_template(
        "Đăng ký email nhận mã",
        f"""
        <section class="hero">
            <div>
                <h1>Đăng ký email nhận mã</h1>
                <p>Nhập số điện thoại và email ngay trên kiosk để hệ thống lưu thông tin và gửi lại link nhận đồ khi cần.</p>
            </div>
            <a class="home-link" href="/">Trang chủ</a>
        </section>

        <section class="layout flow-layout">
            <section class="panel">
                <h2>Thông tin người dùng</h2>
                <form method="post" action="/dang-ky-email" class="form-grid">
                    <div>
                        <label for="portal_phone">Số điện thoại</label>
                        <input
                            id="portal_phone"
                            name="phone"
                            placeholder="Nhập số điện thoại"
                            autocomplete="off"
                            inputmode="none"
                            readonly
                            data-touch-input="true"
                            data-keyboard-mode="numeric"
                            required
                            value="{escape(phone)}"
                        >
                    </div>
                    <div>
                        <label for="portal_email">Email</label>
                        <input
                            id="portal_email"
                            name="email"
                            placeholder="Nhập email nhận thông báo"
                            autocomplete="off"
                            inputmode="none"
                            readonly
                            data-touch-input="true"
                            data-keyboard-mode="email"
                            required
                            value="{escape(email)}"
                        >
                    </div>
                    <div class="form-actions">
                        <button class="submit-button secondary" type="submit" name="intent" value="lookup">Tra cứu</button>
                        <button class="submit-button" type="submit" name="intent" value="resend">Lưu mail và gửi lại mail mới</button>
                    </div>
                </form>
                {result_html}
            </section>

            <section class="stack">
                <section class="panel">
                    <h2>Tóm tắt</h2>
                    <div class="kiosk-user-summary">
                        <article class="kiosk-summary-card">
                            <span>Tổng đơn theo số điện thoại</span>
                            <strong>{len(orders)}</strong>
                        </article>
                        <article class="kiosk-summary-card">
                            <span>Đơn đang còn trong tủ</span>
                            <strong>{active_count}</strong>
                        </article>
                        <article class="kiosk-summary-card">
                            <span>Email đang dùng</span>
                            <strong>{escape(email or "---")}</strong>
                        </article>
                    </div>
                </section>

                <section class="panel">
                    <h2>Đơn hàng đã lưu</h2>
                    {history_html}
                </section>
            </section>
        </section>
        """,
        show_keyboard=True,
        enable_admin_command_polling=True,
    )


def support_page() -> str:
    return page_template(
        "Hỗ trợ",
        """
        <section class="hero">
            <div>
                <h1>Hỗ trợ</h1>
                <p>Thông tin hướng dẫn nhanh khi sử dụng Smart Locker.</p>
            </div>
            <a class="home-link" href="/">Trang chủ</a>
        </section>

        <section class="panel">
            <h2>Hướng dẫn nhanh</h2>
            <div class="info-list">
                <div class="info-item">
                    <strong>Gửi đồ</strong>
                    Chọn Gửi đồ, nhập số điện thoại và chờ hệ thống cấp mã mở tủ.
                </div>
                <div class="info-item">
                    <strong>Giao đồ</strong>
                    Shipper nhập số điện thoại người nhận và mã đơn trước khi đóng cửa tủ. Nếu người nhận đã đăng ký email, hệ thống sẽ cấp link nhận đồ an toàn.
                </div>
                <div class="info-item">
                    <strong>Nhận đồ</strong>
                    Người nhận nên ưu tiên dùng link nhận đồ gửi qua email. Mã 6 số tại kiosk chỉ là phương án dự phòng.
                </div>
                <div class="info-item">
                    <strong>Liên hệ</strong>
                    Hotline hỗ trợ: 0900 000 000. Nhân viên trực kiosk: Quầy vận hành tầng 1.
                </div>
            </div>
        </section>
        """,
        enable_admin_command_polling=True,
    )


def issue_report_page(result_html: str = "", selected_issue_type: str = "", contact_phone: str = "", issue_code: str = "") -> str:
    issue_options_html = "".join(
        f"""
        <label class="issue-option">
            <input type="radio" name="issue_type" value="{escape(issue_type)}" {'checked' if selected_issue_type == issue_type else ''} required>
            <span>{escape(label)}</span>
        </label>
        """
        for issue_type, label in ISSUE_TYPE_OPTIONS.items()
    )
    return page_template(
        "Báo cáo sự cố",
        f"""
        <section class="hero">
            <div>
                <h1>Báo cáo sự cố</h1>
                <p>Chọn nhanh loại sự cố thường gặp để nhân viên vận hành xử lý dễ hơn.</p>
            </div>
            <a class="home-link" href="/">Trang chủ</a>
        </section>

        <section class="layout flow-layout">
            <section class="panel">
                <h2>Thông tin sự cố</h2>
                <form method="post" action="/bao-cao-su-co" class="form-grid single">
                    <div>
                        <label for="contact_phone">Số điện thoại liên hệ</label>
                        <input
                            id="contact_phone"
                            name="contact_phone"
                            placeholder="Nhập số điện thoại nếu cần phản hồi"
                            autocomplete="off"
                            inputmode="none"
                            readonly
                            data-touch-input="true"
                            data-keyboard-mode="numeric"
                            value="{escape(contact_phone)}"
                        >
                    </div>
                    <div>
                        <label for="issue_code">Mã tủ / mã đơn</label>
                        <input
                            id="issue_code"
                            name="issue_code"
                            placeholder="Nhập mã tủ hoặc mã đơn liên quan"
                            autocomplete="off"
                            inputmode="none"
                            readonly
                            data-touch-input="true"
                            data-keyboard-mode="full"
                            value="{escape(issue_code)}"
                        >
                    </div>
                    <div>
                        <label>Loại sự cố</label>
                        <div class="issue-options-grid">
                            {issue_options_html}
                        </div>
                    </div>
                    <button class="submit-button" type="submit">Gửi báo cáo</button>
                </form>
                {result_html}
            </section>
        </section>
        """,
        show_keyboard=True,
        enable_admin_command_polling=True,
    )


def pickup_link_page(
    raw_token: str,
    locker_id: int,
    masked_email: str,
    expires_at: datetime,
    result_html: str = "",
    timeout_seconds: int = 0,
) -> str:
    action_url = f"/nhan-do/link/{escape(raw_token)}"
    timeout_notice = ""
    timeout_script = ""
    if timeout_seconds > 0:
        action_url += "?source=kiosk"
        timeout_notice = f"""
                <div class="result-panel timeout-panel">
                    <strong>Xác nhận trong thời gian giới hạn</strong>
                    <p>Vì đây là màn hình nhận đồ trên kiosk, phiên xác nhận sẽ tự thoát sau <span data-timeout-seconds>{timeout_seconds}</span> giây nếu không nhập 4 số cuối.</p>
                </div>
        """
        timeout_script = f"""
                <script>
                    (() => {{
                        const timeoutEl = document.querySelector("[data-timeout-seconds]");
                        let remaining = {timeout_seconds};
                        const render = () => {{
                            if (timeoutEl) timeoutEl.textContent = String(remaining);
                        }};
                        render();
                        const intervalId = window.setInterval(() => {{
                            remaining -= 1;
                            if (remaining <= 0) {{
                                window.clearInterval(intervalId);
                                window.location.href = "/";
                                return;
                            }}
                            render();
                        }}, 1000);
                    }})();
                </script>
        """
    return page_template(
        "Xác nhận nhận đồ",
        f"""
        <section class="hero">
            <div>
                <h1>Xác nhận nhận đồ</h1>
                <p>Link này dành cho Tủ {locker_id}. Hệ thống yêu cầu nhập 4 số cuối số điện thoại để tránh lộ link là mở được tủ ngay.</p>
            </div>
            <a class="home-link" href="/">Trang chủ</a>
        </section>

        <section class="layout flow-layout">
            <section class="panel">
                <h2>Mở link nhận đồ</h2>
                <div class="info-list">
                    <div class="info-item"><strong>Email nhận link</strong>{escape(masked_email)}</div>
                    <div class="info-item"><strong>Tủ đang chờ</strong>Tủ {locker_id}</div>
                    <div class="info-item"><strong>Hiệu lực</strong>{escape(now_text(expires_at))}</div>
                </div>
                {timeout_notice}
                <form method="post" action="{action_url}" class="form-grid">
                    <div>
                        <label for="phone_last4">4 số cuối số điện thoại</label>
                        <input
                            id="phone_last4"
                            name="phone_last4"
                            placeholder="Nhập 4 số cuối"
                            autocomplete="off"
                            inputmode="none"
                            readonly
                            data-touch-input="true"
                            data-keyboard-mode="numeric"
                            required
                        >
                    </div>
                    <button class="submit-button" type="submit">Xác nhận và mở tủ</button>
                </form>
                {result_html}
            </section>
        </section>
        {timeout_script}
        """,
        show_keyboard=True,
        enable_admin_command_polling=timeout_seconds > 0,
    )


def pickup_code_page(
    pickup_code: str,
    locker_id: int,
    result_html: str = "",
) -> str:
    return page_template(
        "Nhận đồ bằng mã mở tủ",
        f"""
        <section class="hero">
            <div>
                <h1>Nhận đồ bằng mã mở tủ</h1>
                <p>Mã này dành cho Tủ {locker_id}. Hệ thống yêu cầu nhập 4 số cuối số điện thoại để xác nhận.</p>
            </div>
            <a class="home-link" href="/">Trang chủ</a>
        </section>

        <section class="layout flow-layout">
            <section class="panel">
                <h2>Xác nhận nhận đồ</h2>
                <div class="info-list">
                    <div class="info-item"><strong>Tủ đang chờ</strong>Tủ {locker_id}</div>
                    <div class="info-item"><strong>Mã mở tủ</strong>{escape(pickup_code)}</div>
                </div>
                <form method="post" action="/nhan-do/ma-bao-mat/{escape(pickup_code)}" class="form-grid">
                    <div>
                        <label for="phone_last4_qr">4 số cuối số điện thoại</label>
                        <input
                            id="phone_last4_qr"
                            name="phone_last4"
                            placeholder="Nhập 4 số cuối"
                            autocomplete="off"
                            inputmode="none"
                            readonly
                            data-touch-input="true"
                            data-keyboard-mode="numeric"
                            required
                        >
                    </div>
                    <button class="submit-button" type="submit">Xác nhận và mở tủ</button>
                </form>
                {result_html}
            </section>
        </section>
        """,
        show_keyboard=True,
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(home_page())


@app.get("/api/admin-command", response_class=JSONResponse)
async def admin_command_api() -> JSONResponse:
    return JSONResponse(admin_command_payload())


@app.get("/api/pickup-handoff", response_class=JSONResponse)
async def pickup_handoff_api() -> JSONResponse:
    return JSONResponse(consume_pickup_handoff())


@app.get("/qr/pickup-code/{pickup_code}.svg")
async def pickup_code_qr(pickup_code: str) -> Response:
    if not qrcode_available():
        return Response(content="QR support is not installed on this kiosk.", media_type="text/plain", status_code=503)

    import qrcode
    from qrcode.image.svg import SvgPathImage

    cleaned_code = normalize_required(pickup_code, "Mã mở tủ")
    get_record_by_pickup_code(cleaned_code)
    image = qrcode.make(cleaned_code, image_factory=SvgPathImage)
    buffer = BytesIO()
    image.save(buffer)
    return Response(content=buffer.getvalue(), media_type="image/svg+xml")


@app.get("/gui-do", response_class=HTMLResponse)
async def user_dropoff_form() -> HTMLResponse:
    return HTMLResponse(
        page_template(
            "Chức năng đã ngừng",
            """
            <section class="hero">
                <div>
                    <h1>Đã ngừng gửi đồ phổ thông</h1>
                    <p>Kiosk hiện chỉ còn hỗ trợ giao hàng và nhận hàng bằng mã mở tủ.</p>
                </div>
                <a class="home-link" href="/">Trang chủ</a>
            </section>

            <section class="panel">
                <div class="result-panel error">
                    <strong>Chức năng cũ đã được gỡ bỏ</strong>
                    <ul>
                        <li>Không còn hỗ trợ luồng gửi đồ phổ thông tại kiosk này.</li>
                        <li>Nếu bạn cần lưu hàng vào tủ cho khách, hãy dùng mục <strong>Giao hàng</strong>.</li>
                        <li>Nếu bạn đến nhận hàng, hãy dùng mục <strong>Nhận hàng</strong>.</li>
                    </ul>
                </div>
                <div class="nav-row">
                    <a class="nav-link warning" href="/giao-do">Sang giao hàng</a>
                    <a class="nav-link primary" href="/nhan-do">Sang nhận hàng</a>
                </div>
            </section>
            """,
            enable_admin_command_polling=True,
        )
    )


@app.post("/gui-do", response_class=HTMLResponse)
async def user_dropoff(request: Request, phone: str = Form(...)) -> HTMLResponse:
    return HTMLResponse(
        page_template(
            "Chức năng đã ngừng",
            """
            <section class="hero">
                <div>
                    <h1>Đã ngừng gửi đồ phổ thông</h1>
                    <p>Kiosk hiện chỉ còn hỗ trợ giao hàng và nhận hàng bằng mã mở tủ.</p>
                </div>
                <a class="home-link" href="/">Trang chủ</a>
            </section>

            <section class="panel">
                <div class="result-panel error">
                    <strong>Không thể tiếp tục với chức năng cũ</strong>
                    <p>Luồng gửi đồ phổ thông đã bị vô hiệu hóa. Hãy quay lại và dùng chức năng phù hợp hơn.</p>
                </div>
                <div class="nav-row">
                    <a class="nav-link warning" href="/giao-do">Sang giao hàng</a>
                    <a class="nav-link primary" href="/nhan-do">Sang nhận hàng</a>
                </div>
            </section>
            """,
            enable_admin_command_polling=True,
        )
    )


@app.get("/giao-do", response_class=HTMLResponse)
async def shipper_dropoff_form() -> HTMLResponse:
    return HTMLResponse(
        flow_page(
            title="Giao hàng",
            subtitle="Nhập số điện thoại người nhận và mã đơn hàng. Hệ thống sẽ ưu tiên gửi link nhận hàng qua email nếu khách đã đăng ký trước.",
            action="/giao-do",
            fields=[
                ("phone", "phone", "Số điện thoại người nhận", "Nhập số điện thoại người nhận", "numeric"),
                ("order_code", "order_code", "Mã đơn hàng", "Quét hoặc nhập mã đơn hàng", "full"),
            ],
            submit_label="Lưu hàng vào tủ",
            enable_order_camera=True,
        )
    )


@app.post("/giao-do", response_class=HTMLResponse)
async def shipper_dropoff(
    request: Request,
    phone: str = Form(...),
    order_code: str = Form(...),
    order_photo_data: str = Form(""),
) -> HTMLResponse:
    try:
        cleaned_phone = normalize_phone(phone)
        recipient = get_registered_user(cleaned_phone)
        record = create_record(
            cleaned_phone,
            "shipper_dropoff",
            normalize_required(order_code, "Mã đơn hàng"),
            recipient_email=recipient.email if recipient is not None else None,
            email_delivery_status="pending" if recipient is not None else "unregistered",
            email_delivery_note="Đã tìm thấy email đăng ký." if recipient is not None else "Số điện thoại chưa đăng ký email.",
        )
        try:
            await to_thread(open_locker_for_dropoff, record.locker_id)
        except LockerHardwareError as exc:
            release_record(record)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        photo_path: Path | None = None
        photo_note = "Đơn hàng chưa có ảnh chụp."
        if order_photo_data.strip():
            try:
                photo_path = save_order_photo(
                    order_photo_data,
                    record.order_code or order_code,
                    record.locker_id,
                    record.pickup_code,
                )
                if photo_path is not None:
                    photo_note = f"Đã lưu ảnh đơn hàng: {photo_path.name}"
            except (OrderPhotoError, OSError) as exc:
                photo_note = f"Đơn đã lưu nhưng ảnh chụp chưa lưu được: {exc}"
        token_note = "Người nhận chưa đăng ký email nên hiện chỉ có mã dự phòng tại kiosk."
        if recipient is not None:
            record, token_note = queue_pickup_email_delivery(
                record,
                recipient.email,
                request,
                order_photo_path=photo_path,
            )
        result_html = result_panel(
            "Đã lưu hàng vào tủ",
            [
                f"Vị trí tủ đã mở: Tủ {record.locker_id}",
                f"Mã mở tủ tại kiosk: {record.pickup_code}",
                f"Mã đơn hàng: {record.order_code or '---'}",
                photo_note,
                token_note,
            ],
            highlights=[
                ("Vị trí tủ", f"Tủ {record.locker_id}"),
                ("Mã mở tủ", record.pickup_code),
            ],
            redirect_url="/",
        )
    except (HTTPException, LockerHardwareError) as exc:
        result_html = result_panel("Không thể lưu hàng", [user_error_message(exc)], tone="error", redirect_url="/")
    except OSError as exc:
        if 'record' in locals() and recipient is not None:
            record = update_record_email_delivery(record, recipient.email, "failed", str(exc))
        result_html = result_panel(
            "Đã lưu hàng nhưng chưa gửi được email",
            [
                f"Vị trí tủ đã mở: Tủ {record.locker_id}" if 'record' in locals() else "Tủ đã được mở để lưu hàng.",
                f"Mã mở tủ tại kiosk: {record.pickup_code}" if 'record' in locals() else "Vui lòng kiểm tra lại mã mở tủ tại kiosk.",
                f"Mã đơn hàng: {record.order_code or '---'}" if 'record' in locals() else "Đơn hàng đã được lưu.",
                str(exc),
            ],
            highlights=[
                ("Vị trí tủ", f"Tủ {record.locker_id}"),
                ("Mã mở tủ", record.pickup_code),
            ] if 'record' in locals() else None,
            tone="error",
            redirect_url="/",
        )

    return HTMLResponse(
        flow_page(
            title="Giao hàng",
            subtitle="Nhập số điện thoại người nhận và mã đơn hàng. Nếu khách đã đăng ký email, hệ thống sẽ phát link nhận hàng an toàn.",
            action="/giao-do",
            fields=[
                ("phone", "phone", "Số điện thoại người nhận", "Nhập số điện thoại người nhận", "numeric"),
                ("order_code", "order_code", "Mã đơn hàng", "Quét hoặc nhập mã đơn hàng", "full"),
            ],
            submit_label="Lưu hàng vào tủ",
            result_html=result_html,
            enable_order_camera=True,
        )
    )


@app.get("/nhan-do", response_class=HTMLResponse)
async def receiver_form() -> HTMLResponse:
    return HTMLResponse(
        flow_page(
            title="Nhận hàng",
            subtitle="Người nhận nhập số điện thoại và nhập hoặc quét QR mã mở tủ 6 số để lấy hàng.",
            action="/nhan-do",
            fields=[
                ("phone", "phone", "Số điện thoại", "Nhập số điện thoại người nhận", "numeric"),
                ("pickup_code", "pickup_code", "Mã mở tủ", "Quét QR hoặc nhập mã mở tủ 6 số", "numeric"),
            ],
            submit_label="Mở tủ để nhận hàng",
        )
    )


@app.post("/nhan-do", response_class=HTMLResponse)
async def receiver_pickup(phone: str = Form(...), pickup_code: str = Form(...)) -> HTMLResponse:
    try:
        cleaned_phone = normalize_phone(phone)
        enforce_rate_limit(f"pickup-code:{cleaned_phone}")
        cleaned_code = normalize_required(pickup_code, "Mã mở tủ")
        preview_record = get_record_by_phone_pickup(cleaned_phone, cleaned_code)
        open_locker(preview_record.locker_id)
        record = collect_record(cleaned_phone, cleaned_code)
        mark_locker_empty(record.locker_id)
        result_html = result_panel(
            "Mở tủ thành công",
            [
                f"Tủ vừa mở: Tủ {record.locker_id}",
                "Người nhận có thể lấy đồ và đóng cửa tủ lại.",
                "Hệ thống đã giải phóng tủ để dùng cho đơn tiếp theo.",
            ],
            highlights=[
                ("Tủ đã mở", f"Tủ {record.locker_id}"),
                ("Mã xác nhận", record.pickup_code),
            ],
            redirect_url="/",
        )
    except (HTTPException, LockerHardwareError) as exc:
        result_html = result_panel("Không thể mở tủ", [user_error_message(exc)], tone="error")

    return HTMLResponse(
        flow_page(
            title="Nhận hàng",
            subtitle="Người nhận nhập số điện thoại và nhập hoặc quét QR mã mở tủ 6 số để lấy hàng.",
            action="/nhan-do",
            fields=[
                ("phone", "phone", "Số điện thoại", "Nhập số điện thoại người nhận", "numeric"),
                ("pickup_code", "pickup_code", "Mã mở tủ", "Quét QR hoặc nhập mã mở tủ 6 số", "numeric"),
            ],
            submit_label="Mở tủ để nhận hàng",
            result_html=result_html,
        )
    )


@app.get("/nhan-do/link/{raw_token}", response_class=HTMLResponse)
async def pickup_link_form(raw_token: str, request: Request) -> HTMLResponse:
    try:
        record, token = resolve_pickup_access(raw_token)
        masked = mask_email(token.email if isinstance(token, AccessTokenRecord) else token.email)
        expires_at = token.expires_at
        timeout_seconds = 100 if request.query_params.get("source") == "kiosk" else 0
        page = pickup_link_page(raw_token, record.locker_id, masked, expires_at, timeout_seconds=timeout_seconds)
    except HTTPException as exc:
        page = page_template(
            "Link nhận đồ",
            f"""
            <section class="hero">
                <div>
                    <h1>Link nhận đồ</h1>
                    <p>Link này đã hết hạn hoặc không còn hiệu lực.</p>
                </div>
                <a class="home-link" href="/">Trang chủ</a>
            </section>
            <section class="panel">
                {result_panel("Không thể mở link", [exc.detail], tone="error")}
            </section>
            """,
        )
    return HTMLResponse(page)


@app.get("/nhan-do/ma-bao-mat/{pickup_code}", response_class=HTMLResponse)
async def pickup_code_form(pickup_code: str) -> HTMLResponse:
    try:
        record = get_record_by_pickup_code(normalize_required(pickup_code, "Mã mở tủ"))
        page = pickup_code_page(record.pickup_code, record.locker_id)
    except HTTPException as exc:
        page = page_template(
            "Mã mở tủ nhận đồ",
            f"""
            <section class="hero">
                <div>
                    <h1>Mã mở tủ nhận đồ</h1>
                    <p>Mã này đã hết hạn hoặc không còn hiệu lực.</p>
                </div>
                <a class="home-link" href="/">Trang chủ</a>
            </section>
            <section class="panel">
                {result_panel("Không thể mở mã", [exc.detail], tone="error")}
            </section>
            """,
        )
    return HTMLResponse(page)


@app.get("/nhan-do/kiosk/{raw_token}", response_class=HTMLResponse)
async def pickup_kiosk_handoff(raw_token: str) -> HTMLResponse:
    try:
        record, token = resolve_pickup_access(raw_token)
        handoff_id = request_pickup_handoff(raw_token)
        page = page_template(
            "Đang chuyển sang kiosk",
            f"""
            <section class="hero">
                <div>
                    <h1>Đã gửi yêu cầu đến kiosk</h1>
                    <p>Nếu kiosk đang mở trên cùng hệ thống, màn hình sẽ tự chuyển sang bước nhận đồ.</p>
                </div>
                <a class="home-link" href="/nhan-do/link/{escape(raw_token)}">Mở bản web</a>
            </section>
            <section class="panel">
                <h2>Thông tin yêu cầu</h2>
                <div class="info-list">
                    <div class="info-item"><strong>Tủ chờ nhận</strong>Tủ {record.locker_id}</div>
                    <div class="info-item"><strong>Hiệu lực</strong>{escape(now_text(token.expires_at))}</div>
                    <div class="info-item"><strong>Mã handoff</strong>{handoff_id}</div>
                </div>
                <div class="result-panel">
                    <strong>Ưu tiên kiosk</strong>
                    <ul>
                        <li>Kiosk sẽ tự điều hướng sang màn hình xác nhận nhận đồ.</li>
                        <li>Nếu kiosk không phản hồi, bạn vẫn có thể bấm "Mở bản web" để thao tác trên trình duyệt.</li>
                    </ul>
                </div>
            </section>
            """,
            enable_pickup_handoff_polling=False,
            enable_admin_command_polling=False,
        )
    except HTTPException as exc:
        page = page_template(
            "Không thể chuyển sang kiosk",
            f"""
            <section class="hero">
                <div>
                    <h1>Không thể chuyển sang kiosk</h1>
                    <p>Link này đã hết hạn hoặc không còn hiệu lực.</p>
                </div>
                <a class="home-link" href="/">Trang chủ</a>
            </section>
            <section class="panel">
                {result_panel("Không thể mở link", [exc.detail], tone="error")}
            </section>
            """,
            enable_admin_command_polling=False,
        )
    return HTMLResponse(page)


@app.post("/nhan-do/ma-bao-mat/{pickup_code}", response_class=HTMLResponse)
async def pickup_code_open(pickup_code: str, phone_last4: str = Form(...)) -> HTMLResponse:
    cleaned_code = normalize_required(pickup_code, "Mã mở tủ")
    try:
        enforce_rate_limit(f"pickup-code-link:{cleaned_code}")
        preview_record = get_record_by_pickup_code(cleaned_code)
        open_locker(preview_record.locker_id)
        record = collect_record_by_last4(cleaned_code, normalize_phone_last4(phone_last4))
        mark_locker_empty(record.locker_id)
        result_html = result_panel(
            "Mở tủ thành công",
            [
                f"Tủ vừa mở: Tủ {record.locker_id}",
                "Người nhận có thể lấy đồ và đóng cửa tủ lại.",
                "Mã mở tủ này đã được khóa lại để tránh bị dùng lặp.",
            ],
            highlights=[
                ("Tủ đã mở", f"Tủ {record.locker_id}"),
                ("Xác thực", "Đã kiểm tra 4 số cuối"),
            ],
            redirect_url="/",
        )
        return HTMLResponse(pickup_code_page(cleaned_code, preview_record.locker_id, result_html))
    except (HTTPException, LockerHardwareError) as exc:
        try:
            preview_record = get_record_by_pickup_code(cleaned_code)
            page = pickup_code_page(
                cleaned_code,
                preview_record.locker_id,
                result_panel("Không thể mở tủ", [user_error_message(exc)], tone="error"),
            )
        except HTTPException:
            page = page_template(
                "Mã mở tủ nhận đồ",
                f"""
                <section class="hero">
                    <div>
                        <h1>Mã mở tủ nhận đồ</h1>
                        <p>Mã này đã hết hạn hoặc không còn hiệu lực.</p>
                    </div>
                    <a class="home-link" href="/">Trang chủ</a>
                </section>
                <section class="panel">
                    {result_panel("Không thể mở mã", [user_error_message(exc)], tone="error")}
                </section>
                """,
            )
        return HTMLResponse(page)


@app.post("/nhan-do/link/{raw_token}", response_class=HTMLResponse)
async def pickup_link_open(request: Request, raw_token: str, phone_last4: str = Form(...)) -> HTMLResponse:
    timeout_seconds = 100 if request.query_params.get("source") == "kiosk" else 0
    try:
        enforce_rate_limit(f"pickup-link:{hash_token(raw_token)}")
        preview_record, preview_token = resolve_pickup_access(raw_token)
        open_locker(preview_record.locker_id)
        record = mark_pickup_access_used(raw_token, normalize_phone_last4(phone_last4))
        mark_locker_empty(record.locker_id)
        result_html = result_panel(
            "Mở tủ thành công",
            [
                f"Tủ vừa mở: Tủ {record.locker_id}",
                "Người nhận có thể lấy đồ và đóng cửa tủ lại.",
                "Link này đã được khóa lại để tránh bị dùng lặp.",
            ],
            highlights=[
                ("Tủ đã mở", f"Tủ {record.locker_id}"),
                ("Xác thực", "Đã kiểm tra 4 số cuối"),
            ],
            redirect_url="/",
        )
        return HTMLResponse(
            pickup_link_page(
                raw_token,
                record.locker_id,
                mask_email(preview_token.email if isinstance(preview_token, AccessTokenRecord) else preview_token.email),
                preview_token.expires_at,
                result_html,
                timeout_seconds=timeout_seconds,
            )
        )
    except (HTTPException, LockerHardwareError) as exc:
        try:
            preview_record, preview_token = resolve_pickup_access(raw_token)
            page = pickup_link_page(
                raw_token,
                preview_record.locker_id,
                mask_email(preview_token.email if isinstance(preview_token, AccessTokenRecord) else preview_token.email),
                preview_token.expires_at,
                result_panel("Không thể mở tủ", [user_error_message(exc)], tone="error"),
                timeout_seconds=timeout_seconds,
            )
        except HTTPException:
            page = page_template(
                "Link nhận đồ",
                f"""
                <section class="hero">
                    <div>
                        <h1>Link nhận đồ</h1>
                        <p>Link này không còn hiệu lực.</p>
                    </div>
                    <a class="home-link" href="/">Trang chủ</a>
                </section>
                <section class="panel">
                    {result_panel("Không thể mở link", [user_error_message(exc)], tone="error")}
                </section>
                """,
            )
        return HTMLResponse(page)


@app.get("/ho-tro", response_class=HTMLResponse)
async def support() -> HTMLResponse:
    return HTMLResponse(support_page())


@app.get("/dang-ky-email", response_class=HTMLResponse)
async def kiosk_user_portal_form() -> HTMLResponse:
    return HTMLResponse(kiosk_user_portal_page())


@app.post("/dang-ky-email", response_class=HTMLResponse)
async def kiosk_user_portal_submit(
    phone: str = Form(...),
    email: str = Form(...),
    intent: str = Form("lookup"),
) -> HTMLResponse:
    try:
        cleaned_phone = normalize_phone(phone)
        cleaned_email = normalize_email(email)
        resend_requested = intent == "resend"
        action, orders = sync_kiosk_user_email(cleaned_phone, cleaned_email, force_resend=resend_requested)
        result_html = result_panel(
            "Đã gửi lại mail" if resend_requested else "Lưu và tra cứu thành công",
            [
                f"Hệ thống đã {action}.",
                (
                    "Nếu có đơn đang còn trong tủ, hệ thống đã cố gắng gửi lại link mở tủ vào email này."
                    if resend_requested
                    else "Hệ thống chỉ gửi lại email khi email vừa nhập khác email đã đăng ký gần nhất cho số điện thoại này."
                ),
            ],
            highlights=[
                ("Số điện thoại", cleaned_phone),
                ("Email", cleaned_email),
            ],
            stacked_highlights=True,
        )
        return HTMLResponse(kiosk_user_portal_page(result_html, cleaned_phone, cleaned_email, orders))
    except HTTPException as exc:
        result_html = result_panel("Không thể xử lý yêu cầu", [exc.detail], tone="error")
        return HTMLResponse(kiosk_user_portal_page(result_html, phone, email))


@app.get("/bao-cao-su-co", response_class=HTMLResponse)
async def report_issue_form() -> HTMLResponse:
    return HTMLResponse(issue_report_page())


@app.post("/bao-cao-su-co", response_class=HTMLResponse)
async def report_issue(
    contact_phone: str = Form(""),
    issue_code: str = Form(""),
    issue_type: str = Form(...),
) -> HTMLResponse:
    try:
        cleaned_phone = normalize_issue_contact(contact_phone)
        cleaned_code = normalize_issue_reference(issue_code)
        cleaned_issue_type = issue_type.strip()
        if not cleaned_issue_type:
            raise HTTPException(status_code=400, detail="Loại sự cố không được để trống.")
        if cleaned_issue_type not in ISSUE_TYPE_OPTIONS:
            raise HTTPException(status_code=400, detail="Loại sự cố không hợp lệ.")
        issue_label = ISSUE_TYPE_OPTIONS[cleaned_issue_type]

        if not using_database() or SessionLocal is None:
            raise HTTPException(
                status_code=503,
                detail="Kiosk chưa kết nối được cơ sở dữ liệu nên chưa gửi báo cáo sang monitor. Vui lòng thử lại sau ít phút.",
            )

        try:
            with SessionLocal() as session:
                session.add(
                    AdminCommand(
                        action="issue_report",
                        status="completed",
                        note=f"issue_type={cleaned_issue_type} | contact_phone={cleaned_phone} | issue_code={cleaned_code}",
                        created_at=datetime.now(),
                        completed_at=datetime.now(),
                    )
                )
                session.commit()
        except SQLAlchemyError:
            raise HTTPException(
                status_code=503,
                detail="Không thể chuyển báo cáo sang monitor lúc này. Vui lòng thử lại sau.",
            ) from None

        result_html = result_panel(
            "Đã ghi nhận sự cố",
            [
                "Nhân viên vận hành đã nhận thông tin báo cáo.",
                f"Số liên hệ: {cleaned_phone}",
                f"Mã liên quan: {cleaned_code}",
                f"Loại sự cố: {issue_label}",
            ],
            highlights=[
                ("Trạng thái", "Đã tiếp nhận"),
                ("Ưu tiên", "Kiểm tra tại kiosk"),
            ],
            redirect_url="/",
            redirect_delay_ms=3200,
        )
    except HTTPException as exc:
        result_html = result_panel("Không thể gửi báo cáo", [exc.detail], tone="error")
    except Exception:
        result_html = result_panel(
            "Không thể gửi báo cáo",
            ["Đã xảy ra lỗi ngoài dự kiến khi gửi báo cáo sự cố. Vui lòng thử lại."],
            tone="error",
        )

    return HTMLResponse(issue_report_page(result_html, issue_type.strip(), contact_phone, issue_code))


def main() -> None:
    import uvicorn

    reload_enabled = env_str("SMARTLOCKER_RELOAD", "").lower() in {"1", "true", "yes", "on"}
    uvicorn.run("main:app", host=APP_HOST, port=APP_PORT, reload=reload_enabled)


if __name__ == "__main__":
    main()
