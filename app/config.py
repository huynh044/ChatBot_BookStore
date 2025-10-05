from pydantic import BaseModel
from dotenv import load_dotenv
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")  # nạp .env ở thư mục gốc

class Settings(BaseModel):
    # DB (dùng đúng key bạn đang dùng)
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_user: str = os.getenv("DB_USER", "root")
    db_pass: str = os.getenv("DB_PASS", "123456")
    db_name: str = os.getenv("DB_NAME", "bookstore")

    # LLM & Embedding (Ollama)
    llm_model: str   = os.getenv("LLM_MODEL", "llama3.1:8b")
    embed_model: str = os.getenv("EMBED_MODEL", "nomic-embed-text")
    ollama_url: str  = os.getenv("OLLAMA_URL", "http://localhost:11434")

    # Vector store path
    chroma_dir: str = os.getenv("CHROMA_DIR", ".chroma")

    # App misc
    secret_key: str = os.getenv("SECRET_KEY", "change-me")
    admin_user: str = os.getenv("ADMIN_USER", "admin")
    admin_pass: str = os.getenv("ADMIN_PASS", "admin123")

settings = Settings()
