"""Figures for the Patchwerk manual, drawn in HTML and CSS.

WHY THIS REPLACED THE OLD SYSTEM (Cole, 2026-07-27):

> "You're overly beholden to your black box frames, and overly literal when it
> comes to capturing full rack images. Many of the figures could consist of a
> single icon (power on v/off indicator) or a colored line with handles (wires)
> just for instance. Individual cards can be shown in isolation, even between
> lines. Everything needs to be readable but nothing needs to be huge."

So: no plate unless the picture earns one, no full-rack screenshot unless the
whole rack is the subject, and a figure is sized to its job — an icon sits in
the line of text, a wire specimen is a line, a card sits beside the paragraph
that describes it.

Everything here is HTML + CSS drawn from the GUI's own tokens. No SVG export
step, no raster, no capture. That means a figure is editable in place, costs
nothing to ship, and cannot go stale as an image file — it goes stale the same
way prose does, visibly.

Sizes, and each is a deliberate choice, not a fallback:

    inline   rides inside a sentence, cap-height-ish. Icons, indicators, a
             single handle, one top-bar control.
    spot     ~150-260px, sits beside the prose in the marginal rail.
    column   the text column width. Diagrams with parts to label.
    wide     rail + column. Only for a picture that is genuinely the whole
             board.

Each entry is (size, html). `FIGS` is keyed by the slot ids the chapters
already carry, so the prose does not have to change.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# the drawing kit
# --------------------------------------------------------------------------

PLANES = [("audio", "audio", "solid", "none"),
          ("notes", "ctl", "dashed", "10 3"),
          ("mod", "mod", "dotted", "2 3"),
          ("binary", "bin", "dash-dot", "9 3 2 3")]


def wire(plane: str, w=170, label=True, handles=True, live=False,
         pastel_end=True) -> str:
    """A wire. Solid, coloured, no pattern — that is what the instrument draws.

    Verified in gui/blocks.html: `svg#wires path` carries NO stroke-dasharray.
    The only dashes in the wire layer are `path.flow`, the white overlay on a
    LIVE wire, and `path.ghostw` while you drag. DESIGN.md's "triple encoding"
    was wrong about the instrument and the manual inherited it.

    `live=True` adds the travelling white overlay, drawn as explicit dots: in a
    static figure a short dash pattern is exactly the thing we are telling the
    reader does not exist.
    """
    var = {"audio": "--pw-audio", "notes": "--pw-ctl", "mod": "--pw-mod",
           "binary": "--pw-bin"}[plane]
    pastel = {"audio": "--pw-audio-pastel", "notes": "--pw-ctl-pastel",
              "mod": "--pw-mod-pastel", "binary": "--pw-bin-pastel"}[plane]
    h, mid = 20, 10
    out = [f'<svg class="wirespec" viewBox="0 0 {w} {h}" width="{w}" '
           f'height="{h}" role="img" aria-label="{plane} wire">',
           f'<line x1="10" y1="{mid}" x2="{w-10}" y2="{mid}" '
           f'stroke="var({var})" stroke-width="2.4" stroke-linecap="round"/>']
    if live:
        out.extend(f'<circle cx="{x}" cy="{mid}" r="1.5" fill="#fff" '
                   f'opacity=".95"/>' for x in range(16, w - 10, 16))
    if handles:
        end = pastel if pastel_end else var
        out.append(f'<circle cx="10" cy="{mid}" r="4.5" fill="var({var})"/>')
        out.append(f'<circle cx="{w-10}" cy="{mid}" r="4.5" fill="none" '
                   f'stroke="var({end})" stroke-width="2"/>')
    out.append("</svg>")
    if label:
        out.append(f'<span class="wlab {plane}">{plane}</span>')
    return "".join(out)


def stripe(on: bool, fam="voice") -> str:
    """The card colour bar — the power switch. Outline = off, filled = on."""
    cls = "on" if on else "off"
    return (f'<span class="pwrbar {cls}" style="--fam:var(--fam-{fam})" '
            f'title="{"powered" if on else "off"}"></span>')


def handle(kind="in", plane="audio") -> str:
    var = {"audio": "--pw-audio", "notes": "--pw-ctl", "mod": "--pw-mod",
           "binary": "--pw-bin"}[plane]
    pastel = {"audio": "--pw-audio-pastel", "notes": "--pw-ctl-pastel",
              "mod": "--pw-mod-pastel", "binary": "--pw-bin-pastel"}[plane]
    if kind == "out":
        return (f'<span class="hnd out" style="--c:var({var})"></span>')
    return f'<span class="hnd in" style="--c:var({pastel})"></span>'


def osc(kind="sine", w=150, h=44, p=2.0, color="--fam-psine") -> str:
    """A convincing fake of a card's waveform preview.

    These are illustrations. The real card computes its preview from the
    module's own DSP; reproducing that exactly would mean shipping the DSP.
    The shapes below are the same functions the previews draw, evaluated here
    at figure resolution — right for teaching, not a measurement.
    """
    import math
    pts = []
    n = 160
    for i in range(n + 1):
        x = i / n
        t = x * 2 * math.pi * 2          # two cycles
        s = math.sin(t)
        if kind == "psine":              # sgn(s)*|s|^(2/p)
            v = math.copysign(abs(s) ** (2.0 / p), s) if s else 0.0
        elif kind == "square":
            v = 1.0 if s >= 0 else -1.0
        elif kind == "saw":
            v = 2 * ((x * 2) % 1.0) - 1
        elif kind == "tri":
            v = 2 * abs(2 * ((x * 2) % 1.0) - 1) - 1
        elif kind == "pulse":
            v = 1.0 if ((x * 2) % 1.0) < 0.3 else -1.0
        elif kind == "noise":            # deterministic pseudo-noise
            v = math.sin(i * 12.9898) * 43758.5453
            v = (v - math.floor(v)) * 2 - 1
            v *= 0.55 + 0.45 * math.sin(t * 0.5)
        else:
            v = s
        pts.append(f"{x*w:.1f},{h/2 - v*(h/2-4):.1f}")
    return (f'<svg class="osc" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img"><polyline points="{" ".join(pts)}" fill="none" '
            f'stroke="var({color})" stroke-width="1.8" stroke-linejoin="round" '
            f'stroke-linecap="round"/></svg>')


def card(title, rows, fam="voice", on=True, size="S", graphic="", chips="",
         ports=True) -> str:
    """A card specimen. Real geometry, real tokens, only the rows you need.

    A card in the manual does not have to be a photograph of a card. It has to
    be the same card, with the parts under discussion legible and the rest not
    competing.
    """
    ph = '<span class="hnd in any"></span>' if ports else ""
    r = "".join(
        f'<div class="prow">{ph}'
        f'<span class="pn">{n}</span>'
        f'<span class="ptrack"><i style="left:{int(v*100)}%"></i></span>'
        f'<span class="pv">{d}</span></div>' for n, v, d in rows)
    g = f'<div class="cgfx">{graphic}</div>' if graphic else ""
    c = f'<span class="chips">{chips}</span>' if chips else ""
    return (f'<div class="cardspec sz-{size}">'
            f'<div class="chead">{stripe(on, fam)}'
            f'<span class="ctitle">{title}</span>{c}</div>'
            f'<div class="cbody">{r}{g}</div></div>')


def tbicon(glyph, label, cls="") -> str:
    """One top-bar control, inline in the sentence that explains it."""
    return (f'<span class="tbi {cls}"><span class="g">{glyph}</span>'
            f'<span class="l">{label}</span></span>')


def grid_geo() -> str:
    """Units, blocks, gutters and the four card footprints, 1:1 with the GUI."""
    u = 6  # figure scale: 6px per unit, so a 10u block is 60px
    return (
        '<div class="figgeo">'
        f'<div class="gunit" style="width:{u}px;height:{u}px"><b>1u</b></div>'
        f'<div class="gblk" style="width:{u*10}px;height:{u*10}px"><b>block<br>10×10u</b></div>'
        f'<div class="ggut" style="width:{u*2}px;height:{u*10}px"><b>2u</b></div>'
        f'<div class="gblk" style="width:{u*10}px;height:{u*10}px"></div>'
        "</div>"
        '<div class="figgeo szrow">'
        f'<span class="szbox" style="width:{u*4.5}px;height:{u*4.5}px">XS</span>'
        f'<span class="szbox" style="width:{u*10}px;height:{u*4.5}px">S</span>'
        f'<span class="szbox" style="width:{u*10}px;height:{u*10}px">M</span>'
        f'<span class="szbox" style="width:{u*22}px;height:{u*10}px">L</span>'
        "</div>")


def flow(*nodes, plane="audio") -> str:
    """A signal chain as boxes and arrows. For structure, not appearance."""
    var = {"audio": "--pw-audio", "notes": "--pw-ctl", "mod": "--pw-mod",
           "binary": "--pw-bin"}[plane]
    parts = []
    for n, node in enumerate(nodes):
        if n:
            parts.append(f'<span class="farr" style="--c:var({var})"></span>')
        parts.append(f'<span class="fbox">{node}</span>')
    return f'<div class="flow">{"".join(parts)}</div>'


def legend() -> str:
    rows = []
    for name, var, pat, dash in PLANES:
        rows.append(
            f'<div class="lgr">{wire(name, w=110, label=False, pastel_end=False)}'
            f'<span class="lgn">{name}</span>'
            f'<span class="lgp">{pat}</span></div>')
    return f'<div class="legendbox">{"".join(rows)}</div>'


# --------------------------------------------------------------------------
# the figures
# --------------------------------------------------------------------------

FIGS: dict[str, tuple[str, str]] = {}


def F(fid, size, html):
    FIGS[fid] = (size, html)


# --- Ch 3: the interface --------------------------------------------------

F("FIG-03-12", "inline",
  f'{stripe(False)} off &nbsp;·&nbsp; {stripe(True)} on')

F("FIG-03-04", "column", grid_geo())

F("FIG-03-03", "spot", card(
    "Low-pass Filter", [("cutoff", .55, "2080"), ("resonance", .5, "0.500")],
    fam="filter", size="S", chips='<i class="chip">S</i><i class="chip on">M</i>'))

F("FIG-03-08", "inline",
  f'{handle("in", "notes")} <code>notes &gt; cutoff</code>')

F("FIG-03-07", "column",
  '<div class="gutterfig">'
  '<div class="gg"></div>'
  '<svg viewBox="0 0 320 120" width="320" height="120" role="img">'
  '<path d="M8 22 H120 V60 H240 V98 H312" fill="none" stroke="var(--pw-audio)" '
  'stroke-width="2"/>'
  '<path d="M8 34 H108 V72 H240 V86 H312" fill="none" stroke="var(--pw-ctl)" '
  'stroke-width="2"/>'
  '<path d="M8 46 H96 V84 H240 V74 H312" fill="none" stroke="var(--pw-mod)" '
  'stroke-width="2"/>'
  "</svg></div>")

# --- Ch 4: the planes -----------------------------------------------------

F("FIG-04-02", "column", legend())
F("FIG-A-02", "column", legend())

F("FIG-04-01", "column",
  '<div class="planefig">'
  + flow("keys", "voice", "pulse_pad", plane="notes")
  + flow("pulse_pad", "lowpass", "echo", "master", plane="audio")
  + flow("LFO", "lowpass.cutoff", plane="mod")
  + flow("clock", "logic", "arp.power", plane="binary")
  + "</div>")

# --- Ch 5: the audio graph ------------------------------------------------

F("FIG-05-02", "column",
  '<div class="faninfig">'
  + flow("pulse_pad", "reverb", plane="audio")
  + flow("fm_bell", "reverb", plane="audio")
  + flow("pluck", "reverb", plane="audio")
  + "</div>")

F("FIG-05-05", "column",
  '<div class="healfig">'
  '<div class="hf"><b>before</b>' + flow("A", "X", "B") + "</div>"
  '<div class="hf"><b>X removed</b>' + flow("A", "B") + "</div></div>")

# --- Ch 12–14: the modules ------------------------------------------------

for fid, kind, p, col in [("FIG-12-05", "psine", 8.0, "--fam-psine"),
                          ("FIG-12-06", "psine", 8.0, "--fam-psine"),
                          ("FIG-12-07", "psine", 8.0, "--fam-psine")]:
    F(fid, "spot", card("Psine", [("freq", .5, "220.0"), ("p", .45, "8.000"),
                                  ("amp", .3, "0.300")],
                        fam="psine", size="M",
                        graphic=osc(kind, p=p, color=col)))

F("FIG-12-08", "column",
  '<div class="morphrow">'
  + "".join(f'<div class="mo"><span class="mol">p = {p}</span>'
            f'{osc("psine", w=118, h=40, p=p)}</div>'
            for p in (1.2, 2, 4, 12, 64))
  + "</div>")


# --------------------------------------------------------------------------
# styles for the kit. Document furniture, not part of DESIGN.md's type system.
# --------------------------------------------------------------------------

FIGURE_CSS = """
/* --- sizing. A figure is as big as its job and no bigger. --------------- */
.f-inline{display:inline-flex;align-items:center;gap:.4em;vertical-align:-.28em}
figure.f-spot{margin:.2rem 0 1rem;grid-column:1!important;justify-self:end;
  text-align:right}
