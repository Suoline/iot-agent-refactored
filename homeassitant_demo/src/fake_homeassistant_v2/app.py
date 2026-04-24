from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .importer import bootstrap_runtime
from .models import (
    DeviceUpsertRequest,
    EntityDefinition,
    EntityUpsertRequest,
    InitEnvRequest,
    InitEnvResponse,
    ReloadResponse,
    RestoreOriginalEnvResponse,
)
from .runtime import ConflictError, FakeHomeAssistantError, FakeHomeAssistantRuntime, NotFoundError


def _auth_dependency_factory(runtime: FakeHomeAssistantRuntime):
    async def require_auth(authorization: str | None = Header(default=None)) -> None:
        token = runtime.settings.token
        if token is None:
            return
        expected = f"Bearer {token}"
        if authorization != expected:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    return require_auth


def _group_services(runtime: FakeHomeAssistantRuntime) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for service in runtime.registry.services.values():
        grouped.setdefault(service.domain, {})
        payload = service.model_dump(mode="json")
        payload.pop("domain")
        payload.pop("service")
        grouped[service.domain][service.service] = payload
    return [{"domain": domain, "services": services} for domain, services in sorted(grouped.items())]


def create_app(
    settings: Settings | None = None,
    runtime: FakeHomeAssistantRuntime | None = None,
    extra_handlers: dict[str, Any] | None = None,
) -> FastAPI:
    runtime = runtime or bootstrap_runtime(settings or get_settings(), extra_handlers=extra_handlers)
    require_auth = _auth_dependency_factory(runtime)
    app = FastAPI(title="Fake Home Assistant v2", version=runtime.settings.version)
    app.state.runtime = runtime

    @app.exception_handler(FakeHomeAssistantError)
    async def handle_fake_ha_error(_: Request, exc: FakeHomeAssistantError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.get("/")
    async def index() -> dict[str, str]:
        return {"message": "Fake Home Assistant v2"}

    @app.get("/api/", dependencies=[Depends(require_auth)])
    async def api_root() -> dict[str, str]:
        return {"message": "API running."}

    @app.get("/api/config", dependencies=[Depends(require_auth)])
    async def api_config() -> dict[str, Any]:
        return {
            "location_name": runtime.settings.location_name,
            "latitude": runtime.settings.latitude,
            "longitude": runtime.settings.longitude,
            "elevation": runtime.settings.elevation,
            "unit_system": {"name": runtime.settings.unit_system},
            "time_zone": runtime.settings.timezone,
            "version": runtime.settings.version,
        }

    @app.get("/api/events", dependencies=[Depends(require_auth)])
    async def api_events() -> list[dict[str, Any]]:
        return runtime.event_bus.available_event_types()

    @app.post("/api/events/{event_type}", dependencies=[Depends(require_auth)])
    async def post_event(event_type: str, request: Request) -> dict[str, Any]:
        payload = await request.json() if request.headers.get("content-length") else {}
        event = runtime.event_bus.fire(event_type, payload)
        return {"message": f"Event {event_type} fired.", "event": event.model_dump(mode="json")}

    @app.get("/api/services", dependencies=[Depends(require_auth)])
    async def api_services() -> list[dict[str, Any]]:
        return _group_services(runtime)

    @app.get("/api/states", dependencies=[Depends(require_auth)])
    async def api_states() -> list[dict[str, Any]]:
        return [state.model_dump(mode="json") for state in runtime.state_store.list()]

    @app.get("/api/states/{entity_id}", dependencies=[Depends(require_auth)])
    async def get_state(entity_id: str) -> dict[str, Any]:
        return runtime.state_store.get(entity_id).model_dump(mode="json")

    @app.post("/api/states/{entity_id}", dependencies=[Depends(require_auth)])
    async def post_state(entity_id: str, request: Request, response: Response) -> dict[str, Any]:
        payload = await request.json()
        existed = entity_id in runtime.state_store.states
        if entity_id not in runtime.registry.entities:
            domain, object_id = entity_id.split(".", 1)
            entity = EntityDefinition(
                entity_id=entity_id,
                domain=domain,
                object_id=object_id,
                platform="manual",
                state=payload.get("state", "unknown"),
                attributes=payload.get("attributes", {}),
                metadata={"ad_hoc": True},
            )
            runtime.registry.save_entity(entity)
        state = runtime.set_state(
            entity_id,
            state=payload.get("state"),
            attributes=payload.get("attributes", {}),
            context=runtime.state_store.new_context(),
        )
        response.status_code = status.HTTP_200_OK if existed else status.HTTP_201_CREATED
        return state.model_dump(mode="json")

    @app.delete("/api/states/{entity_id}", dependencies=[Depends(require_auth)])
    async def delete_state(entity_id: str) -> dict[str, Any]:
        state = runtime.state_store.delete(entity_id)
        return state.model_dump(mode="json")

    @app.post("/api/services/{domain}/{service}", dependencies=[Depends(require_auth)])
    async def call_service(domain: str, service: str, request: Request, return_response: bool = False) -> Any:
        payload = await request.json() if request.headers.get("content-length") else {}
        result = runtime.service_engine.call_service(
            domain=domain,
            service=service,
            payload=payload,
            return_response=return_response,
        )
        if return_response:
            return result.model_dump(mode="json")
        return [state.model_dump(mode="json") for state in result.changed_states]

    @app.put("/api/mock/devices/{device_id}", dependencies=[Depends(require_auth)])
    async def upsert_device(device_id: str, request_model: DeviceUpsertRequest) -> dict[str, Any]:
        if request_model.device.device_id != device_id:
            raise ConflictError("device_id in path does not match payload")
        entity_ids = set(request_model.device.entities)
        for entity in request_model.entities:
            entity_ids.add(entity.entity_id)
            if entity.device_id not in {None, device_id}:
                raise ConflictError("entity.device_id does not match device_id in path")
            runtime.registry.save_entity(entity.model_copy(update={"device_id": device_id}))
            runtime.state_store.ensure_entity(runtime.registry.get_entity(entity.entity_id))
        device = request_model.device.model_copy(update={"entities": sorted(entity_ids)})
        runtime.registry.save_device(device)
        runtime.state_store.persist()
        return {"device": device.model_dump(mode="json"), "entity_count": len(entity_ids)}

    @app.put("/api/mock/entities/{entity_id}", dependencies=[Depends(require_auth)])
    async def upsert_entity(entity_id: str, request_model: EntityUpsertRequest) -> dict[str, Any]:
        if request_model.entity.entity_id != entity_id:
            raise ConflictError("entity_id in path does not match payload")
        runtime.registry.save_entity(request_model.entity)
        runtime.state_store.ensure_entity(request_model.entity)
        runtime.state_store.persist()
        return request_model.entity.model_dump(mode="json")

    @app.post("/api/mock/reload", dependencies=[Depends(require_auth)], response_model=ReloadResponse)
    async def reload_runtime() -> ReloadResponse:
        runtime.reload()
        return ReloadResponse(
            status="reloaded",
            devices=len(runtime.registry.devices),
            entities=len(runtime.registry.entities),
            services=len(runtime.registry.services),
        )

    @app.post("/api/mock/init_env", dependencies=[Depends(require_auth)], response_model=InitEnvResponse)
    async def init_env(request_model: InitEnvRequest) -> InitEnvResponse:
        payload = runtime.test_env_manager.init_env(
            env_id=request_model.env_id,
            fault_mode=request_model.fault_mode,
        )
        return InitEnvResponse(**payload)

    @app.post("/api/mock/original_env", dependencies=[Depends(require_auth)], response_model=RestoreOriginalEnvResponse)
    async def restore_original_env() -> RestoreOriginalEnvResponse:
        payload = runtime.test_env_manager.restore_original_env()
        return RestoreOriginalEnvResponse(**payload)

    return app
