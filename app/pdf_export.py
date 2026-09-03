"""把文档（Markdown 正文）导出为 PDF。

- 使用 reportlab 排版；中文字体从 macOS / Windows / Linux 常见路径自动探测并注册；
- 文件名规范（PRD AC-P4-03）：品牌-产品名-文档类型.pdf；
- reportlab 不可用或找不到任何中文字体时抛出 PdfExportError，由调用方降级处理。
"""
import io
import os
import re
import glob

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)

FONT_NAME = 'DocHubCJK'
_registered = False

# 各平台常见中文字体候选（TTC 用 subfontIndex=0）
FONT_CANDIDATES = [
    ('/System/Library/Fonts/Hiragino Sans GB.ttc', 0),
    ('/System/Library/Fonts/STHeiti Light.ttc', 0),
    ('/System/Library/Fonts/Supplemental/Songti.ttc', 0),
    ('/System/Library/Fonts/PingFang.ttc', 0),
    ('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', 0),
    ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', 0),
    ('C:/Windows/Fonts/msyh.ttc', 0),
    ('C:/Windows/Fonts/simhei.ttf', None),
]


class PdfExportError(Exception):
    pass


def register_cjk_font():
    """注册一个可用的中文字体，返回字体名；全部失败时抛 PdfExportError。"""
    global _registered
    if _registered:
        return FONT_NAME
    for path, idx in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            if idx is None:
                pdfmetrics.registerFont(TTFont(FONT_NAME, path))
            else:
                pdfmetrics.registerFont(TTFont(FONT_NAME, path, subfontIndex=idx))
            _registered = True
            return FONT_NAME
        except Exception:
            continue
    # 兜底：glob 扫描常见目录下任意中文字体
    for pattern in ('/System/Library/Fonts/*.ttc', '/usr/share/fonts/**/*.tt?',
                    os.path.expanduser('~/.fonts/*.tt?')):
        for path in glob.glob(pattern, recursive=True):
            try:
                pdfmetrics.registerFont(TTFont(FONT_NAME, path, subfontIndex=0))
                _registered = True
                return FONT_NAME
            except Exception:
                continue
    raise PdfExportError('未找到可用的中文字体，无法生成 PDF')


def _inline(text):
    """Markdown 行内格式转 reportlab 标签，并转义 XML 特殊字符。"""
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'`([^`]+)`', r'<font color="#2563EB">\1</font>', text)
    return text


def _styles():
    font = register_cjk_font()
    base = ParagraphStyle('base', fontName=font, fontSize=10.5, leading=18,
                          textColor=colors.HexColor('#1E293B'))
    styles = {
        'title': ParagraphStyle('title', parent=base, fontName=font, fontSize=20,
                                leading=30, spaceAfter=10,
                                textColor=colors.HexColor('#0F172A')),
        'meta': ParagraphStyle('meta', parent=base, fontSize=9, leading=15,
                               textColor=colors.HexColor('#64748B')),
        'h1': ParagraphStyle('h1', parent=base, fontName=font, fontSize=15,
                             leading=24, spaceBefore=14, spaceAfter=8,
                             textColor=colors.HexColor('#0F172A')),
        'h2': ParagraphStyle('h2', parent=base, fontName=font, fontSize=13,
                             leading=21, spaceBefore=12, spaceAfter=6,
                             textColor=colors.HexColor('#1E293B')),
        'h3': ParagraphStyle('h3', parent=base, fontName=font, fontSize=11.5,
                             leading=19, spaceBefore=10, spaceAfter=4,
                             textColor=colors.HexColor('#334155')),
        'p': base,
        'li': ParagraphStyle('li', parent=base, leftIndent=14, spaceAfter=3),
        'code': ParagraphStyle('code', parent=base, fontName=font, fontSize=9,
                               leading=14, leftIndent=8, rightIndent=8,
                               backColor=colors.HexColor('#F1F5F9'),
                               borderPadding=6, spaceBefore=4, spaceAfter=8,
                               textColor=colors.HexColor('#334155')),
    }
    return styles


