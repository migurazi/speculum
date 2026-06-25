"""FastAPI dependencies — Query/Header parsing + service 호출 + Response 주입.

본 패키지의 dependency 들은 service layer (`app/services/`) 의 framework-agnostic
함수들을 wrap. T13 합류 후 SQLAlchemy session, T31 합류 후 JWT decoder 도 본
패키지에 추가.
"""

from app.api.dependencies.as_of import (
    BrowseAsOfDep,
    NormalizedAsOfDep,
    get_browse_as_of,
    get_normalized_as_of,
)
from app.api.dependencies.auth import CurrentUserDep, UserContext, get_current_user

__all__ = [
    "BrowseAsOfDep",
    "CurrentUserDep",
    "NormalizedAsOfDep",
    "UserContext",
    "get_browse_as_of",
    "get_current_user",
    "get_normalized_as_of",
]
