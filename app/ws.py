from typing import Dict, Set
from starlette.websockets import WebSocket

class Hub:
    def __init__(self):
        self.user_channels: Dict[str, Set[WebSocket]] = {}
        self.admin_channels: Set[WebSocket] = set()

    async def connect_user(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self.user_channels.setdefault(session_id, set()).add(ws)

    async def disconnect_user(self, session_id: str, ws: WebSocket):
        s = self.user_channels.get(session_id)
        if s and ws in s:
            s.remove(ws)

    async def connect_admin(self, ws: WebSocket):
        await ws.accept()
        self.admin_channels.add(ws)

    async def disconnect_admin(self, ws: WebSocket):
        if ws in self.admin_channels:
            self.admin_channels.remove(ws)

    async def send_to_user(self, session_id: str, message: dict):
        for ws in list(self.user_channels.get(session_id, [])):
            try:
                await ws.send_json(message)
            except:
                pass

    async def broadcast_admin(self, message: dict):
        for ws in list(self.admin_channels):
            try:
                await ws.send_json(message)
            except:
                pass

hub = Hub()
