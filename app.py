import os
import sys
import streamlit as st
import pandas as pd
import openai
import requests
import time
import warnings
from pathlib import Path
from io import BytesIO
from urllib.parse import urlparse

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# ==========================================
# CARGA DE API KEYS (a prueba de fallos)
# ==========================================
def _load_env_file():
    """Lee el .env manualmente, sin depender de load_dotenv ni st.secrets."""
    env_vars = {}
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars

_env = _load_env_file()
openai_api_key = os.environ.get("OPENAI_API_KEY") or _env.get("OPENAI_API_KEY", "")
serper_api_key = os.environ.get("SERPER_API_KEY") or _env.get("SERPER_API_KEY", "")
jina_api_key = os.environ.get("JINA_API_KEY") or _env.get("JINA_API_KEY", "")

st.set_page_config(page_title="Clon de Clay", layout="wide")
st.title("Pipeline de Enriquecimiento de Leads")

# ==========================================
# SIDEBAR: Estado de APIs
# ==========================================
st.sidebar.header("Estado de APIs")

st.sidebar.markdown(f"- OpenAI: {'✅ Conectada' if openai_api_key else '❌ **FALTA**'}")
st.sidebar.markdown(f"- Serper: {'✅ Conectada' if serper_api_key else '❌ **FALTA**'}")
st.sidebar.markdown(f"- Jina:   {'✅ Conectada' if jina_api_key else '⚠️ Opcional'}")

if not openai_api_key or not serper_api_key:
    env_path = Path(__file__).resolve().parent / ".env"
    st.sidebar.error(f"Archivo .env buscado en: {env_path} ({'existe' if env_path.exists() else 'NO EXISTE'})")

st.sidebar.markdown("---")
max_filas = st.sidebar.number_input("Filas a procesar (0 = todas)", min_value=0, value=10, step=1)

# ==========================================
# FUNCIONES
# ==========================================

def get_openai_client():
    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY no configurada")
    return openai.OpenAI(api_key=openai_api_key)


def normalize_name(raw_name: str) -> str:
    """Fase 1: Normalizar nombres sucios del scraping."""
    if not raw_name or pd.isna(raw_name):
        return str(raw_name) if raw_name else ""

    prompt = f"""Limpia y normaliza el nombre de esta clínica o médico o empresa para usarlo en un email profesional en español. El valor original es: {raw_name}

Reglas:
1. Si está todo junto sin espacios (ej: "Drcolomer", "Faceliftbarcelona"), separa palabras y añade puntos donde corresponda (ej: "Dr. Colomer", "Facelift Barcelona").
2. Si ya está bien escrito, devuélvelo igual.
3. Corrige mayúsculas/minúsculas si es necesario.
4. Devuelve SOLO el nombre limpio, sin explicaciones."""

    try:
        c = get_openai_client()
        resp = c.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=0.1,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        st.warning(f"⚠️ Error normalizando '{raw_name}': {e}")
        return str(raw_name)


