"""T76 system-write-0 게이트 — Note 생성 경로가 route + repository 외에 없음.

**시스템 생성 Notes 0 원칙(T76)**: Note 레코드를 만드는 경로는 인증 route +
repository 뿐이다. seeder/migration insert/builtin 어떤 경로도 Note row 를
만들지 않는다.

본 게이트는 `app/` 소스를 AST 로 스캔해 Note 구성 식별자가 화이트리스트 모듈
밖에서 등장하지 않음을 정적으로 단언한다(test_factor_pack 의 T73 부재 게이트와
동형 정신 — 금지 구성을 컴파일이 아닌 테스트 시점에 강제).

검출 대상 (Note row 를 만드는 모든 구성 경로):
    - `Note(...)` — frozen dataclass 직접 구성.
    - `StockNoteORM(...)` — ORM row 직접 구성.
    - `note_record_to_orm(...)` — dataclass → ORM 직렬화(INSERT 직전).

화이트리스트 (Note 생성이 정당한 모듈):
    - app/repositories/notes_repository.py — Note dataclass + Fake.create.
    - app/repositories/sql_notes_repository.py — SqlNotesRepository.create.
    - app/db/converters.py — note_record_to_orm/note_orm_to_record 정의.
    - app/db/orm/notes.py — StockNoteORM 클래스 정의(구성 아님이나 정의 모듈).
    - app/schemas/notes.py — NoteOut.from_domain(읽기 전용, 구성 아님).

route(app/api/routes/notes.py)는 repository 를 호출할 뿐 Note 를 직접 구성하지
않으므로 화이트리스트에 없어도 무방(스캔에 안 걸림). seeder/migration/builtin
경로에서 위 식별자가 등장하면 본 게이트 실패.
"""

from __future__ import annotations

import ast
from pathlib import Path

# app/ 소스 루트.
_APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# Note row 를 만드는 구성 식별자 (호출/생성자명).
_NOTE_WRITE_NAMES = frozenset(
    {"Note", "StockNoteORM", "note_record_to_orm"}
)

# Note 생성이 정당한 화이트리스트 모듈 (app/ 기준 상대 경로, POSIX).
_WHITELIST = frozenset(
    {
        "repositories/notes_repository.py",
        "repositories/sql_notes_repository.py",
        "db/converters.py",
        "db/orm/notes.py",
        "schemas/notes.py",
    }
)


def _called_or_constructed_names(tree: ast.AST) -> set[str]:
    """소스 AST 에서 호출되는 함수/생성자명 집합 (Name + Attribute).

    `Note(...)` / `StockNoteORM(...)` / `note_record_to_orm(...)` 같은 호출의
    callee 이름을 수집. `x.Note(...)` 같은 attribute 호출의 attr 명도 포함.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def test_no_system_write_of_notes() -> None:
    """app/ 소스에서 Note 구성이 화이트리스트 모듈 외에 등장하지 않음."""
    violations: list[tuple[str, str]] = []

    for py_path in _APP_ROOT.rglob("*.py"):
        rel = py_path.relative_to(_APP_ROOT).as_posix()
        if rel in _WHITELIST:
            continue

        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = _called_or_constructed_names(tree)
        leaked = called & _NOTE_WRITE_NAMES
        for name in sorted(leaked):
            violations.append((rel, name))

    assert not violations, (
        "Note 생성 경로가 화이트리스트(route+repository+converter) 밖에서 발견 — "
        "시스템 생성 Notes 0 원칙(T76) 위반: "
        f"{violations}"
    )


def test_no_note_insert_in_alembic_migrations() -> None:
    """alembic migration 어디서도 stock_notes 에 insert 하지 않음.

    migration 은 schema(table+index) 만 생성하고 Note row 를 절대 insert 하지
    않는다(T76). bulk_insert/insert/execute 의 인자에 'stock_notes' 가 등장하면
    의심 — 본 게이트는 migration 소스에 'stock_notes' 문자열이 INSERT 문맥에
    없음을 보수적으로 단언(table 정의 외 INSERT 키워드 동반 차단).
    """
    versions_dir = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions"
    )
    offenders: list[str] = []
    for py_path in versions_dir.glob("*.py"):
        source = py_path.read_text(encoding="utf-8")
        if "stock_notes" not in source:
            continue
        # stock_notes 를 언급하는 migration 은 0014 뿐이어야 하고, 거기서도
        # insert/bulk_insert 가 stock_notes 대상이면 안 됨.
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else ""
            )
            if callee not in {"insert", "bulk_insert", "execute"}:
                continue
            # 이 호출의 소스 세그먼트에 stock_notes 가 들어가면 의심.
            seg = ast.get_source_segment(source, node) or ""
            if "stock_notes" in seg:
                offenders.append(f"{py_path.name}:{callee}")

    assert not offenders, (
        "migration 이 stock_notes 에 insert — 시스템 생성 Notes 0 위반(T76): "
        f"{offenders}"
    )
