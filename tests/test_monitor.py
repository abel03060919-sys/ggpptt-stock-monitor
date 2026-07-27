from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from monitor import (
    MonitorError,
    Product,
    State,
    load_state,
    main,
    parse_product,
    run,
    send_email,
)


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


def product_with_stock(stock: int) -> Product:
    return Product(
        id=68,
        name="GPT RT Plus 成品号（欧洲渠道）",
        price=14.99,
        stock=stock,
    )


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


class MonitorRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        print_patcher = patch("builtins.print")
        print_patcher.start()
        self.addCleanup(print_patcher.stop)
        self.state_path = Path(self.temp_dir.name) / "state.json"
        self.state_path.write_text(
            json.dumps(
                {"in_stock": False, "last_keepalive": "2026-07-27"}
            ),
            encoding="utf-8",
        )
        self.sent: list[tuple[str, bool]] = []

    def sender(
        self,
        address: str,
        password: str,
        product: Product,
        is_test: bool,
    ) -> None:
        self.assertEqual(password, "app-password")
        self.sent.append((address, is_test))

    @property
    def environment(self) -> dict[str, str]:
        return {
            "GMAIL_ADDRESS": "owner@example.com",
            "GMAIL_APP_PASSWORD": "app-password",
        }

    def test_out_of_stock_stays_silent(self) -> None:
        changed = run(
            state_path=self.state_path,
            fetcher=lambda: product_with_stock(0),
            sender=self.sender,
            environ=self.environment,
            today=date(2026, 7, 27),
        )

        self.assertFalse(changed)
        self.assertEqual(self.sent, [])

    def test_restock_sends_once_and_sets_state(self) -> None:
        changed = run(
            state_path=self.state_path,
            fetcher=lambda: product_with_stock(2),
            sender=self.sender,
            environ=self.environment,
            today=date(2026, 7, 27),
        )

        self.assertTrue(changed)
        self.assertEqual(self.sent, [("owner@example.com", False)])
        self.assertTrue(load_state(self.state_path).in_stock)

        changed_again = run(
            state_path=self.state_path,
            fetcher=lambda: product_with_stock(2),
            sender=self.sender,
            environ=self.environment,
            today=date(2026, 7, 27),
        )
        self.assertFalse(changed_again)
        self.assertEqual(self.sent, [("owner@example.com", False)])

    def test_sold_out_resets_for_the_next_restock(self) -> None:
        self.state_path.write_text(
            json.dumps(
                {"in_stock": True, "last_keepalive": "2026-07-27"}
            ),
            encoding="utf-8",
        )

        changed = run(
            state_path=self.state_path,
            fetcher=lambda: product_with_stock(0),
            sender=self.sender,
            environ=self.environment,
            today=date(2026, 7, 27),
        )

        self.assertTrue(changed)
        self.assertFalse(load_state(self.state_path).in_stock)
        self.assertEqual(self.sent, [])

    def test_email_failure_does_not_change_state(self) -> None:
        def failing_sender(
            address: str,
            password: str,
            product: Product,
            is_test: bool,
        ) -> None:
            raise MonitorError("Gmail 邮件发送失败")

        with self.assertRaisesRegex(MonitorError, "Gmail"):
            run(
                state_path=self.state_path,
                fetcher=lambda: product_with_stock(1),
                sender=failing_sender,
                environ=self.environment,
                today=date(2026, 7, 27),
            )

        self.assertFalse(load_state(self.state_path).in_stock)

    def test_keepalive_updates_only_after_thirty_days(self) -> None:
        unchanged = run(
            state_path=self.state_path,
            fetcher=lambda: product_with_stock(0),
            sender=self.sender,
            environ=self.environment,
            today=date(2026, 8, 25),
        )
        self.assertFalse(unchanged)

        changed = run(
            state_path=self.state_path,
            fetcher=lambda: product_with_stock(0),
            sender=self.sender,
            environ=self.environment,
            today=date(2026, 8, 26),
        )
        self.assertTrue(changed)
        self.assertEqual(
            load_state(self.state_path).last_keepalive,
            date(2026, 8, 26),
        )

    def test_test_email_does_not_change_state(self) -> None:
        changed = run(
            state_path=self.state_path,
            fetcher=lambda: product_with_stock(0),
            sender=self.sender,
            environ=self.environment,
            today=date(2026, 7, 27),
            send_test_email=True,
        )

        self.assertFalse(changed)
        self.assertEqual(self.sent, [("owner@example.com", True)])


class GmailTests(unittest.TestCase):
    def test_smtp_uses_login_and_does_not_put_password_in_message(self) -> None:
        calls: dict[str, object] = {}

        class FakeSmtp:
            def __init__(self, host: str, port: int, **kwargs: object) -> None:
                calls["connection"] = (host, port, kwargs)

            def __enter__(self) -> "FakeSmtp":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def login(self, address: str, password: str) -> None:
                calls["login"] = (address, password)

            def send_message(self, message: object) -> None:
                calls["message"] = message

        send_email(
            "owner@example.com",
            "secret-value",
            product_with_stock(5),
            False,
            smtp_factory=FakeSmtp,
        )

        self.assertIn("login", calls)
        self.assertEqual(
            calls["login"],
            ("owner@example.com", "secret-value"),
        )
        self.assertNotIn("secret-value", calls["message"].as_string())


class CliTests(unittest.TestCase):
    def test_main_returns_one_for_monitor_error(self) -> None:
        with (
            patch("builtins.print"),
            patch("monitor.run", side_effect=MonitorError("测试错误")),
        ):
            self.assertEqual(main([]), 1)

    def test_main_forwards_test_email_flag(self) -> None:
        with patch("monitor.run", return_value=False) as run_mock:
            self.assertEqual(main(["--send-test-email"]), 0)

        run_mock.assert_called_once_with(send_test_email=True)


if __name__ == "__main__":
    unittest.main()
