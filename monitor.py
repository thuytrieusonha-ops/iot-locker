from __future__ import annotations

import hmac
import os
import secrets
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from html import escape
from re import fullmatch
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, desc, select, text

from config import build_local_url, env_int, env_str
from database import SessionLocal, init_db, is_database_configured
from main import retry_email_delivery_for_phone
from model import AdminCommand, LockerAccessToken, LockerOrder, UserAccount

LOCKER_COUNT = 8
MONITOR_HOST = env_str("SMARTLOCKER_MONITOR_HOST", "0.0.0.0") or "0.0.0.0"
MONITOR_PORT = env_int("SMARTLOCKER_MONITOR_PORT", 8001)
MONITOR_URL = env_str("SMARTLOCKER_MONITOR_URL")
KIOSK_URL = env_str("SMARTLOCKER_BASE_URL")
MONITOR_ADMIN_TOKEN = env_str("SMARTLOCKER_MONITOR_ADMIN_TOKEN")
ADMIN_USERNAME = env_str("SMARTLOCKER_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = env_str("SMARTLOCKER_ADMIN_PASSWORD", MONITOR_ADMIN_TOKEN)
CURRENT_MONITOR_PORT = MONITOR_PORT
SESSION_COOKIE_NAME = "smartlocker_admin_session"
SESSION_TTL_HOURS = max(1, env_int("SMARTLOCKER_ADMIN_SESSION_HOURS", 12))
ADMIN_CSRF_HEADER = "x-csrf-token"
ADMIN_LOGIN_WINDOW_SECONDS = max(60, env_int("SMARTLOCKER_ADMIN_LOGIN_WINDOW_SECONDS", 600))
ADMIN_LOGIN_MAX_ATTEMPTS = max(3, env_int("SMARTLOCKER_ADMIN_LOGIN_MAX_ATTEMPTS", 5))
admin_sessions: dict[str, dict[str, object]] = {}
admin_login_attempts: dict[str, list[datetime]] = {}
ACTIVE_ADMIN_LOCK_MESSAGE = "Đã có một quản trị viên khác đang đăng nhập. Hãy đăng xuất phiên hiện tại trước."
USER_PORTAL_PATH = "/portal"

FLOW_LABELS = {
    "user_dropoff": "Giao hàng",
    "shipper_dropoff": "Giao hàng",
}

STATUS_LABELS = {
    "stored": "Đang lưu",
    "collected": "Đã nhận",
}

EMAIL_STATUS_LABELS = {
    "pending": "Đang gửi email",
    "sent": "Đã gửi email",
    "failed": "Lỗi gửi email",
    "smtp_missing": "Chưa cấu hình SMTP",
    "unregistered": "Chưa đăng ký email",
}

ADMIN_ACTION_LABELS = {
    "unlock_all_lockers": "Mở tất cả tủ",
    "unlock_single_locker": "Mở tủ đã chọn",
    "purge_collected_history": "Xóa dữ liệu đã nhận",
    "purge_all_history": "Xóa toàn bộ dữ liệu",
    "issue_report": "Báo cáo sự cố",
}

ISSUE_TYPE_LABELS = {
    "locker_not_open": "Cửa tủ không mở",
    "forgot_pickup_code": "Quên mã mở tủ",
    "screen_slow": "Màn hình phản hồi chậm",
    "wrong_locker_state": "Hiển thị sai trạng thái tủ",
    "cannot_receive_email": "Không nhận được email",
    "other_support": "Cần nhân viên hỗ trợ",
}


def order_status_badge(status: str) -> str:
    label = STATUS_LABELS.get(status, status)
    css_class = "status-badge"
    if status == "stored":
        css_class += " status-stored"
    elif status == "collected":
        css_class += " status-collected"
    return f'<span class="{css_class}">{escape(label)}</span>'


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Smart Locker Access", version="2.0.0", lifespan=lifespan)


def build_monitor_url(port: int) -> str:
    return MONITOR_URL or build_local_url(MONITOR_HOST, port)


def user_portal_url() -> str:
    return USER_PORTAL_PATH


def kiosk_home_url(request: Request | None = None) -> str:
    configured_url = KIOSK_URL.rstrip("/")
    if configured_url:
        parsed = urlsplit(configured_url)
        hostname = (parsed.hostname or "").strip().lower()
        if hostname and hostname not in {"127.0.0.1", "localhost", "::1"}:
            return configured_url
        if request is None:
            return configured_url
        request_host = request.url.hostname or ""
        if not request_host:
            return configured_url
        port = parsed.port or env_int("SMARTLOCKER_APP_PORT", 8000)
        netloc = f"{request_host}:{port}" if port else request_host
        return urlunsplit((parsed.scheme or request.url.scheme, netloc, parsed.path or "/", parsed.query, parsed.fragment)).rstrip("/")

    if request is not None:
        host = request.url.hostname or ""
        if host:
            app_port = env_int("SMARTLOCKER_APP_PORT", 8000)
            return f"{request.url.scheme}://{host}:{app_port}"

    return build_local_url(env_str("SMARTLOCKER_APP_HOST", "0.0.0.0") or "0.0.0.0", env_int("SMARTLOCKER_APP_PORT", 8000))


def find_available_port(host: str, preferred_port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, preferred_port))
            return preferred_port
        except OSError:
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def now_text(value: datetime) -> str:
    return value.strftime("%d/%m/%Y %H:%M:%S")


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 9 or len(digits) > 11:
        raise HTTPException(status_code=400, detail="Số điện thoại phải có từ 9 đến 11 chữ số.")
    return digits


def normalize_email(email: str) -> str:
    cleaned = email.strip().lower()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Email không được để trống.")
    if not fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cleaned):
        raise HTTPException(status_code=400, detail="Email không hợp lệ.")
    return cleaned


def admin_enabled() -> bool:
    return bool(ADMIN_USERNAME and ADMIN_PASSWORD)


def should_use_secure_cookie() -> bool:
    if MONITOR_URL:
        return MONITOR_URL.startswith("https://")
    return False


def ensure_database() -> None:
    if not is_database_configured() or SessionLocal is None:
        raise HTTPException(status_code=500, detail="Chưa cấu hình SMARTLOCKER_DATABASE_URL.")


def cleanup_admin_sessions() -> None:
    now = datetime.now()
    expired: list[str] = []
    for token, session_data in admin_sessions.items():
        if not isinstance(session_data, dict):
            expired.append(token)
            continue
        expires_at = session_data.get("expires_at")
        if not isinstance(expires_at, datetime) or expires_at <= now:
            expired.append(token)
    for token in expired:
        admin_sessions.pop(token, None)


def create_admin_session() -> str:
    cleanup_admin_sessions()
    session_token = secrets.token_urlsafe(32)
    admin_sessions.clear()
    admin_sessions[session_token] = {
        "expires_at": datetime.now() + timedelta(hours=SESSION_TTL_HOURS),
        "csrf_token": secrets.token_urlsafe(32),
    }
    return session_token


def get_admin_session_token(request: Request) -> str:
    return request.cookies.get(SESSION_COOKIE_NAME, "").strip()


def get_admin_session(request: Request) -> dict[str, object] | None:
    cleanup_admin_sessions()
    session_token = get_admin_session_token(request)
    session_data = admin_sessions.get(session_token)
    if not isinstance(session_data, dict):
        return None
    expires_at = session_data.get("expires_at")
    if not isinstance(expires_at, datetime) or expires_at <= datetime.now():
        admin_sessions.pop(session_token, None)
        return None
    return session_data


def is_admin_authenticated(request: Request) -> bool:
    return get_admin_session(request) is not None


def get_admin_csrf_token(request: Request) -> str:
    session_data = get_admin_session(request)
    csrf_token = session_data.get("csrf_token") if session_data else ""
    return str(csrf_token) if csrf_token else ""


def require_admin_csrf(request: Request, csrf_token: str) -> None:
    expected = get_admin_csrf_token(request)
    provided = csrf_token.strip()
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="CSRF token không hợp lệ.")


def clear_admin_session(response: RedirectResponse | HTMLResponse, request: Request | None = None) -> None:
    session_token = get_admin_session_token(request) if request is not None else ""
    if session_token:
        admin_sessions.pop(session_token, None)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def has_active_admin_session(exclude_token: str = "") -> bool:
    cleanup_admin_sessions()
    for token, session_data in admin_sessions.items():
        if token == exclude_token:
            continue
        expires_at = session_data.get("expires_at") if isinstance(session_data, dict) else None
        if isinstance(expires_at, datetime) and expires_at > datetime.now():
            return True
    return False


def cleanup_admin_login_attempts() -> None:
    now = datetime.now()
    window_start = now - timedelta(seconds=ADMIN_LOGIN_WINDOW_SECONDS)
    stale_keys: list[str] = []
    for key, attempts in admin_login_attempts.items():
        recent = [item for item in attempts if item > window_start]
        if recent:
            admin_login_attempts[key] = recent
        else:
            stale_keys.append(key)
    for key in stale_keys:
        admin_login_attempts.pop(key, None)


