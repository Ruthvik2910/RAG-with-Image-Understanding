"""Validated extraction without normalization or silent content truncation."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import codecs
import zipfile
from backend import runtime_settings as settings

class FileValidationError(ValueError):
    pass

@dataclass
class Extracted:
    text: str
    pages: list[dict]
    encoding: str = ''

MIMES = {
    '.pdf': {'application/pdf'},
    '.docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/zip'},
    '.txt': {'text/plain'},
    '.md': {'text/plain', 'text/markdown', 'text/x-markdown'},
}

def validate_envelope(filename, data, mime='application/octet-stream'):
    ext = Path(filename).suffix.lower()
    if ext not in settings.UPLOAD_LIMITS:
        raise FileValidationError('Unsupported file type. Supported: PDF, DOCX, TXT, MD.')
    if len(data) > settings.UPLOAD_LIMITS[ext]:
        raise FileValidationError(
            f'File upload failed. The maximum allowed {ext[1:].upper()} size is '
            f'{settings.UPLOAD_LIMITS[ext] / settings.MIB:g} MB. Your uploaded file is '
            f'{len(data) / settings.MIB:.2f} MB. Please upload a smaller file.'
        )
    if not data:
        raise FileValidationError('File is empty. Please upload a non-empty document.')
    mime = (mime or 'application/octet-stream').split(';')[0].strip().lower()
    if mime not in MIMES[ext] | {'application/octet-stream'}:
        raise FileValidationError(f'Incorrect MIME type {mime} for {ext.upper()}.')
    return ext


def decode_text(data):
    # BOM order matters: UTF-32 prefixes UTF-16. bytes.decode preserves CRLF/tabs.
    for bom, encoding in [(codecs.BOM_UTF32_LE, 'utf-32'), (codecs.BOM_UTF32_BE, 'utf-32'),
                          (codecs.BOM_UTF8, 'utf-8-sig'), (codecs.BOM_UTF16_LE, 'utf-16'),
                          (codecs.BOM_UTF16_BE, 'utf-16')]:
        if data.startswith(bom):
            return data.decode(encoding), encoding
    if b'\0' in data:
        raise FileValidationError('Text contains binary data; use UTF-8 or a Unicode BOM.')
    try:
        return data.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        from charset_normalizer import from_bytes
        candidate = from_bytes(data).best()
        if not candidate or candidate.chaos > 0.1:
            raise FileValidationError('Cannot detect the text encoding. Save the file as UTF-8.')
        # Common Windows text is preferred over an arbitrary best single-byte guess.
        try:
            return data.decode('cp1252'), 'cp1252'
        except UnicodeDecodeError:
            return str(candidate), candidate.encoding


def extract_file(filename, data, mime='application/octet-stream'):
    ext = validate_envelope(filename, data, mime)
    try:
        if ext in {'.txt', '.md'}:
            text, encoding = decode_text(data)
            if any(ord(c) < 32 and c not in '\r\n\t\f' for c in text):
                raise FileValidationError('Text contains unsupported binary/control characters.')
            result = Extracted(text, [{'page': 1, 'start': 0, 'end': len(text)}], encoding)
        elif ext == '.docx':
            if not data.startswith(b'PK'):
                raise FileValidationError('Invalid DOCX signature.')
            with zipfile.ZipFile(BytesIO(data)) as archive:
                if not {'[Content_Types].xml', 'word/document.xml'} <= set(archive.namelist()):
                    raise FileValidationError('Invalid DOCX structure.')
                if sum(i.file_size for i in archive.infolist()) > settings.MAX_EXPANDED_DOCX:
                    raise FileValidationError('DOCX expanded size exceeds its configured safety limit.')
                if len(archive.infolist()) > 10000:
                    raise FileValidationError('DOCX has too many archive entries.')
                if archive.testzip():
                    raise FileValidationError('DOCX archive is corrupted.')
            from docx import Document
            from docx.text.paragraph import Paragraph
            from docx.table import Table
            doc = Document(BytesIO(data))
            blocks = []
            for child in doc.element.body.iterchildren():
                if child.tag.endswith('}p'):
                    paragraph = Paragraph(child, doc)
                    prefix = ''
                    if paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None:
                        prefix = '• '
                    blocks.append(prefix + paragraph.text)
                elif child.tag.endswith('}tbl'):
                    table = Table(child, doc)
                    for row in table.rows:
                        blocks.append('\t'.join(cell.text for cell in row.cells))
            text = '\n'.join(blocks)
            result = Extracted(text, [{'page': 1, 'start': 0, 'end': len(text)}])
        else:
            if not data.startswith(b'%PDF-'):
                raise FileValidationError('Invalid PDF signature.')
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise FileValidationError('Encrypted PDFs are not supported. Upload an unlocked PDF.')
            pieces, pages, offset = [], [], 0
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text(extraction_mode='layout') or ''
                if not text.strip():
                    text = ocr_pdf_page(data, number - 1)
                if not text.strip():
                    raise FileValidationError(f'No readable text found on PDF page {number}; OCR could not extract it.')
                if pieces:
                    pieces.append('\n\f\n')
                    offset += 3
                pages.append({'page': number, 'start': offset, 'end': offset + len(text)})
                pieces.append(text)
                offset += len(text)
            result = Extracted(''.join(pieces), pages)
        if not result.text.strip():
            raise FileValidationError('Document contains no readable text.')
        return result
    except FileValidationError:
        raise
    except Exception as exc:
        raise FileValidationError('Document is corrupted or cannot be decoded. Please upload a valid file.') from exc


def ocr_pdf_page(data, page_index):
    try:
        import fitz
        import pytesseract
        from PIL import Image
        with fitz.open(stream=data, filetype='pdf') as doc:
            page = doc[page_index]
            if page.rect.width * page.rect.height > 10_000_000:
                raise FileValidationError('PDF page is too large for safe OCR.')
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            image = Image.open(BytesIO(pix.tobytes('png')))
            return pytesseract.image_to_string(image, timeout=30)
    except FileValidationError:
        raise
    except Exception as exc:
        raise FileValidationError(
            f'PDF page {page_index + 1} requires OCR. Install Tesseract and the OCR dependencies; '
            'check that the page is readable.'
        ) from exc


def validate_image(data, mime):
    if not data or len(data) > settings.IMAGE_LIMIT:
        raise FileValidationError(f'Image must be non-empty and at most {settings.IMAGE_LIMIT / settings.MIB:g} MB.')
    from PIL import Image
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 25_000_000:
                raise FileValidationError('Image exceeds 25 million pixels.')
            detected = Image.MIME.get(image.format)
            if detected not in {'image/png', 'image/jpeg', 'image/webp'}:
                raise FileValidationError('Supported images: PNG, JPEG, WEBP.')
            if mime not in {detected, 'application/octet-stream', None, ''}:
                raise FileValidationError('Image contents do not match the MIME type.')
            image.verify()
            return detected
    except FileValidationError:
        raise
    except Exception as exc:
        raise FileValidationError('Image is corrupt or invalid.') from exc
