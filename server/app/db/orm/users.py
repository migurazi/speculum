"""UserORM — ADR-0021 D2/D3 의 users 테이블 (멀티유저 전환 인프라).

M0~M1 은 `auth.py` 의 `SYSTEM_USER_ID` placeholder 로 동작했고 users 테이블이
부재했다 (migration 0002 의 deferred). M2 는 user 소유 데이터(watchlist /
screener_sets / screen_runs)를 격리하며, 그 격리의 anchor 인 users 테이블을
본 ORM 이 신설한다.

핵심 결정 (ADR-0021 D2/D3):
    - **최소 스키마** — `id` UUID PK + `created_at` 만. OAuth 메타(email / sub /
      provider)는 본 작업(T69 인프라) scope 밖 — NextAuth 합류(T68)에서 컬럼
      추가. 본 사이클은 user_id 격리의 FK anchor 만 확보.
    - **system sentinel row** — migration 이 `00000000-…-0001`(SYSTEM_USER_ID)
      row 를 insert 하여 M1 의 기존 SYSTEM-owned run/watchlist/screener_set 의
      신규 FK 를 충족(ADR-0021 D3). 어떤 로그인 세션도 이 row 를 받지 않음
      (sentinel 은 마이그레이션된 자산의 소유자로만 잔존).
    - **FK anchor** — `watchlists` / `screener_sets` / `screen_runs`.user_id 가
      본 테이블의 id 를 참조(migration 에서 FK 추가). user 무관 fact 테이블
      (prices/financials/macro/market_caps/treasury)에는 FK 없음 — fact 는 공용
      (ADR-0021 D5).

관련 ADR:
- ADR-0021 D2 (user 격리 메커니즘 — users 테이블 + FK), D3 (M1 마이그레이션 +
  sentinel row)
- ADR-0011 D7 (default "내 관심 종목" 폴더 — user 프로비저닝, T68)
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class UserORM(Base):
    """users row — user 소유 데이터 격리의 FK anchor (ADR-0021 D2/D3) + OAuth 메타.

    id + created_at 은 0012(격리 anchor), google_sub + email 은 0016(T68 NextAuth)
    에서 추가됐다.

    OAuth 메타 (T68, ADR-0021 D1.1):
        - google_sub: Google OAuth `sub` 클레임 — 안정적 사용자 식별자. JIT
          provision 의 키. nullable + unique (NULL 다중 허용 — sentinel row 는
          NULL 유지). 한 Google 계정 = 한 user.
        - email: 표시용 이메일 (JWT email 클레임). unique 아님 (식별자 아님).
    """

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    # T68 — Google OAuth `sub` (안정 식별자). nullable: sentinel row 는 NULL.
    # unique 인덱스(uq_users_google_sub)는 NULL 다중 허용.
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # T68 — 표시용 이메일 (JWT email 클레임). unique 아님.
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    __table_args__ = (
        # google_sub unique — migration 0016 의 uq_users_google_sub 와 동일 명.
        # NULL 다중 허용(PG/SQLite 공통) → sentinel(google_sub=NULL) 충돌 없음.
        Index("uq_users_google_sub", "google_sub", unique=True),
    )
