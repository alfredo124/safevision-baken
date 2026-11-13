"""Cumplimiento de EPP en los laboratorios.

Un solo models.py con todas las tablas del módulo (están muy enlazadas: una incidencia apunta a la
práctica, al laboratorio y al EPP) y un submódulo por tema:

    cumplimiento/  motor de detección de faltas (no usa la base de datos)
    laboratorios/  laboratorios, locales, catálogo de EPP y reglas exigidas
    practicas/     iniciar/finalizar práctica y sesiones guardadas
    incidencias/   alertas: registro automático, validación y capturas
    reportes/      reporte SST, emisión y verificación

comun.py tiene lo compartido por las rutas, inicializacion.py prepara la base al arrancar y router.py
une las rutas de los submódulos bajo /modules/reglas_lab.
"""
