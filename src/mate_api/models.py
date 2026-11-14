from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field, field_validator
from pydantic.types import StringConstraints


NonEmptyStr = Annotated[str, StringConstraints(min_length=1, max_length=160, strip_whitespace=True)]
TitleStr = Annotated[str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)]
ChatMessageStr = Annotated[str, StringConstraints(min_length=1, max_length=500, strip_whitespace=True)]
FactKeyStr = Annotated[str, StringConstraints(min_length=1, max_length=60, strip_whitespace=True)]
FactValueStr = Annotated[str, StringConstraints(min_length=1, max_length=300, strip_whitespace=True)]


class Tone(str, Enum):
    AMABLE = "amable"
    FIRME = "firme"


class Category(str, Enum):
    ESTUDIO = "estudio"
    GYM = "gym"
    PERSONAL = "personal"
    RECORDATORIO = "recordatorio"


class AvatarState(str, Enum):
    NEUTRO = "neutro"
    HABLANDO = "hablando"
    CONTENTO = "contento"
    SERIO = "serio"


class SessionBase(BaseModel):
    session_id: str
    ics_token: str
    tone: Tone = Tone.AMABLE
    created_at_utc: datetime
    expires_at_utc: datetime


class SessionCreate(SessionBase):
    pass


class SessionRead(SessionBase):
    pass


class TaskBase(BaseModel):
    title: TitleStr
    due_time: Optional[str] = Field(default=None, pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")


class TaskCreate(TaskBase):
    pass


class TaskRead(TaskBase):
    id: int
    session_id: str
    completed: bool = False
    created_at_utc: datetime


class HabitBase(BaseModel):
    name: TitleStr
    target_per_week: int = Field(default=3, ge=1, le=7)
    preferred_time: str = Field(default="19:00", pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")


class HabitCreate(HabitBase):
    pass


class HabitRead(HabitBase):
    id: int
    session_id: str
    done_this_week: int = 0
    last_checkin_date: Optional[str] = None
    created_at_utc: datetime


class CalendarBlockBase(BaseModel):
    title: TitleStr
    start_utc: datetime
    end_utc: datetime
    category: Category = Category.ESTUDIO


class CalendarBlockCreate(CalendarBlockBase):
    pass


class CalendarBlockRead(CalendarBlockBase):
    id: int
    session_id: str


class MemoryBase(BaseModel):
    fact_key: FactKeyStr
    fact_value: FactValueStr
    event_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class MemoryCreate(MemoryBase):
    pass


class MemoryRead(MemoryBase):
    id: int
    session_id: str
    created_at_utc: datetime


class ToolArguments(BaseModel):
    model_config = {"extra": "forbid"}


class CrearTareaArgs(ToolArguments):
    titulo: TitleStr
    hora_sugerida: Optional[str] = Field(default=None, pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")


class ListarHoyArgs(ToolArguments):
    pass


class CrearHabitoArgs(ToolArguments):
    nombre: TitleStr
    veces_por_semana: int = Field(default=3, ge=1, le=7)
    hora_habitual: str = Field(default="19:00", pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")


class RegistrarCumplimientoArgs(ToolArguments):
    tipo: Literal["tarea", "habito"]
    item_id: int = Field(ge=1)


class BuscarHuecoArgs(ToolArguments):
    duracion_minutos: int = Field(default=60, ge=15, le=480)
    fecha_iso: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ReservarBloqueArgs(ToolArguments):
    titulo: TitleStr
    inicio_iso: str
    fin_iso: str
    categoria: Category = Category.ESTUDIO

    @field_validator("fin_iso")
    @classmethod
    def validate_end_after_start(cls, v: str, info) -> str:
        if "inicio_iso" in info.data and v <= info.data["inicio_iso"]:
            raise ValueError("fin_iso debe ser posterior a inicio_iso")
        return v


class CrearRecordatorioArgs(ToolArguments):
    titulo: TitleStr
    fecha_iso: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    hora: str = Field(default="09:00", pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")


class GuardarDatoArgs(ToolArguments):
    clave: FactKeyStr
    valor: FactValueStr
    fecha_evento: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ChatRequest(BaseModel):
    message: ChatMessageStr


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class ToolResult(BaseModel):
    name: str
    success: bool
    result: Optional[dict] = None


class ToolExecuted(BaseModel):
    name: str
    args: dict
    result: dict


class ChatResponse(BaseModel):
    reply: str
    avatar_state: AvatarState
    tools_executed: list[ToolResult] = Field(default_factory=list)
    state_snapshot: dict = Field(default_factory=dict)
    is_degraded: bool = False


class ToneRequest(BaseModel):
    tone: Tone


class ToneResponse(BaseModel):
    tone: Tone


class ProactiveTriggerRequest(BaseModel):
    scenario: Literal["gym", "estudio", "cumple"]


class ProactiveTriggerResponse(BaseModel):
    triggered: bool
    message: str


class CalendarEvent(BaseModel):
    id: int
    category: Category
    title: str
    time: str


class SessionStateSnapshot(BaseModel):
    tasks: list[TaskRead] = Field(default_factory=list)
    habits: list[HabitRead] = Field(default_factory=list)
    blocks: list[CalendarBlockRead] = Field(default_factory=list)
    memories: list[MemoryRead] = Field(default_factory=list)
    tone: Tone = Tone.AMABLE
    quota_remaining: int = 0
    ics_url: str = ""


class CompleteItemRequest(BaseModel):
    pass


class CompleteItemResponse(BaseModel):
    completed: bool
    message: str
    avatar_state: AvatarState


class RevokeTokenResponse(BaseModel):
    new_ics_token: str
    new_ics_url: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["connected", "disconnected"]
    redis: Literal["connected", "disconnected", "not_configured"]