figure.f-column{margin:1.7rem 0}
figure.f-wide{margin:2.2rem 0}
figure.f-column figcaption,figure.f-wide figcaption{margin-top:.6rem}
figure.f-spot figcaption{margin-top:.4rem;font-size:.68rem;display:block;
  text-align:right;color:var(--ink-mute)}
figure.f-spot .figid{display:block;margin-bottom:.15rem}

/* --- wires: a coloured line with handles. Usually the whole figure. ----- */
.wirespec{vertical-align:middle}
.wlab{font-family:var(--ui);font-size:.62rem;letter-spacing:.1em;
  text-transform:uppercase;margin-left:.5em}
.wlab.audio{color:var(--audio-ink)}.wlab.notes{color:var(--ctl-ink)}
.wlab.mod{color:var(--mod-ink)}.wlab.binary{color:var(--bin-ink)}
.legendbox{display:grid;gap:.5rem;padding:.9rem 1rem;background:var(--pw-plane);
  border-radius:10px}
.lgr{display:grid;grid-template-columns:120px 5rem 1fr;align-items:center;gap:.7rem}
.lgn{font-family:var(--ui);font-weight:650;font-size:.76rem;color:var(--pw-ink-1)}
.lgp{font-family:var(--mono);font-size:.66rem;color:var(--pw-ink-2)}

/* --- the power bar: the card's colour stripe, at text size -------------- */
.pwrbar{display:inline-block;width:5px;height:1.05em;border-radius:3px;
  vertical-align:-.18em}
.pwrbar.on{background:var(--fam)}
.pwrbar.off{background:none;box-shadow:inset 0 0 0 1.5px var(--fam)}

/* --- handles ------------------------------------------------------------ */
.hnd{display:inline-block;width:11px;height:11px;border-radius:50%;
  vertical-align:-.1em}
.hnd.in{background:none;box-shadow:inset 0 0 0 2px var(--c)}
.hnd.in.any{width:8px;height:8px;background:var(--pw-any-pastel);
  box-shadow:none;opacity:.75}
.hnd.out{background:var(--c)}

/* --- a card specimen ---------------------------------------------------- */
.cardspec{display:inline-block;background:var(--pw-card);border-radius:9px;
  border:1px solid var(--pw-border);box-shadow:0 3px 14px rgba(0,0,0,.45);
  padding:8px 10px 10px;text-align:left;font-family:var(--ui);min-width:150px}
.chead{display:flex;align-items:center;gap:.45em;margin-bottom:.45rem}
.ctitle{color:var(--pw-ink-1);font-weight:700;font-size:.84rem}
.chips{margin-left:auto;display:flex;gap:3px}
.chips .chip{font-style:normal;font-size:.58rem;padding:.05em .32em;border-radius:3px;
  color:var(--pw-ink-2);border:1px solid #3a3a37}
.chips .chip.on{background:#3a3a37;color:var(--pw-ink-1)}
.prow{display:grid;grid-template-columns:11px 4.2em 1fr 2.6em;align-items:center;
  gap:.4em;margin:.16rem 0}
.pn{color:var(--pw-ink-2);font-size:.72rem}
.ptrack{position:relative;height:2.5px;background:#3a3a37;border-radius:2px}
.ptrack i{position:absolute;top:-3px;width:8px;height:8px;border-radius:50%;
  background:#c3c2b7;transform:translateX(-50%)}
.pv{color:var(--pw-ink-1);font-size:.68rem;font-family:var(--mono);text-align:right}
.cgfx{margin-top:.5rem;background:var(--pw-plane);border-radius:6px;padding:4px}
.osc{display:block}

/* --- top-bar controls, inline in the sentence --------------------------- */
.tbi{display:inline-flex;align-items:center;gap:.3em;background:var(--pw-surface);
  border:1px solid var(--pw-hairline);border-radius:5px;padding:.12em .45em;
  vertical-align:-.3em;line-height:1.25}
.tbi .g{color:var(--pw-ink-1);font-size:.8em}
.tbi .l{color:var(--pw-ink-2);font-family:var(--ui);font-size:.68em;
  letter-spacing:.04em}

/* --- grid geometry, 1:1 with the interface ------------------------------ */
.figgeo{display:flex;align-items:flex-end;gap:6px;padding:1rem;
  background:var(--pw-plane);border-radius:10px}
.figgeo.szrow{margin-top:.5rem;gap:10px;align-items:flex-end}
.gunit,.gblk,.ggut{position:relative;border:1px solid var(--pw-border);
  border-radius:2px}
.gblk{background:#202020}.ggut{background:repeating-linear-gradient(
  45deg,transparent,transparent 3px,rgba(255,255,255,.06) 3px,rgba(255,255,255,.06) 6px)}
.gunit{background:var(--pw-audio)}
.figgeo b{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  font-family:var(--ui);font-size:.55rem;color:var(--pw-ink-2);text-align:center;
  font-weight:500;white-space:nowrap}
.gunit b{top:-14px;transform:translateX(-50%);color:var(--pw-audio)}
.szbox{display:inline-grid;place-content:center;background:#202020;
  border:1px solid var(--pw-border);border-radius:5px;font-family:var(--ui);
  font-size:.6rem;color:var(--pw-ink-2)}

/* --- flow diagrams: structure, not appearance --------------------------- */
.flow{display:flex;align-items:center;flex-wrap:wrap;gap:.4rem;margin:.3rem 0}
.fbox{font-family:var(--mono);font-size:.68rem;color:var(--pw-ink-1);
  background:#202020;border:1px solid var(--pw-border);border-radius:5px;
  padding:.22em .55em}
.farr{width:26px;height:2px;background:var(--c);position:relative;flex:none}
.farr::after{content:"";position:absolute;right:0;top:-3px;border-left:6px solid var(--c);
  border-top:4px solid transparent;border-bottom:4px solid transparent}
.planefig,.faninfig,.healfig,.gutterfig,.morphrow{background:var(--pw-plane);
  border-radius:10px;padding:.9rem 1rem}
.healfig{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem}
.hf b{display:block;font-family:var(--ui);font-size:.6rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--pw-ink-2);margin-bottom:.3rem}
.gutterfig{position:relative}
.gutterfig .gg{position:absolute;inset:.9rem 1rem;border-radius:6px;
  background-image:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px);
  background-size:12px 12px}
.gutterfig svg{position:relative}
.morphrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));
  gap:.7rem .6rem;justify-items:center}
.mo{text-align:center}
.mol{display:block;font-family:var(--mono);font-size:.62rem;color:var(--pw-ink-2);
  margin-bottom:.2rem}

@media print{
  .cardspec,.legendbox,.geo,.planefig,.faninfig,.healfig,.gutterfig,.morphrow{
    box-shadow:none}
}
"""


# --------------------------------------------------------------------------
# captions — written per figure, not inherited from the old inventory.
#
# Cole on the old briefs (2026-07-27): "Many of the figure descriptions don't
# make sense. The first has a note about it being a good looking hero shot,
# which is basically a clown nose in text form."
#
# A caption says what to look at. It does not editorialise about the picture.
# --------------------------------------------------------------------------

CAPTIONS = {
    "FIG-03-03": "One card. The colour bar is the power switch; each param row "
                 "carries its own input handle.",
    "FIG-03-04": "Units, blocks and gutters, with the four card footprints "
                 "drawn against them.",
    "FIG-03-07": "Wires travel in the gutters between blocks, bundled and "
                 "centred on the grid line.",
    "FIG-04-01": "The four planes on one patch. Each is a separate wiring "
                 "system with its own rules.",
    "FIG-04-02": "Colour, stroke and word — every plane is encoded three ways, "
                 "so the key survives a greyscale print.",
    "FIG-A-02":  "The plane key, repeated for reference.",
    "FIG-05-02": "Three sources into one destination. Fan-in needs no mixer.",
    "FIG-05-05": "Removing a module heals the chain rather than leaving a hole.",
    "FIG-12-05": "The literal waveshaper at p = 8.",
    "FIG-12-06": "The band-limited bank at p = 8 — the same curve without the "
                 "fold-back.",
    "FIG-12-07": "The crossfade route at p = 8.",
    "FIG-12-08": "One knob, five positions. p = 2 is a pure sine; p = 64 is a "
                 "square.",
}

# f-inline used as a block (slot alone on its line) still needs to not look
# like a bare paragraph.
FIGURE_CSS += """
.f-inline-block{margin:.9rem 0;display:flex;align-items:center;gap:1.2rem}
"""


# ==========================================================================
# ACTIVE STATES
#
# Cole, 2026-07-28: "you can also emulate a lot of active states just by
# grabbing the activated version's CSS."
#
# So these are not approximations. Each is the GUI's own rule for the lit
# state, transcribed from gui/blocks.html's <style> block:
#
#   .gled.on          background/border --bin + 7px glow
#   .stripe.pwr.on    background --sc + 7px glow
#   .mod.bypassed     .body opacity .4
#   .mod.rec          1.5px --danger outline + 10px glow
#   .dstep.on         background --audio;  .dstep.nowat  white outline
#   .metrostrip i.lit --ctl + glow;  i.db.lit --bin, larger
#   .relaybtn.on      --bin fill, dark glyph, 9px glow
#   .tonic div span   --ctl fill;  .root span --bin;  .lead inset white
#   .szchips .on      --ink-2 background, dark text, bold
#   .mbars > div > div  --meter fill;  .clip --danger
# ==========================================================================


def led(on=False, side=False) -> str:
    cls = "led" + (" on" if on else "") + (" side" if side else "")
    return f'<span class="{cls}"></span>'


def banner(title, latch=False, extra="") -> str:
    """A binary card's coloured title banner. Yellow source, orange latch."""
    cls = "bnr latch" if latch else "bnr"
    return f'<span class="{cls}">{title}{extra}</span>'


