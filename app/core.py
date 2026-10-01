"""
RecargaPay Core — Lógica de autenticação e chamadas à API.
Extraído e adaptado do script recargapay_api.py (v5.11.6).
"""

import hashlib
import hmac as _hmac_module
import json
import os
import time
import logging
from datetime import datetime
from typing import Optional

from app.utils.pix_keys import normalize_pix_key_value

import requests

log = logging.getLogger(__name__)

# ==============================================================================
# CONFIGURAÇÃO — valores de conta/dispositivo somente via ambiente do serviço.
# ==============================================================================

BEARER_TOKEN          = os.getenv("BEARER_TOKEN", "")
RECARGA_REFRESH_TOKEN = os.getenv("RECARGA_REFRESH_TOKEN", "")
BEARER_EXPIRES        = int(os.getenv("BEARER_EXPIRES", "0"))

USER_ID        = os.getenv("USER_ID", "")
CLIENT_ID      = os.getenv("CLIENT_ID", "")
DEVICE_ID      = os.getenv("DEVICE_ID", "")
DEVICE_UUID    = os.getenv("DEVICE_UUID", "")
DEVICE_INSTID  = os.getenv("DEVICE_INSTID", "")
ADVERTISING_ID = os.getenv("ADVERTISING_ID", "")
PIN_CODE       = os.getenv("PIN_CODE", "")
PIN_MODE       = os.getenv("PIN_MODE",       "biometric")
COOKIE_AB1     = os.getenv("COOKIE_AB1",     "6")
HID            = os.getenv("HID", "")
GEO_LAT        = float(os.getenv("GEO_LAT", "0"))
GEO_LON        = float(os.getenv("GEO_LON", "0"))

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")

FIREBASE_API_KEY    = os.getenv("FIREBASE_API_KEY", "")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
FIS_FID             = os.getenv("FIS_FID", "")
FIS_REFRESH_TOKEN   = os.getenv("FIS_REFRESH_TOKEN", "")

PIX_HMAC_SECRET = os.getenv("PIX_HMAC_SECRET", "")

# Mensagem/descrição padrão enviada junto ao Pix (visível no comprovante)
PIX_DEFAULT_DESCRIPTION = os.getenv("PIX_DEFAULT_DESCRIPTION", "")

# Destinatário padrão para a rota de contato, configurado no serviço.
RECEIVER_PERSON_ID  = os.getenv("RECEIVER_PERSON_ID", "")
RECEIVER_ACCOUNT_ID = os.getenv("RECEIVER_ACCOUNT_ID", "")

BASE_URL   = "https://api.recarga.com"
USER_AGENT = (
    "RecargaPay/5.11.6.2401087 Mozilla/5.0 "
    "(Linux; Android 14; motorola edge 30 neo Build/U1SSMS34.31-64-4-19; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/153.0.8010.36 Mobile Safari/537.36"
)

# ==============================================================================
# ESTADO GLOBAL DE AUTENTICAÇÃO (atualizado pelo re-login automático)
# ==============================================================================

_state = {
    "bearer_token":           BEARER_TOKEN,
    "bearer_expires":         BEARER_EXPIRES,
    "recarga_refresh_token":  RECARGA_REFRESH_TOKEN,
    "google_access_token":    "",
    "google_token_expiry":    0,
    "fis_auth_token":         "",
    "fis_token_created":      0,
    "login_count":            0,
    "last_login":             None,
}


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "accept":              "application/json",
        "cache-control":       "no-store",
        "content-type":        "application/json;charset=UTF-8",
        "x-client-id":         CLIENT_ID,
        "x-app-version":       "1087",
        "x-build-date":        "2026-09-10T20:01:43Z",
        "cookie":              f"ab1={COOKIE_AB1}",
        "authorization":       f"Bearer {_state['bearer_token']}",
        "x-user":              USER_ID,
        "x-limit-ad-tracking": "false",
        "x-advertisingid":     ADVERTISING_ID,
        "user-agent":          USER_AGENT,
        "Accept-Encoding":     "gzip",
        "Connection":          "Keep-Alive",
    })
    return s


