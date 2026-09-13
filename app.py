import streamlit as st
import pandas as pd
import pydeck as pdk
from datetime import datetime
import requests
import os
import unicodedata
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

st.set_page_config(layout="wide", page_title="G.I.R.A.R. CABA")

def normalizar(texto):
    if not isinstance(texto, str): return ""
    return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('utf-8').strip().upper()

@st.cache_data
def cargar_geojson_caba():
    url = "https://cdn.buenosaires.gob.ar/datosabiertos/datasets/barrios/barrios.geojson"
    try:
        r = requests.get(url, timeout=5)
        return r.json()
    except:
        return None

if os.path.exists("logo.png"):
    st.sidebar.image("logo.png", use_container_width=True)
else:
    st.sidebar.title("Plataforma G.I.R.A.R.")

perfil_usuario = st.sidebar.radio(
    "Seleccionar rol operativo",
    ["centro de control (despacho)", "soy barrendero (reporte de campo)"]
)

# ---------------------------------------------------------
# Módulo 1: formulario móvil para cuadrillas
# ---------------------------------------------------------
if perfil_usuario == "soy barrendero (reporte de campo)":
    if os.path.exists("logo.png"):
        st.image("logo.png", width=150)
    else:
        st.title("G.I.R.A.R.")
        
    st.subheader("Reporte operativo de cuadrilla")
    st.caption("interfaz ligera de relevamiento en tiempo real.")

    with st.form("formulario_operario", clear_on_submit=True):
        st.write("Datos de la intervención")
        
        zona_operario = st.selectbox(
            "Zona de concesión asignada",
            [
                "Zona 1 (AESA)", "Zona 2 (Cliba)", "Zona 3 (Solbayres)",
                "Zona 4 (Nittida)", "Zona 5 (EHU)", "Zona 6 (Ashira)", "Zona 7 (Urbasur)"
            ]
        )
        
        ubicacion = st.text_input(
            "Ubicación aproximada",
            placeholder="ej. Av. Santa Fe y Callao"
        )
        
        estado_contenedor = st.selectbox(
            "Nivel de llenado del contenedor",
            [
                "normal (capacidad disponible)",
                "carga crítica (más del 80%)",
                "desbordado (residuos dispersos en vereda)"
            ]
        )
        
        estado_sumidero = st.radio(
            "Estado del sumidero o desagüe pluvial",
            [
                "despejado",
                "obstrucción parcial por hojas o restos",
                "bloqueo crítico (riesgo inmediato de anegamiento)"
            ]
        )
        
        tipo_material = st.multiselect(
            "Material excedente observado",
            ["orgánico / húmedo", "cartón y papel", "plástico", "escombros / voluminosos"]
        )
        
        observaciones = st.text_area(
            "Observaciones de campo",
            placeholder="ej. contenedor vandalizado o acceso bloqueado por vehículos mal estacionados."
        )
        
        enviar_reporte = st.form_submit_button("Transmitir reporte a la central")
        
        if enviar_reporte:
            hora_actual = datetime.now().strftime("%H:%M")
            st.success(f"reporte enviado con éxito a la central a las {hora_actual}.")
            st.info("alerta incorporada al cálculo dinámico de prioridades logísticas.")

