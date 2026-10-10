# SafeVision: Sistema de Monitoreo de EPP para Laboratorios

## Descripción
SafeVision es un sistema automatizado diseñado para la supervisión y monitoreo en tiempo real del uso correcto de Equipos de Protección Personal (EPP) en laboratorios de Química y Física. El sistema garantiza el cumplimiento de normas de seguridad mediante visión artificial.

## Objetivos
- Automatizar la detección de EPP mediante el procesamiento de video.
- Mantener una bitácora de auditoría detallada de las actividades en los laboratorios.
- Proporcionar una plataforma centralizada para la gestión de usuarios (Administradores y Encargados).
- Integrar visión artificial de alto rendimiento en el backend para un análisis eficiente.

## Arquitectura Técnica
- **Backend:** Python + FastAPI (Modular y desacoplado).
- **Motor de IA:** YOLO11 Nano para la detección de objetos (inferencia de EPP).
- **Base de Datos:** SQLAlchemy (SQLite para desarrollo inicial) con persistencia de logs de auditoría.
- **Seguridad:** Autenticación mediante JWT (JSON Web Tokens).
- **Frontend:** React + Vite (desplegado en Vercel) para la visualización de resultados e inferencias.

## Capacidades de Video
- **Procesamiento de Archivos:** Soporte para carga y análisis de archivos `.mp4`.
- **Transmisión en Vivo:** Capacidad para procesar flujos de video desde cámaras conectadas al equipo servidor.

## Estructura del Backend (Resumen)
- `app/main.py`: API Gateway central (gestiona rutas y middleware de logs).
- `app/core/`: Configuración global, seguridad (JWT) y conexión a base de datos.
- `app/modules/`: Lógica de negocio modularizada (auth_db, vision_engine, quimica, fisica).
