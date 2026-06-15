import os
import streamlit as st
import pandas as pd
import openai
import requests
import time
import base64
import re
from io import BytesIO
from urllib.parse import urlparse
from dotenv import load_dotenv

# Cargar variables de entorno (para local, en producción usará las secrets)
load_dotenv()

# Configuración de la página
st.set_page_config(page_title="Clay Pipeline Replica", layout="wide")

st.title("Pipeline de Enriquecimiento de Leads (Clon de Clay)")
st.markdown("""
Esta herramienta replica el comportamiento de **Clay** para el enriquecimiento de leads de cold email. 
Toma un CSV con columnas `name`, `website`, `state`, y `email`, y añade:
1. `companyName`: Nombre normalizado (IA).
2. `servicio_destacado`: Servicio principal extraído de la web (Scraping + IA).
3. `icebreaker`: Frase de apertura SEO generada (IA).
""")

# ==========================================
# 1. SIDEBAR: Configuración y API Keys
# ==========================================
st.sidebar.header("⚙️ Configuración")

# Las API keys ahora se cargan de forma segura desde variables de entorno o Secrets
openai_api_key = os.environ.get("OPENAI_API_KEY")
serper_api_key = os.environ.get("SERPER_API_KEY")
jina_api_key = os.environ.get("JINA_API_KEY")

st.sidebar.markdown("---")
st.sidebar.markdown("### Ajustes del Motor")
max_filas = st.sidebar.number_input("Límite de filas a procesar (0 = todas)", min_value=0, value=10, step=1)

# Inicializar cliente de OpenAI (se configurará cuando haya API key)
client = None

# ==========================================
# FUNCIONES DE ENRIQUECIMIENTO (LAS 3 FASES)
# ==========================================

def get_openai_client():
    if not openai_api_key:
        raise ValueError("Falta la API Key de OpenAI")
    return openai.OpenAI(api_key=openai_api_key)

def normalize_name(raw_name: str) -> str:
    """Fase 1: Normalización de nombres"""
    if not raw_name or pd.isna(raw_name):
        return ""
    
    prompt = f"""Limpia y normaliza el nombre de esta clínica o médico o empresa para usarlo en un email de marketing profesional en español. El valor original es: {raw_name}

Reglas de limpieza:
1. Si el nombre está todo junto sin espacios (ej: "Drcolomer", "Drlalinde", "Faceliftbarcelona", "Drgarcia Paricio"), separa correctamente las palabras y añade puntos donde corresponda (ej: "Dr. Colomer", "Dr. Lalinde", "Facelift Barcelona", "Dr. García Paricio").
2. Si ya está bien escrito (ej: "DFINE Clinic", "Dr. Castro Sierra"), devuélvelo exactamente igual.
3. Corrige mayúsculas/minúsculas si es necesario.
4. Devuelve SOLO el nombre limpio, sin explicaciones ni texto adicional."""

    try:
        c = get_openai_client()
        response = c.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=0.1
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return str(raw_name)  # Fallback: devolver original si falla

