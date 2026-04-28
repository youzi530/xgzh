"""BE-S5-001 合规护栏: 红线词词典 + LLM 输出过滤.

公开 API:
    forbidden_pattern_filter(text)           - 扫描 + 过滤, 返回 (清理后文本, ScanResult)
    is_tier1_clean(text)                     - 仅查 Tier 1, 不修改文本
    ScanResult                               - 命中详情 (tier1_hits / tier2_hits / negation_skipped)
    ForbiddenPatternError                    - Tier 1 命中时调用方可 raise
    TIER1_PATTERNS / TIER2_PATTERNS          - 词典常量, 便于测试 / admin UI 展示

替代关系:
    与 Sprint 1 的 ``adapters.llm_client.forbidden_pattern_filter`` 重叠;
    后者是粗 6 条 regex + 不真过滤; 本模块是 Tier 1/2 分级 + 否定豁免 + 真替换.
    Sprint 1 旧 API 仍 export 但内部 delegate 到这里, 调用方不需要改.

依赖: 无外部包 (re alternation 编译一次即可, 性能 < 1ms / 千字).
"""

from __future__ import annotations

from app.services.compliance.forbidden_patterns import (
    TIER1_PATTERNS,
    TIER2_PATTERNS,
    ForbiddenPatternError,
    ScanResult,
    find_first_tier1_hit,
    forbidden_pattern_filter,
    is_tier1_clean,
    scan,
)

__all__ = [
    "TIER1_PATTERNS",
    "TIER2_PATTERNS",
    "ForbiddenPatternError",
    "ScanResult",
    "find_first_tier1_hit",
    "forbidden_pattern_filter",
    "is_tier1_clean",
    "scan",
]
