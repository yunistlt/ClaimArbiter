"""
Admin web interface for ClaimArbiter review queue.

Runs as a separate process alongside the Telegram bot, sharing the same
/app/data SQLite databases via Docker volume.

Start: python -m uvicorn web.app:app --host 0.0.0.0 --port 8080
       (from /app/src directory)
"""

import os
import sys
import secrets
import sqlite3
import tempfile
import logging
from io import BytesIO
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

# Ensure parent src/ directory is importable so pdf_service can be found
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from services.pdf_service import create_pdf  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_BASE_DATA = os.path.abspath(
    os.getenv("DATA_DIR", os.path.join(_SRC_DIR, "..", "data"))
)
REVIEW_DB = os.getenv(
    "REVIEW_QUEUE_DB_PATH", os.path.join(_BASE_DATA, "review_queue.db")
)
ACCESS_DB = os.getenv(
    "ACCESS_CONTROL_DB_PATH", os.path.join(_BASE_DATA, "access_control.db")
)
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEB_ADMIN_USER: str = os.getenv("WEB_ADMIN_USER", "admin")
WEB_ADMIN_PASSWORD: str = os.getenv("WEB_ADMIN_PASSWORD", "changeme")

if WEB_ADMIN_PASSWORD == "changeme":
    logger.warning(
        "WEB_ADMIN_PASSWORD is set to default 'changeme'. "
        "Set WEB_ADMIN_PASSWORD in .env before exposing this service!"
    )

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

app = FastAPI(title="ClaimArbiter Admin", docs_url=None, redoc_url=None)
security = HTTPBasic()
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def check_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    ok_user = secrets.compare_digest(
        credentials.username.encode("utf-8"), WEB_ADMIN_USER.encode("utf-8")
    )
    ok_pass = secrets.compare_digest(
        credentials.password.encode("utf-8"), WEB_ADMIN_PASSWORD.encode("utf-8")
    )
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def review_conn() -> sqlite3.Connection:
    return sqlite3.connect(REVIEW_DB)


def access_conn() -> sqlite3.Connection:
    return sqlite3.connect(ACCESS_DB)


# ---------------------------------------------------------------------------
# Jinja2 filters / globals
# ---------------------------------------------------------------------------

TASK_TYPE_NAMES = {
    "claim_processing": "Обработка рекламации",
    "claim": "Рекламация",
    "document_drafting": "Составление документа",
    "legal_advice": "Юрид. консультация",
    "consultation": "Консультация",
}

STATUS_RU = {
    "pending": "На проверке",
    "approved": "Согласовано",
    "rejected": "Отклонено",
}

MODE_RU = {
    "manual": "Ручная проверка",
    "auto": "Автовыпуск",
}

ROLE_CHOICES = [
    "ceo",
    "head_of_legal",
    "lawyer",
    "sales",
    "procurement",
    "warehouse",
    "accountant",
    "employee",
]

ROLE_LABELS_RU = {
    "ceo": "Директор",
    "head_of_legal": "Руководитель юридического отдела",
    "lawyer": "Юрист",
    "sales": "Продажи",
    "procurement": "Снабжение",
    "warehouse": "Склад",
    "accountant": "Бухгалтерия",
    "employee": "Сотрудник",
}


def human_task_type(value: str) -> str:
    return TASK_TYPE_NAMES.get(value, value)


def status_ru(value: str) -> str:
    return STATUS_RU.get(value, value)


def mode_ru(value: str) -> str:
    return MODE_RU.get(value, value)


templates.env.filters["human_task_type"] = human_task_type
templates.env.filters["status_ru"] = status_ru
templates.env.filters["mode_ru"] = mode_ru


def _ensure_user_profiles_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            role TEXT NOT NULL DEFAULT 'employee',
            department TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(user_profiles)").fetchall()
    }
    for column_name in ("username", "telegram_full_name", "avatar_file_id"):
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE user_profiles ADD COLUMN {column_name} TEXT")


async def _telegram_api_get(method: str, params: dict) -> Optional[dict]:
    if not BOT_TOKEN:
        return None

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
        if resp.status_code != 200:
            logger.warning("Telegram %s failed: %s %s", method, resp.status_code, resp.text)
            return None
        payload = resp.json()
        if not payload.get("ok"):
            logger.warning("Telegram %s returned error: %s", method, payload)
            return None
        return payload.get("result")
    except Exception:
        logger.exception("Telegram %s request failed", method)
        return None


