import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="Pokémon TCG Manager", page_icon="🃏", layout="wide")
DB_PATH = Path("pokemon_manager.sqlite3")
BASE = "https://www.cardmarket.com/en/Pokemon"
LANGUAGES = {"en": 1, "eng": 1, "de": 3, "ger": 3, "fr": 2, "es": 4, "it": 5, "zh": 6, "ja": 7, "jp": 7, "jpn": 7, "pt": 8, "ko": 10}
LANGUAGE_NAMES = {"en": "Englisch", "de": "Deutsch", "fr": "Französisch", "es": "Spanisch", "it": "Italienisch", "ja": "Japanisch", "jp": "Japanisch", "pt": "Portugiesisch", "ko": "Koreanisch", "zh": "Chinesisch"}
# Eine vom Nutzer bestätigte Zuordnung. Weitere werden automatisch gesucht.
KNOWN_PRODUCTS = {"swsh12.5gg-GG44": "https://www.cardmarket.com/en/Pokemon/Products/Singles/Crown-Zenith/Mewtwo-VSTAR-CRZGG44"}


def now():
    return datetime.now(timezone.utc).isoformat()


def cents(value):
    try:
        number = Decimal(str(0 if value is None else value))
        if not number.is_finite() or number < 0:
            raise ValueError()
        return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (ValueError, TypeError, ArithmeticError):
        raise ValueError("Ungültiger Preis in der JSON-Datei.")


def money(value):
    return f"{value / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def percentage(value, pct):
    return int((Decimal(value) * Decimal(pct) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def card_language(card):
    return str(card["language"] or "").lower().strip()


def language_name(card):
    key = card_language(card)
    return LANGUAGE_NAMES.get(key, key or "Unbekannt")


def filters(card):
    values = {"sellerCountry": "7", "minCondition": "2"}
    code = LANGUAGES.get(card_language(card))
    if code is not None:
        values["language"] = str(code)
    return values


def valid_product_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.cardmarket.com", "cardmarket.com"):
        return None
    if not re.fullmatch(r"/(?:en|de|fr|es|it)/Pokemon/Products/Singles/[^/]+/[^/]+/?", parsed.path):
        return None
    return "https://www.cardmarket.com" + parsed.path.rstrip("/")


def search_url(card):
    number = str(card["number"] or "").split("/")[0].strip()
    query = " ".join(x for x in (card["name"], number, card["set_name"]) if x)
    params = {"searchString": query, **filters(card)}
    return BASE + "/Products/Search?" + urlencode(params)


def normalized_words(value):
    return set(re.findall(r"[a-z0-9]+", str(value).lower()))


@st.cache_data(ttl=86400, show_spinner=False)
def find_product(card_id, name, set_name, number):
    """Best-effort lookup. Cardmarket may block automated requests."""
    if card_id in KNOWN_PRODUCTS:
        return KNOWN_PRODUCTS[card_id]
    needle = str(number or "").split("/")[0].strip().lower()
    if not needle or not name:
        return None
    query = " ".join(x for x in (name, needle, set_name) if x)
    try:
        response = requests.get(
            BASE + "/Products/Search",
            params={"searchString": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; PokemonCollectionManager/1.0)"},
            timeout=6,
        )
        if response.status_code != 200:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        wanted_name = normalized_words(name)
        candidates = []
        for anchor in soup.select('a[href*="/Pokemon/Products/Singles/"]'):
            raw = urljoin(BASE, anchor.get("href", ""))
            url = valid_product_url(raw)
            if not url:
                continue
            slug = urlparse(url).path.rsplit("/", 1)[-1].lower()
            if not wanted_name.issubset(normalized_words(slug)):
                continue
            # A numeric collector number must be visible as a standalone number
            # or after an expansion prefix (e.g. CRZGG44).
            if not re.search(r"(?<!\d)" + re.escape(needle) + r"(?!\d)", slug):
                continue
            set_slug = urlparse(url).path.split("/")[-2]
            set_match = len(normalized_words(set_name) & normalized_words(set_slug))
            candidates.append((set_match, url))
        candidates = list(set(candidates))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        # Avoid choosing among ambiguous products.
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            return None
        return candidates[0][1]
    except requests.RequestException:
        return None


def product_link(card):
    url = find_product(str(card["card_id"] or ""), str(card["name"] or ""), str(card["set_name"] or ""), str(card["number"] or ""))
    if not url:
        return None
    return url + "?" + urlencode(filters(card))


def cardmarket_link(card):
    return product_link(card) or search_url(card)


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller TEXT NOT NULL, created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'offen',
            quote_percent INTEGER NOT NULL DEFAULT 80,
            purchase_cents INTEGER, purchased_at TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id INTEGER NOT NULL,
            card_id TEXT, name TEXT NOT NULL, set_name TEXT,
            number TEXT, language TEXT, quantity INTEGER NOT NULL,
            price_cents INTEGER NOT NULL, image TEXT, card_path TEXT,
            FOREIGN KEY(collection_id) REFERENCES collections(id) ON DELETE CASCADE
        )""")


def parse_json(file):
    try:
        data = json.load(file)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Keine gültige JSON-Datei.")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("Die Datei enthält keine Karten unter 'items'.")
    cards = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Ungültiger Eintrag {i}.")
        try:
            quantity = int(item.get("quantity", 1))
            if quantity < 1:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError(f"Ungültige Menge bei Karte {i}.")
        cards.append({
            "card_id": str(item.get("id") or ""),
            "name": str(item.get("name") or "Unbekannt"),
            "set_name": str(item.get("setName") or ""),
            "number": str(item.get("number") or ""),
            "language": str(item.get("language") or ""),
            "quantity": quantity,
            "price_cents": cents(item.get("addedEur")),
            "image": str(item.get("image") or ""),
            "card_path": str(item.get("path") or ""),
        })
    return cards


def save_collection(seller, pct, cards):
    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO collections (seller,created_at,status,quote_percent) VALUES (?,?,'offen',?)",
            (seller, now(), pct),
        )
        collection_id = cursor.lastrowid
        for card in cards:
            conn.execute("""INSERT INTO cards
                (collection_id,card_id,name,set_name,number,language,quantity,price_cents,image,card_path)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                collection_id, card["card_id"], card["name"], card["set_name"],
                card["number"], card["language"], card["quantity"],
                card["price_cents"], card["image"], card["card_path"],
            ))
    return collection_id


