from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import DeviceDefinition, EntityDefinition, StateRecord

_DEVICE_METADATA_FIELDS = {
    "id",
    "name",
    "name_by_user",
    "area_id",
    "manufacturer",
    "model",
    "model_id",
    "sw_version",
    "hw_version",
    "serial_number",
    "identifiers",
    "connections",
    "configuration_url",
    "via_device_id",
}

_ENTITY_METADATA_FIELDS = {
    "entity_id",
    "device_id",
    "unique_id",
    "area_id",
    "platform",
    "name",
    "original_name",
    "device_class",
    "original_device_class",
    "entity_category",
    "hidden_by",
    "disabled_by",
    "supported_features",
    "capabilities",
}


@dataclass(slots=True)
class LegacyParseResult:
    devices: list[DeviceDefinition]
    entities: list[EntityDefinition]
    states: dict[str, StateRecord]
    services_payload: list[dict[str, Any]]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _infer_profile(entity_id: str, domain: str, state: Any, attributes: dict[str, Any]) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    for key in ("min", "max", "step"):
        if key in attributes:
            profile[key] = attributes[key]
    if domain == "text":
        pattern = attributes.get("pattern")
        text_value = str(state)
        if not pattern:
            if re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", text_value):
                pattern = r"\d{2}:\d{2}-\d{2}:\d{2}"
            elif re.fullmatch(r"\d{2}:\d{2}:\d{2}-\d{2}:\d{2}:\d{2}", text_value):
                pattern = r"\d{2}:\d{2}:\d{2}-\d{2}:\d{2}:\d{2}"
        if pattern:
            profile["pattern"] = pattern
    if domain == "light" and "effect_list" in attributes:
        profile["effect_list"] = attributes["effect_list"]
    if entity_id.endswith("_dvalue_p_3_1"):
        profile.setdefault("min", 0)
        profile.setdefault("max", 21600)
    return profile


