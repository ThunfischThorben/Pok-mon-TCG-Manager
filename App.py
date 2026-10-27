import io
import json
import re
import sqlite3

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import pandas as pd
import streamlit as st


# =====================================================
# KONFIGURATION
# =====================================================

st.set_page_config(
    page_title="Pokémon TCG Manager",
    page_icon="🃏",
    layout="wide"
)

DB_PATH = Path("pokemon_manager.sqlite3")

DEFAULT_QUOTE = 80

CARDMARKET_BASE = (
    "https://www.cardmarket.com/en/Pokemon"
)

# Cardmarket-Sprachcodes
LANGUAGE_IDS = {
    "en": 1,
    "eng": 1,
    "english": 1,
    "englisch": 1,

    "fr": 2,
    "fra": 2,
    "french": 2,
    "französisch": 2,

    "de": 3,
    "ger": 3,
    "german": 3,
    "deutsch": 3,

    "es": 4,
    "spa": 4,
    "spanish": 4,
    "spanisch": 4,

    "it": 5,
    "ita": 5,
    "italian": 5,
    "italienisch": 5,

    "zh": 6,
    "chinese": 6,
    "chinesisch": 6,

    "ja": 7,
    "jp": 7,
    "jpn": 7,
    "japanese": 7,
    "japanisch": 7,

    "pt": 8,
    "por": 8,
    "portuguese": 8,
    "portugiesisch": 8,

    "ko": 10,
    "kr": 10,
    "kor": 10,
    "korean": 10,
    "koreanisch": 10
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


# =====================================================
# HILFSFUNKTIONEN
# =====================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def to_cents(value):
    try:
        amount = Decimal(
            str(value if value is not None else 0)
        )

        if not amount.is_finite():
            raise ValueError()

        if amount < 0:
            raise ValueError()

        return int(
            (amount * 100).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP
            )
        )

    except (
        ValueError,
        TypeError,
        ArithmeticError
    ):
        raise ValueError(
            "Ungültiger Euro-Preis."
        )


def euro(cents):
    amount = cents / 100

    formatted = f"{amount:,.2f}"

    formatted = (
        formatted
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    return formatted + " €"


def percent_value(cents, percent):
    return int(
        (
            Decimal(cents)
            * Decimal(percent)
            / Decimal(100)
        ).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )
    )


def language_name(language):
    key = str(language or "").strip().lower()

    return LANGUAGE_NAMES.get(
        key,
        str(language or "Unbekannt")
    )


def language_id(language):
    key = str(language or "").strip().lower()

    return LANGUAGE_IDS.get(key)


def safe_filename(text):
    result = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "_",
        str(text)
    )

    return result.strip("_")[:60] or "sammlung"


# =====================================================
# CARDMARKET
# =====================================================

def cardmarket_search_link(card):
    """
    Direkte Suche auf Cardmarket.

    Ohne Cardmarket-Produkt-ID lässt sich eine
    bestimmte Produktseite nicht sicher bestimmen.
    """

    name = str(
        card["name"] or ""
    ).strip()

    number = str(
        card["number"] or ""
    ).strip()

    set_name = str(
        card["set_name"] or ""
    ).strip()

    # Bei Nummern wie 180/196 wird die
    # eigentliche Sammlernummer verwendet.
    collector_number = number.split("/")[0].strip()

    search_text = " ".join(
        value
        for value in [
            name,
            collector_number,
            set_name
        ]
        if value
    )

    parameters = {
        "searchString": search_text
    }

    return (
        CARDMARKET_BASE
        + "/Products/Search?"
        + urlencode(parameters)
    )


