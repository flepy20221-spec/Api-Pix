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
