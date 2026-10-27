import streamlit as st

st.set_page_config(
    page_title="Pokémon TCG Manager",
    page_icon="🃏",
    layout="wide"
)

st.title("🃏 Pokémon TCG Manager")
st.write("Willkommen in deinem Pokémon-Business-Dashboard!")

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

# ANKAUFSRECHNER

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

# SAMMLUNGSANKAUF MIT FOTOS

st.header("📸 Sammlung bewerten")

sammlungsname = st.text_input(
    "Name der Sammlung",
    placeholder="z. B. Pokémon-Sammlung Max"
)

bilder = st.file_uploader(
    "Lade Fotos deiner Pokémon-Karten oder Binder-Seiten hoch",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True
)

if bilder:
    st.success(f"{len(bilder)} Foto(s) hochgeladen!")

    for nummer, bild in enumerate(bilder, start=1):
        st.subheader(f"Foto {nummer}")
        st.image(bild, use_container_width=True)

st.divider()

st.subheader("💶 Ankaufspreis für die Sammlung")

gesamtwert = st.number_input(
    "Geschätzter Cardmarket-Gesamtwert (€)",
    min_value=0.0,
    value=0.0,
    step=1.0
)

sammlungs_prozent = st.slider(
    "Ankaufsquote für die Sammlung (%)",
    min_value=0,
    max_value=100,
    value=80
)

sammlungs_ankauf = gesamtwert * sammlungs_prozent / 100

st.success(
    f"Empfohlener Ankaufspreis für die Sammlung: "
    f"{sammlungs_ankauf:.2f} €"
)

st.info(
    "Die automatische KI-Kartenerkennung wird "
    "im nächsten Schritt ergänzt."
)
