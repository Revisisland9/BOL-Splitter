import streamlit as st
from PyPDF2 import PdfReader, PdfWriter
import zipfile
import os
import re

def extract_bol_number(text, fallback):
    match = re.search(r"BOL Number:\s*(PLS\d+)", text)
    return match.group(1) if match else fallback

def split_and_zip_pdf(uploaded_file):
    # Create or clear the output directory
    temp_dir = os.path.join(os.getcwd(), "output")
    os.makedirs(temp_dir, exist_ok=True)
    for f in os.listdir(temp_dir):
        os.remove(os.path.join(temp_dir, f))

    reader = PdfReader(uploaded_file)
    split_files = []

    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)

        text = page.extract_text() or ""
        bol_number = extract_bol_number(text, f"Page_{i+1}")
        filename = f"{bol_number}.pdf"
        output_path = os.path.join(temp_dir, filename)

        with open(output_path, "wb") as f:
            writer.write(f)
        split_files.append(output_path)

    # Create the zip file
    zip_path = os.path.join(temp_dir, "BOL_PDFs.zip")
    with zipfile.ZipFile(zip_path, "w") as zipf:
        for file_path in split_files:
            zipf.write(file_path, arcname=os.path.basename(file_path))

    return zip_path

# Streamlit UI
st.title("BOL PDF Splitter")
st.write("Upload a multi-page PDF of Bills of Lading. Each page will be split and named by its BOL Number, then packaged into a ZIP file for download.")

uploaded_file = st.file_uploader("Upload PDF", type="pdf")

if uploaded_file is not None:
    if st.button("Process PDF"):
        with st.spinner("Splitting and zipping your file..."):
            zip_file_path = split_and_zip_pdf(uploaded_file)
            with open(zip_file_path, "rb") as f:
                st.success("Done! Download your ZIP file below.")
                st.download_button("Download ZIP", f, file_name="BOL_PDFs.zip")
