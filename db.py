import os
from contextlib import contextmanager
from psycopg2 import pool

_pool = None

def init_pool():
    global _pool
    _pool = pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=os.getenv("DATABASE_URL"),
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )

def get_conn():
    return _pool.getconn()

def put_conn(conn):
    _pool.putconn(conn)

@contextmanager
def get_conn_ctx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

def execute(sql, params=(), *, fetch=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            result = None
            if fetch == "one":
                row = cur.fetchone()
                if row is not None:
                    cols = [d[0] for d in cur.description]
                    result = dict(zip(cols, row))
            elif fetch == "all":
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
                result = [dict(zip(cols, r)) for r in rows]
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
