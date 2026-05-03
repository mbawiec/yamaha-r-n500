from pathlib import Path
import yaml
from pydantic import BaseModel

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


class Settings(BaseModel):
    yamaha_ip: str = "192.168.11.68"
    api_port: int = 8080
    poll_interval: float = 2.0
    max_volume_limit: int = -200
    stations_path: str = "/home/pi/yamaha-r-n500/config/stations.yml"
    ycast_stations_path: str = "/home/pi/yamaha-r-n500/config/stations_ycast.yml"


def _load() -> Settings:
    if not _CONFIG_PATH.exists():
        return Settings()
    with open(_CONFIG_PATH) as f:
        data = yaml.safe_load(f) or {}
    net = data.get("network", {})
    sys = data.get("system", {})
    radio = data.get("radio", {})
    return Settings(
        yamaha_ip=net.get("yamaha_ip", "192.168.11.68"),
        api_port=net.get("api_port", 8080),
        poll_interval=float(net.get("poll_interval", 2.0)),
        max_volume_limit=int(sys.get("max_volume_limit", -200)),
        stations_path=radio.get("stations_path", "/home/pi/yamaha-r-n500/config/stations.yml"),
        ycast_stations_path=radio.get("ycast_stations_path", "/home/pi/yamaha-r-n500/config/stations_ycast.yml"),
    )


settings = _load()
