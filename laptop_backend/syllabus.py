from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET


SUPPORTED_SYLLABUS_SUFFIXES = {".txt", ".md", ".csv", ".pdf", ".docx", ".pptx", ".zip", ".scorm", ".html", ".htm", ".xml"}


def read_syllabus_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".csv"}:
        return path.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError:
            return ""
        document = Document(str(path))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)

    if suffix == ".pptx":
        return _read_pptx(path)

    if suffix in {".zip", ".scorm"}:
        return _read_scorm_zip(path)

    if suffix in {".html", ".htm", ".xml"}:
        return _strip_markup(path.read_text(encoding="utf-8", errors="ignore"))

    return ""


def _read_pptx(path: Path) -> str:
    texts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        slide_names = sorted(name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
        for slide_name in slide_names:
            try:
                root = ET.fromstring(archive.read(slide_name))
            except Exception:
                continue
            slide_text = []
            for node in root.iter():
                if node.tag.endswith("}t") and node.text:
                    slide_text.append(node.text.strip())
            if slide_text:
                texts.append(" ".join(slide_text))
    return "\n\n".join(texts)


def _read_scorm_zip(path: Path) -> str:
    texts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = [
            name for name in archive.namelist()
            if Path(name).suffix.lower() in {".html", ".htm", ".xml", ".txt", ".md"}
            and not name.lower().startswith("__macosx/")
        ]
        for name in sorted(names)[:120]:
            try:
                raw = archive.read(name).decode("utf-8", errors="ignore")
            except Exception:
                continue
            cleaned = _strip_markup(raw)
            if cleaned:
                texts.append(f"File: {name}\n{cleaned}")
    return "\n\n".join(texts)


def _strip_markup(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())
