"""Dev 模拟微信支付回调脚本 (BE-S3-010).

用途
====
开发 / CI 环境下没真商户号 / 私钥, 但需要走完整支付链路验证状态机. 本脚本走
``StubWechatPayClient`` 路径 — 构造 4 种回调 fixture 直接打 ``/api/v1/pay/wechat/notify``,
观察 ``vip_orders`` / ``vip_memberships`` 状态流转.

前置条件
========
1. 应用已起 (``uv run uvicorn app.main:app --reload``); 默认 :8000
2. ``WECHATPAY_DEV_MODE=true`` (默认); 即走 Stub client
3. 提供一个已存在的 ``out_trade_no`` (可用 dev 账号下单后从日志拿)

用法
====
::

    # 1) 成功回调
    uv run python -m scripts.dev_wechatpay_simulate_callback \\
        --out-trade-no XGZH20260427120000ABCDEF \\
        --amount-cents 3900 \\
        --scenario success

    # 2) 验签失败 (没传 X-Stub-Sign-Override header)
    uv run python -m scripts.dev_wechatpay_simulate_callback \\
        --out-trade-no XGZH... --scenario sig-fail

    # 3) 重投幂等 (同 out_trade_no 二次调用 → 服务端 SUCCESS 不重复处理)
    uv run python -m scripts.dev_wechatpay_simulate_callback \\
        --out-trade-no XGZH... --amount-cents 3900 --scenario success
    # 再跑一次 → 看 logs 里 'wechatpay.callback.idempotent'

    # 4) 金额不匹配 (审计关键场景, 服务端 FAIL)
    uv run python -m scripts.dev_wechatpay_simulate_callback \\
        --out-trade-no XGZH... --amount-cents 1 --scenario amount-mismatch

输出
====
打印 HTTP 状态 + body. 服务端日志同步会出 ``wechatpay.callback.*`` 一行.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Literal

import httpx

ScenarioLiteral = Literal["success", "non-success", "sig-fail", "amount-mismatch"]


def _build_body(
    *,
    out_trade_no: str,
    transaction_id: str,
    amount_cents: int,
    trade_state: str,
    openid: str,
) -> bytes:
    """模拟微信 v3 解密后的 ``data`` (Stub 直接走 JSON, 真协议是 AES-GCM 密文).

    Stub 验证后直接读 body 当 dict, 不走 resource.ciphertext 解密, 这就是 dev 优势.
    """
    payload = {
        "id": "SIMULATED-EVENT-ID",
        "create_time": "2026-04-27T12:00:00+08:00",
        "event_type": "TRANSACTION.SUCCESS",
        "resource_type": "encrypt-resource",
        "summary": "支付成功",
        "out_trade_no": out_trade_no,
        "transaction_id": transaction_id,
        "trade_state": trade_state,
        "amount": {"total": amount_cents, "payer_total": amount_cents, "currency": "CNY"},
        "payer": {"openid": openid},
        "success_time": "2026-04-27T12:00:00+08:00",
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dev 模拟微信支付回调")
    parser.add_argument(
        "--base-url", default="http://localhost:8000", help="API 根地址"
    )
    parser.add_argument(
        "--out-trade-no", required=True, help="商户订单号 (从 vip_orders 拿)"
    )
    parser.add_argument(
        "--transaction-id",
        default="WX-SIMULATED-TXN-1234567890",
        help="模拟微信支付订单号; 默认固定串便于幂等测试",
    )
    parser.add_argument(
        "--amount-cents",
        type=int,
        default=3900,
        help="金额 (分); 月度=3900, 季度=9900, 年度=29900, 终身=99900",
    )
    parser.add_argument(
        "--openid", default="stub_openid_simulate", help="支付者 openid"
    )
    parser.add_argument(
        "--scenario",
        choices=["success", "non-success", "sig-fail", "amount-mismatch"],
        default="success",
        help=(
            "success=trade_state=SUCCESS 标 paid; "
            "non-success=trade_state=PAYERROR 标 failed; "
            "sig-fail=不传 X-Stub-Sign-Override → 服务端 FAIL; "
            "amount-mismatch=故意改 amount-cents 不匹配 → 服务端 FAIL"
        ),
    )

    args = parser.parse_args()
    scenario: ScenarioLiteral = args.scenario

    trade_state = "PAYERROR" if scenario == "non-success" else "SUCCESS"
    body = _build_body(
        out_trade_no=args.out_trade_no,
        transaction_id=args.transaction_id,
        amount_cents=args.amount_cents,
        trade_state=trade_state,
        openid=args.openid,
    )

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if scenario != "sig-fail":
        # Stub 模式下唯一的"验签": 必须传这个 header
        headers["X-Stub-Sign-Override"] = "bypass"

    url = args.base_url.rstrip("/") + "/api/v1/pay/wechat/notify"
    print(
        f"POST {url} scenario={scenario} out_trade_no={args.out_trade_no} "
        f"amount_cents={args.amount_cents} trade_state={trade_state}",
        file=sys.stderr,
    )
    print("Headers:", headers, file=sys.stderr)
    print("Body:", body.decode("utf-8"), file=sys.stderr)

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(url, content=body, headers=headers)

    print(f"\n--- Response ---\nstatus={resp.status_code}\nbody={resp.text}")


if __name__ == "__main__":
    main()