def bincard(title, body="", latch=False, on=False, size="XS", opsel="") -> str:
    head = banner(title, latch,
                  f'<i class="opsel">{opsel}</i>' if opsel else "")
    return (f'<div class="cardspec bin sz-{size}"><div class="chead bhead">'
            f'{head}{led(on, side=True)}</div>'
            f'<div class="cbody">{body}</div></div>')


def stepgrid(rows: dict, steps=16, playhead=None) -> str:
    """The drum sequencer grid. .dstep.on is --audio; .dstep.nowat outlines."""
    out = []
    for lane, pat in rows.items():
        cells = "".join(
            f'<i class="dstep{" on" if v else ""}'
            f'{" nowat" if playhead == i else ""}"></i>'
            for i, v in enumerate(pat[:steps]))
        out.append(f'<div class="dlane"><span class="dln">{lane}</span>'
                   f'<div class="dcells">{cells}</div></div>')
    return f'<div class="stepgrid">{"".join(out)}</div>'


def metrostrip(beats=4, lit=0) -> str:
    cells = "".join(
        f'<i class="{"db " if i == 0 else ""}{"lit" if i == lit else ""}"></i>'
        for i in range(beats))
    return f'<div class="metrostrip">{cells}</div>'


def meter(l=0.62, r=0.55, clip=False) -> str:
    c = " clip" if clip else ""
    return (f'<span class="mbars"><span class="mb{c}"><i style="width:{int(l*100)}%">'
            f'</i></span><span class="mb{c}"><i style="width:{int(r*100)}%"></i>'
            f"</span></span>")


def histogram(weights, root=0, lead=None) -> str:
    NOTE = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]
    cells = "".join(
        f'<div class="{"root" if i == root else ""}'
        f'{" leadnote" if i == lead and i != root else ""}">'
        f'<i>{NOTE[i]}</i><span style="height:{int(w*100)}%"></span></div>'
        for i, w in enumerate(weights))
    return f'<div class="tonicviz">{cells}</div>'


def deckcard(state="empty", notes=None, playhead=None) -> str:
    """The Loop Deck in one of its states. .mod.rec is the recording outline."""
    rec = " rec" if state == "recording" else ""
    ph = ""
    if playhead is not None:
        col = "var(--pw-danger)" if state == "recording" else "var(--pw-audio)"
        ph = (f'<i class="dph" style="left:{int(playhead*100)}%;'
              f'background:{col}"></i>')
    nb = "".join(
        f'<i style="left:{a/8*100:.1f}%;width:{(b-a)/8*100:.1f}%;'
        f'bottom:{(n-55)*4}%"></i>'
        for a, b, n in (notes or []))
    return (f'<div class="cardspec{rec}" style="min-width:190px">'
            f'<div class="chead">{stripe(True, "service")}'
            f'<span class="ctitle">Loop Deck</span></div>'
            f'<div class="csub">midi looper · {state}</div>'
            f'<div class="deckbtns"><b class="rec">● rec</b><b>■</b><b>▶</b>'
            f'<b>✕</b></div>'
            f'<div class="deckroll">{nb}{ph}</div></div>')


def relaycard(closed=True) -> str:
    dots = "".join(f'<i class="rc {k}"></i>'
                   for k in ("audio", "notes", "binary", "mod"))
    btn = f'<span class="relaybtn{" on" if closed else ""}">⏻</span>'
    return (f'<div class="cardspec bin sz-XS" style="min-width:104px">'
            f'<div class="chead bhead">{banner("Relay", latch=True)}</div>'
            f'<div class="rcrow">{dots}</div>{btn}'
            f'<div class="rcrow">{dots}</div></div>')


def logiccard(op="AND", out=False) -> str:
    glyph = {"AND": "&", "OR": "≥1", "NOR": "≥1", "XOR": "=1",
             "SR latch": "SR", "T latch": "T"}[op]
    bub = '<i class="bub"></i>' if op == "NOR" else ""
    return (f'<div class="cardspec bin sz-XS" style="min-width:104px">'
            f'<div class="chead bhead">{banner("", latch=True, extra="")}'
            f'<i class="opsel">{op}</i>{led(out, side=True)}</div>'
            f'<div class="gate"><span class="pin">A</span>'
            f'<span class="gbody">{glyph}{bub}</span>'
            f'<span class="pin b">B</span></div></div>')


def topbar(*items) -> str:
    return f'<div class="tbstrip">{"".join(items)}</div>'


def sizechips(sizes=("S", "M", "L"), on="M") -> str:
    return "".join(f'<i class="chip{" on" if s == on else ""}">{s}</i>'
                   for s in sizes)


FIGURE_CSS += """
/* --- states, transcribed from blocks.html ------------------------------- */
.led{display:inline-block;width:9px;height:9px;border-radius:50%;
  border:1.5px solid var(--pw-ink-2);background:var(--pw-card);opacity:.7;
  vertical-align:-.05em}
.led.on{background:var(--pw-bin);border-color:var(--pw-bin);opacity:1;
  box-shadow:0 0 7px color-mix(in srgb,var(--pw-bin) 70%,transparent)}
.led.side{margin-left:auto}
.pwrbar.on{box-shadow:0 0 7px color-mix(in srgb,var(--fam) 65%,transparent)}
.cardspec.bypassed .cbody{opacity:.4}
.cardspec.rec{outline:1.5px solid var(--pw-danger);
  box-shadow:0 0 10px rgba(227,73,72,.5)}
.bnr{display:inline-flex;align-items:center;gap:.35em;background:var(--pw-bin);
  color:#16150f;font-weight:800;font-size:.66rem;letter-spacing:.02em;
  border-radius:5px;padding:.12em .5em}
.bnr.latch{background:var(--pw-binlatch)}
.bhead{gap:.4em}
.opsel{font-style:normal;font-size:.6rem;font-weight:800;padding:.08em .38em;
  border-radius:4px;background:rgba(22,21,15,.22);color:#16150f}
.bnr.latch .opsel,.chead .opsel{background:var(--pw-binlatch);color:#16150f}
.opsel.solo{font-size:.64rem;padding:.16em .55em}
.csub{font-family:var(--ui);font-size:.62rem;color:var(--pw-ink-2);
  margin:-.2rem 0 .35rem}
.deckbtns{display:flex;gap:3px;margin:.25rem 0}
.deckbtns b{flex:1;background:var(--pw-hairline);color:var(--pw-ink-1);
  border-radius:4px;font-size:.55rem;text-align:center;padding:.15em 0;
  font-weight:500}
.deckbtns b.rec{color:var(--pw-danger);font-weight:700}
.deckroll{position:relative;height:46px;background:var(--pw-plane);
  border-radius:5px;margin-top:.25rem;overflow:hidden}
.deckroll i{position:absolute;height:3px;border-radius:2px;
  background:var(--pw-danger)}
.deckroll .dph{width:1.5px;height:100%;bottom:0;border-radius:0}
.stepgrid{display:grid;gap:2px;background:var(--pw-plane);border-radius:8px;
  padding:.7rem .8rem}
.dlane{display:grid;grid-template-columns:22px 1fr;gap:4px;align-items:center}
.dln{font-family:var(--ui);font-size:.5rem;color:var(--pw-ink-2)}
.dcells{display:grid;grid-template-columns:repeat(16,1fr);gap:2px}
.dstep{height:7px;border-radius:1px;background:var(--pw-hairline)}
.dstep.on{background:var(--pw-audio)}
.dstep.nowat{outline:1.5px solid rgba(255,255,255,.9);position:relative}
.dstep.nowat::after{content:"";position:absolute;left:50%;top:-3px;bottom:-3px;
  width:1.5px;background:rgba(255,255,255,.55);transform:translateX(-50%)}
.metrostrip{display:flex;gap:5px;align-items:center}
.metrostrip i{width:7px;height:7px;border-radius:50%;
  background:var(--pw-hairline)}
.metrostrip i.db{width:11px;height:11px;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.28)}
.metrostrip i.lit{background:var(--pw-ctl);
  box-shadow:0 0 7px color-mix(in srgb,var(--pw-ctl) 70%,transparent)}
.metrostrip i.db.lit{background:var(--pw-bin);
  box-shadow:0 0 9px color-mix(in srgb,var(--pw-bin) 75%,transparent)}
.mbars{display:inline-flex;flex-direction:column;gap:2px;width:70px;
  vertical-align:-.1em}
.mb{height:5px;background:var(--pw-hairline);border-radius:3px;overflow:hidden}
.mb i{display:block;height:100%;background:var(--pw-meter)}
.mb.clip i{background:var(--pw-danger)}
.tonicviz{display:grid;grid-template-columns:repeat(12,1fr);gap:2px;height:46px;
  background:var(--pw-plane);border-radius:5px;padding:3px}
.tonicviz div{position:relative;border-radius:3px;
  background:var(--pw-hairline);overflow:hidden}
.tonicviz div span{position:absolute;bottom:0;left:0;right:0;
  background:var(--pw-ctl)}
.tonicviz div.root span{background:var(--pw-bin)}
.tonicviz div.leadnote{box-shadow:inset 0 0 0 1px rgba(255,255,255,.55)}
.tonicviz i{position:absolute;top:1px;left:0;right:0;text-align:center;
  font-style:normal;font-size:.42rem;color:var(--pw-ink-2);z-index:2}
.rcrow{display:flex;gap:4px;justify-content:center;margin:.2rem 0}
.rc{width:9px;height:9px;border-radius:50%;border:2px solid}
.rc.audio{border-color:var(--pw-audio)}.rc.notes{border-color:var(--pw-ctl)}
.rc.binary{border-color:var(--pw-bin)}.rc.mod{border-color:var(--pw-mod)}
.relaybtn{display:grid;place-content:center;width:24px;height:24px;margin:0 auto;
  border-radius:50%;border:1.5px solid var(--pw-ink-2);color:var(--pw-ink-2);
  font-size:.7rem}
.relaybtn.on{color:#111;background:var(--pw-bin);border-color:var(--pw-bin);
  box-shadow:0 0 9px color-mix(in srgb,var(--pw-bin) 70%,transparent)}
.gate{display:flex;align-items:center;gap:4px;margin-top:.3rem}
.gate .pin{font-family:var(--mono);font-size:.5rem;color:var(--pw-ink-2)}
.gbody{position:relative;flex:1;text-align:center;font-family:var(--mono);
  font-size:.6rem;color:var(--pw-ink-1);border:1px solid var(--pw-ink-2);
  border-radius:2px;padding:.35em 0}
.gbody .bub{position:absolute;right:-5px;top:50%;width:5px;height:5px;
  border-radius:50%;border:1px solid var(--pw-ink-2);
  transform:translateY(-50%);background:var(--pw-card)}
.tbstrip{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;
  background:var(--pw-surface);border-radius:8px;padding:.6rem .7rem}
.szchips{display:inline-flex;gap:2px;margin-left:auto}
"""


