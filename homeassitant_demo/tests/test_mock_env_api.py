from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from fastapi.testclient import TestClient

from fake_homeassistant_v2.app import create_app
from fake_homeassistant_v2.config import Settings


REPO_ROOT = Path(__file__).resolve().parent.parent

SHARED_CLIMATE_LIVING = "climate.test_living_room_ac_main"
SHARED_SENSOR_LIVING = "sensor.test_living_room_temperature_main"
SHARED_CLIMATE_BEDROOM = "climate.test_bedroom_ac_main"
SHARED_SENSOR_BEDROOM = "sensor.test_bedroom_temperature_main"

NEW_ENV_EXPECTATIONS = [
    ("te_normal_pair_a_v1", "normal", 24.0, 25.0),
    ("te_normal_pair_b_v1", "normal", 22.0, 23.0),
    ("te_one_shot_network_error_pair_a_v1", "one_shot_network_error", 24.0, 25.0),
    ("te_one_shot_network_error_pair_b_v1", "one_shot_network_error", 22.0, 23.0),
    ("te_fake_success_pair_a_v1", "fake_success", 24.0, 25.0),
    ("te_fake_success_pair_b_v1", "fake_success", 22.0, 23.0),
]


def _shared_living_setpoint(client: TestClient) -> float:
    return client.get(f"/api/states/{SHARED_CLIMATE_LIVING}").json()["attributes"]["temperature"]


def _shared_bedroom_setpoint(client: TestClient) -> float:
    return client.get(f"/api/states/{SHARED_CLIMATE_BEDROOM}").json()["attributes"]["temperature"]


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


@pytest.mark.parametrize(
    ("env_id", "expected_mode", "expected_living_setpoint", "expected_bedroom_setpoint"),
    NEW_ENV_EXPECTATIONS,
)
def test_new_envs_init_with_fixed_mode_and_expected_baseline(
    tmp_path: Path,
    env_id: str,
    expected_mode: str,
    expected_living_setpoint: float,
    expected_bedroom_setpoint: float,
) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    result = client.post("/api/mock/init_env", json={"env_id": env_id})
    assert result.status_code == 200
    payload = result.json()
    assert payload["status"] == "initialized"
    assert payload["env_id"] == env_id
    assert payload["active_fault_mode"] == expected_mode

    states = client.get("/api/states").json()
    entity_ids = {item["entity_id"] for item in states}
    assert entity_ids == {
        SHARED_CLIMATE_LIVING,
        SHARED_SENSOR_LIVING,
        SHARED_CLIMATE_BEDROOM,
        SHARED_SENSOR_BEDROOM,
    }
    assert payload["entity_count"] == 4

    assert _shared_living_setpoint(client) == expected_living_setpoint
    assert _shared_bedroom_setpoint(client) == expected_bedroom_setpoint


@pytest.mark.parametrize(
    ("env_id", "unsupported_fault_mode"),
    [
        ("te_normal_pair_a_v1", "fake_success"),
        ("te_normal_pair_b_v1", "one_shot_network_error"),
        ("te_one_shot_network_error_pair_a_v1", "normal"),
        ("te_one_shot_network_error_pair_b_v1", "fake_success"),
        ("te_fake_success_pair_a_v1", "normal"),
        ("te_fake_success_pair_b_v1", "one_shot_network_error"),
    ],
)
def test_new_envs_reject_unsupported_fault_modes(
    tmp_path: Path,
    env_id: str,
    unsupported_fault_mode: str,
) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    result = client.post(
        "/api/mock/init_env",
        json={"env_id": env_id, "fault_mode": unsupported_fault_mode},
    )
    assert result.status_code == 400


@pytest.mark.parametrize(
    ("env_a", "env_b"),
    [
        ("te_normal_pair_a_v1", "te_normal_pair_b_v1"),
        ("te_one_shot_network_error_pair_a_v1", "te_one_shot_network_error_pair_b_v1"),
        ("te_fake_success_pair_a_v1", "te_fake_success_pair_b_v1"),
    ],
)
def test_new_env_pairs_have_different_initial_temperatures(tmp_path: Path, env_a: str, env_b: str) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.post("/api/mock/init_env", json={"env_id": env_a}).status_code == 200
    a_temps = (_shared_living_setpoint(client), _shared_bedroom_setpoint(client))

    assert client.post("/api/mock/init_env", json={"env_id": env_b}).status_code == 200
    b_temps = (_shared_living_setpoint(client), _shared_bedroom_setpoint(client))

    assert a_temps != b_temps


