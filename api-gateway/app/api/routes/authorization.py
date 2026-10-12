from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette import status

from app.api.deps import create_jwt_token
from app.core.config import settings
from app.models.web.authorization import Token

router = APIRouter()
security = HTTPBasic()


@router.post("/login")
async def login(credentials: HTTPBasicCredentials = Depends(security)) -> Token:
    if (
        credentials.username != settings.AUTH_USER
        or credentials.password != settings.AUTH_PASSWORD
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect credentials"
        )

    token = create_jwt_token(credentials.username)
    return Token(access_token=token, token_type="Bearer")
