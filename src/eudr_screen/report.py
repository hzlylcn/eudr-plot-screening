"""PDF screening report (A4) built with reportlab.

Input: the screening GeoDataFrame (as written to web/data/plots.geojson,
including optional ndvi_before/ndvi_after) and the thumbnail folder.

Structure
  1. Summary: key figures, overview map, how to read the flags
  2. Results table for all plots
  3. One sheet per flagged plot: reason, metrics, Sentinel-2 before/after
  4. Method, thresholds, limitations, data sources and licences
"""

from __future__ import annotations

import datetime as dt
import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from . import screen  # noqa: E402

FLAG_STYLE = {
    screen.FLAG_REVIEW: ("Review", colors.HexColor("#c62828")),
    screen.FLAG_CONFLICT: ("Review: data conflict", colors.HexColor("#ef6c00")),
    screen.FLAG_LOW: ("Low risk", colors.HexColor("#2e7d32")),
}
ORDER = [screen.FLAG_REVIEW, screen.FLAG_CONFLICT, screen.FLAG_LOW]
INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#616e7c")
LINE = colors.HexColor("#d9dee3")
PANEL = colors.HexColor("#f4f6f8")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


def _hx(c) -> str:
    """reportlab Color -> '#rrggbb' (hexval() gives '0xrrggbb')."""
    return "#" + c.hexval()[2:]


def _styles():
    ss = getSampleStyleSheet()
    base = dict(fontName="Helvetica", textColor=INK)
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=22,
                                leading=26, alignment=TA_LEFT, spaceAfter=2, textColor=INK),
        "subtitle": ParagraphStyle("st", **{**base, "textColor": MUTED}, fontSize=11, leading=15),
        "h1": ParagraphStyle("h1", **{**base, "fontName": "Helvetica-Bold"}, fontSize=15, leading=19,
                             spaceBefore=6, spaceAfter=6),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Helvetica-Bold"}, fontSize=12, leading=15,
                             spaceBefore=4, spaceAfter=4),
        "body": ParagraphStyle("b", **base, fontSize=9.5, leading=13.5, spaceAfter=5),
        "small": ParagraphStyle("s", **{**base, "textColor": MUTED}, fontSize=8, leading=10.5),
        "cell": ParagraphStyle("c", **base, fontSize=8, leading=10),
        "cellb": ParagraphStyle("cb", **{**base, "fontName": "Helvetica-Bold"}, fontSize=8, leading=10),
        "big": ParagraphStyle("big", **{**base, "fontName": "Helvetica-Bold"}, fontSize=22, leading=24),
        "reason": ParagraphStyle("r", **base, fontSize=9.5, leading=13),
    }


def _fmt_pct(v):
    return "-" if v is None or v != v else f"{v * 100:.0f}%"


def _fmt(v, d=2):
    return "-" if v is None or v != v else f"{v:.{d}f}"


def _flag_chip(flag, st):
    label, col = FLAG_STYLE[flag]
    return Paragraph(f'<font color="{_hx(col)}"><b>{label}</b></font>', st["cell"])


