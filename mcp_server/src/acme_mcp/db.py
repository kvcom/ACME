import os
import psycopg


def get_conn() -> psycopg.Connection:
    url = os.getenv('SYNC_DATABASE_URL', 'postgresql://acme:acme@postgres:5432/acme')
    return psycopg.connect(url)
