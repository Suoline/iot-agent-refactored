from __future__ import annotations

import importlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field

from .config import Settings
from .legacy_parser import parse_legacy_data
from .models import (
    ContextModel,
    DeviceDefinition,
    EntityDefinition,
    EventRecord,
    HandlerResult,
    ServiceCallResponse,
    ServiceDefinition,
    StateRecord,
)

BUILTIN_SERVICE_HANDLERS: dict[str, str] = {
    "switch.turn_on": "builtin:switch.turn_on",
    "switch.turn_off": "builtin:switch.turn_off",
    "switch.toggle": "builtin:switch.toggle",
    "light.turn_on": "builtin:light.turn_on",
    "light.turn_off": "builtin:light.turn_off",
    "light.toggle": "builtin:light.toggle",
    "climate.set_hvac_mode": "builtin:climate.set_hvac_mode",
    "climate.set_temperature": "builtin:climate.set_temperature",
    "number.set_value": "builtin:number.set_value",
    "text.set_value": "builtin:text.set_value",
    "select.select_first": "builtin:select.select_first",
    "select.select_last": "builtin:select.select_last",
    "select.select_next": "builtin:select.select_next",
    "select.select_previous": "builtin:select.select_previous",
    "select.select_option": "builtin:select.select_option",
    "button.press": "builtin:button.press",
    "media_player.volume_set": "builtin:media_player.volume_set",
    "media_player.volume_up": "builtin:media_player.volume_up",
    "media_player.volume_down": "builtin:media_player.volume_down",
    "media_player.volume_mute": "builtin:media_player.volume_mute",
    "media_player.media_play": "builtin:media_player.media_play",
    "media_player.media_pause": "builtin:media_player.media_pause",
    "media_player.media_play_pause": "builtin:media_player.media_play_pause",
    "media_player.media_stop": "builtin:media_player.media_stop",
    "media_player.media_previous_track": "builtin:media_player.media_previous_track",
    "media_player.media_next_track": "builtin:media_player.media_next_track",
    "notify.send_message": "builtin:notify.send_message",
    "homeassistant.turn_on": "builtin:homeassistant.turn_on",
    "homeassistant.turn_off": "builtin:homeassistant.turn_off",
    "homeassistant.toggle": "builtin:homeassistant.toggle",
    "homeassistant.update_entity": "builtin:homeassistant.update_entity",
    "homeassistant.save_persistent_states": "builtin:homeassistant.save_persistent_states",
}


class FakeHomeAssistantError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(FakeHomeAssistantError):
    status_code = 404


class ConflictError(FakeHomeAssistantError):
    status_code = 409


class ServiceUnavailableError(FakeHomeAssistantError):
    status_code = 503


class StorageManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.devices_dir = root / "devices"
        self.entities_dir = root / "entities"
        self.services_dir = root / "services"
        self.state_path = root / "state_store.json"
        self.events_path = root / "events.json"
        self.ensure_layout()

    def ensure_layout(self) -> None:
        for path in (self.root, self.devices_dir, self.entities_dir, self.services_dir):
            path.mkdir(parents=True, exist_ok=True)

    def _safe_name(self, name: str) -> str:
        return name.replace("/", "__").replace("\\", "__").replace(":", "__")

    def _atomic_write(self, path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)

    def write_json(self, path: Path, data: Any) -> None:
        self._atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))

    def write_yaml(self, path: Path, data: Any) -> None:
        self._atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))

    def list_data_files(self, directory: Path) -> list[Path]:
        files: list[Path] = []
        for suffix in ("*.json", "*.yaml", "*.yml"):
            files.extend(sorted(directory.glob(suffix)))
        return files

    def read_data_file(self, path: Path) -> Any:
        raw = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            return json.loads(raw)
        return yaml.safe_load(raw)

    def write_device(self, device: DeviceDefinition) -> None:
        path = self.devices_dir / f"{self._safe_name(device.device_id)}.json"
        self.write_json(path, device.model_dump(mode="json"))

    def write_entity(self, entity: EntityDefinition) -> None:
        path = self.entities_dir / f"{self._safe_name(entity.entity_id)}.json"
        self.write_json(path, entity.model_dump(mode="json"))

    def write_service(self, service: ServiceDefinition) -> None:
        path = self.services_dir / f"{self._safe_name(service.domain)}__{self._safe_name(service.service)}.yaml"
        self.write_yaml(path, service.model_dump(mode="json"))

    def write_states(self, states: dict[str, StateRecord]) -> None:
        payload = {key: value.model_dump(mode="json") for key, value in states.items()}
        self.write_json(self.state_path, payload)

    def write_events(self, events: list[EventRecord]) -> None:
        self.write_json(self.events_path, [event.model_dump(mode="json") for event in events])

    def load_devices(self) -> dict[str, DeviceDefinition]:
        devices: dict[str, DeviceDefinition] = {}
        for path in self.list_data_files(self.devices_dir):
            device = DeviceDefinition.model_validate(self.read_data_file(path))
            devices[device.device_id] = device
        return devices

    def load_entities(self) -> dict[str, EntityDefinition]:
        entities: dict[str, EntityDefinition] = {}
        for path in self.list_data_files(self.entities_dir):
            entity = EntityDefinition.model_validate(self.read_data_file(path))
            entities[entity.entity_id] = entity
        return entities

    def load_services(self) -> dict[str, ServiceDefinition]:
        services: dict[str, ServiceDefinition] = {}
        for path in self.list_data_files(self.services_dir):
            payload = self.read_data_file(path)
            for item in payload if isinstance(payload, list) else [payload]:
                service = ServiceDefinition.model_validate(item)
                services[service.key] = service
        return services

    def load_states(self) -> dict[str, StateRecord]:
        if not self.state_path.exists():
            return {}
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return {key: StateRecord.model_validate(value) for key, value in payload.items()}

    def load_events(self) -> list[EventRecord]:
        if not self.events_path.exists():
            return []
        payload = json.loads(self.events_path.read_text(encoding="utf-8"))
        return [EventRecord.model_validate(item) for item in payload]

    def seed_services(self, source_dir: Path) -> None:
        if any(self.list_data_files(self.services_dir)):
            return
        for path in self.list_data_files(source_dir):
            shutil.copy2(path, self.services_dir / path.name)

    def clear_data_files(self, directory: Path) -> None:
        for path in self.list_data_files(directory):
            path.unlink(missing_ok=True)


