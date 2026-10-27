import io
import hashlib
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

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
MARKUP_PERCENT = 20

REQUIRED_COLUMNS = [
    "Karten-ID",
    "Kartenname",
    "Set",
    "Kartennummer",
    "Sprache",
    "Anzahl",
    "Cardmarket-Direktlink",
    "Cardmarket-Preis (€)"
]


# =====================================================
# HILFSFUNKTIONEN
# =====================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def to_cents(value):
    if value is None or pd.isna(value):
        raise ValueError("Ein Preis fehlt.")

    if isinstance(value, str):
        value = value.strip().replace("€", "").replace(" ", "")

        if "," in value:
            value = value.replace(".", "").replace(",", ".")

    try:
        number = Decimal(str(value))

        if not number.is_finite() or number < 0:
            raise ValueError()

        return int(
            (number * 100).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP
            )
        )

    except (ValueError, TypeError, ArithmeticError):
        raise ValueError(f"Ungültiger Preis: {value}")


def money(cents):
    return (
        f"{cents / 100:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
        + " €"
    )


def selling_price(purchase_cents):
    return int(
        (
            Decimal(purchase_cents) * Decimal("1.20")
        ).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP
        )
    )


def clean(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def card_key(card_id, language):
    return f"{card_id.strip().lower()}|{language.strip().lower()}"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# =====================================================
# DATENBANK
# =====================================================

def init_db():
    with get_db() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_key TEXT NOT NULL UNIQUE,
                card_id TEXT NOT NULL,
                name TEXT NOT NULL,
                set_name TEXT,
                card_number TEXT,
                language TEXT,
                cardmarket_url TEXT,
                quantity INTEGER NOT NULL DEFAULT 0,
                purchase_total_cents INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS import_history (
                file_hash TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                imported_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inventory_id INTEGER NOT NULL,
                card_id TEXT NOT NULL,
                name TEXT NOT NULL,
                language TEXT,
                quantity INTEGER NOT NULL,
                purchase_total_cents INTEGER NOT NULL,
                sale_total_cents INTEGER NOT NULL,
                sold_at TEXT NOT NULL,
                FOREIGN KEY(inventory_id)
                    REFERENCES inventory(id)
            )
        """)


# =====================================================
# DATEN LADEN
# =====================================================

def get_inventory():
    with get_db() as conn:
        return conn.execute("""
            SELECT *
            FROM inventory
            ORDER BY name, card_number
        """).fetchall()


def get_sales():
    with get_db() as conn:
        return conn.execute("""
            SELECT *
            FROM sales
            ORDER BY id DESC
        """).fetchall()


def file_already_imported(file_hash):
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM import_history WHERE file_hash=?",
            (file_hash,)
        ).fetchone()

    return row is not None


# =====================================================
# EXCEL EINLESEN
# =====================================================

def parse_excel(file_bytes):
    try:
        df = pd.read_excel(
            io.BytesIO(file_bytes),
            engine="openpyxl",
            dtype={
                "Karten-ID": str,
                "Kartennummer": str,
                "Sprache": str
            }
        )

    except Exception as error:
        raise ValueError(
            f"Excel-Datei konnte nicht gelesen werden: {error}"
        )

    missing = [
        col for col in REQUIRED_COLUMNS
        if col not in df.columns
    ]

    if missing:
        raise ValueError(
            "Diese Spalten fehlen: " + ", ".join(missing)
        )

    cards = []

    for index, row in df.iterrows():
        excel_row = index + 2

        card_id = clean(row["Karten-ID"])
        name = clean(row["Kartenname"])
        set_name = clean(row["Set"])
        number = clean(row["Kartennummer"])
        language = clean(row["Sprache"]).lower()
        link = clean(row["Cardmarket-Direktlink"])

        if not card_id or not name or not language:
            raise ValueError(
                f"Zeile {excel_row}: Karten-ID, Name oder Sprache fehlt."
            )

        try:
            quantity_value = row["Anzahl"]
            quantity = int(quantity_value)

            if (
                quantity < 1
                or float(quantity_value) != quantity
            ):
                raise ValueError()

        except (ValueError, TypeError):
            raise ValueError(
                f"Zeile {excel_row}: Ungültige Anzahl."
            )

        try:
            purchase_cents = to_cents(
                row["Cardmarket-Preis (€)"]
            )

        except ValueError:
            raise ValueError(
                f"Zeile {excel_row}: Bitte einen gültigen "
                "Einkaufspreis in 'Cardmarket-Preis (€)' eintragen."
            )

        cards.append({
            "card_key": card_key(card_id, language),
            "card_id": card_id,
            "name": name,
            "set_name": set_name,
            "card_number": number,
            "language": language,
            "cardmarket_url": link,
            "quantity": quantity,
            "purchase_cents": purchase_cents
        })

    if not cards:
        raise ValueError("Die Excel-Datei enthält keine Karten.")

    return cards


# =====================================================
# IMPORT SPEICHERN
# =====================================================

def import_cards(cards, file_hash, filename):
    with get_db() as conn:

        existing_import = conn.execute(
            "SELECT 1 FROM import_history WHERE file_hash=?",
            (file_hash,)
        ).fetchone()

        if existing_import:
            raise ValueError(
                "Diese Datei wurde bereits importiert."
            )

        for card in cards:

            existing = conn.execute("""
                SELECT *
                FROM inventory
                WHERE card_key=?
            """, (card["card_key"],)).fetchone()

            new_purchase_total = (
                card["purchase_cents"] * card["quantity"]
            )

            if existing:

                conn.execute("""
                    UPDATE inventory
                    SET
                        name=?,
                        set_name=?,
                        card_number=?,
                        cardmarket_url=?,
                        quantity=quantity+?,
                        purchase_total_cents=purchase_total_cents+?,
                        updated_at=?
                    WHERE id=?
                """, (
                    card["name"],
                    card["set_name"],
                    card["card_number"],
                    (
                        card["cardmarket_url"]
                        or existing["cardmarket_url"]
                    ),
                    card["quantity"],
                    new_purchase_total,
                    now(),
                    existing["id"]
                ))

            else:

                conn.execute("""
                    INSERT INTO inventory (
                        card_key,
                        card_id,
                        name,
                        set_name,
                        card_number,
                        language,
                        cardmarket_url,
                        quantity,
                        purchase_total_cents,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    card["card_key"],
                    card["card_id"],
                    card["name"],
                    card["set_name"],
                    card["card_number"],
                    card["language"],
                    card["cardmarket_url"],
                    card["quantity"],
                    new_purchase_total,
                    now(),
                    now()
                ))

        conn.execute("""
            INSERT INTO import_history (
                file_hash,
                filename,
                imported_at
            )
            VALUES (?, ?, ?)
        """, (
            file_hash,
            filename,
            now()
        ))


