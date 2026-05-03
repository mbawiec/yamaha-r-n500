"""Radio station management: parse stations.yml and navigate Yamaha NET RADIO menu."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import yaml

from app.core.config_loader import settings

log = logging.getLogger(__name__)

_MENU_WAIT_TRIES = 20
_MENU_WAIT_DELAY = 0.35   # seconds between polls — fast with persistent HTTP connection
_POST_SELECT_DELAY = 0.30  # pause after each select() before polling — avoids stale state

# Prevents concurrent navigation tasks from fighting over the Yamaha menu state.
# If a new play request arrives while navigation is in progress, the previous task
# is cancelled and the new one starts fresh.
_nav_lock: asyncio.Lock | None = None
_current_nav_task: asyncio.Task | None = None


def _get_nav_lock() -> asyncio.Lock:
    global _nav_lock
    if _nav_lock is None:
        _nav_lock = asyncio.Lock()
    return _nav_lock


def load_stations() -> list[dict]:
    """
    Parse our rich 3-level stations.yml:
      Category → Station → {Label: URL}   (multiple streams)
      Category → Station: URL             (single stream)

    Returns list of dicts:
      {name, category, streams: [{label, url, ycast_name}], preferred}

    ycast_name is the station entry name in the auto-generated YCast file:
      - single stream  → station name (as-is)
      - multiple streams → "Station Name Label"
    """
    path = Path(settings.stations_path)
    if not path.exists():
        log.warning("stations file not found: %s", path)
        return []

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    stations = []
    for category_name, category_data in raw.items():
        if not isinstance(category_data, dict):
            continue
        for station_name, station_data in category_data.items():
            if isinstance(station_data, str):
                # Single stream — URL directly
                streams = [{"label": "main", "url": station_data, "ycast_name": station_name}]
                preferred = "main"
            elif isinstance(station_data, dict):
                multi = len(station_data) > 1
                streams = [
                    {
                        "label": label,
                        "url": url,
                        "ycast_name": f"{station_name} {label}" if multi else station_name,
                    }
                    for label, url in station_data.items()
                ]
                preferred = streams[0]["label"]
            else:
                continue
            stations.append({
                "name": station_name,
                "category": category_name,
                "streams": streams,
                "preferred": preferred,
            })
    return stations


def generate_ycast_stations() -> None:
    """
    Write a YCast-compatible 2-level stations.yml from our rich config.

    YCast format: Category → Station: URL
    Multi-stream stations become multiple entries: "Station Label: URL"
    Single-stream stations keep their name: "Station: URL"
    """
    out_path = Path(settings.ycast_stations_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    path = Path(settings.stations_path)
    if not path.exists():
        log.warning("stations_path not found, skipping YCast generation: %s", path)
        return

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    ycast: dict[str, dict[str, str]] = {}
    for category_name, category_data in raw.items():
        if not isinstance(category_data, dict):
            continue
        cat: dict[str, str] = {}
        for station_name, station_data in category_data.items():
            if isinstance(station_data, str):
                cat[station_name] = station_data
            elif isinstance(station_data, dict):
                multi = len(station_data) > 1
                for label, url in station_data.items():
                    key = f"{station_name} {label}" if multi else station_name
                    cat[key] = url
        if cat:
            ycast[category_name] = cat

    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(ycast, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    log.info("YCast stations written to %s (%d categories)", out_path, len(ycast))


async def navigate_and_play(yamaha, station_name: str, stream_label: str) -> None:
    """
    Switch to NET RADIO and navigate YCast menu to play the requested stream.
    Uses a lock so concurrent play requests don't fight over the Yamaha menu.
    Retries once on failure.
    """
    global _current_nav_task

    stations = load_stations()
    station = next((s for s in stations if s["name"] == station_name), None)
    if station is None:
        raise RuntimeError(f"Station '{station_name}' not found in config")

    stream = next((s for s in station["streams"] if s["label"] == stream_label), None)
    if stream is None:
        stream = station["streams"][0] if station["streams"] else None
    if stream is None:
        raise RuntimeError(f"No streams available for station '{station_name}'")

    category = station["category"]
    ycast_name = stream["ycast_name"]

    async with _get_nav_lock():
        yamaha.navigating = True
        try:
            last_err: Exception = RuntimeError("Navigation failed")
            for attempt in range(3):
                try:
                    await _do_navigate(yamaha, category, ycast_name)
                    return
                except RuntimeError as e:
                    last_err = e
                    log.warning("Navigation attempt %d failed: %s", attempt + 1, e)
                    await asyncio.sleep(2.5)
            raise last_err
        finally:
            yamaha.navigating = False


async def _do_navigate(yamaha, category: str, ycast_name: str) -> None:
    """Single navigation attempt: Home → My Stations → Category → Station."""
    await yamaha.select_input("NET RADIO")
    await asyncio.sleep(0.5)

    await yamaha.net_radio_home()
    await asyncio.sleep(_POST_SELECT_DELAY)   # let device process home command

    # Layer 1: Home — find "My Stations"
    info = await _wait_for_ready(yamaha, target_layer=1, expected_max_line=1)
    if info["max_line"] == 0:
        raise RuntimeError("NET RADIO menu failed to load (YCast not responding?)")

    my_stations_line = _find_item(info["items"], "My Stations")
    if my_stations_line is None:
        raise RuntimeError("'My Stations' not found in NET RADIO root menu")
    await yamaha.net_radio_select(my_stations_line)
    await asyncio.sleep(_POST_SELECT_DELAY)   # wait before polling new layer

    # Layer 2: Categories — find our category
    info = await _wait_for_ready(yamaha, target_layer=2, expected_max_line=1)
    if info["max_line"] == 0:
        raise RuntimeError("Category list is empty (YCast my_stations misconfigured?)")

    category_line = _find_item(info["items"], category)
    if category_line is None:
        available = [t for _, t, _ in info["items"]]
        raise RuntimeError(f"Category '{category}' not found. Available: {available}")
    await yamaha.net_radio_select(category_line)
    await asyncio.sleep(_POST_SELECT_DELAY)   # wait before polling new layer

    # Layer 3: Stations in category — find ycast_name
    info = await _wait_for_ready(yamaha, target_layer=3, expected_max_line=1)
    if info["max_line"] == 0:
        raise RuntimeError(f"No stations found in category '{category}'")

    station_line = _find_item(info["items"], ycast_name)
    if station_line is None:
        available = [t for _, t, _ in info["items"]]
        raise RuntimeError(f"Station '{ycast_name}' not found in '{category}'. Available: {available}")
    await yamaha.net_radio_select(station_line)

    # Give the player a moment to start
    await asyncio.sleep(0.6)


async def _wait_for_ready(yamaha, target_layer: int, expected_max_line: int) -> dict:
    """Poll NET RADIO list until layer matches, status=Ready, and max_line >= expected."""
    for _ in range(_MENU_WAIT_TRIES):
        info = await yamaha.net_radio_list()
        if (info["status"] == "Ready"
                and info["layer"] == target_layer
                and info["max_line"] >= expected_max_line):
            return info
        await asyncio.sleep(_MENU_WAIT_DELAY)
    return await yamaha.net_radio_list()


def _find_item(items: list, text: str) -> Optional[int]:
    """Find a menu item line number by text (case-insensitive, exact then substring)."""
    text_lower = text.lower()
    for line_num, item_text, _ in items:
        if item_text.lower() == text_lower:
            return line_num
    for line_num, item_text, _ in items:
        if text_lower in item_text.lower():
            return line_num
    return None
