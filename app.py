import streamlit as st
import pdfplumber
import re
import io
import zipfile
from pypdf import PdfReader, PdfWriter

# ── Configuração da página ──────────────────────────────────────────────────
st.set_page_config(
    page_title="Separador de Boletos PDF",
    page_icon="📄",
    layout="centered",
)

st.title("📄 Separador de Boletos PDF")
st.markdown(
    "Faça o upload do arquivo PDF com todos os boletos. "
    "O sistema irá separar cada página em um arquivo individual, "
    "renomeando automaticamente com o nome do condomínio, competência, unidade e pagador."
)
st.divider()

# ── Sufixos de tipo de documento a remover do nome ─────────────────────────
SUFIXOS_DOCUMENTO = ["BALANCETE", "EXTRATO", "AVISO", "DEMONSTRATIVO"]

# ── Funções de extração ─────────────────────────────────────────────────────

def extrair_nome_cond(pdf_bytes: bytes) -> str:
    """Extrai o nome do condomínio da primeira linha da primeira página."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = pdf.pages[0].extract_text() or ""
            first_line = text.split("\n")[0] if text else ""
            is_recibo = bool(re.match(r"^RECIBO DO PAGADOR", first_line, re.IGNORECASE))
            first_line = re.sub(r"^RECIBO DO PAGADOR[:\s]+", "", first_line, flags=re.IGNORECASE).strip()
            nome = first_line.split(" - ")[0].strip()
            if is_recibo:
                for sufixo in SUFIXOS_DOCUMENTO:
                    nome = re.sub(rf'\s+{sufixo}\s*$', '', nome, flags=re.IGNORECASE).strip()
            return nome
    except Exception:
        pass
    return ""


def extrair_info(lines: list[str]) -> tuple[str, str, str, bool]:
    """Extrai competência, unidade, nome do pagador e se é acordo."""
    first_line = lines[0] if lines else ""

    # Competência — busca linha "COMPOSIÇÃO DE ARRECADAÇÃO" primeiro
    competencia = None
    for line in lines:
        m = re.search(r"COMPOSI[ÇC][ÃA]O.*?(\d{2}/\d{4})", line, re.IGNORECASE)
        if m:
            competencia = m.group(1).replace("/", "_")
            break
    if not competencia:
        m = re.search(r"(\d{2}/\d{4})", first_line)
        if m:
            competencia = m.group(1).replace("/", "_")
    if not competencia:
        for line in lines:
            m = re.search(r"PAGÁVEL.*?(\d{2}/\d{2}/\d{4})", line)
            if m:
                d = m.group(1)
                competencia = f"{d[3:5]}_{d[6:10]}"
                break
    if not competencia:
        competencia = "00_0000"

    # Unidade — tenta 1ª linha (último nº), depois busca "Unidade:" em todas
    unit = None
    m = re.search(r"[Uu][Nn][Ii][Dd][Aa][Dd][Ee].*?(\d+)\s*$", first_line)
    if m:
        unit = m.group(1)
    if not unit:
        for line in lines:
            m = re.search(r"[Uu]nidade[:\s]+(.+?)(?:\s{3,}|$)", line)
            if m:
                unit = m.group(1).strip()
                break
    if not unit:
        unit = "?"

    # Acordo
    is_acordo = any("ACORDO" in line.upper() for line in lines[:6])

    # Pagador
    pagador = ""
    for line in lines:
        if "Pagador" in line:
            clean = re.sub(r"^Pagador\s+", "", line, flags=re.IGNORECASE)
            clean = re.sub(r"\s+\d{3}\.\d{3}\.\d{3}-\d{2}.*$", "", clean).strip()
            pagador = clean.upper()
            break
    if not pagador:
        pagador = "PAGADOR DESCONHECIDO"

    return competencia, unit, pagador, is_acordo


def nome_arquivo(cond: str, competencia: str, unit: str, pagador: str, is_acordo: bool) -> str:
    base = f"{cond} - {competencia} - UN {unit} - {pagador}"
    if is_acordo:
        base += " - ACORDO"
    return re.sub(r'[<>:"/\\|?*]', "", base) + ".pdf"


def processar_pdf(pdf_bytes: bytes, cond: str) -> tuple[dict[str, bytes], list[str]]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    used_names: dict[str, int] = {}
    resultados: dict[str, bytes] = {}
    nomes: list[str] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            lines = text.split("\n")

            competencia, unit, pagador, is_acordo = extrair_info(lines)
            filename = nome_arquivo(cond, competencia, unit, pagador, is_acordo)

            key = filename.lower()
            if key in used_names:
                used_names[key] += 1
                base = filename[:-4]
                filename = f"{base} ({used_names[key]}).pdf"
            else:
                used_names[key] = 1

            writer = PdfWriter()
            writer.add_page(reader.pages[i])
            buf = io.BytesIO()
            writer.write(buf)
            resultados[filename] = buf.getvalue()
            nomes.append(filename)

    return resultados, nomes


def criar_zip(boletos: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for nome, conteudo in boletos.items():
            zf.writestr(nome, conteudo)
    return buf.getvalue()


# ── Interface ───────────────────────────────────────────────────────────────

uploaded_file = st.file_uploader(
    "Selecione o arquivo PDF com os boletos",
    type=["pdf"],
    help="Cada página do PDF deve corresponder a um boleto.",
)

if uploaded_file is not None:
    pdf_bytes = uploaded_file.read()
    st.success(f"Arquivo carregado: **{uploaded_file.name}**")

    nome_detectado = extrair_nome_cond(pdf_bytes)
    nome_cond = st.text_input(
        "Nome do condomínio (detectado automaticamente — edite se necessário)",
        value=nome_detectado,
        help="Extraído da primeira linha do boleto. Pode ser alterado manualmente.",
    )

    if st.button("⚙️ Processar boletos", type="primary", use_container_width=True):
        if not nome_cond.strip():
            st.warning("⚠️ Não foi possível detectar o nome do condomínio. Preencha o campo acima.")
        else:
            with st.spinner("Processando... aguarde."):
                try:
                    boletos, nomes = processar_pdf(pdf_bytes, nome_cond.strip())
                    zip_bytes = criar_zip(boletos)

                    st.success(f"✅ **{len(boletos)} boletos** processados com sucesso!")

                    st.download_button(
                        label="⬇️ Baixar todos os boletos (.zip)",
                        data=zip_bytes,
                        file_name=f"{nome_cond.strip()} - boletos separados.zip",
                        mime="application/zip",
                        use_container_width=True,
                        type="primary",
                    )

                    with st.expander(f"Ver lista dos {len(nomes)} arquivos gerados"):
                        for i, nome in enumerate(nomes, 1):
                            acordo = " 🔵" if "ACORDO" in nome else ""
                            st.markdown(f"`{i:02d}.` {nome}{acordo}")

                except Exception as e:
                    st.error(f"Erro ao processar o arquivo: {e}")
                    st.exception(e)

st.divider()
st.caption(
    "Boletos identificados automaticamente por condomínio, competência, unidade e pagador."
                        )