# ==========================================================================
# more kit
# ==========================================================================

def term(*lines) -> str:
    """Terminal output. A screenshot of a terminal is a picture of a font."""
    body = "".join(
        f'<div class="tl{" ok" if l.startswith("+") else " dim" if l.startswith("~") else ""}">'
        f'{l[1:] if l[:1] in "+~" else l}</div>' for l in lines)
    return f'<div class="term">{body}</div>'


def keycap(k, sub="") -> str:
    return (f'<span class="kc">{k}'
            + (f'<i>{sub}</i>' if sub else "") + "</span>")


def twoup(a_label, a, b_label, b) -> str:
    return (f'<div class="twoup"><div><b>{a_label}</b>{a}</div>'
            f'<div><b>{b_label}</b>{b}</div></div>')


def threeup(*pairs) -> str:
    cells = "".join(f'<div><b>{lab}</b>{h}</div>' for lab, h in pairs)
    return f'<div class="threeup">{cells}</div>'


def swatches(items) -> str:
    return ('<div class="swatches">' + "".join(
        f'<span class="sw"><i style="background:var(--fam-{k})"></i>{n}</span>'
        for k, n in items) + "</div>")


def regionmap(regions) -> str:
    """A labelled map of the page. Drawn, not photographed — a screenshot of
    the whole window at figure size makes every label unreadable."""
    cells = "".join(
        f'<div class="rg {cls}" style="{style}"><b>{n}</b><span>{lab}</span></div>'
        for n, (cls, style, lab) in enumerate(regions, 1))
    return f'<div class="regionmap">{cells}</div>'


def note_bars(notes, stuck=None) -> str:
    """The note monitor: one bar per sounding note, time running right."""
    bars = "".join(
        f'<i style="left:{a}%;width:{w}%;bottom:{b}%" class="{c}"></i>'
        for a, w, b, c in notes)
    st = (f'<i style="left:{stuck}%;width:{100-stuck}%;bottom:62%" '
          f'class="stuck"></i>') if stuck is not None else ""
    return f'<div class="notemon">{bars}{st}</div>'


FIGURE_CSS += """
.term{background:var(--pw-plane);border-radius:8px;padding:.7rem .85rem;
  font-family:var(--mono);font-size:.72rem;line-height:1.6;color:var(--pw-ink-2)}
.term .tl.ok{color:var(--pw-meter)}
.term .tl.dim{color:#6f6e66}
.kc{display:inline-grid;place-content:center;min-width:1.7em;height:1.7em;
  border-radius:4px;background:var(--pw-surface);border:1px solid var(--pw-hairline);
  color:var(--pw-ink-1);font-family:var(--ui);font-size:.66rem;font-weight:600;
  padding:0 .3em;vertical-align:-.35em}
.kc i{display:block;font-style:normal;font-size:.5rem;color:var(--pw-ink-2)}
.twoup,.threeup{display:grid;gap:1rem;background:var(--pw-plane);
  border-radius:10px;padding:.9rem 1rem;align-items:start}
.twoup{grid-template-columns:1fr 1fr}
.threeup{grid-template-columns:repeat(3,1fr)}
.twoup .cardspec,.threeup .cardspec,.gaterow .cardspec{min-width:0!important;
  width:100%;box-sizing:border-box}
.twoup>div,.threeup>div{min-width:0}
.deckroll i{min-width:2px}
.twoup b,.threeup b{display:block;font-family:var(--ui);font-size:.58rem;
  letter-spacing:.1em;text-transform:uppercase;color:var(--pw-ink-2);
  margin-bottom:.4rem;font-weight:650}
.swatches{display:flex;flex-wrap:wrap;gap:.5rem .9rem;background:var(--pw-plane);
  border-radius:10px;padding:.8rem 1rem}
.sw{display:inline-flex;align-items:center;gap:.4em;font-family:var(--ui);
  font-size:.7rem;color:var(--pw-ink-1)}
.sw i{width:4px;height:15px;border-radius:3px;display:inline-block}
.regionmap{position:relative;height:250px;background:var(--pw-plane);
  border-radius:10px;padding:6px;
  background-image:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px);
  background-size:14px 14px}
.rg{position:absolute;border:1px solid var(--pw-border);border-radius:6px;
  background:rgba(32,32,32,.9);display:flex;align-items:center;gap:.4em;
  padding:.3em .5em;font-family:var(--ui);font-size:.62rem;color:var(--pw-ink-2)}
.rg b{display:grid;place-content:center;width:15px;height:15px;border-radius:50%;
  background:var(--pw-ink-1);color:var(--pw-plane);font-size:.55rem;flex:none}
.notemon{position:relative;height:70px;background:var(--pw-plane);
  border-radius:6px;overflow:hidden}
.notemon i{position:absolute;height:4px;border-radius:2px;background:var(--pw-ctl)}
.notemon i.arp{background:#e8c832}.notemon i.deck{background:var(--pw-danger)}
.notemon i.stuck{background:var(--pw-danger);opacity:.85}
"""


# ==========================================================================
# module card specimens — Ch 12, 13, 14
#
# A module's figure is its card, small, showing the parameters the entry
# discusses and the graphic if it has one. Not a photograph of a card on a
# board: a card, on its own, beside the paragraph about it.
# ==========================================================================

MODCARDS = {
    # key: (title, family, rows, graphic-kind, p)
    "wobble_saw": ("Instrument", "voice", [("freq", .42, "110.0"),
                   ("wobble", .3, "4.000"), ("depth", .5, "0.500"),
                   ("amp", .25, "0.250")], "tri", 2),
    "pulse_pad": ("PW Pulse Pad", "voice", [("freq", .5, "220.0"),
                  ("wave", 0, "pulse"), ("detune", .24, "12.00"),
                  ("pwm", .44, "0.200"), ("amp", .22, "0.220")], "pulse", 2),
    "fm_bell": ("FM Bell", "voice", [("freq", .6, "440.0"),
                ("ratio", .4, "3.510"), ("index", .33, "4.000"),
                ("decay", .3, "2.500")], "sine", 2),
    "pluck": ("Pluck", "voice", [("freq", .5, "220.0"), ("decay", .32, "4.000"),
              ("damp", .44, "0.400"), ("amp", .35, "0.350")], "noise", 2),
    "wind": ("Wind", "voice", [("center", .35, "700.0"), ("gust", .6, "0.600"),
             ("resonance", .3, "1.000"), ("amp", .3, "0.300")], "noise", 2),
    "audio_in": ("Audio In", "input", [("gain", .25, "1.000")], "", 2),
    "drone": ("Drone", "service", [("freq", .3, "55.00"), ("amp", .16, "0.160"),
              ("shape", .35, "0.350"), ("sub", .4, "0.400"),
              ("cutoff", .38, "900.0")], "sine", 2),
    "lowpass": ("Low-pass Filter", "filter", [("cutoff", .55, "1200"),
                ("resonance", .5, "0.500")], "", 2),
    "telephone": ("Telephone", "filter", [("low", .3, "380.0"),
                  ("high", .35, "3200"), ("crunch", .25, "3.000"),
                  ("mix", 1, "1.000")], "", 2),
    "echo": ("Echo", "time", [("time", .19, "0.375"), ("feedback", .42, "0.400"),
             ("mix", .35, "0.350")], "", 2),
    "reverb": ("Reverb", "time", [("room", .6, "0.600"), ("damp", .5, "0.500"),
               ("mix", .3, "0.300")], "", 2),
    "chorus": ("Chorus", "time", [("rate", .2, "0.400"), ("depth", .5, "0.500"),
               ("mix", .4, "0.400")], "", 2),
    "flanger": ("Flanger", "time", [("rate", .12, "0.250"), ("depth", .7, "0.700"),
                ("feedback", .44, "0.400"), ("mix", .5, "0.500")], "", 2),
    "phaser": ("Phaser", "time", [("rate", .15, "0.300"), ("depth", .8, "0.800"),
               ("mix", .5, "0.500")], "", 2),
    "autopan": ("Auto Pan", "time", [("rate", .18, "0.500"),
                ("depth", .7, "0.700")], "", 2),
    "drive": ("Drive", "dirt", [("gain", .33, "4.000"), ("tone", .4, "4000"),
              ("mix", 1, "1.000")], "", 2),
    "bitcrush": ("Bitcrush", "dirt", [("srate", .42, "8000"),
                 ("bits", .57, "10.00"), ("mix", 1, "1.000")], "", 2),
    "wavefolder": ("Wavefolder", "dirt", [("fold", .3, "2.500"),
                   ("symmetry", .5, "0.000"), ("mix", 1, "1.000")], "", 2),
    "compressor": ("Compressor", "dyn", [("threshold", .3, "0.300"),
                   ("ratio", .35, "4.000"), ("attack", .2, "0.010"),
                   ("makeup", .3, "1.300")], "", 2),
    "pitchshift": ("Pitch Shift", "vox", [("semitones", .5, "0.000"),
                   ("mix", 1, "1.000"), ("window", .2, "0.040")], "", 2),
    "ringmod": ("Ring Mod", "vox", [("carrier", .4, "200.0"),
                ("mix", .8, "0.800")], "", 2),
    "scope_tap": ("Scope Tap", "io", [("gain", .5, "1.000")], "sine", 2),
    "power_shaper": ("Power Shaper", "psine", [("p", .45, "8.000"),
                     ("drive", .3, "1.000"), ("mix", 1, "1.000")], "psine", 8),
    "power_sine_shaper": ("Psine Waveshaper", "psine", [("freq", .5, "220.0"),
                          ("p", .45, "8.000"), ("amp", .3, "0.300")], "psine", 8),
    "power_sine_additive": ("Psine Harmonic Bank", "psine",
                            [("freq", .5, "220.0"), ("p", .45, "8.000"),
                             ("amp", .3, "0.300")], "psine", 8),
    "power_sine_blend": ("Psine Crossfade", "psine", [("freq", .5, "220.0"),
                         ("p", .45, "8.000"), ("amp", .3, "0.300")], "psine", 8),
}

# Ch12/13/14 slot -> module key
MODFIG = {
    "FIG-12-01": "wobble_saw", "FIG-12-02": "pulse_pad", "FIG-12-03": "fm_bell",
    "FIG-12-04": "pluck", "FIG-13-01": "audio_in", "FIG-13-02": "wind",
    "FIG-13-03": "drone", "FIG-14-01": "lowpass", "FIG-14-02": "telephone",
    "FIG-14-03": "echo", "FIG-14-04": "reverb", "FIG-14-05": "chorus",
    "FIG-14-06": "flanger", "FIG-14-07": "phaser", "FIG-14-08": "autopan",
    "FIG-14-09": "drive", "FIG-14-10": "bitcrush", "FIG-14-11": "wavefolder",
    "FIG-14-12": "compressor", "FIG-14-13": "pitchshift", "FIG-14-14": "ringmod",
    "FIG-14-15": "scope_tap", "FIG-14-18": "power_shaper",
}

_FAMCOL = {"psine": "--fam-psine", "voice": "--fam-voice",
           "service": "--fam-service", "io": "--fam-io", "input": "--fam-input"}

