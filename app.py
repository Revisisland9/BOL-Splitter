import streamlit as st
from PyPDF2 import PdfReader, PdfWriter
import zipfile
import io
import re
import unicodedata

st.set_page_config(page_title="BOL/Invoice Splitter", page_icon="📄")

def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def sanitize_filename(name: str, maxlen: int = 120) -> str:
    # Remove accents, normalize Unicode, keep safe chars
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    # Replace forbidden filename chars
    name = re.sub(r'[\\/*?:"<>|]+', "_", name)
    name = normalize_ws(name).replace(" ", "_")
    return name[:maxlen] or "Untitled"

# --- NEW: robust extractors for EDI Import Primary Reference, with fallbacks ---
def extract_primary_reference(text: str) -> str | None:
    """
    Prefer: EDI Import Primary Reference: PCL43613
    OCR/format tolerant: allow extra spaces, optional colon, etc.
    """
    # Primary: exact label (tolerant spacing/case)
    pat_primary = re.compile(
        r"EDI\s*Import\s*Primary\s*Reference\s*[:\-]?\s*([A-Z]{2,5}\d{4,})",
        flags=re.IGNORECASE
    )
    m = pat_primary.search(text)
    if m:
        return normalize_ws(m.group(1))

    # Fallback 1: Shipment Matching Reference: PCL43613-1-2  -> take left side of the first dash
    pat_match_ref = re.compile(
        r"Shipment\s*Matching\s*Reference\s*[:\-]?\s*([A-Z]{2,5}\d{4,}(?:[-_/][A-Za-z0-9]+)*)",
        flags=re.IGNORECASE
    )
    m2 = pat_match_ref.search(text)
    if m2:
        left = m2.group(1).split("-", 1)[0]
        return normalize_ws(left)

    return None

def split_pdf_pages_to_zip(uploaded_file) -> bytes:
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

            ident = extract_primary_reference(text)
            if not ident:
                ident = f"Page_{i}"

            fname = sanitize_filename(ident)
            # De-dupe filenames
            base = fname
            n = 2
            while fname in seen:
                fname = f"{base}_p{i}" if n == 2 else f"{base}_p{i}_{n}"
                n += 1
            seen.add(fname)

            # Write page PDF to memory and into ZIP
            pdf_bytes = io.BytesIO()
            writer.write(pdf_bytes)
            pdf_bytes.seek(0)
            zipf.writestr(f"{fname}.pdf", pdf_bytes.read())

            progress.progress(i / total_pages, text=f"Processed page {i}/{total_pages}")

    zip_mem.seek(0)
    return zip_mem.read()

# ---------------- UI ----------------
st.title("📄 PDF Splitter → Name by EDI Import Primary Reference")
st.write(
    "Upload a multi-page PDF. Each page will be split and named by its **EDI Import Primary Reference** "
    "(e.g., `PCL43613`). If missing, the app falls back to **Shipment Matching Reference** (left side before any dash), "
    "then to `Page_#`."
)

uploaded_file = st.file_uploader("Upload PDF", type=["pdf"])

if uploaded_file is not None:
    if st.button("Process PDF"):
        try:
            with st.spinner("Splitting and zipping your file…"):
                zip_bytes = split_pdf_pages_to_zip(uploaded_file)
            st.success("Done! Download your ZIP file below.")
            st.download_button(
                "Download ZIP",
                data=zip_bytes,
                file_name="Split_by_EDI_Primary_Ref.zip",
                mime="application/zip",
            )
        except Exception as e:
            st.error(f"Something went wrong: {e}")
else:
    st.info("Upload a multi-page PDF to get started.")
