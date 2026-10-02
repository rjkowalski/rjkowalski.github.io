# rjkowalski.github.io

Personal site and tailored resumes, all generated from one file: `data/resume.yaml`.

**Don't edit the `*.html` pages in the repo root** — they are build output.

| File | What it holds |
|---|---|
| `data/resume.yaml` | All resume content, tagged by lane and priority (rules in its header) |
| `data/website.yaml` | Website-only copy and styling (About story, icons, timeline photos, socials) |
| `templates/site/` | Jinja templates for the six site pages (theme: Samuel 2.2.0 in `assets/`) |
| `templates/resume/` | The PDF resume template and its print CSS |
| `build.py` | The generator |

## Build

Needs [uv](https://docs.astral.sh/uv/) and Google Chrome (used headless to print PDFs).

```sh
uv run build.py                      # regenerate site pages + resume.pdf (public, general lane)
uv run build.py --lane space         # out/resume-space.pdf — 1 page if it fits, else 2
uv run build.py --lane all           # every lane into out/
uv run build.py --lane pm --pages 1  # strict one page; reports overflow
uv run build.py --lane ai-training --long    # include priority-3 items, any length
uv run build.py --lane data-eng --drafts     # preview with draft bullets highlighted
```

Lanes: `general`, `data-eng`, `ai-training`, `space`, `pm`, `teaching`.
`out/` is git-ignored; only `resume.pdf` (linked from the site) is published.

Preview locally: `python3 -m http.server` then open http://localhost:8000.

## Publishing

GitHub Pages serves the repo root. After editing the YAML: build, check, commit
the regenerated `*.html` and `resume.pdf`, push. `_config.yml` keeps the build
inputs off the live site, but **the repo is public, so everything in
`data/resume.yaml` — notes and drafts included — is visible on GitHub.**
