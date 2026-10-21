"""Transactional SQLite migration for supplier-scoped identities."""
import re
import sqlite3


def scope_provider(db: sqlite3.Connection, table: str) -> None:
    if table not in ('mappings', 'publications'):
        raise ValueError('Invalid table')
    info = list(db.execute(f'PRAGMA table_info({table})'))
    if any(row[1] == 'provider' and row[5] for row in info):
        return
    if not any(row[1] == 'provider' for row in info):
        db.execute(f"ALTER TABLE {table} ADD COLUMN provider TEXT NOT NULL DEFAULT 'hstore'")
    sql = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
    sql = re.sub(r'PRIMARY KEY\s*\(account,\s*node,\s*product\)',
                 'PRIMARY KEY(account,node,product,provider)', sql, flags=re.I)
    sql = sql.replace(f'CREATE TABLE {table}', f'CREATE TABLE {table}_scoped', 1)
    db.execute(sql)
    db.execute(f'INSERT INTO {table}_scoped SELECT * FROM {table}')
    db.execute(f'DROP TABLE {table}')
    db.execute(f'ALTER TABLE {table}_scoped RENAME TO {table}')