def markdown_to_flowables(md, styles):
    """把简易 Markdown 转成 reportlab flowable 列表。"""
    flow = []
    lines = (md or '').split('\n')
    i = 0
    in_code = False
    code_buf = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith('```'):
            if in_code:
                flow.append(Paragraph(_inline('\n'.join(code_buf)), styles['code']))
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line.replace(' ', '&nbsp;'))
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        # 表格：连续的 | 行聚合成 Table
        if stripped.startswith('|'):
            tbl_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                row = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not re.match(r'^[\s:\-|]+$', lines[i].strip()):
                    tbl_lines.append(row)
                i += 1
            if tbl_lines:
                ncol = max(len(r) for r in tbl_lines)
                data = [[Paragraph(_inline(c), styles['p']) for c in row] + [''] * (ncol - len(row))
                        for row in tbl_lines]
                table = Table(data, repeatRows=1)
                table.setStyle(TableStyle([
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('FONTNAME', (0, 0), (-1, -1), FONT_NAME),
                    ('FONTSIZE', (0, 0), (-1, -1), 9),
                    ('LEFTPADDING', (0, 0), (-1, -1), 5),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ]))
                flow.append(Spacer(1, 4))
                flow.append(table)
                flow.append(Spacer(1, 8))
            continue

        if stripped.startswith('### '):
            flow.append(Paragraph(_inline(stripped[4:]), styles['h3']))
        elif stripped.startswith('## '):
            flow.append(Paragraph(_inline(stripped[3:]), styles['h2']))
        elif stripped.startswith('# '):
            flow.append(Paragraph(_inline(stripped[2:]), styles['h1']))
        elif stripped.startswith(('- ', '* ')):
            flow.append(Paragraph('• ' + _inline(stripped[2:]), styles['li']))
        elif re.match(r'^\d+\.\s', stripped):
            flow.append(Paragraph(_inline(stripped), styles['li']))
        elif stripped.startswith('> '):
            flow.append(Paragraph(_inline(stripped[2:]), styles['li']))
        else:
            # 合并连续普通段落行
            buf = [stripped]
            while i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if (not nxt or nxt.startswith(('#', '- ', '* ', '```', '|', '> '))
                        or re.match(r'^\d+\.\s', nxt)):
                    break
                buf.append(nxt)
                i += 1
            flow.append(Paragraph(_inline(' '.join(buf)), styles['p']))
            flow.append(Spacer(1, 4))
        i += 1

    if in_code and code_buf:
        flow.append(Paragraph(_inline('\n'.join(code_buf)), styles['code']))
    return flow


def build_pdf_bytes(doc, type_label=''):
    """根据 Document 实例生成 PDF 字节流。"""
    styles = _styles()
    buf = io.BytesIO()
    brand = doc.product.brand.name if doc.product and doc.product.brand else 'DocHub'
    product = doc.product.name if doc.product else ''

    doc_template = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=doc.title, author=brand,
    )
    flow = []
    flow.append(Paragraph(_inline(doc.title), styles['title']))
    meta_line = f'品牌：{brand}　|　产品：{product}　|　类型：{type_label}'
    if doc.created_at:
        meta_line += f'　|　收录日期：{doc.created_at.strftime("%Y-%m-%d")}'
    flow.append(Paragraph(meta_line, styles['meta']))
    if doc.original_url:
        flow.append(Paragraph(f'官方原文：<link href="{doc.original_url}" '
                              f'color="#2563EB">{doc.original_url}</link>', styles['meta']))
    flow.append(Spacer(1, 6))
    flow.append(Table([['']], colWidths=[170 * mm], style=TableStyle([
        ('LINEBELOW', (0, 0), (-1, -1), 0.8, colors.HexColor('#CBD5E1')),
    ])))
    flow.append(Spacer(1, 10))

    body = doc.content or f'# {doc.title}\n\n{doc.description or ""}'
    # 去掉正文里与封面重复的首个一级标题
    body = re.sub(r'^\s*#\s+[^\n]*\n', '', body, count=1)
    flow.extend(markdown_to_flowables(body, styles))

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(FONT_NAME, 8)
        canvas.setFillColor(colors.HexColor('#94A3B8'))
        canvas.drawCentredString(A4[0] / 2, 10 * mm,
                                 f'DocHub 官方文档聚合阅读平台 · {brand} · 第 {_doc.page} 页')
        canvas.restoreState()

    doc_template.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
