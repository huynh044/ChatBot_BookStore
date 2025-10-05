from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from contextlib import contextmanager
from urllib.parse import quote_plus
from .config import settings

def _build_url() -> str:
    user = quote_plus(settings.db_user)
    pwd  = quote_plus(settings.db_pass)
    host = settings.db_host
    port = settings.db_port
    db   = settings.db_name
    return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{db}"

engine: Engine = create_engine(_build_url(), pool_pre_ping=True, future=True)

@contextmanager
def db_conn():
    with engine.connect() as conn:
        yield conn

# ---------- Books ----------
def list_books(conn):
    rows = conn.execute(text("""
      SELECT book_id, title, author, price, stock, category
      FROM Books ORDER BY book_id DESC
    """)).mappings().all()
    return [dict(r) for r in rows]

def get_book_by_id(conn, book_id:int):
    r = conn.execute(text("""
      SELECT book_id, title, author, price, stock, category
      FROM Books WHERE book_id=:id
    """), {"id": book_id}).mappings().first()
    return dict(r) if r else None

def create_book(conn, data: dict) -> int:
    r = conn.execute(text("""
      INSERT INTO Books(title,author,price,stock,category)
      VALUES (:title,:author,:price,:stock,:category)
    """), data)
    conn.commit()
    return r.lastrowid

def update_book(conn, book_id:int, data: dict) -> bool:
    conn.execute(text("""
      UPDATE Books SET title=:title,author=:author,price=:price,stock=:stock,category=:category
      WHERE book_id=:id
    """), {**data, "id": book_id})
    conn.commit()
    return True

def delete_book(conn, book_id:int) -> bool:
    conn.execute(text("DELETE FROM Books WHERE book_id=:id"), {"id": book_id})
    conn.commit()
    return True

# ---------- Orders ----------
def create_order(conn, payload: dict) -> int:
    r = conn.execute(text("""
      INSERT INTO Orders (customer_name, phone, address, book_id, quantity, status, session_id)
      VALUES (:customer_name, :phone, :address, :book_id, :quantity, 'pending', :session_id)
    """), payload)
    conn.commit()
    return r.lastrowid

def list_orders_by_status(conn, status:str, limit:int=200):
    rows = conn.execute(text("""
      SELECT o.order_id, o.customer_name, o.phone, o.address,
             o.book_id, b.title, b.author, o.quantity, o.status, o.created_at,
             b.price, (b.price * o.quantity) AS total, o.session_id
      FROM Orders o
      JOIN Books b ON b.book_id=o.book_id
      WHERE o.status=:status
      ORDER BY o.created_at DESC
      LIMIT :lim
    """), {"status": status, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]

def approve_order(conn, order_id:int) -> bool:
    with conn.begin():
        r = conn.execute(text("SELECT book_id, quantity FROM Orders WHERE order_id=:id FOR UPDATE"),
                         {"id": order_id}).mappings().first()
        if not r: return False
        bid, qty = r["book_id"], r["quantity"]
        upd = conn.execute(text("""
          UPDATE Books SET stock = stock - :qty
          WHERE book_id=:bid AND stock >= :qty
        """), {"qty": qty, "bid": bid})
        if upd.rowcount == 0: return False
        conn.execute(text("UPDATE Orders SET status='approved' WHERE order_id=:id"), {"id": order_id})
    return True

def cancel_order(conn, order_id:int) -> bool:
    res = conn.execute(text("UPDATE Orders SET status='cancelled' WHERE order_id=:id"), {"id": order_id})
    conn.commit()
    return res.rowcount > 0

def get_order_session(conn, order_id:int):
    r = conn.execute(text("SELECT session_id FROM Orders WHERE order_id=:id"), {"id": order_id}).mappings().first()
    return r["session_id"] if r else None

# ---------- Fulltext for RAG ----------
def fetch_books_fulltext(conn, q: str, limit:int=10):
    try:
        rows = conn.execute(text("""
          SELECT book_id, title, author, price, stock, category,
                 MATCH(title, author) AGAINST (:q IN NATURAL LANGUAGE MODE) AS score
          FROM Books
          WHERE MATCH(title, author) AGAINST (:q IN NATURAL LANGUAGE MODE)
          ORDER BY score DESC
          LIMIT :lim
        """), {"q": q, "lim": limit}).mappings().all()
    except Exception:
        rows = conn.execute(text("""
          SELECT book_id, title, author, price, stock, category, 0.0 AS score
          FROM Books
          WHERE title LIKE :like OR author LIKE :like
          LIMIT :lim
        """), {"like": f"%{q}%", "lim": limit}).mappings().all()
    return [dict(r) for r in rows]

# ---------- Chat history ----------
def ensure_chat_session(conn, session_id: str):
    conn.execute(
        text("INSERT IGNORE INTO ChatSessions(session_id) VALUES (:sid)"),
        {"sid": session_id},
    )
    conn.commit()

def get_chat_history(conn, session_id: str, limit: int = 1000):
    rows = conn.execute(text("""
      SELECT role, content, created_at
      FROM ChatMessages
      WHERE session_id = :sid
      ORDER BY id ASC
      LIMIT :lim
    """), {"sid": session_id, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]

def insert_chat(conn, session_id: str, role: str, content: str):
    conn.execute(text("""
      INSERT INTO ChatMessages(session_id, role, content) VALUES (:sid,:role,:content)
    """), {"sid": session_id, "role": role, "content": content})
    conn.commit()

def list_chat_sessions(conn, q: str | None = None, limit: int = 200):
    if q:
        rows = conn.execute(text("""
          SELECT s.session_id,
                 COALESCE(MAX(m.created_at), s.created_at) AS last_time,
                 COUNT(m.id) AS msg_count
          FROM ChatSessions s
          LEFT JOIN ChatMessages m ON m.session_id = s.session_id
          WHERE s.session_id LIKE :q
          GROUP BY s.session_id
          ORDER BY last_time DESC
          LIMIT :lim
        """), {"q": f"%{q}%", "lim": limit}).mappings().all()
    else:
        rows = conn.execute(text("""
          SELECT s.session_id,
                 COALESCE(MAX(m.created_at), s.created_at) AS last_time,
                 COUNT(m.id) AS msg_count
          FROM ChatSessions s
          LEFT JOIN ChatMessages m ON m.session_id = s.session_id
          GROUP BY s.session_id
          ORDER BY last_time DESC
          LIMIT :lim
        """), {"lim": limit}).mappings().all()
    return [dict(r) for r in rows]
def fetch_books_by_category(conn, category: str, limit: int = 10):
    # Lọc đơn giản theo thể loại; MySQL thường đang dùng collation CI nên không phân biệt hoa/thường/dấu
    rows = conn.execute(text("""
      SELECT book_id, title, author, price, stock, category
      FROM Books
      WHERE category LIKE :pat
      ORDER BY stock DESC, book_id DESC
      LIMIT :lim
    """), {"pat": f"%{category}%", "lim": limit}).mappings().all()
    return [dict(r) for r in rows]

def fetch_books_keywords(conn, q: str, limit: int = 10):
    # Fallback khi MATCH() không có: dùng LIKE
    rows = conn.execute(text("""
      SELECT book_id, title, author, price, stock, category, 0.0 AS score
      FROM Books
      WHERE title LIKE :like OR author LIKE :like OR category LIKE :like
      ORDER BY book_id DESC
      LIMIT :lim
    """), {"like": f"%{q}%", "lim": limit}).mappings().all()
    return [dict(r) for r in rows]