session = _make_session()


# ==============================================================================
# RENOVAÇÃO AUTOMÁTICA DO BEARER TOKEN
# ==============================================================================

def bearer_expirado() -> bool:
    return (time.time() * 1000) >= (_state["bearer_expires"] - 300_000)


def renovar_bearer_token() -> str:
    """
    Renova o Bearer Token em 2 etapas:
    1. Google OAuth2 refresh_token → novo access_token
    2. RecargaPay /users/login com google_access_token → novo Bearer Token
    Confirmado e testado em 2026-09-12. client_secret = client_id.
    """
    log.info("[Auth] Renovando Bearer Token...")

    # Etapa 1: Google
    g_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id":     GOOGLE_CLIENT_ID,
            "refresh_token": GOOGLE_REFRESH_TOKEN,
            "grant_type":    "refresh_token",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    if g_resp.status_code != 200:
        raise RuntimeError(f"Falha Google token: HTTP {g_resp.status_code}")

    google_access_token = g_resp.json()["access_token"]
    _state["google_access_token"] = google_access_token
    _state["google_token_expiry"] = int(time.time()) + 3599
    log.info("[Auth] Google access_token renovado.")

    # Etapa 2: RecargaPay login
    device_ids_json = json.dumps([
        {"type": "androidId",  "id": DEVICE_ID},
        {"type": "uuid",       "id": DEVICE_UUID},
        {"type": "instanceid", "id": DEVICE_INSTID},
    ])
    login_resp = requests.post(
        f"{BASE_URL}/api/0.1/users/login",
        data={
            "grant_type":           "google_access_token",
            "google_access_token":  google_access_token,
            "client_id":            CLIENT_ID,
            "client_secret":        CLIENT_ID,
            "device_id":            DEVICE_ID,
            "device_ids":           device_ids_json,
        },
        headers={
            "accept":              "application/json",
            "cache-control":       "no-store",
            "x-client-id":         CLIENT_ID,
            "x-app-version":       "1087",
            "x-build-date":        "2026-09-11T21:22:50Z",
            "cookie":              f"ab1={COOKIE_AB1}",
            "referer":             "https://recargapay.com.br/login/method",
            "x-screen":            "/login/method",
            "x-limit-ad-tracking": "false",
            "x-advertisingid":     ADVERTISING_ID,
            "user-agent":          USER_AGENT,
            "Content-Type":        "application/x-www-form-urlencoded;charset=UTF-8",
            "Accept-Encoding":     "gzip",
            "Connection":          "Keep-Alive",
        },
        timeout=15,
    )
    if login_resp.status_code != 200:
        raise RuntimeError(f"Falha re-login RecargaPay: HTTP {login_resp.status_code}")

    login_data = login_resp.json()
    new_bearer  = login_data["access_token"]
    new_refresh = login_data.get("refresh_token", _state["recarga_refresh_token"])
    new_expires = int(login_data.get("expires", 0))

    _state["bearer_token"]          = new_bearer
    _state["bearer_expires"]        = new_expires
    _state["recarga_refresh_token"] = new_refresh
    _state["login_count"]          += 1
    _state["last_login"]            = datetime.utcnow().isoformat()

    session.headers["authorization"] = f"Bearer {new_bearer}"
    log.info("[Auth] Bearer Token renovado (expira em ~%.1fh)", (new_expires/1000 - time.time())/3600)
    return new_bearer


def _ensure_auth():
    """Garante que o Bearer Token está válido antes de qualquer chamada."""
    if bearer_expirado():
        log.warning("[Auth] Token expirado — renovando proativamente...")
        renovar_bearer_token()


