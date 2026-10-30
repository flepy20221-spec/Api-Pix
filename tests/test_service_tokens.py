import unittest
from unittest.mock import patch

from main import _valid_service_token


class ServiceTokenTest(unittest.TestCase):
    def test_cashpix_and_coinpix_tokens_are_independently_accepted(self):
        with patch.dict("os.environ", {
            "PIX_API_TOKEN": "cashpix-secret",
            "COINPIX_PIX_API_TOKEN": "coinpix-secret",
        }):
            self.assertTrue(_valid_service_token("cashpix-secret"))
            self.assertTrue(_valid_service_token("coinpix-secret"))
            self.assertFalse(_valid_service_token("wrong"))
            self.assertFalse(_valid_service_token(""))

    def test_missing_tokens_fail_closed(self):
        with patch.dict("os.environ", {
            "PIX_API_TOKEN": "",
            "COINPIX_PIX_API_TOKEN": "",
        }):
            self.assertFalse(_valid_service_token("cashpix-secret"))


if __name__ == "__main__":
    unittest.main()
