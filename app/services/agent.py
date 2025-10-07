from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Optional

import anyio

from ..db import (
    db_conn,
    get_book_by_id,
    fetch_books_fulltext,
    fetch_books_keywords,
    create_order,
)
from .llm import (
    classify_intent,
    extract_order_entities,
    QTY_RE,
    PHONE_RE,
)
from .rag import retriever


@dataclass
class AgentResult:
    reply: str
    state: str
    data: Optional[Dict[str, Any]] = None


class BookstoreAgent:
    """Simple tool-based agent that orchestrates catalog search and ordering flows."""

    _EDIT_TOKENS = {"sua", "doi", "thay", "chinh"}

    def __init__(self, hub):
        self.hub = hub

    # ------------------------------------------------------------------
    # Normalisation helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _norm(text: str) -> str:
        s = (text or "").strip().lower()
        s = unicodedata.normalize("NFD", s)
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
        s = re.sub(r"\s+", "", s)
        return s

    @staticmethod
    def _norm_ok(text: str) -> str:
        s = (text or "").strip().lower()
        s = unicodedata.normalize("NFD", s)
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
        s = re.sub(r"[^a-z]", "", s)
        return s

    @classmethod
    def _is_edit_cmd(cls, text: str) -> bool:
        return any(tok in cls._norm(text) for tok in cls._EDIT_TOKENS)

    @staticmethod
    def _which_field(text: str) -> Optional[str]:
        t = text.lower()
        n = BookstoreAgent._norm(text)
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

    @staticmethod
    def _start_new_order_slots(st: Dict[str, Any]) -> None:
        st["slots"]["book_id"] = None
        st["slots"]["quantity"] = None
        st["last_prompt"] = None
        st["state"] = "order_collect"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _format_confirmation(st: Dict[str, Any]) -> AgentResult:
        with db_conn() as conn:
            book = get_book_by_id(conn, st["slots"]["book_id"])
        qty = st["slots"]["quantity"]
        total = (book["price"] * qty) if book else 0
        reply = (
            f"Xác nhận đơn:\n• {book['title']} × {qty} – {book['price']:,}đ → Tổng **{total:,}đ**\n"
            f"Người nhận: {st['slots']['customer_name']} – {st['slots']['phone']}\n"
            f"Địa chỉ: {st['slots']['address']}\n"
            f"Gõ **OK** để đặt, hoặc nhập 'Sửa ...' để chỉnh."
        )
        st["state"] = "await_confirm"
        st["last_prompt"] = None
        return AgentResult(reply=reply, state="await_confirm", data={"book": book, "total": total})

    @staticmethod
    def _catalog_search(query: str, limit: int = 5) -> list[Dict[str, Any]]:
        results = retriever.search(query, limit=limit)
        if results:
            return results
        # fallback lexical search if vector retriever empty
        with db_conn() as conn:
            try:
                return fetch_books_fulltext(conn, query, limit=limit)
            except Exception:
                try:
                    return fetch_books_keywords(conn, query, limit=limit)
                except Exception:
                    return []

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------
    def handle_message(self, sid: str, text_in: str, st: Dict[str, Any]) -> AgentResult:
        text_in = (text_in or "").strip()

        # Quick command: mua thêm khi đang có order trước
        norm = self._norm(text_in)
        if any(k in norm for k in ["muathem", "datthem"]):
            self._start_new_order_slots(st)

        if st["state"] in {"await_admin_decision"} and "mua" in text_in.lower():
            self._start_new_order_slots(st)

        # Awaiting confirmation flow
        if st["state"] == "await_confirm":
            token = self._norm_ok(text_in)
            if token in {"ok", "oke", "okay", "dongy", "xacnhan"}:
                slots = st["slots"]
                payload_sql = {
                    "customer_name": slots["customer_name"],
                    "phone": slots["phone"],
                    "address": slots["address"],
                    "book_id": slots["book_id"],
                    "quantity": slots["quantity"],
                    "session_id": sid,
                }
                with db_conn() as conn:
                    order_id = create_order(conn, payload_sql)
                st["state"] = "await_admin_decision"
                st["last_prompt"] = None
                self._notify_admin_new_order(order_id)
                reply = (
                    f"Đã tạo đơn #{order_id} (chờ duyệt). Mình sẽ báo ngay khi Admin duyệt/hủy."
                )
                return AgentResult(
                    reply=reply,
                    state="await_admin_decision",
                    data={"order_id": order_id},
                )

            if self._is_edit_cmd(text_in) or self._which_field(text_in):
                st["state"] = "order_collect"
                field = self._which_field(text_in)
                prompts = {
                    "quantity": ("ask_quantity", "Bạn muốn sửa **số lượng** thành bao nhiêu?"),
                    "phone": ("ask_phone", "Bạn gửi lại **SĐT** giúp mình nhé?"),
                    "address": ("ask_address", "Bạn sửa **địa chỉ** như thế nào ạ?"),
                    "customer_name": ("ask_name", "Tên người nhận mới là gì ạ?"),
                    "book": ("ask_new_book", "Bạn muốn đổi sang **sách nào**?"),
                }
                if field and field in prompts:
                    st["last_prompt"], reply = prompts[field]
                else:
                    st["last_prompt"] = "ask_edit_field"
                    reply = "Bạn muốn sửa gì: **số lượng**, **SĐT**, **địa chỉ**, **tên**, hay **sách**?"
                return AgentResult(reply=reply, state="order_collect")

            # repeat confirmation card
            return self._format_confirmation(st)

        # Slot filling stage
        if st["state"] == "order_collect":
            lp = st.get("last_prompt")
            if lp == "ask_quantity" and not st["slots"]["quantity"]:
                m = QTY_RE.search(text_in) or re.search(r"\b(\d+)\b", text_in)
                if m:
                    st["slots"]["quantity"] = int(m.group(1))
            elif lp == "ask_phone" and not st["slots"]["phone"]:
                m = PHONE_RE.search(text_in.replace(" ", ""))
                if m:
                    st["slots"]["phone"] = m.group(1)
            elif lp == "ask_address" and not st["slots"]["address"]:
                if len(text_in) >= 4:
                    st["slots"]["address"] = text_in
            elif lp == "ask_name" and not st["slots"]["customer_name"]:
                if len(text_in) >= 2:
                    st["slots"]["customer_name"] = text_in
            elif lp == "ask_edit_field":
                field = self._which_field(text_in)
                if field:
                    st["last_prompt"] = {
                        "quantity": "ask_quantity",
                        "phone": "ask_phone",
                        "address": "ask_address",
                        "customer_name": "ask_name",
                        "book": "ask_new_book",
                    }[field]
                else:
                    reply = "Bạn muốn sửa **số lượng**, **SĐT**, **địa chỉ**, **tên**, hay **sách**?"
                    return AgentResult(reply=reply, state="order_collect")
            elif lp == "ask_new_book":
                ents = extract_order_entities(text_in)
                results = retriever.search(ents["title_or_query"], limit=5)
                if not results:
                    reply = "Mình chưa nhận ra tựa sách mới. Bạn nói rõ tên/tác giả giúp mình nhé?"
                    return AgentResult(reply=reply, state="order_collect")
                st["slots"]["book_id"] = results[0]["book_id"]
                st["last_prompt"] = None

        # Intent routing
        intent = classify_intent(text_in)
        if intent == "catalog" and st["state"] == "catalog":
            results = self._catalog_search(text_in, limit=5)
            if not results:
                reply = "Xin lỗi, mình chưa tìm thấy sách phù hợp. Bạn mô tả rõ hơn (tên/tác giả/thể loại)?"
                return AgentResult(reply=reply, state="catalog", data={"results": []})
            top = results[:3]
            lines = [
                f"• {r['title']} – {r['author']} | {r['price']:,}đ | tồn: {r['stock']}"
                for r in top
            ]
            reply = "Mình tìm thấy:\n" + "\n".join(lines) + "\nBạn muốn đặt mua cuốn nào không?"
            return AgentResult(reply=reply, state="catalog", data={"results": top})

        # Default to order flow
        st["state"] = "order_collect"
        with db_conn() as conn:
            if not st["slots"]["book_id"]:
                ents = extract_order_entities(text_in)
                results = retriever.search(ents["title_or_query"], limit=5)
                if not results:
                    reply = "Mình chưa xác định được tựa sách. Bạn nói rõ tên/tác giả giúp mình nhé?"
                    return AgentResult(reply=reply, state="order_collect")
                book = results[0]
                st["slots"]["book_id"] = book["book_id"]
            else:
                book = get_book_by_id(conn, st["slots"]["book_id"])

        if not st["slots"]["quantity"]:
            st["last_prompt"] = "ask_quantity"
            reply = f"Bạn muốn mua **{book['title']}** mấy quyển ạ?"
            return AgentResult(reply=reply, state="order_collect", data={"book": book})
        if not st["slots"]["phone"]:
            st["last_prompt"] = "ask_phone"
            reply = "Cho mình xin **SĐT** liên hệ ạ?"
            return AgentResult(reply=reply, state="order_collect", data={"book": book})
        if not st["slots"]["address"]:
            st["last_prompt"] = "ask_address"
            reply = "Bạn cho mình **địa chỉ nhận hàng** với ạ?"
            return AgentResult(reply=reply, state="order_collect", data={"book": book})
        if not st["slots"]["customer_name"]:
            st["last_prompt"] = "ask_name"
            reply = "Tên người nhận là gì ạ?"
            return AgentResult(reply=reply, state="order_collect", data={"book": book})

        return self._format_confirmation(st)

    # ------------------------------------------------------------------
    # Side effects
    # ------------------------------------------------------------------
    def _notify_admin_new_order(self, order_id: int) -> None:
        payload = {"type": "new_order", "order_id": order_id}
        try:
            anyio.from_thread.run(self.hub.broadcast_admin, payload)
            return
        except RuntimeError:
            pass

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            anyio.run(self.hub.broadcast_admin, payload)
        else:
            loop.create_task(self.hub.broadcast_admin(payload))