def get_servicio_destacado(website_url: str) -> str:
    """Fase 2: Scraping + Extracción de Servicio Destacado"""
    if not website_url or pd.isna(website_url):
        return "su servicio principal"
    
    # Asegurar que tiene http/https
    if not website_url.startswith("http"):
        website_url = "https://" + website_url

    # Paso 1: Usar Jina Reader API para obtener contenido limpio en Markdown
    jina_url = f"https://r.jina.ai/{website_url}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "X-Return-Format": "markdown"
    }
    if jina_api_key:
        headers["Authorization"] = f"Bearer {jina_api_key}"
    
    text_content = ""
    try:
        # Añadimos logs para depurar el error de scraping
        print(f"Haciendo scraping con Jina a: {jina_url}")
        res = requests.get(jina_url, headers=headers, timeout=20)
        print(f"Jina status code: {res.status_code}")
        if res.status_code == 200:
            text_content = res.text[:8000] # Limitar caracteres (lo ampliamos un poco si Jina devuelve algo)
        else:
            print(f"Jina error: {res.text[:200]}")
            # Fallback simple con requests normal
            print("Intentando fallback con Requests normal...")
            fallback_res = requests.get(website_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if fallback_res.status_code == 200:
                text_content = fallback_res.text[:8000]
    except Exception as e:
        print(f"Jina request failed: {str(e)}")
        try:
            print("Intentando fallback con Requests normal...")
            fallback_res = requests.get(website_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if fallback_res.status_code == 200:
                text_content = fallback_res.text[:8000]
        except Exception:
            pass
    
    if not text_content:
        return "su servicio principal"

    # Paso 2: Usar GPT-4o para extraer el servicio principal de ese texto
    prompt = f"""Analiza este contenido de una web:

{text_content}

Dime cuál es el servicio o tratamiento más destacado que ofrecen en la empresa.
Devuelve SOLO el nombre del servicio, en español, en 2-5 palabras máximo.
Ejemplos: "rinoplastia de preservación", "medicina estética facial", "cirugía de párpados", "vaciado de naves industriales", "vaciado de pisos", "reparación de embragues" .
Si no puedes determinarlo o la web no parece tener servicios claros, devuelve "indeterminado".
No expliques nada. Solo el nombre."""

    try:
        c = get_openai_client()
        response = c.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=30,
            temperature=0.3
        )
        result = response.choices[0].message.content.strip()
        print(f"GPT resultado servicio: {result}")
        # Si GPT responde con algo muy largo por error, lo forzamos al fallback
        if len(result.split()) > 10:
            return "su servicio principal"
        # Limpiar comillas si devolvió comillas
        return result.replace('"', '').replace("'", "")
    except Exception as e:
        print(f"GPT error en servicio: {str(e)}")
        return "su servicio principal"

# Ya no analizaremos el tech stack (WordPress, etc.) porque le daremos más peso a los competidores.
# def detect_tech_stack(website_url: str) -> str:
# ...

def get_google_ranking(servicio: str, ciudad: str, website_url: str) -> tuple[str, str]:
    """Fase 2.6: Búsqueda Real en Google usando Serper.dev extraído del Motor SEO Beta"""
    if not serper_api_key:
        return ("(Sin API Key de Serper)", "en los resultados")
        
    if not servicio or not ciudad or not website_url:
        return ("otros especialistas", "en los resultados")
        
    # Limpiar servicio para evitar "su servicio principal"
    query_servicio = servicio
    if "su servicio principal" in servicio.lower():
        query_servicio = "clínica"
        
    query = f"{query_servicio} en {ciudad}"
    
    # Extraer el dominio base para buscarlo
    try:
        domain = urlparse(website_url if website_url.startswith('http') else f"https://{website_url}").netloc
        domain = domain.replace("www.", "")
    except:
        domain = website_url
        
    try:
        url = "https://google.serper.dev/search"
        payload = {
            "q": query,
            "gl": "es",
            "hl": "es",
            "num": 30, # Ampliamos a 30 resultados para encontrar al lead si está en paginas 2-3
            "type": "search"
        }
        headers = {
            'X-API-KEY': serper_api_key,
            'Content-Type': 'application/json'
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        data = response.json()
        
        organic = data.get("organic", [])
        
        # Filtrar resultados (igual que en el Motor SEO Beta)
        filtered_organic = []
        for r in organic:
            link = r.get("link", "").lower()
            is_youtube = "youtube.com" in link or "youtu.be" in link
            is_maps = "maps.google" in link or "google.com/maps" in link
            if not is_youtube and not is_maps:
                filtered_organic.append(r)
                
        if not filtered_organic:
            return ("competidores", "en la primera página")
            
        # 1. Encontrar en qué posición/página está nuestro lead
        lead_rank = -1
        for i, res in enumerate(filtered_organic):
            link = res.get("link", "").lower()
            if domain.lower() in link:
                lead_rank = i + 1
                break
                
        if lead_rank == -1:
            lead_page = "no aparecéis en las primeras 3 páginas de Google"
        elif lead_rank <= 10:
            lead_page = "aparecéis en la primera página, pero se puede mejorar la posición"
        elif lead_rank <= 20:
            lead_page = "aparecéis relegados a la página 2 de Google"
        else:
            lead_page = "aparecéis en la página 3 o inferior"
            
        # 2. Extraer 2 competidores (los primeros 2 que NO sean directorios)
        directorios = ["topdoctors", "doctoralia", "multiestetica", "sanitas", "quironsalud", "clinicbook"]
        competidores = []
        
        for res in filtered_organic:
            link = res.get("link", "").lower()
            title = res.get("title", "")
            
            # Si es nuestro lead, lo saltamos
            if domain.lower() in link:
                continue
                
            # Si es un directorio conocido, lo saltamos
            is_dir = any(d in link for d in directorios)
            if not is_dir:
                # Limpiar el título (quitar " - Inicio", etc)
                clean_title = title.split(" - ")[0].split(" | ")[0]
                competidores.append(clean_title)
                
            if len(competidores) >= 2:
                break
                
        if len(competidores) == 2:
            comps_text = f"{competidores[0]} y {competidores[1]}"
        elif len(competidores) == 1:
            comps_text = competidores[0]
        else:
            comps_text = "otros especialistas"
            
        return (comps_text, lead_page)
        
    except Exception as e:
        return ("otros especialistas", "en las primeras páginas")

def generate_icebreaker(company_name: str, city: str, servicio: str, competidores: str, google_page: str) -> str:
    """Fase 3: Generación del Icebreaker SEO Prospección (Avanzado V2)"""
    if not company_name or not city:
        return ""
    
    # Manejo de fallbacks
    if pd.isna(servicio) or not servicio:
        servicio = "su servicio principal"
    if pd.isna(city):
        city = ""

    prompt = f"""Eres un experto en copywriting de cold email B2B en español de España. Tu tarea es escribir UNA sola frase de apertura (icebreaker) para un email de prospección SEO.

Esta frase debe:
1. Mencionar que buscaste el servicio estrella de la clínica en Google en su ciudad
2. Mencionar de forma natural a sus competidores que sí están rankeando bien
3. Señalar en qué posición o situación están ellos en Google
4. Sonar conversacional, profesional y observador, como si lo escribiera una persona real de España
5. Tener máximo 3 líneas. Sin saludos, sin punto al final.

IMPORTANTE - Lenguaje: Escribe en español de España. NUNCA uses estas expresiones latinoamericanas:
- "Recientemente busqué" -> usa "He buscado" o "Buscando"
- "Estuve buscando" -> usa "He estado buscando" o "Buscando"
- "Explorando" -> usa "Buscando" o "Mirando"
- "Me sorprendió" -> usa "He notado" o "Me he dado cuenta"
Usa siempre construcciones con presente perfecto ("He buscado", "He visto") o gerundio ("Buscando").

Datos recolectados para personalizar:
- Clínica objetivo: {company_name}
- Ciudad: {city}
- Búsqueda realizada: "{servicio} en {city}"
- Competidores rankeando arriba: {competidores}
- Posición real del lead: {google_page}

Ejemplo de estructura esperada (¡no la copies literal, úsala de guía para el tono!): 
"He buscado '[servicio]' en [ciudad] y he visto que [competidores] están acaparando la primera página mientras que vosotros [posición real], lo que os está haciendo perder pacientes."

Devuelve SOLO la frase. Sin comillas, sin explicaciones."""

    try:
        c = get_openai_client()
        response = c.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.7
        )
        result = response.choices[0].message.content.strip()
        return result.replace('"', '')
    except Exception as e:
        return ""

# ==========================================
# INTERFAZ PRINCIPAL: Carga y Ejecución
# ==========================================

uploaded_file = st.file_uploader("Sube tu archivo CSV de leads (Debe contener columnas: name, website, state, email)", type=["csv"])

if uploaded_file is not None:
    # Cargar CSV original
    df_original = pd.read_csv(uploaded_file)
    
    # Crear variables en el session state si no existen para mantener el df enriquecido
    if "df_results" not in st.session_state:
        # Preparar columnas si no existen
        df = df_original.copy()
        if "companyName" not in df.columns:
            df["companyName"] = ""
        if "servicio_destacado" not in df.columns:
            df["servicio_destacado"] = ""
        if "icebreaker" not in df.columns:
            df["icebreaker"] = ""
        
        st.session_state.df_results = df
        st.session_state.processing = False
        st.session_state.processed_count = 0

    st.subheader("Vista previa de Datos")
    
    # Mostrar el dataframe actual
    # st.dataframe permite visualización interactiva parecida a Clay
    df_placeholder = st.empty()
    df_placeholder.dataframe(st.session_state.df_results, use_container_width=True)

    col1, col2 = st.columns([1, 4])
    
    with col1:
        start_btn = st.button("🚀 Ejecutar Enriquecimiento", type="primary", disabled=st.session_state.processing)
    
    if start_btn:
        if not openai_api_key:
            st.error("⚠️ La API Key de OpenAI no está configurada. Revisa tus variables de entorno (.env o Secrets).")
        else:
            st.session_state.processing = True
            
            # Limitar filas si está configurado
            df_to_process = st.session_state.df_results
            filas_a_procesar = len(df_to_process)
            if max_filas > 0:
                filas_a_procesar = min(len(df_to_process), max_filas)
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Procesar fila por fila
            for index in range(filas_a_procesar):
                row = df_to_process.iloc[index]
                website_url = str(row.get("website", ""))
                
                # Omitir si ya está procesada (tiene icebreaker)
                if pd.notna(row.get("icebreaker")) and str(row.get("icebreaker")).strip() != "":
                    progress_bar.progress((index + 1) / filas_a_procesar)
                    continue

                status_text.text(f"Procesando fila {index+1}/{filas_a_procesar}: {row.get('name', 'Desconocido')}...")
                
                # FASE 1: Normalizar nombre
                if pd.isna(row.get("companyName")) or str(row.get("companyName")).strip() == "":
                    comp_name = normalize_name(row.get("name", ""))
                    df_to_process.at[index, "companyName"] = comp_name
                else:
                    comp_name = row.get("companyName")
                
                df_placeholder.dataframe(df_to_process, use_container_width=True)
                
                # FASE 2: Servicio Destacado
                if pd.isna(row.get("servicio_destacado")) or str(row.get("servicio_destacado")).strip() == "":
                    servicio = get_servicio_destacado(website_url)
                    df_to_process.at[index, "servicio_destacado"] = servicio
                else:
                    servicio = row.get("servicio_destacado")
                
                df_placeholder.dataframe(df_to_process, use_container_width=True)
                
                # FASE 2.6: Google Search
                if "competidores" not in df_to_process.columns:
                    df_to_process["competidores"] = ""
                if "ranking" not in df_to_process.columns:
                    df_to_process["ranking"] = ""
                    
                state = str(row.get("state", "")) if pd.notna(row.get("state")) else ""
                comps, ranking = get_google_ranking(servicio, state, website_url)
                df_to_process.at[index, "competidores"] = comps
                df_to_process.at[index, "ranking"] = ranking
                
                df_placeholder.dataframe(df_to_process, use_container_width=True)
                
                # FASE 3: Icebreaker Evolucionado
                if pd.isna(row.get("icebreaker")) or str(row.get("icebreaker")).strip() == "":
                    ice = generate_icebreaker(comp_name, state, servicio, comps, ranking)
                    df_to_process.at[index, "icebreaker"] = ice
                
                # Actualizar UI en vivo final de fila
                df_placeholder.dataframe(df_to_process, use_container_width=True)
                
                progress_bar.progress((index + 1) / filas_a_procesar)
                
                # Pequeña pausa para no saturar APIs
                time.sleep(0.5)
            
            status_text.text("✅ ¡Enriquecimiento completado!")
            st.session_state.processing = False
            st.session_state.df_results = df_to_process
            st.rerun()

    # ==========================================
    # BOTÓN DE EXPORTACIÓN (Instantly format)
    # ==========================================
    if not st.session_state.processing:
        st.markdown("### Exportar Resultados")
        
        # Generar CSV para descargar
        csv_data = st.session_state.df_results.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Descargar CSV para Instantly",
            data=csv_data,
            file_name="leads_enriquecidos.csv",
            mime="text/csv",
        )
