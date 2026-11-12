import hmac
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def verify_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    expected = get_settings().api_key
    if x_api_key is None or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
