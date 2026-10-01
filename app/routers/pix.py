import logging
import sys
import traceback
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, HTTPException, Request

from app.core import (
    fluxo_pix_por_chave,
    fluxo_pix_por_contato,
    post_pix_payment_by_key,
    get_pix_contact_overview,
    get_pix_keys,
    get_pix_participants,
    get_creditcards,
    get_balance,
    build_integrity_hash,
    PixError,
    PixPreparationError,
    PIX_DEFAULT_DESCRIPTION,
)
from app.models.schemas import (
    PixKeyRequest,
    PixContactRequest,
    PixResponse,
    BalanceResponse,
    PixLookupResponse,
    HistoryResponse,
    TransactionRecord,
    IntegrityHashResponse,
)
from app.responses import PrettyJSONResponse

router = APIRouter(prefix="/pix", tags=["Pagamentos PIX"])
log = logging.getLogger("recargapay.pix")

# Histórico em memória (persiste enquanto o servidor estiver rodando)
_history: list[TransactionRecord] = []


def _format_brl(amount: str) -> str:
    """Formata um valor decimal como moeda brasileira sem caracteres especiais."""
    try:
        value = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return f"R$ {amount}"

    formatted = f"{value:,.2f}"
    formatted = formatted.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {formatted}"


def _as_float(value) -> float:
    """Converte valores monetários do provedor sem propagar formatos inválidos."""
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def _format_receiver(
    receiver: dict,
    key_type: str | None = None,
    key_value: str | None = None,
) -> dict:
    """Mantém somente os dados públicos e úteis do destinatário."""
    owner = receiver.get("owner") if isinstance(receiver.get("owner"), dict) else {}
    account = receiver.get("account") if isinstance(receiver.get("account"), dict) else {}

    institution = {
        "name": account.get("participantName"),
        "ispb": account.get("participant"),
        "branch": account.get("branch"),
        "account_number": account.get("accountNumber"),
        "account_type": account.get("accountType"),
    }

    return {
        "name": owner.get("name"),
        "document": owner.get("taxIdNumber"),
        "owner_type": owner.get("type"),
        "same_owner": owner.get("isSameOwner"),
        "key": key_value or receiver.get("key"),
        "key_type": key_type or receiver.get("keyType"),
        "institution": (
            institution
            if any(value is not None for value in institution.values())
            else None
        ),
    }


def _format_balance_response(result: dict) -> dict:
    """Resume o saldo e remove do retorno todas as contas internas zeradas."""
    accounts = result.get("accounts") if isinstance(result.get("accounts"), dict) else {}
    sources = []

    for source_id, source in accounts.items():
        if not isinstance(source, dict):
            continue

        amount = _as_float(source.get("amount", 0))
        blocked = _as_float(source.get("blocked", 0))
        if amount == 0 and blocked == 0:
            continue

        tags = source.get("tags") if isinstance(source.get("tags"), list) else []
        sources.append({
            "id": source_id,
            "amount": amount,
            "blocked": blocked,
            "formatted_amount": (
                source.get("formattedAmount") or _format_brl(source.get("amount", 0))
            ),
            "tags": [str(tag) for tag in tags],
        })

    sources.sort(key=lambda source: abs(source["amount"]), reverse=True)

    available = _as_float(result.get("amount", 0))
    blocked = _as_float(result.get("blocked", 0))
    return {
        "success": True,
        "message": "Saldo consultado com sucesso.",
        "wallet_available": bool(result.get("walletOk", False)),
        "balance": {
            "available": available,
            "blocked": blocked,
            "available_for_discounts": _as_float(
                result.get("amountForDiscounts", available)
            ),
            "currency": str(result.get("currency") or "BRL"),
            "formatted_available": (
                result.get("formattedAmount") or _format_brl(available)
            ),
            "formatted_blocked": (
                result.get("formattedBlocked") or _format_brl(blocked)
            ),
        },
        "sources": sources,
    }


