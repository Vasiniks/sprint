"""Write the sprint site's static files: mark.svg, favicon.svg and index.html (which inlines the wordmark), and the README's logo.svg.

usage: gen.py <site dir> <page.html> [<logo.svg>]
The wordmark is the terminal app's own logo, each pixel two character cells wide, white, shaded light to solid across the
word (░ ░ ▒ ▓ ▓ █). That shading is one moment of a slow wave that keeps travelling through it; and every few seconds a
glint of colour crosses it fast: yellow, then cyan, then magenta. Under prefers-reduced-motion it holds still.
The mark is three speed lines: yellow on top and longest, cyan, then magenta, their right ends on one slanted edge.
"""
import sys
from html import escape
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))  # the repo, for sprint_tui
from sprint_tui import PIX, STILL, WORD

OUT, TEMPLATE = sys.argv[1], sys.argv[2]
Y, C, M, K, W = "#ffe100", "#00a8e8", "#e6007a", "#000000", "#ffffff"
FILL = {1: "url(#d1)", 2: "url(#d2)", 3: "url(#d3)", 4: W}  # shade -> how it is drawn
ROUND, LOOP = 11, 600  # the wave: seconds to come round, and the length it travels (the word, and a little more)
EVERY, FIRST, CROSS = 4, 2.2, 0.6  # the glints: seconds between them, before the first, from the first letter to the last
BAR, GAP, LENS, LEAN = 12, 4, (46, 30, 14), 0.5  # the mark: line height, space between, lengths, how far an end leans per unit of height
MARK_W, MARK_H = LENS[0] + BAR * LEAN, 3 * BAR + 2 * GAP

DEFS = (
    f'<pattern id="d1" width="5" height="5" patternUnits="userSpaceOnUse"><rect width="3" height="3" fill="{W}"/></pattern>'
    f'<pattern id="d2" width="5" height="5" patternUnits="userSpaceOnUse"><rect width="2.5" height="2.5" fill="{W}"/><rect x="2.5" y="2.5" width="2.5" height="2.5" fill="{W}"/></pattern>'
    f'<pattern id="d3" width="5" height="5" patternUnits="userSpaceOnUse"><path d="M0 0h5v5h-5zM2.5 0h2.5v2.5h-2.5z" fill-rule="evenodd" fill="{W}"/></pattern>')
# The wave: every letter is drawn once per shade, and its four copies trade places smoothly as the crest passes it:
# solid, a slow fall through dense and checked to sparse, a rest, a quick rise. The solid, dense and checked copies lie on
# top of one another (each covers the one before); the sparse one, which the checks do not cover, fades out under them.
# The glints: a colour copy of a letter flashes once every three glints; staggered, so about three letters are lit at a time.
MOVE = ("@media (prefers-reduced-motion: no-preference){"
        f".sh{{animation:{ROUND}s ease-in-out infinite}}.sh1{{animation-name:sh1}}.sh2{{animation-name:sh2}}.sh3{{animation-name:sh3}}.sh4{{animation-name:sh4}}"
        f".w{{animation:glint {3 * EVERY}s linear infinite}}}}"
        "@keyframes sh1{0%,42%{opacity:0}58%,82%{opacity:1}88%,100%{opacity:0}}"
        "@keyframes sh2{0%,42%{opacity:1}58%,82%{opacity:0}88%,100%{opacity:1}}"
        "@keyframes sh3{0%,31%{opacity:1}41.5%,88%{opacity:0}94%,100%{opacity:1}}"
        "@keyframes sh4{0%{opacity:1}18%,94%{opacity:0}100%{opacity:1}}"
        "@keyframes glint{0%,3.2%,100%{opacity:0}1%,1.4%{opacity:1}}")


