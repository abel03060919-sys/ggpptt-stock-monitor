from __future__ import annotations

import json
import os
import smtplib
import ssl
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://ggpptt.store/user/api/index/commodity?categoryId=2"
PRODUCT_ID = 68
SITE_URL = "https://ggpptt.store/"
REQUEST_TIMEOUT_SECONDS = 15
STATE_PATH = Path("state.json")
KEEPALIVE_DAYS = 30
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT_SECONDS = 20


class MonitorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    price: float
    stock: int


@dataclass(frozen=True)
class State:
    in_stock: bool
    last_keepalive: date


def parse_product(payload: Any) -> Product:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise MonitorError("库存接口返回错误")

    items = payload.get("data")
    if not isinstance(items, list):
        raise MonitorError("库存接口缺少商品列表")

    item = next(
        (
            candidate
            for candidate in items
            if isinstance(candidate, dict) and candidate.get("id") == PRODUCT_ID
        ),
        None,
    )
    if item is None:
        raise MonitorError(f"库存接口中找不到商品 {PRODUCT_ID}")

    name = item.get("name")
    price = item.get("price")
    stock = item.get("stock")
    if not isinstance(name, str) or not name.strip():
        raise MonitorError("商品名称无效")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
        raise MonitorError("商品价格无效")
    if isinstance(stock, bool) or not isinstance(stock, int) or stock < 0:
        raise MonitorError("商品库存无效")

    return Product(id=PRODUCT_ID, name=name, price=float(price), stock=stock)


def fetch_product() -> Product:
    request = Request(
        API_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "ggpptt-stock-monitor/1.0",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise MonitorError(f"库存接口 HTTP 状态异常：{response.status}")
            payload = json.load(response)
    except MonitorError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise MonitorError("读取库存接口失败") from None

    return parse_product(payload)


def load_state(path: Path) -> State:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        in_stock = payload["in_stock"]
        last_keepalive = date.fromisoformat(payload["last_keepalive"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise MonitorError("状态文件无效") from None

    if not isinstance(in_stock, bool):
        raise MonitorError("状态文件中的 in_stock 必须是布尔值")
    return State(in_stock=in_stock, last_keepalive=last_keepalive)


def save_state(path: Path, state: State) -> None:
    payload = {
        "in_stock": state.in_stock,
        "last_keepalive": state.last_keepalive.isoformat(),
    }
    temporary_path = path.with_name(f"{path.name}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError:
        raise MonitorError("保存状态文件失败") from None


def require_credentials(environ: Mapping[str, str]) -> tuple[str, str]:
    address = environ.get("GMAIL_ADDRESS", "").strip()
    password = environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not address or not password:
        raise MonitorError(
            "缺少 GitHub Secrets：GMAIL_ADDRESS 或 GMAIL_APP_PASSWORD"
        )
    return address, password


def send_email(
    address: str,
    password: str,
    product: Product,
    is_test: bool,
    *,
    smtp_factory: Callable[..., smtplib.SMTP_SSL] = smtplib.SMTP_SSL,
) -> None:
    message = EmailMessage()
    message["From"] = address
    message["To"] = address
    if is_test:
        message["Subject"] = "✅ 到货监控测试邮件"
        heading = "Gmail 提醒配置成功。"
    else:
        message["Subject"] = f"📦 到货提醒：{product.name}"
        heading = "监控商品已经到货。"
    checked_at = datetime.now(timezone.utc).astimezone()
    message.set_content(
        "\n".join(
            [
                heading,
                "",
                f"商品：{product.name}",
                f"库存：{product.stock}",
                f"价格：{product.price:.2f}",
                f"检查时间：{checked_at:%Y-%m-%d %H:%M:%S %z}",
                f"网站：{SITE_URL}",
            ]
        )
    )

    try:
        context = ssl.create_default_context()
        with smtp_factory(
            SMTP_HOST,
            SMTP_PORT,
            context=context,
            timeout=SMTP_TIMEOUT_SECONDS,
        ) as smtp:
            smtp.login(address, password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException):
        raise MonitorError("Gmail 邮件发送失败") from None


def run(
    *,
    state_path: Path = STATE_PATH,
    fetcher: Callable[[], Product] = fetch_product,
    sender: Callable[[str, str, Product, bool], None] = send_email,
    environ: Mapping[str, str] | None = None,
    today: date | None = None,
    send_test_email: bool = False,
) -> bool:
    environment = os.environ if environ is None else environ
    current_date = date.today() if today is None else today
    state = load_state(state_path)
    product = fetcher()

    if send_test_email:
        address, password = require_credentials(environment)
        sender(address, password, product, True)
        print("测试邮件已发送")
        return False

    new_state = state
    currently_in_stock = product.stock > 0
    if not state.in_stock and currently_in_stock:
        address, password = require_credentials(environment)
        sender(address, password, product, False)
        new_state = replace(new_state, in_stock=True)
        print(f"检测到到货并已发送提醒，库存：{product.stock}")
    elif state.in_stock and not currently_in_stock:
        new_state = replace(new_state, in_stock=False)
        print("商品已售罄，提醒状态已重置")
    else:
        print(f"库存状态未变化，当前库存：{product.stock}")

    if (current_date - state.last_keepalive).days >= KEEPALIVE_DAYS:
        new_state = replace(new_state, last_keepalive=current_date)

    if new_state != state:
        save_state(state_path, new_state)
        return True
    return False
