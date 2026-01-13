

from pydantic import BaseModel, Field
from typing import Optional

class EventResponse(BaseModel):

    id: int
    timestamp: str
    event_type: str
    source_ip: Optional[str] = None
    port: Optional[int] = None
    raw_event: str
    batch_id: Optional[int] = None

class DecisionResponse(BaseModel):

    id: int
    timestamp: str
    batch_id: int
    event_count: int
    verdict: str
    confidence: float
    reason: Optional[str] = None
    threat_ips: list[str] = []

class ConfigResponse(BaseModel):

    sensitivity: int = Field(ge=1, le=10)
    trusted_ports_manual: list[int] = []
    trusted_ports_dynamic: list[int] = []
    ignored_ports: str = ""
    ignored_ips: str = ""
    custom_prompt: str = ""
    llm_api_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: int = 10
    event_buffer: int = 5
    dry_run: bool = False

class ConfigUpdate(BaseModel):

    sensitivity: Optional[int] = Field(None, ge=1, le=10)
    trusted_ports_manual: Optional[list[int]] = None
    ignored_ports: Optional[str] = None
    ignored_ips: Optional[str] = None
    custom_prompt: Optional[str] = None
    llm_api_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_timeout: Optional[int] = Field(None, ge=1, le=60)
    event_buffer: Optional[int] = Field(None, ge=1, le=60)
    dry_run: Optional[bool] = None

class StatsResponse(BaseModel):

    total_events: int
    events_last_hour: int
    total_decisions: int
    blocks_today: int

class HealthResponse(BaseModel):

    status: str
    version: str
    database: str

class TestConnectionResponse(BaseModel):

    success: bool
    message: str

class LogResponse(BaseModel):

    id: int
    timestamp: str
    level: str
    source: str
    message: str
