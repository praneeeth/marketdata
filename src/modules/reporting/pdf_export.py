"""Export a detailed report to PDF, with faithful HTML layout.

markdown -> HTML (python-markdown) -> PDF.
- Main engine **WeasyPrint**: a real CSS layout engine with wrapping, pagination and page numbers; uses system fonts; close to the web page.
- Falls back to **xhtml2pdf** when WeasyPrint is unavailable (missing system libraries such as pango): pure Python, plainer layout but always works
  (CJK text, if any, uses reportlab's built-in STSong-Light CID font).
(Chromium/page.pdf could give higher fidelity later, but needs a browser installed, so it isn't a default dependency.)
"""

from __future__ import annotations

import io
import logging
from html import escape

import markdown as _markdown

from src.platform.compliance import LONG_DISCLAIMER, ensure_guarded, guard_title

logger = logging.getLogger(__name__)


# ---- WeasyPrint (main) ----

_REPORT_CSS = """
@page {
  size: A4; margin: 1.7cm 1.5cm;
  @bottom-center { content: "Educational/informational only. Not investment advice. Page " counter(page) " / " counter(pages);
                   font-size: 8pt; color: #9ca3af; }
}
body { font-family: "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", "Hiragino Sans GB", sans-serif;
       font-size: 10.5pt; line-height: 1.75; color: #1f2937; }
.doc-title { font-size: 18pt; font-weight: 700; color: #0f172a;
             border-bottom: 2pt solid #e11d48; padding-bottom: 8pt; margin: 0 0 14pt; }
h1 { font-size: 14.5pt; color: #0f172a; margin: 16pt 0 6pt; padding-left: 8pt;
     border-left: 3pt solid #2563eb; page-break-after: avoid; }
h2 { font-size: 12.5pt; color: #1e293b; margin: 13pt 0 5pt; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #334155; margin: 10pt 0 4pt; page-break-after: avoid; }
p { margin: 5pt 0; }
ul, ol { margin: 5pt 0 5pt 16pt; }
li { margin: 2.5pt 0; }
strong, b { font-weight: 700; color: #0f172a; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
th, td { border: 0.5pt solid #d1d5db; padding: 5pt 8pt; font-size: 9.5pt; text-align: left;
         vertical-align: top; }
th { background: #f1f5f9; font-weight: 600; }
hr { border: none; border-top: 0.5pt solid #e5e7eb; margin: 12pt 0; }
blockquote { border-left: 3pt solid #cbd5e1; margin: 6pt 0; padding: 2pt 0 2pt 10pt; color: #475569; }
code { background: #f1f5f9; padding: 1pt 3pt; border-radius: 2pt; }
a { color: #2563eb; text-decoration: none; }
"""


def _md_to_html(markdown_text: str) -> str:
    return _markdown.markdown(
        markdown_text or "", extensions=["tables", "fenced_code", "sane_lists"]
    )


def _render_weasyprint(title: str, body_html: str) -> bytes:
    from weasyprint import HTML

    doc = (
        '<html><head><meta charset="utf-8"><style>' + _REPORT_CSS + "</style></head><body>"
        + f'<div class="doc-title">{escape((title or "Deep research").strip())}</div>'
        + body_html
        + "</body></html>"
    )
    return HTML(string=doc).write_pdf()


# ---- xhtml2pdf (fallback; pure Python, no system dependencies) ----

_FALLBACK_CSS = """
@page { size: A4; margin: 1.6cm 1.5cm; }
body { font-family: STSong-Light; font-size: 10.5pt; line-height: 1.6; color: #1f2937; }
.doc-title { font-size: 16pt; font-weight: bold; border-bottom: 1.5pt solid #d1d5db;
             padding-bottom: 6pt; margin-bottom: 12pt; }
h1 { font-size: 14pt; margin: 12pt 0 5pt; } h2 { font-size: 12pt; margin: 10pt 0 4pt; }
h3 { font-size: 11pt; margin: 8pt 0 3pt; } p { margin: 4pt 0; }
ul, ol { margin: 4pt 0 4pt 6pt; } li { margin: 2pt 0; }
table { border-collapse: collapse; width: 100%; } th, td { border: 0.5pt solid #d1d5db; padding: 4pt 6pt; }
"""


