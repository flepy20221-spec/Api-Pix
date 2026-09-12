import time
from datetime import datetime
from fastapi import APIRouter, HTTPException

from app.core import (
    _state, bearer_expirado, renovar_bearer_token,
    BEARER_TOKEN, BEARER_EXPIRES,
)
from app.models.schemas import StatusResponse, RenewResponse, TokenStatus

router = APIRouter(prefix="/status", tags=["Status & Auth"])


@router.get(
    "",
    response_model=StatusResponse,
    summary="Status geral da API e dos tokens",
)
def get_status():
    """
    Retorna o status de todos os tokens de autenticação:
    - Bearer Token RecargaPay (validade ~24h, renovação automática)
    - Google Refresh Token (permanente)
    - Google Access Token (1h, renovação automática)
    - Firebase FIS Token (7 dias, renovação automática)
    - PIX HMAC Secret (permanente, hardcoded no APK)
    """
    now_ms       = time.time() * 1000
    expires_ms   = _state["bearer_expires"]
    remaining_s  = max(0, expires_ms / 1000 - time.time())
    remaining_h  = remaining_s / 3600
    bearer_valid = not bearer_expirado()

    tokens = [
        TokenStatus(
            component  = "Bearer Token (RecargaPay)",
            status     = "ATIVO" if bearer_valid else "EXPIRANDO",
            valid      = bearer_valid,
            expires_in = f"~{int(remaining_h)}h {int((remaining_h % 1) * 60)}min",
            auto_renew = True,
        ),
        TokenStatus(
            component  = "Google Refresh Token",
            status     = "PERMANENTE",
            valid      = True,
            expires_in = "Não expira",
            auto_renew = False,
        ),
        TokenStatus(
            component  = "Google Access Token",
            status     = "ATIVO" if _state["google_token_expiry"] > time.time() else "PENDENTE",
            valid      = _state["google_token_expiry"] > time.time(),
            expires_in = "~1h (renovado sob demanda)",
            auto_renew = True,
        ),
        TokenStatus(
            component  = "Firebase FIS Token",
            status     = "ATIVO",
            valid      = True,
            expires_in = "7 dias",
            auto_renew = True,
        ),
        TokenStatus(
            component  = "PIX HMAC Secret",
            status     = "PERMANENTE",
            valid      = True,
            expires_in = "Hardcoded no APK",
            auto_renew = False,
        ),
        TokenStatus(
            component  = "Device ID",
            status     = "PERMANENTE",
            valid      = True,
            expires_in = "Hardware fixo",
            auto_renew = False,
        ),
    ]

    return StatusResponse(
        healthy      = bearer_valid,
        bearer_token = _state["bearer_token"][:8] + "...",
        bearer_valid = bearer_valid,
        expires_in_h = round(remaining_h, 2),
        login_count  = _state["login_count"],
        last_login   = _state["last_login"],
        tokens       = tokens,
        timestamp    = datetime.utcnow().isoformat(),
    )


@router.post(
    "/renew",
    response_model=RenewResponse,
    summary="Renovar Bearer Token manualmente",
)
def renew_token(force: bool = False):
    """
    Renova o Bearer Token RecargaPay de forma automática.

    **Fluxo de renovação (2 etapas):**
    1. Google OAuth2: `refresh_token` → novo `access_token` (sem `client_secret`)
    2. RecargaPay `/users/login`: `grant_type=google_access_token` → novo Bearer Token

    O token é renovado automaticamente quando expira, mas este endpoint
    permite forçar a renovação a qualquer momento.
    """
    if not force and not bearer_expirado():
        remaining_h = max(0, _state["bearer_expires"] / 1000 - time.time()) / 3600
        return RenewResponse(
            success      = True,
            new_token    = _state["bearer_token"][:8] + "...",
            expires_in_h = round(remaining_h, 2),
            timestamp    = datetime.utcnow().isoformat(),
        )

    try:
        new_token = renovar_bearer_token()
        remaining_h = max(0, _state["bearer_expires"] / 1000 - time.time()) / 3600
        return RenewResponse(
            success      = True,
            new_token    = new_token[:8] + "...",
            expires_in_h = round(remaining_h, 2),
            timestamp    = datetime.utcnow().isoformat(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
