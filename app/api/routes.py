from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.yamaha_xml import VALID_INPUTS
from app.core import radio_manager

router = APIRouter(prefix="/api")


def _yamaha(request: Request):
    return request.app.state.yamaha


@router.get("/status")
async def get_status(request: Request):
    try:
        return {"ok": True, "data": await _yamaha(request).get_status()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/power/{state}")
async def power(request: Request, state: str):
    y = _yamaha(request)
    await y.power(state)
    return {"ok": True, "data": await y.get_status()}


@router.post("/volume/up")
async def volume_up(request: Request):
    y = _yamaha(request)
    await y.volume_up_native()
    status = await y.get_status()
    return {"ok": True, "data": {"volume_raw": status["volume_raw"], "volume_db": status["volume_db"]}}


@router.post("/volume/down")
async def volume_down(request: Request):
    y = _yamaha(request)
    await y.volume_down_native()
    status = await y.get_status()
    return {"ok": True, "data": {"volume_raw": status["volume_raw"], "volume_db": status["volume_db"]}}


@router.post("/volume/set/{val}")
async def volume_set(request: Request, val: int):
    y = _yamaha(request)
    await y.volume_set(val)
    return {"ok": True, "data": {"volume_raw": val, "volume_db": f"{val / 10:.1f}"}}


@router.post("/mute/{state}")
async def mute(request: Request, state: str):
    y = _yamaha(request)
    await y.mute(state)
    return {"ok": True, "data": await y.get_status()}


@router.post("/input/{source:path}")
async def select_input(request: Request, source: str):
    if source not in VALID_INPUTS:
        return {"ok": False, "error": f"Nieznane wejście: {source}"}
    y = _yamaha(request)
    await y.select_input(source)
    return {"ok": True, "data": await y.get_status()}


@router.post("/speaker/{ab}/{state}")
async def speaker(ab: str, state: str):
    # Placeholder — Speaker A/B control via XML API not supported on R-N500
    return {"ok": False, "error": "Speaker A/B przez XML API niedostępne na R-N500"}


# ── Radio ─────────────────────────────────────────────────────────────────────

class PlayRequest(BaseModel):
    station: str
    stream: str | None = None
    url: str | None = None


@router.get("/radio")
async def get_radio_stations():
    stations = radio_manager.load_stations()
    return {"ok": True, "data": stations}


@router.post("/radio/play")
async def radio_play(request: Request, body: PlayRequest):
    y = _yamaha(request)
    try:
        await radio_manager.navigate_and_play(y, body.station, body.stream)
        play_info = await y.net_radio_play_info()
        return {"ok": True, "data": play_info}
    except asyncio.CancelledError:
        return {"ok": False, "error": "Navigation cancelled by newer request"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/radio/stop")
async def radio_stop(request: Request):
    y = _yamaha(request)
    try:
        await y.net_radio_stop()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
