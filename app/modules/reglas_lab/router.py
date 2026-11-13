"""Rutas de reglas_lab (/modules/reglas_lab): une los routers de cada submódulo.

    laboratorios/  laboratorios, locales, catálogo de EPP y reglas exigidas
    practicas/     iniciar/finalizar práctica y sesiones registradas
    incidencias/   alertas, validación y capturas
    reportes/      reporte SST, emisión y verificación

app/main.py solo incluye este router; las URL son las mismas que antes de dividirlo.
"""
from fastapi import APIRouter

from app.modules.reglas_lab.incidencias.router import router as incidencias_router
from app.modules.reglas_lab.laboratorios.router import router as laboratorios_router
from app.modules.reglas_lab.practicas.router import router as practicas_router
from app.modules.reglas_lab.reportes.router import router as reportes_router

router = APIRouter(prefix='/modules/reglas_lab', tags=['reglas_lab'])
router.include_router(laboratorios_router)
router.include_router(incidencias_router)
router.include_router(reportes_router)
router.include_router(practicas_router)
