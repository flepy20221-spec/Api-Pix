import unittest

from app.models.schemas import BalanceResponse, PixLookupResponse, PixResponse
from app.routers.pix import (
    _format_balance_response,
    _format_lookup_response,
    _format_pix_response,
)


class PixResponseTest(unittest.TestCase):
    def test_formats_provider_response_without_internal_payload(self):
        provider_response = {
            "success": True,
            "pix_id": "pix-123",
            "cart_id": "cart-123",
            "run_id": "run-123",
            "amount": "0.01",
            "payment_method": "WALLET",
            "receiver": {
                "key": "12345678909",
                "keyType": "CPF",
                "owner": {
                    "name": "Destinatario",
                    "taxIdNumber": "***.456.789-**",
                },
                "account": {
                    "participant": "22896431",
                    "participantName": "PICPAY",
                    "accountNumber": "*****",
                },
            },
            "result": {
                "status": 101,
                "title": "Prontinho! Pix feito.",
                "currency": "BRL",
                "orderId": 123456,
                "callToActions": [
                    {
                        "url": "https://recargapay.com.br/user/history/123456/voucher",
                    }
                ],
                "bannerCarousel": [{"internal": "must not be returned"}],
            },
            "timestamp": "2026-09-12T17:11:51.687948",
            "key_type": "CPF",
            "key_value": "12345678909",
        }

        formatted = _format_pix_response(provider_response)
        response = PixResponse.model_validate(formatted).model_dump(exclude_none=True)

        self.assertTrue(response["success"])
        self.assertEqual(response["transaction"]["status"], "completed")
        self.assertEqual(response["transaction"]["amount"]["formatted"], "R$ 0,01")
        self.assertEqual(response["receiver"]["institution"]["name"], "PICPAY")
        self.assertNotIn("result", response)
        self.assertNotIn("bannerCarousel", str(response))

    def test_formats_balance_with_only_nonzero_sources(self):
        provider_response = {
            "amount": 0.09,
            "blocked": 0.0,
            "amountForDiscounts": 0.09,
            "currency": "BRL",
            "formattedAmount": "R$ 0,09",
            "formattedBlocked": "R$ 0,00",
            "walletOk": True,
            "accounts": {
                "user-cashin-pix": {
                    "amount": 0.09,
                    "blocked": 0.0,
                    "formattedAmount": "R$ 0,09",
                    "tags": ["available"],
                },
                "user-cashback": {
                    "amount": 0.0,
                    "blocked": 0.0,
                    "formattedAmount": "R$ 0,00",
                    "tags": ["available"],
                },
            },
        }

        formatted = _format_balance_response(provider_response)
        response = BalanceResponse.model_validate(formatted).model_dump()

        self.assertTrue(response["success"])
        self.assertEqual(response["balance"]["available"], 0.09)
        self.assertEqual(response["balance"]["formatted_available"], "R$ 0,09")
        self.assertEqual(len(response["sources"]), 1)
        self.assertEqual(response["sources"][0]["id"], "user-cashin-pix")
        self.assertNotIn("user-cashback", str(response))

    def test_formats_lookup_without_provider_internal_fields(self):
        provider_response = {
            "id": "pix-lookup-123",
            "receiver": {
                "key": "12345678909",
                "keyType": "CPF",
                "temporaryFavoriteId": "internal-id",
                "owner": {
                    "name": "Cliente Exemplo",
                    "taxIdNumber": "***.456.789-**",
                    "type": "NATURAL_PERSON",
                    "isSameOwner": False,
                },
                "account": {
                    "participant": "22896431",
                    "participantName": "BANCO EXEMPLO",
                    "branch": "***",
                    "accountNumber": "*****",
                },
            },
        }

        formatted = _format_lookup_response(
            provider_response,
            key_type="cpf",
            key_value="12345678909",
        )
        response = PixLookupResponse.model_validate(formatted).model_dump(
            exclude_none=True
        )

        self.assertTrue(response["found"])
        self.assertEqual(response["receiver"]["key_type"], "CPF")
        self.assertEqual(response["receiver"]["institution"]["ispb"], "22896431")
        self.assertNotIn("temporaryFavoriteId", str(response))


if __name__ == "__main__":
    unittest.main()

