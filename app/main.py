from __future__ import annotations

import re
import unicodedata
import uuid
import anyio

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import text
from .services.agent import run_agent
from .config import settings
from .schemas import ChatIn, AdminLogin
from .db import (
    db_conn,
    # Books
    list_books, get_book_by_id, create_book, update_book, delete_book,
    # Orders
    create_order, list_orders_by_status, approve_order, cancel_order, get_order_session,
    # Chat history / sessions
    insert_chat, get_chat_history, ensure_chat_session, list_chat_sessions,
)
from .services.state import get_session, reset_session
from .services.rag import retriever
from .services.llm import classify_intent, extract_order_entities, QTY_RE, PHONE_RE
from .ws import hub


# -----------------------------------------------------------------------------
# App & assets
# -----------------------------------------------------------------------------
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def get_or_create_session_id(request: Request) -> str:
    """Lấy session_id từ cookie; nếu chưa có thì tạo mới & đảm bảo có bản ghi ChatSessions."""
    sid = request.session.get("session_id")
    if not sid:
        sid = uuid.uuid4().hex[:24]
        request.session["session_id"] = sid
        with db_conn() as conn:
            ensure_chat_session(conn, sid)
    return sid


def _norm(text: str) -> str:
    s = (text or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", "", s)
    return s


def _norm_ok(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z]", "", s)
    return s


_EDIT_TOKENS = {"sua", "doi", "thay", "chinh"}


def _is_edit_cmd(text: str) -> bool:
    n = _norm(text)
    return any(tok in n for tok in _EDIT_TOKENS)


def _which_field(text: str) -> str | None:
    t = text.lower()
    n = _norm(text)
    if "luong" in n or "quyen" in n or "số lượng" in t or "sl" in n:
        return "quantity"
    if "sdt" in n or "điện thoại" in t or "so dienthoai" in n:
        return "phone"
    if "dia" in n or "địa chỉ" in t:
        return "address"
    if ("ten" in n or "tên" in t) and "sách" not in t:
        return "customer_name"
    if "sach" in n or "tựa" in t or "tên sách" in t:
        return "book"
    return None


def _start_new_order_slots(st: dict) -> None:
    """Khởi tạo order mới: xoá book & quantity; giữ thông tin nhận hàng."""
    st["slots"]["book_id"] = None
    st["slots"]["quantity"] = None
    st["last_prompt"] = None
    st["state"] = "order_collect"


# -----------------------------------------------------------------------------
# Pages
# -----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    sid = get_or_create_session_id(request)
    return templates.TemplateResponse("index.html", {"request": request, "session_id": sid})


# -----------------------------------------------------------------------------
# Chat APIs
# -----------------------------------------------------------------------------
@app.get("/api/chat/history")
def chat_history(session_id: str):
    with db_conn() as conn:
        items = get_chat_history(conn, session_id, limit=1000)
    # không sửa dữ liệu; client render trực tiếp
    return {"session_id": session_id, "messages": items}


def _reply_and_log(sid: str, reply: str, state: str, data: dict | None = None):
    with db_conn() as conn:
        insert_chat(conn, sid, "assistant", reply)
    return {"session_id": sid, "reply": reply, "state": state, "data": data}


@app.post("/api/chat")
def chat_api(payload: ChatIn, request: Request):
    sid = payload.session_id or get_or_create_session_id(request)
    text_in = (payload.message or "").strip()

    with db_conn() as conn:
        ensure_chat_session(conn, sid)
        insert_chat(conn, sid, "user", text_in)

    reply = run_agent(text_in, sid)

    with db_conn() as conn:
        insert_chat(conn, sid, "assistant", reply)

    # tuỳ thích gửi thêm {state} để UI biết
    return {"session_id": sid, "reply": reply, "state": get_session(sid)}


# --- Reset session API (POST + GET cho tiện test) ---
def _do_reset_session(request: Request) -> JSONResponse:
    old_sid = request.session.get("session_id")
    new_sid = uuid.uuid4().hex[:24]
    request.session["session_id"] = new_sid
    with db_conn() as conn:
        ensure_chat_session(conn, new_sid)
    reset_session(old_sid)
    get_session(new_sid)
    return JSONResponse({"ok": True, "session_id": new_sid})


@app.post("/api/chat/reset")
def chat_reset_post(request: Request):
    try:
        return _do_reset_session(request)
    except Exception as e:
        return JSONResponse({"ok": False, "message": f"reset_failed: {e}"}, status_code=500)


@app.get("/api/chat/reset")
def chat_reset_get(request: Request):
    try:
        return _do_reset_session(request)
    except Exception as e:
        return JSONResponse({"ok": False, "message": f"reset_failed: {e}"}, status_code=500)


# -----------------------------------------------------------------------------
# Admin auth + dashboard (1 trang gộp Orders + Books + Chats)
# -----------------------------------------------------------------------------
@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return templates.TemplateResponse("admin_login.html", {"request": request})


@app.post("/admin/login")
def admin_login(request: Request, creds: AdminLogin):
    if creds.username == settings.admin_user and creds.password == settings.admin_pass:
        request.session["is_admin"] = True
        return {"ok": True, "redirect": "/admin"}
    return JSONResponse({"ok": False, "message": "Sai thông tin đăng nhập"}, status_code=401)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse("/admin/login", status_code=302)
    with db_conn() as conn:
        pending = list_orders_by_status(conn, "pending")
        approved = list_orders_by_status(conn, "approved")
        cancelled = list_orders_by_status(conn, "cancelled")
        books = list_books(conn)
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "pending": pending,
            "approved": approved,
            "cancelled": cancelled,
            "books": books,
        },
    )


