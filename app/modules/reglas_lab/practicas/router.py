"""Rutas de prácticas: iniciar y finalizar la práctica de un laboratorio (análisis de sus cámaras y
sesión guardada), estado en curso y listado de prácticas registradas.

Las URL no cambian: todas cuelgan de /modules/reglas_lab (ver reglas_lab/router.py)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.bitacora import registrar
from app.core.permisos import allowed_workshop_ids
from app.modules.auth_db.dependencies import get_current_user
from app.modules.reglas_lab import models
from app.modules.reglas_lab.comun import ensure_can_manage, ensure_workshop_access, get_db

router = APIRouter()


# --------------------------------------------------------------------- monitoreo
def _workshop_for_monitoring(db: Session, workshop_id: int, current_user) -> models.Workshop:
    ensure_can_manage(current_user)
    workshop = db.query(models.Workshop).filter_by(id=workshop_id, is_active=True).first()
    if not workshop:
        raise HTTPException(status_code=404, detail='Taller no encontrado')
    ensure_workshop_access(workshop, current_user)
    return workshop


@router.post('/workshops/{workshop_id}/monitoring/start')
def start_monitoring(workshop_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Inicia la práctica: analiza todas las cámaras del taller aunque nadie mire el video.
    Las incidencias anteriores se conservan siempre: lo ocurrido en el laboratorio no se borra."""
    from app.modules.reglas_lab.practicas import monitoreo, sesiones
    workshop = _workshop_for_monitoring(db, workshop_id, current_user)
    already_open = sesiones.open_session_for(db, workshop_id) is not None
    session = sesiones.start_session(db, workshop, current_user.id)  # antes del análisis: las alertas ya la usan
    cameras = monitoreo.start_workshop(db, workshop_id)
    if not already_open:
        registrar(db, current_user, 'INICIAR_PRACTICA', 'practicas', f'{session.code} en {workshop.name}')
    return {'workshop_id': workshop_id, 'cameras': cameras,
            'session': _session_payload(db, session)}


@router.post('/workshops/{workshop_id}/monitoring/stop')
def stop_monitoring(workshop_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    from app.modules.reglas_lab.practicas import monitoreo, sesiones
    workshop = _workshop_for_monitoring(db, workshop_id, current_user)
    cameras = monitoreo.stop_workshop(db, workshop_id)
    session = sesiones.end_session(db, workshop_id, current_user.id)
    if session is not None:
        registrar(db, current_user, 'FINALIZAR_PRACTICA', 'practicas', f'{session.code} en {workshop.name}')
    return {'workshop_id': workshop_id, 'cameras': cameras,
            'session': _session_payload(db, session) if session else None}


@router.get('/workshops/{workshop_id}/monitoring')
def monitoring_status(workshop_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    from app.modules.reglas_lab.practicas import monitoreo
    workshop = db.query(models.Workshop).filter_by(id=workshop_id, is_active=True).first()
    if not workshop:
        raise HTTPException(status_code=404, detail='Taller no encontrado')
    ensure_workshop_access(workshop, current_user)
    from app.modules.reglas_lab.practicas.sesiones import open_session_for
    session = open_session_for(db, workshop_id)
    return {'workshop_id': workshop_id, 'active': monitoreo.is_active(workshop_id),
            'cameras': monitoreo.workshop_status(db, workshop_id),
            'session': _session_payload(db, session) if session else None}


def _session_payload(db: Session, session: models.PracticeSession) -> dict:
    from app.modules.reglas_lab.practicas import sesiones
    stats = sesiones.session_stats(db, [session.id])[session.id]
    return sesiones.session_response(session, stats, sesiones.user_names(db, [session]))


@router.get('/sessions')
def list_sessions(
    workshop_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Prácticas registradas (las más recientes primero), con sus alertas por estado.
    Con start/end: las que se solapan con ese periodo. El encargado ve solo su laboratorio."""
    from app.modules.reglas_lab.practicas import sesiones
    query = db.query(models.PracticeSession)
    allowed = allowed_workshop_ids(current_user)
    if allowed is not None:
        query = query.filter(models.PracticeSession.workshop_id.in_(allowed or {-1}))
    if workshop_id is not None:
        query = query.filter(models.PracticeSession.workshop_id == workshop_id)
    if start is not None:
        start_utc = start.astimezone(timezone.utc).replace(tzinfo=None) if start.tzinfo else start
        query = query.filter((models.PracticeSession.ended_at.is_(None)) | (models.PracticeSession.ended_at > start_utc))
    if end is not None:
        end_utc = end.astimezone(timezone.utc).replace(tzinfo=None) if end.tzinfo else end
        query = query.filter(models.PracticeSession.started_at < end_utc)
    rows = query.order_by(models.PracticeSession.started_at.desc()).limit(500).all()
    stats = sesiones.session_stats(db, [row.id for row in rows])
    names = sesiones.user_names(db, rows)
    return [sesiones.session_response(row, stats[row.id], names) for row in rows]