class PixKeyNormalizationTest(unittest.TestCase):
    def test_normalizes_formatted_cpf_before_use(self):
        from app.models.schemas import PixKeyRequest

        request = PixKeyRequest(key_type="CPF", key_value="123.456.789-09")
        self.assertEqual(request.key_value, "12345678909")

    def test_normalizes_formatted_cnpj_before_use(self):
        from app.models.schemas import PixKeyRequest

        request = PixKeyRequest(key_type="CNPJ", key_value="11.222.333/0001-81")
        self.assertEqual(request.key_value, "11222333000181")

    def test_rejects_invalid_cpf_or_cnpj_check_digits(self):
        from pydantic import ValidationError
        from app.models.schemas import PixKeyRequest

        for key_type, key_value in (("CPF", "123.456.789-00"), ("CNPJ", "11.222.333/0001-80")):
            with self.subTest(key_type=key_type), self.assertRaises(ValidationError):
                PixKeyRequest(key_type=key_type, key_value=key_value)

    def test_normalizes_key_before_integrity_hash_and_provider_request(self):
        from unittest.mock import patch
        from app import core

        with patch.object(core, "PIX_HMAC_SECRET", "unit-test-only"), patch.object(core.session, "post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"id": "fake"}
            post.return_value.raise_for_status.return_value = None
            core.post_pix_payment_by_key("CPF", "123.456.789-09")
            sent_payload = post.call_args.kwargs["json"]
            self.assertEqual(sent_payload["receiver"]["key"], "12345678909")
            self.assertEqual(sent_payload["integrityHash"], core.build_integrity_hash("12345678909"))


class PixUncertainOutcomeTest(unittest.TestCase):
    def test_terminal_provider_failure_takes_precedence_over_done_flag(self):
        formatted = _format_pix_response({
            "success": True,
            "amount": "1.00",
            "receiver": {},
            "result": {"status": "failed", "done": True},
        })
        self.assertFalse(formatted["success"])
        self.assertEqual(formatted["transaction"]["status"], "failed")

    def test_processing_and_authentication_are_not_successful_or_retry_safe(self):
        for status in ("processing", "pending_authentication"):
            with self.subTest(status=status):
                formatted = _format_pix_response({
                    "success": True,
                    "amount": "1.00",
                    "receiver": {},
                    "result": {"status": "processing" if status == "processing" else "0", "action": "require-pin" if status == "pending_authentication" else "", "title": ""},
                })
                self.assertFalse(formatted["success"])
                self.assertFalse(formatted["retry_safe"])
                self.assertTrue(formatted["uncertain"])

    def test_definitive_provider_failure_is_retry_safe(self):
        formatted = _format_pix_response({
            "success": True,
            "amount": "1.00",
            "receiver": {},
            "result": {"status": "failed", "title": ""},
        })
        self.assertFalse(formatted["success"])
        self.assertTrue(formatted["retry_safe"])
        self.assertFalse(formatted["uncertain"])

    def test_structured_generic_payment_error_has_correlation_id_and_is_uncertain(self):
        from app.routers.pix import _pix_failure_detail

        detail = _pix_failure_detail(
            "3b241101-e2bb-4255-8caf-4136c566a962",
            retry_safe=False,
            code="payment_outcome_unknown",
            message="Não foi possível confirmar o resultado do pagamento.",
        )
        self.assertTrue(detail["uncertain"])
        self.assertFalse(detail["retry_safe"])
        self.assertEqual(detail["request_id"], "3b241101-e2bb-4255-8caf-4136c566a962")

class PixApiAuthTest(unittest.TestCase):
    def test_sensitive_endpoints_fail_closed_without_service_token(self):
        import os
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from main import app

        with patch.dict(os.environ, {"PIX_API_TOKEN": ""}, clear=False):
            response = TestClient(app).get("/pix/history")
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["retry_safe"])
        self.assertFalse(response.json()["uncertain"])

    def test_service_token_protects_pix_endpoint_but_health_remains_public(self):
        import os
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from main import app

        with patch.dict(os.environ, {"PIX_API_TOKEN": "unit-test-token"}, clear=False):
            client = TestClient(app)
            self.assertEqual(client.get("/pix/history").status_code, 401)
            self.assertEqual(client.get("/health").status_code, 200)
            response = client.get("/pix/history", headers={"Authorization": "Bearer unit-test-token"})
        self.assertEqual(response.status_code, 200)

    def test_generic_payment_exception_is_sanitized_and_non_retry_safe(self):
        import os
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from main import app

        with patch.dict(os.environ, {"PIX_API_TOKEN": "unit-test-token"}, clear=False):
            with patch("app.routers.pix.fluxo_pix_por_chave", side_effect=RuntimeError("PRIVATE-ERROR-CONTENT")):
                response = TestClient(app).post(
                    "/pix/key",
                    headers={"Authorization": "Bearer unit-test-token"},
                    json={"key_type": "CPF", "key_value": "123.456.789-09", "amount": "1.00"},
                )
        self.assertEqual(response.status_code, 500)
        detail = response.json()["detail"]
        self.assertFalse(detail["retry_safe"])
        self.assertTrue(detail["uncertain"])
        self.assertTrue(detail["request_id"])
        self.assertNotIn("PRIVATE-ERROR-CONTENT", response.text)

    def test_pre_purchase_failure_is_retry_safe_and_does_not_execute_purchase(self):
        import os
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app import core
        from main import app

        with patch.dict(os.environ, {"PIX_API_TOKEN": "unit-test-token"}, clear=False), \
             patch.object(core, "_ensure_auth"), \
             patch.object(core, "get_startup", return_value={}), \
             patch.object(core, "get_pins", return_value={"statusCode": 0}), \
             patch.object(core, "get_pix_participants", return_value=[]), \
             patch.object(core, "post_pix_payment_by_key", side_effect=RuntimeError("PRIVATE-ERROR-CONTENT")), \
             patch.object(core, "execute_purchase") as purchase:
            response = TestClient(app).post(
                "/pix/key",
                headers={"Authorization": "Bearer unit-test-token"},
                json={"key_type": "CPF", "key_value": "123.456.789-09", "amount": "1.00"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["detail"]["retry_safe"])
        self.assertFalse(response.json()["detail"]["uncertain"])
        self.assertNotIn("PRIVATE-ERROR-CONTENT", response.text)
        purchase.assert_not_called()

    def test_provider_lookup_502_explains_outage_without_leaking_response(self):
        import os
        import requests
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app import core
        from main import app

        provider_response = requests.Response()
        provider_response.status_code = 502
        provider_response._content = b"PRIVATE-PROVIDER-CONTENT"
        error = requests.HTTPError("PRIVATE-PROVIDER-CONTENT", response=provider_response)
        with patch.dict(os.environ, {"PIX_API_TOKEN": "unit-test-token"}, clear=False), \
             patch.object(core, "_ensure_auth"), \
             patch.object(core, "get_startup", return_value={}), \
             patch.object(core, "get_pins", return_value={"statusCode": 0}), \
             patch.object(core, "get_pix_participants", return_value=[]), \
             patch.object(core, "post_pix_payment_by_key", side_effect=error), \
             patch.object(core, "execute_purchase") as purchase:
            response = TestClient(app).post(
                "/pix/key",
                headers={"Authorization": "Bearer unit-test-token"},
                json={"key_type": "EMAIL", "key_value": "email@example.com", "amount": "2.04"},
            )
        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertTrue(detail["retry_safe"])
        self.assertFalse(detail["uncertain"])
        self.assertIn("não conseguiu consultar a chave", detail["message"])
        self.assertNotIn("PRIVATE-PROVIDER-CONTENT", response.text)
        purchase.assert_not_called()

    def test_purchase_failure_is_uncertain_and_does_not_retry_purchase(self):
        import os
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app import core
        from main import app

        with patch.dict(os.environ, {"PIX_API_TOKEN": "unit-test-token"}, clear=False), \
             patch.object(core, "_ensure_auth"), \
             patch.object(core, "get_startup", return_value={}), \
             patch.object(core, "get_pins", return_value={"statusCode": 0}), \
             patch.object(core, "get_pix_participants", return_value=[]), \
             patch.object(core, "post_pix_payment_by_key", return_value={"id": "pix-123", "receiver": {}}), \
             patch.object(core, "create_shopping_cart", return_value={"id": "cart-123"}), \
             patch.object(core, "set_payment_method", return_value={}), \
             patch.object(core, "post_sr_session", return_value={}), \
             patch.object(core, "execute_purchase", side_effect=RuntimeError("timeout")) as purchase:
            response = TestClient(app).post(
                "/pix/key",
                headers={"Authorization": "Bearer unit-test-token"},
                json={"key_type": "CPF", "key_value": "123.456.789-09", "amount": "1.00"},
            )
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json()["detail"]["retry_safe"])
        self.assertTrue(response.json()["detail"]["uncertain"])
        purchase.assert_called_once()

    def test_inconsistent_completed_result_remains_uncertain(self):
        formatted = _format_pix_response({
            "success": False,
            "amount": "1.00",
            "receiver": {},
            "result": {"status": "completed", "done": True},
        })
        self.assertFalse(formatted["success"])
        self.assertFalse(formatted["retry_safe"])
        self.assertTrue(formatted["uncertain"])
