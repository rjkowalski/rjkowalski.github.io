"""Build rjkowalski.github.io and tailored resumes from data/resume.yaml.

    uv run build.py                    # site pages + resume.pdf (general lane)
    uv run build.py --lane space       # out/resume-space.pdf (1 page if it fits, else 2)
    uv run build.py --lane space --pages 1   # strict one page (reports overflow)
    uv run build.py --lane all         # every lane
    uv run build.py --lane pm --long   # include priority-3 items, any length
    uv run build.py --lane space --drafts   # preview with draft items shown

Filtering rules (see the header of resume.yaml):
  - status: draft items are hidden unless --drafts
  - public: false items never reach the website (PDFs still use them)
  - an item shows in lane L if its tags contain L or "general";
    items without tags inherit their parent's visibility
  - priority (default 1) must be <= the max priority for the output
  - note: fields are never rendered
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pypdf import PdfReader

ROOT = Path(__file__).parent
DATA = ROOT / "data"
TEMPLATES = ROOT / "templates"
OUT = ROOT / "out"

SITE_PAGES = ["index", "about", "skills", "experience", "education", "contact"]
LANES = ["general", "data-eng", "ai-training", "space", "pm", "teaching"]

# Per-lane PDF layout: which optional sections appear, in order.
# Experience, education, and skills always appear.
LANE_SECTIONS = {
    "general":     ["honours", "certifications"],
    "data-eng":    ["certifications", "honours"],
    "ai-training": ["publications", "honours", "certifications"],
    "space":       ["honours", "publications", "certifications"],
    "pm":          ["honours", "certifications"],
    "teaching":    ["honours", "publications", "leadership"],
}

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
]

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


# --------------------------------------------------------------------------
# Validation — catch YAML slips that would otherwise silently drop content

def validate(data: dict) -> None:
    errors = []

    def walk(node, path):
        if isinstance(node, dict):
            for tag in node.get("tags", []):
                if tag not in LANES:
                    errors.append(f"{path}: unknown lane tag {tag!r}")
            if "priority" in node and node["priority"] not in (1, 2, 3):
                errors.append(f"{path}: priority must be 1, 2 or 3")
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(data, "resume")
    for g in data.get("skills", []):
        for s in g["items"]:
            # An unquoted comma inside {name: a, b} makes "b" a stray key.
            if extra := set(s) - {"name", "current"}:
                errors.append(f"skills/{g['group']}: {s['name']!r} has stray keys {sorted(extra)} — quote the name")
    if errors:
        sys.exit("resume.yaml problems:\n  " + "\n  ".join(errors))


# --------------------------------------------------------------------------
# Filtering

def visible(item: dict, lane: str | None, max_priority: int, *, site: bool, drafts: bool) -> bool:
    if item.get("status") == "draft" and not drafts:
        return False
    if site and item.get("public") is False:
        return False
    if item.get("priority", 1) > max_priority:
        return False
    if lane is None or "tags" not in item:
        return True
    return lane in item["tags"] or "general" in item["tags"]


def tailor(data: dict, lane: str | None, max_priority: int, *, site: bool, drafts: bool) -> dict:
    """Return a copy of the resume with only the items for this lane/priority."""
    keep = lambda it: visible(it, lane, max_priority, site=site, drafts=drafts)  # noqa: E731
    pick = lambda items: [it for it in items or [] if keep(it)]  # noqa: E731
    key = lane or "general"

    return {
        "basics": data["basics"],
        "lane": key,
        "headline": data["headlines"].get(key, data["headlines"]["general"]),
        "summary": data["summaries"].get(key, data["summaries"]["general"]),
        "experience": [
            {**e, "bullets": pick(e.get("bullets"))} for e in pick(data["experience"])
        ],
        "education": [
            {**e, "highlights": pick(e.get("highlights"))} for e in pick(data["education"])
        ],
        "certifications": pick(data.get("certifications")),
        "honours": pick(data.get("honours")),
        "publications": pick(data.get("publications")),
        "leadership": pick(data.get("leadership")),
        "skills": [
            {**g, "items": [s for s in g["items"] if s.get("current", True)]}
            for g in pick(data.get("skills"))
        ],
        "interests": data.get("interests", []),
    }


# --------------------------------------------------------------------------
# Template helpers

def fmt_date(value, long: bool = False) -> str:
    """2022-07 -> 'Jul 2022'; 2008 -> '2008'; date(2008,3,26) -> 'Mar 2008'."""
    if value in (None, ""):
        return ""
    if isinstance(value, dt.date):
        return f"{MONTHS[value.month - 1]} {value.year}"
    text = str(value)
    if text.lower() == "present":
        return "Present"
    m = re.fullmatch(r"(\d{4})-(\d{2})", text)
    if m:
        month = MONTHS[int(m.group(2)) - 1]
        if long:
            month = dt.date(2000, int(m.group(2)), 1).strftime("%B")
        return f"{month} {m.group(1)}"
    return text


def year(value) -> str:
    if isinstance(value, dt.date):
        return str(value.year)
    text = str(value)
    if text.lower() == "present":
        return "Present"
    # "2022-07" -> "2022"; ranges like "2008–2009" pass through unchanged
    return text[:4] if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", text) else text


def make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        undefined=StrictUndefined,
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["date"] = fmt_date
    env.filters["year"] = year
    return env


# --------------------------------------------------------------------------
# Outputs

def build_site(env: Environment, data: dict, website: dict, drafts: bool) -> None:
    resume = tailor(data, None, 3, site=True, drafts=drafts)
    python_years = dt.date.today().year - data["basics"]["python_since"]
    for block in website["home"]["skills"]:
        block["text"] = block["text"].replace("{python_years}", str(python_years))
    by_id = {e["id"]: e for e in resume["experience"]}

    for page in SITE_PAGES:
        html = env.get_template(f"site/{page}.html.j2").render(
            r=resume,
            w=website,
            page=page,
            by_id=by_id,
            python_years=python_years,
        )
        (ROOT / f"{page}.html").write_text(html)
        print(f"  {page}.html")


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).exists() or shutil.which(c):
            return c
    sys.exit("Chrome/Chromium not found; needed to print PDFs (or pass --no-pdf).")


def print_pdf(html_path: Path, pdf_path: Path) -> int:
    subprocess.run(
        [
            find_chrome(),
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    return len(PdfReader(pdf_path).pages)


def build_resume(
    env: Environment,
    data: dict,
    lane: str,
    *,
    long: bool,
    drafts: bool,
    max_pages: int = 2,
    site: bool = False,
    dest: Path | None = None,
) -> None:
    """Render one lane to HTML + PDF, picking the richest content that fits.

    Tries (priority, page-limit) pairs in order and keeps the first that fits:
    one page with priority 2, one page with priority 1, then the same for two
    pages (unless max_pages=1). --long ignores length.
    """
    OUT.mkdir(exist_ok=True)
    stem = f"resume-{lane}" + ("-long" if long else "")
    html_path = OUT / f"{stem}.html"
    pdf_path = dest or OUT / f"{stem}.pdf"
    template = env.get_template("resume/resume.html.j2")
    css = (TEMPLATES / "resume" / "resume.css").read_text()

    if long:
        attempts = [(3, 99)]
    else:
        attempts = [(p, n) for n in range(1, max_pages + 1) for p in (2, 1)]

    for max_priority, limit in attempts:
        r = tailor(data, lane, max_priority, site=site, drafts=drafts)
        r["sections"] = LANE_SECTIONS[lane]
        r["long"] = long
        html_path.write_text(template.render(r=r, css=css, drafts=drafts))
        pages = print_pdf(html_path, pdf_path)
        if pages <= limit:
            break

    flag = "" if long or pages <= max_pages else f"  <-- over {max_pages} page(s) even at priority 1; trim resume.yaml"
    print(f"  {pdf_path.relative_to(ROOT)}  ({pages} page{'s' * (pages > 1)}, priority <= {max_priority}){flag}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lane", choices=LANES + ["all"], help="build tailored resume PDF(s) instead of the site")
    ap.add_argument("--long", action="store_true", help="include priority-3 items; no page limit")
    ap.add_argument("--pages", type=int, choices=[1, 2], default=2, help="page limit for tailored PDFs (default 2; one page is tried first)")
    ap.add_argument("--drafts", action="store_true", help="include status: draft items (preview only)")
    ap.add_argument("--no-pdf", action="store_true", help="site build: skip regenerating resume.pdf")
    args = ap.parse_args()

    data = yaml.safe_load((DATA / "resume.yaml").read_text())
    validate(data)
    env = make_env()

    if args.lane:
        for lane in LANES if args.lane == "all" else [args.lane]:
            build_resume(env, data, lane, long=args.long, drafts=args.drafts, max_pages=args.pages)
        return

    if args.drafts:
        sys.exit("--drafts is for PDF previews only; never publish drafts to the site.")
    website = yaml.safe_load((DATA / "website.yaml").read_text())
    print("Site:")
    build_site(env, data, website, drafts=False)
    if not args.no_pdf:
        # The downloadable resume on the site: general lane, public items only.
        build_resume(env, data, "general", long=False, drafts=False, max_pages=2, site=True, dest=ROOT / "resume.pdf")


if __name__ == "__main__":
    main()
