import streamlit as st
import pandas as pd
import pydeck as pdk
from datetime import datetime
import requests
import os
import re
import unicodedata
from shapely.geometry import shape, mapping, MultiPoint, Point, LineString
from shapely import wkt
from shapely.ops import unary_union

st.set_page_config(layout="wide", page_title="G.I.R.A.R. CABA")

def normalizar(texto):
    if not isinstance(texto, str): return ""
    return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('utf-8').strip().upper()


def puntos_calientes_representativos(datos, poligono, max_puntos=12, tamano_celda=0.0025):
    """Selecciona puntos reales de las celdas con mayor densidad, dentro del área."""
    if datos.empty:
        return pd.DataFrame()

    candidatos = datos.copy()
    candidatos["lat"] = pd.to_numeric(candidatos["lat"], errors="coerce")
    candidatos["long"] = pd.to_numeric(candidatos["long"], errors="coerce")
    candidatos = candidatos.dropna(subset=["lat", "long"])
    candidatos = candidatos[
        candidatos.apply(lambda r: poligono.buffer(0.00035).covers(Point(r["long"], r["lat"])), axis=1)
    ].copy()

    if candidatos.empty:
        return candidatos

    # Celdas de aproximadamente 250-280 m: se priorizan las que concentran más registros.
    candidatos["celda_lat"] = (candidatos["lat"] / tamano_celda).round().astype(int)
    candidatos["celda_lon"] = (candidatos["long"] / tamano_celda).round().astype(int)
    conteos = (candidatos.groupby(["celda_lat", "celda_lon"])
               .size().rename("intensidad").reset_index()
               .sort_values("intensidad", ascending=False))

    seleccionados = []
    for _, celda in conteos.head(max_puntos).iterrows():
        grupo = candidatos[
            (candidatos["celda_lat"] == celda["celda_lat"]) &
            (candidatos["celda_lon"] == celda["celda_lon"])
        ]
        centro_lat = grupo["lat"].mean()
        centro_lon = grupo["long"].mean()
        idx = ((grupo["lat"] - centro_lat) ** 2 + (grupo["long"] - centro_lon) ** 2).idxmin()
        punto = candidatos.loc[idx].copy()
        punto["intensidad"] = int(celda["intensidad"])
        seleccionados.append(punto)

    return pd.DataFrame(seleccionados).drop(columns=["celda_lat", "celda_lon"], errors="ignore")