def normalize_cardmarket_product_url(url):
    """
    Akzeptiert nur echte Cardmarket-Produktseiten
    im Pokémon-Einzelkartenbereich.
    """

    url = str(url or "").strip()

    if not url:
        return ""

    parsed = urlparse(url)

    hostname = (
        parsed.hostname or ""
    ).lower()

    valid_hosts = {
        "cardmarket.com",
        "www.cardmarket.com"
    }

    if parsed.scheme != "https":
        raise ValueError(
            "Bitte einen HTTPS-Link verwenden."
        )

    if hostname not in valid_hosts:
        raise ValueError(
            "Der Link muss von Cardmarket stammen."
        )

    if not re.match(
        r"^/(?:en|de|fr|it|es)/Pokemon/"
        r"Products/Singles/[^/]+/[^/]+/?$",
        parsed.path
    ):
        raise ValueError(
            "Bitte die direkte Produktseite einer "
            "Pokémon-Einzelkarte einfügen."
        )

    return urlunparse(
        (
            "https",
            "www.cardmarket.com",
            parsed.path,
            "",
            "",
            ""
        )
    )


def cardmarket_product_link(card):
    """
    Baut einen gefilterten Link für eine
    bekannte Cardmarket-Produktseite.

    Sprache: aus der JSON-Datei
    Zustand: mindestens Near Mint
    Verkäuferland: Deutschland
    """

    product_url = str(
        card["cardmarket_url"] or ""
    ).strip()

    if not product_url:
        return None

    try:
        product_url = normalize_cardmarket_product_url(
            product_url
        )

    except ValueError:
        return None

    parsed = urlparse(product_url)

    parameters = dict(
        parse_qsl(
            parsed.query,
            keep_blank_values=True
        )
    )

    card_language = language_id(
        card["language"]
    )

    if card_language is not None:
        parameters["language"] = str(
            card_language
        )

    # Cardmarket:
    # 1 = Mint
    # 2 = Near Mint
    parameters["minCondition"] = "2"

    # Deutschland
    parameters["sellerCountry"] = "7"

    return urlunparse(
        parsed._replace(
            query=urlencode(parameters),
            fragment=""
        )
    )


def cardmarket_link(card):
    """
    Exakte Produktseite, falls vorhanden.
    Andernfalls direkte Cardmarket-Suche.
    """

    product_link = cardmarket_product_link(card)

    if product_link:
        return product_link

    return cardmarket_search_link(card)


