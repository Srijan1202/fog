"""Build paper.docx from paper.md (the canonical manuscript) with pandoc, then style it.

    python build_docx.py   (run from paper/analysis; needs pandoc on PATH and python-docx)

- Replaces the "See references.md" pointer with the full IEEE reference list (the same
  entries as paper.tex / references.md).
- Embeds the PNG figures from ../figures.
- Styles: Times New Roman body 11 pt, black headings, captions, bordered tables, A4
  with 1-inch margins.
"""
import os
import re
import subprocess

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER = os.path.dirname(HERE)
SRC = os.path.join(PAPER, "paper.md")
TMP = os.path.join(HERE, "_paper_for_docx.md")
OUT = os.path.join(PAPER, "paper.docx")

REFS = [
    'D. Brooks and M. Martonosi, "Dynamic thermal management for high-performance microprocessors," in *Proc. 7th Int. Symp. High-Performance Computer Architecture (HPCA)*, 2001, pp. 171–182, doi: 10.1109/HPCA.2001.903261.',
    'K. Skadron, M. R. Stan, W. Huang, S. Velusamy, K. Sankaranarayanan, and D. Tarjan, "Temperature-aware microarchitecture," in *Proc. 30th Annu. Int. Symp. Computer Architecture (ISCA)*, 2003, pp. 2–13, doi: 10.1145/859618.859620.',
    'J. Choi, C.-Y. Cher, H. Franke, H. Hamann, A. Weger, and P. Bose, "Thermal-aware task scheduling at the system software level," in *Proc. Int. Symp. Low Power Electronics and Design (ISLPED)*, 2007, pp. 213–218, doi: 10.1145/1283780.1283826.',
    'J. Yang, X. Zhou, M. Chrobak, Y. Zhang, and L. Jin, "Dynamic thermal management through task scheduling," in *Proc. IEEE Int. Symp. Performance Analysis of Systems and Software (ISPASS)*, 2008, pp. 191–201, doi: 10.1109/ISPASS.2008.4510751.',
    'M. Chrobak, C. Dürr, M. Hurand, and J. Robert, "Algorithms for temperature-aware task scheduling in microprocessor systems," in *Algorithmic Aspects in Information and Management (AAIM)*, ser. Lecture Notes in Computer Science. Springer, 2008, pp. 120–130, doi: 10.1007/978-3-540-68880-8_13.',
    'A. K. Coskun, T. S. Rosing, K. A. Whisnant, and K. C. Gross, "Static and dynamic temperature-aware scheduling for multiprocessor SoCs," *IEEE Trans. Very Large Scale Integr. (VLSI) Syst.*, vol. 16, no. 9, pp. 1127–1140, 2008, doi: 10.1109/TVLSI.2008.2000726.',
    'O. Sahin and A. K. Coskun, "Providing sustainable performance in thermally constrained mobile devices," in *Proc. 14th ACM/IEEE Symp. Embedded Systems for Real-Time Multimedia (ESTIMedia)*, 2016, pp. 72–77, doi: 10.1145/2993452.2994309.',
    'Raspberry Pi Ltd, "Raspberry Pi computer hardware: Frequency management and thermal control," Raspberry Pi Documentation. [Online]. Available: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html (accessed Oct. 18, 2026).',
    'Raspberry Pi Ltd, "Raspberry Pi OS: vcgencmd get_throttled," Raspberry Pi Documentation. [Online]. Available: https://www.raspberrypi.com/documentation/computers/os.html (accessed Oct. 18, 2026).',
    'J. Dean and L. A. Barroso, "The tail at scale," *Commun. ACM*, vol. 56, no. 2, pp. 74–80, 2013, doi: 10.1145/2408776.2408794.',
    'Y. Zhang, D. Meisner, J. Mars, and L. Tang, "Treadmill: Attributing the source of tail latency through precise load testing and statistical inference," in *Proc. ACM/IEEE 43rd Annu. Int. Symp. Computer Architecture (ISCA)*, 2016, pp. 456–468, doi: 10.1109/ISCA.2016.47.',
    'R. J. Hyndman and Y. Fan, "Sample quantiles in statistical packages," *Amer. Statistician*, vol. 50, no. 4, pp. 361–365, 1996, doi: 10.1080/00031305.1996.10473566.',
    'B. Efron, "Bootstrap methods: Another look at the jackknife," *Ann. Statist.*, vol. 7, no. 1, pp. 1–26, 1979, doi: 10.1214/aos/1176344552.',
    'S. Holm, "A simple sequentially rejective multiple test procedure," *Scand. J. Statist.*, vol. 6, pp. 65–70, 1979. [Online]. Available: https://www.jstor.org/stable/4615733',
    'N. Cliff, "Dominance statistics: Ordinal analyses to answer ordinal questions," *Psychol. Bull.*, vol. 114, no. 3, pp. 494–509, 1993, doi: 10.1037/0033-2909.114.3.494.',
    'T. Hoefler and R. Belli, "Scientific benchmarking of parallel computing systems: Twelve ways to tell the masses when reporting performance results," in *Proc. Int. Conf. High Performance Computing, Networking, Storage and Analysis (SC)*, 2015, pp. 1–12, doi: 10.1145/2807591.2807644.',
    'T. Mytkowicz, A. Diwan, M. Hauswirth, and P. F. Sweeney, "Producing wrong data without doing anything obviously wrong!" in *Proc. 14th Int. Conf. Architectural Support for Programming Languages and Operating Systems (ASPLOS)*, 2009, pp. 265–276, doi: 10.1145/1508244.1508275.',
    'B. A. Nosek, C. R. Ebersole, A. C. DeHaven, and D. T. Mellor, "The preregistration revolution," *Proc. Natl. Acad. Sci. USA*, vol. 115, no. 11, pp. 2600–2606, 2018, doi: 10.1073/pnas.1708274114.',
]

