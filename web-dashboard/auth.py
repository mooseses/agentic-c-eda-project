

import os
import secrets
from fastapi import Security, HTTPException, status, Query
from fastapi.security import APIKeyHeader

API_KEY = os.environ.get("SENTINEL_API_KEY", secrets.token_urlsafe(32))
API_KEY_NAME = "X-API-Key"

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def verify_api_key(
    api_key_h: str = Security(api_key_header),
    api_key_q: str | None = Query(None, alias="api_key")
) -> str:

    api_key = api_key_h or api_key_q

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key"
        )
    if not secrets.compare_digest(api_key, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key"
        )
    return api_key

def get_api_key() -> str:

    return API_KEY
