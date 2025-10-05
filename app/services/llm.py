import os, re, json, httpx, unicodedata
from ..config import settings

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