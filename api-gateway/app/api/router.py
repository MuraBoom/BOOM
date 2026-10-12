from fastapi import APIRouter

from app.api.routes import api_gateway, authorization, docs

api_router = APIRouter()

api_router.include_router(authorization.router, prefix="/auth")
api_router.include_router(api_gateway.router)
api_router.include_router(docs.router)
