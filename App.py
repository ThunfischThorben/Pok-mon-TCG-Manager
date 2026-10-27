import io
import json
import sqlite3
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st


# ==========================================
# EINSTELLUNGEN
# ==========================================

st.set_page_config(
    page_title="Pokémon TCG Manager",
    page_icon="🃏",
    layout="wide"
)

DB_PATH = Path("pokemon_manager.db")


# ==========================================
# HILFSFUNKTIONEN
# ==========================================

def euro(value):
    return (
        f"{value:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
        + " €"
    )


def to_cents(value):
    amount = Decimal(str(value))
    return int(
        (amount * 100).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )
    )


def money(cents):
    return euro(Decimal(cents) / 100)


def percentage(cents, percent):
    return int(
        (
            Decimal(cents)
            * Decimal(percent)
            / 100
        ).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )
    )


def database():
    db = sqlite3.connect(
        DB_PATH,
        timeout=30
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


# ==========================================
# DATENBANK
# ==========================================

def initialize_database():

    with database() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'offen',
                purchase_cents INTEGER,
                purchased_at TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id INTEGER NOT NULL,
                card_id TEXT,
                name TEXT NOT NULL,
                set_name TEXT,
                number TEXT,
                language TEXT,
                quantity INTEGER NOT NULL,
                price_cents INTEGER NOT NULL,
                image TEXT,
                FOREIGN KEY(collection_id)
                    REFERENCES collections(id)
                    ON DELETE CASCADE
            )
        """)


def save_collection(seller, cards):

    with database() as db:

        cursor = db.execute("""
            INSERT INTO collections (
                seller,
                created_at,
                status
            )
            VALUES (?, ?, 'offen')
        """, (
            seller,
            datetime.now().isoformat()
        ))

        collection_id = cursor.lastrowid

        for card in cards:

            db.execute("""
                INSERT INTO cards (
                    collection_id,
                    card_id,
                    name,
                    set_name,
                    number,
                    language,
                    quantity,
                    price_cents,
                    image
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                collection_id,
                card["card_id"],
                card["name"],
                card["set_name"],
                card["number"],
                card["language"],
                card["quantity"],
                card["price_cents"],
                card["image"]
            ))

    return collection_id


def get_collections(status=None):

    query = """
        SELECT
            c.*,
            COALESCE(
                SUM(k.quantity * k.price_cents),
                0
            ) AS market_cents,
            COALESCE(
                SUM(k.quantity),
                0
            ) AS card_count
        FROM collections c
        LEFT JOIN cards k
            ON k.collection_id = c.id
    """

    params = []

    if status:
        query += " WHERE c.status = ?"
        params.append(status)

    query += """
        GROUP BY c.id
        ORDER BY c.id DESC
    """

    with database() as db:
        return db.execute(
            query,
            params
        ).fetchall()


def get_cards(collection_id):

    with database() as db:
        return db.execute("""
            SELECT *
            FROM cards
            WHERE collection_id = ?
            ORDER BY id
        """, (collection_id,)).fetchall()


def buy_collection(collection_id, purchase_cents):

    with database() as db:

        db.execute("""
            UPDATE collections
            SET
                status = 'gekauft',
                purchase_cents = ?,
                purchased_at = ?
            WHERE id = ?
              AND status = 'offen'
        """, (
            purchase_cents,
            datetime.now().isoformat(),
            collection_id
        ))


def undo_purchase(collection_id):

    with database() as db:

        db.execute("""
            UPDATE collections
            SET
                status = 'offen',
                purchase_cents = NULL,
                purchased_at = NULL
            WHERE id = ?
              AND status = 'gekauft'
        """, (collection_id,))


def delete_collection(collection_id):

    with database() as db:

        db.execute("""
            DELETE FROM collections
            WHERE id = ?
              AND status = 'offen'
        """, (collection_id,))


# ==========================================
# JSON-IMPORT
# ==========================================

def read_cardvalue_json(uploaded_file):

    try:
        data = json.load(uploaded_file)

    except (ValueError, UnicodeDecodeError):
        raise ValueError(
            "Die Datei enthält kein gültiges JSON."
        )

    if not isinstance(data, dict):
        raise ValueError(
            "Ungültiges JSON-Format."
        )

    items = data.get("items")

    if not isinstance(items, list):
        raise ValueError(
            "Keine gültige Kartenliste gefunden."
        )

    cards = []

    for index, item in enumerate(items, start=1):

        if not isinstance(item, dict):
            raise ValueError(
                f"Ungültiger Karteneintrag: {index}"
            )

        try:
           
