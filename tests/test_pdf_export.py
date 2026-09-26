"""Export a detailed report to PDF (server-side: WeasyPrint, falling back to xhtml2pdf)."""

from __future__ import annotations

import io


def _weasyprint_renders() -> bool:
    """Whether WeasyPrint can really render (needs system libraries such as pango). Otherwise xhtml2pdf is the fallback."""
    try:
        from weasyprint import HTML

        HTML(string="<p>test</p>").write_pdf()
        return True
    except Exception:
        return False


_WEASY = _weasyprint_renders()


def test_render_pdf_returns_valid_bytes_with_text():
    """markdown -> PDF: returns valid PDF bytes, with the text in the text layer (copyable)."""
    from src.modules.reporting.pdf_export import render_analysis_pdf

    md = "# Tata Motors (TATAMOTORS) deep research\n\n**Final decision: Hold**\n\n- Bull: earnings turning point confirmed\n- Bear: valuation rich"
    data = render_analysis_pdf("[Deep research] Tata Motors (TATAMOTORS): Hold", md)
    assert isinstance(data, (bytes, bytearray))
    assert bytes(data[:4]) == b"%PDF"
    assert len(data) > 1500

    if not _WEASY:
        return  # the xhtml2pdf fallback's text layer isn't checked here; only the WeasyPrint path guarantees copyable text

    from pypdf import PdfReader

    txt = PdfReader(io.BytesIO(bytes(data))).pages[0].extract_text() or ""
    assert "Tata Motors" in txt
    assert "Hold" in txt


def test_render_pdf_handles_empty_markdown():
    """An empty body doesn't break; still returns a valid PDF (with at least the title)."""
    from src.modules.reporting.pdf_export import render_analysis_pdf

    data = render_analysis_pdf("Title", "")
    assert bytes(data[:4]) == b"%PDF"


def test_assemble_report_markdown_mirrors_detail_page_sections():
    """The report built from raw_data has every section of the detail page: PM/trader/4 analysts in full/bull-bear debate in full/risk debate in full."""
    from src.modules.reporting.pdf_export import assemble_report_markdown

    raw = {
        "suggestion": {"action_label": "Hold", "confidence": 5.0},
        "final_decision": "PM decision body XYZ",
        "trader_plan": "Trader plan body XYZ",
        "analyst_reports": {
            "market": "Technical analysis body XYZ", "social": "Sentiment analysis body XYZ",
            "news": "News analysis body XYZ", "fundamentals": "Fundamentals analysis body XYZ",
        },
        "debate_history": {"history": "Bull view AAA Bear view BBB", "judge_decision": "Research manager ruling XYZ"},
        "risk_debate": {"history": "Aggressive CCC Conservative DDD", "judge_decision": "Risk ruling XYZ"},
    }
    md = assemble_report_markdown(raw)
    for must in [
        "PM decision body XYZ", "Trader plan body XYZ",
        "Technical analysis body XYZ", "Sentiment analysis body XYZ", "News analysis body XYZ", "Fundamentals analysis body XYZ",
        "Bull view AAA", "Bear view BBB", "Research manager ruling XYZ",
        "Aggressive CCC", "Risk ruling XYZ",
        "Technical analyst", "Bull vs bear debate", "Risk debate",
    ]:
        assert must in md, f"missing: {must}"


def _mem_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import src.platform.persistence.models  # noqa: F401
    from src.platform.persistence.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_pdf_endpoint_returns_full_detail_content():
    """Endpoint: returns an application/pdf attachment with the full detail-page content (analyst/debate text from raw_data, not just the content summary)."""
    from src.modules.automation.api import agents
    from src.platform.persistence.models import AnalysisHistory

    db = _mem_db()
    try:
        db.add(AnalysisHistory(
            agent_name="tradingagents", stock_symbol="TATAMOTORS",
            analysis_date="2026-06-20", title="[Deep research] Tata Motors (TATAMOTORS): Hold",
            content="# Summary\n\n**Hold**",  # content is the short version, without the text below
            raw_data={
                "suggestion": {"action_label": "Hold", "confidence": 5.0},
                "final_decision": "PM decision body",
                "analyst_reports": {"market": "Technical analysis body UNIQUE", "fundamentals": "Fundamentals body"},
                "debate_history": {"history": "Bull view UNIQUE Bear view", "judge_decision": "Research manager ruling"},
                "risk_debate": {"history": "Aggressive Conservative", "judge_decision": "Risk ruling"},
            },
        ))
        db.commit()
        resp = agents.export_tradingagents_analysis_pdf(
            stock_symbol="TATAMOTORS", analysis_date="2026-06-20", db=db)
        assert resp.media_type == "application/pdf"
        assert bytes(resp.body[:4]) == b"%PDF"
        assert "attachment" in resp.headers["content-disposition"]

        if not _WEASY:
            return  # the text layer is only extracted on the WeasyPrint path; test_assemble_* covers content assembly

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(bytes(resp.body)))
        txt = "\n".join((p.extract_text() or "") for p in reader.pages)
        # The analyst/debate text that isn't in the content summary really made it into the PDF
        assert "Technical analysis body UNIQUE" in txt
        assert "Bull view UNIQUE" in txt
    finally:
        db.close()


def test_pdf_endpoint_404_when_missing():
    """Endpoint: no record -> HTTP 404."""
    import pytest
    from fastapi import HTTPException

    from src.modules.automation.api import agents

    db = _mem_db()
    try:
        with pytest.raises(HTTPException) as ei:
            agents.export_tradingagents_analysis_pdf(
                stock_symbol="000000", analysis_date="2026-06-20", db=db)
        assert ei.value.status_code == 404
    finally:
        db.close()
