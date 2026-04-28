"""BE-S5-002 PII inventory 静态清单单元测.

跑得快 (无 DB), 仅校 PII 清单本身的内容 / 完备性 / 合规字段格式.
集成测在 ``tests/integration/test_admin_pii.py`` 走真 DB + admin token.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from app.services.compliance import pii_inventory
from app.services.compliance.pii_inventory import (
    CONSENT_MECHANISM,
    DATA_EXPORT_JURISDICTIONS,
    PII_INVENTORY,
    THIRD_PARTY_SDKS,
    LegalBasis,
    PIIItem,
)

# ─── 1. 清单结构 ────────────────────────────────────────────────────


def test_inventory_is_non_empty_immutable_tuple() -> None:
    assert isinstance(PII_INVENTORY, tuple)
    assert len(PII_INVENTORY) >= 12, "spec/12 §BE-S5-002 至少列 12 条 PII"
    # tuple 不可变, 防止运行时被改 (vibe coding 双保险)
    with pytest.raises(TypeError):
        PII_INVENTORY[0] = None  # type: ignore[index]


def test_each_item_is_pii_item_dataclass() -> None:
    for item in PII_INVENTORY:
        assert isinstance(item, PIIItem)
        # frozen dataclass — 不能改字段
        with pytest.raises(FrozenInstanceError):
            item.field = "hacked"  # type: ignore[misc]


# ─── 2. 字段必填 / 范围 ────────────────────────────────────────────


def test_each_item_has_required_text_fields() -> None:
    for item in PII_INVENTORY:
        assert item.field, f"{item} field 不能空"
        assert item.table, f"{item.field} table 不能空"
        assert item.scenario, f"{item.field} scenario 不能空"
        assert item.purpose, f"{item.field} purpose 不能空"


def test_legal_basis_in_pipl_seven_categories() -> None:
    """所有 legal_basis 必须落在 PIPL §13 七类合法性基础内."""
    valid = set(get_args(LegalBasis))
    assert len(valid) == 7, "PIPL §13 严格 7 类合法性基础"
    for item in PII_INVENTORY:
        assert item.legal_basis in valid, (
            f"{item.field} legal_basis={item.legal_basis!r} 不在 PIPL §13 七类内"
        )


def test_retention_days_non_negative_and_reasonable() -> None:
    for item in PII_INVENTORY:
        assert item.retention_days_after_logout >= 0, (
            f"{item.field} retention 不能为负"
        )
        assert item.retention_days_after_logout <= 90, (
            f"{item.field} retention={item.retention_days_after_logout} > 90d, "
            "PIPL 惯例最长 90d 归档, 超过应有专门的合规论证"
        )


# ─── 3. 必含字段 (基于 spec/12 §BE-S5-002 + ORM models) ─────────────


def test_inventory_covers_all_critical_user_pii() -> None:
    """spec/12 §BE-S5-002 强制必含的关键 PII 字段."""
    fields = {(it.table, it.field) for it in PII_INVENTORY}
    must_have: set[tuple[str, str]] = {
        ("users", "phone"),
        ("users", "wechat_openid"),
        ("users", "wechat_unionid"),
        ("users", "apple_id"),
        ("users", "nickname"),
        ("users", "avatar_url"),
        ("users", "region"),
        ("users", "last_active_at"),
        ("push_tokens", "device_id"),
        ("push_tokens", "token"),
        ("__log__", "ip_address"),
        ("__log__", "user_agent"),
    }
    missing = must_have - fields
    assert not missing, f"清单缺关键 PII: {missing}"


def test_phone_is_marked_sensitive() -> None:
    """PIPL §28: 手机号是敏感个人信息, 必须标 is_sensitive=True."""
    phone_items = [it for it in PII_INVENTORY if it.field == "phone" and it.table == "users"]
    assert len(phone_items) == 1
    assert phone_items[0].is_sensitive is True, "phone 必须标敏感 (PIPL §28)"


def test_phone_retention_30_days() -> None:
    """spec/12 §BE-S5-002 + BE-S5-003: 注销后 30d 真删 phone."""
    phone_items = [it for it in PII_INVENTORY if it.field == "phone" and it.table == "users"]
    assert phone_items[0].retention_days_after_logout == 30


# ─── 4. 数据出境 + 同意机制 + 第三方 SDK ───────────────────────────


def test_data_export_jurisdictions_empty_for_mvp() -> None:
    """MVP 数据全境内, 不出境 — 出境清单必须为空."""
    assert DATA_EXPORT_JURISDICTIONS == ()


def test_consent_mechanism_explicit_opt_in() -> None:
    """PIPL §14: 同意必须明示, 不能默认勾选."""
    assert CONSENT_MECHANISM["type"] == "explicit_opt_in"
    assert "withdrawal_path" in CONSENT_MECHANISM
    assert CONSENT_MECHANISM["withdrawal_path"], "必须有撤回同意的路径 (注销账号)"


def test_third_party_sdks_have_full_disclosure() -> None:
    """PIPL §23-25: 共同处理者必须披露 vendor / 用途 / 收集字段 / 隐私政策链接."""
    assert len(THIRD_PARTY_SDKS) >= 3, "至少披露 微信 / 支付 / Sentry"
    required_keys = {"name", "vendor", "purpose", "pii_collected", "url"}
    for sdk in THIRD_PARTY_SDKS:
        assert required_keys.issubset(sdk.keys()), (
            f"SDK {sdk.get('name')} 缺字段: {required_keys - sdk.keys()}"
        )
        assert sdk["url"].startswith("https://"), (
            f"SDK {sdk['name']} url 必须 HTTPS"
        )


# ─── 5. helper accessor 函数 ──────────────────────────────────────


def test_get_inventory_returns_same_tuple() -> None:
    assert pii_inventory.get_inventory() is PII_INVENTORY


def test_get_jurisdictions_returns_empty_tuple() -> None:
    assert pii_inventory.get_jurisdictions() == ()


def test_get_third_party_sdks_returns_const() -> None:
    assert pii_inventory.get_third_party_sdks() is THIRD_PARTY_SDKS


def test_get_consent_mechanism_returns_dict() -> None:
    cm = pii_inventory.get_consent_mechanism()
    assert isinstance(cm, dict)
    assert cm == CONSENT_MECHANISM


# ─── 6. PIIItem.to_dict() ──────────────────────────────────────────


def test_pii_item_to_dict_round_trips_fields() -> None:
    item = PII_INVENTORY[0]
    d = item.to_dict()
    assert d["field"] == item.field
    assert d["table"] == item.table
    assert d["legal_basis"] == item.legal_basis
    assert d["retention_days_after_logout"] == item.retention_days_after_logout
    assert d["is_sensitive"] == item.is_sensitive
    # 必须 JSON-safe (admin API 直接 dump)
    import json

    json.dumps(d, ensure_ascii=False)


# ─── 7. 字段唯一性 (同 table + field 不能重复) ─────────────────────


def test_no_duplicate_field_table_pairs() -> None:
    pairs = [(it.table, it.field) for it in PII_INVENTORY]
    assert len(pairs) == len(set(pairs)), "同 (table, field) 不能重复声明"
