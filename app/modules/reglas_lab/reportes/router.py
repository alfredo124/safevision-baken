"""Rutas de reportes SST: vista previa de un periodo, emisión (queda guardado con su huella), listado de
reportes emitidos y verificación contra los datos actuales.

Las URL no cambian: todas cuelgan de /modules/reglas_lab (ver reglas_lab/router.py)."""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.bitacora import registrar
from app.core.permisos import allowed_workshop_ids
from app.modules.auth_db.dependencies import get_current_user
from app.modules.reglas_lab import models
from app.modules.reglas_lab.comun import as_utc, ensure_workshop_access, get_db
from app.modules.reglas_lab.reportes import schemas

router = APIRouter()


@router.get('/report')
def incident_report(
    start: datetime,
    end: datetime,
    workshop_id: int | None = None,
    campus_id: int | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Vista previa del reporte SST del periodo [start, end) con las incidencias reales. La web envía el
    inicio y el fin del día en su hora local (con zona). El encargado solo obtiene su laboratorio.
    No queda guardado: para entregarlo se emite con POST /reports."""
    from app.modules.reglas_lab.reportes.reporte import build_report

    scope, filters = _report_scope(db, current_user, start, end, workshop_id, campus_id)
    report = build_report(db, start, end, scope)
    report['filters'] = filters
    report['generated_by'] = current_user.full_name or current_user.username
    return report


def _report_scope(db: Session, current_user, start: datetime, end: datetime,
                  workshop_id: int | None, campus_id: int | None) -> tuple[set[int] | None, dict]:
    """Valida el periodo y calcula los laboratorios del reporte (None = todos) y los filtros con sus
    nombres. El encargado solo obtiene su laboratorio."""
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=422, detail='Las fechas deben incluir la zona horaria')
    if end <= start:
        raise HTTPException(status_code=422, detail='La fecha final debe ser posterior a la inicial')
    if (end - start).days > 366:
        raise HTTPException(status_code=422, detail='El periodo del reporte no puede pasar de un año')

    allowed = allowed_workshop_ids(current_user)
    scope = None if allowed is None else set(allowed)
    campus = None
    if campus_id is not None:
        campus = db.get(models.Campus, campus_id)
        if campus is None:
            raise HTTPException(status_code=404, detail='Local no encontrado')
        in_campus = {row.id for row in db.query(models.Workshop.id).filter(models.Workshop.campus_id == campus_id)}
        scope = in_campus if scope is None else scope & in_campus
    workshop = None
    if workshop_id is not None:
        workshop = db.get(models.Workshop, workshop_id)
        if workshop is None:
            raise HTTPException(status_code=404, detail='Taller no encontrado')
        ensure_workshop_access(workshop, current_user)
        scope = {workshop_id} if scope is None else scope & {workshop_id}

    return scope, {
        'workshop_id': workshop_id,
        'workshop_name': workshop.name if workshop else None,
        'campus_id': campus_id,
        'campus_name': campus.name if campus else None,
    }


def _issued_summary(row: models.IssuedReport) -> dict:
    return {
        'code': row.code,
        'start': as_utc(row.period_start),
        'end': as_utc(row.period_end),
        'filters': row.filters or {},
        'workshop_ids': row.workshop_ids,
        'responsible': row.responsible,
        'fingerprint': row.fingerprint,
        'summary': row.summary or {},
        'generated_by': row.generated_by_name,
        'generated_at': as_utc(row.generated_at),
    }


def _can_see_report(row: models.IssuedReport, current_user) -> bool:
    """El administrador ve todos; el encargado, los que solo incluyen su laboratorio."""
    allowed = allowed_workshop_ids(current_user)
    return allowed is None or set(row.workshop_ids or []) <= set(allowed)


@router.post('/reports', status_code=status.HTTP_201_CREATED)
def emit_report(payload: schemas.ReportEmit, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Emite el reporte (al abrir el documento para imprimir o guardar en PDF): se calcula de nuevo, se
    guarda con su código, responsable y huella, y se devuelve con esos datos."""
    from app.modules.reglas_lab.reportes.reporte import build_report

    scope, filters = _report_scope(db, current_user, payload.start, payload.end, payload.workshop_id,
                                   payload.campus_id)
    if scope is None:  # "todos" se guarda como la lista de hoy, para verificarlo igual más adelante
        scope = {row.id for row in db.query(models.Workshop.id)}
    report = build_report(db, payload.start, payload.end, scope)
    now = datetime.now(timezone.utc)
    issued = models.IssuedReport(
        code=f'RPT-{now:%Y%m%d-%H%M%S}-{uuid4().hex[:4].upper()}',
        period_start=payload.start.astimezone(timezone.utc),
        period_end=payload.end.astimezone(timezone.utc),
        workshop_ids=sorted(scope),
        filters=filters,
        responsible=payload.responsible.strip() or None,
        fingerprint=report['fingerprint'],
        summary={key: report['summary'][key] for key in ('alerts', 'confirmed', 'discarded', 'pending', 'sessions')},
        generated_by=current_user.id,
        generated_by_name=current_user.full_name or current_user.username,
        generated_at=now,
    )
    db.add(issued)
    db.commit()
    registrar(db, current_user, 'EMITIR_REPORTE', 'reportes',
              f'{issued.code}: {payload.start:%Y-%m-%d} a {payload.end:%Y-%m-%d}, '
              f'{filters["workshop_name"] or filters["campus_name"] or "todos los laboratorios"}')
    return {**report, 'report_id': issued.code, 'generated_at': now, 'filters': filters,
            'generated_by': issued.generated_by_name, 'responsible': issued.responsible, 'issued': True}


@router.get('/reports')
def list_issued_reports(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    """Reportes emitidos que el usuario puede ver, los más recientes primero."""
    rows = db.query(models.IssuedReport).order_by(models.IssuedReport.generated_at.desc()).all()
    return [_issued_summary(row) for row in rows if _can_see_report(row, current_user)][:limit]


@router.get('/reports/{code}/verify')
def verify_issued_report(code: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Recalcula el reporte con el mismo periodo y laboratorios y compara la huella con la emitida.
    Si no coincide, los datos del periodo cambiaron después de emitirlo (p. ej. se validó una alerta,
    o el periodo aún no había terminado)."""
    from app.modules.reglas_lab.reportes.reporte import build_report

    row = db.query(models.IssuedReport).filter_by(code=code).first()
    if row is None or not _can_see_report(row, current_user):
        raise HTTPException(status_code=404, detail='Reporte no encontrado')
    current = build_report(db, as_utc(row.period_start), as_utc(row.period_end), set(row.workshop_ids or []))
    return {**_issued_summary(row), 'current_fingerprint': current['fingerprint'],
            'matches': current['fingerprint'] == row.fingerprint}