def get_servicio_destacado(website_url: str) -> str:
    """Fase 2: Leer la web con Jina y extraer el servicio principal con GPT-4o."""
    if not website_url or pd.isna(website_url) or str(website_url).strip() == "":
        return "su servicio principal"

    website_url = str(website_url).strip()
    if not website_url.startswith("http"):
        website_url = "https://" + website_url

    # Paso 1: Obtener contenido de la web via Jina Reader
    text_content = ""
    jina_url = f"https://r.jina.ai/{website_url}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/plain",
    }
    if jina_api_key:
        headers["Authorization"] = f"Bearer {jina_api_key}"

    try:
        res = requests.get(jina_url, headers=headers, timeout=20)
        if res.status_code == 200 and len(res.text.strip()) > 50:
            text_content = res.text[:4000]
    except Exception:
        pass

    # Fallback: si Jina falla, intentar scraping directo básico
    if not text_content:
        try:
            res = requests.get(
                website_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=10,
                verify=False,
            )
            if res.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(res.text, "html.parser")
                parts = []
                for tag in soup.find_all(["title", "h1", "h2", "h3", "meta"]):
                    txt = tag.get_text(separator=" ", strip=True)
                    if txt:
                        parts.append(txt)
                    desc = tag.get("content", "")
                    if desc:
                        parts.append(desc)
                text_content = "\n".join(parts)[:3000]
        except Exception:
            pass

    if not text_content:
        return "su servicio principal"

    # Paso 2: Extraer servicio con GPT-4o
    prompt = f"""Analiza este contenido de una web:

{text_content}

Dime cuál es el servicio o tratamiento más destacado que ofrecen.
Devuelve SOLO el nombre del servicio, en español, en 2-5 palabras máximo.
Ejemplos: "rinoplastia de preservación", "medicina estética facial", "cirugía de párpados".
Si no puedes determinarlo, devuelve "su servicio principal".
No expliques nada. Solo el nombre."""

    try:
        c = get_openai_client()
        resp = c.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=30,
            temperature=0.3,
        )
        result = resp.choices[0].message.content.strip().replace('"', '').replace("'", "")
        if len(result.split()) > 10:
            return "su servicio principal"
        return result
    except Exception as e:
        st.warning(f"⚠️ Error extrayendo servicio: {e}")
        return "su servicio principal"


def get_google_ranking(servicio: str, ciudad: str, website_url: str) -> dict:
    """Buscar en Google via Serper.dev y extraer ranking + competidores."""
    fallback = {
        "competidores": "",
        "ranking": "",
        "posicion_exacta": -1,
    }

    if not serper_api_key:
        st.warning("⚠️ SERPER_API_KEY no configurada, no se puede buscar en Google.")
        return fallback

    if not servicio or not ciudad or not website_url:
        return fallback

    if "su servicio principal" in servicio.lower() or "indeterminado" in servicio.lower():
        query = f"clínica en {ciudad}"
    else:
        query = f"{servicio} en {ciudad}"

    website_url = str(website_url).strip()
    try:
        parsed = urlparse(website_url if website_url.startswith("http") else f"https://{website_url}")
        lead_domain = parsed.netloc.replace("www.", "").lower()
    except Exception:
        lead_domain = website_url.lower()

    try:
        res = requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": serper_api_key,
                "Content-Type": "application/json",
            },
            json={
                "q": query,
                "gl": "es",
                "hl": "es",
                "num": 30,
                "type": "search",
            },
            timeout=15,
        )

        if res.status_code != 200:
            st.warning(f"⚠️ Serper devolvió status {res.status_code} para '{query}'")
            return fallback

        data = res.json()
        organic = data.get("organic", [])

        if not organic:
            return fallback

        # Filtrar YouTube y Maps (igual que beta-serp-fetch)
        filtered = [
            r for r in organic
            if not any(s in r.get("link", "").lower() for s in (
                "youtube.com", "youtu.be", "maps.google", "google.com/maps"
            ))
        ]

        for idx, r in enumerate(filtered):
            r["_pos"] = idx + 1

        # Buscar posición del lead
        lead_pos = -1
        for r in filtered:
            if lead_domain in r.get("link", "").lower():
                lead_pos = r["_pos"]
                break

        if lead_pos == -1:
            ranking_text = "no aparecéis en los primeros 30 resultados de Google"
        elif lead_pos <= 3:
            ranking_text = f"aparecéis en la posición {lead_pos}, pero se puede mejorar"
        elif lead_pos <= 10:
            ranking_text = f"estáis en la posición {lead_pos} de la primera página"
        elif lead_pos <= 20:
            ranking_text = f"estáis relegados a la posición {lead_pos} (página 2)"
        else:
            ranking_text = f"estáis en la posición {lead_pos} (página 3 o inferior)"

        # Extraer competidores reales
        directorios = [
            "topdoctors", "doctoralia", "multiestetica", "sanitas",
            "quironsalud", "clinicbook", "wikipedia", "yelp",
            "paginasamarillas", "infojobs", "milanuncios",
        ]
        competidores = []

        for r in filtered:
            link = r.get("link", "").lower()
            title = r.get("title", "")

            if lead_domain in link:
                continue
            if any(d in link for d in directorios):
                continue

            clean_title = title.split(" - ")[0].split(" | ")[0].strip()
            if clean_title:
                competidores.append({"nombre": clean_title, "posicion": r["_pos"]})
            if len(competidores) >= 2:
                break

        if len(competidores) >= 2:
            comps_text = f"{competidores[0]['nombre']} (pos. {competidores[0]['posicion']}) y {competidores[1]['nombre']} (pos. {competidores[1]['posicion']})"
        elif len(competidores) == 1:
            comps_text = f"{competidores[0]['nombre']} (pos. {competidores[0]['posicion']})"
        else:
            comps_text = "otros especialistas"

        return {
            "competidores": comps_text,
            "ranking": ranking_text,
            "posicion_exacta": lead_pos,
        }

    except Exception as e:
        st.warning(f"⚠️ Error en Serper para '{query}': {e}")
        return fallback


