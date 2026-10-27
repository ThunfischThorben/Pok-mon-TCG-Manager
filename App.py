import io
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st


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
        amount = Decimal(str(value if value is not None else 0))

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
        (Decimal(value) * Decimal(percent) / 100).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )
    )


def now():
    return datetime.now(timezone.utc).isoformat()


def cardmarket_link(card):
    search = quote(
        f'{card["name"]} {card["number"]} {card["set_name"]}'
    )
    return (
        "https://www.cardmarket.com/en/Pokemon/"
        f"Products/Search?searchString={search}"
    )


# ==========================================
# DATENBANK
# ==========================================

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
        raise ValueError("Die Datei ist kein gültiges JSON.")

    if not isinstance(data, dict):
        raise ValueError("Ungültiges JSON-Format.")

    items = data.get("items")

    if not isinstance(items, list) or not items:
        raise ValueError(
            "Die JSON-Datei enthält keine Karten."
        )

    cards = []

    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"Ungültiger Karteneintrag: {index}"
            )

        try:
            quantity = int(item.get("quantity", 1))
            if quantity < 1:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError(
                f"Ungültige Stückzahl bei Karte {index}"
            )

        cards.append({
            "card_id": str(item.get("id") or ""),
            "name": str(item.get("name") or "Unbekannt"),
            "set_name": str(item.get("setName") or ""),
            "number": str(item.get("number") or ""),
            "language": str(item.get("language") or ""),
            "quantity": quantity,
            "price_cents": to_cents(item.get("addedEur")),
            "image": str(item.get("image") or ""),
            "card_path": str(item.get("path") or "")
        })

    return cards


# ==========================================
# SAMMLUNG SPEICHERN
# ==========================================

def save_collection(seller, quote_percent, cards):
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO collections (
                seller,
                created_at,
                status,
                quote_percent
            )
            VALUES (?, ?, 'offen', ?)
        """, (seller, now(), quote_percent))

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
        """, (collection_id,)).fetchall()


# ==========================================
# KAUFEN / RÜCKGÄNGIG MACHEN
# ==========================================

