import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChatIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class ChatOut(BaseModel):
    id: uuid.UUID
    report_id: uuid.UUID
    sender_id: uuid.UUID
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}
