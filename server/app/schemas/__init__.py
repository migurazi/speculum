"""Wire-level Pydantic schemas — FastAPI request/response.

`app/models/` (도메인 entity) 와 분리. `from_domain(domain_obj) -> SchemaOut`
factory 패턴 — 단방향 (domain → wire), 역방향 금지로 의존 그래프 단방향
(models/ ← schemas/ ← routes/).

설계 (oracle T25 자문 결정 1 — Pydantic v2 strict):
- `model_config = ConfigDict(strict=True, extra="forbid", frozen=True)` 표준
- Decimal → str 직렬화 (IEEE 754 drift 회피, oracle Risk-C4)
"""