def generate_icebreaker(company_name: str, city: str, servicio: str, competidores: str, google_page: str) -> str:
    """Fase 3: Generar icebreaker centrado en competidores y ranking real."""
    if not company_name or not city:
        return ""

    if pd.isna(servicio) or not servicio:
        servicio = "su servicio principal"
    if pd.isna(city):
        city = ""

    prompt = f"""Eres un experto en copywriting de cold email B2B en español de España. Escribe UNA sola frase de apertura (icebreaker) para un email de prospección SEO.

La frase debe:
1. Decir que has buscado el servicio estrella de la empresa en Google en su ciudad
2. Nombrar a sus competidores reales que están por encima (con su posición si la tienes)
3. Señalar la posición o situación real del lead en Google
4. Sonar natural, conversacional y profesional
5. Tener máximo 2 líneas. Sin saludos, sin punto al final

IMPORTANTE - Lenguaje: Español de España. NUNCA uses:
- "Recientemente busqué" -> usa "He buscado" o "Buscando"
- "Estuve buscando" -> usa "He estado buscando" o "Buscando"
- "Explorando" -> usa "Buscando" o "Mirando"
- "Me sorprendió" -> usa "He notado" o "He visto"
Usa presente perfecto ("He buscado", "He visto") o gerundio ("Buscando").

Datos reales de la búsqueda en Google:
- Empresa: {company_name}
- Ciudad: {city}
- Búsqueda: "{servicio} en {city}"
- Competidores por encima: {competidores}
- Posición del lead: {google_page}

Ejemplo de tono (NO copies literal):
"He buscado '{servicio} en {city}' como lo haría un paciente y he visto que {competidores} copan los primeros puestos, mientras que {company_name} {google_page}"

Devuelve SOLO la frase. Sin comillas, sin explicaciones."""

    try:
        c = get_openai_client()
        resp = c.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip().replace('"', '')
    except Exception as e:
        st.warning(f"⚠️ Error generando icebreaker para '{company_name}': {e}")
        return ""


# ==========================================
# INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader(
    "Sube tu archivo CSV de leads (columnas esperadas: name, website, state, email)",
    type=["csv"],
)

