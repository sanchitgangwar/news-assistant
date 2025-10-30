import os, io, sys, pandas as pd
import argparse
from pdf2image import convert_from_path
from google.cloud import vision_v1 as vision
import google.genai as genai

import fitz  # PyMuPDF
import io
from PIL import Image
from fpdf import FPDF


# ---------- CONFIG ----------
MODEL_NAME = "gemini-2.5-flash-lite"
OCR_DPI = 300
os.environ['GEMINI_API_KEY'] = "AIzaSyBhZ1JfldD44Z5MvuvNyzt2Pkra5Iwo66o"
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = os.path.dirname(os.path.realpath(__file__)) + "/news-assistant-client.json"
vision_client = vision.ImageAnnotatorClient()

client = genai.Client()

TEMP = "/var/www/news-assistant/temp"
FONT_FACE = "google-noto"
FONT_PATH = os.path.dirname(os.path.realpath(__file__)) + "/assets/Noto_Sans/static/NotoSans-Regular.ttf"
FONT_BOLD_PATH = os.path.dirname(os.path.realpath(__file__)) + "/assets/Noto_Sans/static/NotoSans-Bold.ttf"

# ---------- OCR STEP ----------
def ocr_telugu_pages(pdf_path):
    """Extract Telugu text from each page using Google Vision OCR."""
    pages = convert_from_path(pdf_path, dpi=OCR_DPI)
    texts = []
    for i, page in enumerate(pages, start=1):
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        image = vision.Image(content=buf.getvalue())
        resp = vision_client.document_text_detection(image=image)
        text = resp.full_text_annotation.text.strip() if resp.full_text_annotation.text else ""
        texts.append(text)
        print(f"🪶 OCR done for page {i}")
        sys.stdout.flush()
    return texts

# ---------- TRANSLATE + SUMMARIZE ----------
def translate_and_summarize_gemini(telugu_text):
    """Use Gemini to translate Telugu → English and summarize."""
    if not telugu_text.strip():
        return ""
    prompt = f"""
You are a professional translator and editor.

Step 1: Translate the following Telugu news text into clear English.
Step 2: Then summarize it in 2–4 factual, neutral sentences.
Preserve names, places, dates, and amounts accurately.

For the result of Step 1, use heading "TRANSLATION" without bold or italic.
For the result of Step 2, use heading "SUMMARY" without bold or italic.
For the title of the article, use heading "TITLE" without bold or italic. Do not repeat the title in "TRANSLATION" or "SUMMARY".

Telugu text:
{telugu_text}
"""
    response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
    return response.text.strip() if response.text else ""


def generate_output_pdf(input_path, output_path, results):
    pdf_file = fitz.open(input_path)

    images = []
    for page_index in range(len(pdf_file)):
        page = pdf_file.load_page(page_index)
        image_list = page.get_images(full=True)

        if image_list:
            print(f"[+] Found a total of {len(image_list)} images on page {page_index}")
        else:
            print("[!] No images found on page", page_index)
        sys.stdout.flush()

        for image_index, img in enumerate(image_list, start=1):
            # get the XREF of the image
            xref = img[0]

            # extract the image bytes
            base_image = pdf_file.extract_image(xref)
            image_bytes = base_image["image"]

            # get the image extension
            image_ext = base_image["ext"]

            # save the image
            image_name = f"image{page_index+1}_{image_index}.{image_ext}"
            images.append(TEMP + "/" + image_name)
            with open(TEMP + "/" + image_name, "wb") as image_file:
                image_file.write(image_bytes)
                # print(f"[+] Image saved as {image_name}")
                sys.stdout.flush()


    pdf = FPDF(format="A4")
    pdf.add_font(FONT_FACE, style="", fname=FONT_PATH)
    pdf.add_font(FONT_FACE, style="B", fname=FONT_BOLD_PATH)
    pdf.set_font(FONT_FACE)

    h = pdf.eph/2

    for index in range(len(images)):
        image = images[index]
        result = results[index]

        pdf.add_page()
        pdf.image(image, x=10, y=10, h=pdf.eph/2, w=pdf.epw-10, keep_aspect_ratio=True)
        pdf.set_xy(x=10, y=pdf.eph/2+5)
        pdf.set_font(FONT_FACE, 'B')
        pdf.write(h=None, text=f"SUMMARY: {result["title"]}\n\n")
        pdf.set_font(FONT_FACE)
        pdf.write(h=None, text=result["summary"])

        pdf.add_page()
        pdf.set_xy(x=10, y=10)
        pdf.set_font(FONT_FACE, 'B')
        pdf.write(h=None, text=f"TRANSLATION: {result["title"]}\n\n")
        pdf.set_font(FONT_FACE)
        pdf.write(h=None, text=result["translation"])

    pdf.output(output_path)



# ---------- MAIN ----------
def main(input_path, output_path):
    telugu_pages = ocr_telugu_pages(input_path)
    results = []
    for i, te_text in enumerate(telugu_pages, start=1):
        print(f"✨ Translating + summarizing page {i}...")
        sys.stdout.flush()
        output = translate_and_summarize_gemini(te_text)

        output_parts = output.split("TRANSLATION")
        title = output_parts[0].split("TITLE")[1].replace("\n", "");

        output_parts = output_parts[1].split("SUMMARY")
        translation = output_parts[0]
        summary = output_parts[1]

        results.append({
            "page": i,
            "title": title,
            "summary": summary,
            "translation": translation
        })

    generate_output_pdf(input_path, output_path, results)

    excel_output = []
    for i, output in enumerate(results):
        excel_output.append({
            "Sl. No.": output["page"],
            "Title": output["title"],
            "Summary": output["summary"],
            "Translation": output["translation"]
        }) 

    df = pd.DataFrame(excel_output)
    out_csv = "output.csv"
    df.to_csv(output_path.replace(".pdf", ".csv"), index=False)
    print(f"\n✅ Done! Results ready for download.")
    sys.stdout.flush()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
            description="Process a PDF file and produce a translated output."
    )
    parser.add_argument("--input", required=True, help="Path to the input PDF file")
    parser.add_argument("--output", required=True, help="Path to save the output PDF file")
    args = parser.parse_args()

    input_path = args.input
    output_path = args.output

    if len(sys.argv) < 3:
        print("Usage: python index.py --input <input_file.pdf> --output <output_file.pdf>")
        sys.exit(1)
    main(input_path, output_path)
