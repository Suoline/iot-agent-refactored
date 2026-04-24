from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .legacy_parser import parse_legacy_data
from .models import ServiceDefinition
from .runtime import BUILTIN_SERVICE_HANDLERS, FakeHomeAssistantRuntime, StorageManager


def _service_handler_name(key: str) -> str:
    return BUILTIN_SERVICE_HANDLERS.get(key, "builtin:service.not_implemented")


def _reconcile_builtin_service_handlers(storage: StorageManager) -> None:
    services = storage.load_services()
    for key, builtin_handler in BUILTIN_SERVICE_HANDLERS.items():
        service = services.get(key)
        if service is None:
            continue
        if service.handler != "builtin:service.not_implemented":
            continue
        storage.write_service(service.model_copy(update={"handler": builtin_handler}))


def import_legacy_data(legacy_root: Path, storage: StorageManager) -> None:
    legacy_data = parse_legacy_data(legacy_root)

    for device in legacy_data.devices:
        storage.write_device(device)

    for entity in legacy_data.entities:
        storage.write_entity(entity)

    storage.write_states(legacy_data.states)

    existing_services = storage.load_services()
    for domain_item in legacy_data.services_payload:
        domain = domain_item["domain"]
        for service_name, service_item in domain_item.get("services", {}).items():
            key = f"{domain}.{service_name}"
            existing = existing_services.get(key)
            merged = ServiceDefinition(
                domain=domain,
                service=service_name,
                name=service_item.get("name", existing.name if existing else service_name),
                description=service_item.get("description", existing.description if existing else None),
                fields=service_item.get("fields", existing.fields if existing else {}),
                target=service_item.get("target", existing.target if existing else None),
                handler=existing.handler if existing else _service_handler_name(key),
                supports_response=existing.supports_response if existing else False,
            )
            storage.write_service(merged)


def bootstrap_runtime(settings: Settings, extra_handlers: dict[str, Any] | None = None) -> FakeHomeAssistantRuntime:
    storage = StorageManager(settings.storage_root)
    storage.seed_services(settings.service_seed_root)
    if not any(storage.list_data_files(storage.entities_dir)) and settings.legacy_root is not None:
        import_legacy_data(settings.legacy_root, storage)
    _reconcile_builtin_service_handlers(storage)
    runtime = FakeHomeAssistantRuntime(settings, storage)
    runtime.reload()
    for name, func in (extra_handlers or {}).items():
        runtime.handlers.register(name, func)
    return runtime
