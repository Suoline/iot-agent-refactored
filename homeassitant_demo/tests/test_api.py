from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from fake_homeassistant_v2.app import create_app
from fake_homeassistant_v2.config import Settings
from fake_homeassistant_v2.models import ServiceDefinition
from fake_homeassistant_v2.runtime import StorageManager


REPO_ROOT = Path(__file__).resolve().parent.parent


def make_settings(tmp_path: Path, *, legacy: bool = True) -> Settings:
    return Settings(
        storage_root=tmp_path / "runtime",
        legacy_root=REPO_ROOT / "fake_homeassitant_try" / "copied_data" if legacy else None,
        service_seed_root=REPO_ROOT / "src" / "fake_homeassistant_v2" / "data" / "services",
        token=None,
        timezone="Asia/Shanghai",
        latitude=31.2304,
        longitude=121.4737,
        elevation=4,
        unit_system="metric",
        location_name="Test Home",
        version="test-version",
    )


def test_rest_contracts_and_legacy_import(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    assert client.get("/api/").status_code == 200
    assert client.get("/api/config").json()["time_zone"] == "Asia/Shanghai"

    services = client.get("/api/services").json()
    assert any(item["domain"] == "light" for item in services)

    states = client.get("/api/states").json()
    assert len(states) >= 71
    assert any(item["entity_id"] == "light.philips_cn_1061200910_lite_s_2" for item in states)

    events = client.get("/api/events").json()
    assert any(item["event"] == "call_service" for item in events)


def test_post_state_returns_201_then_200(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path, legacy=False)))

    first = client.post("/api/states/sensor.demo_virtual", json={"state": "on", "attributes": {"unit_of_measurement": "x"}})
    second = client.post("/api/states/sensor.demo_virtual", json={"state": "off"})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["state"] == "off"


def test_service_call_and_return_response(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    result = client.post(
        "/api/services/light/turn_on",
        json={"entity_id": "light.philips_cn_1061200910_lite_s_2", "brightness": 50},
    )
    assert result.status_code == 200
    assert result.json()[0]["attributes"]["brightness"] == 50

    track = client.post(
        "/api/services/media_player/media_next_track?return_response=true",
        json={"entity_id": "media_player.xiaomi_cn_701074704_l15a"},
    )
    assert track.status_code == 200
    assert track.json()["service_response"]["track_action"] == "next"


def test_declarative_device_extension(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path, legacy=False)))

    response = client.put(
        "/api/mock/devices/device.demo_lamp",
        json={
            "device": {
                "device_id": "device.demo_lamp",
                "name": "Demo Lamp",
                "manufacturer": "Demo",
                "model": "Lamp",
                "entities": [],
            },
            "entities": [
                {
                    "entity_id": "switch.demo_lamp_power",
                    "domain": "switch",
                    "object_id": "demo_lamp_power",
                    "device_id": "device.demo_lamp",
                    "state": "off",
                    "attributes": {"friendly_name": "Demo Lamp Power"},
                }
            ],
        },
    )
    assert response.status_code == 200

    service = client.post("/api/services/switch/turn_on", json={"entity_id": "switch.demo_lamp_power"})
    assert service.status_code == 200
    assert service.json()[0]["state"] == "on"


def test_custom_handler_extension(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, legacy=False)
    app = create_app(settings=settings)
    client = TestClient(app)
    storage = StorageManager(settings.storage_root)
    storage.write_service(
        ServiceDefinition(
            domain="fan",
            service="set_percentage",
            name="Set percentage",
            description="Set a fan percentage.",
            fields={"entity_id": {"required": True}, "percentage": {"required": True}},
            target={"entity": [{}]},
            handler="tests.custom_handlers:fan_set_percentage",
            supports_response=True,
        )
    )

    client.put(
        "/api/mock/entities/fan.demo",
        json={
            "entity": {
                "entity_id": "fan.demo",
                "domain": "fan",
                "object_id": "demo",
                "state": "off",
                "attributes": {"percentage": 0},
            }
        },
    )
    client.post("/api/mock/reload")

    result = client.post(
        "/api/services/fan/set_percentage?return_response=true",
        json={"entity_id": "fan.demo", "percentage": 75},
    )
    assert result.status_code == 200
    assert result.json()["service_response"]["percentage"] == 75
    assert client.get("/api/states/fan.demo").json()["attributes"]["percentage"] == 75


def test_button_actions_and_media_regression(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    toggle = client.post(
        "/api/services/button/press",
        json={"entity_id": "button.philips_cn_1061200910_lite_toggle_a_2_1"},
    )
    assert toggle.status_code == 200
    light_state = client.get("/api/states/light.philips_cn_1061200910_lite_s_2").json()["state"]
    assert light_state in {"on", "off"}

    music = client.post(
        "/api/services/button/press",
        json={"entity_id": "button.xiaomi_cn_701074704_l15a_play_music_a_7_5"},
    )
    assert music.status_code == 200
    assert client.get("/api/states/media_player.xiaomi_cn_701074704_l15a").json()["state"] == "playing"


def test_invalid_payloads(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    bad_text = client.post(
        "/api/services/text/set_value",
        json={"entity_id": "text.lumi_cn_551385025_mcn001_effective_time_p_6_2", "value": "bad"},
    )
    assert bad_text.status_code == 400

    missing_service = client.post("/api/services/light/not_exist", json={"entity_id": "light.philips_cn_1061200910_lite_s_2"})
    assert missing_service.status_code == 404

    unsupported_response = client.post(
        "/api/services/light/turn_on?return_response=true",
        json={"entity_id": "light.philips_cn_1061200910_lite_s_2"},
    )
    assert unsupported_response.status_code == 400
