#!/usr/bin/env python3
"""Build the Patchwerk user manual: chapters -> one self-contained HTML file.

    python docs/manual/build.py                 # build, embed figures, purge them
    python docs/manual/build.py --keep          # build, embed, keep the rasters
    python docs/manual/build.py --dry-run       # report only, write nothing

WHY IT PURGES (Cole, 2026-07-27): "If you can consolidate your workflow by
purging image files as you add them to your doc that'd be ideal."

So the build IS the consolidation step. Every figure is read, base64'd into the
HTML as a `data:` URI, verified present in the written output, and only THEN
deleted from `img/`. After a build, `img/` holds the tracked `.svg` sources and
nothing else, and the single output file carries the whole manual, pictures
included.

That is safe here for a specific reason, worth knowing before you reach for
--keep: `img/` is GITIGNORED for rasters. Screenshots are regenerated from the
app by capture.py, never archived. Deleting a `.png` costs one re-capture. The
`.svg` diagram sources are TRACKED and are never purged, because a hand-drawn
SVG cannot be regenerated from anything.

Order of operations is deliberate: embed -> write the output -> verify the data
URI is in the file on disk -> then purge. A figure that failed to embed is never
deleted, and a build that dies halfway deletes nothing.

Markup contract: the emitted HTML targets `style/manual.css`, which is
canonical (DESIGN.md §13). That stylesheet expects a specific shape — a
`.band` grid, `h2 > .num`/`.t`, and figures as `.plate > .shot`. This renderer
emits exactly that. If you change one, change the other.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import re
import sys
from pathlib import Path

import figspec

# Document furniture — the reading apparatus, NOT part of the manual's visual
# design. style/manual.css is canonical for anything that appears on the page
# itself (DESIGN.md §13); everything here is chrome that disappears in print.
FURNITURE_CSS = """
.topbar{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:.6rem;
  padding:.5rem 1.1rem;background:rgba(247,246,241,.92);backdrop-filter:blur(8px);
  border-bottom:1px solid var(--rule);font-family:var(--ui);font-size:.8rem}
.topbar .brand{font-weight:650;margin-right:auto;letter-spacing:-.01em}
.topbar .brand em{color:var(--ink-mute);font-style:normal;font-weight:400}
#q{font:inherit;font-family:var(--ui);width:min(30ch,42vw);padding:.32rem .6rem;
  border:1px solid var(--rule);border-radius:6px;background:var(--paper-2);color:var(--ink)}
#q:focus{outline:2px solid var(--audio-ink);outline-offset:1px}
.tb{font:inherit;font-family:var(--ui);color:var(--ink-mute);text-decoration:none;
  border:1px solid var(--rule);border-radius:6px;padding:.32rem .6rem;background:var(--paper-2);
  cursor:pointer}
.tb:hover{color:var(--ink)}
#results{position:fixed;top:3rem;left:50%;transform:translateX(-50%);z-index:60;
  width:min(48rem,92vw);max-height:70vh;overflow:auto;background:var(--paper);
  border:1px solid var(--rule);border-radius:10px;
  box-shadow:0 18px 50px rgba(26,26,25,.18)}
#results .hit{display:block;padding:.6rem .9rem;border-bottom:1px solid var(--rule-soft);
  text-decoration:none;color:inherit}
#results .hit:hover,#results .hit.sel{background:var(--paper-2)}
#results .ht{font-family:var(--ui);font-weight:650;font-size:.85rem}
#results .hn{font-family:var(--mono);font-size:.7rem;color:var(--ink-mute);margin-right:.5em}
#results .hx{font-size:.78rem;color:var(--ink-mute);line-height:1.45;margin-top:.15rem}
#results mark{background:#fdf0b8;color:inherit;border-radius:2px}
#results .none{padding:.9rem;color:var(--ink-mute);font-family:var(--ui);font-size:.85rem}
nav.toc{display:grid;gap:.1rem;margin-top:1.5rem}
nav.toc a{text-decoration:none;color:inherit;padding:.2rem 0;
  border-bottom:1px solid transparent;font-family:var(--ui)}