for _fid, _k in MODFIG.items():
    _t, _fam, _rows, _g, _p = MODCARDS[_k]
    _gfx = osc(_g, w=130, h=38, p=_p,
               color=_FAMCOL.get(_fam, "--fam-voice")) if _g else ""
    F(_fid, "spot", card(_t, _rows, fam=_fam, size="M" if _gfx else "S",
                         graphic=_gfx))
    CAPTIONS.setdefault(_fid, f"The {_t} card.")

# the three psine cards already had entries above; refresh with real params
for _fid, _k in [("FIG-12-05", "power_sine_shaper"),
                 ("FIG-12-06", "power_sine_additive"),
                 ("FIG-12-07", "power_sine_blend")]:
    _t, _fam, _rows, _g, _p = MODCARDS[_k]
    F(_fid, "spot", card(_t, _rows, fam=_fam, size="M",
                         graphic=osc("psine", w=130, h=38, p=_p,
                                     color="--fam-psine")))


def env(a=.12, d=.15, s=.62, r=.3, w=200, h=52) -> str:
    """An ADSR envelope. Shared by every gated voice, so it is drawn once."""
    x0, pk, sus, rel = 4, 4 + a * (w - 8) * .5, 4 + (a + d) * (w - 8) * .6, w - 4
    y0, y1 = h - 5, 5
    ys = y0 - s * (y0 - y1)
    return (f'<svg class="envfig" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'<path d="M{x0} {y0} L{pk:.0f} {y1} L{sus:.0f} {ys:.0f} '
            f'L{w*0.68:.0f} {ys:.0f} L{rel} {y0}" fill="none" '
            f'stroke="var(--pw-ctl)" stroke-width="1.8" stroke-linejoin="round"/>'
            f'<line x1="{w*0.68:.0f}" y1="{y1}" x2="{w*0.68:.0f}" y2="{y0}" '
            f'stroke="var(--pw-ink-2)" stroke-width="1" stroke-dasharray="2 3"/>'
            f'<text x="{pk:.0f}" y="{h-1}" class="et">A</text>'
            f'<text x="{sus:.0f}" y="{h-1}" class="et">D</text>'
            f'<text x="{w*0.5:.0f}" y="{h-1}" class="et">S</text>'
            f'<text x="{w*0.84:.0f}" y="{h-1}" class="et">R</text></svg>')


def levels(w=260, h=64) -> str:
    """A threshold reading a rising CV, with hysteresis and one edge."""
    import math
    pts = " ".join(f"{i/60*w:.0f},{h-8-(0.5+0.42*math.sin(i/60*6.5))*(h-18):.0f}"
                   for i in range(61))
    ly = h - 8 - 0.62 * (h - 18)
    hy = h - 8 - 0.68 * (h - 18)
    return (f'<svg class="levfig" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'<polyline points="{pts}" fill="none" stroke="var(--pw-mod)" '
            f'stroke-width="1.8"/>'
            f'<line x1="0" y1="{ly:.0f}" x2="{w}" y2="{ly:.0f}" '
            f'stroke="var(--pw-bin)" stroke-width="1"/>'
            f'<line x1="0" y1="{hy:.0f}" x2="{w}" y2="{hy:.0f}" '
            f'stroke="var(--pw-bin)" stroke-width="1" stroke-dasharray="3 3" '
            f'opacity=".6"/>'
            f'<text x="4" y="{ly-4:.0f}" class="et">level</text>'
            f'<text x="4" y="{hy-4:.0f}" class="et">+ hysteresis</text></svg>')


def buslist(items) -> str:
    return ('<div class="buslist">' + "".join(
        f'<div class="bl"><span class="bn">{n}</span>'
        f'<span class="bd">{d}</span></div>' for n, d in items) + "</div>")


def palette_sections(secs) -> str:
    return ('<div class="palfig">' + "".join(
        f'<div class="ps"><b>{h}</b>' + "".join(
            f'<span class="pb"><i style="background:var(--fam-{f})"></i>{n}</span>'
            for n, f in items) + "</div>" for h, items in secs) + "</div>")


def minilayout(cards, flex=False) -> str:
    cls = "mlay flex" if flex else "mlay"
    return (f'<div class="{cls}">' + "".join(
        f'<i style="grid-area:{a}"></i>' for a in cards) + "</div>")


FIGURE_CSS += """
.envfig,.levfig{display:block;background:var(--pw-plane);border-radius:8px}
.et{fill:var(--pw-ink-2);font-size:7.5px;font-family:ui-monospace,monospace}
.buslist{display:grid;gap:.35rem;background:var(--pw-plane);border-radius:10px;
  padding:.85rem 1rem}
.bl{display:grid;grid-template-columns:9rem 1fr;gap:.7rem;align-items:baseline}
.bn{font-family:var(--mono);font-size:.68rem;color:var(--pw-ink-1)}
.bd{font-family:var(--ui);font-size:.7rem;color:var(--pw-ink-2)}
.palfig{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
  gap:.7rem;background:var(--pw-surface);border-radius:10px;padding:.8rem}
.ps b{display:block;font-family:var(--ui);font-size:.52rem;letter-spacing:.12em;
  text-transform:uppercase;color:var(--pw-ink-2);margin-bottom:.3rem}
.pb{display:flex;align-items:center;gap:.4em;font-family:var(--ui);
  font-size:.64rem;color:var(--pw-ink-1);background:var(--pw-card);
  border-radius:5px;padding:.2em .4em;margin-bottom:2px}
.pb i{width:3px;height:11px;border-radius:2px;flex:none}
.mlay{display:grid;grid-template-columns:repeat(4,1fr);
  grid-template-rows:repeat(3,26px);gap:5px;background:var(--pw-plane);
  border-radius:8px;padding:8px}
.mlay i{background:#202020;border:1px solid var(--pw-border);border-radius:5px}
.mlay.flex i{border-radius:7px;opacity:.92}
"""


# ==========================================================================
# Chapters 1, 2, 3
# ==========================================================================

F("FIG-01-01", "column",
  '<div class="planefig">' + flow("psine", "lowpass", "echo", "master")
  + f'<div style="margin-top:.7rem">{osc("psine", w=300, h=52, p=8)}</div></div>')
CAPTIONS["FIG-01-01"] = ("A voice, two effects, and the wire out. The waveform "
                         "is the psine morph at p = 8.")

F("FIG-01-02", "column",
  '<div class="planefig">'
  + flow("modules/*.py", "rack", "scsynth", "sound")
  + flow("browser", "server", "rack", plane="notes")
  + flow("MIDI in", "keys", "voice", plane="notes")
  + '<div class="reload">hot reload: a saved module file respawns in place</div>'
  "</div>")
CAPTIONS["FIG-01-02"] = ("Four processes. Python owns the graph, scsynth makes "
                         "the sound, the browser is a view, MIDI is an input.")

F("FIG-01-03", "column",
  '<div class="planefig">'
  + flow("keys", "voice", "wobble_saw", plane="notes")
  + flow("wobble_saw", "master")
  + f'<div class="kcrow">{keycap("A")}{keycap("W")}{keycap("S")}{keycap("E")}'
    f'{keycap("D")} &nbsp; {keycap("Z", "oct −")}{keycap("X", "oct +")}'
    f'&nbsp; {keycap("Caps", "sustain")}</div></div>')
CAPTIONS["FIG-01-03"] = "The shortest path to a note, and the keys that play it."

F("FIG-02-01", "column",
  term("$ python -m synthbase test",
       "~ booting scsynth …",
       "+ scsynth ready on 57110",
       "+ audio device: system default, 48000 Hz",
       "+ 26 modules loaded",
       "+ test tone 440 Hz for 2.0 s",
       "+ ok"))
CAPTIONS["FIG-02-01"] = ("A clean `test` run. If you do not reach `ok`, stop "
                         "here — §2.9 has the fix.")

F("FIG-02-02", "spot",
  card("Master Out", [("volume", .8, "80%")], fam="io", size="S")
  + '<div class="devpick"><b>output</b><span>system default</span>'
    '<span class="sel">Scarlett 2i2</span><span>MacBook Speakers</span></div>')
CAPTIONS["FIG-02-02"] = ("The output picker on the Master Out card. It only "
                         "appears when there is more than one device.")

F("FIG-02-03", "column",
  term("$ python -m synthbase devices",
       "audio out:", "~   0  MacBook Pro Speakers",
       "+   1  Scarlett 2i2 USB", "midi in:",
       "+   0  CP88", "~   1  IAC Driver Bus 1"))
CAPTIONS["FIG-02-03"] = "What the machine can see. Index, then name."

F("FIG-02-04", "column",
  '<div class="planefig">'
  + flow("Python", "scsynth", plane="mod")
  + flow("Python", "browser", plane="notes")
  + flow("scsynth", "audio interface")
  + "</div>")
CAPTIONS["FIG-02-04"] = ("Three processes and what runs between them: OSC to "
                         "the engine, a websocket to the page, audio to the "
                         "interface.")

F("FIG-02-05", "column",
  buslist([("gui", "the instrument. What you want almost always."),
           ("play", "a patch, no browser. Headless performance."),
           ("test", "boot, tone, exit. Proves the engine only."),
           ("devices", "list hardware and exit.")]))
CAPTIONS["FIG-02-05"] = "Four run modes. Only one of them is the instrument."

F("FIG-03-01", "column", regionmap([
    ("bar", "left:6px;right:6px;top:6px;height:22px", "top bar — global controls"),
    ("pal", "left:6px;top:34px;width:74px;bottom:6px", "palette"),
    ("brd", "left:86px;right:6px;top:34px;bottom:44px", "board — cards and wires"),
    ("gut", "left:86px;right:6px;bottom:6px;height:32px", "view controls"),
]))
CAPTIONS["FIG-03-01"] = ("The four regions. Everything else in this manual "
                         "points back at these names.")

F("FIG-03-05", "column", twoup(
    "blocks — snapped to the grid",
    minilayout(["1/1/2/2", "1/2/2/3", "2/1/3/3", "3/3/4/5"]),
    "flex — free position, auto height",
    minilayout(["1/1/2/3", "2/2/3/4", "1/3/3/5", "3/1/4/3"], flex=True)))
CAPTIONS["FIG-03-05"] = ("The same patch in both modes. The wiring is "
                         "identical; only the arrangement differs.")

# FIG-03-02 retired: §3.2 now names each top-bar control in a sentence with
# its icon inline (FIG-TB-*), which is what the strip was trying to be.
F("FIG-03-06", "column", threeup(
    ("1 · grab", f'<div class="dragfig">{handle("out","audio")}'
     '<span class="dl">press an out handle</span></div>'),
    ("2 · drag", f'<div class="dragfig">{wire("audio", w=90, label=False)}'
     '<span class="dl">a live wire follows</span></div>'),
    ("3 · land", f'<div class="dragfig">{handle("in","audio")}'
     '<span class="dl">release on an in handle</span></div>')))
CAPTIONS["FIG-03-06"] = ("Drawing a wire. Release over nothing and the drag is "
                         "abandoned — nothing is created.")