class RegistryStore:
    def __init__(self, storage: StorageManager) -> None:
        self.storage = storage
        self.devices: dict[str, DeviceDefinition] = {}
        self.entities: dict[str, EntityDefinition] = {}
        self.services: dict[str, ServiceDefinition] = {}

    def reload(self) -> None:
        self.devices = self.storage.load_devices()
        self.entities = self.storage.load_entities()
        self.services = self.storage.load_services()

    def save_device(self, device: DeviceDefinition) -> None:
        self.devices[device.device_id] = device
        self.storage.write_device(device)

    def save_entity(self, entity: EntityDefinition) -> None:
        self.entities[entity.entity_id] = entity
        self.storage.write_entity(entity)

    def save_service(self, service: ServiceDefinition) -> None:
        self.services[service.key] = service
        self.storage.write_service(service)

    def get_device(self, device_id: str) -> DeviceDefinition:
        device = self.devices.get(device_id)
        if device is None:
            raise NotFoundError(f"Unknown device_id: {device_id}")
        return device

    def list_devices(self) -> list[DeviceDefinition]:
        return list(self.devices.values())

    def list_entities(self) -> list[EntityDefinition]:
        return list(self.entities.values())

    def get_entity(self, entity_id: str) -> EntityDefinition:
        entity = self.entities.get(entity_id)
        if entity is None:
            raise NotFoundError(f"Unknown entity_id: {entity_id}")
        return entity

    def get_service(self, domain: str, service: str) -> ServiceDefinition:
        service_def = self.services.get(f"{domain}.{service}")
        if service_def is None:
            raise NotFoundError(f"Unknown service: {domain}.{service}")
        return service_def


class StateStore:
    def __init__(self, settings: Settings, storage: StorageManager, registry: RegistryStore) -> None:
        self.settings = settings
        self.storage = storage
        self.registry = registry
        self.states: dict[str, StateRecord] = {}

    def now(self) -> datetime:
        return datetime.now(tz=ZoneInfo(self.settings.timezone))

    def new_context(self) -> ContextModel:
        return ContextModel(id=uuid4().hex)

    def reload(self) -> None:
        self.states = self.storage.load_states()
        for entity in self.registry.entities.values():
            self.ensure_entity(entity)

    def list(self) -> list[StateRecord]:
        return list(self.states.values())

    def get(self, entity_id: str) -> StateRecord:
        state = self.states.get(entity_id)
        if state is None:
            raise NotFoundError(f"Unknown entity state: {entity_id}")
        return state

    def ensure_entity(self, entity: EntityDefinition) -> StateRecord:
        current = self.states.get(entity.entity_id)
        if current is not None:
            return current
        now = self.now()
        record = StateRecord(
            entity_id=entity.entity_id,
            state=entity.state,
            attributes=dict(entity.attributes),
            last_changed=now,
            last_reported=now,
            last_updated=now,
            context=self.new_context(),
        )
        self.states[entity.entity_id] = record
        return record

    def upsert(
        self,
        entity_id: str,
        *,
        state: Any | None = None,
        attributes: dict[str, Any] | None = None,
        merge_attributes: bool = True,
        context: ContextModel | None = None,
        persist: bool = True,
    ) -> StateRecord:
        current = self.states.get(entity_id)
        if current is None:
            entity = self.registry.entities.get(entity_id)
            if entity is None:
                domain, _, object_id = entity_id.partition(".")
                entity = EntityDefinition(entity_id=entity_id, domain=domain, object_id=object_id, state="unknown")
                self.registry.save_entity(entity)
            current = self.ensure_entity(entity)

        now = self.now()
        next_state = current.state if state is None else state
        next_attributes = dict(current.attributes)
        if attributes:
            next_attributes = {**next_attributes, **attributes} if merge_attributes else dict(attributes)
        record = StateRecord(
            entity_id=entity_id,
            state=next_state,
            attributes=next_attributes,
            last_changed=current.last_changed if next_state == current.state else now,
            last_reported=now,
            last_updated=now,
            context=context or self.new_context(),
        )
        self.states[entity_id] = record
        if persist:
            self.storage.write_states(self.states)
        return record

    def delete(self, entity_id: str) -> StateRecord:
        record = self.states.pop(entity_id, None)
        if record is None:
            raise NotFoundError(f"Unknown entity state: {entity_id}")
        self.storage.write_states(self.states)
        return record

    def persist(self) -> None:
        self.storage.write_states(self.states)


class EventBus:
    def __init__(self, settings: Settings, storage: StorageManager) -> None:
        self.settings = settings
        self.storage = storage
        self.events: list[EventRecord] = []

    def reload(self) -> None:
        self.events = self.storage.load_events()

    def fire(self, event_type: str, data: dict[str, Any] | None = None, context: ContextModel | None = None) -> EventRecord:
        event = EventRecord(
            event_type=event_type,
            data=data or {},
            time_fired=datetime.now(tz=ZoneInfo(self.settings.timezone)),
            context=context or ContextModel(id=uuid4().hex),
        )
        self.events.append(event)
        self.events = self.events[-500:]
        self.storage.write_events(self.events)
        return event

    def available_event_types(self) -> list[dict[str, Any]]:
        types = {"call_service", "state_changed"} | {event.event_type for event in self.events}
        return [{"event": event_type, "listener_count": 0} for event_type in sorted(types)]


FaultMode = Literal["normal", "one_shot_network_error", "fake_success"]


class InitialStateSpec(BaseModel):
    entity_id: str
    state: Any
    attributes: dict[str, Any] = Field(default_factory=dict)


class LinkRuleSpec(BaseModel):
    source_domain: str
    source_service: str
    target_domain: str
    match: str = "same_area_id"
    target_device_class: str | None = None


