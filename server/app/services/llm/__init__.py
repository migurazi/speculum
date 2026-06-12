"""운영 LLM adapter 패키지 — ADR-0031 D6.

`disclosure_fact_extraction.py`(게이트 파이프라인 + Protocol + fake)는 **실제 LLM
SDK 를 import 하지 않는다**(D6). 운영 구현체(Anthropic SDK 등)는 본 패키지로 분리해
의존성 경계를 명확히 한다 — gate/스키마/디스클레이머는 SDK 무관하게 동작하고,
SDK 연동은 선택 의존(`pip install -e .[llm]`)으로만 활성된다.
"""