def _check_401(r: requests.Response):
    if r.status_code == 401:
        log.warning("[Auth] HTTP 401 — renovando token...")
        renovar_bearer_token()


# ==============================================================================
# HELPERS
# ==============================================================================

def _h(referer: str, screen: str, ctype: str = None, pin: str = None) -> dict:
    h = dict(session.headers)
    h["referer"]  = referer
    h["x-screen"] = screen
    if ctype:
        h["content-type"] = ctype
    if pin:
        h["x-pin"]      = pin
        h["x-pin-mode"] = PIN_MODE
    return h


def _device_ids() -> list:
    return [
        {"type": "androidId",  "id": DEVICE_ID},
        {"type": "uuid",       "id": DEVICE_UUID},
        {"type": "instanceid", "id": DEVICE_INSTID},
    ]


def _extra_info() -> dict:
    return {
        "rt": False,
        "geo": {
            "acc": 100, "age": 0, "ertn": 0,
            "lat": GEO_LAT, "lon": GEO_LON,
            "pro": "", "ts": int(time.time() * 1000),
        },
    }


def _parse_body(data: dict) -> dict:
    body = data.get("body", data)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            pass
    return body


def build_integrity_hash(key_value: str) -> str:
    """HMAC-SHA256(key=PIX_HMAC_SECRET, msg=key_value)."""
    if not PIX_HMAC_SECRET:
        raise PixError(
            code="pix_hmac_not_configured",
            title="Configuração incompleta",
            message="A chave de integridade PIX não está configurada no servidor.",
            status_code=503,
        )
    return _hmac_module.new(
        PIX_HMAC_SECRET.encode(),
        key_value.encode(),
        hashlib.sha256,
    ).hexdigest()


DEVICE_FINGERPRINT = {
    "colorDepth":     24,
    "deviceType3ds":  "BROWSER",
    "javaEnabled":    False,
    "language":       "pt-BR",
    "screenHeight":   750,
    "screenWidth":    412,
    "timeZoneOffset": 3,
    "userAgent":      USER_AGENT,
}

# Erros de negócio do DICT
PIX_ERROS = {
    "dict_key_not_found":         "Chave PIX não encontrada no DICT (Banco Central).",
    "dict_key_owner_is_payer":    "A chave PIX pertence à sua própria conta.",
    "dict_key_type_mismatch":     "Tipo de chave não corresponde ao valor informado.",
    "pix_payment_not_allowed":    "Pagamento PIX não permitido para esta chave.",
    "pix_amount_exceeds_limit":   "Valor excede o limite PIX disponível.",
    "invalid_key_format":         "Formato de chave inválido.",
    "receiver_account_not_found": "Conta do destinatário não encontrada.",
    "pix_out_not_allowed":        "Transferência PIX não autorizada para esta conta.",
}


def _handle_pix_error(r: requests.Response):
    """Levanta PixError para respostas 400/422 com código de negócio."""
    if r.status_code in (400, 422):
        try:
            err = r.json()
        except Exception:
            err = {}
        code    = err.get("code", err.get("error", ""))
        title   = err.get("title", f"Erro HTTP {r.status_code}")
        message = PIX_ERROS.get(code, err.get("message", r.text[:300]))
        raise PixError(code=code, title=title, message=message, status_code=r.status_code)


class PixError(Exception):
    def __init__(self, code: str, title: str, message: str, status_code: int = 422):
        self.code        = code
        self.title       = title
        self.message     = message
        self.status_code = status_code
        super().__init__(message)


class PixPreparationError(Exception):
    """A failure before the first purchase request: no money was sent."""

    def __init__(self, stage: str, cause: Exception):
        self.stage = stage
        self.provider_status = (
            cause.response.status_code
            if isinstance(cause, requests.HTTPError) and cause.response is not None
            else None
        )
        self.code = cause.code if isinstance(cause, PixError) else "pix_preparation_failed"
        self.status_code = (
            cause.status_code
            if isinstance(cause, PixError) and 400 <= cause.status_code < 500
            else 503
        )
        super().__init__(f"PIX preparation failed at {stage}")


