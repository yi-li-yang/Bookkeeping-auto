"""Parse PDF files into raw text/table rows using pdfplumber."""
import pdfplumber


def parse_pdf(file_path: str) -> dict:
    """
    Extract text and tables from a PDF.
    Returns a dict with:
      - 'tables': list of tables (each table is a list of rows, each row is a list of cells)
      - 'text': full concatenated text of the document
    """
    all_tables = []
    full_text_parts = []

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            full_text_parts.append(page_text)

            tables = page.extract_tables()
            for table in tables:
                # Filter out completely empty rows
                cleaned = [row for row in table if any(cell for cell in row if cell)]
                if cleaned:
                    all_tables.append(cleaned)

    return {
        "tables": all_tables,
        "text": "\n".join(full_text_parts),
    }