def glyphs():
    """one <g> per letter: a rect per run of pixels, each row one cell further left than the one above (the lean)"""
    out = []
    for ch in sorted(set(WORD)):
        rects = []
        for r, row in enumerate(PIX[ch]):
            c = 0
            while c < len(row):
                n = c
                while n < len(row) and row[n] == "X":
                    n += 1
                if n > c:
                    rects.append(f'<rect x="{(4 - r) * 10 + c * 20}" y="{r * 20}" width="{(n - c) * 20}" height="20"/>')
                c = max(n, c + 1)
        out.append(f'<g id="g-{ch}">{"".join(rects)}</g>')
    return "".join(out)


def letters():
    """the word, 580 x 100. Still, each letter shows the copies its shade in STILL calls for, and that is also where the wave
    starts: the crest on the last letter, each letter before it further down the tail. Over them lie a yellow, a cyan and a
    magenta copy for the glints, each lit later the further right the letter stands"""
    left, x = [], 0
    for ch in WORD:
        left.append(x)
        x += (len(PIX[ch][0]) + 1) * 20
    mid = [x + len(PIX[ch][0]) * 10 for x, ch in zip(left, WORD)]
    out = []
    for i, ch in enumerate(WORD):
        behind = -ROUND * (mid[-1] - mid[i]) / LOOP  # how long ago the crest passed this letter
        shown = {1: STILL[i] == 1, 2: STILL[i] >= 2, 3: STILL[i] >= 3, 4: STILL[i] == 4}
        shades = "".join(f'<use href="#g-{ch}" class="sh sh{k}" fill="{FILL[k]}"' + ("" if shown[k] else ' opacity="0"')
                         + f' style="animation-delay:{behind:.2f}s"/>' for k in FILL)
        at = FIRST + CROSS * (mid[i] - mid[0]) / (mid[-1] - mid[0])
        glints = "".join(f'<use href="#g-{ch}" class="w" fill="{col}" opacity="0" style="animation-delay:{at + n * EVERY:.2f}s"/>'
                         for n, col in enumerate((Y, C, M)))
        out.append(f'<g transform="translate({left[i]},0)">{shades}{glints}</g>')
    return f'<g shape-rendering="crispEdges">{"".join(out)}</g>'


def wordmark():
    return f"<defs>{DEFS}{glyphs()}</defs><style>{MOVE}</style>"


def lines(k=1.0):
    """the three speed lines at scale k, box at 0,0 -> (colour, top, height, length, right end at its top, right end at its bottom)"""
    for i, (col, length) in enumerate(zip((Y, C, M), LENS)):
        top = i * (BAR + GAP)
        yield col, top * k, BAR * k, length * k, (MARK_W - top * LEAN) * k, (MARK_W - (top + BAR) * LEAN) * k


def streak(x=0.0, y=0.0, k=1.0):
    """the mark as three parallelograms, its box (MARK_W x MARK_H, times k) at x, y"""
    return "".join(f'<polygon points="{x + a - n:g},{y + t:g} {x + a:g},{y + t:g} {x + b:g},{y + t + h:g} {x + b - n:g},{y + t + h:g}" fill="{col}"/>'
                   for col, t, h, n, a, b in lines(k))


def cells(text, x, y, fill, size=14, weight=""):
    """text on a strict grid: textLength pins every character to 0.6em whatever monospace font the viewer has"""
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}"{weight} textLength="{len(text) * size * 0.6:.1f}" '
            f'lengthAdjust="spacingAndGlyphs" xml:space="preserve">{escape(text)}</text>')


open(f"{OUT}/mark.svg", "w").write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {MARK_W:g} {MARK_H:g}" role="img" aria-label="sprint">{streak()}</svg>\n')
open(f"{OUT}/favicon.svg", "w").write(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#000"/>'
    + streak((64 - MARK_W) / 2, 12) + "</svg>\n")  # 12 down, not 10: at 16 px every line then covers three whole pixels