def record_admin_login_attempt(scope: str) -> None:
    cleanup_admin_login_attempts()
    attempts = admin_login_attempts.get(scope, [])
    attempts.append(datetime.now())
    admin_login_attempts[scope] = attempts


def clear_admin_login_attempts(scope: str) -> None:
    admin_login_attempts.pop(scope, None)


def ensure_admin_login_allowed(scope: str) -> None:
    cleanup_admin_login_attempts()
    attempts = admin_login_attempts.get(scope, [])
    if len(attempts) >= ADMIN_LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Bạn đăng nhập sai quá nhiều lần. Vui lòng chờ rồi thử lại.")


def require_admin(request: Request) -> None:
    if not admin_enabled():
        raise HTTPException(status_code=503, detail="Chưa cấu hình tài khoản quản trị.")
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=403, detail="Phiên quản trị không hợp lệ hoặc đã hết hạn.")


def fetch_orders(limit: int = 200) -> list[LockerOrder]:
    if not is_database_configured() or SessionLocal is None:
        return []

    with SessionLocal() as session:
        return session.scalars(select(LockerOrder).order_by(desc(LockerOrder.created_at)).limit(limit)).all()


def parse_unlock_command_note(note: str | None) -> tuple[list[int], str]:
    if not note:
        return [], ""

    locker_ids: list[int] = []
    remainder = note.strip()
    for segment in [part.strip() for part in note.split("|")]:
        if segment.startswith("locker_ids="):
            raw_values = segment.split("=", 1)[1].strip()
            locker_ids = [int(value) for value in raw_values.split(",") if value.strip().isdigit()]
        elif segment.startswith("locker_id="):
            raw_value = segment.split("=", 1)[1].strip()
            if raw_value.isdigit():
                locker_ids = [int(raw_value)]
        else:
            remainder = segment if segment else remainder

    if remainder.startswith("locker_id=") or remainder.startswith("locker_ids="):
        remainder = ""

    return list(dict.fromkeys(locker_ids)), remainder


def build_unlock_command_note(locker_ids: list[int] | None, note: str | None) -> str | None:
    parts: list[str] = []
    normalized_ids = [locker_id for locker_id in (locker_ids or []) if 1 <= locker_id <= LOCKER_COUNT]
    if normalized_ids:
        if len(normalized_ids) == 1:
            parts.append(f"locker_id={normalized_ids[0]}")
        else:
            parts.append("locker_ids=" + ",".join(str(locker_id) for locker_id in normalized_ids))
    if note and note.strip():
        parts.append(note.strip())
    return " | ".join(parts) if parts else None


def get_pending_unlock_command() -> AdminCommand | None:
    if not is_database_configured() or SessionLocal is None:
        return None

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


def complete_pending_unlock_command(note: str | None = None) -> AdminCommand | None:
    if not is_database_configured() or SessionLocal is None:
        return None

    with SessionLocal() as session:
        command = session.scalar(
            select(AdminCommand)
            .where(
                AdminCommand.action.in_(("unlock_all_lockers", "unlock_single_locker")),
                AdminCommand.status == "pending",
            )
            .order_by(desc(AdminCommand.created_at))
            .limit(1)
        )
        if command is None:
            return None

        command.status = "completed"
        command.completed_at = datetime.now()
        if note:
            command.note = note
        session.commit()
        session.refresh(command)
        return command


def create_admin_command(action: str, note: str | None = None, status: str = "pending") -> AdminCommand:
    if not is_database_configured() or SessionLocal is None:
        raise RuntimeError("SMARTLOCKER_DATABASE_URL is not configured.")

    with SessionLocal() as session:
        command = AdminCommand(
            action=action,
            status=status,
            note=note.strip() if note else None,
            created_at=datetime.now(),
            completed_at=datetime.now() if status != "pending" else None,
        )
        session.add(command)
        session.commit()
        session.refresh(command)
        return command


def purge_orders(scope: str) -> int:
    if not is_database_configured() or SessionLocal is None:
        raise RuntimeError("SMARTLOCKER_DATABASE_URL is not configured.")

    with SessionLocal() as session:
        if scope == "collected":
            ids = session.scalars(select(LockerOrder.id).where(LockerOrder.status == "collected")).all()
            deleted_count = len(ids)
            if deleted_count:
                session.execute(delete(LockerAccessToken).where(LockerAccessToken.order_id.in_(ids)))
                session.execute(delete(LockerOrder).where(LockerOrder.status == "collected"))
        elif scope == "all":
            ids = session.scalars(select(LockerOrder.id)).all()
            deleted_count = len(ids)
            if deleted_count:
                session.execute(delete(LockerAccessToken))
                session.execute(delete(LockerOrder))
        else:
            raise ValueError("Unsupported purge scope.")

        session.commit()
        remaining_count = session.scalar(select(text("COUNT(*)")).select_from(LockerOrder.__table__)) or 0
        if remaining_count == 0:
            session.execute(text("ALTER TABLE locker_orders AUTO_INCREMENT = 1"))
            session.execute(text("ALTER TABLE locker_access_tokens AUTO_INCREMENT = 1"))
        session.commit()
        return deleted_count


def fetch_issue_reports(limit: int = 50) -> list[AdminCommand]:
    if not is_database_configured() or SessionLocal is None:
        return []

    with SessionLocal() as session:
        return session.scalars(
            select(AdminCommand)
            .where(AdminCommand.action == "issue_report")
            .order_by(desc(AdminCommand.created_at))
            .limit(limit)
        ).all()


def active_orders(orders: list[LockerOrder]) -> list[LockerOrder]:
    return [order for order in orders if order.status == "stored"]


def monitor_payload(csrf_token: str = "") -> dict[str, object]:
    configured = is_database_configured()
    orders = fetch_orders()
    issue_reports = fetch_issue_reports()
    active = active_orders(orders)
    collected_count = sum(1 for item in orders if item.status == "collected")
    last_updated = now_text(orders[0].created_at) if orders else "---"
    access_url = build_monitor_url(CURRENT_MONITOR_PORT)
    pending_unlock_command = get_pending_unlock_command()

    return {
        "configured": configured,
        "access_url": access_url,
        "last_updated": last_updated,
        "pending_unlock_command": {
            "exists": pending_unlock_command is not None,
            "action": pending_unlock_command.action if pending_unlock_command else "",
            "created_at": now_text(pending_unlock_command.created_at) if pending_unlock_command else "",
        },
        "admin_pending_html": admin_pending_html(pending_unlock_command),
        "csrf_token": csrf_token,
        "summary": {
            "active": len(active),
            "free": LOCKER_COUNT - len(active),
            "total": len(orders),
            "collected": collected_count,
        },
        "locker_grid_html": locker_status_grid(active),
        "history_rows_html": history_rows(orders),
        "issue_report_rows_html": issue_report_rows(issue_reports),
    }


def locker_status_grid(active: list[LockerOrder]) -> str:
    active_by_locker = {item.locker_id: item for item in active}
    cards: list[str] = []

    for locker_id in range(1, LOCKER_COUNT + 1):
        item = active_by_locker.get(locker_id)
        busy = item is not None
        status_class = "busy" if busy else "free"
        status_text = "Đang sử dụng" if busy else "Sẵn sàng"
        phone = item.phone if item else "---"
        order_code = item.order_code if item and item.order_code else "---"
        recipient_email = item.recipient_email if item and item.recipient_email else "---"
        email_status = (
            EMAIL_STATUS_LABELS.get(item.email_delivery_status or "", item.email_delivery_status or "---")
            if item
            else "---"
        )
        cards.append(
            f"""
            <article class="locker-card {status_class}">
                <div class="locker-head">
                    <strong>Tủ {locker_id}</strong>
                    <span>{status_text}</span>
                </div>
                <div class="locker-line"><span class="locker-line-label">SDT:</span><span class="locker-line-value">{escape(phone)}</span></div>
                <div class="locker-line"><span class="locker-line-label">Email:</span><span class="locker-line-value">{escape(recipient_email)}</span></div>
                <div class="locker-line"><span class="locker-line-label">Mã đơn:</span><span class="locker-line-value">{escape(order_code)}</span></div>
                <div class="locker-line"><span class="locker-line-label">Mail:</span><span class="locker-line-value">{escape(email_status)}</span></div>
            </article>
            """
        )

    return "".join(cards)


