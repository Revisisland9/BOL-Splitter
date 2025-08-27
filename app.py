import streamlit as st
from PyPDF2 import PdfReader, PdfWriter
import zipfile
import io
import re
import unicodedata

st.set_page_config(page_title="PDF Splitter (Primary Reference / EDI)", page_icon="📄")

def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def sanitize_filename(name: str, maxlen: int = 120) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r'[\\/*?:"<>|]+', "_", name)
    name = normalize_ws(name).replace(" ", "_")
    return name[:maxlen] or "Untitled"

# ------- Pattern helpers (ordered by priority per mode) -------
def patterns_for_mode(mode: str):
    """
    Returns a list of (label, regex, postprocess_fn_or_None) tuples in search order.
    """
    def trim_left_token(g):
        # For "Shipment Matching Reference: PCL43613-1-2" → "PCL43613"
        return g.split("-", 1)[0]

    edi_core = [
        ("EDI Import Primary Reference",
         r"EDI\s*Import\s*Primary\s*Reference\s*[:\-]?\s*([A-Z]{2,5}\d{4,}[A-Za-z0-9\-]*)",
         None),
        ("Shipment Matching Reference",
         r"Shipment\s*Matching\s*Reference\s*[:\-]?\s*([A-Z]{2,5}\d{4,}(?:[-_/][A-Za-z0-9]+)*)",
         trim_left_token),
    ]

    legacy_core = [
        ("Primary Reference / Primary Ref",
         r"\bPrimary\s*Ref(?:erence)?\s*[:\-]?\s*([A-Z]{2,5}\d{4,}[A-Za-z0-9\-]*)",
         None),
        ("BOL Number",
         r"\bBOL(?:\s*Number|\s*No\.?|#)?\s*[:\-]?\s*([A-Z]{2,5}\d{4,}[A-Za-z0-9\-]*)",
         None),
    ]

    loose = [
        ("Loose PLS/PCL token",
         r"\b(?:PLS|PCL)[-]?\d{4,}[A-Za-z0-9\-]*\b",
         None),
    ]

    if mode == "EDI Import":
        return edi_core + legacy_core + loose
    if mode == "Legacy: Primary Reference":
        return legacy_core + edi_core + loose
    # Auto
    return edi_core + legacy_core + loose

def extract_identifier(text: str, mode: str) -> str | None:
    patt_list = patterns_for_mode(mode)
    for _label, patt, post in patt_list:
        m = re.search(patt, text or "", flags=re.IGNORECASE)
        if m:
            g = normalize_ws(m.group(1) if m.lastindex else m.group(0))
            return normalize_ws(post(g) if post else g)
    return None

def split_pdf_pages_to_zip(uploaded_file, mode: str) -> bytes:
    reader = PdfReader(uploaded_file)
    seen = set()
    zip_mem = io.BytesIO()

    with zipfile.ZipFile(zip_mem, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        progress = st.progress(0, text="Processing pages…")
        total_pages = len(reader.pages)

        for i, page in enumerate(reader.pages, start=1):
            writer = PdfWriter()
            writer.add_page(page)

            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""

            ident = extract_identifier(text, mode) or f"Page_{i}"
            fname = sanitize_filename(ident)

            # De-dupe filenames
            base = fname
            suff = 2
            while fname in seen:
                fname = f"{base}_p{i}" if suff == 2 else f"{base}_p{i}_{suff}"
                suff += 1
            seen.add(fname)

            pdf_bytes = io.BytesIO()
            writer.write(pdf_bytes)
            pdf_bytes.seek(0)
            zipf.writestr(f"{fname}.pdf", pdf_bytes.read())

            progress.progress(i / total_pages, text=f"Processed page {i}/{total_pages}")

    zip_mem.seek(0)
    return zip_mem.read()

# -------------------- UI --------------------
st.title("📄 PDF Splitter → Name by Primary Reference / EDI Import")
st.write(
    "Split a multi-page PDF and name each page by an identifier. "
    "Use the sidebar to choose the document profile."
)

with st.sidebar:
    mode = st.selectbox(
        "Doc profile",
        ["Auto (recommended)", "EDI Import", "Legacy: Primary Reference"],
        index=0
    )
    st.caption(
        "- **Auto**: tries EDI Import, Primary Reference, then fallbacks.\n"
        "- **EDI Import**: prioritizes *EDI Import Primary Reference*.\n"
        "- **Legacy**: prioritizes *Primary Reference*."
    )

uploaded_file = st.file_uploader("Upload PDF", type=["pdf"])

if uploaded_file is not None:
    if st.button("Process PDF"):
        try:
            with st.spinner("Splitting and zipping your file…"):
                zip_bytes = split_pdf_pages_to_zip(uploaded_file, mode)
            st.success("Done! Download your ZIP file below.")
            st.download_button(
                "Download ZIP",
                data=zip_bytes,
                file_name="Split_Pages.zip",
                mime="application/zip",
            )
        except Exception as e:
            st.error(f"Something went wrong: {e}")
else:
    st.info("Upload a multi-page PDF to get started.")