# logo.svg: the app's opening screen as a card, for the README and anywhere else that wants the logo on dark.
# Under the word, the prompt: a rule with the model on the right, the mark and the placeholder, a plain rule, then the mode with its icon
CW = 8.4  # one character at 14px
BOLD = ' font-weight="700"'
GREY, FRAME = "#8a8a8a", "#5a5a5a"
model, hint = "Qwen3.5-0.8B:Q4_K_M · 0%", "Ask anything…  / for commands  ! for shell"
tips = [("/", W), (" commands   ", GREY), ("ctrl+c", W), (" quit", GREY)]
tx = 704 - sum(len(t) for t, _ in tips) * CW  # they end where the rules end
tip_text = ""
for t, col in tips:
    tip_text += cells(t, f"{tx:.1f}", 296, col, weight=BOLD if col == W else "")
    tx += len(t) * CW
# the mode under the bar: its icon (one cell of three filled: it asks first), its name, and how to change it
icon = "".join(f'<polygon points="{56 + n * 9 + 5},284 {56 + n * 9 + 12},284 {56 + n * 9 + 7},298 {56 + n * 9},298" '
               + (f'fill="{C}"' if n == 0 else f'fill="none" stroke="{C}"') + "/>" for n in range(3))
mode_text = cells("ask", f"{56 + 4.5 * CW:.1f}", 296, C, weight=BOLD) + cells(" (tab to cycle)", f"{56 + 7.5 * CW:.1f}", 296, GREY)
mx = 704 - (len(model) + 3) * CW  # the model sits three cells in from the right end of its rule
card = (
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 340" role="img" aria-label="sprint" '
    f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, \'Liberation Mono\', monospace">'
    f"{wordmark()}"
    "<style>@media (prefers-reduced-motion: no-preference){.caret{animation:bl 1s steps(1) infinite}}@keyframes bl{50%{opacity:0}}</style>"
    f'<rect width="760" height="340" rx="14" fill="{K}"/>'
    f'<g transform="translate(90,52)">{letters()}</g>'
    f'<line x1="56" y1="212" x2="704" y2="212" stroke="{FRAME}"/>'
    f'<rect x="{mx - CW:.1f}" y="202" width="{(len(model) + 2) * CW:.1f}" height="20" fill="{K}"/>{cells(model, f"{mx:.1f}", 217, GREY)}'
    f'{cells("❯", "56", 245, C, weight=BOLD)}'
    f'<rect class="caret" x="{56 + 2 * CW:.1f}" y="232" width="{CW}" height="17" fill="{W}"/>'
    f'{cells(hint, f"{56 + 3.5 * CW:.1f}", 245, GREY)}'
    f'<line x1="56" y1="270" x2="704" y2="270" stroke="{FRAME}"/>'
    f"{icon}{mode_text}{tip_text}</svg>\n")
open(sys.argv[3] if len(sys.argv) > 3 else f"{OUT}/logo.svg", "w").write(card)

# the hero: the mark at 5/4 beside the word, its lines in pixel rows 2 to 4, ending 14 short of the word's leaning left side.
# Behind each line lies a 4000-long tail that the page shows only during the load-in, drawing it back into the line
HK, HY = 1.25, 22.5
HX = 31 - HY * LEAN - MARK_W * HK
tails = "".join(f'<rect class="s" x="{HX + a - n - 4000:g}" y="{HY + t:g}" width="4000" height="{h:g}" fill="{col}"/>' for col, t, h, n, a, b in lines(HK))
HERO = (f'<svg class="mark" viewBox="{HX:g} 0 {580 - HX:g} 100" role="img" aria-label="sprint">{wordmark()}'
        f'<g class="run">{tails}{streak(HX, HY, HK)}{letters()}</g></svg>')

page = open(TEMPLATE).read()
open(f"{OUT}/index.html", "w").write(page.replace("<!--HERO-->", HERO).replace(
    "<!--MARK-->", f'<svg viewBox="0 0 {MARK_W:g} {MARK_H:g}" aria-hidden="true">{streak()}</svg>'))
print("wrote", OUT)
