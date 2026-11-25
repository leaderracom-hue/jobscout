"""Tailored CV -> PDF in the reference CV design (Assaf-GTM-AI-Dev-v2): pale green left column with the name, titles, contact, skills and
languages, a green rule, and a main column with Summary, Experience and the other sections. Open Sans stands in for Segoe UI
(not redistributable); Hebrew text uses DejaVu Sans, right-aligned. Input is the markdown of the tailored CV (tailored_md format)."""
import re
from pathlib import Path

from fpdf import FPDF

FONTS = Path(__file__).parent / "fonts"
GREEN, PALE, INK, TEXT, MUTE = (14, 90, 85), (238, 243, 241), (31, 35, 34), (61, 70, 68), (107, 116, 114)
SIDE_W, RULE_W, MAIN_X, MAIN_R = 196.0, 3.0, 219.0, 24.5
HEB = re.compile(r"[֐-׿]")


def parse_md(md):
    """Markdown of the tailored CV -> dict(name, headline, sections=[(title, kind, items)])."""
    lines = [ln.rstrip() for ln in md.splitlines()]
    name, headline, secs, cur = "", "", [], None
    for ln in lines:
        if ln.startswith("# ") and not name:
            name = ln[2:].strip()
        elif re.fullmatch(r"\*\*.+\*\*", ln.strip()) and not headline and not secs:
            headline = ln.strip().strip("*").strip()
        elif ln.startswith("## "):
            cur = {"title": ln[3:].strip(), "jobs": [], "lines": []}
            secs.append(cur)
        elif cur is not None:
            if ln.startswith("### "):
                parts = [p.strip() for p in ln[4:].split(" · ")]
                cur["jobs"].append({"role": parts[0], "company": parts[1] if len(parts) > 1 else "", "dates": parts[2] if len(parts) > 2 else "", "bullets": []})
            elif ln.startswith("- ") and cur["jobs"] and not cur.get("_flat"):
                cur["jobs"][-1]["bullets"].append(ln[2:].strip())
            elif ln.strip():
                cur["lines"].append(ln.strip())
    return {"name": name, "headline": headline, "sections": secs}


class _Doc(FPDF):
    def header(self):  # pale column + green rule on every page
        self.set_fill_color(*PALE)
        self.rect(0, 0, SIDE_W, self.h, "F")
        self.set_fill_color(*GREEN)
        self.rect(SIDE_W, 0, RULE_W, self.h, "F")