class FaultRuleSpec(BaseModel):
    domain: str
    service: str
    entity_id: str | None = None
    times: int | None = None


class TestEnvironmentDefinition(BaseModel):
    env_id: str
    default_fault_mode: FaultMode = "normal"
    supported_fault_modes: list[FaultMode] = Field(default_factory=lambda: ["normal"])
    devices: list[DeviceDefinition] = Field(default_factory=list)
    entities: list[EntityDefinition] = Field(default_factory=list)
    initial_states: list[InitialStateSpec] = Field(default_factory=list)
    link_rules: list[LinkRuleSpec] = Field(default_factory=list)
    fault_profiles: dict[str, list[FaultRuleSpec]] = Field(default_factory=dict)


@dataclass(slots=True)
class RuntimeSnapshot:
    devices: dict[str, DeviceDefinition]
    entities: dict[str, EntityDefinition]
    services: dict[str, ServiceDefinition]
    states: dict[str, StateRecord]
    events: list[EventRecord]


class TestEnvManager:
    def __init__(self, runtime: "FakeHomeAssistantRuntime", definitions_dir: Path) -> None:
        self.runtime = runtime
        self.definitions_dir = definitions_dir
        self.environments: dict[str, TestEnvironmentDefinition] = {}
        self.original_snapshot: RuntimeSnapshot | None = None
        self.active_env_id: str | None = None
        self.active_fault_mode: FaultMode = "normal"
        self._active_fault_rules: list[FaultRuleSpec] = []
        self._active_fault_counters: dict[int, int] = {}
        self.reload_definitions()

    @staticmethod
    def _copy_map[T: BaseModel](items: dict[str, T]) -> dict[str, T]:
        return {key: value.model_copy(deep=True) for key, value in items.items()}

    @staticmethod
    def _copy_list[T: BaseModel](items: list[T]) -> list[T]:
        return [item.model_copy(deep=True) for item in items]

    def _register_environment(self, env: TestEnvironmentDefinition) -> None:
        if env.default_fault_mode not in env.supported_fault_modes:
            raise FakeHomeAssistantError(
                f"default_fault_mode '{env.default_fault_mode}' must be listed in supported_fault_modes for {env.env_id}"
            )
        for mode in env.fault_profiles:
            if mode not in env.supported_fault_modes:
                raise FakeHomeAssistantError(
                    f"fault_profiles contains unsupported mode '{mode}' for {env.env_id}"
                )
        if env.env_id in self.environments:
            raise ConflictError(f"Duplicate test environment id: {env.env_id}")
        self.environments[env.env_id] = env

    def _load_dynamic_base_env(self) -> TestEnvironmentDefinition | None:
        legacy_root = self.runtime.settings.legacy_root
        if legacy_root is None:
            return None
        try:
            legacy_data = parse_legacy_data(legacy_root)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

        initial_states = [
            InitialStateSpec(
                entity_id=state.entity_id,
                state=state.state,
                attributes=dict(state.attributes),
            )
            for state in legacy_data.states.values()
        ]
        return TestEnvironmentDefinition(
            env_id="base_env",
            default_fault_mode="normal",
            supported_fault_modes=["normal"],
            devices=[device.model_copy(deep=True) for device in legacy_data.devices],
            entities=[entity.model_copy(deep=True) for entity in legacy_data.entities],
            initial_states=initial_states,
            link_rules=[],
            fault_profiles={},
        )

    def reload_definitions(self) -> None:
        self.environments = {}
        if self.definitions_dir.exists():
            for path in sorted(self.definitions_dir.glob("*.yaml")) + sorted(self.definitions_dir.glob("*.yml")):
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise FakeHomeAssistantError(f"Invalid test environment definition in {path.name}")
                env = TestEnvironmentDefinition.model_validate(payload)
                self._register_environment(env)

        base_env = self._load_dynamic_base_env()
        if base_env is not None:
            self._register_environment(base_env)

    def capture_snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            devices=self._copy_map(self.runtime.registry.devices),
            entities=self._copy_map(self.runtime.registry.entities),
            services=self._copy_map(self.runtime.registry.services),
            states=self._copy_map(self.runtime.state_store.states),
            events=self._copy_list(self.runtime.event_bus.events),
        )

    def _activate_fault_profile(self, env: TestEnvironmentDefinition, mode: FaultMode) -> None:
        self.active_fault_mode = mode
        self._active_fault_rules = [rule.model_copy(deep=True) for rule in env.fault_profiles.get(mode, [])]
        self._active_fault_counters = {}
        for idx, rule in enumerate(self._active_fault_rules):
            if rule.times is not None:
                self._active_fault_counters[idx] = max(0, int(rule.times))
            elif mode == "one_shot_network_error":
                self._active_fault_counters[idx] = 1

    def init_env(self, env_id: str, fault_mode: str | None = None) -> dict[str, Any]:
        env = self.environments.get(env_id)
        if env is None:
            raise NotFoundError(f"Unknown env_id: {env_id}")

        selected_mode = fault_mode or env.default_fault_mode
        if selected_mode not in env.supported_fault_modes:
            raise FakeHomeAssistantError(
                f"Unsupported fault_mode '{selected_mode}' for env '{env_id}', supported: {env.supported_fault_modes}"
            )

        saved_snapshot = False
        if self.original_snapshot is None:
            self.original_snapshot = self.capture_snapshot()
            saved_snapshot = True

        self.runtime.apply_test_environment(env)
        self.active_env_id = env_id
        self._activate_fault_profile(env, selected_mode)

        return {
            "status": "initialized",
            "env_id": env_id,
            "active_fault_mode": self.active_fault_mode,
            "saved_original_snapshot": saved_snapshot,
            "entity_count": len(self.runtime.registry.entities),
        }

    def restore_original_env(self) -> dict[str, Any]:
        if self.original_snapshot is None:
            raise FakeHomeAssistantError("No original environment snapshot found. Call /api/mock/init_env first.")

        self.runtime.restore_snapshot(self.original_snapshot)
        self.original_snapshot = None
        self.active_env_id = None
        self.active_fault_mode = "normal"
        self._active_fault_rules = []
        self._active_fault_counters = {}

        return {"status": "restored", "restored": True, "entity_count": len(self.runtime.registry.entities)}

    def _fault_rule_matches(
        self,
        rule: FaultRuleSpec,
        *,
        domain: str,
        service: str,
        target_entity_ids: list[str],
    ) -> bool:
        if rule.domain != domain or rule.service != service:
            return False
        if rule.entity_id is None:
            return True
        return rule.entity_id in target_entity_ids

    def evaluate_fault(
        self,
        *,
        domain: str,
        service: str,
        target_entity_ids: list[str],
    ) -> FaultMode | None:
        if self.active_env_id is None or self.active_fault_mode == "normal":
            return None

        for idx, rule in enumerate(self._active_fault_rules):
            if not self._fault_rule_matches(rule, domain=domain, service=service, target_entity_ids=target_entity_ids):
                continue
            if idx in self._active_fault_counters:
                remaining = self._active_fault_counters[idx]
                if remaining <= 0:
                    continue
                self._active_fault_counters[idx] = remaining - 1
            return self.active_fault_mode
        return None

    def apply_climate_temperature_links(
        self,
        *,
        source_entity: EntityDefinition,
        temperature: float,
        context: ContextModel,
    ) -> list[str]:
        if self.active_env_id is None:
            return []
        env = self.environments.get(self.active_env_id)
        if env is None:
            return []

        changed: list[str] = []
        for rule in env.link_rules:
            if rule.source_domain != "climate" or rule.source_service != "set_temperature":
                continue
            if source_entity.domain != rule.source_domain:
                continue
            if rule.match != "same_area_id":
                continue
            if source_entity.area_id is None:
                continue

            for target in self.runtime.registry.entities.values():
                if target.domain != rule.target_domain:
                    continue
                if target.area_id is None or target.area_id != source_entity.area_id:
                    continue
                if rule.target_device_class and target.device_class != rule.target_device_class:
                    continue
                if target.entity_id == source_entity.entity_id:
                    continue
                current = self.runtime.state_store.states.get(target.entity_id)
                attributes = dict(current.attributes) if current is not None else dict(target.attributes)
                self.runtime.set_state(target.entity_id, state=temperature, attributes=attributes, context=context)
                changed.append(target.entity_id)

        return list(dict.fromkeys(changed))


