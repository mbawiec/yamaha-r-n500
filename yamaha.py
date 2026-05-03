#!python3

"""
yamaha.py — CLI do sterowania amplitunerem Yamaha R-N500 przez sieć (XML API).

Użycie: python yamaha.py <komenda> [opcje]
        python yamaha.py --help

Konfiguracja: config.yaml w tym samym katalogu (lub zmienne domyślne).
"""

import asyncio
import html
import re
import argparse
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
_CFG_PATH = Path(__file__).parent / "config.yaml"
_YAMAHA_IP = "192.168.11.68"
_APP_URL    = "http://192.168.11.2:8080"   # adres aplikacji webowej w sieci lokalnej

try:
    import yaml as _yaml
    if _CFG_PATH.exists():
        with open(_CFG_PATH) as _f:
            _cfg = _yaml.safe_load(_f) or {}
            _net          = _cfg.get("network", {})
            _YAMAHA_IP    = _net.get("yamaha_ip",  _YAMAHA_IP)
            _port         = _net.get("api_port",   8080)
            _host         = _net.get("app_host",   None)
            if _host:
                _APP_URL = f"http://{_host}:{_port}"
            else:
                # derive from yamaha_ip subnet – same host as RPi (usually .2)
                _octets = _YAMAHA_IP.rsplit(".", 1)
                _rpi_ip = _net.get("rpi_ip", _octets[0] + ".2" if len(_octets) == 2 else "192.168.11.2")
                _APP_URL = f"http://{_rpi_ip}:{_port}"
except ImportError:
    pass  # yaml not installed — use defaults

URL = f"http://{_YAMAHA_IP}/YamahaRemoteControl/ctrl"
HEADERS = {"Content-Type": "text/xml"}

VOLUME_MIN  = -800   # -80.0 dB
VOLUME_MAX  =  0     #   0.0 dB
VOLUME_STEP =  5     #   0.5 dB per step

VALID_INPUTS = [
    "CD", "PHONO", "TUNER",
    "LINE1", "LINE2", "LINE3",
    "OPTICAL1", "OPTICAL2",
    "COAXIAL1", "COAXIAL2",
    "Spotify", "NET RADIO", "SERVER", "AirPlay", "USB",
]

_MENU_WAIT_TRIES = 10
_MENU_WAIT_DELAY = 0.8


# ── HTTP helpers ──────────────────────────────────────────────────────────────
async def send(payload):
    import httpx
    async with httpx.AsyncClient() as client:
        r = await client.post(URL, data=payload, headers=HEADERS, timeout=3.0)
        r.raise_for_status()
        return r.content.decode("utf-8", errors="replace")


def _check_rc(xml, context=""):
    m = re.search(r'RC="(\d+)"', xml)
    if m and m.group(1) != "0":
        suffix = f" ({context})" if context else ""
        raise RuntimeError(f"Yamaha zwróciło błąd RC={m.group(1)}{suffix}")


def _fix_encoding(s: str) -> str:
    """Reverse Latin-1 mojibake: re-encode as Latin-1, decode as UTF-8."""
    if not s:
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s


def _decode(s: str) -> str:
    """Iterative HTML entity decode + mojibake fix."""
    if not s:
        return s
    prev = None
    while prev != s:
        prev = s
        s = html.unescape(s)
    return _fix_encoding(s)


# ── App API helpers (przez sieć, nie lokalnie) ────────────────────────────────
async def app_get(path: str) -> dict:
    """GET {_APP_URL}{path} → parsed JSON data."""
    import httpx
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_APP_URL}{path}", timeout=10.0)
        r.raise_for_status()
        j = r.json()
        return j.get("data", j)


async def app_post(path: str, body: dict) -> dict:
    """POST {_APP_URL}{path} JSON → parsed JSON data."""
    import httpx
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{_APP_URL}{path}", json=body, timeout=60.0)
        r.raise_for_status()
        j = r.json()
        return j.get("data", j)