def _format_lookup_response(result: dict, key_type: str, key_value: str) -> dict:
    """Converte a consulta de chave em uma resposta curta e previsível."""
    receiver = result.get("receiver") if isinstance(result.get("receiver"), dict) else {}
    return {
        "success": True,
        "found": True,
        "message": "Chave Pix encontrada.",
        "pix_id": result.get("id"),
        "receiver": _format_receiver(
            receiver,
            key_type=key_type.upper(),
            key_value=key_value,
        ),
    }


def _get_receipt_url(result: dict) -> str | None:
    """Extrai somente o link do comprovante do retorno interno do provedor."""
    actions = result.get("callToActions", [])
    if not isinstance(actions, list):
        return None

    for action in actions:
        if not isinstance(action, dict):
            continue
        url = action.get("url")
        right_url = (
            action.get("right", {}).get("url")
            if isinstance(action.get("right"), dict)
            else None
        )
        candidate = right_url or url
        if candidate and ("voucher" in candidate or "receipt" in candidate):
            return candidate
    return None


def _get_transaction_status(result: dict) -> str:
    """Normaliza os diferentes estados internos em um status estável da API."""
    status = str(result.get("status", "")).lower()
    action = str(result.get("action", "")).lower()
    title = str(result.get("title", "")).lower()

    if status in {"-1", "400", "401", "403", "422", "failed", "error", "rejected"}:
        return "failed"
    if (
        result.get("done") is True
        or status in {"1", "100", "101", "done", "approved", "completed", "success"}
        or "pix feito" in title
        or "pix realizado" in title
    ):
        return "completed"
    if action == "require-pin":
        return "pending_authentication"
    return "processing"


def _format_pix_response(tx: dict) -> dict:
    """Converte a resposta extensa do provedor em um contrato curto e previsível."""
    receiver = tx.get("receiver") if isinstance(tx.get("receiver"), dict) else {}
    result = tx.get("result") if isinstance(tx.get("result"), dict) else {}

    status = _get_transaction_status(result)
    flow_success = bool(tx.get("success"))
    completed = flow_success and status == "completed"
    retry_safe = flow_success and status == "failed"
    messages = {
        "completed": "Pix realizado com sucesso.",
        "pending_authentication": "Pix aguardando autenticacao.",
        "failed": "Nao foi possivel concluir o Pix.",
        "processing": "Pix enviado e em processamento.",
    }

    key_value = tx.get("key_value") or receiver.get("key")
    key_type = tx.get("key_type") or receiver.get("keyType")
    currency = str(result.get("currency") or "BRL")

    return {
        "success": completed,
        "message": messages[status],
        "retry_safe": retry_safe,
        "uncertain": not completed and not retry_safe,
        "transaction": {
            "status": status,
            "amount": {
                "value": str(tx.get("amount", "0.00")),
                "currency": currency,
                "formatted": _format_brl(tx.get("amount", "0.00")),
            },
            "payment_method": tx.get("payment_method", "WALLET"),
            "receipt_url": _get_receipt_url(result),
            "created_at": tx.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "references": {
                "pix_id": tx.get("pix_id", ""),
                "cart_id": tx.get("cart_id", ""),
                "run_id": tx.get("run_id", ""),
                "order_id": result.get("orderId"),
            },
        },
        "receiver": _format_receiver(
            receiver,
            key_type=key_type,
            key_value=key_value,
        ),
    }


def _record(tx: dict, tx_type: str) -> TransactionRecord:
    """Registra uma transação no histórico."""
    receiver = tx.get("receiver", {})
    owner    = receiver.get("owner", {}) if isinstance(receiver, dict) else {}
    account  = receiver.get("account", {}) if isinstance(receiver, dict) else {}
    result   = tx.get("result", {})
    status   = _get_transaction_status(result)
    success  = status == "completed"

    record = TransactionRecord(
        id             = str(uuid.uuid4()),
        type           = tx_type,
        key_type       = tx.get("key_type"),
        key_value      = tx.get("key_value"),
        person_id      = tx.get("person_id"),
        account_id     = tx.get("account_id"),
        amount         = tx.get("amount", "0.00"),
        payment_method = tx.get("payment_method", "WALLET"),
        receiver_name  = owner.get("name") if owner else None,
        receiver_bank  = account.get("participantName") if account else None,
        status         = "aprovado" if success else status,
        pix_id         = tx.get("pix_id", ""),
        cart_id        = tx.get("cart_id", ""),
        run_id         = tx.get("run_id", ""),
        timestamp      = tx.get("timestamp") or datetime.now(timezone.utc).isoformat(),
    )
    _history.insert(0, record)
    # Manter apenas os últimos 500 registros
    if len(_history) > 500:
        _history.pop()
    return record