@dataclass(slots=True)
class ServiceExecutionContext:
    runtime: "FakeHomeAssistantRuntime"
    service: ServiceDefinition
    payload: dict[str, Any]
    target_entity_ids: list[str]
    context: ContextModel
    return_response: bool


HandlerFunc = Callable[[ServiceExecutionContext], HandlerResult]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, HandlerFunc] = {}

    def register(self, name: str, func: HandlerFunc) -> None:
        self._handlers[name] = func

    def resolve(self, name: str) -> HandlerFunc:
        handler = self._handlers.get(name)
        if handler is not None:
            return handler
        if ":" in name and not name.startswith("builtin:"):
            module_name, func_name = name.split(":", 1)
            module = importlib.import_module(module_name)
            func = getattr(module, func_name)
            self._handlers[name] = func
            return func
        raise NotFoundError(f"Unknown handler: {name}")


class ActionRunner:
    PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")

    def __init__(self, runtime: "FakeHomeAssistantRuntime") -> None:
        self.runtime = runtime

    def _lookup(self, source_entity: EntityDefinition, payload: dict[str, Any], token: str) -> Any:
        current: Any
        if token == "entity_id":
            return source_entity.entity_id
        if token.startswith("links."):
            current = source_entity.links
            parts = token.split(".")[1:]
        elif token.startswith("payload."):
            current = payload
            parts = token.split(".")[1:]
        elif token.startswith("attributes."):
            current = source_entity.attributes
            parts = token.split(".")[1:]
        else:
            raise FakeHomeAssistantError(f"Unsupported placeholder: {token}")
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
        return current

    def _render(self, value: Any, source_entity: EntityDefinition, payload: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {key: self._render(item, source_entity, payload) for key, item in value.items()}
        if isinstance(value, list):
            return [self._render(item, source_entity, payload) for item in value]
        if not isinstance(value, str):
            return value

        full = self.PLACEHOLDER_RE.fullmatch(value)
        if full:
            return self._lookup(source_entity, payload, full.group(1))

        def replace_token(match: re.Match[str]) -> str:
            resolved = self._lookup(source_entity, payload, match.group(1))
            return "" if resolved is None else str(resolved)

        return self.PLACEHOLDER_RE.sub(replace_token, value)

    def run(self, source_entity: EntityDefinition, trigger: str, payload: dict[str, Any], context: ContextModel) -> list[str]:
        changed: list[str] = []
        for action in source_entity.actions.get(trigger, []):
            action_type = action.get("type")
            if action_type == "call_service":
                data = self._render(action.get("data", {}), source_entity, payload)
                result = self.runtime.service_engine.call_service(
                    domain=action["domain"],
                    service=action["service"],
                    payload=data,
                    return_response=bool(action.get("return_response", False)),
                    context=context,
                )
                changed.extend([state.entity_id for state in result.changed_states])
            elif action_type == "set_state":
                entity_id = self._render(action.get("entity_id", "${entity_id}"), source_entity, payload)
                state = self._render(action.get("state"), source_entity, payload)
                attributes = self._render(action.get("attributes", {}), source_entity, payload)
                self.runtime.set_state(entity_id, state=state, attributes=attributes, context=context)
                changed.append(entity_id)
            elif action_type == "fire_event":
                event_type = self._render(action["event_type"], source_entity, payload)
                event_data = self._render(action.get("event_data", {}), source_entity, payload)
                self.runtime.event_bus.fire(event_type, event_data, context=context)
            else:
                raise FakeHomeAssistantError(f"Unknown action type: {action_type}")
        return changed


class ServiceEngine:
    def __init__(self, runtime: "FakeHomeAssistantRuntime") -> None:
        self.runtime = runtime

    def _payload_entity_ids(self, payload: dict[str, Any]) -> list[str]:
        entity_value = payload.get("entity_id")
        if entity_value is None:
            return []
        if isinstance(entity_value, str):
            return [entity_value]
        if isinstance(entity_value, list):
            return [str(item) for item in entity_value]
        raise FakeHomeAssistantError("entity_id must be a string or list of strings")

    def _validate_payload(self, service_def: ServiceDefinition, payload: dict[str, Any]) -> None:
        for field_name, field_def in service_def.fields.items():
            if field_def.required and field_name not in payload:
                raise FakeHomeAssistantError(f"Missing required field: {field_name}")

    def call_service(
        self,
        *,
        domain: str,
        service: str,
        payload: dict[str, Any] | None = None,
        return_response: bool = False,
        context: ContextModel | None = None,
    ) -> ServiceCallResponse:
        service_def = self.runtime.registry.get_service(domain, service)
        if return_response and not service_def.supports_response:
            raise FakeHomeAssistantError(f"Service {domain}.{service} does not support return_response")
        payload = payload or {}
        self._validate_payload(service_def, payload)
        call_context = context or self.runtime.state_store.new_context()
        target_entity_ids = self._payload_entity_ids(payload)
        self.runtime.event_bus.fire(
            "call_service",
            {"domain": domain, "service": service, "service_data": payload},
            context=call_context,
        )
        fault_mode = self.runtime.test_env_manager.evaluate_fault(
            domain=domain,
            service=service,
            target_entity_ids=target_entity_ids,
        )
        if fault_mode == "one_shot_network_error":
            raise ServiceUnavailableError(f"Simulated network error for {domain}.{service}")
        if fault_mode == "fake_success":
            return ServiceCallResponse(changed_states=[], service_response=None)
        handler = self.runtime.handlers.resolve(service_def.handler)
        result = handler(
            ServiceExecutionContext(
                runtime=self.runtime,
                service=service_def,
                payload=payload,
                target_entity_ids=target_entity_ids,
                context=call_context,
                return_response=return_response,
            )
        )
        changed_states = [self.runtime.state_store.get(entity_id) for entity_id in dict.fromkeys(result.changed_entity_ids)]
        return ServiceCallResponse(changed_states=changed_states, service_response=result.response)


def _single_entity(ctx: ServiceExecutionContext) -> EntityDefinition:
    if len(ctx.target_entity_ids) != 1:
        raise FakeHomeAssistantError("This service requires exactly one entity_id")
    return ctx.runtime.registry.get_entity(ctx.target_entity_ids[0])


def _track_update(
    ctx: ServiceExecutionContext,
    entity_id: str,
    state: Any | None,
    attributes: dict[str, Any] | None = None,
) -> HandlerResult:
    ctx.runtime.set_state(entity_id, state=state, attributes=attributes or {}, context=ctx.context)
    return HandlerResult(changed_entity_ids=[entity_id])


def _brightness_from_payload(current: int, payload: dict[str, Any]) -> int:
    if "brightness" in payload:
        return max(0, min(255, int(payload["brightness"])))
    if "brightness_pct" in payload:
        pct = max(0, min(100, int(payload["brightness_pct"])))
        return int(round(pct * 255 / 100))
    if "brightness_step_pct" in payload:
        base_pct = int(round(current * 100 / 255))
        pct = max(0, min(100, base_pct + int(payload["brightness_step_pct"])))
        return int(round(pct * 255 / 100))
    return current


def register_builtin_handlers(registry: HandlerRegistry) -> None:
    def switch_turn_on(ctx: ServiceExecutionContext) -> HandlerResult:
        return _track_update(ctx, _single_entity(ctx).entity_id, "on")

    def switch_turn_off(ctx: ServiceExecutionContext) -> HandlerResult:
        return _track_update(ctx, _single_entity(ctx).entity_id, "off")

    def switch_toggle(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        current_state = ctx.runtime.state_store.get(entity_id).state
        return _track_update(ctx, entity_id, "off" if current_state == "on" else "on")

    def light_turn_on(ctx: ServiceExecutionContext) -> HandlerResult:
        entity = _single_entity(ctx)
        current = ctx.runtime.state_store.get(entity.entity_id)
        attributes = dict(current.attributes)
        if any(key in ctx.payload for key in ("brightness", "brightness_pct", "brightness_step_pct")) or "brightness" in attributes:
            attributes["brightness"] = _brightness_from_payload(int(attributes.get("brightness", 0)), ctx.payload)
        if "color_temp_kelvin" in ctx.payload:
            kelvin = int(ctx.payload["color_temp_kelvin"])
            attributes["color_temp_kelvin"] = kelvin
            attributes["color_temp"] = int(1_000_000 / kelvin)
        elif "color_temp" in ctx.payload:
            mired = int(ctx.payload["color_temp"])
            attributes["color_temp"] = mired
            attributes["color_temp_kelvin"] = int(1_000_000 / mired)
        if "effect" in ctx.payload:
            effect_list = attributes.get("effect_list", [])
            effect = ctx.payload["effect"]
            if effect_list and effect not in effect_list:
                raise FakeHomeAssistantError(f"Invalid effect: {effect}")
            attributes["effect"] = effect
        next_state = "off" if attributes.get("brightness") == 0 else "on"
        ctx.runtime.set_state(entity.entity_id, state=next_state, attributes=attributes, context=ctx.context)
        changed = [entity.entity_id]
        changed.extend(ctx.runtime.actions.run(entity, "on_turn_on", ctx.payload, ctx.context))
        return HandlerResult(changed_entity_ids=changed)

    def light_turn_off(ctx: ServiceExecutionContext) -> HandlerResult:
        entity = _single_entity(ctx)
        ctx.runtime.set_state(entity.entity_id, state="off", attributes={}, context=ctx.context)
        changed = [entity.entity_id]
        changed.extend(ctx.runtime.actions.run(entity, "on_turn_off", ctx.payload, ctx.context))
        return HandlerResult(changed_entity_ids=changed)

    def light_toggle(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        current_state = ctx.runtime.state_store.get(entity_id).state
        if current_state == "on":
            return light_turn_off(ctx)
        return light_turn_on(ctx)

    def climate_set_hvac_mode(ctx: ServiceExecutionContext) -> HandlerResult:
        entity = _single_entity(ctx)
        if "hvac_mode" not in ctx.payload:
            raise FakeHomeAssistantError("Missing required field: hvac_mode")
        hvac_mode = str(ctx.payload["hvac_mode"])
        current = ctx.runtime.state_store.get(entity.entity_id)
        attributes = dict(current.attributes)
        hvac_modes = attributes.get("hvac_modes")
        if isinstance(hvac_modes, list) and hvac_modes and hvac_mode not in hvac_modes:
            raise FakeHomeAssistantError(f"Invalid hvac_mode: {hvac_mode}")
        attributes["hvac_mode"] = hvac_mode
        ctx.runtime.set_state(entity.entity_id, state=hvac_mode, attributes=attributes, context=ctx.context)
        return HandlerResult(changed_entity_ids=[entity.entity_id])

    def climate_set_temperature(ctx: ServiceExecutionContext) -> HandlerResult:
        entity = _single_entity(ctx)
        if "temperature" not in ctx.payload:
            raise FakeHomeAssistantError("Missing required field: temperature")
        target_temperature = float(ctx.payload["temperature"])
        current = ctx.runtime.state_store.get(entity.entity_id)
        attributes = dict(current.attributes)
        attributes["temperature"] = target_temperature
        if "current_temperature" in attributes:
            attributes["current_temperature"] = target_temperature
        if "hvac_mode" in ctx.payload:
            hvac_mode = str(ctx.payload["hvac_mode"])
            hvac_modes = attributes.get("hvac_modes")
            if isinstance(hvac_modes, list) and hvac_modes and hvac_mode not in hvac_modes:
                raise FakeHomeAssistantError(f"Invalid hvac_mode: {hvac_mode}")
            attributes["hvac_mode"] = hvac_mode
        next_state = str(attributes.get("hvac_mode", current.state))
        ctx.runtime.set_state(entity.entity_id, state=next_state, attributes=attributes, context=ctx.context)
        changed = [entity.entity_id]
        changed.extend(
            ctx.runtime.test_env_manager.apply_climate_temperature_links(
                source_entity=entity,
                temperature=target_temperature,
                context=ctx.context,
            )
        )
        return HandlerResult(changed_entity_ids=changed)

    def number_set_value(ctx: ServiceExecutionContext) -> HandlerResult:
        entity = _single_entity(ctx)
        current = ctx.runtime.state_store.get(entity.entity_id)
        attributes = dict(current.attributes)
        value = float(ctx.payload["value"])
        min_value = float(attributes.get("min", entity.service_profile.get("min", value)))
        max_value = float(attributes.get("max", entity.service_profile.get("max", value)))
        if not min_value <= value <= max_value:
            raise FakeHomeAssistantError(f"value must be between {min_value} and {max_value}")
        if value.is_integer():
            value = int(value)
        ctx.runtime.set_state(entity.entity_id, state=value, attributes=attributes, context=ctx.context)
        return HandlerResult(changed_entity_ids=[entity.entity_id])

    def text_set_value(ctx: ServiceExecutionContext) -> HandlerResult:
        entity = _single_entity(ctx)
        current = ctx.runtime.state_store.get(entity.entity_id)
        attributes = dict(current.attributes)
        value = str(ctx.payload["value"])
        min_length = int(attributes.get("min", 0))
        max_length = int(attributes.get("max", max(255, len(value))))
        if not min_length <= len(value) <= max_length:
            raise FakeHomeAssistantError(f"value length must be between {min_length} and {max_length}")
        pattern = entity.service_profile.get("pattern") or attributes.get("pattern")
        if pattern and re.fullmatch(pattern, value) is None:
            raise FakeHomeAssistantError(f"value does not match pattern: {pattern}")
        ctx.runtime.set_state(entity.entity_id, state=value, attributes=attributes, context=ctx.context)
        return HandlerResult(changed_entity_ids=[entity.entity_id])

    def select_first(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        options = ctx.runtime.state_store.get(entity_id).attributes.get("options", [])
        return _track_update(ctx, entity_id, options[0])

    def select_last(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        options = ctx.runtime.state_store.get(entity_id).attributes.get("options", [])
        return _track_update(ctx, entity_id, options[-1])

    def select_next(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        state = ctx.runtime.state_store.get(entity_id)
        options = state.attributes.get("options", [])
        idx = options.index(state.state)
        next_value = state.state
        if idx < len(options) - 1:
            next_value = options[idx + 1]
        elif ctx.payload.get("cycle", True):
            next_value = options[0]
        return _track_update(ctx, entity_id, next_value)

    def select_previous(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        state = ctx.runtime.state_store.get(entity_id)
        options = state.attributes.get("options", [])
        idx = options.index(state.state)
        next_value = state.state
        if idx > 0:
            next_value = options[idx - 1]
        elif ctx.payload.get("cycle", True):
            next_value = options[-1]
        return _track_update(ctx, entity_id, next_value)

    def select_option(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        state = ctx.runtime.state_store.get(entity_id)
        option = ctx.payload["option"]
        options = state.attributes.get("options", [])
        if option not in options:
            raise FakeHomeAssistantError(f"Invalid option: {option}")
        return _track_update(ctx, entity_id, option)

    def button_press(ctx: ServiceExecutionContext) -> HandlerResult:
        entity = _single_entity(ctx)
        timestamp = datetime.now(tz=ZoneInfo(ctx.runtime.settings.timezone)).isoformat()
        ctx.runtime.set_state(entity.entity_id, state=timestamp, attributes={}, context=ctx.context)
        changed = [entity.entity_id]
        changed.extend(ctx.runtime.actions.run(entity, "on_press", ctx.payload, ctx.context))
        return HandlerResult(changed_entity_ids=changed)

    def volume_set(ctx: ServiceExecutionContext) -> HandlerResult:
        entity = _single_entity(ctx)
        state = ctx.runtime.state_store.get(entity.entity_id)
        attributes = dict(state.attributes)
        volume = float(ctx.payload["volume_level"])
        if not 0.0 <= volume <= 1.0:
            raise FakeHomeAssistantError("volume_level must be between 0.0 and 1.0")
        attributes["volume_level"] = volume
        ctx.runtime.set_state(entity.entity_id, state=state.state, attributes=attributes, context=ctx.context)
        return HandlerResult(changed_entity_ids=[entity.entity_id])

    def volume_up(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        current = float(ctx.runtime.state_store.get(entity_id).attributes.get("volume_level", 0.0))
        next_ctx = ServiceExecutionContext(
            runtime=ctx.runtime,
            service=ctx.service,
            payload={"entity_id": entity_id, "volume_level": min(1.0, current + 0.1)},
            target_entity_ids=[entity_id],
            context=ctx.context,
            return_response=ctx.return_response,
        )
        return volume_set(next_ctx)

    def volume_down(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        current = float(ctx.runtime.state_store.get(entity_id).attributes.get("volume_level", 0.0))
        next_ctx = ServiceExecutionContext(
            runtime=ctx.runtime,
            service=ctx.service,
            payload={"entity_id": entity_id, "volume_level": max(0.0, current - 0.1)},
            target_entity_ids=[entity_id],
            context=ctx.context,
            return_response=ctx.return_response,
        )
        return volume_set(next_ctx)

    def volume_mute(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        current = ctx.runtime.state_store.get(entity_id)
        attributes = dict(current.attributes)
        attributes["is_volume_muted"] = bool(ctx.payload["is_volume_muted"])
        ctx.runtime.set_state(entity_id, state=current.state, attributes=attributes, context=ctx.context)
        return HandlerResult(changed_entity_ids=[entity_id])

    def media_play(ctx: ServiceExecutionContext) -> HandlerResult:
        entity = _single_entity(ctx)
        ctx.runtime.set_state(entity.entity_id, state="playing", attributes={}, context=ctx.context)
        changed = [entity.entity_id]
        changed.extend(ctx.runtime.actions.run(entity, "on_media_play", ctx.payload, ctx.context))
        return HandlerResult(changed_entity_ids=changed)

    def media_pause(ctx: ServiceExecutionContext) -> HandlerResult:
        return _track_update(ctx, _single_entity(ctx).entity_id, "paused")

    def media_play_pause(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        current_state = ctx.runtime.state_store.get(entity_id).state
        return _track_update(ctx, entity_id, "paused" if current_state == "playing" else "playing")

    def media_stop(ctx: ServiceExecutionContext) -> HandlerResult:
        return _track_update(ctx, _single_entity(ctx).entity_id, "stopped")

    def media_previous_track(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        state = ctx.runtime.state_store.get(entity_id)
        attributes = dict(state.attributes)
        attributes["last_track_action"] = "previous"
        ctx.runtime.set_state(entity_id, state=state.state, attributes=attributes, context=ctx.context)
        return HandlerResult(changed_entity_ids=[entity_id], response={"track_action": "previous"})

    def media_next_track(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        state = ctx.runtime.state_store.get(entity_id)
        attributes = dict(state.attributes)
        attributes["last_track_action"] = "next"
        ctx.runtime.set_state(entity_id, state=state.state, attributes=attributes, context=ctx.context)
        return HandlerResult(changed_entity_ids=[entity_id], response={"track_action": "next"})

    def notify_send_message(ctx: ServiceExecutionContext) -> HandlerResult:
        entity_id = _single_entity(ctx).entity_id
        state = ctx.runtime.state_store.get(entity_id)
        attributes = dict(state.attributes)
        attributes["last_message"] = ctx.payload["message"]
        if "data" in ctx.payload:
            attributes["last_message_data"] = ctx.payload["data"]
        timestamp = datetime.now(tz=ZoneInfo(ctx.runtime.settings.timezone)).isoformat()
        ctx.runtime.set_state(entity_id, state=timestamp, attributes=attributes, context=ctx.context)
        return HandlerResult(changed_entity_ids=[entity_id])

    def homeassistant_turn_on(ctx: ServiceExecutionContext) -> HandlerResult:
        changed: list[str] = []
        for entity_id in ctx.target_entity_ids:
            domain = entity_id.split(".", 1)[0]
            result = ctx.runtime.service_engine.call_service(
                domain=domain,
                service="turn_on",
                payload={"entity_id": entity_id},
                context=ctx.context,
            )
            changed.extend([state.entity_id for state in result.changed_states])
        return HandlerResult(changed_entity_ids=changed)

    def homeassistant_turn_off(ctx: ServiceExecutionContext) -> HandlerResult:
        changed: list[str] = []
        for entity_id in ctx.target_entity_ids:
            domain = entity_id.split(".", 1)[0]
            result = ctx.runtime.service_engine.call_service(
                domain=domain,
                service="turn_off",
                payload={"entity_id": entity_id},
                context=ctx.context,
            )
            changed.extend([state.entity_id for state in result.changed_states])
        return HandlerResult(changed_entity_ids=changed)

    def homeassistant_toggle(ctx: ServiceExecutionContext) -> HandlerResult:
        changed: list[str] = []
        for entity_id in ctx.target_entity_ids:
            domain = entity_id.split(".", 1)[0]
            result = ctx.runtime.service_engine.call_service(
                domain=domain,
                service="toggle",
                payload={"entity_id": entity_id},
                context=ctx.context,
            )
            changed.extend([state.entity_id for state in result.changed_states])
        return HandlerResult(changed_entity_ids=changed)

    def update_entity(ctx: ServiceExecutionContext) -> HandlerResult:
        changed: list[str] = []
        for entity_id in ctx.target_entity_ids:
            current = ctx.runtime.state_store.get(entity_id)
            ctx.runtime.set_state(entity_id, state=current.state, attributes=current.attributes, context=ctx.context)
            changed.append(entity_id)
        return HandlerResult(changed_entity_ids=changed)

    def save_persistent_states(ctx: ServiceExecutionContext) -> HandlerResult:
        ctx.runtime.persist_all()
        return HandlerResult(response={"saved": True, "states": len(ctx.runtime.state_store.states)})

    def not_implemented(ctx: ServiceExecutionContext) -> HandlerResult:
        raise FakeHomeAssistantError(f"Service handler not implemented for {ctx.service.key}")

    for name, func in {
        "builtin:switch.turn_on": switch_turn_on,
        "builtin:switch.turn_off": switch_turn_off,
        "builtin:switch.toggle": switch_toggle,
        "builtin:light.turn_on": light_turn_on,
        "builtin:light.turn_off": light_turn_off,
        "builtin:light.toggle": light_toggle,
        "builtin:climate.set_hvac_mode": climate_set_hvac_mode,
        "builtin:climate.set_temperature": climate_set_temperature,
        "builtin:number.set_value": number_set_value,
        "builtin:text.set_value": text_set_value,
        "builtin:select.select_first": select_first,
        "builtin:select.select_last": select_last,
        "builtin:select.select_next": select_next,
        "builtin:select.select_previous": select_previous,
        "builtin:select.select_option": select_option,
        "builtin:button.press": button_press,
        "builtin:media_player.volume_set": volume_set,
        "builtin:media_player.volume_up": volume_up,
        "builtin:media_player.volume_down": volume_down,
        "builtin:media_player.volume_mute": volume_mute,
        "builtin:media_player.media_play": media_play,
        "builtin:media_player.media_pause": media_pause,
        "builtin:media_player.media_play_pause": media_play_pause,
        "builtin:media_player.media_stop": media_stop,
        "builtin:media_player.media_previous_track": media_previous_track,
        "builtin:media_player.media_next_track": media_next_track,
        "builtin:notify.send_message": notify_send_message,
        "builtin:homeassistant.turn_on": homeassistant_turn_on,
        "builtin:homeassistant.turn_off": homeassistant_turn_off,
        "builtin:homeassistant.toggle": homeassistant_toggle,
        "builtin:homeassistant.update_entity": update_entity,
        "builtin:homeassistant.save_persistent_states": save_persistent_states,
        "builtin:service.not_implemented": not_implemented,
    }.items():
        registry.register(name, func)


class FakeHomeAssistantRuntime:
    def __init__(self, settings: Settings, storage: StorageManager) -> None:
        self.settings = settings
        self.storage = storage
        self.registry = RegistryStore(storage)
        self.state_store = StateStore(settings, storage, self.registry)
        self.event_bus = EventBus(settings, storage)
        self.handlers = HandlerRegistry()
        register_builtin_handlers(self.handlers)
        self.actions = ActionRunner(self)
        self.service_engine = ServiceEngine(self)
        self.test_env_manager = TestEnvManager(self, settings.service_seed_root.parent / "test_envs")

    def reload(self) -> None:
        self.registry.reload()
        self._reconcile_builtin_service_handlers()
        self.state_store.reload()
        self.event_bus.reload()
        self.test_env_manager.reload_definitions()

    def persist_all(self) -> None:
        self.state_store.persist()
        self.storage.write_events(self.event_bus.events)

    def _reconcile_builtin_service_handlers(self) -> None:
        for key, builtin_handler in BUILTIN_SERVICE_HANDLERS.items():
            service = self.registry.services.get(key)
            if service is None:
                continue
            if service.handler != "builtin:service.not_implemented":
                continue
            self.registry.save_service(service.model_copy(update={"handler": builtin_handler}))

    @staticmethod
    def _copy_model_map[T: BaseModel](items: dict[str, T]) -> dict[str, T]:
        return {key: value.model_copy(deep=True) for key, value in items.items()}

    @staticmethod
    def _copy_model_list[T: BaseModel](items: list[T]) -> list[T]:
        return [item.model_copy(deep=True) for item in items]

    def _reset_runtime_data(self, *, clear_services: bool) -> None:
        self.registry.devices = {}
        self.registry.entities = {}
        self.state_store.states = {}
        self.event_bus.events = []
        self.storage.clear_data_files(self.storage.devices_dir)
        self.storage.clear_data_files(self.storage.entities_dir)
        if clear_services:
            self.registry.services = {}
            self.storage.clear_data_files(self.storage.services_dir)
        self.storage.write_states({})
        self.storage.write_events([])

    def apply_test_environment(self, env: TestEnvironmentDefinition) -> None:
        self._reset_runtime_data(clear_services=False)
        for device in env.devices:
            self.registry.save_device(device.model_copy(deep=True))
        for entity in env.entities:
            entity_copy = entity.model_copy(deep=True)
            self.registry.save_entity(entity_copy)
            self.state_store.ensure_entity(entity_copy)
        for initial in env.initial_states:
            self.state_store.upsert(
                initial.entity_id,
                state=initial.state,
                attributes=initial.attributes,
                merge_attributes=True,
                context=self.state_store.new_context(),
                persist=False,
            )
        self.state_store.persist()
        self.storage.write_events([])

    def restore_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        self._reset_runtime_data(clear_services=True)
        for service in snapshot.services.values():
            self.registry.save_service(service.model_copy(deep=True))
        for device in snapshot.devices.values():
            self.registry.save_device(device.model_copy(deep=True))
        for entity in snapshot.entities.values():
            self.registry.save_entity(entity.model_copy(deep=True))
        self.state_store.states = self._copy_model_map(snapshot.states)
        self.state_store.persist()
        self.event_bus.events = self._copy_model_list(snapshot.events)
        self.storage.write_events(self.event_bus.events)

    def set_state(
        self,
        entity_id: str,
        *,
        state: Any | None,
        attributes: dict[str, Any] | None,
        context: ContextModel,
    ) -> StateRecord:
        before = self.state_store.states.get(entity_id)
        after = self.state_store.upsert(
            entity_id,
            state=state,
            attributes=attributes,
            merge_attributes=True,
            context=context,
            persist=True,
        )
        self.event_bus.fire(
            "state_changed",
            {
                "entity_id": entity_id,
                "old_state": before.model_dump(mode="json") if before else None,
                "new_state": after.model_dump(mode="json"),
            },
            context=context,
        )
        return after
