from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    storage_root: Path
    legacy_root: Path | None
    service_seed_root: Path
    token: str | None
    timezone: str
    latitude: float
    longitude: float
    elevation: int
    unit_system: str
    location_name: str
    version: str


def default_legacy_root(project_root: Path) -> Path:
    return project_root / "fake_homeassitant_try" / "copied_data"


def get_settings() -> Settings:
    package_root = Path(__file__).resolve().parent
    project_root = package_root.parent.parent
    storage_root = Path(
        os.getenv("FAKE_HA_STORAGE_ROOT", str(project_root / ".fake_homeassistant"))
    ).resolve()

    legacy_env = os.getenv("FAKE_HA_LEGACY_ROOT")
    legacy_root = Path(legacy_env).resolve() if legacy_env else default_legacy_root(project_root)
    if not legacy_root.exists():
        legacy_root = None

    return Settings(
        storage_root=storage_root,
        legacy_root=legacy_root,
        service_seed_root=package_root / "data" / "services",
        token=os.getenv("FAKE_HA_TOKEN"),
        timezone=os.getenv("FAKE_HA_TIMEZONE", "Asia/Shanghai"),
        latitude=float(os.getenv("FAKE_HA_LATITUDE", "31.2304")),
        longitude=float(os.getenv("FAKE_HA_LONGITUDE", "121.4737")),
        elevation=int(os.getenv("FAKE_HA_ELEVATION", "4")),
        unit_system=os.getenv("FAKE_HA_UNIT_SYSTEM", "metric"),
        location_name=os.getenv("FAKE_HA_LOCATION_NAME", "Fake Home"),
        version=os.getenv("FAKE_HA_VERSION", "2026.4.0-fake"),
    )
