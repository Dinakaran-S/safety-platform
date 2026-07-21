"""
SENTINEL AI - PDF Export Engine

Generates professional, branded PDF exports for:
  1. Incident Report   - full detail for a single HIGH severity event
  2. Plant Status      - snapshot of all zones, alerts, workers, equipment
  3. Alert Feed        - paginated list of recent alerts with key fields
  4. Audit Log         - agent decision trail for regulatory compliance

Uses fpdf2 (no external paid APIs, no headless browser).
Industrial Blue color scheme matches the dashboard palette.
"""
from __future__ import annotations

import time
from io import BytesIO
from typing import Any

from fpdf import FPDF, XPos, YPos

# ── Brand palette (Industrial Blue) ──────────────────────────────────────────
C_BG          = (6,   16,  26)   # dark navy  #06101a
C_SURFACE     = (11,  25,  38)   # deep blue  #0b1926
C_BLUE        = (0,   96,  184)  # industrial blue #0060b8
C_BLUE_LIGHT  = (0,   150, 255)  # accent     #0096ff
C_RED         = (212, 32,  32)   # alert red
C_AMBER       = (201, 112, 0)    # alert amber
C_GREEN       = (0,   122, 69)   # safe green
C_TEXT        = (15,  37,  64)   # dark navy text
C_DIM         = (61,  96,  128)  # muted blue-grey
C_WHITE       = (255, 255, 255)
C_LIGHT_BG    = (228, 239, 249)  # light blue bg #e4eff9
C_BORDER      = (184, 208, 232)  # blue-tinted border


def _sev_color(sev: str) -> tuple:
    return {
        "HIGH": C_RED,
        "MEDIUM": C_AMBER,
        "LOW": C_GREEN,
        "INFO": C_DIM,
        "CRITICAL": (180, 0, 40),
    }.get(sev, C_DIM)


def _fmt_inr(n: int | float) -> str:
    n = int(n or 0)
    if n >= 10_000_000:
        return f"Rs.{n/10_000_000:.2f} Cr"
    if n >= 100_000:
        return f"Rs.{n/100_000:.2f} L"
    return f"Rs.{n:,}"


def _ts(ts: float | None = None) -> str:
    return time.strftime("%d %b %Y, %H:%M:%S", time.localtime(ts or time.time()))


def _safe(text: str, max_len: int = 200) -> str:
    """Strip non-latin-1 characters so fpdf core fonts don't crash."""
    text = str(text or "")[:max_len]
    return text.encode("latin-1", errors="replace").decode("latin-1")