# ==============================================================================
# SAFE PAYMENT FAILURE CONTRACT
# ==============================================================================

def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))

def _sanitized_stack() -> str:
    """Log frame locations only; exception messages may contain credentials/PII."""
    frames = traceback.extract_tb(sys.exc_info()[2])
    return " <- ".join(f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}" for frame in frames)

def _pix_failure_detail(request_id: str, *, retry_safe: bool, code: str, message: str) -> dict:
    return {
        "error": True,
        "code": code,
        "message": message,
        "retry_safe": retry_safe,
        "uncertain": not retry_safe,
        "request_id": request_id,
    }


# ==============================================================================
# ENDPOINTS
# ==============================================================================

@router.post(
    "/key",
    response_model=PixResponse,
    response_model_exclude_none=True,
    response_class=PrettyJSONResponse,
    summary="Pagar PIX por chave (CPF, CNPJ, PHONE, EMAIL, EVP)",
)
def pagar_por_chave(req: PixKeyRequest, request: Request):
    """
    Executa o fluxo completo de pagamento PIX por chave.

    **Tipos de chave:**
    | Tipo  | Formato                          | Exemplo                              |
    |-------|----------------------------------|--------------------------------------|
    | CPF   | Somente dígitos (11)             | `55537568802`                        |
    | CNPJ  | Somente dígitos (14)             | `12345678000195`                     |
    | PHONE | Com DDI (+55...)                 | `+5511999999999`                     |
    | EMAIL | Endereço de e-mail               | `email@exemplo.com`                  |
    | EVP   | UUID (chave aleatória)           | `26dd398b-b6e0-4202-b3e0-126b46e1049f` |

    O `integrityHash` é gerado automaticamente via `HMAC-SHA256`.
    O PIN é validado automaticamente via configuração.
    """
    try:
        result = fluxo_pix_por_chave(
            key_type       = req.key_type.value,
            key_value      = req.key_value,
            amount         = req.amount,
            payment_method = req.payment_method,
            description    = req.description,
        )
        _record(result, "key")
        return _format_pix_response(result)
    except PixPreparationError as e:
        request_id = _request_id(request)
        log.warning(
            "PIX preparation failed request_id=%s stage=%s provider_http=%s cause=%s",
            request_id, e.stage, e.provider_status, type(e.__cause__).__name__,
        )
        raise HTTPException(status_code=e.status_code, detail=_pix_failure_detail(
            request_id, retry_safe=True, code=e.code,
            message="O pagamento não foi iniciado. Verifique a chave e a carteira PIX antes de tentar novamente.",
        ))
    except PixError as e:
        request_id = _request_id(request)
        raise HTTPException(status_code=e.status_code, detail=_pix_failure_detail(
            request_id, retry_safe=False, code=e.code or "payment_outcome_unknown",
            message="Não foi possível confirmar o pagamento. Consulte o provedor antes de qualquer reenvio.",
        ))
    except Exception:
        request_id = _request_id(request)
        log.error("PIX key payment failed request_id=%s stack=%s", request_id, _sanitized_stack())
        raise HTTPException(status_code=500, detail=_pix_failure_detail(
            request_id, retry_safe=False, code="payment_outcome_unknown",
            message="Não foi possível confirmar o resultado do pagamento. Consulte o provedor antes de qualquer reenvio.",
        ))


