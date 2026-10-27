import streamlit as st

st.set_page_config(
    page_title="Pokémon TCG Manager",
    page_icon="🃏",
    layout="wide"
)

st.title("🃏 Pokémon TCG Manager")
st.write("Willkommen in deinem Pokémon-Business-Dashboard!")

st.divider()

st.header("📊 Dashboard")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Investiertes Kapital", "0,00 €")

with col2:
    st.metric("Warenwert", "0,00 €")

with col3:
    st.metric("Gewinn", "0,00 €")

st.divider()

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
