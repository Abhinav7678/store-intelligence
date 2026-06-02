from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class BoundingBox(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Detection(BaseModel):
    id: Optional[str] = None
    label: str
    score: float = Field(..., ge=0.0, le=1.0)
    bbox: BoundingBox


class Track(BaseModel):
    track_id: str
    detections: List[Detection]
    last_seen: str


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None
    extra: Optional[Dict[str, Any]] = None


class Event(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: str
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: Optional[EventMetadata] = None

    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "uuid-v4",
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_ENTRY_01",
                "visitor_id": "VIS_c8a2f1",
                "event_type": "ZONE_DWELL",
                "timestamp": "2026-03-03T14:22:10Z",
                "zone_id": "SKINCARE",
                "dwell_ms": 8400,
                "is_staff": False,
                "confidence": 0.91,
                "metadata": {"queue_depth": None, "sku_zone": "MOISTURISER", "session_seq": 5}
            }
        }


class EventIn(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: str
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = 1.0
    metadata: EventMetadata = EventMetadata()


class IngestRequest(BaseModel):
    events: List[EventIn]


class IngestResponse(BaseModel):
    accepted: int
    duplicate: int
    rejected: int
    errors: List[dict] = []