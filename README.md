# BookStore Chatbot (RAG + FastAPI + MySQL + Chroma)

This project is a minimal, production-ready reference implementation of a BookStore AI agent built with FastAPI. It uses a hybrid RAG approach (MySQL FULLTEXT + Chroma vector search) for catalog retrieval, Ollama for embeddings and LLM calls, SQLAlchemy + MySQL for data storage, and Pydantic for schema validation. The bot supports slot-filling order flow with user confirmation and Admin approval, an Admin dashboard for managing books and orders, and WebSocket notifications to push order-status updates to active chat sessions.


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
ollama pull bge-m3
ollama pull qwen2.5:14b-instruct
```

# Step 4: Setup Database
- Run sql scripts in db folder on Mysql, schema.sql for tables and seed.sql for demo data.
- Then replace your username and password database in .env.example.

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