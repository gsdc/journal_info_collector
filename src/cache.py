import json
import sqlite3


class SqliteCache:
    """DOI/arXiv ID로 조회한 INSPIRE-HEP·CrossRef 응답을 다음 실행에서도
    재사용하기 위한 로컬 캐시.

    **성공한 조회만 캐시한다** — "아직 못 찾음"(None)은 캐시하지 않는다.
    아직 출판 전인 논문은 다음 실행 사이에 새로 출판될 수 있고, 파이프라인은
    매번 재조회해서 이런 '숨은 출판'을 잡아내야 하므로, 못 찾은 결과를
    캐시해버리면 그 탐지 로직이 무력화된다.
    """

    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_cache (
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (kind, key)
            )
            """
        )
        self._conn.commit()

    def get(self, kind, key):
        row = self._conn.execute(
            "SELECT value FROM api_cache WHERE kind = ? AND key = ?", (kind, key)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set(self, kind, key, value_dict):
        self._conn.execute(
            "INSERT OR REPLACE INTO api_cache (kind, key, value, fetched_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (kind, key, json.dumps(value_dict, ensure_ascii=False)),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()