F("FIG-03-09", "spot", palette_sections([
    ("voices", [("Instrument", "voice"), ("PW Pulse Pad", "voice"),
                ("Psine ×3", "psine")]),
    ("fx", [("Low-pass", "filter"), ("Echo", "time"), ("Drive", "dirt")]),
]))
CAPTIONS["FIG-03-09"] = ("The palette groups by family. Reaching for the wrong "
                         "section is the commonest mistake.")

F("FIG-03-10", "column", twoup(
    "as placed",
    minilayout(["1/1/2/2", "2/3/3/4", "1/4/2/5", "3/2/4/3"]),
    "after tidy",
    minilayout(["1/1/2/2", "2/1/3/2", "3/1/4/2", "1/3/2/4"])))
CAPTIONS["FIG-03-10"] = ("Tidy compacts each connected tree into a column in "
                         "signal order. It moves cards, never wires.")

F("FIG-03-11", "column", twoup(
    "wired — one path",
    note_bars([(10, 18, 40, ""), (34, 22, 55, ""), (62, 20, 30, "")]),
    "unwired — everything",
    note_bars([(6, 14, 40, ""), (14, 20, 62, "arp"), (30, 12, 25, "deck"),
               (44, 18, 55, ""), (58, 24, 35, "arp"), (76, 16, 70, "deck")])))
CAPTIONS["FIG-03-11"] = ("A monitor shows the path it is wired to. Unwired, it "
                         "shows every source at once.")


# ==========================================================================
# Chapters 4-8
# ==========================================================================

F("FIG-04-03", "column", twoup(
    "global — outside the graph",
    buslist([("transport", "one clock, everywhere"), ("master volume", "one fader"),
             ("keys octave", "one setting"), ("MIDI port", "one input")]),
    "wired — defined by the graph",
    buslist([("audio", "every route"), ("notes", "who plays whom"),
             ("binary", "what gates what"), ("modulation", "what moves what")])))
CAPTIONS["FIG-04-03"] = ("If it has one value for the whole instrument it is "
                         "global; if it has a wire it is not.")

F("FIG-04-04", "column",
  note_bars([(8, 16, 45, ""), (28, 12, 60, ""), (44, 20, 35, "")], stuck=70))
CAPTIONS["FIG-04-04"] = ("Every note-on gets an off. The bar that never ends is "
                         "a stuck note — hit panic.")

F("FIG-05-01", "column", twoup(
    "the chain — the order in the file",
    flow("pulse_pad", "lowpass", "echo", "master"),
    "the overlay — the wires you drew",
    flow("pulse_pad", "echo", "master")))
CAPTIONS["FIG-05-01"] = ("Two layers, both true. Once you draw a wire, the "
                         "overlay is the one that routes audio.")

F("FIG-05-03", "column",
  '<div class="planefig">' + flow("reverb", "null bus")
  + '<div class="reload">still running, still costing CPU, simply inaudible</div>'
  + "</div>")
CAPTIONS["FIG-05-03"] = ("A disconnected output parks on the null bus. It is "
                         "alive, not removed.")

F("FIG-05-04", "column",
  '<div class="planefig">' + flow("pluck", "drive", "reverb", "master")
  + '<div class="reload">execution order is derived from the wires, not from '
    'the chain list</div></div>')
CAPTIONS["FIG-05-04"] = ("Sources run before their destinations because the "
                         "wires say so.")

F("FIG-05-06", "column", twoup(
    "enabled",
    card("Echo", [("time", .19, "0.375"), ("feedback", .42, "0.400")],
         fam="time", size="S"),
    "bypassed",
    card("Echo", [("time", .19, "0.375"), ("feedback", .42, "0.400")],
         fam="time", size="S", on=False).replace('class="cardspec',
                                                 'class="cardspec bypassed')))
CAPTIONS["FIG-05-06"] = ("Bypass dims the body and swaps in a pass-through. The "
                         "card, its wires and its settings all stay.")

F("FIG-05-07", "spot",
  card("Master Out", [("volume", .8, "80%")], fam="io", size="S")
  + f'<div class="mrow">{meter(.62, .55)}</div>')
CAPTIONS["FIG-05-07"] = "The master card: one fader, two meters, a limiter behind them."

F("FIG-06-01", "column",
  '<div class="planefig">'
  + flow("keys", "arp", "voice", plane="notes")
  + flow("arp", "deck", "voice", plane="notes")
  + '<div class="reload">the default wiring — playable the moment it loads</div>'
  "</div>")
CAPTIONS["FIG-06-01"] = "How a fresh launch is wired before you touch anything."

F("FIG-06-02", "spot", flow("MIDI", "keys", "arp", plane="notes"))
CAPTIONS["FIG-06-02"] = ("`keys` is a source only. Wire to what it feeds, never "
                         "into it.")

F("FIG-06-03", "column",
  '<div class="planefig">'
  + flow("keys", "Mono Voice", "pulse_pad", plane="notes")
  + flow("keys", "Poly Voice ×8", "fm_bell", plane="notes")
  + flow("keys", "Drone Voice", "pluck", plane="notes")
  + "</div>")
CAPTIONS["FIG-06-03"] = ("Three allocations off one keyboard. They differ on one "
                         "axis: how many notes may sound at once.")

F("FIG-06-04", "column", flow("keys", "arp", "voice", "source", plane="notes"))
CAPTIONS["FIG-06-04"] = ("The arp sits between keys and voice. Bypass it and the "
                         "route still plays.")

F("FIG-06-05", "spot",
  card("Theory Wizard", [("trigger", .3, "1 bar"), ("octave", .4, "C2"),
                         ("memory", .5, "6.0 s")], fam="service", size="M",
       graphic=histogram([1, .05, .1, 0, .35, .2, 0, .8, 0, .3, 0, .15],
                         root=0, lead=7)))
CAPTIONS["FIG-06-05"] = ("The Estimator listening. The amber bar is the committed "
                         "root; the outlined one is what is currently leading.")

F("FIG-06-06", "spot",
  card("Key Shifter", [("key", .2, "C"), ("length", .35, "8 bars")],
       fam="service", size="M",
       graphic='<div class="ksfig"><i class="on">C</i><i></i><i class="on">F</i>'
               '<i></i><i class="on">G</i><i></i><i class="on">F</i><i></i></div>'))
CAPTIONS["FIG-06-06"] = "A key shifter with an eight-step progression loaded."

F("FIG-06-07", "column",
  '<div class="ksfig wide"><i class="on nowat">C</i><i></i><i class="on">F</i>'
  '<i></i><i class="on">G</i><i></i><i class="on">F</i><i></i></div>')
CAPTIONS["FIG-06-07"] = ("One step per bar, advancing with the transport. The "
                         "outlined cell is where the clock is now.")

F("FIG-06-08", "column",
  note_bars([(4, 10, 30, ""), (10, 14, 48, "arp"), (24, 8, 62, "arp"),
             (34, 18, 40, ""), (48, 10, 55, "arp"), (60, 22, 28, "deck"),
             (76, 14, 66, "")]))
CAPTIONS["FIG-06-08"] = ("Notes flowing. Colour is the source: green keys, amber "
                         "arp, red deck.")

F("FIG-07-01", "column",
  '<div class="planefig">'
  + flow("button", "logic", "arp.power", plane="binary")
  + f'<div class="mrow">{led(False)} lo &nbsp;·&nbsp; {led(True)} hi</div>'
  + '<div class="reload">one level, not a stream of events — edges are derived '
    'from it</div></div>')
CAPTIONS["FIG-07-01"] = ("Binary carries a level. Everything else — pings, "
                         "triggers — is an edge derived from it changing.")

F("FIG-07-02", "column",
  '<div class="gaterow">' + "".join(logiccard(op, out=(op in ("AND", "SR latch")))
                                    for op in ("AND", "OR", "NOR", "XOR",
                                               "SR latch", "T latch")) + "</div>")
CAPTIONS["FIG-07-02"] = ("Six operations, one card. The inputs stay `:a` and "
                         "`:b` whichever you choose, so a swap never drops a wire.")

F("FIG-07-03", "spot", relaycard(closed=True))
CAPTIONS["FIG-07-03"] = ("A relay. Each circuit takes the colour of the kind "
                         "wired into it; the button opens and closes all of them.")

F("FIG-07-04", "column", twoup("closed — signal passes", relaycard(True),
                               "open — signal held", relaycard(False)))
CAPTIONS["FIG-07-04"] = ("Opening a relay does not change the picture — the wire "
                         "stays drawn. Only the LED moves.")

F("FIG-07-05", "spot",
  card("Threshold", [("level", .35, "0.350"), ("hyst", .1, "0.020"),
                     ("edge", .2, "rising")], fam="service", size="S"))
CAPTIONS["FIG-07-05"] = ("A threshold turns a modulation value into a binary "
                         "level. Its CV input takes exactly one LFO.")

F("FIG-07-06", "column", twoup(
    f"before — nothing clicked {led(False)}",
    flow("clock", "logic", "arp.power", plane="binary"),
    f"after — the logic fired {led(True)}",
    flow("clock", "logic", "arp.power", plane="binary")))
CAPTIONS["FIG-07-06"] = ("Indicators follow state, not your mouse. Nobody "
                         "touched the arp — the logic did.")

F("FIG-08-01", "spot",
  card("LFO ∿", [("rate", .3, "1.00Hz"), ("depth", .5, "50%"),
                 ("shape", .2, "sine")], fam="service", size="M",
       graphic=osc("sine", w=130, h=34, color="--fam-service")))
CAPTIONS["FIG-08-01"] = ("An LFO card. One card can drive many destinations; the "
                         "subtitle counts them.")

F("FIG-08-02", "column",
  '<div class="planefig">' + flow("LFO", "lowpass.cutoff", plane="mod")
  + '<div class="prow" style="max-width:230px;margin-top:.5rem">'
    f'{handle("in","mod")}<span class="pn">cutoff</span>'
    '<span class="ptrack lfoswept"><i style="left:55%"></i></span>'
    '<span class="pv">2080</span></div>'
  + '<div class="reload">a mapped row wears the amplitude band and rides it'
    '</div></div>')
CAPTIONS["FIG-08-02"] = ("Drop an LFO output on a parameter handle. The slider "
                         "then shows the band it is sweeping.")

F("FIG-08-03", "column",
  '<div class="morphrow">' + "".join(
      f'<div class="mo"><span class="mol">{n}</span>'
      f'{osc(k, w=112, h=36, color="--fam-service")}</div>'
      for n, k in [("sine", "sine"), ("tri", "tri"), ("ramp", "saw"),
                   ("square", "square"), ("s&h", "noise")]) + "</div>")
CAPTIONS["FIG-08-03"] = ("The five LFO shapes. `s&h` holds one random value per "
                         "cycle rather than moving continuously.")

F("FIG-08-04", "column", levels())
CAPTIONS["FIG-08-04"] = ("Hysteresis is the gap between the level that turns it "
                         "on and the level that lets it off again.")

F("FIG-08-05", "spot",
  card("Scope Tap", [("gain", .5, "1.000")], fam="io", size="M",
       graphic=osc("psine", w=130, h=38, p=6, color="--pw-meter")))
CAPTIONS["FIG-08-05"] = ("Splice a Scope Tap anywhere to see that point without "
                         "changing the sound.")


# ==========================================================================
# Chapters 9-16 and the appendices
# ==========================================================================