# --- Books CRUD (JSON) ---
@app.post("/admin/books")
def admin_create_book(request: Request, data: dict = Body(...)):
    if not request.session.get("is_admin"):
        return JSONResponse({"ok": False, "message": "Unauthorized"}, status_code=401)
    with db_conn() as conn:
        bid = create_book(conn, data)
        b = get_book_by_id(conn, bid)
    retriever.upsert_book(b)
    return {"ok": True, "book_id": bid}


@app.put("/admin/books/{book_id}")
def admin_update_book(book_id: int, request: Request, data: dict = Body(...)):
    if not request.session.get("is_admin"):
        return JSONResponse({"ok": False, "message": "Unauthorized"}, status_code=401)
    with db_conn() as conn:
        update_book(conn, book_id, data)
        b = get_book_by_id(conn, book_id)
    retriever.upsert_book(b)
    return {"ok": True}


@app.delete("/admin/books/{book_id}")
def admin_delete_book(book_id: int, request: Request):
    if not request.session.get("is_admin"):
        return JSONResponse({"ok": False, "message": "Unauthorized"}, status_code=401)
    with db_conn() as conn:
        delete_book(conn, book_id)
    retriever.delete_book(book_id)
    return {"ok": True}


# --- Orders actions ---
@app.post("/admin/orders/{order_id}/approve")
def admin_approve(order_id: int, request: Request):
    if not request.session.get("is_admin"):
        return JSONResponse({"ok": False, "message": "Unauthorized"}, status_code=401)
    with db_conn() as conn:
        ok = approve_order(conn, order_id)
        sid = get_order_session(conn, order_id)
    if ok:
        if sid:
            msg = f"Đơn #{order_id} đã được duyệt. Cảm ơn bạn!"
            with db_conn() as conn2:
                insert_chat(conn2, sid, "assistant", msg)
            anyio.from_thread.run(hub.send_to_user, sid, {"type": "order_approved", "order_id": order_id})
        return {"ok": True}
    return JSONResponse({"ok": False, "message": "Không đủ tồn hoặc đơn không hợp lệ"}, status_code=400)


@app.post("/admin/orders/{order_id}/cancel")
def admin_cancel(order_id: int, request: Request):
    if not request.session.get("is_admin"):
        return JSONResponse({"ok": False, "message": "Unauthorized"}, status_code=401)
    with db_conn() as conn:
        ok = cancel_order(conn, order_id)
        sid = get_order_session(conn, order_id)
    if ok:
        if sid:
            msg = f"Đơn #{order_id} đã bị hủy. Nếu cần, mình có thể gợi ý cuốn tương tự."
            with db_conn() as conn2:
                insert_chat(conn2, sid, "assistant", msg)
            anyio.from_thread.run(hub.send_to_user, sid, {"type": "order_cancelled", "order_id": order_id})
        return {"ok": True}
    return JSONResponse({"ok": False, "message": "Đơn không hợp lệ"}, status_code=400)


# --- Admin: APIs xem lịch sử theo session ---
@app.get("/admin/api/chats")
def admin_list_chats(request: Request, q: str | None = None, limit: int = 200):
    if not request.session.get("is_admin"):
        return JSONResponse({"ok": False, "message": "Unauthorized"}, status_code=401)
    with db_conn() as conn:
        items = list_chat_sessions(conn, q=q, limit=limit)
    for it in items:
        if it.get("last_time") is not None:
            it["last_time"] = it["last_time"].isoformat(sep=" ", timespec="seconds")
    return {"ok": True, "items": items}


@app.get("/admin/api/chats/{session_id}")
def admin_chat_history(request: Request, session_id: str, limit: int = 1000):
    if not request.session.get("is_admin"):
        return JSONResponse({"ok": False, "message": "Unauthorized"}, status_code=401)
    with db_conn() as conn:
        items = get_chat_history(conn, session_id, limit=limit)
    for it in items:
        if it.get("created_at") is not None:
            it["created_at"] = it["created_at"].isoformat(sep=" ", timespec="seconds")
    return {"ok": True, "session_id": session_id, "messages": items}


# -----------------------------------------------------------------------------
# WebSockets
# -----------------------------------------------------------------------------
@app.websocket("/ws/{session_id}")
async def ws_user(ws: WebSocket, session_id: str):
    # đảm bảo có dòng session (tránh lỗi FK khi ghi chat sau đó)
    with db_conn() as conn:
        ensure_chat_session(conn, session_id)
    await hub.connect_user(session_id, ws)
    try:
        while True:
            await ws.receive_text()  # giữ kết nối
    except WebSocketDisconnect:
        await hub.disconnect_user(session_id, ws)


@app.websocket("/ws/admin")
async def ws_admin(ws: WebSocket):
    await hub.connect_admin(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect_admin(ws)