def cv_pdf(md, prof=None):
    """prof = the user's profile (contact lines, languages, portfolio URL); nothing personal is written in this file."""
    from core import profile as PF
    contact, langs, portfolio = PF.cv_contact(prof), PF.cv_languages(prof), (prof or {}).get("portfolio")
    d = parse_md(md)
    rtl = bool(HEB.search(md))
    pdf = _Doc(unit="pt", format="Letter")
    pdf.set_auto_page_break(True, margin=26)
    pdf.set_margins(MAIN_X, 34, MAIN_R)
    if rtl:
        pdf.add_font("F", "", str(FONTS / "DejaVuSans.ttf"))
        for st in ("B", "I", "BI"):
            pdf.add_font("F", st, str(FONTS / "DejaVuSans.ttf"))
        pdf.set_text_shaping(True)
        sb, bd = "B", "B"
    else:
        pdf.add_font("F", "", str(FONTS / "OpenSans_400Regular.ttf"))
        pdf.add_font("F", "I", str(FONTS / "OpenSans_600SemiBold.ttf"))  # "I" slot = semibold
        pdf.add_font("F", "B", str(FONTS / "OpenSans_700Bold.ttf"))
        sb, bd = "I", "B"
    pdf.add_page()
    al = "R" if rtl else "L"
    # ---- left column ----
    sx, sw = 30.0, SIDE_W - 30 - 16
    y = 36.0

    def side(text, size, color, style="", gap=0.0, line=1.3):
        nonlocal y
        pdf.set_font("F", style, size)
        pdf.set_text_color(*color)
        pdf.set_xy(sx, y)
        pdf.multi_cell(sw, size * line, text, align=al, new_x="LEFT", new_y="NEXT")
        y = pdf.get_y() + gap

    side((d["name"] or "").upper(), 21, GREEN, sb, 6, 1.1)
    titles = [t.strip() for t in re.split(r"\s*[·|]\s*|\n", d["headline"]) if t.strip()]
    for i, t in enumerate(titles[:5]):
        side(t, 9.4 if i == 0 else 8.6, INK if i == 0 else TEXT, sb if i == 0 else "", 1)
    y += 6
    side("CONTACT", 8.6, GREEN, bd, 2)
    for c in contact:
        side(c, 8.0, TEXT, "", 0.5)
    if portfolio:
        pdf.set_fill_color(*GREEN)
        pdf.rect(sx, y + 4, 92, 15, "F", round_corners=True, corner_radius=3)
        pdf.set_font("F", sb, 7.4)
        pdf.set_text_color(255, 255, 255)
        pdf.set_xy(sx, y + 7.2)
        pdf.cell(92, 9, "VIEW PORTFOLIO", align="C", link=portfolio)
        y += 26
    skills = next((s for s in d["sections"] if re.search(r"skill|כישור", s["title"], re.I)), None)
    if skills:
        side("SKILLS", 8.6, GREEN, bd, 2)
        items = [x.strip() for ln in skills["lines"] for x in re.split(r"\s+·\s+", ln) if x.strip()]
        for it in items[:40]:
            side(it, 8.0, TEXT, "", 0.5)
        y += 4
    if langs:
        side("LANGUAGES", 8.6, GREEN, bd, 2)
    for ln in langs:
        side(ln, 8.0, TEXT, "", 0.5)
    # ---- main column ----
    pdf.set_xy(MAIN_X, 34)
    mw = pdf.w - MAIN_X - MAIN_R

    def heading(t):
        pdf.ln(6)
        pdf.set_font("F", bd, 9.2)
        pdf.set_text_color(*GREEN)
        pdf.set_x(MAIN_X)
        pdf.cell(mw, 12, t.upper(), align=al, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)

    def para(t, size=9.2, color=TEXT, style=""):
        pdf.set_font("F", style, size)
        pdf.set_text_color(*color)
        pdf.set_x(MAIN_X)
        pdf.multi_cell(mw, size * 1.42, t, align=al, new_x="LMARGIN", new_y="NEXT")

    for s in d["sections"]:
        if skills is not None and s is skills:
            continue
        heading(s["title"])
        for ln in s["lines"]:
            if ln.startswith("- "):
                ln = ln[2:]
            para(ln)
        for j in s["jobs"]:
            pdf.ln(4)
            pdf.set_x(MAIN_X)
            y0 = pdf.get_y()
            pdf.set_font("F", sb, 10)
            pdf.set_text_color(*INK)
            head = (j["company"] + " — " + j["role"]) if j["company"] else j["role"]
            pdf.multi_cell(mw - 86, 13, head, align=al, new_x="LMARGIN", new_y="NEXT")
            y1 = pdf.get_y()
            if j["dates"]:
                pdf.set_font("F", "", 8.3)
                pdf.set_text_color(*MUTE)
                pdf.set_xy(pdf.w - MAIN_R - 84, y0 + 1.5)
                pdf.cell(84, 10, j["dates"], align="R" if not rtl else "L")
            pdf.set_xy(MAIN_X, y1 + 1.5)
            for b in j["bullets"]:
                pdf.set_font("F", "", 9)
                pdf.set_text_color(*TEXT)
                yb = pdf.get_y()
                if pdf.get_string_width(b) > 0 and yb > pdf.h - 60:
                    pdf.add_page()
                    yb = pdf.get_y()
                pdf.set_fill_color(*GREEN)
                if rtl:
                    pdf.rect(pdf.w - MAIN_R - 8, yb + 5.6, 8, 1.5, "F")
                    pdf.set_x(MAIN_X)
                    pdf.multi_cell(mw - 14, 12.4, b, align="R", new_x="LMARGIN", new_y="NEXT")
                else:
                    pdf.rect(MAIN_X, yb + 5.6, 8, 1.5, "F")
                    pdf.set_x(MAIN_X + 14)
                    pdf.multi_cell(mw - 14, 12.4, b, align="L", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(1.2)
    return bytes(pdf.output())
