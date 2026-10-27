import io
import json
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="Pokémon TCG Manager",
    page_icon="🃏",
    layout="wide"
)


def euro(value):
    return (
        f"{value:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
        + " €"
    )


def money(value):
    return Decimal(str(value or 0))


def round_eur(value):
    return value.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )


def read_collection(uploaded):
    try:
        payload = json.load(uploaded)
    except (ValueError, UnicodeDecodeError):
        raise ValueError(
            "Die Datei enthält kein gültiges JSON."
        )

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("items"), list)
    ):
        raise ValueError(
            "Das JSON benötigt ein Feld 'items' "
            "mit einer Kartenliste."
        )

    result = []

    for index, card in enumerate(
        payload["items"], start=1
    ):
        if not isinstance(card, dict):
            raise ValueError(
                f"Eintrag {index} ist keine Karte."
            )

        try:
            quantity = int(
                card.get("quantity", 1)
            )

            if quantity < 1:
                raise ValueError()

            price = money(
                card.get("addedEur")
            )

            if price < 0:
                raise ValueError()

        except (
            ValueError,
            TypeError,
            ArithmeticError
        ):
            raise ValueError(
                f"Ungültiger Preis oder Menge "
                f"bei Eintrag {index}."
            )

        result.append({
            "ID": str(card.get("id") or ""),
            "Karte": str(
                card.get("name") or "Unbekannt"
            ),
            "Set": str(
                card.get("setName") or ""
            ),
            "Nummer": str(
                card.get("number") or ""
            ),
            "Sprache": str(
                card.get("language") or ""
            ),
            "Menge": quantity,
            "Preis": price,
            "Bild": str(
                card.get("image") or ""
            ),
        })

    return result


# TITEL

st.title("🃏 Pokémon TCG Manager")

st.write(
    "Dein persönlicher Pokémon-Ankaufsberater"
)

st.divider()


# JSON-UPLOAD

st.header("📂 Sammlung importieren")

seller = st.text_input(
    "Verkäufer / Sammlungsname",
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


if uploaded is None:
    st.info(
        "Lade deinen Cardvalue.ai-Export "
        "als JSON hoch. Alle Karten werden "
        "automatisch eingelesen."
    )
    st.stop()


# JSON EINLESEN

try:
    cards = read_collection(uploaded)

except ValueError as exc:
    st.error(str(exc))
    st.stop()


if not cards:
    st.warning(
        "Die JSON-Datei enthält keine Karten."
    )
    st.stop()


# WERTE BERECHNEN

ratio = Decimal(quote_percent) / Decimal(100)

total_value = sum(
    (
        card["Preis"] * card["Menge"]
        for card in cards
    ),
    Decimal("0")
)

total_offer = round_eur(
    total_value * ratio
)

total_qty = sum(
    card["Menge"]
    for card in cards
)


st.success(
    f"{len(cards)} Kartenpositionen "
    "automatisch eingelesen."
)


# DASHBOARD

st.header("📊 Dashboard")

a, b, c = st.columns(3)

with a:
    st.metric(
        "Karten insgesamt",
        str(total_qty)
    )

with b:
    st.metric(
        "Sammlungswert (Cardvalue-Export)",
        euro(total_value)
    )

with c:
    st.metric(
        f"Ankaufspreis ({quote_percent} %)",
        euro(total_offer)
    )


st.caption(
    "Die JSON-Werte sind gespeicherte "
    "Cardvalue-Exportpreise, keine live "
    "abgefragten Cardmarket-Preise."
)

st.divider()


# KARTENÜBERSICHT

st.header("🃏 Kartenübersicht")

for card in cards:

    left, right = st.columns([1, 4])

    with left:

        if card["Bild"]:
            st.image(
                card["Bild"],
                width=150
            )

    with right:

        st.subheader(
            card["Karte"]
        )

        st.write(
            f'{card["Set"]} · '
            f'{card["Nummer"]} · '
            f'Sprache: {card["Sprache"]} · '
            f'Menge: {card["Menge"]}'
        )

        line_value = (
            card["Preis"] * card["Menge"]
        )

        line_offer = round_eur(
            line_value * ratio
        )

        st.write(
            f'Exportpreis je Karte: '
            f'**{euro(card["Preis"])}**'
        )

        st.write(
            f'Gesamtwert: '
            f'**{euro(line_value)}** · '
            f'Ankauf ({quote_percent} %): '
            f'**{euro(line_offer)}**'
        )

        if card["ID"]:

            query = quote(
                f'{card["Karte"]} '
                f'{card["Nummer"]} '
                f'{card["Set"]}'
            )

            cardmarket_url = (
                "https://www.cardmarket.com/"
                "en/Pokemon/Products/Search"
                f"?searchString={query}"
            )

            st.link_button(
                "Auf Cardmarket suchen",
                cardmarket_url
            )

    st.divider()


# EXCEL-EXPORT

st.header("📥 Excel-Export")

rows = []

for card in cards:

    value = (
        card["Preis"] * card["Menge"]
    )

    rows.append({
        "Verkäufer": seller.strip(),
        "Karten-ID": card["ID"],
        "Kartenname": card["Karte"],
        "Set": card["Set"],
        "Kartennummer": card["Nummer"],
        "Sprache": card["Sprache"],
        "Menge": card["Menge"],
        "Cardvalue-Preis je Karte (€)": float(
            card["Preis"]
        ),
        "Gesamtwert (€)": float(value),
        f"Ankauf {quote_percent} % (€)": float(
            round_eur(value * ratio)
        ),
        "Bildlink": card["Bild"],
    })


df = pd.DataFrame(rows)

buffer = io.BytesIO()

with pd.ExcelWriter(
    buffer,
    engine="xlsxwriter"
) as writer:

    df.to_excel(
        writer,
        sheet_name="Sammlung",
        index=False
    )

    sheet = writer.sheets["Sammlung"]

    sheet.freeze_panes(1, 0)

    sheet.set_column(0, 0, 22)
    sheet.set_column(1, 1, 22)
    sheet.set_column(2, 3, 27)
    sheet.set_column(4, 6, 15)
    sheet.set_column(7, 9, 24)
    sheet.set_column(10, 10, 75)


st.download_button(
    "📥 Sammlung als Excel herunterladen",
    data=buffer.getvalue(),
    file_name="pokemon_sammlung.xlsx",
    mime=(
        "application/vnd.openxmlformats-"
        "officedocument.spreadsheetml.sheet"
    ),
    use_container_width=True
)