def _render_xhtml2pdf(title: str, body_html: str) -> bytes:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from xhtml2pdf import pisa

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception:
        pass
    doc = (
        '<html><head><meta charset="utf-8"><style>' + _FALLBACK_CSS + "</style></head><body>"
        + f'<div class="doc-title">{escape((title or "Deep research").strip())}</div>'
        + body_html
        + '<div style="margin-top:14pt;font-size:8.5pt;color:#9ca3af;">'
        + escape(LONG_DISCLAIMER)
        + "</div></body></html>"
    )
    buf = io.BytesIO()
    pisa.CreatePDF(src=doc, dest=buf, encoding="utf-8")
    return buf.getvalue()


_ANALYST_SECTIONS = [
    ("market", "Technical analyst"),
    ("social", "Sentiment analyst"),
    ("news", "News analyst"),
    ("fundamentals", "Fundamentals analyst"),
]


def assemble_report_markdown(raw_data: dict) -> str:
    """Build the full report markdown from raw_data, with the same sections as the details page (buildAnalysisSections).

    Same order as the details page: decision summary -> PM decision (+ trader) -> the 4 analysts in full -> bull/bear debate in full (+ research manager's ruling)
    -> risk debate in full (+ risk ruling). Fuller than the `content` field (which omits the 4 analysts and the debates).
    """
    rd = raw_data or {}
    if "suggestion" not in rd:
        return _assemble_research_markdown(rd)
    sug = rd.get("suggestion") or {}
    reports = rd.get("analyst_reports") or {}
    debate = rd.get("debate_history") or {}
    risk = rd.get("risk_debate") or {}
    parts: list[str] = []

    label = sug.get("action_label") or "Hold"
    head = f"**{label}**"
    conf = sug.get("confidence")
    if conf is not None:
        try:
            head += f" · confidence {float(conf):.1f}/10"
        except (TypeError, ValueError):
            pass
    parts.append(f"## Final decision\n\n{head}\n")

    final_decision = (rd.get("final_decision") or "").strip()
    trader = (rd.get("trader_plan") or "").strip()
    if final_decision or trader:
        body = final_decision
        if trader:
            body = (body + "\n\n" if body else "") + f"### 💼 Trader's plan\n\n{trader}"
        parts.append(f"## PM decision\n\n{body}\n")

    for key, title in _ANALYST_SECTIONS:
        txt = (reports.get(key) or "").strip()
        if txt:
            parts.append(f"## {title}\n\n{txt}\n")

    dh = (debate.get("history") or "").strip()
    if dh:
        seg = dh
        jd = (debate.get("judge_decision") or "").strip()
        if jd:
            seg += f"\n\n### ⚖️ Research manager's ruling\n\n{jd}"
        parts.append(f"## Bull vs bear debate\n\n{seg}\n")

    rh = (risk.get("history") or "").strip()
    rjd = (rd.get("risk_judgment") or risk.get("judge_decision") or "").strip()
    if rh or rjd:
        seg = rh
        if rjd:
            seg += (("\n\n" if seg else "") + f"### 🛡️ Risk ruling\n\n{rjd}")
        parts.append(f"## Risk debate\n\n{seg}\n")

    return "\n".join(parts).strip()


_RESEARCH_SECTIONS = [
    ("market", "Market and technical analyst"),
    ("social", "Sentiment analyst"),
    ("news", "News analyst"),
    ("fundamentals", "Fundamentals analyst"),
]


def _assemble_research_markdown(rd: dict) -> str:
    """Research-only report: summary, analyst reports, bull/bear debate. No decision."""
    parts: list[str] = []
    summary = (rd.get("research_summary") or "").strip()
    if summary:
        parts.append(f"## Research summary\n\n{summary}\n")
    reports = rd.get("analyst_reports") or {}
    for key, title in _RESEARCH_SECTIONS:
        txt = (reports.get(key) or "").strip()
        if txt:
            parts.append(f"## {title}\n\n{txt}\n")
    debate = rd.get("debate_history") or {}
    history = (debate.get("history") or "").strip()
    if history:
        parts.append(f"## Bull and bear debate\n\n{history}\n")
    return "\n".join(parts).strip()


def render_analysis_pdf(title: str, markdown_text: str) -> bytes:
    """Analysis report markdown -> PDF bytes (vector text, copyable). WeasyPrint first, falling back to xhtml2pdf."""
    title = guard_title(title, surface="pdf_title", fallback="Deep research")
    markdown_text = ensure_guarded(markdown_text, surface="pdf")
    body_html = _md_to_html(markdown_text)
    try:
        return _render_weasyprint(title, body_html)
    except Exception as e:  # WeasyPrint missing system libraries / render error -> fallback
        logger.warning("[PDF export] WeasyPrint unavailable; falling back to xhtml2pdf: %s", e)
        return _render_xhtml2pdf(title, body_html)