def mark_purchased(collection_id, purchase_cents):
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
        """, (collection_id,))


def delete_collection(collection_id):
    with get_db() as conn:
        conn.execute("""
            DELETE FROM collections
            WHERE id = ?
              AND status = 'offen'
        """, (collection_id,))


# ==========================================
# EXCEL EXPORT
# ==========================================

def create_excel(collection, cards):
    rows = []

    for card in cards:
        total = card["price_cents"] * card["quantity"]
        offer = percent_value(
            total,
            collection["quote_percent"]
        )

        rows.append({
            "Verkäufer": collection["seller"],
            "Karten-ID": card["card_id"],
            "Kartenname": card["name"],
            "Set": card["set_name"],
            "Kartennummer": card["number"],
            "Sprache": card["language"],
            "Zustand (Annahme)": "Near Mint",
            "Verkäuferland (Vorgabe)": "Deutschland",
            "Menge": card["quantity"],
            "Cardvalue Einzelpreis (€)":
                card["price_cents"] / 100,
            "Gesamtwert (€)": total / 100,
            "Ankaufsquote (%)":
                collection["quote_percent"],
            "Ankaufswert (€)": offer / 100,
            "Bildlink": card["image"],
            "Cardmarket Suche": cardmarket_link(card)
        })

    df = pd.DataFrame(rows)

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="xlsxwriter"
    ) as writer:
        df.to_excel(
            writer,
            sheet_name="Sammlung",
            index=False
        )

        sheet = writer.sheets["Sammlung"]

        sheet.freeze_panes(1, 0)
        sheet.set_column("A:A", 22)
        sheet.set_column("B:B", 22)
        sheet.set_column("C:D", 28)
        sheet.set_column("E:I", 19)
        sheet.set_column("J:M", 22)
        sheet.set_column("N:O", 65)

    return output.getvalue()


# ==========================================
# APP STARTEN
# ==========================================

init_db()

st.title("🃏 Pokémon TCG Manager")
st.write("Dein persönlicher Pokémon-Ankaufsberater")

st.divider()

tab_dashboard, tab_storage, tab_import = st.tabs([
    "📊 Dashboard",
    "📦 Sammlungen",
    "➕ Neue Sammlung"
])


# ==========================================
# DASHBOARD
# ==========================================

with tab_dashboard:
    st.header("📊 Dashboard")

    all_collections = get_collections()

    purchased = [
        c for c in all_collections
        if c["status"] == "gekauft"
    ]

    invested = sum(
        c["purchase_cents"] or 0
        for c in purchased
    )

    market_value = sum(
        c["market_cents"]
        for c in purchased
    )

    profit = market_value - invested

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Investiertes Kapital",
            euro(invested)
        )

    with col2:
        st.metric(
            "Warenwert",
            euro(market_value)
        )

    with col3:
        st.metric(
            "Rechnerische Differenz",
            euro(profit)
        )

    st.caption(
        "Das Dashboard berücksichtigt ausschließlich "
        "gekaufte Sammlungen. Die Differenz ist kein "
        "realisierter Gewinn und berücksichtigt keine "
        "Gebühren oder Verkaufskosten."
    )

    st.divider()

    st.subheader("📦 Gekaufte Sammlungen")

    if not purchased:
        st.info(
            "Noch keine Sammlung als gekauft markiert."
        )
    else:
        for collection in purchased:
            with st.expander(
                f'✅ {collection["seller"]} '
                f'– {euro(collection["market_cents"])}'
            ):
                st.write(
                    "Karten:",
                    collection["card_count"]
                )
                st.write(
                    "Kaufpreis:",
                    euro(collection["purchase_cents"] or 0)
                )
                st.write(
                    "Warenwert:",
                    euro(collection["market_cents"])
                )


# ==========================================
# SAMMLUNGS-STORAGE
# ==========================================

with tab_storage:
    st.header("📦 Gespeicherte Sammlungen")

    all_collections = get_collections()

    if not all_collections:
        st.info(
            "Noch keine Sammlungen gespeichert."
        )

    for collection in all_collections:
        collection_id = collection["id"]
        seller = collection["seller"]
        market = collection["market_cents"]
        quote_percent = collection["quote_percent"]

        suggested = percent_value(
            market,
            quote_percent
        )

        status = collection["status"]

        status_text = (
            "✅ Gekauft"
            if status == "gekauft"
            else "🕒 Offen"
        )

        with st.expander(
            f"{status_text} | {seller} | "
            f"{euro(market)}"
        ):
            st.write(
                f"**Verkäufer:** {seller}"
            )

            st.write(
                f'**Karten:** {collection["card_count"]}'
            )

            st.write(
                f"**Sammlungswert:** {euro(market)}"
            )

            st.write(
                f"**Ankaufsquote:** {quote_percent} %"
            )

            st.write(
                f"**Empfohlener Ankauf:** "
                f"{euro(suggested)}"
            )

            cards = get_cards(collection_id)

            st.divider()
            st.subheader("🃏 Karten")

            for card in cards:
                left, right = st.columns([1, 4])

                with left:
                    if card["image"]:
                        st.image(
                            card["image"],
                            width=120
                        )

                with right:
                    st.markdown(
                        f'**{card["name"]}**'
                    )

                    st.write(
                        f'{card["set_name"]} | '
                        f'{card["number"]}'
                    )

                    st.write(
                        f'Sprache: {card["language"]} | '
                        f'Menge: {card["quantity"]}'
                    )

                    st.write(
                        f'Preis je Karte: '
                        f'{euro(card["price_cents"])}'
                    )

                    st.link_button(
                        "🔗 Cardmarket",
                        cardmarket_link(card),
                        key=f"link_{collection_id}_{card['id']}"
                    )

            st.divider()

            excel = create_excel(
                collection,
                cards
            )

            st.download_button(
                "📥 Excel herunterladen",
                data=excel,
                file_name=f"sammlung_{collection_id}.xlsx",
                mime=(
                    "application/vnd.openxmlformats-"
                    "officedocument.spreadsheetml.sheet"
                ),
                key=f"excel_{collection_id}"
            )

            st.divider()

            if status == "offen":
                purchase_eur = st.number_input(
                    "Tatsächlicher Kaufpreis (€)",
                    min_value=0.0,
                    value=suggested / 100,
                    step=1.0,
                    key=f"purchase_{collection_id}"
                )

                if st.button(
                    "✅ Als gekauft markieren",
                    key=f"buy_{collection_id}",
                    type="primary"
                ):
                    mark_purchased(
                        collection_id,
                        to_cents(purchase_eur)
                    )
                    st.rerun()

                with st.popover(
                    "🗑️ Sammlung löschen"
                ):
                    st.warning(
                        "Die Sammlung wird dauerhaft gelöscht."
                    )

                    if st.button(
                        "Löschen bestätigen",
                        key=f"delete_{collection_id}"
                    ):
                        delete_collection(collection_id)
                        st.rerun()

            else:
                st.success(
                    "Diese Sammlung ist gekauft "
                    "und im Dashboard enthalten."
                )

                st.write(
                    "Tatsächlicher Kaufpreis:",
                    euro(collection["purchase_cents"] or 0)
                )

                if st.button(
                    "↩️ Kauf rückgängig machen",
                    key=f"undo_{collection_id}"
                ):
                    undo_purchase(collection_id)
                    st.rerun()


# ==========================================
# NEUE SAMMLUNG
# ==========================================

with tab_import:
    st.header("➕ Neue Sammlung anlegen")

    st.write(
        "Lade eine JSON-Datei von Cardvalue.ai hoch. "
        "Die Sammlung wird zunächst nur gespeichert "
        "und nicht ins Dashboard übernommen."
    )

    with st.form("import_form"):
        seller = st.text_input(
            "Name des Verkäufers / der Sammlung",
            placeholder="z. B. Max Mustermann"
        )

        uploaded = st.file_uploader(
            "Cardvalue.ai JSON-Datei hochladen",
            type=["json"]
        )

        quote_percent = st.slider(
            "Ankaufsquote (%)",
            min_value=0,
            max_value=100,
            value=80
        )

        submitted = st.form_submit_button(
            "📦 Sammlung speichern",
            type="primary",
            use_container_width=True
        )

    if submitted:
        if not seller.strip():
            st.warning(
                "Bitte einen Verkäufernamen eingeben."
            )

        elif uploaded is None:
            st.warning(
                "Bitte eine JSON-Datei hochladen."
            )

        else:
            try:
                cards = parse_json(uploaded)

                collection_id = save_collection(
                    seller.strip(),
                    quote_percent,
                    cards
                )

                st.success(
                    f"Sammlung gespeichert! "
                    f"ID: {collection_id}"
                )

                st.info(
                    "Die Sammlung liegt jetzt im Storage. "
                    "Sie wird erst nach dem Klick auf "
                    "'Als gekauft markieren' "
                    "im Dashboard berücksichtigt."
                )

            except ValueError as error:
                st.error(str(error))


st.divider()

st.caption(
    "Pokémon TCG Manager | "
    "JSON-Import · Sammlungsverwaltung · "
    "Ankaufsberechnung · Excel-Export"
)
