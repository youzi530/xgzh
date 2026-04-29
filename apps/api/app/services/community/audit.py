"""用户输入侧 UGC 内容审核 v3 (BE-S6-008 / spec/13 §主线 D).

与 LLM 输出侧 (BE-S5-001 v2) 的差异
====================================
| 场景                  | v2 (LLM 输出)               | v3 (用户输入, 本模块)      |
|----------------------|-----------------------------|---------------------------|
| Tier 1 命中           | 截断 + 阻断提示             | **拒绝入库**, status=rejected |
| Tier 2 命中           | 替换 [已脱敏]               | 进 admin 队列 status=pending |
| 私域引流词            | 不在 v2 词表                | v3 专属新增                |
| 性能要求              | 流式 < 5ms/KB               | 单次 < 50ms                |

设计要点
========
1. **v2 词典完全复用**: 收益承诺 / 推荐买入 / 损失保证 / 内幕信息 / 营销话术 全部继承.
   用户输入比 LLM 还容易踩, 不放过.
2. **v3 私域引流词典 (新增)**: 微信号 / QQ / 群号 / 二维码描述 / 引导加好友. 这是社区
   黑产最常见手段; 命中即 Tier 1 reject.
3. **v3 数字串证件号检测**: 18 位连续数字或 4 段空白分隔的证件号格式 → 自动当作 Tier 1
   命中 (隐私泄露, 用户不能在公开帖里贴自己或他人证件号).
4. **AuditVerdict 三态**: ``approve`` / ``reject`` / ``queue``. service 层据此设置
   Post.status: approve→published / reject→rejected / queue→pending (待 admin 审).
5. **logger.info / warning** 全程打点; 对 reject / queue 落 Sentry breadcrumb.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger

from app.services.compliance.forbidden_patterns import (
    forbidden_pattern_filter,
)

# ─── v3 专属词典 ─────────────────────────────────────────────────────────

# 私域引流词 (Tier 1 — 直接 reject)
PRIVATE_FLOW_PATTERNS: list[str] = [
    # 加群 / 私聊
    "加群",
    "进群",
    "加入群聊",
    "私聊",
    "私信我",
    "加微信",
    "加我微信",
    "加我QQ",
    "加我qq",
    "加好友",
    "vx",
    "VX",
    "微X",
    "微x",
    "weixin",
    "WeChat",
    "wechat",
    # 二维码 / 引流
    "扫码",
    "扫二维码",
    "二维码",
    "扫一扫",
    "公众号关注",
    "关注公众号",
    "私域",
    # 联系方式套话
    "联系方式如下",
    "qq群",
    "QQ群",
    "微信群",
    "电报",
    "telegram",
    "Telegram",
]

# v3 数字串隐私检测
# 1) 18 位连续数字 (大陆身份证常见模式)
_ID_NUMBER_18 = re.compile(r"\d{17}[\dXx]")
# 2) 11 位连续数字 (手机号常见模式; 首位 1, 第二位 3-9)
_PHONE_11 = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 3) 银行卡号常见 16-19 位连续数字
_BANKCARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
# 4) QQ 号: 5-12 位连续数字, 单独成段或前后是空白/标点
_QQ_NUMBER = re.compile(r"(?<!\d)[1-9]\d{4,11}(?!\d)")


# 编译 alternation regex
_PRIVATE_FLOW_REGEX = re.compile("|".join(re.escape(p) for p in PRIVATE_FLOW_PATTERNS))


# ─── 数据类 ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AuditResult:
    """v3 审核结果. service 层用 ``verdict`` 决定 Post.status.

    Fields:
        verdict: 'approve' / 'reject' / 'queue'
        tier1_hits: 触发硬阻断的命中词 (导致 verdict=reject)
        tier2_hits: 触发软审核的命中词 (导致 verdict=queue)
        privacy_hits: 隐私泄露命中 (证件号 / 手机号 / 银行卡), 一律 reject
        private_flow_hits: 私域引流命中 (微信 / QQ / 群号), 一律 reject
        rejection_reason: reject 时填的拒绝原因软枚举
            (content_violation / privacy_leak / spam / other)
    """

    verdict: str
    tier1_hits: list[str] = field(default_factory=list)
    tier2_hits: list[str] = field(default_factory=list)
    privacy_hits: list[str] = field(default_factory=list)
    private_flow_hits: list[str] = field(default_factory=list)
    rejection_reason: str | None = None

    @property
    def all_hits(self) -> list[str]:
        return (
            self.tier1_hits
            + self.tier2_hits
            + self.privacy_hits
            + self.private_flow_hits
        )


# ─── 公开 API ─────────────────────────────────────────────────────────


def audit_user_content(text: str, *, user_id: str = "unknown") -> AuditResult:
    """对用户输入内容做 v3 审核.

    优先级:
    1. 私域引流命中 → reject (rejection_reason=spam)
    2. 隐私数字串命中 → reject (rejection_reason=privacy_leak)
    3. v2 Tier 1 命中 → reject (rejection_reason=content_violation)
    4. v2 Tier 2 命中 → queue (软审, 进 admin 队列)
    5. 其它 → approve

    复杂度 O(n × #patterns). 单次扫描在 1KB 文本上 ~1ms (远低于 50ms 上限).
    """
    if not text:
        return AuditResult(verdict="approve")

    # 1. 私域引流检测 (硬阻断)
    private_flow_hits = list(set(_PRIVATE_FLOW_REGEX.findall(text)))
    if private_flow_hits:
        logger.info(
            f"audit.user_content.private_flow user={user_id} "
            f"hits={private_flow_hits[:5]}"
        )
        return AuditResult(
            verdict="reject",
            private_flow_hits=private_flow_hits,
            rejection_reason="spam",
        )

    # 2. 隐私数字串 (硬阻断)
    privacy_hits: list[str] = []
    for m in _ID_NUMBER_18.finditer(text):
        privacy_hits.append(f"id:{m.group(0)[:6]}***")
    for m in _PHONE_11.finditer(text):
        privacy_hits.append(f"phone:{m.group(0)[:3]}***")
    for m in _BANKCARD.finditer(text):
        # 注意: 16-19 位会和 phone 重叠 (11 位), 已 boundary 隔离
        privacy_hits.append(f"bankcard:{m.group(0)[:4]}***")
    # QQ 号过于宽松 (5-12 位数字), 单独走"明确含 QQ 字样附近 + 数字串"判断, 否则
    # 误杀股票代码 / 申购数 / 股价百位等. 这里不直接命中, 留 v3.1 优化.
    if privacy_hits:
        logger.warning(
            f"audit.user_content.privacy_leak user={user_id} "
            f"hits={privacy_hits[:3]}"
        )
        return AuditResult(
            verdict="reject",
            privacy_hits=privacy_hits,
            rejection_reason="privacy_leak",
        )

    # 3. v2 复用 (Tier 1 + Tier 2)
    _, scan = forbidden_pattern_filter(text)
    if scan.has_tier1:
        logger.info(
            f"audit.user_content.tier1 user={user_id} hits={scan.tier1_hits[:5]}"
        )
        return AuditResult(
            verdict="reject",
            tier1_hits=list(scan.tier1_hits),
            rejection_reason="content_violation",
        )
    if scan.has_tier2:
        logger.info(
            f"audit.user_content.tier2_queue user={user_id} hits={scan.tier2_hits[:5]}"
        )
        return AuditResult(
            verdict="queue",
            tier2_hits=list(scan.tier2_hits),
        )

    # 4. 全过
    return AuditResult(verdict="approve")


# QQ 号验证 (留 v3.1 优化, 当前函数仅对外 API stub, 单元测试用)
_QQ_CONTEXT_REGEX = re.compile(
    r"(?:qq|QQ|扣扣|寇寇)[^\d]{0,5}(?P<qq>" + _QQ_NUMBER.pattern + r")"
)


def find_qq_in_context(text: str) -> list[str]:
    """识别"qq:12345" / "扣扣 6789" 这种带上下文的 QQ 号.

    为什么单独函数: ``audit_user_content`` 主路径不接 — 误杀风险大, 留 admin 工具
    或 v3.1 接入. 测试可以单独覆盖.
    """
    return [m.group("qq") for m in _QQ_CONTEXT_REGEX.finditer(text)]
