# app/services/agent.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
import re, unicodedata
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text

from ..config import settings
from ..db import db_conn
from .state import get_session
from .agent_tools import REGISTRY
from .agent_tools import CreateOrderIn  # dùng cho rule chốt đơn
from .llm_json import complete_json
from .llm import nlu_resolve_from_context, extract_order_entities

# ================= Helpers =================

def _fmt_currency(v: int) -> str:
    try:
        return f"{int(v):,}đ".replace(",", ".")
    except Exception:
        return str(v)

def _recent_dialog(session_id: str, limit: int = 16) -> List[Dict[str, str]]:
    """Lấy lịch sử chat gần đây (user/assistant) để LLM nắm ngữ cảnh."""
    with db_conn() as conn:
        rows = conn.execute(
            text("SELECT role, content FROM ChatMessages WHERE session_id=:sid ORDER BY id DESC LIMIT :lim"),
            {"sid": session_id, "lim": limit},
        ).fetchall()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def _render_books_list(items: List[Dict]) -> str:
    if not items:
        return "Mình chưa tìm thấy sách phù hợp. Bạn mô tả rõ hơn (tên/tác giả/thể loại) giúp mình nhé!"
    lines = []
    for b in items[:5]:
        lines.append(
            f"• {b['title']} – {b['author']} | {_fmt_currency(b['price'])} | tồn: {b['stock']} | id: {b['book_id']}"
        )
    body = "\n".join(lines)
    return "Mình tìm thấy:\n" + body + "\nBạn muốn đặt cuốn nào? (nhập **id** hoặc **tên sách**)."

def _book_by_id(book_id: int) -> Optional[Dict[str, Any]]:
    with db_conn() as conn:
        rows = conn.execute(
            text("SELECT book_id, title, author, price, stock, category FROM Books WHERE book_id = :bid"),
            {"bid": int(book_id)},
        ).mappings().all()
    return rows[0] if rows else None

def _confirm_text(slots: Dict[str, Any]) -> Optional[str]:
    """Tạo đoạn xác nhận đơn nếu đã đủ slot."""
    required = ["book_id", "quantity", "phone", "address", "customer_name"]
    if not all(slots.get(k) for k in required):
        return None
    b = _book_by_id(int(slots["book_id"]))
    if not b:
        return None
    qty = int(slots["quantity"])
    total = int(b["price"]) * qty
    return (
        "Xác nhận đơn:\n"
        f"• {b['title']} × {qty} – {_fmt_currency(b['price'])} → Tổng **{_fmt_currency(total)}**\n"
        f"Người nhận: {slots['customer_name']} – {slots['phone']}\n"
        f"Địa chỉ: {slots['address']}\n"
        "Gõ **OK** để đặt, hoặc nhập 'Sửa ...' để chỉnh."
    )

