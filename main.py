"""
RecargaPay PIX API
FastAPI — pronto para deploy no Railway.

Endpoints:
  GET  /              → Página de boas-vindas
  GET  /health        → Health check (Railway)
  GET  /status        → Status dos tokens
  POST /status/renew  → Renovar Bearer Token
  POST /pix/key       → Pagar por chave PIX
  POST /pix/contact   → Pagar por contato recente
  GET  /pix/history   → Histórico de transações
  GET  /pix/contacts  → Listar contatos
  GET  /pix/keys      → Listar chaves PIX
  GET  /pix/balance   → Consultar saldo
  POST /pix/lookup    → Consultar destinatário sem pagar
  GET  /docs          → Swagger UI (documentação interativa)
  GET  /redoc         → ReDoc (documentação alternativa)
"""

import logging
import os
import time
import uuid
import hmac
import traceback
import sys
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import pix_router, status_router
from app.core import PixError

# ==============================================================================
# LOGGING
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("recargapay")

# ==============================================================================
# APP
# ==============================================================================

app = FastAPI(
    title       = "RecargaPay PIX API",
    description = (
        "API de automação de pagamentos PIX via RecargaPay.\n\n"
        "Todos os tokens são renovados automaticamente — "
        "nenhuma intervenção manual necessária.\n\n"
        "**Autenticação:** Bearer Token de serviço exigido em todos os endpoints PIX e de status. Configure PIX_API_TOKEN no Railway; a API falha fechada se não estiver definido.\n\n"
        "**Segurança:** integrityHash via HMAC-SHA256 configurado por PIX_HMAC_SECRET."
    ),
    version     = "1.0.0",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
    contact     = {"name": "RecargaPay Bot"},
)

# CORS — o acesso aos endpoints PIX/status continua protegido pelo bearer compartilhado.
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ==============================================================================
# ROUTERS
# ==============================================================================

app.include_router(status_router)
app.include_router(pix_router)

# ==============================================================================
# EXCEPTION HANDLERS
# ==============================================================================

@app.exception_handler(PixError)
async def pix_error_handler(request: Request, exc: PixError):
    return JSONResponse(
        status_code = exc.status_code,
        content     = {
            "error":       True,
            "code":        exc.code,
            "title":       exc.title,
            "message":     exc.message,
            "status_code": exc.status_code,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=422,
        content={
            "error": True,
            "code": "invalid_request",
            "message": "Os dados da solicitação são inválidos.",
            "retry_safe": True,
            "uncertain": False,
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


def _sanitized_stack(exc: Exception) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    return " <- ".join(f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}" for frame in frames)


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    log.error("Unhandled API error request_id=%s stack=%s", request_id, _sanitized_stack(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "code": "internal_error",
            "message": "Ocorreu um erro interno. Consulte o suporte com o identificador da solicitação.",
            "retry_safe": False,
            "uncertain": True,
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )

# ==============================================================================
# MIDDLEWARE — log de requisições
# ==============================================================================

@app.middleware("http")
async def request_context_auth_and_logging(request: Request, call_next):
    supplied_id = request.headers.get("x-request-id", "")
    try:
        request_id = str(uuid.UUID(supplied_id))
    except (ValueError, AttributeError):
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    path = request.url.path
    protected = path.startswith("/pix/") or path == "/pix" or path.startswith("/status/") or path == "/status"
    if protected and request.method.upper() != "OPTIONS":
        expected = os.getenv("PIX_API_TOKEN", "").strip()
        if not expected:
            response = JSONResponse(status_code=503, content={
                "error": True,
                "code": "api_token_not_configured",
                "message": "A autenticação da API PIX não está configurada.",
                "retry_safe": True,
                "uncertain": False,
                "request_id": request_id,
            })
            response.headers["X-Request-ID"] = request_id
            return response
        authorization = request.headers.get("authorization", "")
        presented = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if not presented or not hmac.compare_digest(presented, expected):
            response = JSONResponse(status_code=401, content={
                "error": True,
                "code": "unauthorized",
                "message": "Credencial da API PIX inválida ou ausente.",
                "retry_safe": True,
                "uncertain": False,
                "request_id": request_id,
            })
            response.headers["X-Request-ID"] = request_id
            return response

    start = time.time()
    response = await call_next(request)
    duration = (time.time() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    if protected:
        response.headers["Cache-Control"] = "no-store"
    log.info("%s %s → %s (%.0fms) request_id=%s", request.method, path, response.status_code, duration, request_id)
    return response

# ==============================================================================
# ROTAS RAIZ
# ==============================================================================

@app.get("/", tags=["Root"], summary="Boas-vindas")
def root():
    return {
        "name":        "RecargaPay PIX API",
        "version":     "1.0.0",
        "status":      "online",
        "docs":        "/docs",
        "health":      "/health",
        "timestamp":   datetime.utcnow().isoformat(),
        "endpoints": {
            "status":        "GET  /status",
            "renew_token":   "POST /status/renew",
            "pay_by_key":    "POST /pix/key",
            "pay_by_contact":"POST /pix/contact",
            "history":       "GET  /pix/history",
            "contacts":      "GET  /pix/contacts",
            "keys":          "GET  /pix/keys",
            "balance":       "GET  /pix/balance",
            "lookup":        "POST /pix/lookup",
        },
    }


@app.get("/health", tags=["Root"], summary="Health check (Railway)")
def health():
    """Endpoint de health check usado pelo Railway para verificar se a API está viva."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
