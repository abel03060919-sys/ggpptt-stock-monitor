from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://ggpptt.store/user/api/index/commodity?categoryId=2"
PRODUCT_ID = 68
SITE_URL = "https://ggpptt.store/"
REQUEST_TIMEOUT_SECONDS = 15


class MonitorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    price: float
    stock: int


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
