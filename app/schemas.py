"""
Pydantic schemas for the Store Intelligence API.
Accepts the actual challenge event formats: entry/exit, zone, and queue events.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class Event(BaseModel):
    """Flexible event model that accepts all 3 event shapes from the challenge data."""
    # Common
    event_type: str

    # Entry/Exit fields
    id_token: Optional[str] = None
    store_code: Optional[str] = None
    event_timestamp: Optional[str] = None
    is_staff: Optional[bool] = False
    gender_pred: Optional[str] = None
    age_pred: Optional[int] = None
    is_face_hidden: Optional[bool] = None
    group_id: Optional[str] = None
    group_size: Optional[int] = None

    # Zone fields
    track_id: Optional[Any] = None
    store_id: Optional[str] = None
    camera_id: Optional[str] = None
    zone_id: Optional[str] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    is_revenue_zone: Optional[str] = None
    event_time: Optional[str] = None
    zone_hotspot_x: Optional[float] = None
    zone_hotspot_y: Optional[float] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    age_bucket: Optional[str] = None

    # Queue/Billing fields
    queue_event_id: Optional[str] = None
    queue_join_ts: Optional[str] = None
    queue_served_ts: Optional[str] = None
    queue_exit_ts: Optional[str] = None
    wait_seconds: Optional[int] = None
    queue_position_at_join: Optional[int] = None
    abandoned: Optional[bool] = None

    # Legacy / internal fields (for backward compat)
    event_id: Optional[str] = None
    visitor_id: Optional[str] = None
    timestamp: Optional[str] = None
    dwell_ms: Optional[int] = 0
    confidence: Optional[float] = Field(default=1.0, ge=0.0, le=1.0)
    metadata: Optional[Dict[str, Any]] = None

    model_config = {"extra": "allow"}

    def get_store_id(self) -> str:
        return self.store_code or self.store_id or ""

    def get_visitor_id(self) -> str:
        return self.id_token or (str(self.track_id) if self.track_id is not None else "") or self.visitor_id or ""

    def get_timestamp(self) -> str:
        return self.event_timestamp or self.event_time or self.queue_join_ts or self.timestamp or ""

    def get_event_id(self) -> str:
        import uuid
        return self.event_id or self.queue_event_id or str(uuid.uuid4())

    def get_camera_id(self) -> str:
        return self.camera_id or "UNKNOWN"

    def get_is_staff(self) -> bool:
        return self.is_staff or False


class IngestRequest(BaseModel):
    events: List[Event]


class IngestResponse(BaseModel):
    status: str
    accepted: int
    duplicates_ignored: int
    rejected: List[dict] = []
    inserted_ids: List[str] = []