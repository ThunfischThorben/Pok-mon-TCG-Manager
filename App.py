import io
import json
import sqlite3

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st


# ==========================================
# APP KONFIGURATION
# ==========================================

st.set_page_config(
    page_title="Pokémon TCG Manager",
    page_icon="🃏",
    layout="wide"
)

DB_PATH = Path("pokemon_manager.sqlite3")


# ==========================================
# HILFSFUNKTIONEN
# ==========================================

def to_cents(value):
    try:
        amount = Decimal(
            str(value if value is not None else 0)
        )

        if not amount.is_finite() or amount < 0:
            raise ValueError("Ungültiger Preis")

        return int(
            (amount * 100).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP
            )
        )

    except (ValueError, TypeError, ArithmeticError):
        raise ValueError("Ungültiger Euro-Preis")


def euro(cents):
    value = cents / 100

    return (
        f"{value:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
        + " €"
    )


def percent_value(value, percent):
    return int(
        (
            Decimal(value)
            * Decimal(percent)
            / 100
        ).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )
    )


def now():
    return datetime.now(timezone.utc).isoformat()


# ==========================================
# CARDMARKET LINK
# ==========================================

def cardmarket_link(card):

    name = str(
        card["name"] or ""
    ).strip()

    number = str(
        card["number"] or ""
    ).strip()

    set_name = str(
        card["set_name"] or ""
    ).strip()

    search_text = " ".join(
        part
        for part in [
            name,
            number,
            set_name
        ]
        if part
    )

    # Gezielte Google-Suche nach Cardmarket-Karten.
    # Vermeidet den bisher fehlerhaften
    # direkten Cardmarket-Suchlink.

    query = (
        "site:cardmarket.com/de/Pokemon/ "
        + search_text
    )

    return (
        "https://www.google.com/search?q="
        + quote(query)
    )


# ==========================================
# DATENBANK
# ==========================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    return conn


def init_db():

    with get_db() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'offen',
                quote_percent INTEGER NOT NULL DEFAULT 80,
                purchase_cents INTEGER,
                purchased_at TEXT
            )
        """)

        conn.execute("""
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
                card_path TEXT,

                FOREIGN KEY(collection_id)
                    REFERENCES collections(id)
                    ON DELETE CASCADE
            )
        """)


# ==========================================
# JSON IMPORT
# ==========================================

def parse_json(uploaded_file):

    try:
        data = json.load(uploaded_file)

    except (ValueError, UnicodeDecodeError):
        raise ValueError(
            "Die Datei ist kein gültiges JSON."
        )

    if not isinstance(data, dict):
        raise ValueError(
            "Ungültiges JSON-Format."
        )

    items = data.get("items")

    if not isinstance(items, list) or not items:
        raise ValueError(
            "Die JSON-Datei enthält keine Karten."
        )

    cards = []

    for index, item in enumerate(
        items,
        start=1
    ):

        if not isinstance(item, dict):
            raise ValueError(
                f"Ungültiger Karteneintrag: {index}"
            )

        try:
            quantity = int(
                item.get("quantity", 1)
            )

            if quantity < 1:
                raise ValueError()

        except (TypeError, ValueError):
            raise ValueError(
                f"Ungültige Stückzahl bei Karte {index}"
            )

        cards.append({

            "card_id": str(
                item.get("id") or ""
            ),

            "name": str(
                item.get("name") or "Unbekannt"
            ),

            "set_name": str(
                item.get("setName") or ""
            ),

            "number": str(
                item.get("number") or ""
            ),

            "language": str(
                item.get("language") or ""
            ),

            "quantity": quantity,

            "price_cents": to_cents(
                item.get("addedEur")
            ),

            "image": str(
                item.get("image") or ""
            ),

            "card_path": str(
                item.get("path") or ""
            )

        })

    return cards


# ==========================================
# SAMMLUNG SPEICHERN
# ==========================================

def save_collection(
    seller,
    quote_percent,
    cards
):

    with get_db() as conn:

        cursor = conn.execute("""

            INSERT INTO collections (
                seller,
                created_at,
                status,
                quote_percent
            )

            VALUES (?, ?, 'offen', ?)

        """, (
            seller,
            now(),
            quote_percent
        ))

        collection_id = cursor.lastrowid

        for card in cards:

            conn.execute("""

                INSERT INTO cards (
                    collection_id,
                    card_id,
                    name,
                    set_name,
                    number,
                    language,
                    quantity,
                    price_cents,
                    image,
                    card_path
                )

                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            """, (

                collection_id,
                card["card_id"],
                card["name"],
                card["set_name"],
                card["number"],
                card["language"],
                card["quantity"],
                card["price_cents"],
                card["image"],
                card["card_path"]

            ))

    return collection_id


# ==========================================
# SAMMLUNGEN LADEN
# ==========================================

def get_collections():

    with get_db() as conn:

        return conn.execute("""

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

            GROUP BY c.id

            ORDER BY c.id DESC

        """).fetchall()


def get_cards(collection_id):

    with get_db() as conn:

        return conn.execute("""

            SELECT *

            FROM cards

            WHERE collection_id = ?

            ORDER BY id

        """, (
            collection_id,
        )).fetchall()


# ==========================================
# KAUFEN
# ==========================================

def mark_purchased(
    collection_id,
    purchase_cents
):

    with get_db() as conn:

        conn.execute("""

            UPDATE collections

            SET
                status = 'gekauft',
                purchase_cents = ?,
                purchased_at = ?

            WHERE id = ?
              AND status = 'offen'

        """, (

            purchase_cents,
            now(),
            collection_id

        ))


# ==========================================
# KAUF RÜCKGÄNGIG
# ==========================================

def undo_purchase(collection_id):

    with get_db() as conn:

        conn.execute("""

            UPDATE collections

            SET
                status = 'offen',
                purchase_cents = NULL,
                purchased_at = NULL

            WHERE id = ?
              AND status = 'gekauft'

        """, (
            collection_id,
        ))


# ==========================================
# SAMMLUNG LÖSCHEN
# ==========================================

def delete_collection(collection_id):

    with get_db() as conn:

        conn.execute("""

            DELETE FROM collections

            WHERE id = ?
              AND status = 'offen'

        """, (
            collection_id,
        ))


# ==========================================
# EXCEL EXPORT
# ==========================================

def create_excel(collection, cards):

    rows = []

    for card in cards:

        total = (
            card["price_cents"]
            * card["quantity"]
        )

        offer = percent_value(
            total,
            collection["quote_percent"]
        )

        rows.append({

            "Verkäufer":
                collection["seller"],

            "Karten-ID":
                card["card_id"],

            "Kartenname":
                card["name"],

            "Set":
                card["set_name"],

            "Kartennummer":
                card["number"],

            "Sprache":
                card["language"],

            "Zustand (Annahme)":
                "Near Mint",

            "Verkäuferland (Vorgabe)":
                "Deutschland",

            "Menge":
                card["quantity"],

            "Cardvalue Einzelpreis (€)":
                card["price_cents