# ---------------------------------------------------------
# Módulo 2: centro de control logístico y ruteo
# ---------------------------------------------------------
else:
    st.subheader("Sistema de gestión dinámica de residuos")
    st.sidebar.markdown("---")
    st.sidebar.write("Parámetros de ruteo")

    tipo_recolector = st.sidebar.radio(
        "1. Tipo de recolección",
        ["húmedos (gastronómicos)", "secos (reciclables)"]
    )

    @st.cache_data
    def cargar_datos(tipo):
        archivo = "oferta_gastronomica_raw.xlsx" if tipo == "húmedos (gastronómicos)" else "recolectores_secos.xlsx"
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
        st.error("⚠️ no se pudieron procesar las coordenadas del archivo seleccionado.")
    else:
        # ZONAS OFICIALES ACTUALIZADAS
        zonas_caba = {
            "Zona 1 (AESA)": ["Retiro", "San Nicolás", "Monserrat", "Puerto Madero", "San Telmo", "Constitución"],
            "Zona 2 (Cliba)": ["Recoleta", "Palermo", "Belgrano", "Colegiales", "Núñez"],
            "Zona 3 (Solbayres)": ["Villa Crespo", "Chacarita", "Paternal", "Villa Ortúzar", "Parque Chas", "Agronomía", "Saavedra", "Coghlan", "Villa Urquiza", "Villa Pueyrredón", "Villa Devoto", "Villa del Parque", "Villa Santa Rita", "Villa General Mitre"],
            "Zona 4 (Nittida)": ["Parque Avellaneda", "Mataderos", "Liniers", "Villa Luro", "Versalles", "Vélez Sársfield", "Floresta", "Monte Castro", "Villa Real"],
            "Zona 5 (EHU)": ["Villa Lugano", "Villa Riachuelo", "Villa Soldati"],
            "Zona 6 (Ashira)": ["Flores", "Parque Chacabuco", "Caballito", "Boedo", "Almagro"],
            "Zona 7 (Urbasur)": ["Balvanera", "San Cristóbal", "Nueva Pompeya", "Parque Patricios", "Barracas", "La Boca"]
        }

        lista_zonas = ["Todas las Zonas"] + list(zonas_caba.keys())
        zona_seleccionada = st.sidebar.selectbox("2. Filtrar por zona de concesión", lista_zonas)

        if 'barrio' in df.columns:
            if zona_seleccionada == "Todas las Zonas":
                barrios_disponibles = ["Todos los Barrios"] + sorted(list(df['barrio'].dropna().unique()))
            else:
                barrios_zona = zonas_caba[zona_seleccionada]
                barrios_existentes = [b for b in df['barrio'].dropna().unique() if b in barrios_zona]
                barrios_disponibles = ["Todos los Barrios de la Zona"] + sorted(barrios_existentes)
                df = df[df['barrio'].isin(barrios_zona)]

            barrio_seleccionado = st.sidebar.selectbox("3. Filtrar por barrio específico", barrios_disponibles)
            
            if barrio_seleccionado not in ["Todos los Barrios", "Todos los Barrios de la Zona"]:
                df = df[df['barrio'] == barrio_seleccionado]

        # ---------------------------------------------------------
        # FUSIÓN DE POLÍGONOS (Evita las rayas internas)
        # ---------------------------------------------------------
        if zona_seleccionada != "Todas las Zonas":
            geojson_data = cargar_geojson_caba()
            if geojson_data:
                barrios_zona_norm = [normalizar(b) for b in barrios_zona]
                poligonos_a_fusionar = []
                
                for feature in geojson_data.get('features', []):
                    nombre_barrio = feature['properties'].get('BARRIO', feature['properties'].get('barrio', ''))
                    nombre_norm = normalizar(nombre_barrio)
                    
                    if nombre_norm in barrios_zona_norm or (nombre_norm == 'MONTSERRAT' and 'MONSERRAT' in barrios_zona_norm):
                        poligono = shape(feature['geometry'])
                        # "Inflamos" milimétricamente cada barrio para tapar los huecos del mapa del GCBA
                        poligono = poligono.buffer(0.0001)
                        poligonos_a_fusionar.append(poligono)
                
                if poligonos_a_fusionar:
                    # Derretimos todos los barrios en una sola mancha gigante
                    zona_fusionada = unary_union(poligonos_a_fusionar)
                    # "Desinflamos" a su tamaño original
                    zona_fusionada = zona_fusionada.buffer(-0.0001)
                    
                    geojson_filtrado = {
                        "type": "FeatureCollection", 
                        "features": [{
                            "type": "Feature",
                            "geometry": mapping(zona_fusionada),
                            "properties": {"zona": zona_seleccionada}
                        }]
                    }
                    
                    capa_bordes = pdk.Layer(
                        "GeoJsonLayer",
                        data=geojson_filtrado,
                        opacity=0.15,
                        stroked=True,
                        filled=True,
                        extruded=False,
                        wireframe=True,
                        get_fill_color=[0, 150, 255, 10],  # Relleno apenas perceptible
                        get_line_color=[0, 200, 255, 255], # Borde exterior de neón 100% nítido
                        get_line_width=60,
                    )
                    capas.append(capa_bordes)

        st.sidebar.markdown("---")
        escenario = st.sidebar.radio(
            "4. Simulación predictiva",
            ["operación normal", "alerta de tormenta"]
        )

        if escenario == "operación normal":
            st.write(f"Densidad de demanda ({tipo_recolector})")
            radio_impacto = 80
            intensidad = 1
            color_rango = [[255, 255, 178], [254, 204, 92], [253, 141, 60], [240, 59, 32], [189, 0, 38]]
        else:
            st.write("Intervención de emergencia: riesgo de anegamiento")
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

        if zona_seleccionada != "Todas las Zonas":
            st.sidebar.markdown("---")
            simular_ruta = st.sidebar.checkbox("5. Generar ruta óptima por asfalto (IA)")
            
            if simular_ruta and not df.empty:
                muestra = df.sample(min(15, len(df)), random_state=42)
                puntos = muestra[['long', 'lat']].values.tolist()
                
                ruta_optima = [puntos.pop(0)]
                while puntos:
                    ultimo = ruta_optima[-1]
                    siguiente = min(puntos, key=lambda p: (p[0]-ultimo[0])**2 + (p[1]-ultimo[1])**2)
                    ruta_optima.append(siguiente)
                    puntos.remove(siguiente)
                
                coordenadas_str = ";".join([f"{p[0]},{p[1]}" for p in ruta_optima])
                url_osrm = f"http://router.project-osrm.org/route/v1/driving/{coordenadas_str}?geometries=geojson&overview=full"
                
                try:
                    respuesta = requests.get(url_osrm, timeout=5)
                    datos_ruta = respuesta.json()
                    
                    if datos_ruta.get('code') == 'Ok':
                        geometria_real = datos_ruta['routes'][0]['geometry']['coordinates']
                        camino_final = [{"path": geometria_real}]
                    else:
                        camino_final = [{"path": ruta_optima}]
                except:
                    camino_final = [{"path": ruta_optima}]
                
                capa_ruta = pdk.Layer(
                    "PathLayer",
                    data=camino_final,
                    get_path="path",
                    get_color=[0, 100, 255, 255],
                    width_scale=20,
                    width_min_pixels=4,
                )
                capas.append(capa_ruta)
                
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
