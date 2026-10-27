import io
import json
import re
import sqlite3

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import streamlit as st


# =====================================================
# APP EINSTELLUNGEN
# =====================================================

st.set_page_config(
    page_title="Pokémon TCG Manager",
    page_icon="🃏",
    layout="wide"
)

DB_PATH = Path("pokemon_manager.sqlite3")

DEFAULT_QUOTE = 80

LANGUAGES = {
    "en": 1,
    "eng": 1,
    "english": 1,
    "de": 3,
    "ger": 3,
    "german": 3,
    "fr": 2,
    "es": 4,
    "it": 5,
    "zh": 6,
    "ja": 7,
    "jp": 7,
    "jpn": 7,
    "pt": 8,
    "ko": 10
}

LANGUAGE_NAMES = {
    "en": "Englisch",
    "de": "Deutsch",
    "fr": "Französisch",
    "es": "Spanisch",
    "it": "Italienisch",
    "ja": "Japanisch",
    "jp": "Japanisch",
    "pt": "Portugiesisch",
    "ko": "Koreanisch",
    "zh": "Chinesisch"
}

# Bestätigte Cardmarket-Produktseiten
KNOWN_PRODUCTS = {
    "swsh12.5gg-GG44": (
        "https://www.cardmarket.com/en/Pokemon/"
        "Products/Singles/Crown-Zenith/"
        "Mewtwo-VSTAR-CRZGG44"
    )
}


# =====================================================
# HILFSFUNKTIONEN
# =====================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def cents(value):
    try:
        number = Decimal(
            str(0 if value is None else value)
        )

        if not number.is_finite() or number < 0:
            raise ValueError()

        return int(
            (number * 100).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP
            )
        )

    except (ValueError, TypeError, ArithmeticError):
        raise ValueError(
            "Ungültiger Preis in der JSON-Datei."
        )


def money(value):
    return (
        f"{value / 100:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
        + " €"
    )


def percentage(value, pct):
    return int(
        (
            Decimal(value)
            * Decimal(pct)
            / Decimal(100)
        ).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )
    )


def card_language(card):
    return str(
        card["language"] or ""
    ).lower().strip()


def language_name(card):
    key = card_language(card)

    return LANGUAGE_NAMES.get(
        key,
        key or "Unbekannt"
    )


def cardmarket_filters(card):
    result = {
        "sellerCountry": "7",
        "minCondition": "2"
    }

    language = LANGUAGES.get(
        card_language(card)
    )

    if language is not None:
        result["language"] = str(language)

    return result


# =====================================================
# CARDMARKET LINKS
# =====================================================

def direct_cardmarket_link(card):
    """
    Direkter Cardmarket-Link, wenn die
    korrekte Produktseite bekannt ist.

    Filter:
    - Verkäuferland Deutschland
    - Mindestens Near Mint
    - Sprache aus JSON
    """

    card_id = str(
        card["card_id"] or ""
    ).strip()

    product_url = KNOWN_PRODUCTS.get(
        card_id
    )

    if not product_url:
        return None

    return (
        product_url
        + "?"
        + urlencode(
            cardmarket_filters(card)
        )
    )


def web_search_link(card):
    """
    Normale Google-Websuche nach der
    richtigen Cardmarket-Produktseite.

    Verwendet:
    - Kartenname
    - Set
    - Kartennummer
    - Cardmarket-Domain
    """

    name = str(
        card["name"] or ""
    ).strip()

    set_name = str(
        card["set_name"] or ""
    ).strip()

    number = str(
        card["number"] or ""
    ).strip()

    number_short = number.split("/")[0].strip()

    query = " ".join(
        part
        for part in [
            "site:cardmarket.com/en/Pokemon/Products/Singles/",
            name,
            set_name,
            number_short
        ]
        if part
    )

    return (
        "https://www.google.com/search?"
        + urlencode({
            "q": query
        })
    )


def cardmarket_link(card):
    """
    Bekannte Produktseite direkt öffnen.
    Sonst normale Websuche verwenden.
    """

    direct = direct_cardmarket_link(card)

    if direct:
        return direct

    return web_search_link(card)


# =====================================================
# DATENBANK
# =====================================================

def db():
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
    with db() as conn:

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


# =====================================================
# JSON IMPORT
# =====================================================