def ordenar_puntos_2opt(punto_inicio, puntos):
    """Orden inicial por vecino cercano y mejora el orden mediante 2-opt."""
    pendientes = [list(map(float, p)) for p in puntos]
    ruta = [list(map(float, punto_inicio))]
    while pendientes:
        ultimo = ruta[-1]
        siguiente = min(pendientes, key=lambda p: (p[0]-ultimo[0])**2 + (p[1]-ultimo[1])**2)
        ruta.append(siguiente)
        pendientes.remove(siguiente)

    if len(ruta) > 3:
        mejoro = True
        while mejoro:
            mejoro = False
            for i in range(1, len(ruta)-2):
                for j in range(i+1, len(ruta)-1):
                    a, b = ruta[i-1], ruta[i]
                    c, d = ruta[j], ruta[j+1]
                    actual = ((a[0]-b[0])**2 + (a[1]-b[1])**2) ** 0.5 + ((c[0]-d[0])**2 + (c[1]-d[1])**2) ** 0.5
                    nuevo = ((a[0]-c[0])**2 + (a[1]-c[1])**2) ** 0.5 + ((b[0]-d[0])**2 + (b[1]-d[1])**2) ** 0.5
                    if nuevo + 1e-12 < actual:
                        ruta[i:j+1] = reversed(ruta[i:j+1])
                        mejoro = True
    return ruta

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


    @st.cache_data
    def cargar_puntos_disposicion():
        """Obtiene un punto representativo dentro de cada polígono de concesión."""
        archivo_zonas = "zonas-de-recoleccion-de-residuos-humedos_clean.xlsx"

        def corregir_wkt_sin_comas(geometria):
            # El archivo recibido tiene pares X/Y separados por espacios,
            # sin comas entre pares de coordenadas.
            def corregir_anillo(match):
                valores = re.findall(
                    r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", match.group(1)
                )
                if len(valores) % 2:
                    raise ValueError("Cantidad impar de coordenadas en WKT")
                pares = [
                    f"{valores[i]} {valores[i + 1]}"
                    for i in range(0, len(valores), 2)
                ]
                return "(" + ", ".join(pares) + ")"

            return re.sub(r"\(([^()]*)\)", corregir_anillo, geometria)

        try:
            zonas = pd.read_excel(archivo_zonas)
            requeridas = {"wkt_geom", "zona", "empresa"}
            if not requeridas.issubset(zonas.columns):
                st.error("El Excel debe contener las columnas wkt_geom, zona y empresa.")
                return pd.DataFrame()

            registros = []
            for _, fila in zonas.iterrows():
                try:
                    geometria_txt = str(fila["wkt_geom"])
                    try:
                        try:
                            poligono = wkt.loads(geometria_txt)
                        except Exception:
                            poligono = wkt.loads(corregir_wkt_sin_comas(geometria_txt))

                        if not poligono.is_valid:
                            poligono = poligono.buffer(0)

                        punto = poligono.representative_point()
                        metodo_punto = "interior del polígono"
                    except Exception:
                        # Algunas celdas del Excel están truncadas y no cierran
                        # el WKT. En esos casos se obtiene un centro aproximado
                        # a partir de los pares de coordenadas que sí existen.
                        valores = re.findall(
                            r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?",
                            geometria_txt
                        )
                        # Si la celda terminó en un decimal incompleto (p. ej. -34.)
                        # se descarta ese último valor antes de formar pares X/Y.
                        if geometria_txt.rstrip().endswith(".") and valores:
                            valores = valores[:-1]
                        if len(valores) % 2:
                            valores = valores[:-1]
                        if len(valores) < 4:
                            raise ValueError("WKT incompleto y sin pares X/Y suficientes")
                        coordenadas = [
                            (float(valores[i]), float(valores[i + 1]))
                            for i in range(0, len(valores), 2)
                        ]
                        punto = MultiPoint(coordenadas).convex_hull.representative_point()
                        metodo_punto = "aproximado (geometría truncada)"

                    registros.append({
                        "zona": str(fila["zona"]).strip().upper(),
                        "empresa": str(fila["empresa"]).strip(),
                        "lat": float(punto.y),
                        "long": float(punto.x),
                        "metodo_punto": metodo_punto
                    })
                except Exception as error:
                    st.warning(f"No se pudo procesar {fila.get('zona', 'una zona')}: {error}")

            return pd.DataFrame(registros)
        except Exception as error:
            st.error(
                f"No se pudo cargar {archivo_zonas}: {error}. "
                "Verificá que esté en GitHub junto al archivo principal."
            )
            return pd.DataFrame()

    df = cargar_datos(tipo_recolector)
    df_disposicion = cargar_puntos_disposicion()
    capas = []

    if df.empty:
        st.error("⚠️ no se pudieron procesar las coordenadas del archivo seleccionado.")
    else:
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
        # Puntos de referencia de disposición por zona
        # ---------------------------------------------------------
        if not df_disposicion.empty:
            if zona_seleccionada == "Todas las Zonas":
                puntos_mapa = df_disposicion.copy()
            else:
                numero_seleccionado = zona_seleccionada.split("(")[0].strip().upper()
                puntos_mapa = df_disposicion[
                    df_disposicion["zona"] == numero_seleccionado
                ].copy()

            if not puntos_mapa.empty:
                capa_disposicion = pdk.Layer(
                    "ScatterplotLayer",
                    data=puntos_mapa,
                    get_position=["long", "lat"],
                    get_fill_color=[35, 165, 90, 245],
                    get_line_color=[255, 255, 255, 255],
                    get_radius=150,
                    radius_min_pixels=8,
                    radius_max_pixels=18,
                    stroked=True,
                    filled=True,
                    pickable=True,
                )
                capas.append(capa_disposicion)
                st.markdown(
                    "<div style='display:flex;align-items:center;gap:9px;margin:4px 0 10px 0;'>"
                    "<span style='display:inline-block;width:11px;height:11px;border-radius:50%;background:#23A55A;border:1px solid white;'></span>"
                    "<span style='font-size:0.88rem;'>Punto de referencia de disposición</span>"
                    "</div>",
                    unsafe_allow_html=True
                )

        # Geometría de la región operativa (se reutiliza para filtrar y validar el recorrido).
        poligono_zona_trabajo = None

        # Fusión de polígonos
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
                        poligono = poligono.buffer(0.0001)
                        poligonos_a_fusionar.append(poligono)
                
                if poligonos_a_fusionar:
                    zona_fusionada = unary_union(poligonos_a_fusionar).buffer(-0.0001)
                    poligono_zona_trabajo = zona_fusionada
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
                        get_fill_color=[0, 150, 255, 10],
                        get_line_color=[0, 200, 255, 255], 
                        get_line_width=60,
                    )
                    capas.append(capa_bordes)

        st.sidebar.markdown("---")
        escenario = st.sidebar.radio(
            "4. Simulación predictiva",
            ["operación normal", "alerta de tormenta", "eventos masivos (exclusión)"]
        )

        if escenario == "operación normal":
            st.write(f"Densidad de demanda ({tipo_recolector})")
            capa_calor = pdk.Layer(
                "HeatmapLayer", data=df, get_position=["long", "lat"], opacity=0.8,
                get_weight=1, radiusPixels=80, intensity=1,
                colorRange=[[255, 255, 178], [254, 204, 92], [253, 141, 60], [240, 59, 32], [189, 0, 38]]
            )
            capas.append(capa_calor)
            
        elif escenario == "alerta de tormenta":
            st.write("Intervención de emergencia: riesgo de anegamiento")
            capa_calor = pdk.Layer(
                "HeatmapLayer", data=df, get_position=["long", "lat"], opacity=0.8,
                get_weight=1, radiusPixels=250, intensity=4,
                colorRange=[[254, 229, 217], [252, 174, 145], [251, 106, 74], [222, 45, 38], [165, 15, 21]]
            )
            capas.append(capa_calor)
            
        else:
            st.write("Anillo de exclusión logística y redirección de cuadrillas")
            # Mapa de calor difuminado de fondo
            capa_calor = pdk.Layer(
                "HeatmapLayer", data=df, get_position=["long", "lat"], opacity=0.25,
                get_weight=1, radiusPixels=80, intensity=1,
                colorRange=[[255, 255, 178], [254, 204, 92], [253, 141, 60], [240, 59, 32], [189, 0, 38]]
            )
            capas.append(capa_calor)

            coordenadas_eventos = {
                "Estadio River Plate (80.000 aforo)": {"lat": -34.5453, "long": -58.4497},
                "Movistar Arena (15.000 aforo)": {"lat": -34.5944, "long": -58.4458},
                "Estadio Vélez Sarsfield (49.000 aforo)": {"lat": -34.6354, "long": -58.5207},
                "Obelisco (Concentración pública)": {"lat": -34.6037, "long": -58.3816},
                "Parque de la Ciudad (Festivales)": {"lat": -34.6738, "long": -58.4494}
            }
            
            evento_seleccionado = st.selectbox(
                "Seleccionar epicentro autorizado (Datos GCBA 2026):",
                list(coordenadas_eventos.keys())
            )
            
            coord = coordenadas_eventos[evento_seleccionado]
            df_evento = pd.DataFrame([coord])
            
            # Anillo rojo de 800 metros
            capa_exclusion = pdk.Layer(
                "ScatterplotLayer",
                data=df_evento,
                get_position=["long", "lat"],
                get_fill_color=[255, 0, 0, 80], 
                get_line_color=[255, 0, 0, 255],
                get_radius=800,
                stroked=True,
                filled=True,
                line_width_min_pixels=3,
            )
            capas.append(capa_exclusion)
            
            # Punto central negro
            capa_centro = pdk.Layer(
                "ScatterplotLayer",
                data=df_evento,
                get_position=["long", "lat"],
                get_fill_color=[0, 0, 0, 255],
                get_radius=40,
            )
            capas.append(capa_centro)

        # ---------------------------------------------------------
        # Ruteo por asfalto: inicio y final en disposición + focos de mayor demanda.
        # ---------------------------------------------------------
        if zona_seleccionada != "Todas las Zonas":
            st.sidebar.markdown("---")
            simular_ruta = st.sidebar.checkbox(
                "5. Generar ruta optimizada por asfalto"
            )

            if simular_ruta and not df.empty:
                numero_seleccionado = zona_seleccionada.split("(")[0].strip().upper()
                punto_zona = (
                    df_disposicion[df_disposicion["zona"] == numero_seleccionado]
                    if not df_disposicion.empty else pd.DataFrame()
                )

                if punto_zona.empty:
                    st.error("No se encontró el punto de referencia para la zona seleccionada.")
                elif poligono_zona_trabajo is None or poligono_zona_trabajo.is_empty:
                    st.error("No se pudo construir el límite geográfico de la zona. No se generó una ruta sin restricción territorial.")
                else:
                    punto_base = [
                        float(punto_zona.iloc[0]["long"]),
                        float(punto_zona.iloc[0]["lat"])
                    ]

                    # El punto de disposición debe estar dentro de la región operativa.
                    tolerancia_region = poligono_zona_trabajo.buffer(0.00035)
                    if not tolerancia_region.covers(Point(punto_base[0], punto_base[1])):
                        st.warning(
                            "El punto de referencia obtenido del Excel no coincide con el polígono "
                            "operativo de barrios. Se utilizará un punto interior de la zona "
                            "como base de simulación; no representa una instalación real."
                        )
                        punto_interior = poligono_zona_trabajo.representative_point()
                        punto_base = [float(punto_interior.x), float(punto_interior.y)]
                        punto_zona = punto_zona.copy()
                        punto_zona.loc[:, "long"] = punto_base[0]
                        punto_zona.loc[:, "lat"] = punto_base[1]
                    # Solo se consideran registros dentro del polígono real de la zona.
                    df_ruteo = df[
                        df.apply(
                            lambda r: tolerancia_region.covers(Point(float(r["long"]), float(r["lat"]))),
                            axis=1
                        )
                    ].copy()

                    # Se eligen focos densos y representativos, no una muestra aleatoria.
                    focos = puntos_calientes_representativos(
                        df_ruteo, poligono_zona_trabajo, max_puntos=12
                    )

                    if focos.empty:
                        st.warning("No se encontraron puntos de recolección dentro del polígono de la zona.")
                    else:
                        orden = ordenar_puntos_2opt(
                            punto_base,
                            focos[["long", "lat"]].astype(float).values.tolist()
                        )
                        # Cierre del circuito: vuelve al punto de disposición.
                        ruta_waypoints = [punto_base] + orden[1:] + [punto_base]
                        coordenadas_str = ";".join(
                            f"{p[0]},{p[1]}" for p in ruta_waypoints
                        )
                        url_osrm = (
                            "https://router.project-osrm.org/route/v1/driving/"
                            f"{coordenadas_str}?geometries=geojson&overview=full&alternatives=false&steps=false"
                        )

                        try:
                            respuesta = requests.get(url_osrm, timeout=25)
                            respuesta.raise_for_status()
                            datos_ruta = respuesta.json()
                            rutas_candidatas = (
                                datos_ruta.get("routes", [])
                                if datos_ruta.get("code") == "Ok" else []
                            )
                            # OSRM calcula por calles, pero no acepta polígonos
                            # de exclusión. No ocultamos la ruta si el eje vial
                            # cruza el límite: medimos y comunicamos el desvío.
                            if rutas_candidatas:
                                candidata = min(
                                    rutas_candidatas,
                                    key=lambda r: r.get("duration", float("inf"))
                                )
                                geometria_real = candidata.get("geometry", {}).get("coordinates", [])
                                if len(geometria_real) >= 2:
                                    linea = LineString(geometria_real)
                                    limite_validacion = poligono_zona_trabajo.buffer(0.0004)
                                    exterior = linea.difference(limite_validacion)
                                    porcentaje_exterior = (
                                        100 * exterior.length / linea.length
                                        if linea.length else 0.0
                                    )
                                    capas.append(pdk.Layer(
                                        "PathLayer",
                                        data=[{"path": geometria_real}],
                                        get_path="path",
                                        get_color=[0, 100, 255, 255],
                                        width_scale=20,
                                        width_min_pixels=4,
                                    ))
                                    st.success(
                                        f"Ruta por calles generada: {len(focos)} focos de demanda; "
                                        "inicio y regreso al punto verde de referencia."
                                    )
                                    if porcentaje_exterior > 1:
                                        st.warning(
                                            f"El {porcentaje_exterior:.1f}% de la longitud del trazado "
                                            "se encuentra fuera del perímetro cartográfico "
                                            "(con tolerancia aproximada de 40 m). "
                                            "OSRM no permite prohibir calles fuera de la zona: "
                                            "esta ruta es una simulación, no una ruta territorialmente garantizada."
                                        )
                                else:
                                    st.warning("OSRM no devolvió una geometría vial utilizable.")
                            else:
                                st.warning(
                                    "OSRM no pudo resolver el circuito completo. "
                                    "No se dibujó una línea recta que pudiera atravesar manzanas."
                                )
                        except Exception as error:
                            st.warning(f"No se pudo validar el recorrido con OSRM: {error}. No se dibujó una ruta aproximada fuera de la red vial.")

                        # Los focos elegidos se destacan en blanco con borde azul; el verde queda reservado
                        # para el punto de disposición.
                        capas.append(pdk.Layer(
                            "ScatterplotLayer", data=focos,
                            get_position=["long", "lat"],
                            get_fill_color=[255, 255, 255, 255],
                            get_line_color=[0, 80, 180, 255],
                            get_radius=75, radius_min_pixels=5,
                            stroked=True, filled=True, pickable=True,
                        ))

                        # El marcador verde se vuelve a dibujar al final para que quede por encima.
                        capas.append(pdk.Layer(
                            "ScatterplotLayer", data=punto_zona,
                            get_position=["long", "lat"],
                            get_fill_color=[35, 165, 90, 255],
                            get_line_color=[255, 255, 255, 255],
                            get_radius=220, radius_min_pixels=12,
                            radius_max_pixels=24, stroked=True,
                            filled=True, pickable=True,
                        ))

                        st.markdown(
                            f"**Punto de disposición:** {punto_zona.iloc[0]['zona']} — "
                            f"{punto_zona.iloc[0]['empresa']}\n"
                            f"**Criterio de selección:** {len(focos)} focos con mayor cantidad de registros "
                            "por celda geográfica, utilizando puntos reales del dataset."
                        )

        # Lógica de cámara inteligente (Zoom)
        # ---------------------------------------------------------
        if escenario == "eventos masivos (exclusión)":
            lat_centro = coord["lat"]
            lon_centro = coord["long"]
            zoom_nivel = 14
        elif poligono_zona_trabajo is not None and not poligono_zona_trabajo.is_empty:
            centroide = poligono_zona_trabajo.representative_point()
            lat_centro = centroide.y
            lon_centro = centroide.x
            zoom_nivel = 12.8
        elif not df.empty:
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