# ==============================================================================
# ENDPOINTS DA API RECARGAPAY
# ==============================================================================

def get_startup() -> dict:
    _ensure_auth()
    r = session.get(f"{BASE_URL}/api/v2/app/startup",
                    headers=_h("https://recargapay.com.br/", "/"), timeout=30)
    _check_401(r)
    r.raise_for_status()
    return r.json()


def get_pins(pin: str = None) -> dict:
    _ensure_auth()
    r = session.get(
        f"{BASE_URL}/api/0.1/users/me/pins",
        params={"device_id": DEVICE_ID},
        headers=_h("https://recargapay.com.br/", "/",
                   ctype="application/x-www-form-urlencoded;charset=UTF-8",
                   pin=pin),
        timeout=30,
    )
    _check_401(r)
    r.raise_for_status()
    return r.json()


def get_pix_contact_overview() -> dict:
    r = session.get(
        f"{BASE_URL}/api/v2/persons/me/dict/pix/contact/overview",
        headers=_h("https://recargapay.com.br/", "/pix/transactions/contacts"),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_pix_keys() -> list:
    r = session.get(
        f"{BASE_URL}/api/v2/persons/me/dict/keys",
        headers=_h("https://recargapay.com.br/", "/pix/transactions/contacts"),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_pix_participants() -> list:
    r = session.get(
        f"{BASE_URL}/api/v2/persons/me/dict/pix/participants",
        headers=_h("https://recargapay.com.br/pix/transactions/contacts",
                   "/pix/transactions/prices"),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_creditcards() -> dict:
    r = session.get(
        f"{BASE_URL}/api/0.1/users/me/creditcards",
        headers=_h("https://recargapay.com.br/pix/transactions/prices",
                   "/pix/transactions/prices"),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_balance() -> dict:
    r = session.get(
        f"{BASE_URL}/api/0.1/users/me/balance",
        headers=_h("https://recargapay.com.br/", "/"),
        timeout=30,
    )
    _check_401(r)
    r.raise_for_status()
    return r.json()


def post_pix_payment_by_contact(
    person_id: str = RECEIVER_PERSON_ID,
    account_id: str = RECEIVER_ACCOUNT_ID,
    integrity_hash: str = None,
) -> dict:
    if integrity_hash is None:
        integrity_hash = build_integrity_hash(person_id)
    r = session.post(
        f"{BASE_URL}/api/v2/persons/me/dict/pix/payments",
        json={
            "receiver": {
                "contact": {"personId": person_id, "accountId": account_id},
                "inputMethod": "RECENT",
            },
            "integrityHash": integrity_hash,
        },
        headers=_h("https://recargapay.com.br/pix/transactions/contacts",
                   "/pix/transactions/contacts",
                   ctype="application/json;charset=UTF-8"),
        timeout=30,
    )
    _check_401(r)
    _handle_pix_error(r)
    r.raise_for_status()
    return r.json()


def post_pix_payment_by_key(
    key_type: str,
    key_value: str,
    integrity_hash: str = None,
) -> dict:
    key_value = normalize_pix_key_value(key_type, key_value)
    if integrity_hash is None:
        integrity_hash = build_integrity_hash(key_value)
    r = session.post(
        f"{BASE_URL}/api/v2/persons/me/dict/pix/payments",
        json={
            "receiver": {
                "keyType":     key_type.upper(),
                "key":         key_value,
                "inputMethod": "TYPED_KEY",
            },
            "integrityHash": integrity_hash,
        },
        headers=_h("https://recargapay.com.br/pix/transactions/contacts",
                   "/pix/transactions/contacts",
                   ctype="application/json;charset=UTF-8"),
        timeout=30,
    )
    _check_401(r)
    _handle_pix_error(r)
    r.raise_for_status()
    return r.json()


def create_shopping_cart(pix_id: str, amount: str, currency: str = "BRL", description: str = None) -> dict:
    pix_payment_data = {
        "id":         pix_id,
        "amount":     amount,
        "currency":   currency,
        "pixOutType": "",
    }
    # Inclui a mensagem/descrição apenas se fornecida (visível no comprovante)
    msg = description if description is not None else PIX_DEFAULT_DESCRIPTION
    if msg:
        pix_payment_data["description"] = msg

    r = session.post(
        f"{BASE_URL}/api/0.1/users/me/shopping-carts",
        data={
            "device_ids":       json.dumps(_device_ids()),
            "device_id":        DEVICE_ID,
            "pix_payment_data": json.dumps(pix_payment_data),
            "hid": HID,
        },
        headers=_h("https://recargapay.com.br/pix/transactions/prices",
                   "/pix/transactions/prices",
                   ctype="application/x-www-form-urlencoded;charset=UTF-8"),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def set_payment_method(cart_id: str, method: str = "WALLET") -> dict:
    r = session.put(
        f"{BASE_URL}/api/0.1/users/me/shopping-carts/{cart_id}",
        data={"payment_method_id": method},
        headers=_h("https://recargapay.com.br/shopping/cart/payments-methods",
                   "/shopping/cart/payments-methods",
                   ctype="application/x-www-form-urlencoded;charset=UTF-8"),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def post_sr_session(flow: str = "startup") -> dict:
    """Cria uma sessão SR (anti-fraude/Sardine) antes de executar a compra."""
    r = session.post(
        f"{BASE_URL}/api/v2/persons/me/sr-sessions",
        json={"flow": flow},
        headers=_h("https://recargapay.com.br/shopping/cart/confirmed",
                   "/shopping/cart/confirmed",
                   ctype="application/json;charset=UTF-8"),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def execute_purchase(cart_id: str, pin: str = None) -> dict:
    r = session.post(
        f"{BASE_URL}/api/v2/users/me/shopping-carts/{cart_id}/purchases",
        data={
            "device_ids":         json.dumps(_device_ids()),
            "device_id":          DEVICE_ID,
            "shopping_cart_id":   cart_id,
            "hid":                HID,
            "extra_info":         json.dumps(_extra_info()),
            "device_fingerprint": json.dumps(DEVICE_FINGERPRINT),
            "kid":                "",
        },
        headers=_h("https://recargapay.com.br/shopping/cart/confirmed",
                   "/shopping/cart/confirmed",
                   ctype="application/x-www-form-urlencoded;charset=UTF-8",
                   pin=pin or PIN_CODE),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def poll_status(cart_id: str, run_id: str, max_attempts: int = 20, interval: float = 3.0) -> dict:
    url = f"{BASE_URL}/api/v2/users/me/shopping-carts/{cart_id}/purchases/{run_id}"
    data = {}
    for _ in range(max_attempts):
        r = session.get(
            url,
            headers=_h("https://recargapay.com.br/user/retry-redirection",
                        "/user/retry-redirection"),
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("done", False):
            return data
        time.sleep(interval)
    return data


def get_raf_context() -> dict:
    r = session.get(
        f"{BASE_URL}/api/0.1/users/me/raf-context/header",
        params={"is_receipt": "true"},
        headers=_h("https://recargapay.com.br/user/retry-redirection",
                   "/shopping/*/receipt"),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ==============================================================================
# FLUXOS COMPLETOS
# ==============================================================================

def fluxo_pix_por_chave(
    key_type: str,
    key_value: str,
    amount: str,
    payment_method: str = "WALLET",
    integrity_hash: str = None,
    description: str = None,
) -> dict:
    """Executa o fluxo completo de pagamento PIX por chave."""
    # Only the purchase endpoint can move money. A preparation failure is safe
    # to release for a later admin attempt; any exception once purchase starts
    # remains uncertain and must be reconciled without another payment call.
    stage = "normalize_key"
    try:
        key_value = normalize_pix_key_value(key_type, key_value)
        stage = "authentication"
        _ensure_auth()

        stage = "startup"
        get_startup()
        stage = "pin_read"
        pins = get_pins()
        if pins.get("statusCode", -1) not in (-1, 0, None):
            log.warning("[PIX] Unexpected PIN read status")

        stage = "participants"
        get_pix_participants()

        stage = "recipient_lookup"
        pix = post_pix_payment_by_key(key_type, key_value, integrity_hash)
        pix_id = pix["id"]

        stage = "shopping_cart"
        cart = create_shopping_cart(pix_id, amount, description=description)
        cart_id = cart["id"]

        stage = "payment_method"
        set_payment_method(cart_id, payment_method)

        stage = "pin_verification"
        pin_result = get_pins(pin=PIN_CODE)
        if pin_result.get("statusCode", -1) != 0:
            raise PixError(
                code="invalid_pin",
                title="PIN inválido",
                message="Validação do PIN falhou.",
                status_code=400,
            )

        stage = "risk_session"
        post_sr_session()
    except Exception as exc:
        raise PixPreparationError(stage, exc) from exc

    purchase = execute_purchase(cart_id, pin=PIN_CODE)
    run_id   = purchase["runId"]

    # A purchase request has already reached the provider. Never submit it a
    # second time just because a poll still reports an authentication step;
    # expose that state as nonterminal for manual reconciliation instead.
    result = poll_status(cart_id, run_id)
    body   = _parse_body(result)

    return {
        "success":        True,
        "pix_id":         pix_id,
        "cart_id":        cart_id,
        "run_id":         run_id,
        "key_type":       key_type,
        "key_value":      key_value,
        "amount":         amount,
        "payment_method": payment_method,
        "description":    description,
        "receiver":       pix.get("receiver", {}),
        "result":         body,
        "timestamp":      datetime.utcnow().isoformat(),
    }


def fluxo_pix_por_contato(
    person_id: str = RECEIVER_PERSON_ID,
    account_id: str = RECEIVER_ACCOUNT_ID,
    amount: str = "0.01",
    payment_method: str = "WALLET",
    integrity_hash: str = None,
    description: str = None,
) -> dict:
    """Executa o fluxo completo de pagamento PIX por contato recente."""
    _ensure_auth()

    get_startup()
    get_pins()
    get_pix_contact_overview()
    get_pix_keys()
    get_pix_participants()

    pix = post_pix_payment_by_contact(person_id, account_id, integrity_hash)
    pix_id  = pix["id"]

    cart    = create_shopping_cart(pix_id, amount, description=description)
    cart_id = cart["id"]

    get_creditcards()
    set_payment_method(cart_id, payment_method)

    pin_result = get_pins(pin=PIN_CODE)
    if pin_result.get("statusCode", -1) != 0:
        raise PixError(
            code="invalid_pin",
            title="PIN inválido",
            message=f"Validação do PIN falhou: {pin_result}",
            status_code=400,
        )

    post_sr_session()

    purchase = execute_purchase(cart_id, pin=PIN_CODE)
    run_id   = purchase["runId"]

    # Do not issue a second purchase after a nonterminal response. A pending
    # authentication state must be reconciled without repeating the side effect.
    result = poll_status(cart_id, run_id)
    body   = _parse_body(result)

    get_raf_context()

    return {
        "success":        True,
        "pix_id":         pix_id,
        "cart_id":        cart_id,
        "run_id":         run_id,
        "person_id":      person_id,
        "account_id":     account_id,
        "amount":         amount,
        "payment_method": payment_method,
        "description":    description,
        "receiver":       pix.get("receiver", {}),
        "result":         body,
        "timestamp":      datetime.utcnow().isoformat(),
    }