# ── Base PDF class with shared header/footer ──────────────────────────────────
class SentinelPDF(FPDF):
    def __init__(self, title: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(16, 16, 16)
        self._doc_title = title
        self.add_page()
        self._draw_cover_header()

    # ── Header stripe ────────────────────────────────────────────────────────
    def _draw_cover_header(self):
        self.set_fill_color(*C_BG)
        self.rect(0, 0, 210, 30, "F")
        # Brand name
        self.set_font("Helvetica", "B", 18)
        self.set_text_color(*C_BLUE_LIGHT)
        self.set_xy(16, 8)
        self.cell(0, 8, "SENTINEL AI", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        # Subtitle
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*C_DIM)
        self.set_x(16)
        self.cell(0, 5, _safe("INDUSTRIAL SAFETY INTELLIGENCE PLATFORM"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        # Blue rule line
        self.set_draw_color(*C_BLUE)
        self.set_line_width(0.6)
        self.line(0, 30, 210, 30)
        self.ln(8)
        # Document title
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*C_TEXT)
        self.cell(0, 8, self._doc_title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        # Generated timestamp
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*C_DIM)
        self.cell(0, 5, f"Generated: {_ts()}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(4)
        self._thin_rule()
        self.ln(4)

    def header(self):
        if self.page_no() == 1:
            return
        # Subsequent pages: slim header
        self.set_fill_color(*C_BG)
        self.rect(0, 0, 210, 12, "F")
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*C_BLUE_LIGHT)
        self.set_xy(16, 4)
        self.cell(90, 5, "SENTINEL AI")
        self.set_text_color(*C_DIM)
        self.set_font("Helvetica", "", 8)
        self.cell(0, 5, _safe(self._doc_title), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*C_BLUE)
        self.set_line_width(0.4)
        self.line(0, 12, 210, 12)
        self.ln(4)

    def footer(self):
        self.set_y(-14)
        self.set_draw_color(*C_BORDER)
        self.set_line_width(0.3)
        self.line(16, self.get_y(), 194, self.get_y())
        self.ln(2)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*C_DIM)
        self.cell(0, 5,
            _safe(f"SENTINEL AI  -  Industrial Safety Intelligence Platform  -  Page {self.page_no()}"),
            align="C")

    # ── Shared helpers ────────────────────────────────────────────────────────
    def _thin_rule(self, color=C_BORDER):
        self.set_draw_color(*color)
        self.set_line_width(0.25)
        self.line(16, self.get_y(), 194, self.get_y())

    def _section(self, title: str):
        self.set_fill_color(*C_LIGHT_BG)
        self.rect(16, self.get_y(), 178, 7, "F")
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*C_BLUE)
        self.set_x(18)
        self.cell(0, 7, title.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)

    def _kv(self, key: str, value: str, value_color=None):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*C_DIM)
        self.set_x(18)
        self.cell(52, 6, _safe(key))
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*(value_color or C_TEXT))
        self.cell(0, 6, _safe(str(value)), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def _sev_badge(self, sev: str, x: float, y: float, w: float = 26):
        col = _sev_color(sev)
        self.set_fill_color(*col)
        self.set_text_color(*C_WHITE)
        self.set_font("Helvetica", "B", 8)
        self.set_xy(x, y)
        self.cell(w, 6, sev, align="C", fill=True)
        self.set_text_color(*C_TEXT)

    def _body(self, text: str, indent: int = 18):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*C_TEXT)
        self.set_x(indent)
        self.multi_cell(178 - (indent - 16), 5, _safe(text, 600))
        self.ln(1)

    def _table_header(self, cols: list[tuple[str, float, str]]):
        """cols: [(label, width_mm, align)]"""
        self.set_fill_color(*C_BG)
        self.set_text_color(*C_BLUE_LIGHT)
        self.set_font("Helvetica", "B", 8)
        self.set_x(16)
        for label, w, align in cols:
            self.cell(w, 6, label, border=0, align=align, fill=True)
        self.ln()
        self._thin_rule(C_BLUE)
        self.ln(1)

    def _table_row(self, cells: list[tuple[str, float, str]], shade: bool = False):
        if shade:
            self.set_fill_color(*C_LIGHT_BG)
            self.rect(16, self.get_y(), 178, 5.5, "F")
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*C_TEXT)
        self.set_x(16)
        for text, w, align in cells:
            self.cell(w, 5.5, _safe(str(text), 42), border=0, align=align)
        self.ln()


