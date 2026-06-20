import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from werkzeug.utils import secure_filename


ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".docx", ".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
SOURCE_TYPES = {
    ".png": "图片",
    ".jpg": "图片",
    ".jpeg": "图片",
    ".docx": "Word",
    ".pdf": "PDF",
}
SIGNATURE_READ_BYTES = 8
MIN_EXTRACTED_TEXT_LENGTH = 2


class FileImportError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


@dataclass
class ImportedText:
    text: str
    source_type: str
    source_filename: str
    source_pages: str = ""


def extract_uploaded_file(file_storage, upload_dir, max_bytes):
    if file_storage is None or not file_storage.filename:
        raise FileImportError("请先选择要导入的文件。")

    safe_name = secure_filename(file_storage.filename)
    if not safe_name:
        raise FileImportError("文件名无效，请重新选择文件。")

    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise FileImportError("仅支持 PNG、JPG、JPEG、DOCX 和 PDF 文件。")

    temp_dir = tempfile.mkdtemp(prefix="task-import-", dir=upload_dir)
    temp_path = os.path.join(temp_dir, safe_name)
    try:
        _save_with_limit(file_storage, temp_path, max_bytes)
        _validate_file_signature(temp_path, extension)
        try:
            if extension in IMAGE_EXTENSIONS:
                text = extract_image_text(temp_path)
                pages = ""
            elif extension == ".docx":
                text = extract_docx_text(temp_path)
                pages = ""
            else:
                text, pages = extract_pdf_text(temp_path)
        except FileImportError:
            raise
        except Exception as exc:
            raise FileImportError("文件解析失败，请确认文件内容可读取后重试。") from exc

        text = text.strip()
        if len(text) < MIN_EXTRACTED_TEXT_LENGTH:
            raise FileImportError("未提取到可用文字，请确认文件内容清晰且包含文本。")
        return ImportedText(
            text=text,
            source_type=SOURCE_TYPES[extension],
            source_filename=safe_name,
            source_pages=pages,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def extract_image_text(path):
    try:
        from PIL import Image
    except ImportError as exc:
        raise FileImportError("OCR依赖 Pillow 未安装，请安装依赖后再导入图片。") from exc
    try:
        import pytesseract
    except ImportError as exc:
        raise FileImportError("OCR依赖 pytesseract 未安装，请安装依赖后再导入图片。") from exc

    try:
        with Image.open(path) as image:
            return pytesseract.image_to_string(image, lang="chi_sim+eng")
    except pytesseract.TesseractNotFoundError as exc:
        raise FileImportError("本机未检测到 Tesseract OCR，请安装后再导入图片。") from exc
    except Exception as exc:
        raise FileImportError("图片OCR失败，请确认图片清晰且格式正确。") from exc


def extract_docx_text(path):
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
    except ImportError as exc:
        raise FileImportError("Word解析依赖 python-docx 未安装，请安装依赖后再导入DOCX。") from exc

    try:
        document = Document(path)
        parts = []
        for child in document.element.body.iterchildren():
            if isinstance(child, CT_P):
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if text:
                    parts.append(text)
            elif isinstance(child, CT_Tbl):
                table = Table(child, document)
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
        return "\n".join(parts)
    except Exception as exc:
        raise FileImportError("DOCX解析失败，请确认文件未损坏且不是旧版DOC格式。") from exc


def extract_pdf_text(path):
    try:
        import fitz
    except ImportError as exc:
        raise FileImportError("PDF解析依赖 PyMuPDF 未安装，请安装依赖后再导入PDF。") from exc

    try:
        document = fitz.open(path)
        parts = []
        pages = []
        for index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            if text:
                parts.append(text)
                pages.append(f"第{index}页")
        document.close()
    except Exception as exc:
        raise FileImportError("PDF解析失败，请确认文件未损坏。") from exc

    if not parts:
        raise FileImportError("该PDF没有可提取文字，可能是扫描型PDF；当前阶段不对整份PDF执行OCR。")
    return "\n\n".join(parts), "、".join(pages)


def _save_with_limit(file_storage, temp_path, max_bytes):
    total = 0
    with open(temp_path, "wb") as output:
        while True:
            chunk = file_storage.stream.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise FileImportError(f"文件大小不能超过 {max_bytes // (1024 * 1024)} MB。")
            output.write(chunk)
    if total == 0:
        raise FileImportError("文件为空，请重新选择。")


def _validate_file_signature(path, extension):
    with open(path, "rb") as handle:
        signature = handle.read(SIGNATURE_READ_BYTES)

    if extension == ".png" and not signature.startswith(b"\x89PNG\r\n\x1a\n"):
        raise FileImportError("图片内容不是有效PNG文件。")
    if extension in (".jpg", ".jpeg") and not signature.startswith(b"\xff\xd8\xff"):
        raise FileImportError("图片内容不是有效JPEG文件。")
    if extension == ".pdf" and not signature.startswith(b"%PDF"):
        raise FileImportError("文件内容不是有效PDF。")
    if extension == ".docx":
        if not signature.startswith(b"PK"):
            raise FileImportError("文件内容不是有效DOCX。")
        try:
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise FileImportError("文件内容不是有效DOCX。")
        except zipfile.BadZipFile as exc:
            raise FileImportError("文件内容不是有效DOCX。") from exc
