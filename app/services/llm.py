from __future__ import annotations

import os, re, json, httpx, unicodedata
from ..config import settings
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from .llm_json import complete_json

ORDER_WORDS = [r"mua", r"đặt", r"mình lấy", r"order", r"mua giúp"]
CATALOG_WORDS = [r"giá", r"còn không", r"tác giả", r"thể loại", r"tồn", r"bao nhiêu"]
PHONE_RE = re.compile(r"(0\d{9,10})")
QTY_RE = re.compile(r"(\d+)\s*(quyển|cuốn|q|x)\b", re.I)

def classify_intent(text: str) -> str:
    t = text.lower()
    if any(re.search(w, t) for w in ORDER_WORDS):
        return "order"
    if any(re.search(w, t) for w in CATALOG_WORDS):
        return "catalog"
    # default: try order if contains quoted title
    if '"' in t or '“' in t:
        return "order"
    return "catalog"

def extract_order_entities(text: str) -> dict:
    # naive extractor: try qty/phone, others left None; title_or_query = content inside quotes
    title = None
    m = re.search(r"""["'“”‘’](.+?)["'“”‘’]""", text)
    if m:
        title = m.group(1)
    q = None
    mq = QTY_RE.search(text)
    if mq:
        try:
            q = int(mq.group(1))
        except:
            q = None
    phone = None
    mp = PHONE_RE.search(text.replace(" ",""))
    if mp:
        phone = mp.group(1)
    return {
        "title_or_query": title or text,
        "quantity": q,
        "customer_name": None,
        "phone": phone,
        "address": None
    }
def _strip_diacritics(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn").lower()

# Từ khóa kích hoạt filter chủ đề
_TOPIC_HINTS = ["chu de", "chude", "the loai", "theloai", "danh muc", "danhmuc", "thuoc the loai", "loai sach", "genre"]

# Một ít synonym gợi ý (có thể mở rộng theo catalog của bạn)
_TOPIC_SYNONYMS = {
    "phieu luu": ["adventure", "tham hiem", "hanh trinh"],
    "khoa hoc": ["science", "vu tru", "vat ly", "sinh hoc"],
    "lich su":  ["history", "nhan loai"],
    "tam ly":   ["self-help", "ky nang", "phat trien ban than"],
    "thieu nhi":["thieu nien", "thieu dong", "thieu nhi"],
}

def parse_catalog_query(text: str) -> dict:
    """
    Trả về {"query": <chuỗi để search>, "category": <lọc theo thể loại hoặc None>}
    Heuristic: nếu câu nêu 'chủ đề/thể loại' → ưu tiên coi đó là filter.
    """
    raw = text or ""
    norm = _strip_diacritics(raw)
    category = None

    # Nếu câu có cụm 'chủ đề/thể loại' kèm từ sau đó
    for hint in _TOPIC_HINTS:
        if hint in norm:
            # lấy cụm từ sau hint (đơn giản hóa)
            # ví dụ: "sách chủ đề phiêu lưu" -> cat_guess="phieu luu"
            m = re.search(r"(?:chu de|the loai|danh muc|thuoc the loai|loai sach)\s+([a-zA-Z0-9 \-\_]+)", norm)
            if m:
                category = m.group(1).strip()
            break

    # Nếu không phát hiện bằng hint, nhưng user chỉ nêu 1-2 từ (vd "phiêu lưu", "khoa học") thì coi như category
    tokens = [t for t in re.split(r"[^a-z0-9]+", norm) if t]
    if not category and 1 <= len(tokens) <= 3:
        # loại bớt từ "sach", "muon", "tim"
        core = " ".join([t for t in tokens if t not in {"sach","sachve","muon","tim","toi","minh"}])
        if core:
            category = core

    # map synonym
    if category:
        for k, alts in _TOPIC_SYNONYMS.items():
            if category == k or any(category in alt for alt in alts):
                category = k
                break

    # Query text để search (nếu người dùng có cụm tên sách/tác giả vẫn giữ)
    return {"query": raw.strip(), "category": category}

class NLUOut(BaseModel):
    intent: str = Field(..., description="search | order | status | smalltalk | unknown")
    query: Optional[str] = None
    book_ref: Optional[str] = None   # mô tả thô khách nói (vd: 'Sherlock Holmes Toàn Tập')
    book_id: Optional[int] = None    # nếu suy ra được từ bối cảnh/last_hits
    quantity: Optional[int] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    customer_name: Optional[str] = None
    ask: Optional[str] = None        # nếu còn thiếu, đề xuất câu hỏi ngắn

def nlu_resolve_from_context(
    user_text: str,
    recent_dialog: List[Dict[str, str]],
    last_hits: List[Dict[str, Any]],
    current_slots: Dict[str, Any],
) -> dict:
    """
    Dùng LLM hiểu ngữ cảnh: xác định intent + slot từ lịch sử & danh sách kết quả gần nhất.
    Trả JSON chặt chẽ (NLUOut).
    """
    system = (
        "Bạn là NLU cho Bookstore. Nhiệm vụ: từ lịch sử hội thoại, câu nhập mới, và danh sách sách gần nhất "
        "(last_hits), hãy rút trích intent và các slot. Nếu người dùng nhắc đến một cuốn sách vừa liệt kê "
        "('cuốn đầu tiên', 'Sherlock Holmes Toàn Tập', 'id 7'...), hãy điền book_id tương ứng. "
        "Nếu thiếu thông tin để đặt hàng (book_id, quantity, phone, address, customer_name) hãy đề xuất 'ask' ngắn gọn (tiếng Việt). "
        "Trả về duy nhất JSON đúng schema."
    )
    context = {
        "recent_dialog": recent_dialog,          # [{"role":"user/assistant","content": "..."}]
        "last_hits": last_hits,                  # [{"book_id", "title", "author", ...}]
        "current_slots": current_slots or {},    # slot đã biết
    }
    schema_hint = {
        "intent": "search|order|status|smalltalk|unknown",
        "query?": "string",
        "book_ref?": "string",
        "book_id?": "int",
        "quantity?": "int",
        "phone?": "string",
        "address?": "string",
        "customer_name?": "string",
        "ask?": "string"
    }
    out = complete_json(
        base_url=getattr(settings, "ollama_base_url", "http://localhost:11434"),
        model=getattr(settings, "planner_model", "qwen2.5:14b-instruct"),
        system=system,
        user=user_text,
        context=context,
        schema_hint=schema_hint,
        schema_model=NLUOut,
    )
    return out