# app/index_books.py
from sqlalchemy import text                 # <-- THÊM DÒNG NÀY
from .db import db_conn
from .services.rag import retriever

def main():
    with db_conn() as conn:
        rows = conn.execute(                # <-- DÙNG text(...)
            text("SELECT book_id, title, author, price, stock, category FROM Books")
        ).mappings().all()
        for r in rows:
            retriever.upsert_book(dict(r))
    print(f"Indexed {len(rows)} books into Chroma.")

if __name__ == "__main__":
    main()
