from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


JsonDict = dict[str, Any]


class ContextModel(BaseModel):
    id: str
    parent_id: str | None = None
    user_id: str | None = None


class StateRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    entity_id: str
    state: Any
    attributes: JsonDict = Field(default_factory=dict)
    last_changed: datetime
    last_reported: datetime
    last_updated: datetime
    context: ContextModel


class EventRecord(BaseModel):
    event_type: str
    data: JsonDict = Field(default_factory=dict)
    time_fired: datetime
    context: ContextModel


class ServiceFieldDefinition(BaseModel):
    required: bool = False
    name: str | None = None
    description: str | None = None
    selector: JsonDict | None = None


class ServiceDefinition(BaseModel):
    domain: str
    service: str
    name: str
    description: str | None = None
    fields: dict[str, ServiceFieldDefinition] = Field(default_factory=dict)
    target: JsonDict | None = None
    handler: str
    supports_response: bool = False

    @property
    def key(self) -> str:
        return f"{self.domain}.{self.service}"


class DeviceDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    device_id: str
    name: str | None = None
    name_by_user: str | None = None
    area_id: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    model_id: str | None = None
    sw_version: str | None = None
    hw_version: str | None = None
    serial_number: str | None = None
    identifiers: list[list[str]] = Field(default_factory=list)
    connections: list[list[str]] = Field(default_factory=list)
    configuration_url: str | None = None
    via_device_id: str | None = None
    entities: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


class EntityDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    entity_id: str
    domain: str
    object_id: str
    unique_id: str | None = None
    device_id: str | None = None
    area_id: str | None = None
    platform: str = "mock"
    name: str | None = None
    original_name: str | None = None
    device_class: str | None = None
    entity_category: str | None = None
    hidden_by: str | None = None
    disabled_by: str | None = None
    supported_features: int = 0
    capabilities: JsonDict | None = None
    service_profile: JsonDict = Field(default_factory=dict)
    links: JsonDict = Field(default_factory=dict)
    actions: dict[str, list[JsonDict]] = Field(default_factory=dict)
    state: Any = "unknown"
    attributes: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)


class HandlerResult(BaseModel):
    changed_entity_ids: list[str] = Field(default_factory=list)
    response: Any = None


class ServiceCallResponse(BaseModel):
    changed_states: list[StateRecord]
    service_response: Any = None


class DeviceUpsertRequest(BaseModel):
    device: DeviceDefinition
    entities: list[EntityDefinition] = Field(default_factory=list)


class EntityUpsertRequest(BaseModel):
    entity: EntityDefinition


class ReloadResponse(BaseModel):
    status: str
    devices: int
    entities: int
    services: int


class InitEnvRequest(BaseModel):
    env_id: str
    fault_mode: str | None = None


class InitEnvResponse(BaseModel):
    status: str
    env_id: str
    active_fault_mode: str
    saved_original_snapshot: bool
    entity_count: int


class RestoreOriginalEnvResponse(BaseModel):
    status: str
    restored: bool
    entity_count: int
