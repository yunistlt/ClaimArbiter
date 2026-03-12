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
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
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


def human_task_type(value: str) -> str:
    return TASK_TYPE_NAMES.get(value, value)


def status_ru(value: str) -> str:
    return STATUS_RU.get(value, value)


def mode_ru(value: str) -> str:
    return MODE_RU.get(value, value)


templates.env.filters["human_task_type"] = human_task_type
templates.env.filters["status_ru"] = status_ru
templates.env.filters["mode_ru"] = mode_ru

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
            user_rows = conn.execute(
                "SELECT user_id, first_seen_at FROM allowed_users ORDER BY first_seen_at DESC"
            ).fetchall()
            chat_rows = conn.execute(
                "SELECT chat_id, first_seen_at FROM allowed_chats ORDER BY first_seen_at DESC"
            ).fetchall()
    except Exception:
        user_rows, chat_rows = [], []

    users = [{"user_id": r[0], "first_seen_at": r[1]} for r in user_rows]
    chats = [{"chat_id": r[0], "first_seen_at": r[1]} for r in chat_rows]
    return templates.TemplateResponse(
        "users.html",
        {"request": request, "users": users, "chats": chats, "success": success},
    )


@app.post("/users/remove")
async def remove_user(
    user_id: int = Form(...),
    _user: str = Depends(check_auth),
) -> RedirectResponse:
    with access_conn() as conn:
        conn.execute("DELETE FROM allowed_users WHERE user_id=?", (user_id,))
        conn.commit()
    return RedirectResponse(url="/users?success=removed", status_code=303)


@app.post("/users/add")
async def add_user(
    user_id: int = Form(...),
    _user: str = Depends(check_auth),
) -> RedirectResponse:
    with access_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO allowed_users(user_id) VALUES(?)", (user_id,)
        )
        conn.commit()
    return RedirectResponse(url="/users?success=added", status_code=303)