nav.toc a:hover{border-bottom-color:var(--rule)}
nav.toc .n{display:inline-block;min-width:3.4em;font-family:var(--mono);
  font-size:.74rem;color:var(--ink-mute)}
nav.toc .t1{font-weight:650;font-size:1rem;margin-top:.9rem}
nav.toc .t2{font-size:.86rem;padding-left:1.2rem;color:var(--ink-mute)}
nav.toc .t2:hover{color:var(--ink)}
.bookindex{columns:2;column-gap:2.2rem;margin-top:1.4rem}
.bookindex h4{break-after:avoid;margin-top:1.2rem}
.bookindex dl{margin:0}
.bookindex dt{font-size:.8rem;margin-top:.35rem;break-inside:avoid}
.bookindex dd{margin:0 0 .1rem;font-family:var(--mono);font-size:.7rem}
.bookindex dd a{color:var(--ink-mute);text-decoration:none;margin-right:.45em}
.bookindex dd a:hover{color:var(--ink);text-decoration:underline}
:target{scroll-margin-top:4rem}

/* Typographic guards. Not in DESIGN.md — it specifies the type system, not
   line-breaking behaviour. Worth folding into style/manual.css properly.
   text-wrap:pretty asks the browser to avoid a single-word last line;
   balance keeps short headings from breaking 1-word-per-line. */
/* Code blocks. manual.css styles `code` as an inline chip (background, border,
   padding) — correct inline, but inside a <pre> it paints a stray box around
   the block. Reset it there and let the <pre> carry the panel instead. */
pre{background:var(--paper-2);border:1px solid var(--rule-soft);border-radius:8px;
  padding:.75rem .9rem;margin:1.4rem 0;overflow-x:auto;
  font-family:var(--mono);font-size:.82rem;line-height:1.5}
pre code{background:none;border:0;padding:0;font-size:inherit;border-radius:0}

.rail .k{margin-top:.55rem}
.rail .k:first-child{margin-top:0}
.rail a{color:inherit;text-decoration:none;display:inline-block;margin-left:.35em}
.rail a:hover{color:var(--ink);text-decoration:underline}
p,li,figcaption,dd{text-wrap:pretty}
h1,h2 .t,h3,h4,.standfirst{text-wrap:balance}
p{orphans:2;widows:2}
table{break-inside:auto}
tr,td,th{break-inside:avoid}