F("FIG-09-01", "column",
  '<div class="planefig"><div class="mrow">'
  '<span class="tplay stopped">⏵</span>'
  f'<span class="tlab">stopped</span> &nbsp;&nbsp; {metrostrip(4, 0)}</div>'
  '<div class="reload">the card and the top bar are two views of one clock — '
  'move either and both follow</div></div>')
CAPTIONS["FIG-09-01"] = ("A fresh launch comes up stopped. The keys still play; "
                         "only clocked things wait.")

F("FIG-09-02", "column",
  buslist([("1/1", "4 beats — one bar"), ("1/2", "2 beats"),
           ("1/4", "1 beat"), ("1/4.", "1.5 beats — dotted"),
           ("1/4T", "0.667 beats — triplet"), ("1/8", "half a beat"),
           ("1/16", "a quarter beat")]))
CAPTIONS["FIG-09-02"] = ("Divisions in beats. Change one mid-flight and the next "
                         "event lands in phase, not offset.")

F("FIG-09-03", "column",
  stepgrid({"kick":  [1,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0],
            "snare": [0,0,0,0,1,0,0,0,0,0,0,0,1,0,0,0],
            "hat":   [1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,1],
            "clap":  [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]}, playhead=4))
CAPTIONS["FIG-09-03"] = ("Sixteen steps, four lanes. The outlined cell is the "
                         "step the transport is on.")

F("FIG-09-04", "column",
  '<div class="planefig">' + flow("drums", "master")
  + flow("drums", "drive", "master")
  + '<div class="reload">aim them anywhere; if the target is removed they fall '
    'back to master rather than going silent</div></div>')
CAPTIONS["FIG-09-04"] = "Drums are a source like any other — route them deliberately."

F("FIG-09-05", "spot", flow("controller", "keys", "arp", plane="notes"))
CAPTIONS["FIG-09-05"] = ("MIDI arrives at `keys` and nowhere else. Splits and "
                         "layers belong on the controller.")

F("FIG-10-01", "column", threeup(
    ("idle", deckcard("empty")),
    ("recording", deckcard("recording",
                           [(0, .5, 60), (1, 1.5, 64)], playhead=.32)),
    ("playing", deckcard("playing",
                         [(0, .5, 60), (1, 1.5, 64), (2, 3, 67), (4, 5.5, 72)],
                         playhead=.64))))
CAPTIONS["FIG-10-01"] = ("The deck's three working states. The subtitle is the "
                         "only place it tells you which.")

F("FIG-10-02", "spot",
  deckcard("recording", [(0, .5, 60), (1, 1.5, 64)], playhead=.38))
CAPTIONS["FIG-10-02"] = ("Recording runs a fixed window. It captures what is "
                         "wired into it — wire nothing and it records nothing.")

F("FIG-10-03", "column", twoup(
    "wired raw — before the voice",
    flow("keys", "deck", plane="notes"),
    "wired voiced — after it",
    flow("keys", "voice", "deck", plane="notes")))
CAPTIONS["FIG-10-03"] = ("Where you wire the deck decides what it stores: the "
                         "keys you pressed, or the notes that sounded.")

F("FIG-10-04", "column",
  '<div class="planefig">' + flow("record", "play", "overdub", "clear",
                                  plane="binary")
  + '<div class="reload">`bars` can only change from empty — clear first</div>'
  "</div>")
CAPTIONS["FIG-10-04"] = "The cycle. Overdub layers onto the take; clear empties it."

F("FIG-11-03", "column", swatches([
    ("voice", "voice"), ("input", "input"), ("psine", "psine"),
    ("filter", "filter"), ("time", "time"), ("dirt", "dirt"),
    ("dyn", "dyn"), ("vox", "vox"), ("service", "service"), ("io", "io")]))
CAPTIONS["FIG-11-03"] = ("The family colours. Family groups by appearance, not "
                         "by what a module does to the signal.")

F("FIG-11-01", "column", palette_sections([
    ("allocation", [("Mono Voice", "service"), ("Poly Voice", "service"),
                    ("Drone Voice", "service")]),
    ("control", [("LFO ∿", "service"), ("Key Shifter", "service")]),
    ("triggers", [("Button", "dirt"), ("Clock", "dirt"), ("Threshold", "dirt")]),
    ("voices", [("Instrument", "voice"), ("PW Pulse Pad", "voice"),
                ("Psine ×3", "psine"), ("Drone", "service")]),
    ("fx", [("Low-pass", "filter"), ("Echo", "time"), ("Drive", "dirt"),
            ("Compressor", "dyn"), ("Ring Mod", "vox")]),
    ("monitors", [("Scope Tap", "io"), ("Note Monitor", "io")]),
]))
CAPTIONS["FIG-11-01"] = "The palette, grouped the way it is grouped on screen."

F("FIG-11-02", "spot",
  card("PW Pulse Pad", [("freq", .5, "220.0"), ("wave", 0, "pulse"),
                        ("porta", 0, "off"), ("amp", .22, "0.220")],
       fam="voice", size="S"))
CAPTIONS["FIG-11-02"] = ("Four control types on one card: exponential `freq`, "
                         "a select, a toggle, and a linear `amp`.")

F("FIG-12-09", "column", osc("pulse", w=430, h=64, color="--fam-voice"))
CAPTIONS["FIG-12-09"] = ("The pad's pulse wave. `pwm` moves the duty cycle "
                         "slowly; `detune` stacks a second copy beside it.")

F("FIG-12-10", "column", env())
CAPTIONS["FIG-12-10"] = ("Every gated voice shares this envelope. Note-on runs "
                         "attack and decay; note-off starts release.")

F("FIG-13-04", "column",
  note_bars([(6, 16, 40, ""), (26, 14, 40, ""), (44, 52, 40, "")]))
CAPTIONS["FIG-13-04"] = ("The drone holds the last root instead of stopping. It "
                         "is driven by notes but never gated by them.")

F("FIG-14-16", "column", twoup(
    "distortion before reverb",
    flow("voice", "drive", "reverb", "master"),
    "reverb before distortion",
    flow("voice", "reverb", "drive", "master")))
CAPTIONS["FIG-14-16"] = ("Order is the effect. The first is a distorted sound in "
                         "a room; the second is a distorted room.")

F("FIG-14-17", "column", twoup(
    "pan the whole mix",
    flow("voice", "echo", "autopan", "master"),
    "pan one voice",
    flow("voice", "autopan", "echo", "master")))
CAPTIONS["FIG-14-17"] = ("Auto Pan acts on everything upstream of it. Its "
                         "position is a decision, not a habit.")

F("FIG-15-01", "column",
  '<div class="planefig">' + flow("(empty)", "wobble_saw", "master")
  + flow("keys", "voice", "wobble_saw", plane="notes") + "</div>")
CAPTIONS["FIG-15-01"] = "A blank patch, one source, and the two wires that make it sound."

F("FIG-15-02", "column",
  '<div class="planefig">' + flow("wobble_saw", "lowpass", "master")
  + '<div class="reload">dropped on the wire, it splices in where you dropped it'
    '</div></div>')
CAPTIONS["FIG-15-02"] = "An effect added mid-wire lands where you dropped it."

F("FIG-15-03", "column", threeup(
    ("1 · running", flow("voice", "echo", "master")),
    ("2 · cut", flow("voice", "master")),
    ("3 · rewired", flow("voice", "reverb", "master"))))
CAPTIONS["FIG-15-03"] = ("Rewiring a live rack. Nothing stops; removal heals the "
                         "gap rather than leaving a hole.")

F("FIG-15-04", "column",
  '<div class="faninfig">' + flow("pulse_pad", "reverb")
  + flow("fm_bell", "reverb") + flow("pluck", "reverb") + "</div>")
CAPTIONS["FIG-15-04"] = "Parallel paths into one destination. No mixer, no cost per wire."

F("FIG-15-05", "column",
  '<div class="planefig">'
  + flow("keys", "arp", "voice", "pulse_pad", plane="notes")
  + flow("pulse_pad", "lowpass", "drive", "echo", "master")
  + flow("LFO", "lowpass.cutoff", plane="mod")
  + flow("clock", "logic", "arp.power", plane="binary")
  + flow("deck", "voice.2", "fm_bell", plane="notes") + "</div>")
CAPTIONS["FIG-15-05"] = ("The worked rack. Four planes, one instrument — read it "
                         "one plane at a time.")

F("FIG-15-06", "column", twoup(
    "patch — the modules and their order",
    buslist([("patches/*.py", "chain + note bindings"),
             ("carries", "no wires, no play state")]),
    "preset — everything performable",
    buslist([("presets/*.json", "params, relays, LFOs, drums"),
             ("carries", "no audio graph")])))
CAPTIONS["FIG-15-06"] = ("Two different saves. Neither holds your card layout — "
                         "that lives in the browser.")

F("FIG-15-07", "spot",
  '<div class="planefig">' + flow("edit file", "respawn", "same wires")
  + '<div class="reload">a syntax error keeps the OLD sound rather than dropping '
    'to silence</div></div>')
CAPTIONS["FIG-15-07"] = "Hot reload swaps the module in place and keeps its wiring."

F("FIG-16-01", "column", buslist([
    ("hardware in", "the interface's input, via Audio In"),
    ("module buses", "one private stereo bus per stage"),
    ("null bus", "where disconnected outputs park"),
    ("master bus", "the sum, through volume and limiter"),
    ("hardware out", "the interface's output")]))
CAPTIONS["FIG-16-01"] = "Five places a signal can be. Everything else is a detail of one."

F("FIG-16-02", "spot",
  card("Master Out", [("volume", .8, "80%")], fam="io", size="S")
  + f'<div class="mrow">{meter(.62, .55)}</div>')
CAPTIONS["FIG-16-02"] = ("The output half: fader, meters, and the device picker "
                         "when there is more than one device.")

F("FIG-16-03", "spot", card("Audio In", [("gain", .25, "1.000")],
                            fam="input", size="S"))
CAPTIONS["FIG-16-03"] = ("Audio In is a source like any other. Set `gain` here; "
                         "read the level on the master meters.")

F("FIG-16-04", "column", twoup(
    "few wires — one edge",
    '<div class="ovfig"><span class="ovcard">Master Out</span>'
    '<i class="oh t"></i><i class="oh t2"></i></div>',
    "busy — spills to the side",
    '<div class="ovfig"><span class="ovcard">Master Out</span>'
    '<i class="oh t"></i><i class="oh t2"></i><i class="oh t3"></i>'
    '<i class="oh s1"></i><i class="oh s2"></i></div>'))
CAPTIONS["FIG-16-04"] = ("Handles fill the top edge first and spill onto the side "
                         "only when they run out of room.")

F("FIG-16-05", "column",
  '<div class="planefig">'
  + flow("controller", "keys", "voice", plane="notes")
  + flow("pulse_pad", "lowpass", "echo", "master", "speakers") + "</div>")
CAPTIONS["FIG-16-05"] = "One signal, controller to speakers, as a final read-through."

F("FIG-A-01", "column",
  '<div class="kcrow wrap">'
  + "".join(keycap(k) for k in "AWSEDFTGYHUJK")
  + f'&nbsp;&nbsp;{keycap("Z", "oct −")}{keycap("X", "oct +")}'
    f'{keycap("Caps", "sustain")}{keycap("Space", "deck rec")}</div>')
