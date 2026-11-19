import streamlit as st
import pandas as pd
import pydeck as pdk
from datetime import datetime
import requests
import os
import io
import math
import re
import unicodedata
from shapely.geometry import shape, mapping, MultiPoint, Point, LineString
from shapely import wkt
from shapely.ops import unary_union

st.set_page_config(layout="wide", page_title="G.I.R.A.R. CABA")

# Referencia pública fija: GCBA, Anexo 8, noviembre 2026.
# Precio al público del gasoil: ARS 2.252,03 por litro.
# Es una referencia histórica editable, NO una cotización en tiempo real.
def formato_ar(valor, decimales=2, signo=False):
    """Formato argentino: miles con punto y decimales con coma."""
    patron = f"{valor:+,.{decimales}f}" if signo else f"{valor:,.{decimales}f}"
    return patron.replace(",", "X").replace(".", ",").replace("X", ".")


def parsear_importe_ar(texto):
    """Admite 2.252,03, 2252,03 o 2252.03 (decimal simple)."""
    limpio = str(texto).strip().replace("$", "").replace("ARS", "").replace(" ", "")
    if not limpio:
        raise ValueError("El precio no puede quedar vacío")
    if "," in limpio:
        limpio = limpio.replace(".", "").replace(",", ".")
    elif limpio.count(".") > 1:
        limpio = limpio.replace(".", "")
    precio = float(limpio)
    if not math.isfinite(precio) or precio <= 0:
        raise ValueError("El precio debe ser positivo")
    return precio


PRECIO_GASOIL_REFERENCIA = 2252.03
FUENTE_GASOIL = ("GCBA · Anexo 8, costo del combustible y energía · noviembre 2026")
URL_FUENTE_GASOIL = "https://documentosboletinoficial.buenosaires.gob.ar/publico/PE-RES-MMIGC-SECT-181-26-ANX-2.pdf"


# Estaciones de transferencia para residuos húmedos. Las direcciones son
# referencias; el punto de acceso vial debe ser confirmado por el operador.
ESTACIONES_TRANSFERENCIA = {
    "Colegiales": "Crámer 290, Ciudad Autónoma de Buenos Aires, Argentina",
    "Flores": "Balbastro 3160, Ciudad Autónoma de Buenos Aires, Argentina",
    "Pompeya": "Zavaleta 858, Ciudad Autónoma de Buenos Aires, Argentina",
}

@st.cache_data(ttl=604800, show_spinner=False)
def geocodificar_estacion(nombre):
    """Nominatim devuelve una ubicación aproximada, no la entrada de camiones."""
    if nombre not in ESTACIONES_TRANSFERENCIA:
        return None
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": ESTACIONES_TRANSFERENCIA[nombre], "format": "json", "limit": 1},
            headers={"User-Agent": "GIRAR-Piloto/1.0 (proyecto académico)"}, timeout=12)
        r.raise_for_status()
        resultados = r.json()
        if resultados:
            lon, lat = float(resultados[0]["lon"]), float(resultados[0]["lat"])
            if -58.6 < lon < -58.3 and -34.75 < lat < -34.5:
                return [lon, lat]
    except (requests.RequestException, ValueError, KeyError, IndexError):
        pass
    return None

def capa_estaciones(datos):
    """Marcador verde REF: referencia aproximada dentro de la zona."""
    if not datos:
        return []
    return [
        pdk.Layer("ScatterplotLayer", data=datos,
                  get_position=["long", "lat"], get_fill_color=[15, 122, 76, 245],
                  get_line_color=[255, 255, 255, 255], get_radius=185,
                  radius_min_pixels=16, radius_max_pixels=24, stroked=True,
                  filled=True, pickable=True),
        pdk.Layer("TextLayer", data=datos, get_position=["long", "lat"],
                  get_text="REF", get_size=11, size_min_pixels=9,
                  get_color=[255, 255, 255, 255], get_text_anchor="middle",
                  get_alignment_baseline="center", pickable=True),
    ]

