import logging
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException, Request
from starlette import status
from starlette.responses import JSONResponse

from app.api.deps import UsernameDep
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.api_route(
    "/{service_prefix}/openapi.json", methods=["GET"], include_in_schema=False
)
async def proxy_openapi(service_prefix: str, request: Request) -> Any:
    if service_prefix not in settings.SERVICES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    base_url = settings.SERVICES[service_prefix]
    response = await request.app.state.http_client.get(f"{base_url}/openapi.json")
    spec = response.json()
    spec["servers"] = [{"url": f"/{service_prefix}"}]
    return spec


@router.api_route(
    "/{service_prefix}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
)
async def gateway_proxy(
    service_prefix: str, path: str, request: Request, username: UsernameDep
) -> JSONResponse:
    if service_prefix not in settings.SERVICES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service not found"
        )

    target_url = f"{settings.SERVICES[service_prefix]}/{path}"
    query_params = str(request.query_params)
    if query_params:
        target_url += f"?{query_params}"

    content = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("authorization", None)

    try:
        response = await request.app.state.http_client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=content,
            timeout=30.0,
        )
        try:
            return JSONResponse(
                content=response.json(), status_code=response.status_code
            )
        except Exception as e:
            logger.exception(e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Incorrect internal response",
            )
    except Exception as e:
        logger.exception(e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Internal error"
        )
