# Stock Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a public GitHub Actions monitor that checks product 68 every five minutes and sends one Gmail alert per restock cycle without exposing credentials.

**Architecture:** A single standard-library Python module owns API validation, state transitions, Gmail delivery, and the command entry point. A JSON file persists the alert state and a 30-day keepalive date; one GitHub Actions workflow runs the module and commits that file only when it changes.

**Tech Stack:** Python 3.12 standard library, `unittest`, GitHub Actions, Gmail SMTP over SSL.

---

## File map

- Create `monitor.py`: inventory API client, validated models, state machine, Gmail sender, and CLI.
- Create `tests/test_monitor.py`: isolated unit tests with fake inventory and SMTP dependencies.
- Create `state.json`: public non-sensitive initial state and keepalive date.
- Create `.github/workflows/monitor.yml`: five-minute schedule, manual test-email input, state commit, and concurrency lock.
- Create `README.md`: Chinese setup instructions for Gmail Secrets and first manual run.
- Modify `docs/superpowers/specs/2026-07-27-stock-monitor-design.md`: record the required 30-day keepalive behavior.

### Task 1: Inventory response validation

**Files:**
- Create: `tests/test_monitor.py`
- Create: `monitor.py`

- [ ] **Step 1: Write failing parser tests**

Create `tests/test_monitor.py`:

```python
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
```

- [ ] **Step 2: Run the parser tests and verify failure**

Run:

```powershell
python -m unittest tests.test_monitor -v
```

Expected: import failure because `monitor.py` does not exist.

- [ ] **Step 3: Implement the validated product parser and API request**

Create the first part of `monitor.py`:

```python
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
```

- [ ] **Step 4: Run parser tests and verify success**

Run:

```powershell
python -m unittest tests.test_monitor -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit inventory validation**

Run:

```powershell
git add monitor.py tests/test_monitor.py
git commit -m "feat: validate monitored inventory"
```

Expected: one commit containing only the parser, API request, and parser tests.

### Task 2: State transitions, keepalive, and Gmail

**Files:**
- Modify: `tests/test_monitor.py`
- Modify: `monitor.py`

- [ ] **Step 1: Add failing state-machine and email tests**

Add these imports and helpers to `tests/test_monitor.py`:

```python
import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from monitor import State, load_state, run, send_email


def product_with_stock(stock: int) -> Product:
    return Product(
        id=68,
        name="GPT RT Plus 成品号（欧洲渠道）",
        price=14.99,
        stock=stock,
    )
```

Add the following test classes before the `if __name__ == "__main__"` block:

```python
class MonitorRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
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

        self.assertEqual(
            calls["login"],
            ("owner@example.com", "secret-value"),
        )
        self.assertNotIn("secret-value", calls["message"].as_string())
```

- [ ] **Step 2: Run state tests and verify failure**

Run:

```powershell
python -m unittest tests.test_monitor -v
```

Expected: import failure for `State`, `load_state`, `run`, or `send_email`.

- [ ] **Step 3: Implement validated state, transition logic, and Gmail delivery**

Add these imports to `monitor.py`:

```python
import os
import smtplib
import ssl
from dataclasses import replace
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Mapping
```

Add these constants and implementations after `Product`:

```python
STATE_PATH = Path("state.json")
KEEPALIVE_DAYS = 30
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class State:
    in_stock: bool
    last_keepalive: date


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
```

- [ ] **Step 4: Run all unit tests and verify success**

Run:

```powershell
python -m unittest tests.test_monitor -v
```

Expected: all parser, transition, keepalive, retry, and SMTP tests pass without network access.

- [ ] **Step 5: Commit state and Gmail behavior**

Run:

```powershell
git add monitor.py tests/test_monitor.py
git commit -m "feat: add restock email state machine"
```

Expected: one commit containing the state machine, email sender, and their tests.

### Task 3: Command entry point and initial state

**Files:**
- Modify: `monitor.py`
- Create: `state.json`

- [ ] **Step 1: Add a failing CLI smoke test**

Add imports to `tests/test_monitor.py`:

```python
from unittest.mock import patch

from monitor import main
```

Add this class before the executable test block:

```python
class CliTests(unittest.TestCase):
    def test_main_returns_one_for_monitor_error(self) -> None:
        with patch("monitor.run", side_effect=MonitorError("测试错误")):
            self.assertEqual(main([]), 1)

    def test_main_forwards_test_email_flag(self) -> None:
        with patch("monitor.run", return_value=False) as run_mock:
            self.assertEqual(main(["--send-test-email"]), 0)

        run_mock.assert_called_once_with(send_test_email=True)
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run:

```powershell
python -m unittest tests.test_monitor.CliTests -v
```

Expected: import failure because `main` is not defined.

- [ ] **Step 3: Add the CLI and initial public state**

Add imports to `monitor.py`:

```python
import argparse
import sys
from collections.abc import Sequence
```

Append:

