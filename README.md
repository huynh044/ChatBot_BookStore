# BookStore Chatbot (RAG + FastAPI + MySQL + Chroma)

This is a minimal, production-minded reference implementation for a BookStore chatbot with:
- RAG-based catalog search (Hybrid: MySQL FULLTEXT + Vector via Chroma)
- Order flow with slot-filling (state machine), confirmation, and Admin approval
- Admin dashboard for Books (CRUD) + Orders (Pending/Approved/Cancelled)
- WebSocket notifications to the chat when Admin approves/cancels


## The steps which setup the project
# Step 1: Clone project
```bash
git clone https://github.com/huynh044/ChatBot_BookStore.git
```

# Step 2: Download ollama
```bash
https://ollama.com/download
```

# Step 3: Pull language model and embedding model
```bash
ollama pull nomic-embed-text
ollama pull llama3.1:8b
```

# Step 4: Setup Database
--- Run sql scripts in db folder on Mysql, schema.sql for tables and seed.sql for demo data.
--- Then replace your username and password database in .env.example.

# Step 5:
Run this command to create data index:
```bash
python -m app.index_books
```

# Step 6: Run project
```bash
uvicorn app.main:app --reload
```

## Some images about archiving result in resuls folder