def get_collections():
    with db() as conn:
        return conn.execute("""SELECT c.*,
            COALESCE(SUM(k.quantity*k.price_cents),0) AS market_cents,
            COALESCE(SUM(k.quantity),0) AS card_count
            FROM collections c LEFT JOIN cards k ON k.collection_id=c.id
            GROUP BY c.id ORDER BY c.id DESC""").fetchall()


def get_cards(collection_id):
    with db() as conn:
        return conn.execute("SELECT * FROM cards WHERE collection_id=? ORDER BY id", (collection_id,)).fetchall()


def mark_purchased(collection_id, value):
    with db() as conn:
        conn.execute("""UPDATE collections SET status='gekauft',purchase_cents=?,purchased_at=?
            WHERE id=? AND status='offen'""", (value, now(), collection_id))


def undo_purchase(collection_id):
    with db() as conn:
        conn.execute("""UPDATE collections SET status='offen',purchase_cents=NULL,purchased_at=NULL
            WHERE id=? AND status='gekauft'""", (collection_id,))


def delete_collection(collection_id):
    with db() as conn:
        conn.execute("DELETE FROM collections WHERE id=? AND status='offen'", (collection_id,))


def create_excel(collection, cards):
    rows = []
    for card in cards:
        total = card["quantity"] * card["price_cents"]
        rows.append({
            "Verkäufer": collection["seller"],
            "Karten-ID": card["card_id"],
            "Kartenname": card["name"],
            "Set": card["set_name"],
            "Kartennummer": card["number"],
            "Sprache": language_name(card),
            "Zustand (Annahme)": "Near Mint",
            "Verkäuferland": "Deutschland",
            "Menge": card["quantity"],
            "Cardvalue Einzelpreis (€)": card["price_cents"] / 100,
            "Gesamtwert (€)": total / 100,
            "Ankaufsquote (%)": collection["quote_percent"],
            "Ankaufswert (€)": percentage(total, collection["quote_percent"]) / 100,
            "Bildlink": card["image"],
            "Cardmarket Link": cardmarket_link(card),
            "Direkte Produktseite": "Ja" if product_link(card) else "Nein – Suche",
        })
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Sammlung", index=False)
        sheet = writer.sheets["Sammlung"]
        sheet.freeze_panes(1, 0)
        sheet.set_column("A:B", 23)
        sheet.set_column("C:D", 30)
        sheet.set_column("E:M", 20)
        sheet.set_column("N:O", 65)
        sheet.set_column("P:P", 25)
    return output.getvalue()


init_db()
st.title("🃏 Pokémon TCG Manager")
st.write("Dein persönlicher Pokémon-Ankaufsmanager")
st.divider()

tab_dashboard, tab_storage, tab_import = st.tabs([
    "📊 Dashboard", "📦 Sammlungen", "➕ Neue Sammlung"
])

with tab_dashboard:
    st.header("📊 Dashboard")
    purchased = [c for c in get_collections() if c["status"] == "gekauft"]
    invested = sum(c["purchase_cents"] or 0 for c in purchased)
    market = sum(c["market_cents"] for c in purchased)
    c1, c2, c3 = st.columns(3)
    c1.metric("Investiertes Kapital", money(invested))
    c2.metric("Warenwert", money(market))
    c3.metric("Rechnerische Differenz", money(market - invested))
    st.caption("Nur gekaufte Sammlungen. Warenwerte basieren auf Cardvalue.ai-Importpreisen, nicht auf Live-Cardmarket-Preisen. Gebühren und Versand sind nicht enthalten.")
    st.divider()
    st.subheader("📦 Gekaufte Sammlungen")
    if not purchased:
        st.info("Noch keine Sammlung als gekauft markiert.")
    for collection in purchased:
        with st.expander(f'✅ {collection["seller"]} – {money(collection["market_cents"])}'):
            st.write("Karten:", collection["card_count"])
            st.write("Kaufpreis:", money(collection["purchase_cents"] or 0))
            st.write("Warenwert:", money(collection["market_cents"]))
            st.write("Rechnerische Differenz:", money(collection["market_cents"] - (collection["purchase_cents"] or 0)))

