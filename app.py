import streamlit as st
from PyPDF2 import PdfReader, PdfWriter
import zipfile
import io
import re
import unicodedata

st.set_page_config(page_title="PDF Tools (BOL / EDI / Primary Ref)", page_icon="📄")

# ------------------------
# Utilities
# ------------------------
def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def sanitize_filename(name: str, maxlen: int = 120) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r'[\\/*?:"<>|]+', "_", name)
    name = normalize_ws(name).replace(" ", "_")
    return name[:maxlen] or "Untitled"

# ------------------------
# Existing: split-by-identifier helpers
# ------------------------
def patterns_for_mode(mode: str):
    def trim_left_token(g):
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
    return edi_core + legacy_core + loose  # Auto

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

            # de-dupe
            base = fname; suff = 2
            while fname in seen:
                fname = f"{base}_p{i}" if suff == 2 else f"{base}_p{i}_{suff}"
                suff += 1
            seen.add(fname)

            buf = io.BytesIO()
            writer.write(buf)
            buf.seek(0)
            zipf.writestr(f"{fname}.pdf", buf.read())

            progress.progress(i / total_pages, text=f"Processed page {i}/{total_pages}")

    zip_mem.seek(0)
    return zip_mem.read()

# ------------------------
# NEW: Batch rename multiple PDFs by first-page BOL
# ------------------------
def extract_bol_from_first_page(text: str, custom_regex: str | None = None) -> str | None:
    """
    Looks for a BOL token like PLS0017445558 on the FIRST PAGE text.
    - Tries custom regex first (must contain a capturing group).
    - Then tries common BOL label patterns.
    - Then a loose PLS/PCL token.
    """
    t = text or ""
    # 0) user-provided
    if custom_regex:
        try:
            m = re.search(custom_regex, t, flags=re.IGNORECASE)
            if m:
                return normalize_ws(m.group(1) if m.lastindex else m.group(0))
        except re.error:
            # if regex invalid, ignore and continue
            pass

    # 1) Explicit bottom label variants (not strictly required to be bottom in text flow)
    labeled = [
        r"\bBOL\s*(?:Number|No\.?|#)?\s*[:\-]?\s*(PLS\d{8,})",
        r"\bBill\s*of\s*Lading(?:\s*Number|\s*No\.?|#)?\s*[:\-]?\s*(PLS\d{8,})",
        r"\bPrimary\s*Ref(?:erence)?\s*[:\-]?\s*(PLS\d{8,})",
    ]
    for patt in labeled:
        m = re.search(patt, t, flags=re.IGNORECASE)
        if m:
            return normalize_ws(m.group(1))

    # 2) Loose PLS/PCL token (numbers often 8–12+ digits; adjust if needed)
    m = re.search(r"\b(?:PLS|PCL)[-]?\d{6,}\b", t, flags=re.IGNORECASE)
    if m:
        return normalize_ws(m.group(0))

    return None

def batch_rename_pdfs_to_zip(files, custom_regex: str | None = None, keep_original_name_fallback: bool = False) -> bytes:
    """
    For each uploaded PDF: read FIRST PAGE only, pull BOL (PLS###########),
    rename the file to that BOL, and add to a ZIP. If not found, either:
      - use original filename (if keep_original_name_fallback=True), or
      - use Untitled_n.
    """
    zip_mem = io.BytesIO()
    with zipfile.ZipFile(zip_mem, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        progress = st.progress(0, text="Renaming…")
        total = len(files)
        untitled_idx = 1

        for idx, up in enumerate(files, start=1):
            try:
                reader = PdfReader(up)
                first_page = reader.pages[0]
                try:
                    text = first_page.extract_text() or ""
                except Exception:
                    text = ""

                bol = extract_bol_from_first_page(text, custom_regex)
                if bol:
                    new_name = sanitize_filename(bol) + ".pdf"
                else:
                    if keep_original_name_fallback and hasattr(up, "name") and up.name:
                        base = up.name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                        new_name = sanitize_filename(base.rsplit(".pdf", 1)[0]) + ".pdf"
                    else:
                        new_name = f"Untitled_{untitled_idx}.pdf"
                        untitled_idx += 1

                # write original PDF bytes into zip with new_name
                up.seek(0)
                data = up.read()
                zipf.writestr(new_name, data)

            except Exception as e:
                # add an error stub so nothing silently disappears
                zipf.writestr(f"ERROR_{idx}.txt", f"Failed to process file {getattr(up,'name','(unknown)')}: {e}")

            progress.progress(idx / total, text=f"Processed {idx}/{total}")

    zip_mem.seek(0)
    return zip_mem.read()

# ------------------------
# UI
# ------------------------
st.title("📄 PDF Tools Suite")
mode_main = st.sidebar.radio(
    "Choose tool",
    ["Split multi-page PDF by identifier", "Batch rename PDFs by first-page BOL"],
    index=0
)

if mode_main == "Split multi-page PDF by identifier":
    st.subheader("Split & Name by Primary Reference / EDI Import")
    with st.sidebar:
        mode_profile = st.selectbox(
            "Doc profile",
            ["Auto (recommended)", "EDI Import", "Legacy: Primary Reference"],
            index=0
        )
        st.caption(
            "- **Auto**: EDI Import → Primary Ref → fallbacks\n"
            "- **EDI Import**: prioritize *EDI Import Primary Reference*\n"
            "- **Legacy**: prioritize *Primary Reference*"
        )

    uploaded_file = st.file_uploader("Upload a multi-page PDF", type=["pdf"])
    if uploaded_file is not None and st.button("Process PDF"):
        try:
            with st.spinner("Splitting and zipping your file…"):
                zip_bytes = split_pdf_pages_to_zip(uploaded_file, mode_profile)
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
    st.subheader("Batch Rename PDFs by First-Page BOL (e.g., PLS0017445558)")
    st.write("Upload multiple PDFs. Each file is renamed to the **BOL number** found on the **first page** and zipped.")

    with st.sidebar:
        st.markdown("**BOL Detection Options**")
        custom_regex = st.text_input(
            "Custom BOL regex (optional)",
            value="",
            placeholder=r"Example: BOL\s*#?\s*[:\-]?\s*(PLS\d{8,})",
            help="Include a capturing group for the BOL value (e.g., (PLS\\d{8,}))."
        )
        keep_original_name_fallback = st.checkbox(
            "If no BOL found, keep original filename",
            value=True
        )

    multi_files = st.file_uploader("Upload one or more PDFs", type=["pdf"], accept_multiple_files=True)
    if multi_files and st.button("Rename Files"):
        try:
            with st.spinner("Renaming files and building ZIP…"):
                zip_bytes = batch_rename_pdfs_to_zip(
                    files=multi_files,
                    custom_regex=custom_regex.strip() or None,
                    keep_original_name_fallback=keep_original_name_fallback
                )
            st.success("Done! Download your ZIP below.")
            st.download_button(
                "Download ZIP",
                data=zip_bytes,
                file_name="Renamed_By_BOL.zip",
                mime="application/zip",
            )
        except Exception as e:
            st.error(f"Something went wrong: {e}")