```python
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="监控 ggpptt.store 商品库存")
    parser.add_argument(
        "--send-test-email",
        action="store_true",
        help="发送一封 Gmail 配置测试邮件，不改变库存状态",
    )
    arguments = parser.parse_args(argv)
    try:
        run(send_test_email=arguments.send_test_email)
    except MonitorError as error:
        print(f"监控失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `state.json`:

```json
{
  "in_stock": false,
  "last_keepalive": "2026-07-27"
}
```

- [ ] **Step 4: Run CLI and complete test suite**

Run:

```powershell
python monitor.py --help
python -m unittest discover -s tests -v
```

Expected: help text lists `--send-test-email`; all tests pass.

- [ ] **Step 5: Commit the runnable command**

Run:

```powershell
git add monitor.py tests/test_monitor.py state.json
git commit -m "feat: add stock monitor command"
```

Expected: one commit with the CLI, its tests, and the initial state.

### Task 4: GitHub Actions workflow and setup guide

**Files:**
- Create: `.github/workflows/monitor.yml`
- Create: `README.md`

- [ ] **Step 1: Create the scheduled workflow**

Create `.github/workflows/monitor.yml`:

```yaml
name: Stock monitor

on:
  schedule:
    - cron: "2-57/5 * * * *"
  workflow_dispatch:
    inputs:
      send_test_email:
        description: Send a Gmail configuration test
        required: false
        default: false
        type: boolean

permissions:
  contents: write

concurrency:
  group: ggpptt-stock-monitor
  cancel-in-progress: false

jobs:
  monitor:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Check out repository
        uses: actions/checkout@v6

      - name: Check inventory
        env:
          GMAIL_ADDRESS: ${{ secrets.GMAIL_ADDRESS }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
          SEND_TEST_EMAIL: ${{ inputs.send_test_email }}
        shell: bash
        run: |
          arguments=()
          if [[ "$SEND_TEST_EMAIL" == "true" ]]; then
            arguments+=(--send-test-email)
          fi
          python3 monitor.py "${arguments[@]}"

      - name: Save state change
        shell: bash
        run: |
          if git diff --quiet -- state.json; then
            echo "No state change"
            exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add state.json
          git commit -m "chore: update stock monitor state"
          git push
```

- [ ] **Step 2: Write the concise Chinese setup guide**

Create `README.md` with:

```markdown
# ggpptt.store 到货提醒

每 5 分钟检查商品 `GPT RT Plus 成品号（欧洲渠道）`（ID 68）。补货时使用 Gmail 给自己发送一次邮件；持续有货不会重复发送。

## 启用

1. 在 GitHub 新建一个 **Public** 仓库，并把本项目推送到默认分支。
2. 在 Google 账号中开启两步验证，然后创建一个“应用专用密码”。
3. 打开 GitHub 仓库的 `Settings → Secrets and variables → Actions`。
4. 新建两个 Repository secrets：
   - `GMAIL_ADDRESS`：你的完整 Gmail 地址。
   - `GMAIL_APP_PASSWORD`：Google 生成的应用专用密码，不是 Gmail 登录密码。
5. 打开仓库的 `Actions → Stock monitor → Run workflow`，勾选测试邮件并运行。
6. 收到“到货监控测试邮件”后配置完成，之后无需保持电脑开机。

## 提醒规则

- 库存从 0 变为大于 0：发送一次邮件。
- 库存持续大于 0：不重复发送。
- 库存再次变为 0：重置，等待下一次补货。
- 公开仓库每 30 天自动提交一次非敏感保活日期，避免 GitHub 因 60 天无活动停用定时任务。

## 安全

邮箱地址和应用专用密码只保存在 GitHub Secrets 中。代码、`state.json` 和运行日志不会输出这些值。

## 手动检查

在 Actions 页面运行 `Stock monitor`。不勾选测试邮件时，它只执行一次正常库存检查。
```

- [ ] **Step 3: Validate workflow shape and secret handling**

Run:

```powershell
rg -n "GMAIL_ADDRESS|GMAIL_APP_PASSWORD|schedule|workflow_dispatch|contents: write|concurrency" .github/workflows/monitor.yml README.md
rg -n "gmail\\.com|secret-value|app-password|owner@example\\.com" monitor.py state.json .github README.md
```

Expected: the first command shows the documented variable names and workflow controls. The second command finds only `smtp.gmail.com` in `monitor.py`; it finds no test password or example account in production/configuration files.

- [ ] **Step 4: Run all local checks**

Run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q monitor.py tests
git diff --check
```

Expected: all tests pass, compilation succeeds silently, and `git diff --check` prints nothing.

- [ ] **Step 5: Commit workflow and guide**

Run:

```powershell
git add .github/workflows/monitor.yml README.md
git commit -m "ci: schedule stock monitor"
```

Expected: one commit containing the workflow and setup guide.

### Task 5: Read-only live verification and final review

**Files:**
- Modify only if a verification failure reveals a defect in an in-scope file.

- [ ] **Step 1: Verify the public endpoint without sending email**

Run:

```powershell
python -c "from monitor import fetch_product; print(fetch_product())"
```

Expected: a `Product` with `id=68`; stock may legitimately be zero or greater.

- [ ] **Step 2: Run the final verification suite**

Run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q monitor.py tests
git diff --check
git status --short
```

Expected: tests pass, compilation and diff checks are silent, and status contains no unintended files.

- [ ] **Step 3: Review the complete diff**

Run:

```powershell
git diff HEAD~4..HEAD -- monitor.py tests/test_monitor.py state.json .github/workflows/monitor.yml README.md docs/superpowers
```

Expected: only the approved monitor, tests, workflow, state, README, design, and plan changes appear; no email address or app password appears.

- [ ] **Step 4: Hand off GitHub setup**

Report the local files and tests, then guide the user through creating the public repository and adding the two GitHub Secrets. Do not request, display, or store the user’s Gmail application password.
