from __future__ import annotations
from typing import Dict, Any

# In-memory state store per session (you can replace with DB if needed)
SESSIONS: dict[str, dict[str, Any]] = {}

def get_session(session_id: str) -> dict[str, Any]:
    st = SESSIONS.get(session_id)
    if not st:
        st = {
            'state': 'catalog',
            'slots': {
                'book_id': None, 'quantity': None,
                'customer_name': None, 'phone': None, 'address': None
            },
            'last_prompt': None
        }
        SESSIONS[session_id] = st
    return st
def reset_session(session_id: str):
    if session_id in SESSIONS:
        del SESSIONS[session_id]