# ── Status ────────────────────────────────────────────────────────────────────
async def get_status():
    payload = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<YAMAHA_AV cmd=\"GET\"><Main_Zone>"
        "<Basic_Status>GetParam</Basic_Status>"
        "</Main_Zone></YAMAHA_AV>"
    )
    xml = await send(payload)
    power = re.search(r'<Power>(.*?)</Power>', xml)
    vol   = re.search(r'<Val>(.*?)</Val>',    xml)
    inp   = re.search(r'<Input_Sel>(.*?)</Input_Sel>', xml)
    mute  = re.search(r'<Mute>(.*?)</Mute>',  xml)
    vol_raw = int(vol.group(1)) if vol else None
    return {
        "power":      power.group(1) if power else "N/A",
        "volume_raw": vol_raw,
        "volume_db":  f"{vol_raw / 10:.1f}" if vol_raw is not None else "N/A",
        "input":      inp.group(1)  if inp   else "N/A",
        "mute":       mute.group(1) if mute  else "N/A",
    }


async def cmd_status():
    s = await get_status()
    print(f"\n{'─' * 24}")
    print(f"  Yamaha R-N500 — status")
    print(f"{'─' * 24}")
    print(f"  Zasilanie : {s['power']}")
    print(f"  Głośność  : {s['volume_db']} dB  (raw {s['volume_raw']})")
    print(f"  Wejście   : {s['input']}")
    print(f"  Wyciszenie: {s['mute']}")
    print(f"{'─' * 24}\n")


# ── Power ─────────────────────────────────────────────────────────────────────
async def power_cmd(state):
    xml = await send(
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<YAMAHA_AV cmd="PUT"><Main_Zone>'
        f'<Power_Control><Power>{state}</Power></Power_Control>'
        f'</Main_Zone></YAMAHA_AV>'
    )
    _check_rc(xml, "power")
    print(f"✔ Power → {state}")


# ── Volume ────────────────────────────────────────────────────────────────────
async def _get_volume_raw() -> int:
    s = await get_status()
    if s["volume_raw"] is None:
        raise RuntimeError("Nie można odczytać aktualnej głośności")
    return s["volume_raw"]


async def volume_set(val: int):
    val = max(VOLUME_MIN, min(VOLUME_MAX, int(val)))
    xml = await send(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<YAMAHA_AV cmd="PUT"><Main_Zone><Volume><Lvl>'
        f'<Val>{val}</Val><Exp>1</Exp><Unit>dB</Unit>'
        '</Lvl></Volume></Main_Zone></YAMAHA_AV>'
    )
    _check_rc(xml, "volume set")
    print(f"✔ Volume → {val / 10:.1f} dB")


async def volume_up(step: int = VOLUME_STEP):
    await volume_set(await _get_volume_raw() + step)


async def volume_down(step: int = VOLUME_STEP):
    await volume_set(await _get_volume_raw() - step)


# ── Mute ──────────────────────────────────────────────────────────────────────
async def mute_cmd(state):
    xml = await send(
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<YAMAHA_AV cmd="PUT"><Main_Zone><Volume>'
        f'<Mute>{state}</Mute>'
        f'</Volume></Main_Zone></YAMAHA_AV>'
    )
    _check_rc(xml, "mute")
    print(f"✔ Mute → {state}")


# ── Input ─────────────────────────────────────────────────────────────────────
async def select_input(source):
    if source not in VALID_INPUTS:
        print(f"❌ Nieznane wejście: {source}")
        print(f"   Dostępne: {', '.join(VALID_INPUTS)}")
        sys.exit(1)
    xml = await send(
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<YAMAHA_AV cmd="PUT"><Main_Zone><Input>'
        f'<Input_Sel>{source}</Input_Sel>'
        f'</Input></Main_Zone></YAMAHA_AV>'
    )
    _check_rc(xml, f"input {source}")
    print(f"✔ Input → {source}")


