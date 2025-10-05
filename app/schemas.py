from pydantic import BaseModel, field_validator
from typing import Optional, Literal

class ChatIn(BaseModel):
    session_id: Optional[str] = None
    message: str

class ChatOut(BaseModel):
    session_id: str
    reply: str
    state: Literal['catalog','order_collect','await_confirm','await_admin_decision','done']
    data: dict | None = None

class BookOut(BaseModel):
    book_id: int
    title: str
    author: str
    price: int
    stock: int
    category: str
    score: float | None = None

class OrderCreate(BaseModel):
    customer_name: str
    phone: str
    address: str
    book_id: int
    quantity: int

    @field_validator('quantity')
    def qty_pos(cls, v):
        if v < 1:
            raise ValueError('quantity must be >= 1')
        return v

class AdminLogin(BaseModel):
    username: str
    password: str
