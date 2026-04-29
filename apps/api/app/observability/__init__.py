"""OPS-S5-001 可观测性子包: Sentry SDK 集成 + 未来扩展点 (OTel / Prometheus)."""

from app.observability.sentry import init_sentry

__all__ = ["init_sentry"]