# ── Speakers A/B ──────────────────────────────────────────────────────────────
async def select_speaker(speaker, state):
    tag = f"Sp_{speaker}"
    xml = await send(
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<YAMAHA_AV cmd="PUT"><Main_Zone><Volume>'
        f'<{tag}>{state}</{tag}>'
        f'</Volume></Main_Zone></YAMAHA_AV>'
    )
    _check_rc(xml, f"speaker {speaker}")
    print(f"✔ Speaker {speaker} → {state}")


# ── NET RADIO play info (bezpośrednio z urządzenia) ───────────────────────────
async def net_radio_play_info() -> dict:
    xml = await send(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<YAMAHA_AV cmd="GET"><NET_RADIO>'
        '<Play_Info>GetParam</Play_Info>'
        '</NET_RADIO></YAMAHA_AV>'
    )
    pb      = re.search(r'<Playback_Info>(.*?)</Playback_Info>', xml)
    station = re.search(r'<Station>(.*?)</Station>', xml)
    song    = re.search(r'<Song>(.*?)</Song>', xml)
    return {
        "playback": pb.group(1)                if pb      else "Stop",
        "station":  _decode(station.group(1))  if station else "",
        "song":     _decode(song.group(1))     if song    else "",
    }


async def net_radio_stop():
    await send(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<YAMAHA_AV cmd="PUT"><NET_RADIO>'
        '<Play_Control><Playback>Stop</Playback></Play_Control>'
        '</NET_RADIO></YAMAHA_AV>'
    )


# ── Radio via app API ─────────────────────────────────────────────────────────
async def radio_list_api(filter_str: str | None = None) -> None:
    """Pobiera listę stacji z aplikacji webowej."""
    stations = await app_get("/api/radio")
    if not stations:
        print("Brak stacji.")
        return

    # group by category
    cats: dict[str, list] = {}
    for st in stations:
        c = st.get("category", "—")
        cats.setdefault(c, []).append(st)

    for cat, sts in cats.items():
        filtered = [s for s in sts
                    if not filter_str or filter_str.lower() in s["name"].lower()]
        if not filtered:
            continue
        print(f"\n  [{cat}]")
        for st in filtered:
            streams = st.get("streams", [])
            if len(streams) == 1 and streams[0]["label"] == "main":
                print(f"    📻 {st['name']}")
            else:
                print(f"    📻 {st['name']}")
                for s in streams:
                    marker = " ★" if s["label"] == st.get("preferred") else ""
                    print(f"         {s['label']}: {s['url']}{marker}")
    print()