def _overview_png(res) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=200)
    for flag in reversed(ORDER):
        sub = res[res.flag == flag]
        if sub.empty:
            continue
        c = sub.geometry.centroid
        ax.scatter(c.x, c.y, s=46, color=_hx(FLAG_STYLE[flag][1]), edgecolor="white",
                   linewidth=1.2, label=f"{FLAG_STYLE[flag][0]} ({len(sub)})", zorder=3)
    cents = res.geometry.centroid
    for i, (_, r) in enumerate(res.iterrows()):
        c = cents.iloc[i]
        # a close neighbour with a lower ID already labels to the upper right: go lower left
        crowded = any(abs(c.x - o.x) < 0.05 and abs(c.y - o.y) < 0.05
                      for j, o in enumerate(cents) if j < i)
        ax.annotate(r.plot_id.split("-")[-1], (c.x, c.y),
                    xytext=(-15, -9) if crowded else (4, 3), textcoords="offset points",
                    fontsize=6.5, color="#3e4c59")
    ax.set_aspect("equal")
    ax.grid(color="#e4e7eb", linewidth=0.6, zorder=0)
    ax.set_xlabel("Longitude", fontsize=8)
    ax.set_ylabel("Latitude", fontsize=8)
    ax.tick_params(labelsize=7)
    for s in ax.spines.values():
        s.set_color("#cbd2d9")
    ax.legend(fontsize=7.5, frameon=False, loc="lower right")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _key_figures(res, st):
    cells = []
    for flag in ORDER:
        label, col = FLAG_STYLE[flag]
        n = int((res.flag == flag).sum())
        cells.append([Paragraph(f'<font color="{_hx(col)}">{n}</font>', st["big"]),
                      Paragraph(label, st["small"])])
    total = [Paragraph(str(len(res)), st["big"]), Paragraph("Plots screened", st["small"])]
    t = Table([[total] + cells], colWidths=[CONTENT_W / 4] * 4)
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _note_box(text, st, color=PANEL):
    t = Table([[Paragraph(text, st["body"])]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def _results_table(res, st):
    head = ["Plot", "Input", "Area (ha)", "Forest 2020 (JRC)", "Loss after 2020 (ha)", "Loss years", "Flag"]
    rows = [[Paragraph(h, st["cellb"]) for h in head]]
    for _, r in _sorted(res).iterrows():
        rows.append([
            Paragraph(r.plot_id, st["cellb"]), Paragraph(r.geom_input, st["cell"]),
            Paragraph(_fmt(r.area_ha), st["cell"]), Paragraph(_fmt_pct(r.jrc_forest_2020_share), st["cell"]),
            Paragraph(_fmt(r.loss_post2020_ha), st["cell"]),
            Paragraph((r.loss_years or "-").replace(",", ", "), st["cell"]),
            _flag_chip(r.flag, st),
        ])
    w = [20, 17, 17, 25, 27, 30, 38]
    t = Table(rows, colWidths=[x * mm for x in w], repeatRows=1)
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")]),
    ]))
    return t


def _sorted(res):
    rank = {f: i for i, f in enumerate(ORDER)}
    return res.assign(_r=res.flag.map(rank)).sort_values(["_r", "plot_id"]).drop(columns="_r")