@router.post(
    "/contact",
    response_model=PixResponse,
    response_model_exclude_none=True,
    response_class=PrettyJSONResponse,
    summary="Pagar PIX para contato recente (personId + accountId)",
)
def pagar_por_contato(req: PixContactRequest, request: Request):
    """
    Executa o fluxo completo de pagamento PIX para um contato já conhecido.

    Usa `inputMethod=RECENT` com `personId` e `accountId` do contato.
    Ideal para pagamentos recorrentes ao mesmo destinatário.
    """
    try:
        result = fluxo_pix_por_contato(
            person_id      = req.person_id,
            account_id     = req.account_id,
            amount         = req.amount,
            payment_method = req.payment_method,
            description    = req.description,
        )
        _record(result, "contact")
        return _format_pix_response(result)
    except PixError as e:
        request_id = _request_id(request)
        raise HTTPException(status_code=e.status_code, detail=_pix_failure_detail(
            request_id, retry_safe=True, code=e.code or "pix_rejected", message=e.message
        ))
    except Exception:
        request_id = _request_id(request)
        log.error("PIX contact payment failed request_id=%s stack=%s", request_id, _sanitized_stack())
        raise HTTPException(status_code=500, detail=_pix_failure_detail(
            request_id, retry_safe=False, code="payment_outcome_unknown",
            message="Não foi possível confirmar o resultado do pagamento. Consulte o provedor antes de qualquer reenvio.",
        ))


@router.get(
    "/history",
    response_model=HistoryResponse,
    summary="Histórico de transações PIX",
)
def get_history(limit: int = 50, offset: int = 0):
    """
    Retorna o histórico de transações PIX executadas nesta sessão.
    Máximo de 500 registros em memória.
    """
    paginated = _history[offset: offset + limit]
    return HistoryResponse(total=len(_history), transactions=paginated)


@router.get(
    "/contacts",
    summary="Listar contatos recentes e favoritos",
)
def list_contacts():
    """Retorna os contatos recentes e favoritos do usuário."""
    try:
        return get_pix_contact_overview()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/keys",
    summary="Listar chaves PIX cadastradas na conta",
)
def list_keys():
    """Retorna as chaves PIX cadastradas na conta RecargaPay do usuário."""
    try:
        return get_pix_keys()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/participants",
    summary="Listar participantes do PIX (bancos)",
)
def list_participants():
    """Retorna a lista de participantes do sistema PIX (instituições financeiras)."""
    try:
        return get_pix_participants()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/balance",
    response_model=BalanceResponse,
    response_model_exclude_none=True,
    response_class=PrettyJSONResponse,
    summary="Consultar saldo da carteira",
)
def get_wallet_balance():
    """Retorna o saldo disponível na carteira RecargaPay."""
    try:
        return _format_balance_response(get_balance())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/creditcards",
    summary="Listar cartões de crédito cadastrados",
)
def list_creditcards():
    """Retorna os cartões de crédito cadastrados na conta."""
    try:
        return get_creditcards()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/integrity-hash",
    response_model=IntegrityHashResponse,
    summary="Gerar integrityHash para uma chave PIX",
)
def generate_integrity_hash(key_value: str):
    """
    Gera o `integrityHash` para uma chave PIX.

    Algoritmo: `HMAC-SHA256(key=PIX_HMAC_SECRET, msg=key_value)`

    Extraído do `classes10.dex` do APK RecargaPay 5.11.6 via engenharia reversa.
    """
    return IntegrityHashResponse(
        key_value      = key_value,
        integrity_hash = build_integrity_hash(key_value),
    )


@router.post(
    "/lookup",
    response_model=PixLookupResponse,
    response_model_exclude_none=True,
    response_class=PrettyJSONResponse,
    summary="Consultar dados do destinatário por chave PIX (sem pagar)",
)
def lookup_key(key_type: str, key_value: str):
    """
    Consulta os dados do destinatário de uma chave PIX sem realizar o pagamento.
    Útil para validar a chave antes de enviar o pagamento.
    """
    try:
        result = post_pix_payment_by_key(key_type, key_value)
        return _format_lookup_response(result, key_type, key_value)
    except PixError as e:
        raise HTTPException(status_code=e.status_code, detail={
            "code":    e.code,
            "title":   e.title,
            "message": e.message,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
