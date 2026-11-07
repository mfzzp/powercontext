"""Engineering documents live in seekdb. Parameterized SQL results form the context."""
import re

import pymysql

from .config import db_settings, digest, safe_source


class DocumentStore:
    def __init__(self):
        settings = db_settings()
        database = settings.pop("database")
        self.connection = pymysql.connect(**settings, cursorclass=pymysql.cursors.DictCursor)
        with self.connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
            cursor.execute(f"USE `{database}`")
            cursor.execute("""CREATE TABLE IF NOT EXISTS sdd_documents (
                project_id VARCHAR(100) NOT NULL, path VARCHAR(400) NOT NULL,
                kind VARCHAR(16) NOT NULL, content LONGTEXT NOT NULL,
                sha256 CHAR(64) NOT NULL, revision BIGINT NOT NULL,
                PRIMARY KEY (project_id, path))""")
        self.connection.commit()

    def close(self):
        self.connection.close()

    def ingest(self, project, root, manifest):
        # Validate all files before touching database rows. Manifest is a complete snapshot.
        documents = []
        seen = set()
        for item in manifest:
            path, kind = item["path"], item["kind"]
            if path in seen or kind not in {"spec", "code", "test"}:
                raise ValueError("duplicate path or unsupported source kind")
            seen.add(path)
            content = safe_source(root, path).read_text(encoding="utf-8")
            if len(content.encode()) > 100_000:
                raise ValueError("sample source exceeds 100 KB limit")
            documents.append(dict(path=path, kind=kind, content=content, sha256=digest(content)))
        audit = []
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT path, sha256, kind, revision FROM sdd_documents WHERE project_id=%s FOR UPDATE", (project,))
                previous = {row["path"]: row for row in cursor.fetchall()}
                for doc in documents:
                    old = previous.pop(doc["path"], None)
                    changed = old is None or (old["sha256"], old["kind"]) != (doc["sha256"], doc["kind"])
                    revision = (old["revision"] if old else 0) + int(changed)
                    cursor.execute("REPLACE INTO sdd_documents (project_id,path,kind,content,sha256,revision) VALUES (%s,%s,%s,%s,%s,%s)",
                                   (project, doc["path"], doc["kind"], doc["content"], doc["sha256"], revision))
                    audit.append({k: v for k, v in doc.items() if k != "content"} | {"revision": revision, "changed": changed})
                for path in previous:
                    cursor.execute("DELETE FROM sdd_documents WHERE project_id=%s AND path=%s", (project, path))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return audit

    def search(self, project, query, limit=12):
        # Small project: portable lexical retrieval. All task terms participate; no fixed paths/results.
        terms = list(dict.fromkeys(re.findall(r"[\w]+", query.lower(), flags=re.UNICODE)))[:16]
        if not terms or not 1 <= limit <= 50:
            raise ValueError("query needs words and limit must be 1..50")
        score = " + ".join("CASE WHEN LOWER(content) LIKE %s THEN 1 ELSE 0 END" for _ in terms)
        sql = (f"SELECT path,kind,content,sha256,revision,({score}) AS relevance FROM sdd_documents "
               "WHERE project_id=%s HAVING relevance>0 ORDER BY relevance DESC,path LIMIT %s")
        # Escape LIKE metacharacters even though parameters already protect SQL syntax.
        params = ["%" + term.replace("_", "\\_") + "%" for term in terms] + [project, limit]
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = list(cursor.fetchall())
        for row in rows:
            row["lines"] = [1, len(row["content"].splitlines())]
        return rows, {"sql": sql, "parameters": params, "results": [{k: v for k, v in r.items() if k != "content"} for r in rows]}
