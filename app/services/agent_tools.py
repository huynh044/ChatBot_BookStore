# app/services/agent_tools.py
from __future__ import annotations
from typing import Callable, Dict, Any
from pydantic import BaseModel, Field
from ..db import db_conn, create_order
from .rag import retriever

class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: type[BaseModel]
    func: Callable[[BaseModel, dict], dict]  # (args, ctx) -> observation

REGISTRY: Dict[str, ToolSpec] = {}
def register(spec: ToolSpec): REGISTRY[spec.name] = spec

# --- Tool: search_books (RAG hybrid) ---
class SearchBooksIn(BaseModel):
    query: str = Field(..., description="Câu tìm kiếm (tên sách/tác giả/chủ đề)")
    limit: int = 5

def _search_books(args: SearchBooksIn, ctx: dict) -> dict:
    q = (args.query or ctx.get("user_text") or "").strip()
    results = retriever.search(q, limit=args.limit)
    return {"results": results}

register(ToolSpec(
    name="search_books",
    description="Tìm sách qua RAG (title/author/category, hybrid lexical+vector)",
    input_schema=SearchBooksIn,
    func=_search_books
))

# --- Tool: create_order ---
class CreateOrderIn(BaseModel):
    book_id: int
    quantity: int
    phone: str
    address: str
    customer_name: str

def _create_order(args: CreateOrderIn, ctx: dict) -> dict:
    payload = args.model_dump()
    payload["session_id"] = ctx["session_id"]
    with db_conn() as conn:
        order_id = create_order(conn, payload)
    # cập nhật state để kênh chat biết đang chờ duyệt
    ctx["state"]["state"] = "await_admin_decision"
    return {"order_id": order_id}

register(ToolSpec(
    name="create_order",
    description="Tạo đơn hàng mới từ các slot đã đủ",
    input_schema=CreateOrderIn,
    func=_create_order
))

# --- Tool: last_order_status (hỏi trạng thái đơn mới nhất theo session) ---
class LastOrderStatusIn(BaseModel):
    session_id: str

def _last_order_status(args: LastOrderStatusIn, ctx: dict) -> dict:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT order_id, status FROM Orders WHERE session_id=:sid ORDER BY order_id DESC LIMIT 1",
            {"sid": args.session_id}
        ).mappings().all()
    if not rows:
        return {"found": False}
    return {"found": True, "order_id": rows[0]["order_id"], "status": rows[0]["status"]}

register(ToolSpec(
    name="last_order_status",
    description="Lấy trạng thái đơn gần nhất của phiên chat hiện tại",
    input_schema=LastOrderStatusIn,
    func=_last_order_status
))
