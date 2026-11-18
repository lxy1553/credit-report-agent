"""Render the deterministic report Markdown as editable DOCX and shareable PDF."""

from pathlib import Path
import os
import re


def _blocks(markdown: str):
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            yield "title", line[2:].strip()
        elif line.startswith("## "):
            yield "heading", line[3:].strip()
        elif line.startswith("- "):
            yield "bullet", line[2:].strip()
        else:
            yield "body", line


def render_docx(markdown_path: Path, output_path: Path) -> Path:
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Inches, Pt, RGBColor
    except ImportError as exc:
        raise RuntimeError("DOCX 导出需要安装 python-docx") from exc

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.8)
    section.right_margin = Inches(0.8)

    for name in ("Normal", "Title", "Heading 1", "List Bullet"):
        style = document.styles[name]
        style.font.name = "Arial Unicode MS"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Arial Unicode MS")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial Unicode MS")
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Songti SC")
        language = style._element.rPr.find(qn("w:lang"))
        if language is None:
            language = OxmlElement("w:lang")
            style._element.rPr.append(language)
        language.set(qn("w:val"), "en-US")
        language.set(qn("w:eastAsia"), "zh-CN")
    title_ppr = document.styles["Title"]._element.find(qn("w:pPr"))
    if title_ppr is not None:
        title_border = title_ppr.find(qn("w:pBdr"))
        if title_border is not None:
            title_ppr.remove(title_border)
    document.styles["Normal"].font.size = Pt(10.5)
    document.styles["Normal"].paragraph_format.space_after = Pt(6)
    document.styles["Title"].font.size = Pt(22)
    document.styles["Title"].font.bold = True
    document.styles["Title"].font.color.rgb = RGBColor(20, 42, 74)
    document.styles["Heading 1"].font.size = Pt(14)
    document.styles["Heading 1"].font.bold = True
    document.styles["Heading 1"].font.color.rgb = RGBColor(20, 73, 115)

    def add_text(paragraph, text):
        run = paragraph.add_run(text)
        run.font.name = "Arial Unicode MS"
        run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Arial Unicode MS")
        run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Arial Unicode MS")
        run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Songti SC")
        language = OxmlElement("w:lang")
        language.set(qn("w:val"), "en-US")
        language.set(qn("w:eastAsia"), "zh-CN")
        run._element.get_or_add_rPr().append(language)
        return run

    for kind, text in _blocks(markdown_path.read_text(encoding="utf-8")):
        if kind == "title":
            # Word's Title style requires a plain subject title without punctuation.
            text = re.sub(r"\s*[·•]\s*", " ", text).strip()
            paragraph = document.add_paragraph(style="Title")
            paragraph.paragraph_format.space_after = Pt(14)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            add_text(paragraph, text)
        elif kind == "heading":
            add_text(document.add_paragraph(style="Heading 1"), text)
        elif kind == "bullet":
            add_text(document.add_paragraph(style="List Bullet"), text)
        else:
            add_text(document.add_paragraph(style="Normal"), text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path)
    return output_path


def render_pdf(markdown_path: Path, output_path: Path) -> Path:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate
    except ImportError as exc:
        raise RuntimeError("PDF 导出需要安装 reportlab") from exc

    font_candidates = [os.environ.get("REPORT_CJK_FONT"),
                       "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                       "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf",
                       "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttf"]
    font_path = next((Path(path) for path in font_candidates if path and Path(path).is_file()), None)
    if font_path is None:
        raise RuntimeError("PDF 中文字体未找到；请设置 REPORT_CJK_FONT 指向支持中文的 TTF 字体")
    try:
        pdfmetrics.registerFont(TTFont("ReportCJK", str(font_path)))
    except Exception:
        raise RuntimeError("REPORT_CJK_FONT 必须指向 ReportLab 支持的 TTF 字体文件") from None
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], fontName="ReportCJK",
                              fontSize=20, leading=28, textColor=colors.HexColor("#142A4A"),
                              alignment=TA_LEFT, spaceAfter=16))
    styles.add(ParagraphStyle(name="ReportHeading", parent=styles["Heading2"], fontName="ReportCJK",
                              fontSize=13, leading=20, textColor=colors.HexColor("#144973"),
                              spaceBefore=12, spaceAfter=6, keepWithNext=True))
    styles.add(ParagraphStyle(name="ReportBody", parent=styles["BodyText"], fontName="ReportCJK",
                              fontSize=10, leading=17, textColor=colors.HexColor("#263445"), spaceAfter=7))
    styles.add(ParagraphStyle(name="ReportBullet", parent=styles["ReportBody"], leftIndent=14,
                              firstLineIndent=-9, bulletIndent=0))
    story = []
    for kind, text in _blocks(markdown_path.read_text(encoding="utf-8")):
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if kind == "title":
            escaped = re.sub(r"\s*[·•]\s*", " ", escaped).strip()
            story.append(Paragraph(escaped, styles["ReportTitle"]))
        elif kind == "heading":
            story.append(Paragraph(escaped, styles["ReportHeading"]))
        elif kind == "bullet":
            story.append(Paragraph(f"&#8226;&nbsp; {escaped}", styles["ReportBullet"]))
        else:
            story.append(Paragraph(escaped, styles["ReportBody"]))
    if not story:
        raise ValueError("报告内容为空，无法导出")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(str(output_path), pagesize=A4, rightMargin=54, leftMargin=54,
                                 topMargin=50, bottomMargin=50, title="信贷指标日报")
    document.build(story)
    return output_path


def export_documents(markdown_path: Path, formats=("docx", "pdf")) -> list[Path]:
    stem = markdown_path.with_suffix("")
    outputs = []
    for output_format in formats:
        if output_format == "docx":
            outputs.append(render_docx(markdown_path, stem.with_suffix(".docx")))
        elif output_format == "pdf":
            outputs.append(render_pdf(markdown_path, stem.with_suffix(".pdf")))
        else:
            raise ValueError(f"不支持的报告格式：{output_format}")
    return outputs
