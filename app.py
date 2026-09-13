import streamlit as st
import pandas as pd
import pydeck as pdk

st.set_page_config(layout="wide", page_title="SGDR CABA")
st.title("Sistema de gestión dinámica de residuos")

st.sidebar.header("Panel de control logístico B2B")

tipo_recolector = st.sidebar.radio(
    "1. Tipo de recolección:",
    ["Húmedos (Gastronómicos)", "Secos (Reciclables)"]
)

@st.cache_data
def cargar_datos(tipo):
    archivo = "oferta_gastronomica_raw.xlsx" if tipo == "Húmedos (Gastronómicos)" else "recolectores_secos.xlsx"
    try:
        df = pd.read_excel(archivo)
        if 'lat' in df.columns and 'long' in df.columns:
            df = df.dropna(subset=['lat', 'long'])
            df['lat'] = pd.to_numeric(df['lat'], errors='coerce')
            df['long'] = pd.to_numeric(df['long'], errors='coerce')
            df = df.dropna(subset=['lat', 'long'])
            df = df[(df['lat'] > -34.7) & (df['lat'] < -34.5)]
            df = df[(df['long'] > -58.55) & (df['long'] < -58.35)]
        return df
    except Exception:
        return pd.DataFrame()

df = cargar_datos(tipo_recolector)

capas = [] 

if df.empty:
    st.error(f"⚠️ No se pudo cargar la base de datos para {tipo_recolector}.")
else:
    zonas_caba = {
        "Zona 1 (Cliba)": ["Retiro", "San Nicolás", "Puerto Madero", "San Telmo", "Montserrat", "Constitución"],
        "Zona 2 (AESA)": ["Recoleta", "Palermo", "Belgrano", "Colegiales", "Núñez"],
        "Zona 3 (Urbaser)": ["Balvanera", "San Cristóbal", "La Boca", "Barracas", "Parque Patricios", "Nueva Pompeya"],
        "Zona 4 (Ashira)": ["Almagro", "Boedo", "Caballito", "Parque Chacabuco"],
        "Zona 5 (Nittida)": ["Villa Real", "Monte Castro", "Versalles", "Floresta", "Vélez Sársfield", "Villa Luro", "Liniers", "Mataderos", "Parque Avellaneda"],
        "Zona 6 (EHU)": ["Villa Soldati", "Villa Lugano", "Villa Riachuelo"],
        "Zona 7 (Solbayres)": ["Agronomía", "Chacarita", "Parque Chas", "Paternal", "Villa Crespo", "Villa del Parque", "Villa Devoto", "Villa Gral. Mitre", "Villa Ortúzar", "Villa Pueyrredón", "Villa Santa Rita", "Villa Urquiza"]
    }

    lista_zonas = ["Todas las Zonas"] + list(zonas_caba.keys())
    zona_seleccionada = st.sidebar.selectbox("2. Filtrar por zona de concesión:", lista_zonas)

    if 'barrio' in df.columns:
        if zona_seleccionada == "Todas las Zonas":
            barrios_disponibles = ["Todos los Barrios"] + sorted(list(df['barrio'].dropna().unique()))
        else:
            barrios_zona = zonas_caba[zona_seleccionada]
            barrios_existentes = [b for b in df['barrio'].dropna().unique() if b in barrios_zona]
            barrios_disponibles = ["Todos los Barrios de la Zona"] + sorted(barrios_existentes)
            df = df[df['barrio'].isin(barrios_zona)]

        barrio_seleccionado = st.sidebar.selectbox("3. Filtrar por barrio específico:", barrios_disponibles)
        
        if barrio_seleccionado not in ["Todos los Barrios", "Todos los Barrios de la Zona"]:
            df = df[df['barrio'] == barrio_seleccionado]

    st.sidebar.markdown("---")
    escenario = st.sidebar.radio(
        "4. Simulación predictiva:",
        ["Operación Normal", "Alerta de Tormenta"]
    )

    if escenario == "Operación Normal":
        st.subheader(f"Mapa de calor operativo: {tipo_recolector}")
        radio_impacto = 80
        intensidad = 1
        color_rango = [[255, 255, 178], [254, 204, 92], [253, 141, 60], [240, 59, 32], [189, 0, 38]]
    else:
        st.subheader("Intervención de emergencia: riesgo de anegamiento")
        radio_impacto = 250
        intensidad = 4
        color_rango = [[254, 229, 217], [252, 174, 145], [251, 106, 74], [222, 45, 38], [165, 15, 21]]

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
    capas.append(capa_calor)

    # 5. Simulador de ruta dinámica (aparece solo al seleccionar una Zona específica)
    if zona_seleccionada != "Todas las Zonas":
        st.sidebar.markdown("---")
        simular_ruta = st.sidebar.checkbox("5. Generar ruta óptima (IA)")
        
        if simular_ruta and not df.empty:
            # Selecciona 15 puntos clave en las áreas más rojas de la zona
            muestra = df.sample(min(15, len(df)), random_state=42)
            puntos = muestra[['long', 'lat']].values.tolist()
            
            # Algoritmo de ruteo
            ruta_optima = [puntos.pop(0)]
            while puntos:
                ultimo = ruta_optima[-1]
                siguiente = min(puntos, key=lambda p: (p[0]-ultimo[0])**2 + (p[1]-ultimo[1])**2)
                ruta_optima.append(siguiente)
                puntos.remove(siguiente)
            
            # Dibuja la línea de la ruta en azul eléctrico
            capa_ruta = pdk.Layer(
                "PathLayer",
                data=[{"path": ruta_optima}],
                get_path="path",
                get_color=[0, 100, 255, 255], 
                width_scale=20,
                width_min_pixels=4,
            )
            capas.append(capa_ruta)
            
            # Dibuja los puntos blancos donde frenaría el camión
            capa_puntos = pdk.Layer(
                "ScatterplotLayer",
                data=muestra,
                get_position=["long", "lat"],
                get_color=[255, 255, 255, 255],
                get_radius=80,
                radius_min_pixels=3,
            )
            capas.append(capa_puntos)

    if not df.empty:
        lat_centro = df['lat'].mean()
        lon_centro = df['long'].mean()
        zoom_nivel = 13.5 if ('barrio_seleccionado' in locals() and barrio_seleccionado not in ["Todos los Barrios", "Todos los Barrios de la Zona"]) else 12.5
    else:
        lat_centro, lon_centro = -34.6037, -58.3816
        zoom_nivel = 11.5

    vista_inicial = pdk.ViewState(
        latitude=lat_centro,
        longitude=lon_centro,
        zoom=zoom_nivel,
        pitch=45,
    )

    st.pydeck_chart(pdk.Deck(
        map_provider="carto",
        map_style="light",
        initial_view_state=vista_inicial,
        layers=capas,
    ))
