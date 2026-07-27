from __future__ import annotations

import unittest

from monitor import MonitorError, Product, parse_product


def valid_payload(*, stock: int = 0) -> dict:
    return {
        "code": 200,
        "msg": "success",
        "data": [
            {
                "id": 68,
                "name": "GPT RT Plus 成品号（欧洲渠道）",
                "price": 14.99,
                "stock": stock,
            }
        ],
    }


class ParseProductTests(unittest.TestCase):
    def test_parses_target_product(self) -> None:
        product = parse_product(valid_payload(stock=3))

        self.assertEqual(
            product,
            Product(
                id=68,
                name="GPT RT Plus 成品号（欧洲渠道）",
                price=14.99,
                stock=3,
            ),
        )

    def test_rejects_missing_target_product(self) -> None:
        payload = valid_payload()
        payload["data"][0]["id"] = 90

        with self.assertRaisesRegex(MonitorError, "商品 68"):
            parse_product(payload)

    def test_rejects_invalid_stock(self) -> None:
        payload = valid_payload()
        payload["data"][0]["stock"] = "1"

        with self.assertRaisesRegex(MonitorError, "库存"):
            parse_product(payload)

    def test_rejects_api_error(self) -> None:
        payload = {"code": 500, "msg": "failed", "data": []}

        with self.assertRaisesRegex(MonitorError, "接口"):
            parse_product(payload)


if __name__ == "__main__":
    unittest.main()