# =====================================================
# MEHRFACHVERKAUF
# =====================================================

def sell_cards(sale_items):
    """
    sale_items:
    [
        {
            "inventory_id": 1,
            "quantity": 2,
            "sale_unit_cents": 1200
        }
    ]
    """

    with get_db() as conn:

        for item in sale_items:

            card = conn.execute("""
                SELECT *
                FROM inventory
                WHERE id=?
            """, (
                item["inventory_id"],
            )).fetchone()

            if not card:
                raise ValueError("Karte nicht gefunden.")

            quantity = item["quantity"]

            if quantity < 1 or quantity > card["quantity"]:
                raise ValueError(
                    f"Ungültige Verkaufsmenge: {card['name']}"
                )

            old_quantity = card["quantity"]
            old_total = card["purchase_total_cents"]

            # Durchschnittlicher Einkaufspreis:
            # Bei Teilverkäufen wird der Einkauf proportional
            # auf die verkauften Exemplare verteilt.
            if quantity == old_quantity:
                purchase_total = old_total

            else:
                purchase_total = int(
                    (
                        Decimal(old_total)
                        * Decimal(quantity)
                        / Decimal(old_quantity)
                    ).quantize(
                        Decimal("1"),
                        rounding=ROUND_HALF_UP
                    )
                )

            sale_total = (
                item["sale_unit_cents"] * quantity
            )

            conn.execute("""
                INSERT INTO sales (
                    inventory_id,
                    card_id,
                    name,
                    language,
                    quantity,
                    purchase_total_cents,
                    sale_total_cents,
                    sold_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                card["id"],
                card["card_id"],
                card["name"],
                card["language"],
                quantity,
                purchase_total,
                sale_total,
                now()
            ))

            conn.execute("""
                UPDATE inventory
                SET
                    quantity=quantity-?,
                    purchase_total_cents=purchase_total_cents-?,
                    updated_at=?
                WHERE id=?
            """, (
                quantity,
                purchase_total,
                now(),
                card["id"]
            ))


# =====================================================
# EXCEL EXPORT
# =====================================================

def export_inventory(cards):
    rows = []

    for card in cards:

        if card["quantity"] <= 0:
            continue

        avg_purchase = round(
            card["purchase_total_cents"]
            / card["quantity"]
        )

        proposed_sale = selling_price(avg_purchase)

        rows.append({
            "Karten-ID": card["card_id"],
            "Kartenname": card["name"],
            "Set": card["set_name"],
            "Kartennummer": card["card_number"],
            "Sprache": card["language"],
            "Anzahl": card["quantity"],
            "Einkaufspreis (€)": avg_purchase / 100,
            "Verkaufspreis +20% (€)": proposed_sale / 100,
            "Cardmarket-Link": card["cardmarket_url"]
        })

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="xlsxwriter"
    ) as writer:

        pd.DataFrame(rows).to_excel(
            writer,
            index=False,
            sheet_name="Kartenbestand"
        )

        sheet = writer.sheets["Kartenbestand"]
        sheet.freeze_panes(1, 0)
        sheet.set_column("A:A", 20)
        sheet.set_column("B:C", 30)
        sheet.set_column("D:H", 20)
        sheet.set_column("I:I", 70)

    return output.getvalue()


# =====================================================
# APP START
# =====================================================

init_db()

st.title("🃏 Pokémon TCG Manager")
st.caption("Einkauf · Verkauf · Bestand · Gewinn")

tab_dashboard, tab_inventory, tab_sales, tab_import = st.tabs([
    "📊 Dashboard",
    "🃏 Kartenbestand",
    "💰 Verkäufe",
    "📥 Excel-Import"
])


# =====================================================
# DASHBOARD
# =====================================================

with tab_dashboard:

    st.header("📊 Dashboard")

    inventory = [
        c for c in get_inventory()
        if c["quantity"] > 0
    ]

    sales = get_sales()

    total_cards = sum(
        c["quantity"] for c in inventory
    )

    invested = sum(
        c["purchase_total_cents"]
        for c in inventory
    )

    proposed_sales = sum(
        selling_price(
            round(
                c["purchase_total_cents"]
                / c["quantity"]
            )
        ) * c["quantity"]
        for c in inventory
    )

    realized_revenue = sum(
        s["sale_total_cents"]
        for s in sales
    )

    realized_cost = sum(
        s["purchase_total_cents"]
        for s in sales
    )

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Karten im Bestand",
        total_cards
    )

    col2.metric(
        "Investiertes Kapital",
        money(invested)
    )

    col3.metric(
        "Geplanter Verkaufswert",
        money(proposed_sales)
    )

    col4.metric(
        "Möglicher Gewinn",
        money(proposed_sales - invested)
    )

    st.divider()

    st.subheader("💰 Bisherige Verkäufe")

    a, b, c = st.columns(3)

    a.metric(
        "Umsatz",
        money(realized_revenue)
    )

    b.metric(
        "Wareneinsatz verkauft",
        money(realized_cost)
    )

    c.metric(
        "Realisierter Gewinn",
        money(realized_revenue - realized_cost)
    )

    st.caption(
        "Alle Werte ohne Verkaufsgebühren, "
        "Versandkosten und Steuern."
    )


# =====================================================
# KARTENBESTAND
# =====================================================

with tab_inventory:

    st.header("🃏 Kartenbestand")

    cards = [
        c for c in get_inventory()
        if c["quantity"] > 0
    ]

    if not cards:
        st.info(
            "Noch keine Karten vorhanden. "
            "Lade zuerst eine Excel-Datei hoch."
        )

    else:

        search = st.text_input(
            "🔎 Karte suchen",
            placeholder="Name, Set oder Kartennummer"
        )

        filtered = []

        for card in cards:

            searchable = (
                card["name"]
                + " "
                + (card["set_name"] or "")
                + " "
                + (card["card_number"] or "")
            ).lower()

            if search.lower() in searchable:
                filtered.append(card)

        rows = []

        for card in filtered:

            avg_purchase = round(
                card["purchase_total_cents"]
                / card["quantity"]
            )

            proposed = selling_price(avg_purchase)

            rows.append({
                "ID": card["id"],
                "Karte": card["name"],
                "Set": card["set_name"],
                "Nummer": card["card_number"],
                "Sprache": card["language"],
                "Bestand": card["quantity"],
                "EK (€)": avg_purchase / 100,
                "VK +20% (€)": proposed / 100,
                "Cardmarket": card["cardmarket_url"]
            })

        if rows:

            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Cardmarket": st.column_config.LinkColumn(
                        "Cardmarket"
                    ),
                    "EK (€)": st.column_config.NumberColumn(
                        "EK (€)",
                        format="%.2f €"
                    ),
                    "VK +20% (€)": st.column_config.NumberColumn(
                        "VK +20% (€)",
                        format="%.2f €"
                    )
                }
            )

        st.divider()

        st.download_button(
            "📥 Gesamten Kartenbestand als Excel herunterladen",
            data=export_inventory(cards),
            file_name="Pokemon_Kartenbestand.xlsx",
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            )
        )


# =====================================================
# VERKÄUFE
# =====================================================

with tab_sales:

    st.header("💰 Karten verkaufen")

    available = [
        c for c in get_inventory()
        if c["quantity"] > 0
    ]

    if not available:
        st.info(
            "Aktuell sind keine Karten zum Verkauf verfügbar."
        )

    else:

        options = {
            (
                f"{c['name']} | "
                f"{c['card_number']} | "
                f"{c['language']} | "
                f"Bestand: {c['quantity']} "
                f"[ID {c['id']}]"
            ): c
            for c in available
        }

        selected = st.multiselect(
            "Karten auswählen",
            options=list(options.keys())
        )

        if selected:

            st.write(
                "Lege die verkauften Mengen und "
                "tatsächlichen Verkaufspreise fest."
            )

            sale_items = []

            with st.form("sales_form"):

                for label in selected:

                    card = options[label]

                    st.markdown(
                        f"**{card['name']} – "
                        f"{card['card_number']}**"
                    )

                    avg_purchase = round(
                        card["purchase_total_cents"]
                        / card["quantity"]
                    )

                    proposed = selling_price(avg_purchase)

                    col1, col2 = st.columns(2)

                    with col1:

                        qty = st.number_input(
                            "Verkaufte Menge",
                            min_value=1,
                            max_value=card["quantity"],
                            value=1,
                            step=1,
                            key=f"sale_qty_{card['id']}"
                        )

                    with col2:

                        sale_price = st.number_input(
                            "Tatsächlicher Verkaufspreis pro Karte (€)",
                            min_value=0.0,
                            value=proposed / 100,
                            step=0.50,
                            format="%.2f",
                            key=f"sale_price_{card['id']}"
                        )

                    sale_items.append({
                        "inventory_id": card["id"],
                        "quantity": qty,
                        "sale_unit_cents": to_cents(sale_price)
                    })

                    st.divider()

                submitted = st.form_submit_button(
                    "✅ Ausgewählte Karten als verkauft markieren",
                    type="primary",
                    use_container_width=True
                )

            if submitted:

                try:

                    sell_cards(sale_items)

                    st.success(
                        "Verkäufe gespeichert! "
                        "Der Kartenbestand wurde aktualisiert."
                    )

                    st.rerun()

                except ValueError as error:

                    st.error(str(error))

    st.divider()

    st.subheader("📜 Verkaufshistorie")

    sales = get_sales()

    if not sales:

        st.info(
            "Noch keine Verkäufe gespeichert."
        )

    else:

        history = []

        for sale in sales:

            history.append({
                "Datum": sale["sold_at"][:10],
                "Karte": sale["name"],
                "Sprache": sale["language"],
                "Menge": sale["quantity"],
                "Einkauf gesamt (€)":
                    sale["purchase_total_cents"] / 100,
                "Verkauf gesamt (€)":
                    sale["sale_total_cents"] / 100,
                "Gewinn (€)":
                    (
                        sale["sale_total_cents"]
                        - sale["purchase_total_cents"]
                    ) / 100
            })

        st.dataframe(
            pd.DataFrame(history),
            hide_index=True,
            use_container_width=True
        )


# =====================================================
# EXCEL IMPORT
# =====================================================

with tab_import:

    st.header("📥 Neue Karten importieren")

    st.write(
        "Lade die Excel-Datei aus deinem "
        "Cardmarket-Link-Finder-Projekt hoch."
    )

    st.info(
        "Die Spalte 'Cardmarket-Preis (€)' "
        "wird als Einkaufspreis verwendet. "
        "Der Verkaufspreis wird automatisch "
        "mit 20 % Aufschlag berechnet."
    )

    uploaded = st.file_uploader(
        "Excel-Datei auswählen",
        type=["xlsx"]
    )

    if uploaded is not None:

        file_bytes = uploaded.getvalue()

        file_hash = hashlib.sha256(
            file_bytes
        ).hexdigest()

        if file_already_imported(file_hash):

            st.warning(
                "Diese Datei wurde bereits importiert. "
                "Es werden keine doppelten Karten angelegt."
            )

        else:

            try:

                cards = parse_excel(file_bytes)

                st.success(
                    f"{len(cards)} Kartenpositionen erkannt."
                )

                preview = []

                for card in cards:

                    preview.append({
                        "Karte": card["name"],
                        "Sprache": card["language"],
                        "Anzahl": card["quantity"],
                        "Einkaufspreis (€)":
                            card["purchase_cents"] / 100,
                        "Verkaufspreis +20% (€)":
                            selling_price(
                                card["purchase_cents"]
                            ) / 100
                    })

                st.dataframe(
                    pd.DataFrame(preview),
                    hide_index=True,
                    use_container_width=True
                )

                if st.button(
                    "✅ Karten importieren",
                    type="primary",
                    use_container_width=True
                ):

                    import_cards(
                        cards,
                        file_hash,
                        uploaded.name
                    )

                    st.success(
                        "Alle Karten wurden erfolgreich importiert!"
                    )

                    st.rerun()

            except ValueError as error:

                st.error(str(error))


# =====================================================
# FOOTER
# =====================================================

st.divider()

st.caption(
    "Pokémon TCG Manager | "
    "Excel-Import | "
    "20 % Verkaufsaufschlag | "
    "Bestandsverwaltung | "
    "Mehrfachverkauf"
)