async def _fetch_telegram_profile(user_id: int) -> dict:
    profile: dict = {}

    chat = await _telegram_api_get("getChat", {"chat_id": str(user_id)})
    if chat:
        first_name = (chat.get("first_name") or "").strip()
        last_name = (chat.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first_name, last_name) if part).strip() or None
        profile["username"] = chat.get("username")
        profile["telegram_full_name"] = full_name

    photos = await _telegram_api_get(
        "getUserProfilePhotos", {"user_id": str(user_id), "limit": 1}
    )
    if photos and photos.get("total_count", 0) > 0:
        first_photo_group = photos.get("photos", [[]])[0]
        if first_photo_group:
            # The last size is usually the largest one.
            profile["avatar_file_id"] = first_photo_group[-1].get("file_id")

    return profile


async def _upsert_telegram_profile(conn: sqlite3.Connection, user_id: int) -> dict:
    telegram_profile = await _fetch_telegram_profile(user_id)
    if not telegram_profile:
        return {}

    conn.execute(
        """
        INSERT INTO user_profiles(user_id, username, telegram_full_name, avatar_file_id)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = COALESCE(excluded.username, user_profiles.username),
            telegram_full_name = COALESCE(excluded.telegram_full_name, user_profiles.telegram_full_name),
            avatar_file_id = COALESCE(excluded.avatar_file_id, user_profiles.avatar_file_id),
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            user_id,
            telegram_profile.get("username"),
            telegram_profile.get("telegram_full_name"),
            telegram_profile.get("avatar_file_id"),
        ),
    )
    return telegram_profile

# ---------------------------------------------------------------------------
# Helper: send document to Telegram after web approval
# ---------------------------------------------------------------------------

async def _dispatch_approved_task(chat_id: int, content: str, task_id: int) -> None:
    """Generate PDF and send it to the target Telegram chat via Bot API."""
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN not set, cannot dispatch approved task to Telegram")
        return

    pdf_path = os.path.join(tempfile.gettempdir(), f"web_approved_{task_id}.pdf")
    try:
        success = create_pdf(content, pdf_path)
        if success and os.path.exists(pdf_path):
            async with httpx.AsyncClient(timeout=30) as client:
                with open(pdf_path, "rb") as f:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                        data={
                            "chat_id": str(chat_id),
                            "caption": "📄 <b>Документ согласован администратором.</b>",
                            "parse_mode": "HTML",
                        },
                        files={"document": ("document.pdf", f, "application/pdf")},
                    )
                if resp.status_code != 200:
                    logger.error(
                        "Telegram sendDocument failed: %s %s", resp.status_code, resp.text
                    )
        else:
            logger.error("PDF generation failed for task %d", task_id)
    except Exception:
        logger.exception("Error dispatching approved task %d to Telegram", task_id)
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


async def _notify_rejection(chat_id: int, task_id: int, reason: str) -> None:
    """Notify the work chat about task rejection."""
    if not BOT_TOKEN:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": (
                        f"❌ Документ <b>#{task_id}</b> отклонён администратором.\n"
                        f"Причина: {reason}"
                    ),
                    "parse_mode": "HTML",
                },
            )
        except Exception:
            logger.exception("Error notifying chat %d about rejection of task %d", chat_id, task_id)


# ---------------------------------------------------------------------------
# Routes — Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _user: str = Depends(check_auth)) -> HTMLResponse:
    stats: dict = {}
    try:
        with review_conn() as conn:
            stats["pending"] = conn.execute(
                "SELECT COUNT(*) FROM review_tasks WHERE status='pending'"
            ).fetchone()[0]
            stats["approved"] = conn.execute(
                "SELECT COUNT(*) FROM review_tasks WHERE status='approved'"
            ).fetchone()[0]
            stats["rejected"] = conn.execute(
                "SELECT COUNT(*) FROM review_tasks WHERE status='rejected'"
            ).fetchone()[0]
            stats["rules"] = conn.execute(
                "SELECT COUNT(*) FROM review_rules"
            ).fetchone()[0]
    except Exception:
        stats.update({"pending": "—", "approved": "—", "rejected": "—", "rules": "—"})

    try:
        with access_conn() as conn:
            stats["users"] = conn.execute(
                "SELECT COUNT(*) FROM allowed_users"
            ).fetchone()[0]
            stats["chats"] = conn.execute(
                "SELECT COUNT(*) FROM allowed_chats"
            ).fetchone()[0]
    except Exception:
        stats.update({"users": "—", "chats": "—"})

    return templates.TemplateResponse(
        "dashboard.html", {"request": request, **stats}
    )


# ---------------------------------------------------------------------------
# Routes — Review queue
# ---------------------------------------------------------------------------

@app.get("/queue", response_class=HTMLResponse)
async def queue_list(
    request: Request,
    status: str = "pending",
    _user: str = Depends(check_auth),
) -> HTMLResponse:
    valid_statuses = {"pending", "approved", "rejected", "all"}
    if status not in valid_statuses:
        status = "pending"

    with review_conn() as conn:
        if status == "all":
            rows = conn.execute(
                "SELECT id, chat_id, requester_name, task_type, status, created_at "
                "FROM review_tasks ORDER BY id DESC LIMIT 200"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, chat_id, requester_name, task_type, status, created_at "
                "FROM review_tasks WHERE status=? ORDER BY id DESC LIMIT 200",
                (status,),
            ).fetchall()

    tasks = [
        {
            "id": r[0],
            "chat_id": r[1],
            "requester_name": r[2],
            "task_type": r[3],
            "status": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]
    return templates.TemplateResponse(
        "queue.html",
        {"request": request, "tasks": tasks, "status_filter": status},
    )


@app.get("/queue/{task_id}", response_class=HTMLResponse)
async def task_detail(
    task_id: int,
    request: Request,
    success: Optional[str] = None,
    _user: str = Depends(check_auth),
) -> HTMLResponse:
    with review_conn() as conn:
        row = conn.execute(
            "SELECT id, chat_id, requester_user_id, requester_name, task_type, "
            "content, status, reviewer_id, reviewer_comment, created_at, updated_at "
            "FROM review_tasks WHERE id=?",
            (task_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    task = {
        "id": row[0],
        "chat_id": row[1],
        "requester_user_id": row[2],
        "requester_name": row[3],
        "task_type": row[4],
        "content": row[5],
        "status": row[6],
        "reviewer_id": row[7],
        "reviewer_comment": row[8],
        "created_at": row[9],
        "updated_at": row[10],
    }
    return templates.TemplateResponse(
        "task.html", {"request": request, "task": task, "success": success}
    )


@app.post("/queue/{task_id}/approve")
async def task_approve(
    task_id: int,
    _user: str = Depends(check_auth),
) -> RedirectResponse:
    with review_conn() as conn:
        row = conn.execute(
            "SELECT chat_id, content, status FROM review_tasks WHERE id=?",
            (task_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        if row[2] != "pending":
            raise HTTPException(status_code=400, detail="Задача уже обработана")

        chat_id: int = row[0]
        content: str = row[1]

        conn.execute(
            "UPDATE review_tasks SET status='approved', reviewer_id=-1, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
            (task_id,),
        )
        conn.commit()

    # Fire-and-forget PDF dispatch to Telegram (don't block the redirect)
    await _dispatch_approved_task(chat_id, content, task_id)

    return RedirectResponse(url=f"/queue/{task_id}?success=approved", status_code=303)


@app.post("/queue/{task_id}/reject")
async def task_reject(
    task_id: int,
    comment: str = Form(...),
    _user: str = Depends(check_auth),
) -> RedirectResponse:
    comment = comment.strip()
    if not comment:
        raise HTTPException(status_code=422, detail="Необходимо указать причину отклонения")

    with review_conn() as conn:
        row = conn.execute(
            "SELECT chat_id, status FROM review_tasks WHERE id=?",
            (task_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        if row[1] != "pending":
            raise HTTPException(status_code=400, detail="Задача уже обработана")

        chat_id: int = row[0]

        conn.execute(
            "UPDATE review_tasks SET status='rejected', reviewer_id=-1, "
            "reviewer_comment=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status='pending'",
            (comment, task_id),
        )
        conn.commit()

    await _notify_rejection(chat_id, task_id, comment)

    return RedirectResponse(url=f"/queue/{task_id}?success=rejected", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Review rules
# ---------------------------------------------------------------------------

@app.get("/rules", response_class=HTMLResponse)
async def rules_list(
    request: Request,
    success: Optional[str] = None,
    _user: str = Depends(check_auth),
) -> HTMLResponse:
    with review_conn() as conn:
        rows = conn.execute(
            "SELECT task_type, mode FROM review_rules ORDER BY task_type"
        ).fetchall()
    rules = [{"task_type": r[0], "mode": r[1]} for r in rows]
    return templates.TemplateResponse(
        "rules.html", {"request": request, "rules": rules, "success": success}
    )


@app.post("/rules/save")
async def rules_save(
    task_type: str = Form(...),
    mode: str = Form(...),
    _user: str = Depends(check_auth),
) -> RedirectResponse:
    task_type = task_type.strip()
    if not task_type:
        raise HTTPException(status_code=422, detail="Тип задачи не может быть пустым")
    if mode not in ("auto", "manual"):
        raise HTTPException(status_code=422, detail="Некорректный режим")

    with review_conn() as conn:
        conn.execute(
            "INSERT INTO review_rules(task_type, mode) VALUES(?,?) "
            "ON CONFLICT(task_type) DO UPDATE SET mode=excluded.mode",
            (task_type, mode),
        )
        conn.commit()
    return RedirectResponse(url="/rules?success=1", status_code=303)


@app.post("/rules/delete")
async def rules_delete(
    task_type: str = Form(...),
    _user: str = Depends(check_auth),
) -> RedirectResponse:
    with review_conn() as conn:
        conn.execute("DELETE FROM review_rules WHERE task_type=?", (task_type,))
        conn.commit()
    return RedirectResponse(url="/rules", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Users / access control
# ---------------------------------------------------------------------------

@app.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    success: Optional[str] = None,
    _user: str = Depends(check_auth),
) -> HTMLResponse:
    try:
        with access_conn() as conn:
            _ensure_user_profiles_schema(conn)
            base_rows = conn.execute(
                """
                SELECT
                    au.user_id,
                    au.first_seen_at,
                    up.full_name,
                    COALESCE(up.role, 'employee') AS role,
                    up.department,
                    up.username,
                    up.telegram_full_name,
                    up.avatar_file_id
                FROM allowed_users au
                LEFT JOIN user_profiles up ON up.user_id = au.user_id
                ORDER BY au.first_seen_at DESC
                """
            ).fetchall()

            for row in base_rows:
                user_id = int(row[0])
                has_username = bool(row[5])
                has_telegram_name = bool(row[6])
                has_avatar = bool(row[7])
                if not (has_username and has_telegram_name and has_avatar):
                    await _upsert_telegram_profile(conn, user_id)

            user_rows = conn.execute(
                """
                SELECT
                    au.user_id,
                    au.first_seen_at,
                    up.full_name,
                    COALESCE(up.role, 'employee') AS role,
                    up.department,
                    up.username,
                    up.telegram_full_name,
                    up.avatar_file_id
                FROM allowed_users au
                LEFT JOIN user_profiles up ON up.user_id = au.user_id
                ORDER BY au.first_seen_at DESC
                """
            ).fetchall()
            chat_rows = conn.execute(
                "SELECT chat_id, first_seen_at FROM allowed_chats ORDER BY first_seen_at DESC"
            ).fetchall()
            conn.commit()
    except Exception:
        user_rows, chat_rows = [], []

    users = [
        {
            "user_id": r[0],
            "first_seen_at": r[1],
            "full_name": r[2],
            "role": r[3] or "employee",
            "department": r[4],
            "username": r[5],
            "telegram_full_name": r[6],
            "avatar_file_id": r[7],
        }
        for r in user_rows
    ]
    chats = [{"chat_id": r[0], "first_seen_at": r[1]} for r in chat_rows]
    return templates.TemplateResponse(
        "users.html",
        {
            "request": request,
            "users": users,
            "chats": chats,
            "success": success,
            "role_choices": ROLE_CHOICES,
            "role_labels": ROLE_LABELS_RU,
        },
    )


@app.post("/users/remove")
async def remove_user(
    user_id: int = Form(...),
    _user: str = Depends(check_auth),
) -> RedirectResponse:
    with access_conn() as conn:
        _ensure_user_profiles_schema(conn)
        conn.execute("DELETE FROM allowed_users WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM user_profiles WHERE user_id=?", (user_id,))
        conn.commit()
    return RedirectResponse(url="/users?success=removed", status_code=303)


@app.post("/users/add")
async def add_user(
    user_id: int = Form(...),
    full_name: Optional[str] = Form(None),
    role: str = Form("employee"),
    department: Optional[str] = Form(None),
    _user: str = Depends(check_auth),
) -> RedirectResponse:
    role = role.strip().lower() if role else "employee"
    if role not in ROLE_CHOICES:
        raise HTTPException(status_code=422, detail="Некорректная роль")

    with access_conn() as conn:
        _ensure_user_profiles_schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO allowed_users(user_id) VALUES(?)", (user_id,)
        )

        telegram_profile = await _upsert_telegram_profile(conn, user_id)
        effective_full_name = (full_name or "").strip() or telegram_profile.get("telegram_full_name")

        conn.execute(
            "INSERT INTO user_profiles(user_id, full_name, role, department, username, telegram_full_name, avatar_file_id) "
            "VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "full_name=COALESCE(excluded.full_name, user_profiles.full_name), "
            "role=excluded.role, department=excluded.department, "
            "username=COALESCE(excluded.username, user_profiles.username), "
            "telegram_full_name=COALESCE(excluded.telegram_full_name, user_profiles.telegram_full_name), "
            "avatar_file_id=COALESCE(excluded.avatar_file_id, user_profiles.avatar_file_id), "
            "updated_at=CURRENT_TIMESTAMP",
            (
                user_id,
                effective_full_name or None,
                role,
                (department or "").strip() or None,
                telegram_profile.get("username"),
                telegram_profile.get("telegram_full_name"),
                telegram_profile.get("avatar_file_id"),
            ),
        )
        conn.commit()
    return RedirectResponse(url="/users?success=added", status_code=303)


@app.post("/users/save_profile")
async def save_user_profile(
    user_id: int = Form(...),
    full_name: Optional[str] = Form(None),
    role: str = Form("employee"),
    department: Optional[str] = Form(None),
    _user: str = Depends(check_auth),
) -> RedirectResponse:
    role = role.strip().lower() if role else "employee"
    if role not in ROLE_CHOICES:
        raise HTTPException(status_code=422, detail="Некорректная роль")

    with access_conn() as conn:
        _ensure_user_profiles_schema(conn)
        conn.execute("INSERT OR IGNORE INTO allowed_users(user_id) VALUES(?)", (user_id,))
        conn.execute(
            "INSERT INTO user_profiles(user_id, full_name, role, department) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "full_name=excluded.full_name, role=excluded.role, department=excluded.department, "
            "updated_at=CURRENT_TIMESTAMP",
            (user_id, (full_name or "").strip() or None, role, (department or "").strip() or None),
        )
        conn.commit()

    return RedirectResponse(url="/users?success=saved", status_code=303)


@app.get("/users/avatar/{user_id}")
async def user_avatar(user_id: int, _user: str = Depends(check_auth)) -> Response:
    if not BOT_TOKEN:
        return Response(status_code=404)

    with access_conn() as conn:
        _ensure_user_profiles_schema(conn)
        row = conn.execute(
            "SELECT avatar_file_id FROM user_profiles WHERE user_id=?",
            (user_id,),
        ).fetchone()

    avatar_file_id = (row[0] if row else None) or None
    if not avatar_file_id:
        return Response(status_code=404)

    tg_file = await _telegram_api_get("getFile", {"file_id": avatar_file_id})
    file_path = tg_file.get("file_path") if tg_file else None
    if not file_path:
        return Response(status_code=404)

    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            image_resp = await client.get(file_url)
        if image_resp.status_code != 200:
            return Response(status_code=404)
        return StreamingResponse(
            BytesIO(image_resp.content),
            media_type=image_resp.headers.get("content-type", "image/jpeg"),
        )
    except Exception:
        logger.exception("Error loading avatar for user_id=%s", user_id)
        return Response(status_code=404)
