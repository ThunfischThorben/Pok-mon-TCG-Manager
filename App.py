import streamlit as st

st.set_page_config(
    page_title="Pokémon TCG Manager",
    page_icon="🃏",
    layout="wide"
)

st.title("🃏 Pokémon TCG Manager")
st.write("Dein persönlicher Pokémon-Ankaufsberater")

st.divider()

# DASHBOARD

st.header("📊 Dashboard")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Investiertes Kapital", "0,00 €")

with col2:
    st.metric("Warenwert", "0,00 €")

with col3:
    st.metric("Gewinn", "0,00 €")

st.divider()

# EINZELKARTEN-RECHNER

st.header("💰 Ankaufsrechner")

marktpreis = st.number_input(
    "Cardmarket-Preis (€)",
    min_value=0.0,
    value=10.0,
    step=0.5
)

prozentsatz = st.slider(
    "Ankaufspreis in %",
    min_value=0,
    max_value=100,
    value=80
)

ankaufspreis = marktpreis * prozentsatz / 100

st.success(
    f"Empfohlener Ankaufspreis: {ankaufspreis:.2f} €"
)

st.divider()

# SAMMLUNGSBEWERTUNG

st.header("📸 Sammlung bewerten")

sammlungsname = st.text_input(
    "Name des Verkäufers / der Sammlung",
    placeholder="z. B. Max Mustermann"
)

bilder = st.file_uploader(
    "Binder-Fotos oder Kartenbilder hochladen",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True
)

if bilder:
    st.success(
        f"{len(bilder)} Foto(s) hochgeladen"
    )

    for nummer, bild in enumerate(bilder, start=1):
        with st.expander(
            f"Foto {nummer} anzeigen"
        ):
            st.image(
                bild,
                use_container_width=True
            )

st.subheader("⚙️ Bewertungseinstellungen")

ankaufsquote = st.slider(
    "Ankaufsquote (%)",
    min_value=0,
    max_value=100,
    value=80,
    key="sammlungsquote"
)

st.caption(
    "Bewertung unter der Annahme, "
    "dass die Karten original sind."
)

# BUTTON ZUM STARTEN

if st.button(
    "🔍 Sammlung bewerten",
    type="primary",
    use_container_width=True
):
    if not sammlungsname.strip():
        st.warning(
            "Bitte zuerst einen Sammlungsnamen eingeben."
        )

    elif not bilder:
        st.warning(
            "Bitte zuerst mindestens ein Foto hochladen."
        )

    else:
        st.session_state["bewertung_aktiv"] = True
        st.session_state["bewertung_name"] = sammlungsname

# MANUELLE KARTENLISTE ALS ZWISCHENSCHRITT

if st.session_state.get("bewertung_aktiv", False):

    st.divider()

    st.header("📋 Bewertungsergebnis")

    st.write(
        "Sammlung:",
        st.session_state.get("bewertung_name", "")
    )

    st.info(
        "Die automatische Bilderkennung ist noch "
        "nicht angeschlossen. Trage vorerst die "
        "Karten und ihre Vergleichspreise ein."
    )

    anzahl = st.number_input(
        "Anzahl unterschiedlicher Kartenpositionen",
        min_value=1,
        max_value=100,
        value=9,
        step=1
    )

    gesamtwert = 0.0

    for i in range(int(anzahl)):

        st.subheader(f"Karte {i + 1}")

        name = st.text_input(
            "Kartenname",
            key=f"karte_name_{i}"
        )

        menge = st.number_input(
            "Menge",
            min_value=1,
            value=1,
            step=1,
            key=f"karte_menge_{i}"
        )

        preis = st.number_input(
            "Cardmarket-Vergleichspreis (€)",
            min_value=0.0,
            value=0.0,
            step=0.5,
            key=f"karte_preis_{i}"
        )

        gesamtwert += menge * preis

    ankauf = gesamtwert * ankaufsquote / 100

    st.divider()

    st.header("💶 Ankaufsergebnis")

    col1, col2 = st.columns(2)

    with col1:
        st.metric(
            "Geschätzter Marktwert",
            f"{gesamtwert:.2f} €"
        )

    with col2:
        st.metric(
            "Empfohlener Ankaufspreis",
            f"{ankauf:.2f} €"
        )

    st.caption(
        "Die eingegebenen Vergleichspreise "
        "sind keine automatisch abgerufenen "
        "Live-Preise."
    )
