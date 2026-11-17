"""Tiny, safe Markdown subset (paragraphs, lists, headings, bold/italic, links, record-ID auto-links). Everything is HTML-escaped first."""
import html, re

ID_RE = re.compile(r"\b([A-Z]{2})-(\d+)\b")


def _inline(text, known):
    text = html.escape(text, quote=False)
    stash = []
    def keep(h):
        stash.append(h)
        return f"\x00{len(stash)-1}\x00"
    def link(m):
        url = html.unescape(m.group(2))
        if re.match(r"https?://|mailto:", url):
            return keep(f'<a href="{html.escape(url)}" rel="noopener noreferrer">{m.group(1)}</a>')
        return m.group(0)
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", lambda m: keep(f"<em>[image: {m.group(1) or m.group(2)}]</em>"), text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, text)
    text = re.sub(r"(?<![\w\"=/])(https?://[^\s<>\x00]+[^\s<>.,;:)\x00])",
                  lambda m: keep(f'<a href="{html.escape(html.unescape(m.group(1)))}" rel="noopener noreferrer">{m.group(1)}</a>'), text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", text)
    text = ID_RE.sub(lambda m: f'<a class="rid" href="/r/{m.group(0)}">{m.group(0)}</a>' if m.group(0) in known else m.group(0), text)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))] if int(m.group(1)) < len(stash) else "", text)


def render(md, known=()):
    out = []
    md = (md or "").replace("\x00", "")          # NUL marks the placeholders below: never let stored text forge one
    for block in re.split(r"\n{2,}", md.strip()):
        lines = block.split("\n")
        if not block.strip():
            continue
        m = re.match(r"(#{1,6}) (.*)", lines[0])
        if m and len(lines) == 1:
            n = min(len(m.group(1)) + 2, 6)
            out.append(f"<h{n}>{_inline(m.group(2), known)}</h{n}>")
        elif all(re.match(r"\s*([-*]|\d+\.) ", l) for l in lines):
            tag = "ol" if re.match(r"\s*\d+\.", lines[0]) else "ul"
            items = "".join(f"<li>{_inline(re.sub(r'^\s*([-*]|\d+\.) ', '', l), known)}</li>" for l in lines)
            out.append(f"<{tag}>{items}</{tag}>")
        else:
            out.append("<p>" + "<br>".join(_inline(l, known) for l in lines) + "</p>")
    return "\n".join(out)