# ── 1. INCIDENT REPORT PDF ────────────────────────────────────────────────────
def generate_incident_report(report: dict, alert: dict | None = None) -> bytes:
    pdf = SentinelPDF("PRELIMINARY INCIDENT REPORT")

    sev = report.get("severity", "UNKNOWN")
    col = _sev_color(sev)

    # Severity banner
    pdf.set_fill_color(*col)
    pdf.rect(16, pdf.get_y(), 178, 10, "F")
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*C_WHITE)
    pdf.set_x(18)
    prob = report.get("calibrated_probability", 0)
    pdf.cell(0, 10, f"{sev} SEVERITY  -  Probability: {prob*100:.1f}%",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    # ── Overview ──
    pdf._section("Report Overview")
    pdf._kv("Report ID",       report.get("report_id", "--"))
    pdf._kv("Zone",            f"{report.get('zone_id')} - {report.get('zone_name')}")
    pdf._kv("Generated At",    _ts(report.get("generated_at")))
    pdf._kv("Sim Hour",        f"{report.get('sim_hour', 0):.2f}h")
    lt = report.get("estimated_lead_time_minutes")
    pdf._kv("Lead Time",       f"~{lt} minutes" if lt else "--")
    pdf.ln(3)

    # ── Contributing factors ──
    pdf._section("Contributing Factors & Rule Trace")
    for factor in (report.get("contributing_factors") or [])[:15]:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*C_TEXT)
        pdf.set_x(20)
        pdf.cell(4, 5, ">")
        pdf.multi_cell(172, 5, _safe(str(factor), 300))
    pdf.ln(2)

    # ── Grounded precedent ──
    prec = report.get("grounded_precedent")
    if prec:
        pdf._section("Grounded Historical Precedent")
        pdf.set_fill_color(*C_LIGHT_BG)
        pdf.set_draw_color(*C_BLUE)
        pdf.set_line_width(0.8)
        pdf.line(18, pdf.get_y(), 18, pdf.get_y() + 22)
        pdf.set_line_width(0.25)
        pdf._body(_safe(str(prec), 600), indent=22)
    pdf.ln(2)

    # ── Action checklist ──
    pdf._section("Recommended Actions")
    actions = report.get("action_checklist") or []
    for i, item in enumerate(actions):
        pdf.set_fill_color(*C_BLUE if i % 2 == 0 else C_BG)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*C_BLUE)
        pdf.set_x(18)
        pdf.cell(10, 6, f"[{item.get('rank', i+1)}]")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*C_TEXT)
        pdf.multi_cell(162, 6, _safe(item.get("action", "")))
    pdf.ln(3)

    # ── Feature attribution (from alert if available) ──
    if alert:
        attrs = alert.get("feature_attribution", {})
        if attrs:
            pdf._section("Feature Attribution (AI Explainability)")
            pdf._table_header([("Feature", 80, "L"), ("Contribution", 40, "R"), ("Direction", 58, "C")])
            for i, (k, v) in enumerate(sorted(attrs.items(), key=lambda x: -abs(x[1]))):
                direction = "^ Increases risk" if v > 0 else "v Reduces risk"
                pdf._table_row([(k, 80, "L"), (f"{v:+.4f}", 40, "R"), (direction, 58, "C")], shade=i%2==0)
            pdf.ln(3)

    # ── Financial impact ──
    fi = {}
    if alert and alert.get("prediction"):
        fi = alert["prediction"].get("financial_impact") or {}
    if fi:
        pdf._section("Financial Impact Assessment")
        cols = [("Line Item", 90, "L"), ("Amount (INR)", 88, "R")]
        pdf._table_header(cols)
        items = [
            ("Production Loss (Expected)", fi.get("production_loss_inr", 0)),
            ("Equipment Repair Cost", fi.get("repair_cost_inr", 0)),
            ("Regulatory Fine (OISD)", fi.get("regulatory_fine_inr", 0)),
            ("Environmental Remediation", fi.get("environmental_damage_inr", 0)),
        ]
        for i, (label, val) in enumerate(items):
            pdf._table_row([(label, 90, "L"), (_fmt_inr(val), 88, "R")], shade=i%2==0)
        # Total row
        pdf.set_fill_color(*C_BG)
        pdf.rect(16, pdf.get_y(), 178, 6.5, "F")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*C_BLUE_LIGHT)
        pdf.set_x(16)
        pdf.cell(90, 6.5, "TOTAL EXPECTED LOSS")
        pdf.cell(88, 6.5, _fmt_inr(fi.get("total_loss_inr", 0)), align="R",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        # Prevented row
        pdf.set_fill_color(*C_GREEN)
        pdf.rect(16, pdf.get_y(), 178, 6.5, "F")
        pdf.set_text_color(*C_WHITE)
        pdf.set_x(16)
        pdf.cell(90, 6.5, "LOSS PREVENTED (if acting now)")
        pdf.cell(88, 6.5, _fmt_inr(fi.get("loss_prevented_if_acted_now_inr", 0)), align="R",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)

    # ── SCADA note ──
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(*C_DIM)
    pdf._thin_rule()
    pdf.ln(2)
    pdf.set_x(16)
    pdf.multi_cell(178, 4.5,
        _safe("STRUCTURED OUTPUT READY FOR SCADA/ALERTING SYSTEM INTEGRATION - "
        "This report is generated for dashboard display and export. "
        "No live notification or control-system write is performed by this prototype."))

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()


# ── 2. PLANT STATUS PDF ───────────────────────────────────────────────────────
def generate_plant_status(state: dict, alerts: list, workers: list, equipment: dict) -> bytes:
    pdf = SentinelPDF("PLANT STATUS REPORT")

    safety_score = state.get("plant_safety_score", 0)
    col = C_GREEN if safety_score >= 90 else C_AMBER if safety_score >= 70 else C_RED
    pdf.set_font("Helvetica", "B", 32)
    pdf.set_text_color(*col)
    pdf.set_x(16)
    pdf.cell(40, 16, str(round(safety_score)), new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*C_DIM)
    pdf.set_y(pdf.get_y() + 4)
    pdf.set_x(58)
    pdf.cell(0, 5, "/ 100  PLANT SAFETY SCORE", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(58)
    pdf.cell(0, 5, f"Sim Hour: {state.get('sim_hour', 0):.2f}h  -  Tick: {state.get('tick_count', 0)}")
    pdf.ln(8)

    # ── Zone status table ──
    pdf._section("Zone Risk Status")
    pdf._table_header([("Zone", 14, "C"), ("Name", 52, "L"), ("Severity", 24, "C"),
                        ("Probability", 24, "R"), ("Active Permits", 66, "L")])
    for i, zone in enumerate(state.get("zones") or []):
        risk = zone.get("risk") or {}
        ctx  = zone.get("context") or {}
        sev  = risk.get("severity", "INFO")
        prob = risk.get("calibrated_probability", 0)
        permits = ", ".join(ctx.get("active_permit_types") or []) or "--"
        if sev in ("HIGH", "MEDIUM"):
            pdf.set_fill_color(*(_sev_color(sev)[0], _sev_color(sev)[1], _sev_color(sev)[2]))
            pdf.rect(16, pdf.get_y(), 178, 5.5, "F")
            pdf.set_text_color(*C_WHITE if sev == "HIGH" else C_TEXT)
        else:
            if i % 2 == 0:
                pdf.set_fill_color(*C_LIGHT_BG)
                pdf.rect(16, pdf.get_y(), 178, 5.5, "F")
            pdf.set_text_color(*C_TEXT)
        pdf.set_font("Helvetica", "B" if sev in ("HIGH","MEDIUM") else "", 8)
        pdf.set_x(16)
        pdf.cell(14, 5.5, zone.get("zone_id",""), align="C")
        pdf.cell(52, 5.5, _safe(zone.get("name",""), 25))
        pdf.cell(24, 5.5, sev, align="C")
        pdf.cell(24, 5.5, f"{prob*100:.1f}%" if prob > 0 else "--", align="R")
        pdf.cell(66, 5.5, _safe(permits, 35))
        pdf.ln()
    pdf.ln(4)

    # ── Recent HIGH/MEDIUM alerts ──
    high_alerts = [a for a in alerts[:50] if a.get("severity") in ("HIGH", "MEDIUM")][:10]
    if high_alerts:
        pdf._section(f"Recent High-Priority Alerts ({len(high_alerts)} shown)")
        pdf._table_header([("Zone", 14, "C"), ("Severity", 24, "C"), ("Probability", 24, "R"),
                            ("Sim Hour", 20, "C"), ("Lead Time", 22, "C"), ("Precedent", 74, "L")])
        for i, a in enumerate(high_alerts):
            sev  = a.get("severity", "INFO")
            prob = a.get("calibrated_probability", 0)
            lt   = a.get("estimated_lead_time_minutes")
            cite = ""
            if a.get("briefing"):
                cite = ", ".join(a["briefing"].get("source_citation") or [])
            pdf._table_row([
                (a.get("zone_id",""), 14, "C"),
                (sev, 24, "C"),
                (f"{prob*100:.1f}%", 24, "R"),
                (f"{a.get('sim_hour',0):.2f}h", 20, "C"),
                (f"~{lt}m" if lt else "--", 22, "C"),
                (cite[:30] or "--", 74, "L"),
            ], shade=i%2==0)
        pdf.ln(4)

    # ── Workers ──
    if workers:
        pdf._section("Worker Safety Summary")
        danger = [w for w in workers if w.get("status") == "danger"]
        at_risk = [w for w in workers if w.get("status") == "at_risk"]
        safe = [w for w in workers if w.get("status") == "safe"]
        pdf._kv("In danger", str(len(danger)) + (f"  >>  {', '.join(w['name'] for w in danger)}" if danger else ""), C_RED)
        pdf._kv("At risk",   str(len(at_risk)) + (f"  >>  {', '.join(w['name'] for w in at_risk)}" if at_risk else ""), C_AMBER)
        pdf._kv("Safe",      str(len(safe)), C_GREEN)
        ppe_viol = [w["name"] for w in workers if not w.get("ppe_compliant", True)]
        pdf._kv("PPE violations", ", ".join(ppe_viol) if ppe_viol else "None", C_RED if ppe_viol else C_GREEN)
        pdf.ln(4)

    # ── Equipment health ──
    if equipment:
        pdf._section("Equipment Health Summary")
        all_eq = [e for zone in equipment.values() for e in (zone.get("equipment_states") or [])]
        critical = [e for e in all_eq if e.get("status") in ("critical", "offline")]
        pdf._kv("Total equipment monitored", str(len(all_eq)))
        pdf._kv("Critical / Offline", str(len(critical)) +
                (f"  >>  {', '.join(e['equipment_id'] for e in critical)}" if critical else ""), C_RED if critical else C_GREEN)
        if all_eq:
            lowest = min(all_eq, key=lambda e: e.get("health_score", 100))
            pdf._kv("Lowest health score",
                    f"{lowest['name']} ({lowest.get('health_score',0):.0f}%) - {lowest.get('status','?')}", C_AMBER)

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()


# ── 3. ALERT FEED PDF ─────────────────────────────────────────────────────────
def generate_alert_feed(alerts: list, limit: int = 30) -> bytes:
    pdf = SentinelPDF(f"ALERT FEED - Last {min(len(alerts), limit)} Events")
    alerts = alerts[:limit]

    counts = {}
    for a in alerts:
        s = a.get("severity","INFO")
        counts[s] = counts.get(s, 0) + 1

    # Summary row
    for sev in ("HIGH", "MEDIUM", "LOW", "INFO"):
        n = counts.get(sev, 0)
        if n:
            pdf.set_fill_color(*_sev_color(sev))
            pdf.set_text_color(*C_WHITE)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_x(pdf.get_x())
            pdf.cell(30, 8, f"{sev}: {n}", align="C", fill=True)
            pdf.cell(4, 8, "")
    pdf.ln(12)
    pdf._thin_rule()
    pdf.ln(4)

    pdf._table_header([("Zone", 14, "C"), ("Severity", 24, "C"), ("Prob", 18, "R"),
                        ("Hour", 16, "C"), ("Permits", 40, "L"), ("Precedent", 66, "L")])
    for i, a in enumerate(alerts):
        sev  = a.get("severity", "INFO")
        prob = a.get("calibrated_probability", 0)
        ctx  = a.get("contributing_context") or {}
        permits = ", ".join(ctx.get("active_permit_types") or [])[:20] or "--"
        cite = ""
        if a.get("briefing"):
            cite = ", ".join(a["briefing"].get("source_citation") or [])[:22]
        if sev in ("HIGH", "MEDIUM"):
            pdf.set_text_color(*_sev_color(sev))
        else:
            pdf.set_text_color(*C_TEXT)
        pdf._table_row([
            (a.get("zone_id",""), 14, "C"),
            (sev, 24, "C"),
            (f"{prob*100:.1f}%", 18, "R"),
            (f"{a.get('sim_hour',0):.2f}h", 16, "C"),
            (permits, 40, "L"),
            (cite or "--", 66, "L"),
        ], shade=i%2==0)
        pdf.set_text_color(*C_TEXT)

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()


# ── 4. AUDIT LOG PDF ──────────────────────────────────────────────────────────
def generate_audit_log(entries: list, limit: int = 100) -> bytes:
    pdf = SentinelPDF(f"DECISION AUDIT LOG - {min(len(entries), limit)} Entries")
    entries = entries[:limit]

    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*C_DIM)
    pdf.set_x(16)
    pdf.multi_cell(178, 5,
        "This log contains every agent decision recorded during the simulation session. "
        "It is structured for regulatory and compliance review.")
    pdf.ln(4)

    pdf._table_header([("Hour", 16, "C"), ("Agent", 44, "L"), ("Summary", 118, "L")])
    for i, entry in enumerate(reversed(entries)):
        agent   = entry.get("agent", "--")
        summary = entry.get("summary", "")[:90]
        hour    = entry.get("sim_hour", 0)
        is_high = "HIGH" in summary or "CRITICAL" in summary
        if is_high:
            pdf.set_text_color(*C_RED)
            pdf.set_font("Helvetica", "B", 8)
        else:
            pdf.set_text_color(*C_TEXT)
            pdf.set_font("Helvetica", "", 8)
        if i % 2 == 0:
            pdf.set_fill_color(*C_LIGHT_BG)
            pdf.rect(16, pdf.get_y(), 178, 5.5, "F")
        pdf.set_x(16)
        pdf.cell(16, 5.5, f"{hour:.2f}h", align="C")
        pdf.cell(44, 5.5, _safe(agent, 22))
        pdf.cell(118, 5.5, _safe(summary, 90))
        pdf.ln()

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()
