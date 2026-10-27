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
# GRUNDEINSTELLUNGEN
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

def to_cents(value):
    amount = Decimal(str(value if value is not None else 0))

    if not amount.is_finite():
        raise ValueError("Ungültiger Preis")

    return int(
        (amount * 100).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )
    )


def format_euro(value_cents):
    value = Decimal(value_cents) / 100

    return (
        f"{value:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
        + " €"
    )


def calculate_offer(market_cents, percentage=80):
    return int(
        (
            Decimal(market_cents)
            * Decimal(percentage)
            / 100
        ).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )
    )


def get_connection():
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


# ==========================================
# DATENBANK
# ==========================================

def initialize_database():

    with get_connection() as db:

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

                FOREIGN KEY (collection_id)
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
            "Die hochgeladene Datei enthält kein gültiges JSON."
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
            quantity = int(
                item.get("quantity", 1)
            )

            price_cents = to_cents(
                item.get("addedEur")
            )

            if quantity < 1 or price_cents < 0:
                raise ValueError()

        except (
            ValueError,
            TypeError,
            ArithmeticError
        ):
            raise ValueError(
                f"Ungültige Menge oder Preis bei Karte {index}."
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
            "price_cents": price_cents,
            "image": str(
                item.get("image") or ""
            )
        })

    if not cards:
        raise ValueError(
            "Die Sammlung enthält keine Karten."
        )

    return cards


# ==========================================
# SAMMLUNG SPEICHERN
# ==========================================

def save_collection(seller, cards):

    with get_connection() as db:

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


# ==========================================
# SAMMLUNGEN ABRUFEN
# ==========================================

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

    parameters = []

    if status is not None:

        query += """
            WHERE c.status = ?
        """

        parameters.append(status)

    query += """
        GROUP BY c.id
        ORDER BY c.id DESC
    """

    with get_connection() as db:

        return db.execute(
            query,
            parameters
        ).fetchall()


def get_cards(collection_id):

    with get_connection() as db:

        return db.execute("""
            SELECT *
            FROM cards
            WHERE collection_id = ?
            ORDER BY id
        """, (
            collection_id,
        )).fetchall()


# ==========================================
# KAUFVERWALTUNG
# ==========================================

def purchase_collection(
    collection_id,
    purchase_cents
):

    with get_connection() as db:

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

    with get_connection() as db:

        db.execute("""
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


def delete_collection(collection_id):

    with get_connection() as db:

        db.execute("""
            DELETE FROM collections

            WHERE id = ?
              AND status = 'offen'
        """, (
            collection_id,
        ))


# ==========================================
# KARTEN ANZEIGEN
# ==========================================

def display_cards(collection_id):

    cards = get_cards(collection_id)

    for card in cards:

        left, right = st.columns([1, 4])

        with left:

            if card["image"]:

                st.image(
                    card["image"],
                    width=140
                )

        with right:

            st.subheader(
                card["name"]
            )

            st.write(
                f"Set: {card['set_name']}"
            )

            st.write(
                f"Kartennummer: {card['number']}"
            )

            st.write(
                f"Sprache: {card['language']}"
            )

            st.write(
                f"Menge: {card['quantity']}"
            )

            total_cents = (
                card["quantity"]
                * card["price_cents"]
            )

            st.write(
                "Einzelpreis: "
                f"**{format_euro(card['price_cents'])}**"
            )

            st.write(
                "Gesamtwert: "
                f"**{format_euro(total_cents)}**"
            )

            st.write(
                "80-%-Ankaufswert: "
                f"**{format_euro(calculate_offer(total_cents))}**"
            )

            search_text = quote(
                f"{card['name']} "
                f"{card['number']} "
                f"{card['set_name']}"
            )

            cardmarket_url = (
                "https://www.cardmarket.com/"
                "en/Pokemon/Products/Search"
                f"?searchString={search_text}"
            )

            st.link_button(
                "🔗 Auf Cardmarket suchen",
                cardmarket_url
            )

        st.divider()


# ==========================================
# EXCEL EXPORT
# ==========================================

def create_excel(collection_id, seller):

    cards = get_cards(collection_id)

    rows = []

    for card in cards:

        total_cents = (
            card["quantity"]
            * card["price_cents"]
        )

        rows.append({
            "Verkäufer": seller,
            "Karten-ID": card["card_id"],
            "Kartenname": card["name"],
            "Set": card["set_name"],
            "Kartennummer": card["number"],
            "Sprache": card["language"],
            "Menge": card["quantity"],

            "Einzelpreis (€)":
                card["price_cents"] / 100,

            "Gesamtwert (€)":
                total_cents / 100,

            "80% Ankauf (€)":
                calculate_offer(total_cents) / 100,

            "Bildlink": card["image"]
        })

    dataframe = pd.DataFrame(rows)

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="xlsxwriter"
    ) as writer:

        dataframe.to_excel(
            writer,
            sheet_name="Sammlung",
            index=False
        )

        worksheet = writer.sheets["Sammlung"]

        worksheet.freeze_panes(1, 0)

        worksheet.set_column(
            0, 0, 24
        )

        worksheet.set_column(
            1, 1, 24
        )

        worksheet.set_column(
            2, 3, 30
        )

        worksheet.set_column(
            4, 6, 18
        )

        worksheet.set_column(
            7, 9, 22
        )

        worksheet.set_column(
            10, 10, 70
        )

    return output.getvalue()


def excel_button(collection):

    collection_id = collection["id"]

    excel_data = create_excel(
        collection_id,
        collection["seller"]
    )

    st.download_button(
        "📥 Sammlung als Excel herunterladen",
        data=excel_data,
        file_name=f"sammlung_{collection_id}.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        key=f"excel_{collection_id}",
        use_container_width=True
    )


# ==========================================
# DATENBANK
