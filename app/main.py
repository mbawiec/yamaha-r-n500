from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config_loader import settings
from app.core.yamaha_xml import YamahaClient
from app.core import radio_manager

logger = logging.getLogger("uvicorn.error")
BASE = Path(__file__).parent


class _ConnectionManager:
    def __init__(self):
        self._clients: list[WebSocket] = []

    @property
    def count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self._clients.remove(ws)
        except ValueError:
            pass

    async def broadcast(self, data: dict) -> None:
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_manager = _ConnectionManager()


async def _poller(app: FastAPI) -> None:
    while True:
        interval = settings.poll_interval if _manager.count > 0 else 30.0
        await asyncio.sleep(interval)
        if _manager.count == 0:
            continue
        if app.state.yamaha.navigating:
            continue
        try:
            status = await app.state.yamaha.get_status()
            await _manager.broadcast({"type": "status", "data": status})
        except Exception as e:
            logger.debug("Poller error: %s", e)


async def _radio_poller(app: FastAPI) -> None:
    """Periodically push NET RADIO now-playing info to WebSocket clients."""
    while True:
        await asyncio.sleep(5.0)
        if _manager.count == 0:
            continue
        if app.state.yamaha.navigating:
            continue
        try:
            status = await app.state.yamaha.get_status()
            if status.get("input") == "NET RADIO":
                play_info = await app.state.yamaha.net_radio_play_info()
                if play_info.get("playback") == "Play":
                    await _manager.broadcast({"type": "radio_info", "data": play_info})
        except Exception as e:
            logger.debug("Radio poller error: %s", e)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        radio_manager.generate_ycast_stations()
    except Exception as e:
        logger.warning("YCast generation failed: %s", e)
    app.state.yamaha = YamahaClient(settings.yamaha_ip)
    task1 = asyncio.create_task(_poller(app))
    task2 = asyncio.create_task(_radio_poller(app))
    yield
    task1.cancel()
    task2.cancel()
    for t in (task1, task2):
        try:
            await t
        except asyncio.CancelledError:
            pass
    await app.state.yamaha.close()


app = FastAPI(title="Yamaha R-N500", lifespan=_lifespan)

templates = Jinja2Templates(directory=str(BASE / "web" / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "web" / "static")), name="static")

from app.api.routes import router  # noqa: E402
app.include_router(router)


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await _manager.connect(websocket)
    try:
        try:
            status = await websocket.app.state.yamaha.get_status()
            await websocket.send_json({"type": "status", "data": status})
        except Exception as e:
            logger.debug("Initial WS status error: %s", e)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _manager.disconnect(websocket)