def _plot_sheet(r, thumbs: Path, st):
    label, col = FLAG_STYLE[r.flag]
    head = Table([[Paragraph(f"<b>{r.plot_id}</b>", st["h2"]),
                   Paragraph(f'<font color="white"><b>{label}</b></font>', st["cell"])]],
                 colWidths=[CONTENT_W - 45 * mm, 45 * mm])
    head.setStyle(TableStyle([("BACKGROUND", (1, 0), (1, 0), col), ("ALIGN", (1, 0), (1, 0), "CENTER"),
                              ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (0, 0), 0),
                              ("LINEBELOW", (0, 0), (-1, 0), 0.8, col)]))
    reason = Table([[Paragraph(r.flag_reason, st["reason"])]], colWidths=[CONTENT_W])
    reason.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PANEL), ("LINEBEFORE", (0, 0), (0, 0), 2.5, col),
                                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))

    metrics = [
        ("Commodity", r.commodity),
        ("Geolocation given as", "point (< 4 ha)" if r.geom_input == "point" else "polygon"),
        ("Plot area", f"{_fmt(r.area_ha)} ha"),
        ("Forest on 31 Dec 2020 (JRC)", _fmt_pct(r.jrc_forest_2020_share)),
        ("Tree cover in 2000 (Hansen)", f"{_fmt(r.treecover2000_mean, 0)}%"),
        ("Tree-cover loss after 2020", f"{_fmt(r.loss_post2020_ha)} ha ({_fmt_pct(r.loss_post2020_share)})"),
        ("Loss years", (r.loss_years or "-").replace(",", ", ")),
        ("NDVI in plot, dry season 2019/20", _fmt(r.get("ndvi_before"))),
        ("NDVI in plot, dry season 2025/26", _fmt(r.get("ndvi_after"))),
    ]
    mt = Table([[Paragraph(k, st["cell"]), Paragraph(str(v), st["cellb"])] for k, v in metrics],
               colWidths=[52 * mm, 30 * mm])
    mt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                            ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    side = 43 * mm
    imgs = []
    for period, cap in (("before", "Sentinel-2, dry season 2019/20"), ("after", "Sentinel-2, dry season 2025/26")):
        p = thumbs / f"{r.plot_id}_{period}.jpg"
        cell = [Image(str(p), width=side, height=side)] if p.exists() else [Paragraph("No image", st["small"])]
        cell.append(Paragraph(cap, st["small"]))
        imgs.append(cell)
    it = Table([imgs], colWidths=[side + 3 * mm] * 2)
    it.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    body = Table([[mt, it]], colWidths=[CONTENT_W - 2 * (side + 3 * mm), 2 * (side + 3 * mm)])
    body.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return KeepTogether([head, Spacer(1, 4), reason, Spacer(1, 6), body, Spacer(1, 12)])


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 10 * mm,
                      "EUDR plot screening, demo dataset (synthetic plots). Screening only, not a legal compliance determination.")
    canvas.drawRightString(PAGE_W - MARGIN, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build(res, thumbs: Path, out: Path, title_note: str = "Synthetic demo plots, south-west Côte d'Ivoire"):
    st = _styles()
    today = dt.date.today().isoformat()
    flagged = _sorted(res[res.flag != screen.FLAG_LOW])
    n_flag = len(flagged)

    s = []
    # --- 1. Summary
    s += [Paragraph("EUDR Plot Screening Report", st["title"]),
          Paragraph(f"{title_note} · generated {today}", st["subtitle"]), Spacer(1, 10)]
    s.append(_note_box(
        "<b>Question.</b> Was there tree-cover loss on these plots after <b>31 December 2020</b>, the cut-off "
        "date of the EU Deforestation Regulation (EU 2023/1115)? "
        "<b>Data.</b> JRC Global Forest Cover 2020 (V4) for the forest baseline, Hansen/UMD Global Forest "
        "Change v1.13 for annual loss up to 2025, Copernicus Sentinel-2 for visual before/after checks.", st))
    s += [Spacer(1, 10), _key_figures(res, st), Spacer(1, 10)]
    s.append(Paragraph(
        f"<b>{n_flag} of {len(res)}</b> plots show tree-cover loss after 2020 and need review. "
        f"{int((res.flag == screen.FLAG_CONFLICT).sum())} of these are <b>data conflicts</b>: Hansen detects "
        "tree-cover loss, but the JRC map did not classify the plot as forest in 2020, which typically "
        "happens with cocoa agroforestry or other tree crops. These need a closer look at the imagery and "
        "supporting documents before any conclusion.", st["body"]))
    s.append(Image(_overview_png(res), width=CONTENT_W * 0.86, height=CONTENT_W * 0.86 * 4.6 / 6.4))
    s.append(Paragraph("Plot positions coloured by screening result. Numbers are plot IDs.", st["small"]))
    s.append(Spacer(1, 8))
    flags_tbl = Table([[_flag_chip(f, st), Paragraph(t, st["cell"])] for f, t in [
        (screen.FLAG_REVIEW, "Loss after 2020 and the plot was forest in 2020 (JRC): both datasets agree."),
        (screen.FLAG_CONFLICT, "Loss after 2020, but JRC mapped little or no forest in 2020: datasets disagree, check imagery."),
        (screen.FLAG_LOW, "No tree-cover loss after 2020 above the screening threshold. Small losses are still listed."),
    ]], colWidths=[40 * mm, CONTENT_W - 40 * mm])
    flags_tbl.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    s.append(flags_tbl)
    s.append(PageBreak())

    # --- 2. Results table
    s += [Paragraph("Results for all plots", st["h1"]),
          Paragraph("Sorted by flag. Point plots (under 4 ha) are measured as circles of the declared area.",
                    st["small"]), Spacer(1, 6), _results_table(res, st)]
    small = res[(res.flag == screen.FLAG_LOW) & (res.loss_post2020_ha > 0)]
    if len(small):
        s += [Spacer(1, 8), _note_box(
            "<b>Below-threshold losses.</b> " + "; ".join(
                f"{r.plot_id}: {r.loss_post2020_ha:.2f} ha ({r.loss_years})" for _, r in small.iterrows())
            + ". The EUDR has no minimum area, so a quick visual check is recommended.", st)]
    s.append(PageBreak())

    # --- 3. Plot sheets
    s += [Paragraph(f"Plots to review ({n_flag})", st["h1"]),
          Paragraph("Yellow outline: plot boundary. Images are cloud-masked median composites of all clear "
                    "Sentinel-2 observations in each dry season (November to March).", st["small"]),
          Spacer(1, 8)]
    for _, r in flagged.iterrows():
        s.append(_plot_sheet(r, thumbs, st))

    # --- 4. Method (flows on after the last plot sheet)
    s.append(Spacer(1, 6))
    s.append(Paragraph("Method, limitations and sources", st["h1"]))
    for h, txt in [
        ("Forest baseline", "JRC Global Forest Cover 2020 (V4), 10 m, the EU reference map for the EUDR. "
         "Share of plot pixels mapped as forest on 31 December 2020."),
        ("Loss after the cut-off", "Hansen/UMD Global Forest Change v1.13, 30 m. Pixels with a loss year of 2021 "
         "or later inside the plot, converted to hectares."),
        ("Screening thresholds", f"A plot is flagged when post-2020 loss is at least {screen.MIN_LOSS_HA} ha or "
         f"{screen.MIN_LOSS_SHARE:.0%} of its area (filters single-pixel noise). A plot counts as forest in 2020 "
         f"when at least {screen.MIN_JRC_FOREST_SHARE:.0%} of it is JRC forest."),
        ("Visual check", "Sentinel-2 L2A (Element 84 Earth Search, AWS), cloud-masked with the scene "
         "classification layer, median composite per dry season; NDVI averaged inside the plot."),
        ("Limitations", "Hansen measures tree-cover loss, not legally defined deforestation: it also captures "
         "plantation harvest, fire and storm damage. JRC excludes tree crops from forest by design. NDVI "
         "recovers within one or two seasons once crops or regrowth cover a cleared plot (e.g. DEMO-007: "
         "cleared in 2023, NDVI 0.59 before and 0.61 after), so a stable NDVI does not rule out clearing; "
         "the loss year from Hansen is the primary evidence. Point plots "
         "are approximated as circles. Hansen v1.13 covers loss up to 2025; clearing in 2026 is only visible "
         "in Sentinel-2. Persistent cloud cover can leave haze in composites."),
        ("Use of this report", "This is a screening aid for due diligence. It does not determine legal "
         "compliance, land ownership or legality of production. Demo plots are synthetic and do not "
         "represent real farms."),
    ]:
        s += [Paragraph(h, st["h2"]), Paragraph(txt, st["body"])]
    s += [Paragraph("Data sources and licences", st["h2"]), Paragraph(
        "JRC Global Forest Cover 2020 V4, © European Union, CC BY 4.0. · Hansen, M.C. et al. (2013) "
        "High-Resolution Global Maps of 21st-Century Forest Cover Change, Science 342; data v1.13, CC BY 4.0. · "
        "Contains modified Copernicus Sentinel data 2019-2026.", st["body"])]

    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title="EUDR Plot Screening Report", author="EUDR plot screening tool")
    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)
    return out