def history_rows(orders: list[LockerOrder]) -> str:
    if not orders:
        return """
        <tr>
            <td colspan="12" class="empty-cell">Chưa có giao dịch trong database.</td>
        </tr>
        """

    rows: list[str] = []
    for item in orders:
        rows.append(
            f"""
            <tr>
                <td>{item.id}</td>
                <td>Tủ {item.locker_id}</td>
                <td>{escape(item.phone)}</td>
                <td>{escape(item.recipient_email or "---")}</td>
                <td>{escape(EMAIL_STATUS_LABELS.get(item.email_delivery_status or "", item.email_delivery_status or "---"))}</td>
                <td>{escape(item.pickup_code)}</td>
                <td>{escape(item.order_code or "---")}</td>
                <td>{escape(FLOW_LABELS.get(item.flow, item.flow))}</td>
                <td>{order_status_badge(item.status)}</td>
                <td>{escape(now_text(item.email_sent_at) if item.email_sent_at else "---")}</td>
                <td>{now_text(item.created_at)}</td>
                <td>{escape(item.email_delivery_note or "---")}</td>
            </tr>
            """
        )
    return "".join(rows)


def user_history_rows(orders: list[LockerOrder]) -> str:
    if not orders:
        return """
        <tr>
            <td colspan="4" class="empty-cell">Hiện chưa có đơn hàng nào gắn với số điện thoại này.</td>
        </tr>
        """

    rows: list[str] = []
    for item in orders:
        customer_info = f"""
        <div class="stack-cell">
            <div><span class="cell-label">Tủ</span><strong>Tủ {item.locker_id}</strong></div>
            <div><span class="cell-label">Mã mở tủ</span><strong>Ẩn vì lý do bảo mật</strong></div>
        </div>
        """
        order_info = f"""
        <div class="stack-cell">
            <div><span class="cell-label">Loại giao dịch</span><strong>{escape(FLOW_LABELS.get(item.flow, item.flow))}</strong></div>
            <div><span class="cell-label">Mã đơn</span><strong>{escape(item.order_code or "---")}</strong></div>
            <div><span class="cell-label">Trạng thái đơn</span>{order_status_badge(item.status)}</div>
        </div>
        """
        email_info = f"""
        <div class="stack-cell">
            <div><span class="cell-label">Email</span><strong>{escape(item.recipient_email or "---")}</strong></div>
            <div><span class="cell-label">Gửi mail</span><strong>{escape(EMAIL_STATUS_LABELS.get(item.email_delivery_status or "", item.email_delivery_status or "---"))}</strong></div>
            <div><span class="cell-label">Ghi chú</span><strong>{escape(item.email_delivery_note or "---")}</strong></div>
        </div>
        """
        rows.append(
            f"""
            <tr>
                <td>{customer_info}</td>
                <td>{order_info}</td>
                <td>{email_info}</td>
                <td class="time-cell">{now_text(item.created_at)}</td>
            </tr>
            """
        )
    return "".join(rows)


def parse_issue_report_note(note: str | None) -> tuple[str, str, str]:
    if not note:
        return "---", "---", "---"

    issue_type = "---"
    contact_phone = "---"
    issue_code = "---"
    for segment in [part.strip() for part in note.split("|")]:
        if segment.startswith("issue_type="):
            raw = segment.split("=", 1)[1].strip()
            issue_type = ISSUE_TYPE_LABELS.get(raw, raw or "---")
        elif segment.startswith("contact_phone="):
            raw = segment.split("=", 1)[1].strip()
            contact_phone = raw or "---"
        elif segment.startswith("issue_code="):
            raw = segment.split("=", 1)[1].strip()
            issue_code = raw or "---"
    return issue_type, contact_phone, issue_code


def issue_report_rows(reports: list[AdminCommand]) -> str:
    if not reports:
        return """
        <tr>
            <td colspan="5" class="empty-cell">Chưa có báo cáo sự cố nào từ kiosk.</td>
        </tr>
        """

    rows: list[str] = []
    for report in reports:
        issue_type, contact_phone, issue_code = parse_issue_report_note(report.note)
        rows.append(
            f"""
            <tr>
                <td>{report.id}</td>
                <td>{escape(issue_type)}</td>
                <td>{escape(contact_phone)}</td>
                <td>{escape(issue_code)}</td>
                <td>{now_text(report.created_at)}</td>
            </tr>
            """
        )
    return "".join(rows)


def admin_pending_html(command: AdminCommand | None) -> str:
    if command is None:
        return ""

    locker_ids, note = parse_unlock_command_note(command.note)
    command_label = ADMIN_ACTION_LABELS.get(command.action, command.action)
    action_line = (
        f"Tủ mục tiêu: {', '.join(f'Tủ {locker_id}' for locker_id in locker_ids)}."
        if command.action == "unlock_single_locker" and locker_ids
        else "Áp dụng cho toàn bộ tủ."
    )
    note_text = escape(note) if note else "Không có ghi chú."
    return f"""
    <div class="admin-pending">
        <strong>Đã gửi yêu cầu tới kiosk: {escape(command_label)}.</strong>
        <span>Khởi tạo lúc {escape(now_text(command.created_at))}.</span>
        <span>{escape(action_line)}</span>
        <span>{note_text}</span>
    </div>
    """