async def radio_play_api(station: str, stream: str | None = None,
                         url: str | None = None) -> None:
    """Uruchamia stację przez app API (nawigacja i playback po stronie serwera)."""
    body: dict = {}
    if url:
        body["url"] = url
        body["station"] = station
    else:
        body["station"] = station
        if stream:
            body["stream"] = stream

    print(f"▶ Uruchamianie: {station}" +
          (f" [{stream}]" if stream else "") +
          (f" → {url}" if url else "") + "…")
    result = await app_post("/api/radio/play", body)
    print(f"✔ Zlecono. Odpowiedź: {result}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        prog="yamaha.py",
        description="🎛 Sterowanie amplitunerem Yamaha R-N500 przez sieć (HTTP XML API)",
        epilog=f"""
Przykłady:
  python yamaha.py status
  python yamaha.py power On
  python yamaha.py volume set -400
  python yamaha.py volume up --step 10
  python yamaha.py mute On
  python yamaha.py input Spotify
  python yamaha.py radio list
  python yamaha.py radio list --filter bbc
  python yamaha.py radio play "Radio 357"
  python yamaha.py radio play "Radio Nowy Świat" --stream AAC
  python yamaha.py radio play "Moja stacja" --url https://stream.example.com/live.mp3
  python yamaha.py radio info
  python yamaha.py radio stop

Yamaha:   {_YAMAHA_IP}
App API:  {_APP_URL}
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    sub = parser.add_subparsers(dest="cmd", help="Komenda")

    sub.add_parser("status", help="Pokaż aktualny stan urządzenia")

    pwr = sub.add_parser("power", help="Włącz / wyłącz")
    pwr.add_argument("state", choices=["On", "Standby"])

    vol = sub.add_parser("volume", help="Sterowanie głośnością")
    vol.add_argument("action", choices=["set", "up", "down"])
    vol.add_argument("value",  nargs="?", type=int, help="wartość dla 'set' (np. -400 = -40.0 dB)")
    vol.add_argument("--step", type=int, default=VOLUME_STEP,
                     help=f"krok dla up/down (domyślnie {VOLUME_STEP})")

    mute_p = sub.add_parser("mute", help="Wycisz / odcisz")
    mute_p.add_argument("state", choices=["On", "Off"])

    inp = sub.add_parser("input", help="Zmień wejście sygnałowe")
    inp.add_argument("source", help=f"Dostępne: {', '.join(VALID_INPUTS)}")

    spk = sub.add_parser("speaker", help="Steruj głośnikami A/B")
    spk.add_argument("speaker", choices=["A", "B"])
    spk.add_argument("state",   choices=["On", "Off"])

    rad = sub.add_parser("radio", help="Sterowanie radiem internetowym (NET RADIO / YCast)")
    rad_sub = rad.add_subparsers(dest="radio_cmd")

    lst = rad_sub.add_parser("list",  help="Pokaż dostępne stacje (przez app API)")
    lst.add_argument("--filter", "-f", default=None, metavar="TEKST",
                     help="Filtruj stacje po nazwie")

    rad_sub.add_parser("stop",  help="Zatrzymaj odtwarzanie radia")
    rad_sub.add_parser("info",  help="Pokaż aktualnie grającą stację i utwór")

    rad_play = rad_sub.add_parser("play", help="Odtwórz stację radiową")
    rad_play.add_argument("station", help="Nazwa stacji (np. 'Radio 357') lub dowolna nazwa gdy podano --url")
    rad_play.add_argument("--stream", default=None, help="Etykieta jakości (np. AAC, MP3)")
    rad_play.add_argument("--url", default=None, metavar="URL",
                          help="Bezpośredni URL strumienia (podaj razem z nazwą stacji)")

    args = parser.parse_args()

    async def runner():
        import httpx
        try:
            if args.cmd == "status":
                await cmd_status()

            elif args.cmd == "power":
                await power_cmd(args.state)

            elif args.cmd == "volume":
                if args.action == "set":
                    if args.value is None:
                        print("❌ Podaj wartość: volume set -400")
                        sys.exit(1)
                    await volume_set(args.value)
                elif args.action == "up":
                    await volume_up(args.step)
                elif args.action == "down":
                    await volume_down(args.step)

            elif args.cmd == "mute":
                await mute_cmd(args.state)

            elif args.cmd == "input":
                await select_input(args.source)

            elif args.cmd == "speaker":
                await select_speaker(args.speaker, args.state)

            elif args.cmd == "radio":
                if args.radio_cmd == "list":
                    await radio_list_api(getattr(args, "filter", None))

                elif args.radio_cmd == "play":
                    await radio_play_api(args.station, args.stream, args.url)

                elif args.radio_cmd == "stop":
                    await net_radio_stop()
                    print("✔ Radio zatrzymane")

                elif args.radio_cmd == "info":
                    pi = await net_radio_play_info()
                    print(f"\n  Status    : {pi['playback']}")
                    print(f"  Stacja    : {pi['station'] or '—'}")
                    print(f"  Utwór     : {pi['song'] or '—'}\n")

                else:
                    rad.print_help()

            else:
                parser.print_help()

        except httpx.ConnectError as e:
            if _APP_URL in str(e):
                print(f"❌ Brak połączenia z app API ({_APP_URL})")
            else:
                print(f"❌ Brak połączenia z Yamaha ({_YAMAHA_IP})")
            sys.exit(1)
        except httpx.HTTPStatusError as e:
            print(f"❌ HTTP {e.response.status_code}: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Błąd: {e}")
            sys.exit(1)

    asyncio.run(runner())


if __name__ == "__main__":
    main()