def _device_domain_map(entity_registry_entries: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    by_device: dict[str, dict[str, list[str]]] = {}
    for item in entity_registry_entries:
        device_id = item.get("device_id")
        if not device_id:
            continue
        domain = item["entity_id"].split(".", 1)[0]
        by_device.setdefault(device_id, {}).setdefault(domain, []).append(item["entity_id"])
    return by_device


def _infer_links_and_actions(
    entity_id: str,
    domain: str,
    device_id: str | None,
    by_device: dict[str, dict[str, list[str]]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    links: dict[str, Any] = {}
    actions: dict[str, list[dict[str, Any]]] = {}
    if device_id is None:
        return links, actions

    device_entities = by_device.get(device_id, {})
    lights = device_entities.get("light", [])
    players = device_entities.get("media_player", [])

    if domain == "button" and lights:
        links["light"] = lights[0]
        if entity_id.endswith("_toggle_a_2_1"):
            actions["on_press"] = [
                {"type": "call_service", "domain": "light", "service": "toggle", "data": {"entity_id": "${links.light}"}}
            ]
        elif entity_id.endswith("_brightness_down_a_3_1"):
            actions["on_press"] = [
                {
                    "type": "call_service",
                    "domain": "light",
                    "service": "turn_on",
                    "data": {"entity_id": "${links.light}", "brightness_step_pct": -10},
                }
            ]
        elif entity_id.endswith("_brightness_up_a_3_2"):
            actions["on_press"] = [
                {
                    "type": "call_service",
                    "domain": "light",
                    "service": "turn_on",
                    "data": {"entity_id": "${links.light}", "brightness_step_pct": 10},
                }
            ]

    if domain == "button" and players:
        links["media_player"] = players[0]
        if entity_id.endswith("_play_music_a_7_5"):
            actions["on_press"] = [
                {
                    "type": "call_service",
                    "domain": "media_player",
                    "service": "media_play",
                    "data": {"entity_id": "${links.media_player}"},
                }
            ]

    return links, actions


def parse_legacy_data(legacy_root: Path) -> LegacyParseResult:
    devices_payload = _read_json(legacy_root / "device_registry.json")
    entity_registry_payload = _read_json(legacy_root / "entity_registry.json")
    states_payload = _read_json(legacy_root / "entities.json")
    services_payload = _read_json(legacy_root / "domains_services.json")

    device_entries = devices_payload["data"]["devices"]
    entity_registry_entries = entity_registry_payload["data"]["entities"]
    if not isinstance(device_entries, list) or not isinstance(entity_registry_entries, list):
        raise ValueError("Invalid legacy registry payload.")
    if not isinstance(states_payload, list) or not isinstance(services_payload, list):
        raise ValueError("Invalid legacy entities/services payload.")

    state_by_entity = {item["entity_id"]: item for item in states_payload}
    entity_registry_by_id = {item["entity_id"]: item for item in entity_registry_entries}
    by_device = _device_domain_map(entity_registry_entries)

    devices: list[DeviceDefinition] = []
    for device_item in device_entries:
        device_id = device_item["id"]
        entities = sorted(by_device.get(device_id, {}).get("binary_sensor", []))
        for entity_ids in by_device.get(device_id, {}).values():
            entities.extend(entity_ids)
        device = DeviceDefinition(
            device_id=device_id,
            name=device_item.get("name"),
            name_by_user=device_item.get("name_by_user"),
            area_id=device_item.get("area_id"),
            manufacturer=device_item.get("manufacturer"),
            model=device_item.get("model"),
            model_id=device_item.get("model_id"),
            sw_version=device_item.get("sw_version"),
            hw_version=device_item.get("hw_version"),
            serial_number=device_item.get("serial_number"),
            identifiers=device_item.get("identifiers", []),
            connections=device_item.get("connections", []),
            configuration_url=device_item.get("configuration_url"),
            via_device_id=device_item.get("via_device_id"),
            entities=sorted(set(entities)),
            metadata={k: v for k, v in device_item.items() if k not in _DEVICE_METADATA_FIELDS},
        )
        devices.append(device)
    devices.sort(key=lambda item: item.device_id)

    entities: list[EntityDefinition] = []
    states: dict[str, StateRecord] = {}
    for entity_id in sorted(state_by_entity):
        state_item = state_by_entity[entity_id]
        registry_item = entity_registry_by_id.get(entity_id, {})
        domain, object_id = entity_id.split(".", 1)
        attributes = state_item.get("attributes", {})
        profile = _infer_profile(entity_id, domain, state_item.get("state"), attributes)
        links, actions = _infer_links_and_actions(entity_id, domain, registry_item.get("device_id"), by_device)
        entity = EntityDefinition(
            entity_id=entity_id,
            domain=domain,
            object_id=object_id,
            unique_id=registry_item.get("unique_id"),
            device_id=registry_item.get("device_id"),
            area_id=registry_item.get("area_id"),
            platform=registry_item.get("platform", "legacy_import"),
            name=registry_item.get("name"),
            original_name=registry_item.get("original_name"),
            device_class=registry_item.get("device_class") or registry_item.get("original_device_class"),
            entity_category=registry_item.get("entity_category"),
            hidden_by=registry_item.get("hidden_by"),
            disabled_by=registry_item.get("disabled_by"),
            supported_features=registry_item.get("supported_features", attributes.get("supported_features", 0)),
            capabilities=registry_item.get("capabilities"),
            service_profile=profile,
            links=links,
            actions=actions,
            state=state_item.get("state", "unknown"),
            attributes=attributes,
            metadata={k: v for k, v in registry_item.items() if k not in _ENTITY_METADATA_FIELDS},
        )
        entities.append(entity)
        states[entity_id] = StateRecord.model_validate(state_item)

    return LegacyParseResult(
        devices=devices,
        entities=entities,
        states=states,
        services_payload=services_payload,
    )