def parse_json(file):
    try:
        data = json.load(file)

    except (ValueError, UnicodeDecodeError):
        raise ValueError(
            "Keine gültige JSON-Datei."
        )

    items = (
        data.get("items")
        if isinstance(data, dict)
        else None
    )

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
                f"Ungültiger Karteneintrag {index}."
            )

        try:
            quantity = int(
                item.get("quantity", 1)
            )

            if quantity < 1:
                raise ValueError()

        except (ValueError, TypeError):
            raise ValueError(
                f"Ungültige Menge bei Karte {index}."
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
            "price_cents": cents(
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


# =====================================================
# SAMMLUNG SPEICHERN
# =====================================================

def save_collection(seller, pct, cards):
    with db() as conn:

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
            pct
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


# =====================================================
# SAMMLUNGEN LADEN
# =====================================================

def get_collections():
    with db() as conn:

        return conn.execute("""
            SELECT
                c.*,

                COALESCE(
                    SUM(
                        k.quantity * k.price_cents
                    ),
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
    with db() as conn:

        return conn.execute("""
            SELECT *
            FROM cards
            WHERE collection_id = ?
            ORDER BY id
        """, (
            collection_id,
        )).fetchall()


# =====================================================
# KAUFVERWALTUNG
# =====================================================

def mark_purchased(collection_id, value):
    with db() as conn:

        conn.execute("""
            UPDATE collections
            SET
                status = 'gekauft',
                purchase_cents = ?,
                purchased_at = ?
            WHERE id = ?
              AND status = 'offen'
        """, (
            value,
            now(),
            collection_id
        ))


def undo_purchase(collection_id):
    with db() as conn:

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


def delete_collection(collection_id):
    with db() as conn:

        conn.execute("""
            DELETE FROM collections
            WHERE id = ?
              AND status = 'offen'
        """, (
            collection_id,
        ))


# =====================================================
# EXCEL EXPORT
# =====================================================

def create_excel(collection, cards):
    rows = []

    for card in cards:

        total = (
            card["quantity"]
            * card["price_cents"]
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
                language_name(card),

            "Zustand (Annahme)":
                "Near Mint",

            "Verkäuferland":
                "Deutschland",

            "Menge":
                card["quantity"],

            "Cardvalue Einzelpreis (€)":
                card["price_cents"] / 100,

            "Gesamtwert (€)":
                total / 100,

            "Ankaufsquote (%)":
                collection["quote_percent"],

            "Ankaufswert (€)":
                percentage(
                    total,
                    collection["quote_percent"]
                ) / 100,

            "Bildlink":
                card["image"],

            "Cardmarket Link":
                cardmarket_link(card),

            "Linktyp":
                (
                    "Direkte Produktseite"
                    if direct_cardmarket_link(card)
                    else "Google-Websuche"
                )
        })

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="xlsxwriter"
    ) as writer:

        pd.DataFrame(rows).to_excel(
            writer,
            sheet_name="Sammlung",
            index=False
        )

        sheet = writer.sheets["Sammlung"]

        sheet.freeze_panes(1, 0)

        sheet.set_column("A:B", 23)
        sheet.set_column("C:D", 30)
        sheet.set_column("E:M", 20)
        sheet.set_column("N:O", 65)
        sheet.set_column("P:P", 25)

    return output.getvalue()


# =====================================================
# APP STARTEN
# =====================================================

init_db()

st.title("🃏 Pokémon TCG Manager")

st.write(
    "Dein persönlicher Pokémon-Ankaufsmanager"
)

st.divider()

tab_dashboard, tab_storage, tab_import = st.tabs([
    "📊 Dashboard",
    "📦 Sammlungen",
    "➕ Neue Sammlung"
])


# =====================================================
# DASHBOARD
# =====================================================

with tab_dashboard:

    st.header("📊 Dashboard")

    purchased = [
        collection
        for collection in get_collections()
        if collection["status"] == "gekauft"
    ]

    invested = sum(
        collection["purchase_cents"] or 0
        for collection in purchased
    )

    market = sum(
        collection["market_cents"]
        for collection in purchased
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Investiertes Kapital",
            money(invested)
        )

    with col2:
        st.metric(
            "Warenwert",
            money(market)
        )

    with col3:
        st.metric(
            "Rechnerische Differenz",
            money(market - invested)
        )

    st.caption(
        "Nur gekaufte Sammlungen. "
        "Die Warenwerte basieren auf den "
        "importierten Cardvalue.ai-Preisen. "
        "Gebühren und Versand sind nicht enthalten."
    )

    st.divider()

    st.subheader("📦 Gekaufte Sammlungen")

    if not purchased:
        st.info(
            "Noch keine Sammlung als gekauft markiert."
        )

    for collection in purchased:

        with st.expander(
            f'✅ {collection["seller"]} – '
            f'{money(collection["market_cents"])}'
        ):

            st.write(
                "Karten:",
                collection["card_count"]
            )

            st.write(
                "Kaufpreis:",
                money(
                    collection["purchase_cents"] or 0
                )
            )

            st.write(
                "Warenwert:",
                money(collection["market_cents"])
            )

            st.write(
                "Rechnerische Differenz:",
                money(
                    collection["market_cents"]
                    - (
                        collection["purchase_cents"]
                        or 0
                    )
                )
            )


# =====================================================
# SAMMLUNGEN
# =====================================================

with tab_storage:

    st.header("📦 Gespeicherte Sammlungen")

    collections = get_collections()

    if not collections:
        st.info(
            "Noch keine Sammlungen gespeichert."
        )

    for collection in collections:

        cid = collection["id"]

        market = collection["market_cents"]

        suggested = percentage(
            market,
            collection["quote_percent"]
        )

        status = collection["status"]

        status_text = (
            "✅ Gekauft"
            if status == "gekauft"
            else "🕒 Offen"
        )

        with st.expander(
            f'{status_text} | '
            f'{collection["seller"]} | '
            f'{money(market)}'
        ):

            st.write(
                "**Verkäufer:**",
                collection["seller"]
            )

            st.write(
                "**Karten:**",
                collection["card_count"]
            )

            st.write(
                "**Sammlungswert:**",
                money(market)
            )

            st.write(
                "**Ankaufsquote:**",
                f'{collection["quote_percent"]} %'
            )

            st.write(
                "**Empfohlener Ankauf:**",
                money(suggested)
            )

            st.divider()

            st.subheader("🃏 Karten")

            cards = get_cards(cid)

            for card in cards:

                left, right = st.columns([1, 4])

                with left:

                    if card["image"]:

                        try:
                            st.image(
                                card["image"],
                                width=120
                            )

                        except Exception:
                            st.caption(
                                "Bild nicht verfügbar"
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
                        f'Sprache: '
                        f'{language_name(card)} | '
                        f'Menge: {card["quantity"]}'
                    )

                    st.write(
                        "Einzelpreis:",
                        money(card["price_cents"])
                    )

                    direct = direct_cardmarket_link(card)

                    if direct:

                        st.link_button(
                            "🔗 Cardmarket – passende Angebote",
                            direct
                        )

                        st.caption(
                            "Direkte Produktseite mit "
                            "Kartensprache, Deutschland "
                            "und mindestens Near Mint."
                        )

                    else:

                        st.link_button(
                            "🔎 Cardmarket über Google suchen",
                            web_search_link(card)
                        )

                        st.caption(
                            "Normale Websuche nach der "
                            "richtigen Cardmarket-Karte. "
                            "Die Angebotsfilter werden "
                            "erst bei einem bekannten "
                            "Produktlink automatisch gesetzt."
                        )

                st.divider()

            excel = create_excel(
                collection,
                cards
            )

            st.download_button(
                "📥 Excel herunterladen",
                data=excel,
                file_name=f"sammlung_{cid}.xlsx",
                mime=(
                    "application/vnd.openxmlformats-"
                    "officedocument.spreadsheetml.sheet"
                ),
                key=f"excel_{cid}"
            )

            st.divider()

            if status == "offen":

                purchase_eur = st.number_input(
                    "Tatsächlicher Kaufpreis (€)",
                    min_value=0.0,
                    value=suggested / 100,
                    step=1.0,
                    key=f"purchase_{cid}"
                )

                if st.button(
                    "✅ Als gekauft markieren",
                    key=f"buy_{cid}",
                    type="primary"
                ):

                    mark_purchased(
                        cid,
                        cents(purchase_eur)
                    )

                    st.rerun()

                with st.popover(
                    "🗑️ Sammlung löschen"
                ):

                    st.warning(
                        "Diese Sammlung wird "
                        "dauerhaft gelöscht."
                    )

                    if st.button(
                        "Löschen bestätigen",
                        key=f"delete_{cid}"
                    ):

                        delete_collection(cid)

                        st.rerun()

            else:

                st.success(
                    "Diese Sammlung ist gekauft "
                    "und im Dashboard enthalten."
                )

                st.write(
                    "Tatsächlicher Kaufpreis:",
                    money(
                        collection["purchase_cents"]
                        or 0
                    )
                )

                if st.button(
                    "↩️ Kauf rückgängig machen",
                    key=f"undo_{cid}"
                ):

                    undo_purchase(cid)

                    st.rerun()


# =====================================================
# NEUE SAMMLUNG
# =====================================================

with tab_import:

    st.header("➕ Neue Sammlung anlegen")

    st.write(
        "Lade eine Cardvalue.ai-JSON-Datei hoch. "
        "Kartensprache, Kartennummer, Set, "
        "Bilder und Preise werden übernommen."
    )

    with st.form("import_form"):

        seller = st.text_input(
            "Name des Verkäufers / der Sammlung"
        )

        uploaded = st.file_uploader(
            "Cardvalue.ai JSON-Datei",
            type=["json"]
        )

        quote_percent = st.slider(
            "Ankaufsquote (%)",
            min_value=0,
            max_value=100,
            value=DEFAULT_QUOTE
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

                cid = save_collection(
                    seller.strip(),
                    quote_percent,
                    cards
                )

                total = sum(
                    card["quantity"]
                    * card["price_cents"]
                    for card in cards
                )

                st.success(
                    f"Sammlung #{cid} gespeichert!"
                )

                st.write(
                    "Karten:",
                    sum(
                        card["quantity"]
                        for card in cards
                    )
                )

                st.write(
                    "Sammlungswert:",
                    money(total)
                )

                st.info(
                    "Die Sammlung liegt unter "
                    "'Sammlungen' und erscheint "
                    "erst nach dem Kauf im Dashboard."
                )

            except ValueError as error:

                st.error(str(error))


# =====================================================
# FOOTER
# =====================================================

st.divider()

st.caption(
    "Pokémon TCG Manager · "
    "Cardvalue.ai · "
    "Sammlungen · "
    "Cardmarket · "
    "Excel"
)