@media print{
  .topbar,#results{display:none!important}
  .bookindex{columns:3}
  nav.toc a{break-inside:avoid}
  p,li{orphans:3;widows:3}
  h1{break-before:page}
  figure.fig,.callout{break-inside:avoid}
}
"""

SEARCH_JS = """
const q=document.getElementById('q'),R=document.getElementById('results');
let sel=-1;
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const rx=t=>new RegExp('('+t.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&')+')','ig');
function run(t){
  t=t.trim();
  if(t.length<2){R.hidden=true;R.innerHTML='';sel=-1;return;}
  const terms=t.toLowerCase().split(/\\s+/);
  const hits=[];
  for(const d of DOCS){
    const hay=(d.t+' '+d.n+' '+d.c+' '+d.x).toLowerCase();
    if(!terms.every(w=>hay.includes(w)))continue;
    // title matches outrank body matches; earlier body position breaks ties
    const inTitle=terms.every(w=>d.t.toLowerCase().includes(w));
    hits.push({d,score:(inTitle?0:1000)+d.x.toLowerCase().indexOf(terms[0])});
  }
  hits.sort((a,b)=>a.score-b.score);
  if(!hits.length){R.innerHTML='<div class="none">Nothing found.</div>';R.hidden=false;return;}
  const r=rx(terms[0]);
  R.innerHTML=hits.slice(0,40).map(({d})=>{
    const i=Math.max(0,d.x.toLowerCase().indexOf(terms[0])-70);
    const snip=esc((i?'…':'')+d.x.slice(i,i+190)+'…').replace(r,'<mark>$1</mark>');
    return `<a class="hit" href="#${d.i}"><div class="ht">`+
      `<span class="hn">${esc(d.n||'')}</span>${esc(d.t)}</div>`+
      `<div class="hx">${snip}</div></a>`;
  }).join('');
  R.hidden=false;sel=-1;
}
q.addEventListener('input',e=>run(e.target.value));
q.addEventListener('keydown',e=>{
  const hits=[...R.querySelectorAll('.hit')];
  if(e.key==='Escape'){q.value='';run('');q.blur();}
  else if(e.key==='ArrowDown'&&hits.length){e.preventDefault();
    sel=Math.min(sel+1,hits.length-1);hits.forEach(h=>h.classList.remove('sel'));
    hits[sel].classList.add('sel');hits[sel].scrollIntoView({block:'nearest'});}
  else if(e.key==='ArrowUp'&&hits.length){e.preventDefault();
    sel=Math.max(sel-1,0);hits.forEach(h=>h.classList.remove('sel'));
    hits[sel].classList.add('sel');hits[sel].scrollIntoView({block:'nearest'});}
  else if(e.key==='Enter'&&hits.length){e.preventDefault();
    (hits[sel]||hits[0]).click();R.hidden=true;q.blur();}
});
document.addEventListener('keydown',e=>{
  if(e.key==='/'&&document.activeElement!==q){e.preventDefault();q.focus();q.select();}
});
document.addEventListener('click',e=>{
  if(!R.contains(e.target)&&e.target!==q)R.hidden=true;
});
document.getElementById('printbtn').addEventListener('click',()=>window.print());
"""

HERE = Path(__file__).resolve().parent
IMG = HERE / "img"
STYLE = HERE / "style" / "manual.css"
FIGURES_MD = HERE / "FIGURES.md"

RELEASE = "v2.2 “Polyphony”"
RELEASE_DATE = "2026-07-28"

PURGEABLE = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
KEEP_ALWAYS = {".svg"}

# Ordinary English words that appear in backticks somewhere in the prose and
# would otherwise pollute the index. Logic-gate names (AND/OR/NOR/XOR) and real
# identifiers are deliberately NOT here — they are exactly what readers look up.
STOPWORDS = {
    "Monospace", "absolute", "memory", "contain", "none", "auto", "the", "and",
    "or", "not", "on", "off", "up", "down", "left", "right", "top", "bottom",
    "yes", "no", "true", "false", "OK",
}

FIG_SLOT = re.compile(r"^\s*\[(FIG-[0-9A-Z-]+)\]\s*$", re.MULTILINE)
BULLET = re.compile(r"^\s*[-*]\s+")
NUMBERED = re.compile(r"^\s*\d+\.\s+")
TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
FIG_ROW = re.compile(
    r"^\|\s*(FIG-[0-9A-Z]+-[0-9]+)\s*\|\s*`?\[?([A-Z-]+)\]?`?\s*\|\s*(.*?)\s*\|", re.MULTILINE
)
# Aspect class per chapter-figure convention, DESIGN.md §6.2.
AR_BY_KIND = {"card": "3/2", "page": "16/10", "wide": "16/9",
              "diagram": "4/3", "pair": "2/1", "seq": "3/1"}


# ---------------------------------------------------------------------------
# figure inventory
# ---------------------------------------------------------------------------

def figure_briefs() -> dict[str, tuple[str, str]]:
    """{FIG-ID: (kind, 'what it must show')} parsed from FIGURES.md.

    Used for placeholder plates. An uncaptured manual should still tell you
    what belongs in the hole — that is what makes it reviewable before the
    rig session.
    """
    if not FIGURES_MD.exists():
        return {}
    out: dict[str, tuple[str, str]] = {}
    for m in FIG_ROW.finditer(FIGURES_MD.read_text(encoding="utf-8")):
        fig_id, kind, must_show = m.group(1), m.group(2), m.group(3)
        out[fig_id] = (kind.strip(), must_show.strip())
    return out


def find_figure(fig_id: str) -> Path | None:
    stem = fig_id.lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"):
        p = IMG / f"{stem}{ext}"
        if p.exists():
            return p
    return None


# Figures are captured at device_scale_factor 2 against a 1600px viewport, so a
# whole-page shot arrives 3200px wide. The manual's text column is 33rem and a
# figure spans rail+column — call it 700 CSS px, so 2000px is still comfortably
# retina. Downscaling here rather than at capture keeps the generator's output
# full-resolution for any other use, and the raster is purged after embedding
# anyway. Cuts the built file by roughly two thirds.
MAX_FIG_PX = 2000


def data_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if mime is None:
        mime = "image/svg+xml" if path.suffix == ".svg" else "application/octet-stream"
    raw = path.read_bytes()
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        try:
            from io import BytesIO

            from PIL import Image
            im = Image.open(BytesIO(raw))
            if im.width > MAX_FIG_PX:
                h = round(im.height * MAX_FIG_PX / im.width)
                im = im.resize((MAX_FIG_PX, h), Image.LANCZOS)
            # WebP, not PNG. These are screenshots of a dark UI with fine
            # text — PNG barely compresses them (measured: 739K → 683K),
            # WebP at q88 gets the same figure to 147K with no visible loss.
            # Every browser that can open this file supports it.
            buf = BytesIO()
            im.convert("RGB").save(buf, "WEBP", quality=88, method=6)
            if buf.tell() < len(raw):
                raw, mime = buf.getvalue(), "image/webp"
        except Exception:
            pass                      # Pillow absent or odd file — embed as-is
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def aspect_for(fig_id: str) -> str:
    # Module card portraits are 3:2; everything else defaults to the page ratio
    # unless FIGURES.md says otherwise. Chapters 12-14 are the card chapters.
    ch = fig_id.split("-")[1]
    return AR_BY_KIND["card"] if ch in {"12", "13", "14"} else AR_BY_KIND["page"]


# ---------------------------------------------------------------------------
# markdown -> the markup manual.css expects
# ---------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(s, quote=False)


def inline(s: str) -> str:
    # A figure slot occurring INSIDE a sentence renders in the sentence — an
    # indicator, a handle, one top-bar control. Cole, 2026-07-27: "a breakdown
    # of the top bar would be much more useful in the form of individual icons
    # inserted inline."
    def _inline_fig(m):
        fid = m.group(1)
        spec = figspec.FIGS.get(fid)
        if spec and spec[0] == "inline":
            return f'<span class="f-inline">{spec[1]}</span>'
        return m.group(0)
    s = re.sub(r"\[(FIG-[0-9A-Z-]+)\]", _inline_fig, s)
    s = re.sub(r"`([^`]+)`", lambda m: f"<code>{esc(m.group(1))}</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    # REFERENCE §x.y -> muted xref span
    s = re.sub(r"(REFERENCE\s+&sect;|REFERENCE\s+§)\s*([0-9A-Z][0-9.]*)",
               r'<span class="xref">REFERENCE §\2</span>', s)
    return s


def split_num(title: str) -> tuple[str, str]:
    """'3.1 The top bar' -> ('3.1', 'The top bar')."""
    m = re.match(r"^([0-9]+(?:\.[0-9]+)*|Appendix\s+[A-Z])\.?\s+(.*)$", title)
    return (m.group(1), m.group(2)) if m else ("", title)


def slug(num: str, title: str) -> str:
    base = num if num else re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return "s-" + base.replace(".", "-")


class Collector:
    """Accumulates the navigation, search and term data as chapters render.

    Built during the single render pass rather than by re-parsing afterwards —
    the numbers and titles are already split at that point, so re-deriving them
    would be a second source of truth for the same fact.
    """

    def __init__(self) -> None:
        self.toc: list[dict] = []          # {level,num,title,id,chapter}
        self.search: list[dict] = []       # {id,title,num,chapter,text}
        self.terms: dict[str, set[str]] = {}   # `code term` -> {section id}
        self._cur: dict | None = None
        self.chapter = ""

    def heading(self, level: int, num: str, title: str) -> str:
        sid = slug(num, title)
        # TOC and search show plain text, not markdown — a backticked module
        # key in a heading must not surface as literal ` in a result row.
        plain = re.sub(r"[`*_]", "", title)
        self.toc.append({"level": level, "num": num, "title": plain,
                         "id": sid, "chapter": self.chapter})
        self._cur = {"id": sid, "title": plain, "num": num,
                     "chapter": self.chapter, "text": []}
        self.search.append(self._cur)
        return sid

    def text(self, s: str) -> None:
        if self._cur is not None:
            self._cur["text"].append(s)

    def term(self, t: str) -> None:
        # Module keys, endpoints, params, commands — the things a reader looks
        # up by name. Filtered to plausible identifiers so prose in backticks
        # does not flood the index.
        if self._cur is None or not re.fullmatch(r"[A-Za-z_][\w.:/-]{1,40}", t):
            return
        self.terms.setdefault(t, set()).add(self._cur["id"])


def harvest(text: str, chapter: str, col: Collector) -> None:
    """Fill the search corpus and term index from raw markdown.

    Deliberately a separate pass over the source rather than a hook inside
    render(): render() consumes table and list lines in inner loops, so a hook
    there would silently miss exactly the content readers most often search for
    (parameter tables, endpoint lists).
    """
    cur = None
    for line in text.split("\n"):
        if m := re.match(r"^#{2,4}\s+(.*)$", line):
            num, title = split_num(m.group(1))
            cur = slug(num, title)
            continue
        if cur is None or not line.strip():
            continue
        for entry in col.search:
            if entry["id"] == cur:
                clean = re.sub(r"[|*_>#`\[\]]", " ", line)
                entry["text"].append(" ".join(clean.split()))
                break
        for t in re.findall(r"`([^`]+)`", line):
            if re.fullmatch(r"\.?[A-Za-z_][\w.:/-]{1,40}", t) and t not in STOPWORDS:
                col.terms.setdefault(t, set()).add(cur)


def render(text: str, figure_html, col: Collector) -> str:
    out: list[str] = []
    lines = text.split("\n")
    i, in_code = 0, False

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            # NOT .full — a code block belongs in the text column, not spanning
            # the marginal rail as well.
            out.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            i += 1
            continue
        if in_code:
            out.append(esc(line))
            i += 1
            continue

        # figure slot on its own line
        if m := FIG_SLOT.match(line):
            out.append(figure_html(m.group(1)))
            i += 1
            continue

        # table
        if line.strip().startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append('<table class="full"><thead><tr>'
                       + "".join(f"<th>{inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>")
            for r in rows:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue

        # headings
        if m := re.match(r"^(#{2,4})\s+(.*)$", line):
            lvl, num, title = len(m.group(1)), *split_num(m.group(2))
            sid = col.heading(lvl, num, title)
            if lvl == 2:
                out.append(f'<h2 id="{sid}"><span class="num">{num}</span>'
                           f'<span class="t">{inline(title)}</span></h2>')
                # Fill the marginal rail, per DESIGN.md §5.3: "The rail carries
                # figure references and § citations, so they stay out of the
                # prose." Scan this section for both and emit them alongside
                # its opening paragraph. Left empty, the rail reads as a hole.
                sec: list[str] = []
                for ln in lines[i + 1:]:
                    if re.match(r"^##\s", ln):
                        break
                    sec.append(ln)
                blob = "\n".join(sec)
                refs = sorted(set(re.findall(r"REFERENCE\s+§\s*([0-9A-Z][0-9.]*)", blob)),
                              key=lambda s: [int(p) if p.isdigit() else 0
                                             for p in s.split(".")])
                figs = sorted(set(FIG_SLOT.findall(blob)))
                bits = []
                if refs:
                    bits.append('<span class="k">Reference</span>'
                                + " · ".join(f"§{r}" for r in refs))
                if figs:
                    bits.append('<span class="k">Figures</span>'
                                + " ".join(f'<a href="#{f}">{f[4:]}</a>' for f in figs))
                if bits:
                    out.append('<aside class="rail">' + "".join(bits) + "</aside>")
            elif lvl == 3:
                out.append(f'<h3 id="{sid}"><span class="num">{num}</span>'
                           f"{inline(title)}</h3>")
            else:
                out.append(f'<h4 id="{sid}">{inline(title)}</h4>')
            i += 1
            continue

        # blockquote callouts
        if line.strip().startswith(">"):
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(lines[i].strip().lstrip(">").strip())
                i += 1
            body = " ".join(b for b in block if b)
            cls, lbl = "callout", "Note"
            for marker, c, l in (("**WARN.**", "callout warn", "Warning"),
                                 ("**TRY THIS.**", "callout try", "Try this")):
                if body.startswith(marker):
                    cls, lbl, body = c, l, body[len(marker):].strip()
            out.append(f'<div class="{cls}"><span class="lbl">{lbl}</span>'
                       f"<p>{inline(body)}</p></div>")
            continue

        # Lists. A source line that is neither a new bullet nor blank is a
        # CONTINUATION of the current item — the markdown is hard-wrapped at
        # ~80 columns, so most items span several lines.
        for matcher, tag in ((BULLET, "ul"), (NUMBERED, "ol")):
            if matcher.match(line):
                out.append(f"<{tag}>")
                items: list[str] = []
                while i < len(lines):
                    ln = lines[i]
                    if matcher.match(ln):
                        items.append(matcher.sub("", ln).strip())
                    elif ln.strip() and items and not BULLET.match(ln) \
                            and not NUMBERED.match(ln) and not ln.startswith("#") \
                            and not ln.strip().startswith(("|", ">", "```")):
                        items[-1] += " " + ln.strip()
                    else:
                        break
                    i += 1
                out.extend(f"<li>{inline(t)}</li>" for t in items)
                out.append(f"</{tag}>")
                break
        else:
            # Paragraph. Join every line up to the next blank line or block
            # opener. Emitting one <p> per SOURCE line is the bug that put a
            # paragraph break at every 80-column wrap point.
            if line.strip() and not line.startswith("---"):
                para = []
                while i < len(lines):
                    ln = lines[i]
                    if not ln.strip() or ln.startswith(("#", "---", "```")) \
                            or ln.strip().startswith((">", "|")) \
                            or BULLET.match(ln) or NUMBERED.match(ln) \
                            or FIG_SLOT.match(ln):
                        break
                    para.append(ln.strip())
                    i += 1
                if para:
                    out.append(f"<p>{inline(' '.join(para))}</p>")
                continue
            i += 1
            continue
        continue

    return "\n".join(out)


# ---------------------------------------------------------------------------

def chapters() -> list[Path]:
    files = sorted(p for p in HERE.glob("*.md")
                   if re.match(r"^\d{2}-", p.name) and p.name != "00-INDEX.md")
    if not files:
        sys.exit(f"no chapter files found in {HERE}")
    return files


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="embed figures but do NOT delete the rasters")
    ap.add_argument("--dry-run", action="store_true", help="write and delete nothing")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    briefs = figure_briefs()
    embedded: dict[str, Path] = {}
    missing: list[str] = []
    drawn: list[str] = []
    body: list[str] = []
    col = Collector()

    def figure_html(fig_id: str) -> str:
        # An authored HTML/CSS figure wins over any raster. No plate unless the
        # picture earns one — see figspec.py's header for why.
        spec = figspec.FIGS.get(fig_id)
        if spec:
            size, html = spec
            drawn.append(fig_id)
            if size == "inline":
                return f'<p class="f-inline-block">{html}</p>'
            cap = inline(esc(figspec.CAPTIONS.get(fig_id, "")))
            return (f'<figure class="f-{size}" id="{fig_id}">{html}'
                    f'<figcaption><span class="figid">{fig_id}</span>'
                    f'<span class="cap">{cap}</span></figcaption></figure>')

        kind, must_show = briefs.get(fig_id, ("", ""))
        ar = aspect_for(fig_id)
        src = find_figure(fig_id)
        if src is not None:
            embedded[fig_id] = src
            art = f'<img alt="{fig_id}" src="{data_uri(src)}">'
            ph = ""
        else:
            missing.append(fig_id)
            tag_cls = "tag rig" if "RIG" in kind else "tag"
            label = "needs rig" if "RIG" in kind else "authored"
            art = ""
            ph = (f'<div class="ph"><span class="id">{fig_id}</span>'
                  f'<span class="desc">{esc(must_show)}</span>'
                  f'<span class="{tag_cls}">{label}</span></div>')
        cap = esc(must_show) if must_show else ""
        return (f'<figure class="fig full" id="{fig_id}">'
                f'<div class="plate"><div class="shot" style="--ar:{ar}">{art}{ph}</div></div>'
                f'<figcaption><span class="figid">{fig_id}</span>'
                f'<span class="cap">{cap}</span></figcaption></figure>')

    for path in chapters():
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")

        # chapter opener: first H1 is the title, first paragraph the standfirst
        title, rest_from = path.stem, 0
        for n, ln in enumerate(lines):
            if ln.startswith("# "):
                title, rest_from = ln[2:].strip(), n + 1
                break
        num, name = split_num(title)
        # The standfirst is the chapter's first paragraph — which is itself
        # hard-wrapped, so take every line of it, not just the first, or the
        # remainder is left behind as an orphan paragraph.
        stand, s_from, s_to = "", None, None
        for n in range(rest_from, len(lines)):
            ln = lines[n]
            if s_from is None:
                if ln.strip() and not ln.startswith(("#", ">", "|", "-", "*", "[")):
                    s_from = n
                continue
            if not ln.strip():
                s_to = n
                break
        if s_from is not None:
            s_to = s_to if s_to is not None else len(lines)
            stand = " ".join(l.strip() for l in lines[s_from:s_to])

        eyebrow = (f"Appendices" if path.stem.startswith("90")
                   else f"Chapter {num}" if num else "")
        keep = lines[rest_from:s_from] + lines[s_to:] if s_from is not None \
            else lines[rest_from:]
        rest = "\n".join(keep)

        col.chapter = name
        chap_id = f"ch-{path.stem}"
        col.toc.append({"level": 1, "num": num, "title": name,
                        "id": chap_id, "chapter": name})
        rendered = render(rest, figure_html, col)
        harvest(rest, name, col)

        body.append(
            f'<section class="sheet" id="{chap_id}">'
            f'<div class="band"><header class="ch-open full">'
            f'<div class="ch-num">{eyebrow}</div><h1>{esc(name)}</h1>'
            + (f'<p class="standfirst">{inline(stand)}</p>' if stand else "")
            + "</header>" + rendered + "</div></section>"
        )

    # ---- contents -----------------------------------------------------------
    toc: list[str] = ['<section class="sheet" id="contents"><div class="band">'
                      '<header class="ch-open full"><div class="ch-num">Contents</div>'
                      "<h1>Contents</h1></header><nav class=\"toc full\">"]
    for e in col.toc:
        if e["level"] == 1:
            toc.append(f'<a class="t1" href="#{e["id"]}">'
                       f'<span class="n">{e["num"]}</span>{esc(e["title"])}</a>')
        elif e["level"] == 2:
            toc.append(f'<a class="t2" href="#{e["id"]}">'
                       f'<span class="n">{e["num"]}</span>{esc(e["title"])}</a>')
    toc.append("</nav></div></section>")

    # ---- back-of-book index -------------------------------------------------
    # Every backticked identifier the manual uses, and where. This is the thing
    # a player actually reaches for: "where does it talk about `keyshift`?"
    idx: list[str] = ['<section class="sheet" id="index"><div class="band">'
                      '<header class="ch-open full"><div class="ch-num">Index</div>'
                      "<h1>Index</h1><p class=\"standfirst\">Every module key, "
                      "parameter, endpoint and command named in the manual, and "
                      "the sections that discuss it.</p></header>"
                      '<div class="bookindex full">']
    by_letter: dict[str, list[str]] = {}
    for term in sorted(col.terms, key=str.lower):
        by_letter.setdefault(term[0].upper(), []).append(term)
    id_to_num = {e["id"]: (e["num"] or e["title"]) for e in col.toc}
    for letter in sorted(by_letter):
        idx.append(f'<h4>{letter}</h4><dl>')
        for term in by_letter[letter]:
            links = sorted(col.terms[term],
                           key=lambda s: [int(p) if p.isdigit() else 0
                                          for p in s[2:].split("-")])
            refs = " ".join(f'<a href="#{s}">{id_to_num.get(s, "?")}</a>'
                            for s in links)
            idx.append(f"<dt><code>{esc(term)}</code></dt><dd>{refs}</dd>")
        idx.append("</dl>")
    idx.append("</div></div></section>")

    corpus = [{"i": e["id"], "t": e["title"], "n": e["num"],
               "c": e["chapter"], "x": " ".join(e["text"])[:1400]}
              for e in col.search if e["text"]]

    css = STYLE.read_text(encoding="utf-8") if STYLE.exists() else ""
    doc = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Patchwerk {RELEASE} — User Manual</title>"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<style>{css}</style><style>{FURNITURE_CSS}</style>"
        f"<style>{figspec.FIGURE_CSS}</style></head><body>"
        f'<div class="topbar"><span class="brand">Patchwerk <em>{RELEASE}</em></span>'
        '<input id="q" type="search" placeholder="Search the manual   /" '
        'autocomplete="off" spellcheck="false">'
        '<a class="tb" href="#contents">Contents</a>'
        '<a class="tb" href="#index">Index</a>'
        '<button class="tb" id="printbtn">PDF</button></div>'
        '<div id="results" hidden></div>'
        f'<section class="sheet"><div class="band"><header class="ch-open full">'
        f'<div class="ch-num">User Manual</div><h1>Patchwerk</h1>'
        f'<p class="standfirst">{RELEASE} · {RELEASE_DATE}<br>'
        f"Companion to <code>docs/REFERENCE-v2.2-polyphony.md</code></p>"
        "</header></div></section>"
        + "".join(toc) + "\n".join(body) + "".join(idx)
        + f"<script>const DOCS={json.dumps(corpus, ensure_ascii=False)};"
        f"{SEARCH_JS}</script></body></html>"
    )

    out_path = Path(args.out) if args.out else HERE / f"MANUAL-{RELEASE_DATE}.html"

    print(f"chapters : {len(chapters())}")
    print(f"figures  : {len(drawn)} drawn, {len(embedded)} embedded, "
          f"{len(set(missing))} outstanding")
    if args.dry_run:
        print("dry run  - nothing written, nothing purged")
        return

    out_path.write_text(doc, encoding="utf-8")
    print(f"wrote    : {out_path}  ({len(doc) / 1_000_000:.2f} MB)")

    # Also drop a copy where the running instrument serves it. synthbase's
    # GuiServer exposes GET /manual from gui/manual.html, and the ? popup in
    # the GUI links to it — so a build is what makes the in-app manual current.
    # Deliberately not a flag: a stale in-app manual is worse than none.
    served = HERE.parent.parent / "gui" / "manual.html"
    if served.parent.is_dir():
        served.write_text(doc, encoding="utf-8")
        print(f"served   : {served}  (GET /manual)")
    else:
        print("served   : skipped - no gui/ directory beside docs/")

    if args.keep:
        print("purge    : skipped (--keep)")
        return

    written = out_path.read_text(encoding="utf-8")
    purged = kept = 0
    for fig_id, src in embedded.items():
        if src.suffix.lower() in KEEP_ALWAYS or src.suffix.lower() not in PURGEABLE:
            kept += 1
            continue
        if f'id="{fig_id}"' in written and "data:image" in written:
            src.unlink()
            purged += 1
        else:
            kept += 1
            print(f"           ! kept {src.name} - not verified in output")
    print(f"purge    : {purged} raster(s) removed, {kept} kept "
          f"(.svg sources are never purged)")


if __name__ == "__main__":
    main()