def metros_trazado(coordenadas):
    """Distancia aproximada sobre geometría WGS84 mediante haversine."""
    total = 0.0
    for (lon1,lat1),(lon2,lat2) in zip(coordenadas, coordenadas[1:]):
        a1,a2 = math.radians(lat1), math.radians(lat2)
        dlat,dlon = a2-a1,math.radians(lon2-lon1)
        h = math.sin(dlat/2)**2+math.cos(a1)*math.cos(a2)*math.sin(dlon/2)**2
        total += 12742000*math.asin(min(1,math.sqrt(h)))
    return total

def tramos_cobertura_simulada(coordenadas, focos, progreso):
    """Clasificación visual de la ruta PLANIFICADA, no de calles verificadas.
    Progreso manual simulado; no equivale a telemetría GPS.
    """
    if len(coordenadas)<2:
        return []
    puntos = focos[["long","lat"]].astype(float).values.tolist()
    longitudes = [metros_trazado([a,b]) for a,b in zip(coordenadas,coordenadas[1:])]
    total = sum(longitudes)
    recorrido = 0
    tramos = []
    for i,(a,b) in enumerate(zip(coordenadas,coordenadas[1:])):
        mitad = [(a[0]+b[0])/2,(a[1]+b[1])/2]
        cerca = any(metros_trazado([mitad,p]) <= 400 for p in puntos)
        # Gris = tránsito sin foco próximo; NO significa calle exenta de servicio.
        estado = "Atendida (simulada)" if recorrido+longitudes[i]/2 <= total*progreso/100 else (
            "Prioridad pendiente" if cerca else "Tránsito / sin prioridad identificada")
        color = ([22,150,96,240] if estado.startswith("Atendida") else
                 [244,157,35,240] if cerca else [135,145,156,180])
        tramos.append({"path":[a,b],"color":color,"estado":estado})
        recorrido += longitudes[i]
    return tramos



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


