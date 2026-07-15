from pydantic import BaseModel, Field


class MoveRequest(BaseModel):
    direction: str = Field(pattern="^(forward|backward|left|right|rotate|stop)$")
    speed: float = Field(default=0.65, ge=0.0, le=1.0)


class AskRequest(BaseModel):
    question: str
    student_name: str | None = None


class AskResponse(BaseModel):
    answer: str
    audio_url: str | None = None