md = open(SRC, encoding="utf-8").read()
pointer = "See `references.md` (IEEE style). In-text numbers [1]–[18] refer to that list."
assert md.count(pointer) == 1, "reference pointer not found"
refs_md = "\n\n".join(f"\\[{i}\\] {r}" for i, r in enumerate(REFS, 1))
md = md.replace(pointer, refs_md)
# the docx has no date line under the title
md = re.sub(r'^date: .*\n', "", md, count=1, flags=re.M)

# Pandoc only starts a list after a blank line. paper.md often puts a list straight under
# a bold lead-in line, so insert the blank line pandoc needs (content unchanged).
LIST = re.compile(r"^\s*(?:[-*]|\d+\.)\s")
lines, fixed = md.split("\n"), []
for i, line in enumerate(lines):
    prev = fixed[-1] if fixed else ""
    if (LIST.match(line) and prev.strip() and not LIST.match(prev)
            and not prev.startswith((" ", "\t", "|", "#"))):
        fixed.append("")
    fixed.append(line)
md = "\n".join(fixed)
open(TMP, "w", encoding="utf-8", newline="\n").write(md)

subprocess.run(["pandoc", TMP, "-f", "markdown", "-t", "docx", "-o", OUT,
                "--resource-path", PAPER], check=True)
os.remove(TMP)

# ---------------------------------------------------------------- styling
doc = Document(OUT)
sec = doc.sections[0]
sec.page_width, sec.page_height = Inches(8.27), Inches(11.69)
for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
    setattr(sec, side, Inches(1))
TEXT_W = Inches(6.27)


def set_font(style, name="Times New Roman", size=None, bold=None, italic=None, color=RGBColor(0, 0, 0)):
    f = style.font
    f.name = name
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(a), name)
    for a in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
        if rfonts.get(qn(a)) is not None:
            del rfonts.attrib[qn(a)]
    if size:
        f.size = Pt(size)
    if bold is not None:
        f.bold = bold
    if italic is not None:
        f.italic = italic
    if color is not None:
        f.color.rgb = color


styles = doc.styles
for name in ("Normal", "Body Text", "First Paragraph", "Compact"):
    if name in styles:
        set_font(styles[name], size=11)
        pf = styles[name].paragraph_format
        pf.space_after = Pt(6)
        pf.line_spacing = 1.15
for name, size in (("Title", 18), ("Heading 1", 14), ("Heading 2", 12), ("Heading 3", 11)):
    if name in styles:
        set_font(styles[name], size=size, bold=True, italic=False)
        styles[name].paragraph_format.space_before = Pt(14 if name != "Title" else 0)
        styles[name].paragraph_format.space_after = Pt(6)
for name in ("Author", "Subtitle"):
    if name in styles:
        set_font(styles[name], size=12, bold=False, italic=False)
for name in ("Image Caption", "Table Caption", "Caption"):
    if name in styles:
        set_font(styles[name], size=9.5, italic=True, color=RGBColor(0x33, 0x33, 0x33))
if "Source Code" in styles:
    set_font(styles["Source Code"], name="Consolas", size=9.5)
if "Verbatim Char" in styles:
    set_font(styles["Verbatim Char"], name="Consolas", size=9.5)
if "Hyperlink" in styles:
    styles["Hyperlink"].font.color.rgb = RGBColor(0x1F, 0x4E, 0x8C)

for p in doc.paragraphs:
    if p.style.name in ("Title", "Author"):
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # pandoc puts each figure in its own paragraph: keep images within the text width, centred
    for r in p.runs:
        for d in r._element.findall(".//" + qn("wp:extent")):
            cx, cy = int(d.get("cx")), int(d.get("cy"))
            if cx > TEXT_W:
                ratio = TEXT_W / cx
                d.set("cx", str(int(cx * ratio)))
                d.set("cy", str(int(cy * ratio)))
                for ext in r._element.findall(".//" + qn("a:ext")):
                    if ext.get("cx") == str(cx):
                        ext.set("cx", str(int(cx * ratio)))
                        ext.set("cy", str(int(cy * ratio)))
            if r._element.findall(".//" + qn("wp:inline")):
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER


def border_table(tbl):
    tblPr = tbl._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "bottom", "insideH"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single")
        e.set(qn("w:sz"), "6" if edge == "insideH" else "10")
        e.set(qn("w:color"), "BFBFBF" if edge == "insideH" else "000000")
        borders.append(e)
    for edge in ("left", "right", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "nil")
        borders.append(e)
    old = tblPr.find(qn("w:tblBorders"))
    if old is not None:
        tblPr.remove(old)
    tblPr.append(borders)


for t in doc.tables:
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    border_table(t)
    for i, row in enumerate(t.rows):
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(1)
                for r in p.runs:
                    r.font.size = Pt(9)
                    r.font.name = "Times New Roman"
                    if i == 0:
                        r.font.bold = True

doc.core_properties.title = "Effect of Job Ordering on Thermal Throttling and Tail Latency on a Raspberry Pi 4: A Preregistered Negative Result"
doc.core_properties.author = "Srijan Srivastava; Padma Priya"
doc.save(OUT)
print("wrote", OUT)
