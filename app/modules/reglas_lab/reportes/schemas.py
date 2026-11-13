"""Datos que reciben las rutas de reportes."""
from datetime import datetime

from pydantic import BaseModel, Field


class ReportEmit(BaseModel):
    """Emitir un reporte: mismo periodo y filtros que la vista previa, más el responsable SST."""
    start: datetime
    end: datetime
    workshop_id: int | None = None
    campus_id: int | None = None
    responsible: str = Field(default='', max_length=160)