def page_shell(title: str, subtitle: str, content: str, script: str = "") -> str:
    return f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{escape(title)}</title>
        <style>
            :root {{
                --bg: #f6fbff;
                --panel: rgba(255, 255, 255, 0.96);
                --line: rgba(18, 93, 160, 0.14);
                --text: #12314d;
                --muted: #5c7a96;
                --accent: #1570ef;
                --accent-strong: #0b4fae;
                --accent-soft: #e6f1ff;
                --danger: #dc2626;
                --warning: #d97706;
                --success: #166534;
                --shadow: 0 24px 50px rgba(17, 76, 131, 0.10);
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                font-family: "Aptos", "Segoe UI Variable", "Segoe UI", Tahoma, sans-serif;
                color: var(--text);
                background:
                    radial-gradient(circle at top right, rgba(21, 112, 239, 0.16), transparent 28%),
                    radial-gradient(circle at left top, rgba(111, 180, 255, 0.18), transparent 22%),
                    linear-gradient(180deg, #ffffff 0%, var(--bg) 100%);
                min-height: 100vh;
            }}
            .page {{
                max-width: 1240px;
                margin: 0 auto;
                padding: 24px 18px 36px;
            }}
            .hero {{
                display: flex;
                justify-content: space-between;
                align-items: end;
                gap: 14px;
                margin-bottom: 18px;
            }}
            .hero.centered {{
                flex-direction: column;
                justify-content: center;
                align-items: center;
                text-align: center;
                margin-bottom: 32px;
            }}
            .hero h1 {{
                margin: 0;
                font-size: clamp(1.7rem, 2.8vw, 2.6rem);
                color: #0d3b66;
                line-height: 1.08;
            }}
            .hero p {{
                margin: 6px 0 0;
                color: var(--muted);
                font-size: 0.96rem;
                line-height: 1.5;
            }}
            .nav-row {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-top: 12px;
            }}
            .nav-link, .button {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                border: 0;
                border-radius: 14px;
                padding: 11px 16px;
                font-size: 0.95rem;
                font-weight: 700;
                text-decoration: none;
                cursor: pointer;
            }}
            .nav-link.primary, .button.primary {{
                background: linear-gradient(180deg, #1b7be0 0%, var(--accent-strong) 100%);
                color: #fff;
            }}
            .nav-link.secondary, .button.secondary {{
                background: #ecf4ff;
                color: #0d4f8f;
                border: 1px solid rgba(13, 79, 143, 0.1);
            }}
            .nav-link.warning, .button.warning {{
                background: linear-gradient(180deg, #f59e0b 0%, var(--warning) 100%);
                color: #fff;
            }}
            .grid-roles {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 18px;
            }}
            .home-shell {{
                max-width: 1080px;
                margin: 0 auto;
            }}
            .clock-card {{
                min-width: 240px;
                padding: 16px 20px;
                text-align: center;
            }}
            .clock-time {{
                font-size: clamp(1.9rem, 4.6vw, 3rem);
                font-weight: 800;
                color: var(--accent-strong);
                line-height: 1;
                letter-spacing: 0.04em;
            }}
            .clock-date {{
                margin-top: 8px;
                color: var(--muted);
                font-size: 0.95rem;
                font-weight: 600;
            }}
            .panel, .notice, .summary-card, .role-card {{
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 22px;
                box-shadow: var(--shadow);
            }}
            .panel, .notice, .role-card {{ padding: 18px; margin-bottom: 16px; }}
            .panel, .notice {{
                content-visibility: auto;
                contain-intrinsic-size: 480px;
            }}
            .role-card h2, .section-head h2 {{ margin: 0 0 8px; }}
            .role-card p, .section-head p, .notice p, .muted, .timestamp {{ color: var(--muted); }}
            .role-card {{
                display: flex;
                min-height: 220px;
                align-items: center;
                justify-content: center;
                gap: 18px;
            }}
            .role-card.compact {{
                padding: 22px 20px;
            }}
            .role-card-main {{
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                gap: 14px;
                text-align: center;
            }}
            .role-eyebrow {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 6px 10px;
                border-radius: 999px;
                background: #eef5ff;
                color: var(--accent-strong);
                font-size: 0.78rem;
                font-weight: 800;
                letter-spacing: 0.06em;
                text-transform: uppercase;
            }}
            .role-icon {{
                width: 72px;
                height: 72px;
                border-radius: 22px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                background: linear-gradient(180deg, #eff6ff 0%, #dbeafe 100%);
                color: var(--accent-strong);
                flex-shrink: 0;
            }}
            .role-icon svg {{
                width: 34px;
                height: 34px;
            }}
            .role-card h2 {{
                margin: 0;
                font-size: clamp(1.65rem, 2.5vw, 2.2rem);
                line-height: 1.08;
            }}
            .role-action {{
                width: min(280px, 100%);
                min-height: 60px;
                font-size: 1.05rem;
                border-radius: 18px;
            }}
            .form-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 14px;
                margin-bottom: 14px;
            }}
            .form-grid.single {{ grid-template-columns: 1fr; }}
            .form-actions {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 10px;
                grid-column: 1 / -1;
            }}
            .form-actions .button {{ width: 100%; }}
            label span {{
                display: block;
                margin-bottom: 6px;
                color: var(--muted);
                font-size: 0.9rem;
            }}
            input {{
                width: 100%;
                border-radius: 14px;
                border: 1px solid var(--line);
                padding: 12px 14px;
                font-size: 0.98rem;
                color: var(--text);
                background: #fff;
            }}
            .password-field {{
                position: relative;
            }}
            .password-field input {{
                padding-right: 92px;
            }}
            .password-toggle {{
                position: absolute;
                top: 50%;
                right: 10px;
                transform: translateY(-50%);
                border: 0;
                background: transparent;
                color: var(--accent-strong);
                font-weight: 700;
                cursor: pointer;
                padding: 8px 10px;
                border-radius: 12px;
            }}
            .result {{
                border-radius: 18px;
                padding: 16px 18px;
                border: 1px solid rgba(22, 163, 74, 0.22);
                background: #effcf3;
                color: var(--success);
                margin-bottom: 18px;
            }}
            .result.error {{
                border-color: rgba(220, 38, 38, 0.22);
                background: #fff4f4;
                color: #b42318;
            }}
            .summary-grid {{
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 12px;
                margin-bottom: 16px;
            }}
            .summary-card {{ padding: 16px 18px; min-width: 0; }}
            .summary-card span {{ display: block; color: var(--muted); margin-bottom: 8px; font-size: 0.9rem; }}
            .summary-card strong {{ font-size: clamp(1.45rem, 2.4vw, 2.2rem); color: var(--accent); }}
            .section-head {{
                display: flex;
                justify-content: space-between;
                gap: 12px;
                align-items: end;
                margin-bottom: 14px;
            }}
            .locker-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
                gap: 12px;
            }}
            .locker-card {{
                border-radius: 18px;
                padding: 16px;
                border: 1px solid var(--line);
                background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
                min-width: 0;
            }}
            .locker-card.free {{
                background: linear-gradient(180deg, #f4fff8 0%, #dcfce7 100%);
            }}
            .locker-card.busy {{
                background: linear-gradient(180deg, #fff6f6 0%, #fee2e2 100%);
            }}
            .locker-head {{
                display: flex;
                justify-content: space-between;
                gap: 10px;
                margin-bottom: 10px;
                align-items: flex-start;
                flex-wrap: wrap;
            }}
            .locker-head strong {{ font-size: 1rem; }}
            .locker-head span {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                background: rgba(255, 255, 255, 0.85);
                border-radius: 999px;
                padding: 6px 10px;
                font-size: 0.82rem;
                color: var(--accent);
                white-space: nowrap;
            }}
            .locker-line {{
                color: #31506d;
                margin-top: 8px;
                display: grid;
                grid-template-columns: 70px minmax(0, 1fr);
                gap: 8px;
                align-items: start;
                font-size: 0.92rem;
            }}
            .locker-line-label {{
                font-weight: 600;
                white-space: nowrap;
            }}
            .locker-line-value {{
                min-width: 0;
                line-height: 1.45;
                overflow-wrap: anywhere;
                word-break: break-word;
                text-align: left;
            }}
            .summary-card .summary-email {{
                display: block;
                min-width: 0;
                font-size: 1.05rem;
                line-height: 1.5;
                overflow-wrap: anywhere;
                word-break: break-word;
            }}
            .table-wrap {{
                overflow: auto;
                border: 1px solid var(--line);
                border-radius: 16px;
                background: rgba(255, 255, 255, 0.92);
                contain: layout paint;
                -webkit-overflow-scrolling: touch;
            }}
            table {{ width: 100%; border-collapse: collapse; min-width: 1080px; }}
            th, td {{ padding: 12px 10px; text-align: left; border-bottom: 1px solid var(--line); }}
            th {{ background: var(--accent-soft); color: #0d4f8f; }}
            .empty-cell {{ text-align: center; color: var(--muted); padding: 28px 12px; }}
            .stack-cell {{
                display: grid;
                gap: 8px;
            }}
            .stack-cell > div {{
                padding: 8px 10px;
                border-radius: 12px;
                background: #f8fbff;
                border: 1px solid rgba(18, 93, 160, 0.08);
            }}
            .stack-cell strong {{
                display: block;
                margin-top: 4px;
                line-height: 1.45;
                color: #0d3b66;
                word-break: break-word;
            }}
            .cell-label {{
                display: inline-block;
                font-size: 0.8rem;
                font-weight: 700;
                letter-spacing: 0.02em;
                text-transform: uppercase;
                color: var(--muted);
            }}
            .time-cell {{
                white-space: nowrap;
                color: var(--muted);
                font-weight: 600;
                vertical-align: top;
            }}
            .status-badge {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 6px 10px;
                border-radius: 999px;
                font-size: 0.84rem;
                font-weight: 800;
                line-height: 1;
                border: 1px solid rgba(18, 93, 160, 0.12);
                background: #f5f8fc;
                color: #31506d;
            }}
            .status-stored {{
                background: #e8f2ff;
                border-color: rgba(21, 112, 239, 0.18);
                color: #0b4fae;
            }}
            .status-collected {{
                background: #ecfdf3;
                border-color: rgba(22, 101, 52, 0.18);
                color: #166534;
            }}
            code {{ background: #eef5ff; padding: 2px 6px; border-radius: 8px; color: #0d4f8f; }}
            .admin-panel {{ margin-top: 24px; }}
            .admin-sections {{ display: grid; gap: 18px; }}
            .admin-card {{
                border-radius: 18px;
                padding: 16px;
                background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
                border: 1px solid rgba(18, 93, 160, 0.12);
                content-visibility: auto;
                contain-intrinsic-size: 240px;
            }}
            .admin-card h3 {{ margin: 0 0 6px; color: #0d4f8f; }}
            .admin-card p {{ margin: 0 0 16px; color: var(--muted); line-height: 1.45; }}
            .admin-grid {{
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 12px;
                margin-bottom: 14px;
            }}
            .admin-grid.single {{ grid-template-columns: 1fr; }}
            .admin-field-wide {{ grid-column: 1 / -1; }}
            .locker-select-grid {{
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 10px;
            }}
            .locker-select-button {{
                border: 1px solid var(--line);
                border-radius: 16px;
                padding: 12px 10px;
                background: linear-gradient(180deg, #ffffff 0%, #eef5ff 100%);
                color: #0d4f8f;
                cursor: pointer;
                text-align: center;
            }}
            .locker-select-button.active {{
                background: linear-gradient(180deg, #1678d8 0%, #0b4fae 100%);
                color: #fff;
            }}
            .locker-select-button span,
            .locker-select-button strong {{ display: block; }}
            .locker-select-button span {{ font-size: 0.86rem; margin-bottom: 6px; }}
            .locker-select-button strong {{ font-size: 1.12rem; }}
            .admin-actions {{
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 10px;
            }}
            .admin-feedback {{ margin-top: 14px; color: var(--muted); line-height: 1.45; }}
            .admin-feedback.error {{ color: #b54708; }}
            .admin-feedback.success {{ color: var(--success); }}
            .admin-pending {{
                margin-bottom: 16px;
                padding: 16px 18px;
                border-radius: 18px;
                background: #fff7ed;
                border: 1px solid rgba(217, 119, 6, 0.2);
                color: #9a3412;
            }}
            .admin-pending strong,
            .admin-pending span {{ display: block; }}
            .admin-pending span {{ margin-top: 6px; }}
            @media (max-width: 980px) {{
                .grid-roles, .summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
                .form-grid, .admin-grid, .admin-actions {{ grid-template-columns: 1fr; }}
                .locker-select-grid {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
                .hero, .section-head {{ flex-direction: column; align-items: start; }}
                .role-card {{ flex-direction: column; align-items: start; }}
                .role-action {{ width: 100%; }}
            }}
            @media (max-width: 640px) {{
                .page {{ padding: 20px 14px 28px; }}
                .grid-roles, .summary-grid, .locker-grid, .locker-select-grid {{ grid-template-columns: 1fr; }}
                .form-actions {{ grid-template-columns: 1fr; }}
                .locker-line {{ grid-template-columns: 62px minmax(0, 1fr); }}
                .role-card {{
                    min-height: 220px;
                }}
            }}
        </style>
    </head>
    <body>
        <main class="page">
            <section class="hero">
                <div>
                    <h1>{escape(title)}</h1>
                    <p>{escape(subtitle)}</p>
                </div>
            </section>
            {content}
        </main>
        {script}
    </body>
    </html>
    """


def home_page() -> str:
    content = """
    <section class="home-shell">
        <section class="hero centered">
            <div class="clock-card panel">
                <div class="clock-time" id="home-clock-time">--:--:--</div>
                <div class="clock-date" id="home-clock-date">--/--/----</div>
            </div>
        </section>
        <section class="grid-roles">
            <article class="role-card compact">
                <div class="role-card-main">
                    <span class="role-eyebrow">Portal</span>
                    <span class="role-icon" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="8" r="3.2"></circle>
                            <path d="M6 19.5c1.2-2.8 3.3-4.2 6-4.2s4.8 1.4 6 4.2"></path>
                            <path d="M18.5 8.2h1.8"></path>
                            <path d="M19.4 7.3V9"></path>
                        </svg>
                    </span>
                    <h2>Người dùng</h2>
                    <p>Lưu email và tra cứu đơn hàng theo số điện thoại.</p>
                    <a class="nav-link primary role-action" href="/portal">Đăng ký mail nhận mã</a>
                </div>
            </article>
            <article class="role-card compact">
                <div class="role-card-main">
                    <span class="role-eyebrow">Control</span>
                    <span class="role-icon" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="5" y="4.5" width="14" height="15" rx="3"></rect>
                            <path d="M9 9.5h6"></path>
                            <path d="M9 13h6"></path>
                            <path d="M12 17.5h.01"></path>
                        </svg>
                    </span>
                    <h2>Quản trị</h2>
                    <p>Theo dõi tình trạng tủ và gửi lệnh điều khiển từ xa.</p>
                    <a class="nav-link secondary role-action" href="/admin">Vào trang quản trị</a>
                </div>
            </article>
        </section>
    </section>
    """
    script = """
    <script>
        (() => {
            const timeEl = document.getElementById("home-clock-time");
            const dateEl = document.getElementById("home-clock-date");
            if (!timeEl || !dateEl) return;

            const renderClock = () => {
                const now = new Date();
                timeEl.textContent = now.toLocaleTimeString("vi-VN", { hour12: false });
                dateEl.textContent = now.toLocaleDateString("vi-VN", {
                    weekday: "long",
                    day: "2-digit",
                    month: "2-digit",
                    year: "numeric",
                });
            };

            renderClock();
            window.setInterval(renderClock, 1000);
        })();
    </script>
    """
    return page_shell("Smart Locker", "Chọn đúng vai trò để tiếp tục.", content, script)


def result_box(title: str, message: str, tone: str = "success") -> str:
    return f"""
    <section class="result {escape(tone if tone == 'error' else '')}">
        <strong>{escape(title)}</strong>
        <div>{escape(message)}</div>
    </section>
    """


def sync_user_email(phone: str, email: str, force_resend: bool = False) -> tuple[str, list[LockerOrder]]:
    ensure_database()
    assert SessionLocal is not None

    with SessionLocal() as session:
        existing_email = session.scalar(select(UserAccount).where(UserAccount.email == email, UserAccount.phone != phone))
        if existing_email is not None:
            raise HTTPException(status_code=409, detail="Email này đã được dùng cho số điện thoại khác.")

        account = session.scalar(select(UserAccount).where(UserAccount.phone == phone))
        now = datetime.now()
        previous_email = account.email if account is not None else None
        if account is None:
            session.add(UserAccount(phone=phone, email=email, created_at=now, updated_at=now))
            action = "đăng ký mới"
        else:
            account.email = email
            account.updated_at = now
            action = "cập nhật"

        email_changed = previous_email is None or previous_email != email
        if account is not None and not email_changed:
            action = "tra cứu"
        orders = session.scalars(select(LockerOrder).where(LockerOrder.phone == phone).order_by(desc(LockerOrder.created_at))).all()
        queued_count = 0
        if email_changed or force_resend:
            for item in orders:
                if item.status != "stored":
                    continue

                queued_count += 1
                item.recipient_email = email
                item.email_delivery_status = "pending"
                item.email_delivery_note = (
                    "Người dùng yêu cầu gửi lại mail mở tủ từ cổng người dùng."
                    if force_resend and not email_changed
                    else "Đã cập nhật email mới từ cổng người dùng, chuẩn bị gửi lại mail cho đơn đang còn trong tủ."
                )
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


def user_lookup_page(
    request: Request | None = None,
    result_html: str = "",
    phone: str = "",
    email: str = "",
    orders: list[LockerOrder] | None = None,
) -> str:
    orders = orders or []
    order_count = len(orders)
    active_count = sum(1 for item in orders if item.status == "stored")
    content = f"""
    <section class="notice">
        <h2>Tra cứu và đăng ký nhận thông báo</h2>
        <div class="nav-row">
            <a class="nav-link secondary" href="/">Trang chủ</a>
        </div>
    </section>
    <section class="panel">
        {result_html}
        <form method="post" class="form-grid single">
            <label>
                <span>Số điện thoại</span>
                <input name="phone" placeholder="Nhập số điện thoại" autocomplete="tel" required value="{escape(phone)}">
            </label>
            <label>
                <span>Email</span>
                <input name="email" type="email" placeholder="Nhập email nhận thông báo" autocomplete="email" required value="{escape(email)}">
            </label>
            <div class="form-actions">
                <button class="button secondary" type="submit" name="intent" value="lookup">Tra cứu</button>
                <button class="button primary" type="submit" name="intent" value="resend">Lưu mail và gửi lại mail mới</button>
            </div>
        </form>
    </section>
    <section class="summary-grid">
        <article class="summary-card">
            <span>Tổng đơn theo số điện thoại</span>
            <strong>{order_count}</strong>
        </article>
        <article class="summary-card">
            <span>Đơn đang còn trong tủ</span>
            <strong>{active_count}</strong>
        </article>
        <article class="summary-card">
            <span>Email đang dùng</span>
            <strong class="summary-email">{escape(email or "---")}</strong>
        </article>
        <article class="summary-card">
            <span>Luồng tra cứu</span>
            <strong style="font-size:1.05rem;line-height:1.4">Người dùng</strong>
        </article>
    </section>
    <section class="panel">
        <div class="section-head">
            <div>
                <h2>Đơn hàng theo số điện thoại</h2>
                <p>Tách riêng thông tin khách hàng, tình trạng đơn và trạng thái gửi mail để dễ đọc hơn. Mã mở tủ vẫn được ẩn để giảm lộ lọt thông tin.</p>
            </div>
        </div>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>Thông tin khách hàng</th>
                        <th>Tình trạng đơn hàng</th>
                        <th>Tình trạng gửi mail</th>
                        <th>Thời gian tạo</th>
                    </tr>
                </thead>
                <tbody>
                    {user_history_rows(orders)}
                </tbody>
            </table>
        </div>
    </section>
    """
    return page_shell("Cổng Người Dùng Smart Locker", "Lưu email và tra cứu đơn hàng của chính bạn.", content)


def admin_login_page(result_html: str = "") -> str:
    hint = ""
    if not admin_enabled():
        hint = result_box(
            "Chưa cấu hình quản trị",
            "Cần đặt SMARTLOCKER_ADMIN_USERNAME và SMARTLOCKER_ADMIN_PASSWORD hoặc SMARTLOCKER_MONITOR_ADMIN_TOKEN.",
            tone="error",
        )

    content = f"""
    <section class="notice">
        <h2>Đăng nhập quản trị</h2>
        <div class="nav-row">
            <a class="nav-link secondary" href="/">Trang chủ</a>
        </div>
    </section>
    <section class="panel">
        {hint}
        {result_html}
        <form method="post" action="/admin/login" class="form-grid">
            <label>
                <span>Tên đăng nhập</span>
                <input name="username" placeholder="Nhập tên đăng nhập" autocomplete="username" required>
            </label>
            <label>
                <span>Mật khẩu</span>
                <div class="password-field">
                    <input id="admin-password" name="password" type="password" placeholder="Nhập mật khẩu" autocomplete="current-password" required>
                    <button class="password-toggle" type="button" id="toggle-admin-password">Hiện</button>
                </div>
            </label>
            <div>
                <button class="button primary" type="submit">Đăng nhập</button>
            </div>
        </form>
    </section>
    """
    script = """
    <script>
        (() => {
            const input = document.getElementById("admin-password");
            const toggle = document.getElementById("toggle-admin-password");
            if (!input || !toggle) return;
            toggle.addEventListener("click", () => {
                const showing = input.type === "text";
                input.type = showing ? "password" : "text";
                toggle.textContent = showing ? "Hiện" : "Ẩn";
            });
        })();
    </script>
    """
    return page_shell("Quản Trị Smart Locker", "Đăng nhập để truy cập dashboard giám sát và điều khiển.", content, script)


def admin_panel_html(payload: dict[str, object], csrf_token: str) -> str:
    configured = bool(payload["configured"])
    if not configured:
        return ""

    pending_state = str(payload.get("admin_pending_html", ""))
    locker_selector = "".join(
        f"""
        <button type="button" class="locker-select-button" data-locker-choice="{locker_id}">
            <span>Tủ</span>
            <strong>{locker_id}</strong>
        </button>
        """
        for locker_id in range(1, LOCKER_COUNT + 1)
    )

    return f"""
    <section class="panel admin-panel">
        <div class="section-head">
            <div>
                <h2>Điều khiển từ xa</h2>
                <p>Dashboard này giữ nguyên các chức năng chính của monitor cũ và chỉ khả dụng sau khi đăng nhập.</p>
            </div>
        </div>
        <div id="admin-pending-host">{pending_state}</div>
        <div class="admin-sections">
            <section class="admin-card">
                <h3>Điều khiển tủ</h3>
                <p>Chọn trực tiếp tủ cần mở hoặc phát lệnh mở toàn bộ.</p>
                <div class="admin-grid">
                    <div class="admin-field admin-field-wide">
                        <span>Tủ cần mở</span>
                        <input id="admin-locker-ids" type="hidden" value="">
                        <div class="locker-select-grid" id="locker-select-grid">
                            {locker_selector}
                        </div>
                        <small id="admin-locker-selection-text">Chưa chọn tủ nào.</small>
                    </div>
                    <label class="admin-field admin-field-wide">
                        <span>Ghi chú lệnh mở tủ</span>
                        <input id="admin-note" type="text" placeholder="Ví dụ: kiểm tra phần cứng, bảo trì định kỳ">
                    </label>
                </div>
                <div class="admin-actions">
                    <button type="button" class="button warning" data-admin-action="unlock-one">Mở tủ đang chọn</button>
                    <button type="button" class="button warning" data-admin-action="unlock-all">Mở tất cả tủ</button>
                    <button type="button" class="button secondary" data-admin-action="complete-unlock">Đánh dấu đã xử lý</button>
                </div>
            </section>
            <section class="admin-card">
                <h3>Dữ liệu hệ thống</h3>
                <p>Các thao tác xóa yêu cầu chuỗi xác nhận để tránh thao tác nhầm.</p>
                <div class="admin-grid single">
                    <label class="admin-field">
                        <span>Xác nhận xóa dữ liệu</span>
                        <input id="admin-confirmation" type="text" placeholder="Nhập XOA_DU_LIEU để xác nhận">
                    </label>
                </div>
                <div class="admin-actions">
                    <button type="button" class="button warning" data-admin-action="purge-collected">Xóa dữ liệu đã nhận</button>
                    <button type="button" class="button warning" data-admin-action="purge-all">Xóa toàn bộ dữ liệu</button>
                    <form method="post" action="/admin/logout">
                        <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                        <button type="submit" class="button secondary" style="width:100%">Đăng xuất</button>
                    </form>
                </div>
            </section>
        </div>
        <div class="admin-feedback" id="admin-feedback">Chưa có thao tác quản trị nào được gửi.</div>
    </section>
    """


def admin_dashboard_page(csrf_token: str = "") -> str:
    payload = monitor_payload(csrf_token)
    configured = bool(payload["configured"])
    summary = payload["summary"]
    assert isinstance(summary, dict)
    csrf_token = str(payload.get("csrf_token", ""))
    admin_panel = admin_panel_html(payload, csrf_token)
    access_url = escape(str(payload["access_url"]))
    last_updated = escape(str(payload["last_updated"]))
    locker_grid_html = str(payload["locker_grid_html"])
    history_rows_html = str(payload["history_rows_html"])
    issue_report_rows_html = str(payload["issue_report_rows_html"])

    if not configured:
        content = f"""
        <section class="notice" id="monitor-notice">
            <h2>Chưa cấu hình database</h2>
            <div class="nav-row">
                <a class="nav-link secondary" href="/">Trang chủ</a>
            </div>
            <p>Hãy đặt biến SMARTLOCKER_DATABASE_URL trước khi chạy monitor.</p>
            <p>URL monitor hiện tại: <code>{access_url}</code></p>
        </section>
        """
    else:
        content = f"""
        <section class="notice" id="monitor-notice">
            <h2>Khu vực quản trị đang hoạt động</h2>
            <div class="nav-row">
                <a class="nav-link secondary" href="/">Trang chủ</a>
                <form method="post" action="/admin/logout">
                    <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                    <button type="submit" class="button secondary">Đăng xuất</button>
                </form>
            </div>
        </section>

        <section class="summary-grid">
            <article class="summary-card">
                <span>Tủ đang sử dụng</span>
                <strong id="summary-active">{summary["active"]}/{LOCKER_COUNT}</strong>
            </article>
            <article class="summary-card">
                <span>Tủ còn trống</span>
                <strong id="summary-free">{summary["free"]}</strong>
            </article>
            <article class="summary-card">
                <span>Tổng giao dịch</span>
                <strong id="summary-total">{summary["total"]}</strong>
            </article>
            <article class="summary-card">
                <span>Đã nhận hàng</span>
                <strong id="summary-collected">{summary["collected"]}</strong>
            </article>
        </section>

        <section class="panel">
            <div class="section-head">
                <div>
                    <h2>Tình trạng 8 tủ</h2>
                    <p>Cập nhật theo dữ liệu MySQL hiện tại.</p>
                </div>
                <div class="timestamp" id="last-updated">Lần cập nhật gần nhất: {last_updated}</div>
            </div>
            <div class="locker-grid" id="locker-grid">
                {locker_grid_html}
            </div>
        </section>

        <section class="panel">
            <div class="section-head">
                <div>
                    <h2>Lịch sử giao dịch</h2>
                    <p>Dữ liệu lấy trực tiếp từ bảng <code>locker_orders</code>.</p>
                </div>
            </div>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Tủ</th>
                            <th>SDT</th>
                            <th>Email</th>
                            <th>Gửi mail</th>
                            <th>Mã mở</th>
                            <th>Mã đơn</th>
                            <th>Loại</th>
                            <th>Trạng thái</th>
                            <th>Mail lúc</th>
                            <th>Thời gian</th>
                            <th style="min-width: 340px;">Ghi chú mail</th>
                        </tr>
                    </thead>
                    <tbody id="history-rows">
                        {history_rows_html}
                    </tbody>
                </table>
            </div>
        </section>

        <section class="panel">
            <div class="section-head">
                <div>
                    <h2>Báo cáo sự cố từ kiosk</h2>
                    <p>Các báo cáo sự cố người dùng đã gửi từ kiosk sẽ được gom về đây để theo dõi.</p>
                </div>
            </div>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Loại sự cố</th>
                            <th>SĐT liên hệ</th>
                            <th>Mã liên quan</th>
                            <th>Thời gian</th>
                        </tr>
                    </thead>
                    <tbody id="issue-report-rows">
                        {issue_report_rows_html}
                    </tbody>
                </table>
            </div>
        </section>
        {admin_panel}
        """

    script = f"""
    <script>
        (() => {{
            const csrfToken = {csrf_token!r};
            const notice = document.getElementById("monitor-notice");
            const activeEl = document.getElementById("summary-active");
            const freeEl = document.getElementById("summary-free");
            const totalEl = document.getElementById("summary-total");
            const collectedEl = document.getElementById("summary-collected");
            const updatedEl = document.getElementById("last-updated");
            const lockerGrid = document.getElementById("locker-grid");
            const historyRows = document.getElementById("history-rows");
            const issueReportRows = document.getElementById("issue-report-rows");
            const adminPendingHost = document.getElementById("admin-pending-host");
            const adminFeedback = document.getElementById("admin-feedback");
            const adminLockerIds = document.getElementById("admin-locker-ids");
            const adminLockerSelectionText = document.getElementById("admin-locker-selection-text");
            const lockerSelectGrid = document.getElementById("locker-select-grid");
            const adminNote = document.getElementById("admin-note");
            const adminConfirmation = document.getElementById("admin-confirmation");
            let isRefreshing = false;
            let lastPayloadHash = "";

            const setAdminFeedback = (message, tone = "") => {{
                if (!adminFeedback) return;
                adminFeedback.textContent = message;
                adminFeedback.classList.remove("error", "success");
                if (tone) {{
                    adminFeedback.classList.add(tone);
                }}
            }};

            const getSelectedLockerIds = () => {{
                return String(adminLockerIds?.value || "")
                    .split(",")
                    .map((value) => Number(value.trim()))
                    .filter((value) => Number.isInteger(value) && value >= 1 && value <= {LOCKER_COUNT});
            }};

            const renderLockerSelection = (lockerIds) => {{
                if (!adminLockerIds || !lockerSelectGrid) return;
                adminLockerIds.value = lockerIds.join(",");
                lockerSelectGrid.querySelectorAll("[data-locker-choice]").forEach((button) => {{
                    const lockerId = Number(button.getAttribute("data-locker-choice") || 0);
                    button.classList.toggle("active", lockerIds.includes(lockerId));
                }});
                if (adminLockerSelectionText) {{
                    adminLockerSelectionText.textContent = lockerIds.length
                        ? `Đã chọn: ${{lockerIds.map((lockerId) => `Tủ ${{lockerId}}`).join(", ")}}`
                        : "Chưa chọn tủ nào.";
                }}
            }};

            const toggleLockerSelection = (lockerId) => {{
                const current = getSelectedLockerIds();
                const next = current.includes(lockerId)
                    ? current.filter((value) => value !== lockerId)
                    : [...current, lockerId].sort((a, b) => a - b);
                renderLockerSelection(next);
            }};

            const renderNotice = (payload) => {{
                if (!notice) return;
                if (payload.configured) {{
                    notice.innerHTML = `
                        <h2>Khu vực quản trị đang hoạt động</h2>
                        <div class="nav-row">
                            <a class="nav-link secondary" href="/">Trang chủ</a>
                            <form method="post" action="/admin/logout">
                                <input type="hidden" name="csrf_token" value="${{csrfToken}}">
                                <button type="submit" class="button secondary">Đăng xuất</button>
                            </form>
                        </div>
                    `;
                }} else {{
                    notice.innerHTML = `
                        <h2>Chưa cấu hình database</h2>
                        <div class="nav-row">
                            <a class="nav-link secondary" href="/">Trang chủ</a>
                        </div>
                        <p>Hãy đặt biến SMARTLOCKER_DATABASE_URL trước khi chạy monitor.</p>
                        <p>URL monitor hiện tại: <code>${{payload.access_url}}</code></p>
                    `;
                }}
            }};

            const applyPayload = (payload) => {{
                renderNotice(payload);
                if (activeEl) activeEl.textContent = `${{payload.summary.active}}/{LOCKER_COUNT}`;
                if (freeEl) freeEl.textContent = payload.summary.free;
                if (totalEl) totalEl.textContent = payload.summary.total;
                if (collectedEl) collectedEl.textContent = payload.summary.collected;
                if (updatedEl) updatedEl.textContent = `Lần cập nhật gần nhất: ${{payload.last_updated}}`;
                if (lockerGrid && lockerGrid.innerHTML !== payload.locker_grid_html) {{
                    lockerGrid.innerHTML = payload.locker_grid_html;
                }}
                if (historyRows && historyRows.innerHTML !== payload.history_rows_html) {{
                    historyRows.innerHTML = payload.history_rows_html;
                }}
                if (issueReportRows && issueReportRows.innerHTML !== payload.issue_report_rows_html) {{
                    issueReportRows.innerHTML = payload.issue_report_rows_html;
                }}
                if (adminPendingHost && adminPendingHost.innerHTML !== (payload.admin_pending_html || "")) {{
                    adminPendingHost.innerHTML = payload.admin_pending_html || "";
                }}
            }};

            const refreshMonitor = async () => {{
                if (isRefreshing || document.hidden) {{
                    return;
                }}
                isRefreshing = true;
                try {{
                    const response = await fetch("/api/admin/monitor", {{ cache: "no-store" }});
                    if (response.status === 403) {{
                        window.location.href = "/admin";
                        return;
                    }}
                    if (!response.ok) {{
                        throw new Error(`HTTP ${{response.status}}`);
                    }}
                    const payload = await response.json();
                    const nextHash = JSON.stringify([
                        payload.summary,
                        payload.last_updated,
                        payload.locker_grid_html,
                        payload.history_rows_html,
                        payload.issue_report_rows_html,
                        payload.admin_pending_html || "",
                    ]);
                    if (nextHash !== lastPayloadHash) {{
                        applyPayload(payload);
                        lastPayloadHash = nextHash;
                    }}
                }} catch (error) {{
                    setAdminFeedback("Không thể lấy dữ liệu mới. Hệ thống sẽ tự thử lại.", "error");
                }} finally {{
                    isRefreshing = false;
                }}
            }};

            const postAdminAction = async (path, confirmation = "") => {{
                const response = await fetch(path, {{
                    method: "POST",
                    headers: {{
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrfToken,
                    }},
                    body: JSON.stringify({{
                        locker_ids: adminLockerIds?.value || "",
                        note: adminNote?.value || "",
                        confirmation: confirmation || adminConfirmation?.value || "",
                    }}),
                }});
                const payload = await response.json();
                if (!response.ok || !payload.ok) {{
                    throw new Error(payload.detail || `HTTP ${{response.status}}`);
                }}
                return payload;
            }};

            document.querySelectorAll("[data-admin-action]").forEach((button) => {{
                button.addEventListener("click", async () => {{
                    if (button.disabled) return;
                    button.disabled = true;
                    const action = button.getAttribute("data-admin-action");
                    try {{
                        let payload;
                        if (action === "unlock-all") {{
                            payload = await postAdminAction("/api/admin/unlock-all");
                        }} else if (action === "unlock-one") {{
                            const lockerIds = getSelectedLockerIds();
                            if (!lockerIds.length) {{
                                throw new Error("Hãy chọn ít nhất một tủ cần mở.");
                            }}
                            payload = await postAdminAction("/api/admin/unlock-one");
                        }} else if (action === "complete-unlock") {{
                            payload = await postAdminAction("/api/admin/unlock-all/complete");
                        }} else if (action === "purge-collected") {{
                            payload = await postAdminAction("/api/admin/purge-collected", adminConfirmation?.value || "");
                        }} else if (action === "purge-all") {{
                            payload = await postAdminAction("/api/admin/purge-all", adminConfirmation?.value || "");
                        }} else {{
                            return;
                        }}

                        setAdminFeedback(payload.message || "Đã thực hiện thao tác quản trị.", "success");
                        refreshMonitor();
                    }} catch (error) {{
                        setAdminFeedback(error.message || "Thao tác quản trị thất bại.", "error");
                    }} finally {{
                        button.disabled = false;
                    }}
                }});
            }});

            lockerSelectGrid?.querySelectorAll("[data-locker-choice]").forEach((button) => {{
                button.addEventListener("click", () => {{
                    const lockerId = Number(button.getAttribute("data-locker-choice") || 0);
                    toggleLockerSelection(lockerId);
                }});
            }});

            renderLockerSelection(getSelectedLockerIds());
            refreshMonitor();
            window.setInterval(refreshMonitor, 10000);
        }})();
    </script>
    """
    return page_shell("Smart Locker Quản Trị", "Giám sát từ xa và điều khiển hệ thống sau khi xác thực.", content, script)


async def read_admin_request(request: Request) -> tuple[list[int], str, str]:
    payload = await request.json()
    locker_ids_raw = str(payload.get("locker_ids", payload.get("locker_id", ""))).strip()
    locker_ids = [
        int(value)
        for value in locker_ids_raw.split(",")
        if value.strip().isdigit()
    ]
    note = str(payload.get("note", ""))
    confirmation = str(payload.get("confirmation", ""))
    return list(dict.fromkeys(locker_ids)), note, confirmation


def admin_login_scope(request: Request, username: str) -> str:
    client_host = request.client.host if request.client else "unknown"
    return f"{client_host}:{username.strip().lower()}"


def unauthorized_response(detail: str = "Bạn cần đăng nhập quản trị để thực hiện thao tác này.") -> JSONResponse:
    return JSONResponse({"ok": False, "detail": detail}, status_code=403)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(home_page())


@app.get("/portal", response_class=HTMLResponse)
async def user_lookup_form(request: Request) -> HTMLResponse:
    return HTMLResponse(user_lookup_page(request))


@app.post("/portal", response_class=HTMLResponse)
async def user_lookup(
    request: Request,
    phone: str = Form(...),
    email: str = Form(...),
    intent: str = Form("lookup"),
) -> HTMLResponse:
    try:
        normalized_phone = normalize_phone(phone)
        normalized_email = normalize_email(email)
        resend_requested = intent == "resend"
        action, orders = sync_user_email(normalized_phone, normalized_email, force_resend=resend_requested)
        result_html = result_box(
            "Đã gửi lại mail" if resend_requested else "Lưu và tra cứu thành công",
            (
                f"Hệ thống đã {action} và tra cứu dữ liệu đơn hàng cho số điện thoại này."
                if resend_requested
                else f"Hệ thống đã {action} email và tra cứu dữ liệu đơn hàng cho số điện thoại này."
            ),
        )
        return HTMLResponse(user_lookup_page(request, result_html, normalized_phone, normalized_email, orders))
    except HTTPException as exc:
        result_html = result_box("Không thể xử lý yêu cầu", str(exc.detail), tone="error")
        return HTMLResponse(user_lookup_page(request, result_html, phone, email))


@app.get("/nguoi-dung", response_class=HTMLResponse)
async def user_lookup_legacy_get() -> RedirectResponse:
    return RedirectResponse(url=USER_PORTAL_PATH, status_code=307)


@app.post("/nguoi-dung", response_class=HTMLResponse)
async def user_lookup_legacy_post(request: Request, phone: str = Form(...), email: str = Form(...)) -> HTMLResponse:
    return await user_lookup(request, phone, email)


@app.post("/switch-role/home")
async def switch_role_home(request: Request) -> RedirectResponse:
    response = RedirectResponse(url="/", status_code=303)
    clear_admin_session(response, request)
    return response


@app.post("/switch-role/user")
async def switch_role_user(request: Request) -> RedirectResponse:
    response = RedirectResponse(url="/", status_code=303)
    clear_admin_session(response, request)
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_entry(request: Request) -> HTMLResponse:
    if admin_enabled() and is_admin_authenticated(request):
        return HTMLResponse(admin_dashboard_page(get_admin_csrf_token(request)))
    return HTMLResponse(admin_login_page())


@app.post("/admin/login", response_class=HTMLResponse)
async def admin_login(request: Request, username: str = Form(...), password: str = Form(...)) -> HTMLResponse:
    if not admin_enabled():
        return HTMLResponse(admin_login_page(result_box("Chưa cấu hình", "Chưa cấu hình tài khoản quản trị.", tone="error")))

    scope = admin_login_scope(request, username)
    try:
        ensure_admin_login_allowed(scope)
    except HTTPException as exc:
        return HTMLResponse(admin_login_page(result_box("Tạm khóa đăng nhập", str(exc.detail), tone="error")))

    if has_active_admin_session():
        return HTMLResponse(admin_login_page(result_box("Đang có phiên quản trị", ACTIVE_ADMIN_LOCK_MESSAGE, tone="error")))

    if not hmac.compare_digest(username.strip(), ADMIN_USERNAME) or not hmac.compare_digest(password, ADMIN_PASSWORD):
        record_admin_login_attempt(scope)
        return HTMLResponse(admin_login_page(result_box("Đăng nhập thất bại", "Tên đăng nhập hoặc mật khẩu không đúng.", tone="error")))

    session_token = create_admin_session()
    clear_admin_login_attempts(scope)
    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=should_use_secure_cookie(),
        path="/",
    )
    return response


@app.post("/admin/logout")
async def admin_logout(request: Request, csrf_token: str = Form("")) -> RedirectResponse:
    require_admin(request)
    require_admin_csrf(request, csrf_token)
    response = RedirectResponse(url="/admin", status_code=303)
    clear_admin_session(response, request)
    return response


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request) -> HTMLResponse:
    if not admin_enabled() or not is_admin_authenticated(request):
        return HTMLResponse(admin_login_page(result_box("Cần đăng nhập", "Vui lòng đăng nhập để vào dashboard quản trị.", tone="error")))
    return HTMLResponse(admin_dashboard_page(get_admin_csrf_token(request)))


@app.get("/api/admin/monitor", response_class=JSONResponse)
async def monitor_api(request: Request) -> JSONResponse:
    if not admin_enabled() or not is_admin_authenticated(request):
        return unauthorized_response("Phiên quản trị không hợp lệ hoặc đã hết hạn.")
    return JSONResponse(monitor_payload(get_admin_csrf_token(request)))


@app.post("/api/admin/unlock-all", response_class=JSONResponse)
async def admin_unlock_all(request: Request) -> JSONResponse:
    if not admin_enabled() or not is_admin_authenticated(request):
        return unauthorized_response()
    try:
        require_admin_csrf(request, request.headers.get(ADMIN_CSRF_HEADER, ""))
    except HTTPException as exc:
        return unauthorized_response(str(exc.detail))

    _, note, _ = await read_admin_request(request)
    create_admin_command("unlock_all_lockers", note=note, status="pending")
    return JSONResponse({"ok": True, "message": "Đã gửi yêu cầu mở tất cả tủ tới kiosk."})


@app.post("/api/admin/unlock-all/complete", response_class=JSONResponse)
async def admin_complete_unlock_all(request: Request) -> JSONResponse:
    if not admin_enabled() or not is_admin_authenticated(request):
        return unauthorized_response()
    try:
        require_admin_csrf(request, request.headers.get(ADMIN_CSRF_HEADER, ""))
    except HTTPException as exc:
        return unauthorized_response(str(exc.detail))

    _, note, _ = await read_admin_request(request)
    command = complete_pending_unlock_command(note=note or None)
    if command is None:
        return JSONResponse({"ok": False, "detail": "Không có lệnh mở tủ nào đang chờ."}, status_code=404)
    return JSONResponse({"ok": True, "message": "Đã đánh dấu hoàn tất lệnh mở tủ đang chờ."})


@app.post("/api/admin/unlock-one", response_class=JSONResponse)
async def admin_unlock_one(request: Request) -> JSONResponse:
    if not admin_enabled() or not is_admin_authenticated(request):
        return unauthorized_response()
    try:
        require_admin_csrf(request, request.headers.get(ADMIN_CSRF_HEADER, ""))
    except HTTPException as exc:
        return unauthorized_response(str(exc.detail))

    locker_ids, note, _ = await read_admin_request(request)
    valid_locker_ids = [locker_id for locker_id in locker_ids if 1 <= locker_id <= LOCKER_COUNT]
    if not valid_locker_ids:
        return JSONResponse(
            {"ok": False, "detail": f"Hãy chọn ít nhất một tủ trong khoảng từ 1 đến {LOCKER_COUNT}."},
            status_code=400,
        )

    create_admin_command("unlock_single_locker", note=build_unlock_command_note(valid_locker_ids, note), status="pending")
    lockers_text = ", ".join(f"Tủ {locker_id}" for locker_id in valid_locker_ids)
    return JSONResponse({"ok": True, "message": f"Đã gửi yêu cầu mở {lockers_text} tới kiosk."})


@app.post("/api/admin/purge-collected", response_class=JSONResponse)
async def admin_purge_collected(request: Request) -> JSONResponse:
    if not admin_enabled() or not is_admin_authenticated(request):
        return unauthorized_response()
    try:
        require_admin_csrf(request, request.headers.get(ADMIN_CSRF_HEADER, ""))
    except HTTPException as exc:
        return unauthorized_response(str(exc.detail))

    _, note, confirmation = await read_admin_request(request)
    if confirmation.strip().upper() != "XOA_DU_LIEU":
        return JSONResponse({"ok": False, "detail": "Cần nhập XOA_DU_LIEU để xác nhận xóa dữ liệu."}, status_code=400)

    deleted_count = purge_orders("collected")
    create_admin_command(
        "purge_collected_history",
        note=f"{note.strip()} | deleted={deleted_count}" if note.strip() else f"deleted={deleted_count}",
        status="completed",
    )
    return JSONResponse({"ok": True, "message": f"Đã xóa {deleted_count} giao dịch đã nhận."})


@app.post("/api/admin/purge-all", response_class=JSONResponse)
async def admin_purge_all(request: Request) -> JSONResponse:
    if not admin_enabled() or not is_admin_authenticated(request):
        return unauthorized_response()
    try:
        require_admin_csrf(request, request.headers.get(ADMIN_CSRF_HEADER, ""))
    except HTTPException as exc:
        return unauthorized_response(str(exc.detail))

    _, note, confirmation = await read_admin_request(request)
    if confirmation.strip().upper() != "XOA_DU_LIEU":
        return JSONResponse({"ok": False, "detail": "Cần nhập XOA_DU_LIEU để xác nhận xóa toàn bộ dữ liệu."}, status_code=400)

    deleted_count = purge_orders("all")
    create_admin_command(
        "purge_all_history",
        note=f"{note.strip()} | deleted={deleted_count}" if note.strip() else f"deleted={deleted_count}",
        status="completed",
    )
    return JSONResponse({"ok": True, "message": f"Đã xóa toàn bộ dữ liệu, tổng cộng {deleted_count} bản ghi."})


def main() -> None:
    import uvicorn

    global CURRENT_MONITOR_PORT

    CURRENT_MONITOR_PORT = find_available_port(MONITOR_HOST, MONITOR_PORT)
    os.environ["SMARTLOCKER_MONITOR_PORT"] = str(CURRENT_MONITOR_PORT)
    uvicorn.run("monitor:app", host=MONITOR_HOST, port=CURRENT_MONITOR_PORT, reload=True)


if __name__ == "__main__":
    main()