if uploaded_file is not None:
    df_original = pd.read_csv(uploaded_file)

    # Mostrar columnas detectadas para debug
    st.caption(f"Columnas detectadas: {', '.join(df_original.columns.tolist())}")

    if "df_results" not in st.session_state:
        df = df_original.copy()
        for col in ["companyName", "servicio_destacado", "competidores", "ranking", "icebreaker"]:
            if col not in df.columns:
                df[col] = ""
        st.session_state.df_results = df
        st.session_state.processing = False

    st.subheader("Datos")
    df_placeholder = st.empty()
    df_placeholder.dataframe(st.session_state.df_results, use_container_width=True, height=400)

    col1, col2, col3 = st.columns([1, 1, 3])

    with col1:
        start_btn = st.button("Ejecutar Enriquecimiento", type="primary", disabled=st.session_state.processing)
    with col2:
        reset_btn = st.button("Reiniciar datos")

    if reset_btn:
        del st.session_state["df_results"]
        st.rerun()

    if start_btn:
        if not openai_api_key:
            st.error("Falta OPENAI_API_KEY. Revisa tu archivo .env o las Secrets de Streamlit Cloud.")
        else:
            st.session_state.processing = True

            df_to_process = st.session_state.df_results
            total = len(df_to_process) if max_filas == 0 else min(len(df_to_process), max_filas)

            progress_bar = st.progress(0)
            status_text = st.empty()
            log_container = st.expander("Log de procesamiento", expanded=True)

            for i in range(total):
                row = df_to_process.iloc[i]
                raw_name = str(row.get("name", "")) if pd.notna(row.get("name")) else ""
                website_url = str(row.get("website", "")) if pd.notna(row.get("website")) else ""
                state = str(row.get("state", "")) if pd.notna(row.get("state")) else ""

                # Saltar filas ya procesadas
                existing_ice = str(row.get("icebreaker", "")).strip()
                if existing_ice and existing_ice != "":
                    progress_bar.progress((i + 1) / total)
                    continue

                status_text.text(f"Fila {i+1}/{total}: {raw_name}...")

                # FASE 1: Normalizar nombre
                with log_container:
                    st.write(f"**[{i+1}/{total}] {raw_name}**")

                comp_name = str(row.get("companyName", "")).strip()
                if not comp_name:
                    comp_name = normalize_name(raw_name)
                    df_to_process.at[i, "companyName"] = comp_name
                    with log_container:
                        st.write(f"  Nombre: {raw_name} → {comp_name}")
                    df_placeholder.dataframe(df_to_process, use_container_width=True, height=400)

                # FASE 2: Servicio Destacado
                servicio = str(row.get("servicio_destacado", "")).strip()
                if not servicio:
                    servicio = get_servicio_destacado(website_url)
                    df_to_process.at[i, "servicio_destacado"] = servicio
                    with log_container:
                        st.write(f"  Servicio: {servicio}")
                    df_placeholder.dataframe(df_to_process, use_container_width=True, height=400)

                # FASE 2.5: Google Search
                serp_data = get_google_ranking(servicio, state, website_url)
                comps = serp_data["competidores"]
                ranking = serp_data["ranking"]
                df_to_process.at[i, "competidores"] = comps
                df_to_process.at[i, "ranking"] = ranking
                with log_container:
                    st.write(f"  Competidores: {comps}")
                    st.write(f"  Ranking: {ranking}")
                df_placeholder.dataframe(df_to_process, use_container_width=True, height=400)

                # FASE 3: Icebreaker
                ice = generate_icebreaker(comp_name, state, servicio, comps, ranking)
                df_to_process.at[i, "icebreaker"] = ice
                with log_container:
                    st.write(f"  Icebreaker: {ice}")

                df_placeholder.dataframe(df_to_process, use_container_width=True, height=400)
                progress_bar.progress((i + 1) / total)
                time.sleep(0.3)

            status_text.text("Enriquecimiento completado.")
            st.session_state.processing = False
            st.session_state.df_results = df_to_process
            st.rerun()

    # Exportación
    if not st.session_state.processing:
        st.markdown("---")
        csv_data = st.session_state.df_results.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Descargar CSV enriquecido",
            data=csv_data,
            file_name="leads_enriquecidos.csv",
            mime="text/csv",
        )