# =====================================================
# DATENBANK
# =====================================================

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
                cardmarket_url TEXT DEFAULT '',
                FOREIGN KEY(collection_id)
                    REFERENCES collections(id)
                    ON DELETE CASCADE
            )
        """)

        # Bestehende Datenbank automatisch erweitern.
        # Bereits gespeicherte Sammlungen bleiben erhalten.

        columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(cards)"
            ).fetchall()
        ]

        if "cardmarket_url" not in columns:
            conn.execute("""
                ALTER TABLE cards
                ADD COLUMN cardmarket_url TEXT DEFAULT ''
            """)


# =====================================================
# JSON IMPORT
# =====================================================

def parse_json(uploaded_file):
    try:
        data = json.load(uploaded_file)

    except (
        ValueError,
        UnicodeDecodeError
    ):
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
                f"Ungültiger Eintrag bei Karte {index}."
            )

        try:
            quantity = int(
                item.get("quantity", 1)
            )

            if quantity < 1:
                raise ValueError()

        except (
            TypeError,
            ValueError
        ):
            raise ValueError(
                f"Ungültige Stückzahl bei Karte {index}."
            )

        cardmarket_url = str(
            item.get("cardmarketUrl")
            or item.get("cardmarket_url")
            or ""
        ).strip()

        if cardmarket_url:
            try:
                cardmarket_url = (
                    normalize_cardmarket_product_url(
                        cardmarket_url
                    )
                )
            except ValueError:
                cardmarket_url = ""

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
            ),
            "cardmarket_url": cardmarket_url
        })

    return cards


# =====================================================
# SAMMLUNG SPEICHERN
# =====================================================

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
                    card_path,
                    cardmarket_url
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
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
                card["card_path"],
                card["cardmarket_url"]
            ))

    return collection_id


# =====================================================
# SAMMLUNGEN LADEN
# =====================================================

def get_collections():
    with get_db() as conn:

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
    with get_db() as conn:

        return conn.execute("""
            SELECT *
            FROM cards
            WHERE collection_id = ?
            ORDER BY id
        """, (
            collection_id,
        )).fetchall()


def save_cardmarket_url(
    card_id,
    collection_id,
    product_url
):
    normalized = (
        normalize_cardmarket_product_url(
            product_url
        )
        if product_url.strip()
        else ""
    )

    with get_db() as conn:
        conn.execute("""
            UPDATE cards
            SET cardmarket_url = ?
            WHERE id = ?
              AND collection_id = ?
        """, (
            normalized,
            card_id,
            collection_id
        ))


# =====================================================
# KAUFVERWALTUNG
# =====================================================

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


def delete_collection(collection_id):
    with get_db() as conn:

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
                language_name(card["language"]),

            "Zustand (Annahme)":
                "Near Mint",

            "Verkäuferland (Vorgabe)":
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
                offer / 100,

            "Bildlink":
                card["image"],

            "Cardmarket Link":
                cardmarket_link(card),

            "Cardmarket Produkt zugeordnet":
                "Ja"
                if cardmarket_product_link(card)
                else "Nein"
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
        sheet.set_column("E:I", 20)
        sheet.set_column("J:M", 23)
        sheet.set_column("N:O", 65)
        sheet.set_column("P:P", 24)

        money_format = writer.book.add_format({
            "num_format": '#,##0.00 "€"'
        })

        for column in ["J", "K", "M"]:
            sheet.set_column(
                f"{column}:{column}",
                22,
                money_format
            )

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

    all_collections = get_collections()

    purchased = [
        collection
        for collection in all_collections
        if collection["status"] == "gekauft"
    ]

    invested = sum(
        collection["purchase_cents"] or 0
        for collection in purchased
    )

    market_value = sum(
        collection["market_cents"]
        for collection in purchased
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
        "Nur gekaufte Sammlungen werden berücksichtigt. "
        "Die Werte stammen aus den importierten "
        "Cardvalue.ai-Preisen. Gebühren und Versand "
        "sind nicht berücksichtigt."
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
                f'✅ {collection["seller"]} – '
                f'{euro(collection["market_cents"])}'
            ):

                st.write(
                    "Karten:",
                    collection["card_count"]
                )

                st.write(
                    "Kaufpreis:",
                    euro(
                        collection["purchase_cents"] or 0
                    )
                )

                st.write(
                    "Warenwert:",
                    euro(
                        collection["market_cents"]
                    )
                )

                difference = (
                    collection["market_cents"]
                    - (
                        collection["purchase_cents"]
                        or 0
                    )
                )

                st.write(
                    "Rechnerische Differenz:",
                    euro(difference)
                )


# =====================================================
# SAMMLUNGEN
# =====================================================

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
            f"{status_text} | "
            f"{seller} | "
            f"{euro(market)}"
        ):

            st.write(
                f"**Verkäufer:** {seller}"
            )

            st.write(
                f'**Karten:** '
                f'{collection["card_count"]}'
            )

            st.write(
                f"**Sammlungswert:** "
                f"{euro(market)}"
            )

            st.write(
                f"**Ankaufsquote:** "
                f"{quote_percent} %"
            )

            st.write(
                f"**Empfohlener Ankauf:** "
                f"{euro(suggested)}"
            )

            cards = get_cards(collection_id)

            st.divider()

            st.subheader("🃏 Karten")

            for card in cards:

                left, right = st.columns(
                    [1, 4]
                )

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
                        f'Sprache: '
                        f'{language_name(card["language"])}'
                    )

                    st.write(
                        f'Menge: {card["quantity"]}'
                    )

                    st.write(
                        f'Einzelpreis: '
                        f'{euro(card["price_cents"])}'
                    )

                    st.caption(
                        "Cardmarket-Vorgaben: "
                        "Verkäufer Deutschland · "
                        "mindestens Near Mint"
                    )

                    exact_link = (
                        cardmarket_product_link(card)
                    )

                    if exact_link:

                        st.link_button(
                            "🔗 Cardmarket – gefilterte Angebote",
                            exact_link
                        )

                        st.caption(
                            "Produktseite hinterlegt. "
                            "Sprache aus JSON, Deutschland "
                            "und mindestens NM in der URL."
                        )

                    else:

                        st.link_button(
                            "🔎 Direkt auf Cardmarket suchen",
                            cardmarket_search_link(card)
                        )

                        st.caption(
                            "Die exakte Produktseite ist "
                            "noch nicht zugeordnet. "
                            "Die Angebotsfilter können "
                            "erst auf der Produktseite "
                            "zuverlässig angewendet werden."
                        )

                    with st.popover(
                        "⚙️ Cardmarket-Produktlink"
                    ):

                        st.write(
                            "Hier kannst du einmalig die "
                            "exakte Cardmarket-Produktseite "
                            "dieser Karte speichern."
                        )

                        url_input = st.text_input(
                            "Cardmarket-Produkt-URL",
                            value=(
                                card["cardmarket_url"]
                                or ""
                            ),
                            key=(
                                f"cm_url_"
                                f"{collection_id}_"
                                f"{card['id']}"
                            ),
                            placeholder=(
                                "https://www.cardmarket.com/"
                                "en/Pokemon/Products/Singles/..."
                            )
                        )

                        if st.button(
                            "Produktlink speichern",
                            key=(
                                f"cm_save_"
                                f"{collection_id}_"
                                f"{card['id']}"
                            )
                        ):

                            try:

                                save_cardmarket_url(
                                    card["id"],
                                    collection_id,
                                    url_input
                                )

                                st.rerun()

                            except ValueError as error:

                                st.error(
                                    str(error)
                                )

                st.divider()

            excel = create_excel(
                collection,
                cards
            )

            st.download_button(
                "📥 Excel herunterladen",
                data=excel,
                file_name=(
                    f"sammlung_"
                    f"{collection_id}_"
                    f"{safe_filename(seller)}.xlsx"
                ),
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
                        "Diese Sammlung wird "
                        "dauerhaft gelöscht."
                    )

                    if st.button(
                        "Löschen bestätigen",
                        key=f"delete_{collection_id}"
                    ):

                        delete_collection(
                            collection_id
                        )

                        st.rerun()

            else:

                st.success(
                    "Diese Sammlung wurde gekauft "
                    "und ist im Dashboard enthalten."
                )

                st.write(
                    "Tatsächlicher Kaufpreis:",
                    euro(
                        collection["purchase_cents"]
                        or 0
                    )
                )

                if st.button(
                    "↩️ Kauf rückgängig machen",
                    key=f"undo_{collection_id}"
                ):

                    undo_purchase(
                        collection_id
                    )

                    st.rerun()


# =====================================================
# NEUE SAMMLUNG
# =====================================================

with tab_import:

    st.header("➕ Neue Sammlung anlegen")

    st.write(
        "Lade deine Cardvalue.ai-JSON-Datei hoch. "
        "Die Kartensprache wird automatisch übernommen."
    )

    with st.form("import_form"):

        seller = st.text_input(
            "Name des Verkäufers / der Sammlung",
            placeholder="z. B. Max Mustermann"
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

                collection_id = save_collection(
                    seller.strip(),
                    quote_percent,
                    cards
                )

                total_cards = sum(
                    card["quantity"]
                    for card in cards
                )

                total_value = sum(
                    card["price_cents"]
                    * card["quantity"]
                    for card in cards
                )

                st.success(
                    "Sammlung erfolgreich gespeichert!"
                )

                st.write(
                    f"**Sammlungs-ID:** "
                    f"{collection_id}"
                )

                st.write(
                    f"**Karten:** {total_cards}"
                )

                st.write(
                    f"**Sammlungswert:** "
                    f"{euro(total_value)}"
                )

                st.info(
                    "Die Sammlung befindet sich jetzt "
                    "im Bereich 'Sammlungen'. "
                    "Erst nach dem Markieren als gekauft "
                    "wird sie im Dashboard berücksichtigt."
                )

            except ValueError as error:

                st.error(
                    str(error)
                )


# =====================================================
# FOOTER
# =====================================================

st.divider()

st.caption(
    "Pokémon TCG Manager | "
    "Cardvalue.ai JSON-Import · "
    "Sammlungsverwaltung · "
    "Cardmarket-Verlinkung · "
    "Excel-Export"
)
