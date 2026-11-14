"""Product model: dataclass + sqlite3 CRUD helpers."""

import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Product:
    id: Optional[int]
    name: str
    source_url: str
    created_at: str  # UTC ISO-8601 string


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def insert_product(conn: sqlite3.Connection, name: str, source_url: str) -> Product:
    """Insert a new product and return the created Product."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO products (name, source_url, created_at) VALUES (?, ?, ?)",
        (name, source_url, now),
    )
    return Product(id=cur.lastrowid, name=name, source_url=source_url, created_at=now)


def get_product(conn: sqlite3.Connection, product_id: int) -> Optional[Product]:
    """Return a Product by primary key, or None."""
    row = conn.execute(
        "SELECT id, name, source_url, created_at FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()
    if row is None:
        return None
    return Product(**dict(row))


def list_products(conn: sqlite3.Connection) -> list[Product]:
    """Return all products ordered by name."""
    rows = conn.execute(
        "SELECT id, name, source_url, created_at FROM products ORDER BY name"
    ).fetchall()
    return [Product(**dict(r)) for r in rows]


def get_or_create_product(
    conn: sqlite3.Connection, name: str, source_url: str
) -> Product:
    """Return existing product by name, or insert and return a new one."""
    row = conn.execute(
        "SELECT id, name, source_url, created_at FROM products WHERE name = ?",
        (name,),
    ).fetchone()
    if row:
        return Product(**dict(row))
    product = insert_product(conn, name, source_url)
    conn.commit()
    logger.info("Created product %r (id=%s)", name, product.id)
    return product