CAPTIONS["FIG-A-01"] = ("The computer keyboard as a controller. The `?` button "
                         "shows this live, plus your own trigger bindings.")

FIGURE_CSS += """
.reload{font-family:var(--ui);font-size:.62rem;color:var(--pw-ink-2);
  margin-top:.5rem;font-style:italic}
.kcrow{display:flex;gap:.3rem;align-items:center;margin-top:.7rem;flex-wrap:wrap}
.kcrow.wrap{background:var(--pw-plane);border-radius:10px;padding:.9rem 1rem}
.mrow{display:flex;align-items:center;gap:.6rem;margin-top:.45rem}
.devpick{margin-top:.45rem;background:var(--pw-surface);border-radius:6px;
  padding:.35rem .5rem;font-family:var(--ui);font-size:.62rem;
  color:var(--pw-ink-2);display:grid;gap:.15rem;max-width:190px}
.devpick b{font-size:.5rem;letter-spacing:.1em;text-transform:uppercase}
.devpick .sel{color:var(--pw-ink-1);background:var(--pw-hairline);
  border-radius:3px;padding:.1em .3em}
.gaterow{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));
  gap:.6rem;background:var(--pw-plane);border-radius:10px;padding:.9rem}
.ksfig{display:grid;grid-template-columns:repeat(8,1fr);gap:2px}
.ksfig.wide{background:var(--pw-plane);border-radius:8px;padding:.7rem;gap:4px}
.ksfig i{font-style:normal;text-align:center;font-family:var(--ui);
  font-size:.58rem;color:var(--pw-ink-2);background:var(--pw-hairline);
  border-radius:3px;padding:.3em 0}
.ksfig i.on{background:var(--pw-ctl);color:#0d0d0d;font-weight:700}
.ksfig i.nowat{outline:1.5px solid rgba(255,255,255,.75)}
.ptrack.lfoswept{background:linear-gradient(90deg,var(--pw-hairline) 30%,
  color-mix(in srgb,var(--pw-mod) 45%,transparent) 30% 75%,
  var(--pw-hairline) 75%)}
.tplay{display:inline-grid;place-content:center;width:34px;height:34px;
  border-radius:7px;font-size:.9rem}
.tplay.stopped{color:var(--pw-meter);border:1.5px solid var(--pw-danger);
  box-shadow:0 0 9px color-mix(in srgb,var(--pw-danger) 45%,transparent)}
.tlab{font-family:var(--ui);font-size:.62rem;color:var(--pw-ink-2)}
.dragfig{display:flex;flex-direction:column;align-items:center;gap:.4rem;
  min-height:44px;justify-content:center}
.dl{font-family:var(--ui);font-size:.58rem;color:var(--pw-ink-2)}
.ovfig{position:relative;height:74px}
.ovcard{position:absolute;left:14px;top:18px;right:14px;bottom:6px;
  background:#202020;border:1px solid var(--pw-border);border-radius:7px;
  display:grid;place-content:center;font-family:var(--ui);font-size:.6rem;
  color:var(--pw-ink-2)}
.oh{position:absolute;width:9px;height:9px;border-radius:50%;
  border:2px solid var(--pw-audio)}
.oh.t{left:34px;top:13px}.oh.t2{left:58px;top:13px}.oh.t3{left:82px;top:13px}
.oh.s1{left:9px;top:34px}.oh.s2{left:9px;top:52px}
"""


# Inline top-bar controls. Cole, 2026-07-27: "a breakdown of the top bar would
# be much more useful in the form of individual icons inserted inline." So §3.2
# names each control in a sentence and the icon sits in that sentence, rather
# than one strip figure the reader has to map back onto the prose.
for _fid, _g, _l in [
        ("FIG-TB-MODE", "blocks", "mode"), ("FIG-TB-RESTART", "⟳", "restart"),
        ("FIG-TB-PLAY", "▶", "play"), ("FIG-TB-BPM", "112", "bpm"),
        ("FIG-TB-METER", "4/4", "meter"), ("FIG-TB-CLICK", "♩", "click"),
        ("FIG-TB-VOL", "▮▮", "master"), ("FIG-TB-KEYS", "C4", "keys"),
        ("FIG-TB-TIDY", "⇱", "tidy"), ("FIG-TB-LOCK", "🔒", "lock"),
        ("FIG-TB-HELP", "?", "help")]:
    F(_fid, "inline", tbicon(_g, _l))


# ==========================================================================
# CORRECTIONS AND RESTYLE — 2026-07-28
#
# 1. WIRES ARE NOT PATTERNED. Cole: "the thing about patterns on wires is
#    wrong. Its just colors. They get an overlay while live."
#    Verified in gui/blocks.html: `svg#wires path` carries NO stroke-dasharray.
#    The only dashes in the wire layer are `path.flow` — a white overlay,
#    stroke-width 2.7, dasharray 0.1 16, animated `flowdots` — drawn ON TOP of
#    a live wire, and `path.ghostw`, the marching ghost while you drag.
#    DESIGN.md §7's "triple encoding" was wrong about the instrument, and the
#    manual inherited it. Figures now match the interface: solid colour.
#
# 2. NO BOXES. Cole: "blanket rule, No more black boxes around figures. It's
#    really ugly." Diagrams are drawn on the paper, in paper-native colours.
#    A CARD stays dark, because a card is dark — that is the thing itself,
#    not a frame around it.
#
# 3. Figures sit INLINE with the text, and a caption is set to the width of
#    the figure it describes rather than the full column.
# ==========================================================================


def legend() -> str:
    """The plane key: colour and the word. Those are the two encodings the
    instrument actually has."""
    rows = "".join(
        f'<div class="lgr">{wire(n, w=104, label=False, pastel_end=False)}'
        f'<span class="lgn {n}">{n}</span></div>' for n, _, _, _ in PLANES)
    return f'<div class="legendbox">{rows}</div>'


F("FIG-04-02", "column", legend())
F("FIG-A-02", "column", legend())
CAPTIONS["FIG-04-02"] = ("The four wire colours. Colour is the whole code — "
                         "wires are solid, and the word is on the handle label.")
CAPTIONS["FIG-A-02"] = "The plane colours, repeated for reference."

F("FIG-04-05", "column",
  f'<div class="livewire">{wire("audio", w=190, label=False)}'
  f'<span class="lwl">idle</span></div>'
  f'<div class="livewire">{wire("audio", w=190, label=False, live=True)}'
  f'<span class="lwl">passing signal</span></div>')
CAPTIONS["FIG-04-05"] = ("A live wire wears a moving white overlay. The wire's "
                         "own colour never changes.")

FIGURE_CSS += """
/* ---- NO BOXES. Diagrams live on the paper. -------------------------- */
.planefig,.faninfig,.healfig,.gutterfig,.morphrow,.legendbox,.twoup,.threeup,
.swatches,.buslist,.palfig,.term,.stepgrid,.gaterow,.kcrow.wrap,.regionmap,
.notemon,.tonicviz,.envfig,.levfig,.ksfig.wide,.mlay,.figgeo{
  background:none!important;border-radius:0;padding:0}
.twoup,.threeup{gap:1.6rem}
.gaterow,.morphrow{gap:.8rem}
.palfig{gap:1rem}
.stepgrid{gap:3px}
.regionmap{background-image:none!important;border:1px solid var(--rule)}
.notemon,.tonicviz,.envfig,.levfig{border:1px solid var(--rule);
  border-radius:6px;background:var(--paper-2)!important}
.deckroll{background:var(--pw-plane)}

/* diagram primitives, restyled for paper */
.fbox{background:var(--paper-2);border:1px solid var(--rule);color:var(--ink)}
.term{font-size:.74rem;color:var(--ink-mute);border-left:2px solid var(--rule);
  padding-left:.9rem}
.term .tl.ok{color:#1d7a52}.term .tl.dim{color:#93918a}
.bn,.bd{color:var(--ink)}.bd{color:var(--ink-mute)}
.lgn{color:var(--ink)}
.lgn.audio{color:var(--audio-ink)}.lgn.notes{color:var(--ctl-ink)}
.lgn.mod{color:var(--mod-ink)}.lgn.binary{color:var(--bin-ink)}
.lgr{grid-template-columns:110px 1fr;gap:.8rem}
.sw{color:var(--ink)}
.mlay i{background:var(--paper-2);border-color:var(--rule)}
.gblk{background:var(--paper-2)}.figgeo b{color:var(--ink-mute)}
.gunit b{color:var(--audio-ink)}
.szbox{background:var(--paper-2);border-color:var(--rule);color:var(--ink-mute)}
.ggut{background:repeating-linear-gradient(45deg,transparent,transparent 3px,
  var(--rule) 3px,var(--rule) 6px)}
.rg{background:var(--paper);border-color:var(--rule);color:var(--ink-mute)}
.rg b{background:var(--ink);color:var(--paper)}
.dstep{background:var(--rule)}
.dln{color:var(--ink-mute)}
.pb{background:var(--paper-2);color:var(--ink)}
.ps b,.mol,.dl,.reload,.et{color:var(--ink-mute)}
.tonicviz div{background:var(--rule)}
.tonicviz i{color:var(--ink-mute)}
.ksfig i{background:var(--paper-2);color:var(--ink-mute)}
.kc{background:var(--paper-2);border-color:var(--rule);color:var(--ink)}
.kc i{color:var(--ink-mute)}
.tbi{background:var(--paper-2);border-color:var(--rule)}
.tbi .g{color:var(--ink)}.tbi .l{color:var(--ink-mute)}
.twoup b,.threeup b{color:var(--ink-mute)}
.metrostrip i{background:var(--rule)}
.ovcard{background:var(--paper-2);border-color:var(--rule);color:var(--ink-mute)}
.gbody{color:var(--ink);border-color:var(--ink-mute)}
.gbody .bub{background:var(--paper);border-color:var(--ink-mute)}
.gate .pin{color:var(--ink-mute)}
.livewire{display:flex;align-items:center;gap:.8rem;margin:.3rem 0}
.lwl{font-family:var(--ui);font-size:.68rem;color:var(--ink-mute)}

/* ---- figures sit inline, captions match the figure's width ----------- */
figure.f-column,figure.f-spot{display:inline-block;max-width:100%;
  vertical-align:top}
figure.f-column{margin:1.2rem 0}
figure.f-column figcaption,figure.f-spot figcaption{display:block;
  grid-template-columns:none;margin-top:.5rem;font-size:.72rem;line-height:1.45}
figure.f-column .figid,figure.f-spot .figid{display:block;margin-bottom:.1rem}
figure.f-spot{margin:.1rem 0 .9rem}
figure.f-spot figcaption{text-align:left;font-size:.68rem}
"""


# Re-registered AFTER the corrected wire(): these two call wire() and were
# originally defined above it, so they would otherwise carry the old geometry.
F("FIG-03-06", "column", threeup(
    ("1 · grab", f'<div class="dragfig">{handle("out","audio")}'
     '<span class="dl">press an out handle</span></div>'),
    ("2 · drag", f'<div class="dragfig">{wire("audio", w=90, label=False)}'
     '<span class="dl">a live wire follows</span></div>'),
    ("3 · land", f'<div class="dragfig">{handle("in","audio")}'
     '<span class="dl">release on an in handle</span></div>')))
