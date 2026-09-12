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
from datetime import datetime

from fastapi import FastAPI, Request
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
        "**Autenticação:** Bearer Token RecargaPay (renovado via Google OAuth2 → RecargaPay login)\n\n"
        "**Segurança:** integrityHash via HMAC-SHA256 (extraído do APK 5.11.6)"
    ),
    version     = "1.0.0",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
    contact     = {"name": "RecargaPay Bot"},
)

# CORS — permite acesso de qualquer origem (ajuste em produção se necessário)
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


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    log.error(f"Erro não tratado: {exc}", exc_info=True)
    return JSONResponse(
        status_code = 500,
        content     = {
            "error":   True,
            "code":    "internal_error",
            "message": str(exc),
        },
    )

# ==============================================================================
# MIDDLEWARE — log de requisições
# ==============================================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = (time.time() - start) * 1000
    log.info(f"{request.method} {request.url.path} → {response.status_code} ({duration:.0f}ms)")
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
