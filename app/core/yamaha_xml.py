import asyncio
import html
import re
import httpx

VOLUME_MIN = -800
VOLUME_MAX = 0
VOLUME_STEP = 5

VALID_INPUTS = [
    "CD", "PHONO", "TUNER",
    "LINE1", "LINE2", "LINE3",
    "OPTICAL1", "OPTICAL2",
    "COAXIAL1", "COAXIAL2",
    "Spotify", "NET RADIO", "SERVER", "AirPlay", "USB",
]

_HEADERS = {"Content-Type": "text/xml"}


class YamahaClient:
    def __init__(self, ip: str):
        self._url = f"http://{ip}/YamahaRemoteControl/ctrl"
        # Raised while NET RADIO navigation is in progress; pollers skip when set.
        self.navigating = False
        # Persistent connection — eliminates TCP handshake overhead on every command.
        # keepalive_expiry keeps the connection warm between polling intervals.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(3.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=30.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _fix_encoding(s: str) -> str:
        """Reverse Latin-1 mojibake: UTF-8 bytes misread as Latin-1 then re-encoded.
        E.g. "Świat" bytes (0xC5 0x9A …) decoded as Latin-1 → "Å\x9awiat";
        re-encoding as Latin-1 and decoding as UTF-8 restores the original."""
        if not s:
            return s
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return s

    async def _send(self, payload: str) -> str:
        r = await self._client.post(self._url, data=payload, headers=_HEADERS)
        r.raise_for_status()
        return r.content.decode("utf-8", errors="replace")

    @staticmethod
    def _check_rc(xml: str, ctx: str = "") -> None:
        m = re.search(r'RC="(\d+)"', xml)
        if m and m.group(1) != "0":
            raise RuntimeError(f"Yamaha RC={m.group(1)}" + (f" ({ctx})" if ctx else ""))

    async def get_status(self) -> dict:
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            "<YAMAHA_AV cmd=\"GET\"><Main_Zone>"
            "<Basic_Status>GetParam</Basic_Status>"
            "</Main_Zone></YAMAHA_AV>"
        )
        xml = await self._send(payload)
        power = re.search(r"<Power>(.*?)</Power>", xml)
        vol   = re.search(r"<Val>(.*?)</Val>", xml)
        inp   = re.search(r"<Input_Sel>(.*?)</Input_Sel>", xml)
        mute  = re.search(r"<Mute>(.*?)</Mute>", xml)
        vol_raw = int(vol.group(1)) if vol else None
        return {
            "power":      power.group(1) if power else "N/A",
            "volume_raw": vol_raw,
            "volume_db":  f"{vol_raw / 10:.1f}" if vol_raw is not None else "N/A",
            "input":      inp.group(1) if inp else "N/A",
            "mute":       mute.group(1) if mute else "N/A",
        }

    async def power(self, state: str) -> None:
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<YAMAHA_AV cmd="PUT"><Main_Zone>'
            f'<Power_Control><Power>{state}</Power></Power_Control>'
            f'</Main_Zone></YAMAHA_AV>'
        )
        self._check_rc(await self._send(payload), "power")

    async def volume_set(self, val: int) -> None:
        val = max(VOLUME_MIN, min(VOLUME_MAX, val))
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<YAMAHA_AV cmd="PUT"><Main_Zone><Volume><Lvl>'
            f'<Val>{val}</Val><Exp>1</Exp><Unit>dB</Unit>'
            '</Lvl></Volume></Main_Zone></YAMAHA_AV>'
        )
        self._check_rc(await self._send(payload), "volume")

    async def volume_up(self, step: int = VOLUME_STEP) -> int:
        status = await self.get_status()
        current = status["volume_raw"] or 0
        new_val = min(VOLUME_MAX, current + step)
        await self.volume_set(new_val)
        return new_val

    async def volume_down(self, step: int = VOLUME_STEP) -> int:
        status = await self.get_status()
        current = status["volume_raw"] or 0
        new_val = max(VOLUME_MIN, current - step)
        await self.volume_set(new_val)
        return new_val

    async def volume_up_native(self) -> None:
        """Single-request volume increment using Yamaha's native Up command."""
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<YAMAHA_AV cmd="PUT"><Main_Zone><Volume><Lvl>'
            '<Val>Up</Val><Exp></Exp><Unit></Unit>'
            '</Lvl></Volume></Main_Zone></YAMAHA_AV>'
        )
        self._check_rc(await self._send(payload), "volume up")

    async def volume_down_native(self) -> None:
        """Single-request volume decrement using Yamaha's native Down command."""
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<YAMAHA_AV cmd="PUT"><Main_Zone><Volume><Lvl>'
            '<Val>Down</Val><Exp></Exp><Unit></Unit>'
            '</Lvl></Volume></Main_Zone></YAMAHA_AV>'
        )
        self._check_rc(await self._send(payload), "volume down")

    async def mute(self, state: str) -> None:
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<YAMAHA_AV cmd="PUT"><Main_Zone><Volume>'
            f'<Mute>{state}</Mute>'
            f'</Volume></Main_Zone></YAMAHA_AV>'
        )
        self._check_rc(await self._send(payload), "mute")

    async def select_input(self, source: str) -> None:
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<YAMAHA_AV cmd="PUT"><Main_Zone><Input>'
            f'<Input_Sel>{source}</Input_Sel>'
            f'</Input></Main_Zone></YAMAHA_AV>'
        )
        self._check_rc(await self._send(payload), f"input {source}")

    async def net_radio_home(self) -> None:
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<YAMAHA_AV cmd="PUT"><NET_RADIO>'
            '<List_Control><Cursor>Return to Home</Cursor></List_Control>'
            '</NET_RADIO></YAMAHA_AV>'
        )
        await self._send(payload)

    async def net_radio_list(self) -> dict:
        """Return current NET RADIO menu state."""
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<YAMAHA_AV cmd="GET"><NET_RADIO>'
            '<List_Info>GetParam</List_Info>'
            '</NET_RADIO></YAMAHA_AV>'
        )
        xml = await self._send(payload)
        status = re.search(r'<Menu_Status>(.*?)</Menu_Status>', xml)
        layer  = re.search(r'<Menu_Layer>(\d+)</Menu_Layer>', xml)
        max_ln = re.search(r'<Max_Line>(\d+)</Max_Line>', xml)
        items  = re.findall(
            r'<Line_(\d+)><Txt>(.*?)</Txt><Attribute>(.*?)</Attribute>', xml
        )

        def _di(t: str) -> str:
            t = html.unescape(t)
            return YamahaClient._fix_encoding(t)

        return {
            "status":   status.group(1) if status else "Unknown",
            "layer":    int(layer.group(1))  if layer  else 0,
            "max_line": int(max_ln.group(1)) if max_ln else 0,
            "items":    [(int(n), _di(txt), attr) for n, txt, attr in items if txt],
        }

    async def net_radio_select(self, line: int) -> None:
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<YAMAHA_AV cmd="PUT"><NET_RADIO>'
            f'<List_Control><Direct_Sel>Line_{line}</Direct_Sel></List_Control>'
            '</NET_RADIO></YAMAHA_AV>'
        )
        await self._send(payload)

    async def net_radio_play_info(self) -> dict:
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<YAMAHA_AV cmd="GET"><NET_RADIO>'
            '<Play_Info>GetParam</Play_Info>'
            '</NET_RADIO></YAMAHA_AV>'
        )
        xml = await self._send(payload)
        pb      = re.search(r'<Playback_Info>(.*?)</Playback_Info>', xml)
        station = re.search(r'<Station>(.*?)</Station>', xml)
        song    = re.search(r'<Song>(.*?)</Song>', xml)

        def _decode(s: str) -> str:
            """Iterative HTML entity decode + mojibake fix."""
            if not s:
                return s
            prev = None
            while prev != s:
                prev = s
                s = html.unescape(s)
            return YamahaClient._fix_encoding(s)

        return {
            "playback": pb.group(1)               if pb      else "Stop",
            "station":  _decode(station.group(1)) if station else "",
            "song":     _decode(song.group(1))    if song    else "",
        }

    async def net_radio_stop(self) -> None:
        payload = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<YAMAHA_AV cmd="PUT"><NET_RADIO>'
            '<Play_Control><Playback>Stop</Playback></Play_Control>'
            '</NET_RADIO></YAMAHA_AV>'
        )
        await self._send(payload)