with tab_storage:
    st.header("📦 Gespeicherte Sammlungen")
    collections = get_collections()
    if not collections:
        st.info("Noch keine Sammlungen gespeichert.")
    for collection in collections:
        cid = collection["id"]
        market = collection["market_cents"]
        suggested = percentage(market, collection["quote_percent"])
        status = collection["status"]
        status_text = "✅ Gekauft" if status == "gekauft" else "🕒 Offen"
        with st.expander(f'{status_text} | {collection["seller"]} | {money(market)}'):
            st.write("**Verkäufer:**", collection["seller"])
            st.write("**Karten:**", collection["card_count"])
            st.write("**Sammlungswert:**", money(market))
            st.write("**Ankaufsquote:**", f'{collection["quote_percent"]} %')
            st.write("**Empfohlener Ankauf:**", money(suggested))
            st.divider()
            st.subheader("🃏 Karten")
            cards = get_cards(cid)
            for card in cards:
                left, right = st.columns([1, 4])
                with left:
                    if card["image"]:
                        try:
                            st.image(card["image"], width=120)
                        except Exception:
                            st.caption("Bild nicht verfügbar")
                with right:
                    st.markdown(f'**{card["name"]}**')
                    st.write(f'{card["set_name"]} | {card["number"]}')
                    st.write(f'Sprache: {language_name(card)} | Menge: {card["quantity"]}')
                    st.write("Einzelpreis:", money(card["price_cents"]))
                    direct = product_link(card)
                    if direct:
                        st.link_button("🔗 Cardmarket – passende Angebote", direct)
                        st.caption("Produktseite · Sprache aus JSON · Deutschland · mindestens Near Mint")
                    else:
                        st.link_button("🔎 Auf Cardmarket suchen", search_url(card))
                        st.caption("Produktseite nicht eindeutig ermittelbar. Cardmarket-Suche mit Filterparametern; deren Anwendung auf Suchergebnisse kann abweichen.")
                st.divider()
            excel = create_excel(collection, cards)
            st.download_button(
                "📥 Excel herunterladen", data=excel,
                file_name=f"sammlung_{cid}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"excel_{cid}",
            )
            st.divider()
            if status == "offen":
                purchase_eur = st.number_input(
                    "Tatsächlicher Kaufpreis (€)", min_value=0.0,
                    value=suggested / 100, step=1.0, key=f"purchase_{cid}",
                )
                if st.button("✅ Als gekauft markieren", key=f"buy_{cid}", type="primary"):
                    mark_purchased(cid, cents(purchase_eur))
                    st.rerun()
                with st.popover("🗑️ Sammlung löschen"):
                    st.warning("Diese Sammlung wird dauerhaft gelöscht.")
                    if st.button("Löschen bestätigen", key=f"delete_{cid}"):
                        delete_collection(cid)
                        st.rerun()
            else:
                st.success("Diese Sammlung ist gekauft und im Dashboard enthalten.")
                st.write("Tatsächlicher Kaufpreis:", money(collection["purchase_cents"] or 0))
                if st.button("↩️ Kauf rückgängig machen", key=f"undo_{cid}"):
                    undo_purchase(cid)
                    st.rerun()

with tab_import:
    st.header("➕ Neue Sammlung anlegen")
    st.write("Lade eine Cardvalue.ai-JSON-Datei hoch. Kartensprache, Kartennummer, Set, Bilder und Preise werden automatisch übernommen.")
    with st.form("import_form"):
        seller = st.text_input("Name des Verkäufers / der Sammlung")
        uploaded = st.file_uploader("Cardvalue.ai JSON-Datei", type=["json"])
        quote_percent = st.slider("Ankaufsquote (%)", 0, 100, 80)
        submitted = st.form_submit_button("📦 Sammlung speichern", type="primary", use_container_width=True)
    if submitted:
        if not seller.strip():
            st.warning("Bitte einen Verkäufernamen eingeben.")
        elif uploaded is None:
            st.warning("Bitte eine JSON-Datei hochladen.")
        else:
            try:
                cards = parse_json(uploaded)
                cid = save_collection(seller.strip(), quote_percent, cards)
                total = sum(card["quantity"] * card["price_cents"] for card in cards)
                st.success(f"Sammlung #{cid} gespeichert!")
                st.write("Karten:", sum(card["quantity"] for card in cards))
                st.write("Sammlungswert:", money(total))
                st.info("Die Sammlung liegt unter 'Sammlungen' und erscheint erst nach dem Kauf im Dashboard.")
            except ValueError as error:
                st.error(str(error))

st.divider()
st.caption("Pokémon TCG Manager · Cardvalue.ai · Sammlungen · Cardmarket · Excel")
