"""전체 Alembic 체인이 SQLite 에서 실행되는지 회귀 가드.

migration 0004 (prices_daily.trading_value) 가 `ALTER COLUMN ... DROP DEFAULT`
(SQLite 미지원 — SQLite 는 ALTER COLUMN 자체가 없음) 를 직접 발행해
`alembic upgrade head` 가 SQLite 에서 깨졌던 버그의 회귀 가드.

batch_alter_table 적용 (20260528_0004_prices_trading_value.py) 후 0001~head 전체
체인이 SQLite 에서 upgrade/downgrade 가능해야 한다. README 의 `sqlite:///` 운영
프로비저닝 경로 (정식 데이터 적재) 가 alembic 으로 동작함을 보증한다.

다른 migration 테스트 (test_market_caps_migration 등) 는 개별 migration 을 격리
구동하지만, 본 테스트는 **전체 체인** 을 실제 alembic command 로 돌려 chain-level
SQLite 호환을 검증한다 (이 버그는 단일 migration 격리로는 잡히지 않았다).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

# tests/test_db/test_alembic_chain_sqlite.py → parents[2] = server 디렉터리.
_SERVER_DIR = Path(__file__).resolve().parents[2]


def _alembic_config(db_url: str) -> Config:
    """server/alembic.ini 기반 Config — script_location·url 명시 주입.

    cwd 독립을 위해 alembic.ini / script_location 을 절대경로로 고정. url 은
    cfg 와 env var 양쪽에 동일 주입 (env.py 가 SPECULUM_DATABASE_URL 1순위라
    호출자가 env var 도 set 해야 실제 적용됨).
    """
    cfg = Config(str(_SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_SERVER_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_full_chain_upgrades_and_downgrades_on_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0001~head 전체 upgrade → trading_value 스키마 확인 → base 로 downgrade.

    0004 의 batch_alter_table 가 없으면 upgrade 가 OperationalError (near "ALTER")
    로 실패한다 (회귀 가드). downgrade base 는 batch drop_column 까지 검증.
    """
    db_path = tmp_path / "chain.db"
    db_url = f"sqlite:///{db_path}"
    # env.py 는 SPECULUM_DATABASE_URL 을 1순위로 읽음 → 격리 위해 tmp 로 고정.
    monkeypatch.setenv("SPECULUM_DATABASE_URL", db_url)
    cfg = _alembic_config(db_url)

    # 전체 체인 upgrade — 0004 batch 수정 전에는 여기서 깨졌다.
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        assert "prices_daily" in insp.get_table_names()
        cols = {c["name"]: c for c in insp.get_columns("prices_daily")}
        assert "trading_value" in cols
        tv = cols["trading_value"]
        # 0004 의도: NOT NULL + DB-level default 제거 (ORM 이 명시값 강제).
        assert tv["nullable"] is False
        assert tv["default"] is None
    finally:
        engine.dispose()

    # downgrade base — batch drop_column (구버전 SQLite 안전) 까지 SQLite 동작.
    command.downgrade(cfg, "base")

    engine = create_engine(db_url)
    try:
        # base 로 내려오면 핵심 테이블이 사라져야 함 (체인 역방향 정상).
        assert "prices_daily" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