# alias tool cho planner (đề phòng LLM đặt lệch tên)
TOOL_ALIASES = {
    "searchbooks": "search_books", "search_book": "search_books", "search": "search_books",
    "find_books": "search_books", "findbook": "search_books", "lookup_books": "search_books",
    "rag_search": "search_books",
    "createorder": "create_order", "place_order": "create_order", "order_create": "create_order",
    "submit_order": "create_order",
    "order_status": "last_order_status", "get_order_status": "last_order_status", "status": "last_order_status",
}
def _resolve_tool(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    n = name.strip().lower().replace("-", "_")
    if n in REGISTRY:
        return n
    return TOOL_ALIASES.get(n) or TOOL_ALIASES.get(n.replace(" ", ""))

# ================= JSON schema cho Planner / Responder =================

class PlanAction(BaseModel):
    tool: str = Field(..., description="search_books | create_order | last_order_status")
    args: Dict[str, Any] = Field(default_factory=dict)

class PlanOut(BaseModel):
    actions: List[PlanAction] = Field(default_factory=list)
    ask: Optional[str] = Field(None, description="Câu hỏi bù nếu thiếu thông tin")

class RespondOut(BaseModel):
    say: str = Field(..., description="Câu trả lời tự nhiên (TIẾNG VIỆT)")

# ================= Agent =================

def run_agent(user_text: str, session_id: str, max_actions: int = 3) -> str:
    """
    Agent 3 giai đoạn: NLU → (Shortcut) → Planner→Execute→Responder.
    - NLU theo ngữ cảnh điền slot trước (book_id/quantity/phone/address/name).
    - Nếu là search → gọi tool & hiển thị.
    - Nếu order đủ slot → hiển thị phiếu xác nhận (state=await_confirm).
    - Nếu đang chờ xác nhận và user 'OK' → tạo đơn.
    - Nếu thiếu → hỏi bù ngắn gọn.
    - Ambiguous → Planner→Execute→Responder.
    """
    st = get_session(session_id)
    tok = (user_text or "").strip().lower()

    # ===== RULE: chốt đơn khi đang chờ xác nhận =====
    if st["state"] == "await_confirm" and tok in {"ok", "oke", "okay", "đồng ý", "dong y", "xac nhan", "xác nhận"}:
        try:
            args = CreateOrderIn(**{
                "book_id": st["slots"]["book_id"],
                "quantity": st["slots"]["quantity"],
                "phone": st["slots"]["phone"],
                "address": st["slots"]["address"],
                "customer_name": st["slots"]["customer_name"],
            })
        except ValidationError:
            # còn thiếu slot -> rơi xuống NLU/planner
            pass
        else:
            from .agent_tools import _create_order  # type: ignore
            ob = _create_order(args, {"session_id": session_id, "state": st, "user_text": user_text})
            return f"Đã tạo đơn #{ob['order_id']} (chờ duyệt). Mình sẽ báo khi Admin duyệt/hủy."

    # ===== NLU: hiểu ngữ cảnh & lấp slot =====
    # gợi ý nhanh từ câu nhập (để tăng độ bắt số lượng/phone)
    ents = extract_order_entities(user_text)
    for k in ("quantity", "phone"):
        if ents.get(k) and not st["slots"].get(k):
            st["slots"][k] = ents[k]

    last_hits = (st.get("cache") or {}).get("last_hits") or []
    nlu = nlu_resolve_from_context(
        user_text=user_text,
        recent_dialog=_recent_dialog(session_id),
        last_hits=last_hits,
        current_slots=st.get("slots") or {},
    )
    # merge slots từ NLU
    for k in ["book_id", "quantity", "phone", "address", "customer_name"]:
        v = nlu.get(k)
        if v is not None and not st["slots"].get(k):
            st["slots"][k] = v

    # ===== SHORTCUTS =====
    # 1) Nếu intent=search → gọi tool trực tiếp
    if nlu.get("intent") == "search":
        spec = REGISTRY.get("search_books")
        if spec:
            args = spec.input_schema(query=nlu.get("query") or user_text, limit=5)
            ctx = {"session_id": session_id, "state": st, "user_text": user_text}
            result = spec.func(args, ctx)
            items = (result or {}).get("results") or []
            return _render_books_list(items)

    # 2) Nếu order đã đủ slot → hiển thị phiếu xác nhận (không auto tạo)
    confirm = _confirm_text(st["slots"])
    if confirm and st["state"] != "await_confirm":
        st["state"] = "await_confirm"
        return confirm

    # 3) Nếu intent=order nhưng thiếu thông tin → hỏi bù (ưu tiên câu hỏi NLU đề xuất)
    if nlu.get("intent") == "order":
        if nlu.get("ask"):
            return nlu["ask"]
        # hỏi bù tối thiểu
        missing = [k for k in ["book_id", "quantity", "customer_name", "phone", "address"] if not st["slots"].get(k)]
        if missing:
            friendly = {
                "book_id": "Bạn cho mình **id/tên sách** muốn mua?",
                "quantity": "Bạn muốn **mấy quyển** ạ?",
                "customer_name": "Bạn cho mình **tên người nhận**?",
                "phone": "Bạn cho mình **SĐT** liên hệ?",
                "address": "Bạn cho mình **địa chỉ nhận hàng**?",
            }
            return friendly.get(missing[0], "Bạn bổ sung giúp mình thông tin còn thiếu nhé?")

    # ===== PLANNER → EXECUTE → RESPONDER (cho case mơ hồ) =====
    tools_contract = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema.model_json_schema()}
        for t in REGISTRY.values()
    ]
    system_plan = (
        "Bạn là Bookstore Agent. Lập kế hoạch 0..2 hành động (tool) cần gọi để giúp người dùng đạt mục tiêu. "
        "Nếu đủ dữ liệu để đặt hàng, hãy đề xuất create_order. Nếu đang tìm sách, đề xuất search_books với truy vấn phù hợp. "
        "Nếu thiếu dữ liệu, hãy điền 'ask' (một câu hỏi ngắn). Trả về DUY NHẤT JSON theo schema."
    )
    plan = complete_json(
        base_url=getattr(settings, "ollama_base_url", "http://localhost:11434"),
        model=getattr(settings, "planner_model", "qwen2.5:14b-instruct"),
        system=system_plan,
        user=user_text,
        context={"state": st, "tools": tools_contract, "nlu": nlu},
        schema_hint={"actions":"array[{tool,args}]", "ask?":"string"},
        schema_model=PlanOut,
    )

    # Execute
    observations: List[Dict[str, Any]] = []
    for act in plan.get("actions", [])[:max_actions]:
        tool_name = _resolve_tool(act.get("tool"))
        if not tool_name or tool_name not in REGISTRY:
            observations.append({"tool": act.get("tool"), "error": "unknown_tool"})
            continue
        spec = REGISTRY[tool_name]
        try:
            args = spec.input_schema(**(act.get("args") or {}))
        except ValidationError as e:
            observations.append({"tool": tool_name, "error": "args_invalid", "detail": e.errors()})
            continue
        ctx = {"session_id": session_id, "state": st, "user_text": user_text}
        result = spec.func(args, ctx)
        # Chuẩn hoá kết quả search cho Responder
        if tool_name == "search_books":
            result = {"items": (result or {}).get("results") or []}
        observations.append({"tool": tool_name, "result": result})

    # Nếu planner nói thiếu thông tin → hỏi bù luôn
    if plan.get("ask"):
        return plan["ask"]

    # Responder
    system_resp = (
        "Bạn là Bookstore Agent. Viết câu trả lời **TIẾNG VIỆT, tự nhiên** dựa vào 'observations' và 'state'. "
        "Nếu có danh sách sách (search_books.items), hãy liệt kê 3–5 dòng theo mẫu: "
        "Tiêu đề – Tác giả | giá | tồn | id. Nếu đã đủ thông tin đặt hàng nhưng chưa xác nhận, "
        "hãy trình bày phiếu tóm tắt và mời người dùng gõ OK. Trả về JSON với field 'say'."
    )
    respond = complete_json(
        base_url=getattr(settings, "ollama_base_url", "http://localhost:11434"),
        model=getattr(settings, "planner_model", "qwen2.5:14b-instruct"),
        system=system_resp,
        user="",
        context={"state": st, "observations": observations},
        schema_hint={"say":"string"},
        schema_model=RespondOut,
    )
    say = respond.get("say") or "Mình đã ghi nhận nhé."

    # Safety: nếu Responder quên render list trong khi có search_books
    if any(obs.get("tool") == "search_books" for obs in observations):
        # nếu câu quá ngắn, tự render
        if len(say.strip()) < 12:
            for obs in observations:
                if obs.get("tool") == "search_books":
                    items = (obs.get("result") or {}).get("items") or []
                    return _render_books_list(items)
    return say