def puntos_cobertura_zona5(poligono, focos, cantidad=45):
    """Puntos sintéticos de paso para ampliar cobertura, NO tachos relevados.

    La grilla es una aproximación de cobertura espacial: OSRM ajusta cada
    waypoint a la red vial, pero no asegura cubrir todas las calles.
    """
    import math
    minx, miny, maxx, maxy = poligono.bounds
    lat_media = (miny + maxy) / 2
    # Grilla de aproximadamente 420 m, expresada en grados geográficos.
    paso_lat = 420 / 111_320
    paso_lon = 420 / (111_320 * max(0.2, math.cos(math.radians(lat_media))))
    existentes = focos[["long", "lat"]].astype(float).values.tolist()
    candidatos = []
    fila = 0
    y = miny + paso_lat / 2
    while y < maxy:
        x = minx + paso_lon * (0.5 if fila % 2 == 0 else 1.0)
        while x < maxx:
            if poligono.covers(Point(x, y)):
                candidatos.append([float(x), float(y)])
            x += paso_lon
        y += paso_lat
        fila += 1

    # Cobertura por máxima separación respecto de los puntos ya elegidos.
    # Así se evitan decenas de waypoints concentrados en una misma manzana.
    seleccionados = []
    anclas = existentes[:]
    def distancia_m2(a, b):
        return ((a[0]-b[0])*111_320*math.cos(math.radians(lat_media)))**2 + ((a[1]-b[1])*111_320)**2

    while candidatos and len(seleccionados) < cantidad:
        if anclas:
            elegido = max(candidatos, key=lambda c: min(distancia_m2(c, a) for a in anclas))
        else:
            elegido = candidatos[len(candidatos)//2]
        seleccionados.append(elegido)
        anclas.append(elegido)
        candidatos.remove(elegido)
    return seleccionados


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

def matriz_vial_osrm(puntos):
    """Distancias dirigidas (metros) sobre calles, no distancias en línea recta."""
    coords = ";".join(f"{p[0]:.7f},{p[1]:.7f}" for p in puntos)
    r = requests.get(f"https://router.project-osrm.org/table/v1/driving/{coords}",
                     params={"annotations": "distance"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "Ok" or not data.get("distances"):
        raise ValueError("OSRM no devolvió una matriz vial válida")
    return data["distances"]


def optimizar_circuito_vial(matriz, obligatorios, opcionales, presupuesto_extra=0.30):
    """Inserción de menor costo vial, 2-opt dirigido y presupuesto de desvíos.

    Los índices obligatorios incluyen el depósito (0) y focos. Se incluyen
    puntos opcionales solo si el costo marginal no excede el presupuesto.
    """
    import math
    def costo(ruta):
        valores = [matriz[a][b] for a,b in zip(ruta, ruta[1:])]
        return sum(valores) if all(v is not None for v in valores) else math.inf
    def insertar_mejor(ruta, nodo):
        opciones = [(costo(ruta[:k] + [nodo] + ruta[k:]), k)
                    for k in range(1, len(ruta))]
        return min(opciones)
    def mejorar(ruta):
        actual = costo(ruta)
        for _ in range(5):
            mejor = None
            for i in range(1, len(ruta)-2):
                for j in range(i+1, len(ruta)-1):
                    candidata = ruta[:i]+list(reversed(ruta[i:j+1]))+ruta[j+1:]
                    c = costo(candidata)
                    if c < actual-1 and (mejor is None or c < mejor[0]):
                        mejor=(c,candidata)
            if mejor is None: break
            actual,ruta=mejor
        return ruta
    ruta=[0,0]
    pendientes=list(obligatorios)
    while pendientes:
        alternativas=[(*insertar_mejor(ruta,n),n) for n in pendientes]
        valor,k,n=min(alternativas)
        if not math.isfinite(valor):
            raise ValueError("Los focos obligatorios no están conectados por la red vial")
        ruta.insert(k,n); pendientes.remove(n)
    ruta=mejorar(ruta)
    base=costo(ruta)
    limite=base*(1+presupuesto_extra)
    pendientes=list(opcionales)
    while pendientes:
        alternativas=[(*insertar_mejor(ruta,n),n) for n in pendientes]
        valor,k,n=min(alternativas)
        if not math.isfinite(valor) or valor>limite: break
        ruta.insert(k,n); pendientes.remove(n)
        ruta=mejorar(ruta)
    return ruta, base, costo(ruta)


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
    ["Centro de control · Mapa", "Simulador de eficiencia", "Soy conductor · Mi turno", "Soy barrendero · Reporte"]
)

# ---------------------------------------------------------
# Módulo 1: formulario móvil para cuadrillas
# ---------------------------------------------------------
if perfil_usuario == "Soy barrendero · Reporte":
    if os.path.exists("logo.png"):
        st.image("logo.png", width=150)
    else:
        st.title("G.I.R.A.R.")
        
    st.subheader("Reporte operativo de cuadrilla")
    st.caption("interfaz ligera de relevamiento en tiempo real.")

    st.markdown("""
    <style>
    div[data-testid="stSegmentedControl"] button {min-height: 48px; padding: 10px 16px;}
    div[data-testid="stCheckbox"] {padding: 5px 0;}
    </style>
    """, unsafe_allow_html=True)

    zona_operario = st.selectbox(
        "Zona de concesión asignada",
        ["Zona 1 (AESA)", "Zona 2 (Cliba)", "Zona 3 (Solbayres)",
         "Zona 4 (Nittida)", "Zona 5 (EHU)", "Zona 6 (Ashira)",
         "Zona 7 (Urbasur)"]
    )

    st.markdown("**Ubicación del reporte**")
    st.caption("Tu ubicación se adjuntará automáticamente al enviar el registro, "
               "si autorizás el acceso a la ubicación de tu dispositivo.")
    # La geolocalización requiere permiso del navegador y HTTPS (Streamlit Cloud).
    # Instalación: agregar streamlit-geolocation a requirements.txt.
    try:
        from streamlit_geolocation import streamlit_geolocation
        geolocalizacion = streamlit_geolocation()
    except ImportError:
        geolocalizacion = None
        st.error("Para habilitar la ubicación automática, agregá "
                 "`streamlit-geolocation` a requirements.txt.")
    except Exception:
        geolocalizacion = None
        st.info("No se pudo obtener la ubicación. Revisá los permisos del navegador.")

    latitud = geolocalizacion.get("latitude") if isinstance(geolocalizacion, dict) else None
    longitud = geolocalizacion.get("longitude") if isinstance(geolocalizacion, dict) else None
    ubicacion_lista = latitud is not None and longitud is not None
    if ubicacion_lista:
        st.caption("📍 Ubicación obtenida y lista para adjuntar.")
    else:
        st.caption("📍 Esperando permiso o señal de ubicación. "
                   "Activá la ubicación del dispositivo y autorizá al navegador.")

    st.markdown("**¿El contenedor está lleno o desbordado?**")
    contenedor_lleno = st.segmented_control(
        "Contenedor", ["Sí", "No"], default=None,
        selection_mode="single", label_visibility="collapsed",
        key="operario_contenedor"
    )

    st.markdown("**¿El sumidero está obstruido?**")
    sumidero_obstruido = st.segmented_control(
        "Sumidero", ["Sí", "No"], default=None,
        selection_mode="single", label_visibility="collapsed",
        key="operario_sumidero"
    )

    st.markdown("**¿Hay residuos fuera del contenedor?**")
    residuos_fuera = st.segmented_control(
        "Residuos fuera", ["Sí", "No"], default=None,
        selection_mode="single", label_visibility="collapsed",
        key="operario_residuos"
    )

    st.markdown("**Material excedente observado**")
    st.caption("Seleccioná todas las opciones que correspondan.")
    materiales = ["Orgánicos", "Papel / cartón", "Plásticos",
                  "Vidrio", "Escombros", "Otros"]
    tipo_material = []
    columnas = st.columns(2)
    for i, material in enumerate(materiales):
        with columnas[i % 2]:
            if st.checkbox(material, key=f"material_{i}"):
                tipo_material.append(material)

    observaciones = st.text_area(
        "Observaciones de campo (opcional)",
        placeholder="Agregá un detalle solamente si es necesario.",
        height=72
    )

    enviar_reporte = st.button(
        "Transmitir reporte a la central",
        type="primary", use_container_width=True
    )
    if enviar_reporte:
        if any(x is None for x in [contenedor_lleno, sumidero_obstruido, residuos_fuera]):
            st.error("Respondé las tres preguntas con Sí o No antes de enviar.")
        elif not ubicacion_lista:
            st.error("No se pudo adjuntar la ubicación. Activá la ubicación del "
                     "dispositivo, autorizá el acceso en el navegador e intentá nuevamente.")
        else:
            reporte = {
                "fecha_hora": datetime.now().isoformat(timespec="seconds"),
                "zona": zona_operario,
                "latitud": float(latitud),
                "longitud": float(longitud),
                "precision_m": geolocalizacion.get("accuracy"),
                "contenedor_lleno": contenedor_lleno,
                "sumidero_obstruido": sumidero_obstruido,
                "residuos_fuera": residuos_fuera,
                "materiales": ", ".join(tipo_material),
                "observaciones": observaciones.strip()
            }
            # La versión previa solo mostraba un mensaje, sin persistir el reporte.
            # Este registro queda disponible en la sesión hasta integrar un backend.
            st.session_state.setdefault("reportes_operario", []).append(reporte)
            st.success("Reporte registrado en esta sesión con ubicación adjunta.")
            st.caption("Pendiente: conectar un almacenamiento central persistente "
                       "para que despacho reciba los reportes entre dispositivos.")

# ---------------------------------------------------------
# Módulo 2: centro de control logístico y ruteo
# ---------------------------------------------------------
elif perfil_usuario == "Centro de control · Mapa":
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

        # Punto de referencia geográfico de la zona, obtenido del Excel original.
        # No se requiere geocodificación ni ingreso manual de coordenadas.
        datos_zona = df_disposicion[
            df_disposicion["zona"].astype(str).str.upper()
            == zona_seleccionada.split(" (")[0].upper()
        ] if not df_disposicion.empty else pd.DataFrame()
        punto_estacion = (
            [float(datos_zona.iloc[0]["long"]), float(datos_zona.iloc[0]["lat"])]
            if not datos_zona.empty else None
        )
        estacion_elegida = "Referencia de " + zona_seleccionada
        datos_et = ([{"long": punto_estacion[0], "lat": punto_estacion[1],
                      "nombre": "Punto de referencia de " + zona_seleccionada}]
                    if punto_estacion is not None else [])
        capas.extend(capa_estaciones(datos_et))
        st.caption("Marcador verde: punto de referencia aproximado de la zona. "
                   "No representa una estación de transferencia verificada.")

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

            cantidad_cobertura_z5 = 20
            presupuesto_desvio_z5 = 30
            if simular_ruta and zona_seleccionada == "Zona 5 (EHU)":
                cantidad_cobertura_z5 = st.sidebar.slider(
                    "6. Candidatos para cobertura de calles (Zona 5)",
                    min_value=10, max_value=35, value=20, step=5,
                    help="Son candidatos: el optimizador puede descartar los que causen desvíos costosos."
                )
                presupuesto_desvio_z5 = st.sidebar.slider(
                    "7. Máximo desvío respecto de la ruta entre focos (%)",
                    min_value=0, max_value=60, value=30, step=5,
                    help="Limita los kilómetros adicionales destinados a ampliar la cobertura."
                )

            if simular_ruta and not df.empty:
                if punto_estacion is None:
                    st.warning("No se encontró el punto de referencia de esta zona "
                               "en el dataset de zonas. Revisá el archivo Excel.")
                elif poligono_zona_trabajo is None or poligono_zona_trabajo.is_empty:
                    st.error("No se pudo construir el límite geográfico de la zona.")
                else:
                    # Punto de referencia dentro de la zona; los focos se filtran por polígono.
                    punto_base = punto_estacion
                    tolerancia_region = poligono_zona_trabajo.buffer(0.00035)
                    punto_zona = pd.DataFrame([{
                        "zona": estacion_elegida,
                        "empresa": "Punto de referencia aproximado",
                        "long": punto_base[0], "lat": punto_base[1]
                    }])
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
                        puntos_focos = focos[["long", "lat"]].astype(float).values.tolist()
                        puntos_cobertura = []
                        if zona_seleccionada == "Zona 5 (EHU)":
                            puntos_cobertura = puntos_cobertura_zona5(
                                poligono_zona_trabajo, focos,
                                cantidad=cantidad_cobertura_z5
                            )
                        # Matriz de distancias por calles. La selección opcional se
                        # limita por el costo incremental de circulación real.
                        puntos_matriz = [punto_base] + puntos_focos + puntos_cobertura
                        try:
                            matriz = matriz_vial_osrm(puntos_matriz)
                            indices, distancia_base, distancia_plan = optimizar_circuito_vial(
                                matriz,
                                obligatorios=list(range(1, 1 + len(puntos_focos))),
                                opcionales=list(range(1 + len(puntos_focos), len(puntos_matriz))),
                                presupuesto_extra=presupuesto_desvio_z5 / 100
                                if zona_seleccionada == "Zona 5 (EHU)" else 0,
                            )
                            ruta_waypoints = [puntos_matriz[i] for i in indices]
                            cobertura_incluida = sum(i > len(puntos_focos) for i in indices)
                        except (requests.RequestException, ValueError) as error:
                            st.error(f"No se pudo optimizar con distancias viales: {error}")
                            ruta_waypoints = []
                            cobertura_incluida = 0
                            distancia_base = distancia_plan = 0
                        coordenadas_str = ";".join(
                            f"{p[0]},{p[1]}" for p in ruta_waypoints
                        )
                        url_osrm = (
                            "https://router.project-osrm.org/route/v1/driving/"
                            f"{coordenadas_str}?geometries=geojson&overview=full&alternatives=false&steps=false"
                        )

                        try:
                            if not ruta_waypoints:
                                raise ValueError("No se generó un orden de visita válido")
                            respuesta = requests.get(url_osrm, timeout=30)
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
                                    st.session_state["girar_ultima_ruta"] = {
                                        "zona": str(punto_zona.iloc[0]["zona"]),
                                        "geometria": geometria_real,
                                        "focos": focos[["long", "lat"]].to_dict("records"),
                                        "distancia_m": float(candidata.get("distance", 0)),
                                        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
                                        "punto_referencia": estacion_elegida,
                                    }
                                    capas.append(pdk.Layer(
                                        "PathLayer",
                                        data=[{"path": geometria_real}],
                                        get_path="path",
                                        get_color=[0, 100, 255, 255],
                                        width_scale=20,
                                        width_min_pixels=4,
                                    ))
                                    st.success(
                                        f"Ruta por calles generada: {len(focos)} focos prioritarios"
                                        + (f" y {cobertura_incluida} puntos adicionales seleccionados por costo vial"
                                           if puntos_cobertura else "")
                                        + "; inicio y regreso a la estación de transferencia seleccionada."
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

                        if puntos_cobertura and ruta_waypoints:
                            st.caption(
                                "Zona 5: los puntos adicionales distribuyen el recorrido por más "
                                "sectores de la concesión, pero solo se incorporan si respetan "
                                "el presupuesto de distancia adicional. No son tachos relevados "
                                "ni garantizan recorrer todas las cuadras."
                            )

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

                        # Marcador de referencia aproximada sobre la ruta.
                        capas.extend(capa_estaciones(datos_et))

                        st.markdown(
                            f"**Punto de referencia:** {punto_zona.iloc[0]['zona']} — "
                            f"{punto_zona.iloc[0]['empresa']}\n"
                            f"**Criterio de selección:** {len(focos)} focos con mayor cantidad de registros "
                            "por celda geográfica, utilizando puntos reales del dataset."
                            + (f"  \n**Cobertura adicional:** {cobertura_incluida} de {len(puntos_cobertura)} "
                               "candidatos incorporados según su costo vial; "
                               f"distancia estimada: {distancia_plan/1000:.1f} km "
                               f"(base {distancia_base/1000:.1f} km)."
                               if puntos_cobertura else "")
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

elif perfil_usuario == "Simulador de eficiencia":
    st.title("Simulador de eficiencia logística")
    st.caption("Compará recorridos de igual cobertura y condiciones operativas. "
               "Los resultados son estimaciones, no ahorros comprobados.")
    ruta = st.session_state.get("girar_ultima_ruta")
    if not ruta:
        st.info("Primero generá una ruta desde Centro de control · Mapa. "
                "La ruta queda disponible en esta sesión.")
    else:
        st.write(f"**{ruta['zona']}** · Ruta generada el {ruta['fecha']}")
        km_girar = float(ruta["distancia_m"]) / 1000
        st.metric("Recorrido G.I.R.A.R. (calculado automáticamente)",
                  f"{formato_ar(km_girar)} km")
        st.caption("Esta distancia se obtiene directamente del último recorrido "
                   "calculado por OSRM en el mapa. Si cambiás la ruta, volvé a "
                   "generarla en el Centro de control para actualizar esta comparación.")
        km_base = st.number_input(
            "¿Cuántos kilómetros recorrería el camión sin G.I.R.A.R.?",
            min_value=0.1, value=float(round(max(0.1, km_girar), 2)),
            step=0.5, format="%.2f",
            help="Ingresá únicamente la distancia de un recorrido convencional "
                 "que cubra las mismas calles. El recorrido de G.I.R.A.R. se carga solo.")
        consumo = st.number_input("Consumo del camión (L/100 km)",
                                  min_value=1.0, value=35.0, step=1.0,
                                  help="Valor ilustrativo; reemplazar por el consumo real.")
        st.markdown("#### Precio del combustible")
        st.caption(
            f"Referencia de gasoil: **$ {formato_ar(PRECIO_GASOIL_REFERENCIA)} ARS/L** "
            f"({FUENTE_GASOIL}). Valor fijo, editable y no actualizado en tiempo real.")
        st.markdown(f"[Consultar fuente pública]({URL_FUENTE_GASOIL})")
        precio_texto = st.text_input(
            "Precio pagado por litro de gasoil ($ ARS/L)",
            value=formato_ar(PRECIO_GASOIL_REFERENCIA),
            help="Escribí, por ejemplo, 2.252,03. Se admite coma decimal. "
                 "Usá el precio efectivamente pagado si lo conocés.")
        try:
            precio = parsear_importe_ar(precio_texto)
        except ValueError:
            st.error("Ingresá un precio válido, por ejemplo: 2.252,03")
            st.stop()
        diferencia_km = km_base - km_girar
        litros = diferencia_km * consumo / 100
        pesos = litros * precio
        c1, c2, c3 = st.columns(3)
        c1.metric("Diferencia de distancia", f"{formato_ar(diferencia_km, signo=True)} km")
        c2.metric("Combustible estimado", f"{formato_ar(litros, signo=True)} L")
        c3.metric("Diferencia económica", f"$ {formato_ar(pesos, signo=True)} ARS")
        st.caption("Positivo = ahorro estimado; negativo = sobrecosto estimado. "
                   "La comparación presupone igual cobertura, vehículo, carga y "
                   "condiciones de circulación. No incluye ralentí ni detenciones.")
        comparacion = pd.DataFrame([
            {"Escenario": "Convencional", "Distancia (km)": km_base,
             "Combustible (L)": km_base * consumo / 100,
             "Costo (ARS)": km_base * consumo / 100 * precio},
            {"Escenario": "G.I.R.A.R.", "Distancia (km)": km_girar,
             "Combustible (L)": km_girar * consumo / 100,
             "Costo (ARS)": km_girar * consumo / 100 * precio}
        ])
        tabla_visible = comparacion.copy()
        tabla_visible["Distancia (km)"] = tabla_visible["Distancia (km)"].map(formato_ar)
        tabla_visible["Combustible (L)"] = tabla_visible["Combustible (L)"].map(formato_ar)
        tabla_visible["Costo (ARS)"] = tabla_visible["Costo (ARS)"].map(
            lambda v: f"$ {formato_ar(v)}")
        st.dataframe(tabla_visible, hide_index=True, use_container_width=True)
        st.download_button("Exportar comparación (CSV)",
                           comparacion.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "eficiencia_girar.csv", "text/csv")

elif perfil_usuario == "Soy conductor · Mi turno":
    st.title("Mi turno · Conductor")
    ruta = st.session_state.get("girar_ultima_ruta")
    if not ruta:
        st.info("Generá primero una ruta desde Centro de control · Mapa. "
                "Para compartirla entre dispositivos hace falta una base de datos central.")
    else:
        st.caption(f"{ruta['zona']} · {ruta['fecha']}")
        st.metric("Distancia planificada", f"{ruta['distancia_m']/1000:.1f} km")
        st.metric("Focos prioritarios", len(ruta["focos"]))
        avance = st.slider("Avance simulado (%)",0,100,0,5)
        segmentos = tramos_cobertura_simulada(
            ruta["geometria"],pd.DataFrame(ruta["focos"]),avance)
        centro = ruta["geometria"][len(ruta["geometria"])//2]
        st.pydeck_chart(pdk.Deck(map_provider="carto",map_style="light",
            initial_view_state=pdk.ViewState(latitude=centro[1],longitude=centro[0],zoom=12),
            layers=[pdk.Layer("PathLayer",data=segmentos,get_path="path",
                              get_color="color",width_min_pixels=5)]))
        st.caption("Verde: avance simulado · Naranja: pendiente cerca de focos · "
                   "Gris: tránsito sin foco cercano. No equivale a recolección GPS confirmada.")
