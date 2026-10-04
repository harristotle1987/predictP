"""
Vercel Serverless / Deployment Entrypoint.
Exposes FastAPI application directly from backend.app.
"""
from backend.app import app, application

__all__ = ["app", "application"]
