import streamlit as st
import pandas as pd
import pydeck as pdk

st.set_page_config(layout="wide", page_title="SGDR CABA")
st.title("Sistema de Gestión Dinámica de Residuos")

st.sidebar.header("Panel de Control Logístico B2B")

# 1. BOTÓN PARA SUBIR EXCEL DINÁMICAMENTE
archivo_subido = st.sidebar.file_uploader("1. Subir base de datos de la empresa (Excel)", type=["xlsx"])

@st.cache_data
def procesar_datos(archivo):
    df = pd.read_excel(archivo)
    # Limpieza de coordenadas
    if 'lat' in df.columns and 'long' in df.columns:
        df = df.dropna(subset=['lat', 'long'])
        df['lat'] = pd.to_numeric(df['lat'], errors='coerce')
        df['long'] = pd.to_numeric(df['long'], errors='coerce')
        df = df.dropna(subset=['lat', 'long'])
        df = df[(df['lat'] > -34.7) & (df['lat'] < -34.5)]
        df = df[(df['long'] > -58.55) & (df['long'] < -58.35)]
    return df

# Cargar el archivo subido o usar el base por defecto
if archivo_subido is not None:
    df = procesar_datos(archivo_subido)
else:
    df = procesar_datos("oferta_gastronomica_raw.xlsx")

# 2. FILTRO DESPLEGABLE POR ZONA (Simula el cliente B2B)
if 'barrio' in df.columns:
    lista_zonas = ["Toda la Ciudad"] + sorted(list(df['barrio'].dropna().unique()))
    zona_seleccionada = st.sidebar.selectbox("2. Seleccionar Zona de Operación (Barrio):", lista_zonas)
    
    if zona_seleccionada != "Toda la Ciudad":
        df = df[df['barrio'] == zona_seleccionada]

st.sidebar.markdown("---")

# 3. SELECTOR DE ESCENARIO
escenario = st.sidebar.radio(
    "3. Simulación Predictiva:",
    ["Operación Normal (Densidad)", "Alerta de Tormenta"]
)

if escenario == "Operación Normal (Densidad)":
    st.subheader("Mapa de calor: Riesgo de desborde nocturno")
    radio_impacto = 80
    intensidad = 1
    color_rango = [[255, 255, 178], [254, 204, 92], [253, 141, 60], [240, 59, 32], [189, 0, 38]]
else:
    st.subheader("Intervención de emergencia: Riesgo de sumideros")
    radio_impacto = 250
    intensidad = 4
    color_rango = [[254, 229, 217], [252, 174, 145], [251, 106, 74], [222, 45, 38], [165, 15, 21]]

# Renderizado del mapa
capa_calor = pdk.Layer(
    "HeatmapLayer",
    data=df,
    get_position=["long", "lat"],
    opacity=0.8,
    get_weight=1,
    radiusPixels=radio_impacto,
    intensity=intensidad,
    colorRange=color_rango
)

# Centrar la cámara dinámicamente según los datos
if not df.empty:
    lat_centro = df['lat'].mean()
    lon_centro = df['long'].mean()
else:
    lat_centro, lon_centro = -34.6037, -58.3816

vista_inicial = pdk.ViewState(
    latitude=lat_centro,
    longitude=lon_centro,
    zoom=12.5 if 'zona_seleccionada' in locals() and zona_seleccionada != "Toda la Ciudad" else 11.5,
    pitch=45,
)

st.pydeck_chart(pdk.Deck(
    map_provider="carto",
    map_style="light",
    initial_view_state=vista_inicial,
    layers=[capa_calor],
))