def test_new_normal_mode_room_linkage(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.post("/api/mock/init_env", json={"env_id": "te_normal_pair_a_v1"}).status_code == 200

    result = client.post(
        "/api/services/climate/set_temperature",
        json={"entity_id": SHARED_CLIMATE_LIVING, "temperature": 21.5},
    )
    assert result.status_code == 200
    changed_ids = {item["entity_id"] for item in result.json()}
    assert changed_ids == {SHARED_CLIMATE_LIVING, SHARED_SENSOR_LIVING}

    assert client.get(f"/api/states/{SHARED_SENSOR_LIVING}").json()["state"] == 21.5
    assert client.get(f"/api/states/{SHARED_SENSOR_BEDROOM}").json()["state"] == 27.0


def test_new_one_shot_mode_first_call_fails_then_recovers(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.post("/api/mock/init_env", json={"env_id": "te_one_shot_network_error_pair_a_v1"}).status_code == 200

    first = client.post(
        "/api/services/climate/set_temperature",
        json={"entity_id": SHARED_CLIMATE_LIVING, "temperature": 22.0},
    )
    assert first.status_code == 503
    assert _shared_living_setpoint(client) == 24.0

    second = client.post(
        "/api/services/climate/set_temperature",
        json={"entity_id": SHARED_CLIMATE_LIVING, "temperature": 22.0},
    )
    assert second.status_code == 200
    changed_ids = {item["entity_id"] for item in second.json()}
    assert changed_ids == {SHARED_CLIMATE_LIVING, SHARED_SENSOR_LIVING}

    assert client.get(f"/api/states/{SHARED_SENSOR_LIVING}").json()["state"] == 22.0
    assert client.get(f"/api/states/{SHARED_SENSOR_BEDROOM}").json()["state"] == 27.0


def test_new_fake_success_mode_returns_success_without_state_change(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.post("/api/mock/init_env", json={"env_id": "te_fake_success_pair_a_v1"}).status_code == 200

    result = client.post(
        "/api/services/climate/set_temperature",
        json={"entity_id": SHARED_CLIMATE_LIVING, "temperature": 20.0},
    )
    assert result.status_code == 200
    assert result.json() == []

    assert _shared_living_setpoint(client) == 24.0
    assert client.get(f"/api/states/{SHARED_SENSOR_LIVING}").json()["state"] == 28.0


def test_init_env_replaces_entities_and_uses_default_fault_mode(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    result = client.post("/api/mock/init_env", json={"env_id": "te_ac_sensor_v1"})
    assert result.status_code == 200
    payload = result.json()
    assert payload["status"] == "initialized"
    assert payload["env_id"] == "te_ac_sensor_v1"
    assert payload["active_fault_mode"] == "normal"
    assert payload["saved_original_snapshot"] is True

    states = client.get("/api/states").json()
    entity_ids = {item["entity_id"] for item in states}
    assert entity_ids == {
        "climate.test_ac_01",
        "sensor.test_room_temperature_living_1",
        "sensor.test_room_temperature_living_2",
        "sensor.test_room_temperature_bedroom_1",
    }
    assert payload["entity_count"] == len(entity_ids)


def test_one_shot_network_error_then_success_and_room_linkage(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    init_result = client.post(
        "/api/mock/init_env",
        json={"env_id": "te_ac_sensor_v1", "fault_mode": "one_shot_network_error"},
    )
    assert init_result.status_code == 200

    first = client.post(
        "/api/services/climate/set_temperature",
        json={"entity_id": "climate.test_ac_01", "temperature": 22.0},
    )
    assert first.status_code == 503

    climate_after_first = client.get("/api/states/climate.test_ac_01").json()
    assert climate_after_first["attributes"]["temperature"] == 24.0

    second = client.post(
        "/api/services/climate/set_temperature",
        json={"entity_id": "climate.test_ac_01", "temperature": 22.0},
    )
    assert second.status_code == 200
    changed_ids = {item["entity_id"] for item in second.json()}
    assert changed_ids == {
        "climate.test_ac_01",
        "sensor.test_room_temperature_living_1",
        "sensor.test_room_temperature_living_2",
    }

    assert client.get("/api/states/sensor.test_room_temperature_living_1").json()["state"] == 22.0
    assert client.get("/api/states/sensor.test_room_temperature_living_2").json()["state"] == 22.0
    assert client.get("/api/states/sensor.test_room_temperature_bedroom_1").json()["state"] == 26.0


def test_bootstrap_reconciles_stale_builtin_service_handler(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, legacy=False)
    services_dir = settings.storage_root / "services"
    services_dir.mkdir(parents=True, exist_ok=True)
    (services_dir / "climate__set_temperature.yaml").write_text(
        dedent(
            """
            domain: climate
            service: set_temperature
            name: Set temperature
            fields:
              entity_id:
                required: true
              temperature:
                required: true
            handler: builtin:service.not_implemented
            supports_response: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    client = TestClient(create_app(settings=settings))

    services = client.get("/api/services").json()
    climate_group = next(item for item in services if item["domain"] == "climate")
    assert climate_group["services"]["set_temperature"]["handler"] == "builtin:climate.set_temperature"

    assert (
        client.post(
            "/api/mock/init_env",
            json={"env_id": "te_ac_sensor_v1", "fault_mode": "one_shot_network_error"},
        ).status_code
        == 200
    )

    first = client.post(
        "/api/services/climate/set_temperature",
        json={"entity_id": "climate.test_ac_01", "temperature": 22.0},
    )
    assert first.status_code == 503

    second = client.post(
        "/api/services/climate/set_temperature",
        json={"entity_id": "climate.test_ac_01", "temperature": 22.0},
    )
    assert second.status_code == 200


def test_fake_success_returns_success_but_does_not_change_state(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    init_result = client.post(
        "/api/mock/init_env",
        json={"env_id": "te_ac_sensor_v1", "fault_mode": "fake_success"},
    )
    assert init_result.status_code == 200

    result = client.post(
        "/api/services/climate/set_temperature",
        json={"entity_id": "climate.test_ac_01", "temperature": 20.0},
    )
    assert result.status_code == 200
    assert result.json() == []

    assert client.get("/api/states/climate.test_ac_01").json()["attributes"]["temperature"] == 24.0
    assert client.get("/api/states/sensor.test_room_temperature_living_1").json()["state"] == 28.0
    assert client.get("/api/states/sensor.test_room_temperature_living_2").json()["state"] == 28.0


def test_normal_mode_updates_all_same_room_temperature_sensors(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.post("/api/mock/init_env", json={"env_id": "te_ac_sensor_v1"}).status_code == 200

    result = client.post(
        "/api/services/climate/set_temperature",
        json={"entity_id": "climate.test_ac_01", "temperature": 21.5},
    )
    assert result.status_code == 200

    assert client.get("/api/states/sensor.test_room_temperature_living_1").json()["state"] == 21.5
    assert client.get("/api/states/sensor.test_room_temperature_living_2").json()["state"] == 21.5
    assert client.get("/api/states/sensor.test_room_temperature_bedroom_1").json()["state"] == 26.0


def test_base_env_loads_from_legacy_states(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    init_result = client.post("/api/mock/init_env", json={"env_id": "base_env"})
    assert init_result.status_code == 200
    payload = init_result.json()
    assert payload["env_id"] == "base_env"
    assert payload["active_fault_mode"] == "normal"
    assert payload["entity_count"] == 71

    light_state = client.get("/api/states/light.philips_cn_1061200910_lite_s_2").json()
    assert light_state["state"] == "on"
    assert light_state["attributes"]["brightness"] == 3

    player_state = client.get("/api/states/media_player.xiaomi_cn_701074704_l15a").json()
    assert player_state["state"] == "idle"
    assert player_state["attributes"]["volume_level"] == 0.1


def test_base_env_reinit_resets_to_copied_data_baseline(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))
    assert client.post("/api/mock/init_env", json={"env_id": "base_env"}).status_code == 200

    changed = client.post("/api/services/light/turn_off", json={"entity_id": "light.philips_cn_1061200910_lite_s_2"})
    assert changed.status_code == 200
    assert client.get("/api/states/light.philips_cn_1061200910_lite_s_2").json()["state"] == "off"

    reset = client.post("/api/mock/init_env", json={"env_id": "base_env"})
    assert reset.status_code == 200
    reset_payload = reset.json()
    assert reset_payload["saved_original_snapshot"] is False

    light_state = client.get("/api/states/light.philips_cn_1061200910_lite_s_2").json()
    assert light_state["state"] == "on"
    assert light_state["attributes"]["brightness"] == 3


def test_base_env_rejects_non_normal_fault_mode(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    result = client.post(
        "/api/mock/init_env",
        json={"env_id": "base_env", "fault_mode": "one_shot_network_error"},
    )
    assert result.status_code == 400


def test_base_env_not_registered_when_legacy_root_missing(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path, legacy=False)))

    result = client.post("/api/mock/init_env", json={"env_id": "base_env"})
    assert result.status_code == 404


def test_original_env_restore_and_no_snapshot_error(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=make_settings(tmp_path)))

    baseline_states = client.get("/api/states").json()
    baseline_count = len(baseline_states)
    assert baseline_count > 10

    assert client.post("/api/mock/init_env", json={"env_id": "te_ac_sensor_v1"}).status_code == 200
    assert len(client.get("/api/states").json()) == 4

    restored = client.post("/api/mock/original_env")
    assert restored.status_code == 200
    restored_payload = restored.json()
    assert restored_payload["status"] == "restored"
    assert restored_payload["restored"] is True

    after_restore_states = client.get("/api/states").json()
    assert len(after_restore_states) == baseline_count
    assert any(item["entity_id"] == "light.philips_cn_1061200910_lite_s_2" for item in after_restore_states)

    no_snapshot = client.post("/api/mock/original_env")
    assert no_snapshot.status_code == 400
