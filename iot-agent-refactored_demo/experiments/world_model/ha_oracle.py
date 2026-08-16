from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
WORLD_PATH = Path(__file__).with_name("v1.json")


class HAOracle:
    """Deterministic Home Assistant-like world used by the experiment runner."""

    def __init__(self, world_path: str | Path = WORLD_PATH):
        self.world_path = Path(world_path)
        self.definition = json.loads(self.world_path.read_text(encoding="utf-8"))
        self.world_version = self.definition["world_version"]
        self.t0 = datetime.fromisoformat(self.definition["t0"])
        self.current_time = self.t0
        self.entities = {item["entity_id"]: copy.deepcopy(item) for item in self.definition["entities"]}
        self.states = {
            entity_id: self._default_state(item["type"])
            for entity_id, item in self.entities.items()
        }
        self.failures: dict[str, dict[str, Any]] = {}
        self.events_applied: list[str] = []

    @staticmethod
    def _default_state(entity_type: str) -> dict[str, Any]:
        if entity_type in {"light", "switch"}:
            return {"state": "off", "attributes": {}}
        if entity_type == "cover":
            return {"state": "open", "attributes": {"position": 100}}
        if entity_type == "lock":
            return {"state": "unlocked", "attributes": {}}
        if entity_type == "climate":
            return {"state": "off", "attributes": {"target_temp": 24}}
        if entity_type == "binary_sensor":
            return {"state": "off", "attributes": {"motion": False}}
        return {"state": "unknown", "attributes": {}}

    def reset(self, state_overrides: dict[str, dict[str, Any]] | None = None):
        self.current_time = self.t0
        self.entities = {item["entity_id"]: copy.deepcopy(item) for item in self.definition["entities"]}
        self.states = {
            entity_id: self._default_state(item["type"])
            for entity_id, item in self.entities.items()
        }
        self.failures = {}
        self.events_applied = []
        for entity_id, override in (state_overrides or {}).items():
            if entity_id in self.states:
                self.states[entity_id] = copy.deepcopy(override)

    def get_registry(self, at: datetime | None = None) -> dict[str, Any]:
        return {
            "world_version": self.world_version,
            "sim_time": (at or self.current_time).isoformat(),
            "entities": copy.deepcopy(list(self.entities.values())),
        }

    def get_state(self, entity_id: str, at: datetime | None = None) -> dict[str, Any]:
        if entity_id not in self.states:
            raise KeyError(f"Unknown entity: {entity_id}")
        return copy.deepcopy(self.states[entity_id])

    def events_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        events = []
        for event in self.definition["events"]:
            event_time = self.t0 + timedelta(days=event["offset_days"])
            hour, minute = map(int, event["time"].split(":"))
            event_time = event_time.replace(hour=hour, minute=minute)
            if start < event_time <= end:
                events.append({**copy.deepcopy(event), "sim_time": event_time.isoformat()})
        return sorted(events, key=lambda item: item["sim_time"])

    def advance_to(self, target: datetime):
        for event in self.events_between(self.current_time, target):
            self.apply_event(event)
        self.current_time = target

    def apply_event(self, event: dict[str, Any]):
        event_id = event["event_id"]
        if event_id in self.events_applied:
            return
        payload = event["payload"]
        entity_id = payload["entity_id"]
        if payload.get("failure"):
            trigger_time = datetime.fromisoformat(event["sim_time"])
            self.failures[entity_id] = {
                "error": payload["failure"],
                "trigger_time": trigger_time,
                "expires_at": trigger_time + timedelta(minutes=1),
            }
        elif payload.get("mutation") == "remove":
            self.entities.pop(entity_id, None)
            self.states.pop(entity_id, None)
        elif payload.get("mutation") == "remove_capability":
            entity = self.entities.get(entity_id)
            if entity:
                entity["capabilities"] = [
                    item for item in entity["capabilities"] if item != payload["capability"]
                ]
        elif payload.get("mutation") == "move_room":
            entity = self.entities.get(entity_id)
            if entity:
                entity["room"] = payload["room"]
        self.events_applied.append(event_id)

    def apply(self, service: str, args: dict[str, Any], at: datetime | None = None) -> dict[str, Any]:
        service_schema = self.definition.get("services", {}).get(service)
        if service_schema is None:
            return {"success": False, "error": "unsupported_service", "service": service}
        required = set(service_schema.get("required", []))
        optional = set(service_schema.get("optional", []))
        normalized_keys = {"entity" if key == "entity_id" else key for key in args}
        missing = sorted(required - normalized_keys)
        if missing:
            return {"success": False, "error": "missing_required_args", "missing": missing, "service": service}
        unexpected = sorted(normalized_keys - required - optional)
        if unexpected:
            return {"success": False, "error": "unexpected_args", "unexpected": unexpected, "service": service}
        entity_id = args.get("entity") or args.get("entity_id")
        if entity_id not in self.entities:
            return {"success": False, "error": "entity_not_found", "entity_id": entity_id}
        failure = self.failures.get(entity_id)
        effective_time = at or self.current_time
        if failure:
            if failure["trigger_time"] <= effective_time < failure["expires_at"]:
                self.failures.pop(entity_id, None)
                return {"success": False, "error": failure["error"], "entity_id": entity_id}
            if effective_time >= failure["expires_at"]:
                self.failures.pop(entity_id, None)

        entity = self.entities[entity_id]
        domain, action = service.split(".", 1)
        if entity["type"] != service_schema.get("type") or entity["type"] != domain:
            return {"success": False, "error": "wrong_domain", "entity_id": entity_id}
        required_capability = {
            "turn_on": "on_off",
            "turn_off": "on_off",
            "set_position": "set_position",
            "lock": "lock",
            "unlock": "unlock",
            "set_temperature": "set_temp",
        }.get(action)
        if required_capability not in entity.get("capabilities", []):
            return {
                "success": False,
                "error": "unsupported_capability",
                "entity_id": entity_id,
                "required_capability": required_capability,
            }
        for argument, capability in {"brightness": "brightness", "color_temp": "color_temp"}.items():
            if argument in args and capability not in entity.get("capabilities", []):
                return {
                    "success": False,
                    "error": "unsupported_capability",
                    "entity_id": entity_id,
                    "required_capability": capability,
                }
        range_error = self._validate_action_ranges(args)
        if range_error:
            return {"success": False, "error": "argument_out_of_range", "service": service, **range_error}
        if action == "turn_on":
            self.states[entity_id]["state"] = "on"
            self.states[entity_id]["attributes"].update(
                {key: value for key, value in args.items() if key in {"brightness", "color_temp"}}
            )
        elif action == "turn_off":
            self.states[entity_id]["state"] = "off"
        elif action == "set_position":
            self.states[entity_id]["state"] = "open" if args["position"] > 0 else "closed"
            self.states[entity_id]["attributes"]["position"] = args["position"]
        elif action in {"lock", "unlock"}:
            self.states[entity_id]["state"] = "locked" if action == "lock" else "unlocked"
        elif action == "set_temperature":
            self.states[entity_id]["attributes"]["target_temp"] = args["temperature"]
        else:
            return {"success": False, "error": "unsupported_service", "service": service}
        return {"success": True, "entity_id": entity_id, "state": self.get_state(entity_id)}

    @staticmethod
    def _validate_action_ranges(args: dict[str, Any]) -> dict[str, Any] | None:
        limits = {
            "brightness": (0, 255),
            "color_temp": (153, 500),
            "position": (0, 100),
            "temperature": (16, 30),
        }
        for key, (minimum, maximum) in limits.items():
            if key not in args:
                continue
            value = args[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
                return {"argument": key, "value": value, "minimum": minimum, "maximum": maximum}
        return None
