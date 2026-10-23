import json
import sqlite3


class SqliteCache:
    """DOI/arXiv ID로 조회한 INSPIRE-HEP·CrossRef 응답을 다음 실행에서도
    재사용하기 위한 로컬 캐시.

    **성공한 조회만 캐시한다** — "아직 못 찾음"(None)은 캐시하지 않는다.
    아직 출판 전인 논문은 다음 실행 사이에 새로 출판될 수 있고, 파이프라인은
    매번 재조회해서 이런 '숨은 출판'을 잡아내야 하므로, 못 찾은 결과를
    캐시해버리면 그 탐지 로직이 무력화된다.

    ttl_days가 지정되면 그보다 오래된 항목은 없는 것으로 취급해 다시 조회한다
    (출판 직후 출판사가 페이지 번호·funder 정보 등을 고치는 경우를 반영하기 위함).
    단 PERMANENT_KINDS(출판 확정 후 유발논문 근거까지 판정을 마친 결과)는
    재조사가 필요 없으므로 TTL을 적용하지 않는다.
    """

    PERMANENT_KINDS = {"evidence"}

    def __init__(self, db_path, ttl_days=None):
        self._ttl_days = ttl_days
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

    @classmethod
    def from_settings(cls, settings):
        cfg = settings.get("cache", {})
        return cls(cfg.get("path", "data/cache.sqlite"), ttl_days=cfg.get("ttl_days"))

    def get(self, kind, key):
        sql = "SELECT value FROM api_cache WHERE kind = ? AND key = ?"
        params = [kind, key]
        if self._ttl_days is not None and kind not in self.PERMANENT_KINDS:
            sql += " AND fetched_at >= datetime('now', ?)"
            params.append(f"-{int(self._ttl_days)} days")
        row = self._conn.execute(sql, params).fetchone()
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
