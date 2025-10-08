"""
Markdown to HTML converter
- CLI mode: convert files or read stdin
- Web mode: lightweight Flask app with a form to paste Markdown and get HTML

Features added:
- Headings (#, ##, etc.)
- Paragraphs
- Ordered (1.) and unordered lists (-, +, *)
- Bold (**bold**), italic (*italic*), strikethrough (~~strike~~)
- Inline code (`code`)
- Links ([text](url)) and images (![alt](src))
- Fenced code blocks (```lang)
- Blockquotes (> quote)
- Horizontal rules (---, ***, ___)
- Tables (| col | col |)
- automatic SEO: <title> from first heading, meta description from first paragraph, OpenGraph and Twitter cards
- outputs clean well structured HTML

Usage:
- CLI single file -> python app.py -i sample.md -o sample.html
- CLI read stdin -> cat sample.md | python app.py --stdout
- Batch directory -> python app.py -d md_dir -D html_dir
- Run web form -> python app.py --serve (then open http://127.0.0.1:5000)

Requires: Flask (only for web form). Install with: pip install flask
"""

from pathlib import Path
import re
import argparse
import html
from typing import Optional

# Flask import (optional)
try:
    from flask import Flask, request, render_template_string
except Exception:
    Flask = None


# Inline regexes
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"\*(.+?)\*")
STRIKE_RE = re.compile(r"~~(.+?)~~")
BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
HR_RE = re.compile(r"^\s*((?:-{3,})|(?:\*{3,})|(?:_{3,}))\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
# Block-level regexes
UL_ITEM_RE = re.compile(r"^\s*[-+*]\s+(.*)")
OL_ITEM_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)")
HEADING_RE = re.compile(r"^(#{1,6})\s*(.*)")
FENCE_RE = re.compile(r"^\s*```(.*)$")

# Inline parsing
def escape_html(text: str) -> str:
    return html.escape(text, quote=False)

# Inline parsing function
def inline_parse(text: str) -> str:
    # images first
    def _img(m):
        alt = escape_html(m.group(1))
        src = escape_html(m.group(2))
        return f'<img src="{src}" alt="{alt}" loading="lazy">'

    text = IMAGE_RE.sub(_img, text)

    # links
    def _link(m):
        t = escape_html(m.group(1))
        u = escape_html(m.group(2))
        return f'<a href="{u}">{t}</a>'
    
    text = LINK_RE.sub(_link, text)
    # inline code
    text = INLINE_CODE_RE.sub(r"<code>\1</code>", text)
    # bold then italic
    text = BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = ITALIC_RE.sub(r"<em>\1</em>", text)
    # strikethrough
    text = STRIKE_RE.sub(r"<del>\1</del>", text)

    return text

# Table parsing
def parse_table(lines):
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(cells)
    header = rows[0]
    body = rows[2:] if len(rows) > 2 else rows[1:]
    html = (
        "<table>\n<thead><tr>"
        + "".join(f"<th>{c}</th>" for c in header)
        + "</tr></thead>\n"
    )
    html += "<tbody>"
    for r in body:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
    html += "</tbody></table>"

    return html

# Main markdown to HTML conversion function
def md_to_html(md: str) -> dict:
    
    lines = md.splitlines()
    i = 0
    html_lines = []
    title: Optional[str] = None
    meta_desc: Optional[str] = None

    inside_code = False
    code_lang = ""
    code_lines = []

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        # fenced code block start/end
        fence = FENCE_RE.match(line)
        if fence:
            if not inside_code:
                inside_code = True
                code_lang = fence.group(1).strip()
                code_lines = []
            else:
                # close
                inside_code = False
                code_html = "\n".join(escape_html(l) for l in code_lines)
                lang_class = (
                    f' class="language-{escape_html(code_lang)}"' if code_lang else ""
                )
                html_lines.append(f"<pre><code{lang_class}>{code_html}\n</code></pre>")
            i += 1
            continue
        
        if inside_code:
            code_lines.append(raw)
            i += 1
            continue

        if not line.strip():
            i += 1
            continue

        # heading
        m_h = HEADING_RE.match(line)
        if m_h:
            level = len(m_h.group(1))
            text = m_h.group(2).strip()
            parsed = inline_parse(escape_html(text))
            if not title:
                title = text
            html_lines.append(f"<h{level}>{parsed}</h{level}>")
            i += 1
            continue
        
        # lists (supports nested ordered and unordered by indentation)
        if UL_ITEM_RE.match(line) or OL_ITEM_RE.match(line):
            def _list_match(l):
                m_ul = UL_ITEM_RE.match(l)
                m_ol = OL_ITEM_RE.match(l)
                if m_ul:
                    indent = len(re.match(r"^(\s*)", l).group(1).expandtabs(4))
                    return ("ul", indent, m_ul.group(1).strip())
                if m_ol:
                    indent = len(re.match(r"^(\s*)", l).group(1).expandtabs(4))
                    return ("ol", indent, m_ol.group(2).strip())
                return (None, None, None)

            stack = []  # tuples of (indent, list_type)
            
            # helper to close last <li> if needed
            def close_inline_li():
                if html_lines and html_lines[-1].endswith("</li>") is False:
                    html_lines[-1] = html_lines[-1].rstrip() + "</li>"

            while i < len(lines):
                l = lines[i]
                if not (UL_ITEM_RE.match(l) or OL_ITEM_RE.match(l)):
                    break

                list_type, indent, content = _list_match(l)
                if list_type is None:
                    break

                if not stack:
                    html_lines.append(f"<{list_type}>")
                    stack.append((indent, list_type))
                    html_lines.append(f"<li>{inline_parse(escape_html(content))}")
                else:
                    top_indent, top_type = stack[-1]

                    if indent > top_indent:
                        # nested list inside the current <li>
                        html_lines[-1] = html_lines[-1].rstrip() + f"<{list_type}>"
                        stack.append((indent, list_type))
                        html_lines.append(f"<li>{inline_parse(escape_html(content))}")

                    elif indent == top_indent:
                        # same level list item
                        close_inline_li()
                        html_lines.append(f"<li>{inline_parse(escape_html(content))}")

                    else:
                        # dedent (close inner lists first)
                        close_inline_li()
                        while stack and stack[-1][0] > indent:
                            _, ttype = stack.pop()
                            html_lines.append(f"</{ttype}>")
                        close_inline_li()
                        html_lines.append(f"<li>{inline_parse(escape_html(content))}")

                i += 1

            # close remaining open lists
            close_inline_li()
            while stack:
                _, ttype = stack.pop()
                html_lines.append(f"</{ttype}>")
            continue


        # table
        elif re.match(r"^\s*\|.+\|\s*$", line):
            table_lines = [line]
            i += 1
            while i < len(lines) and re.match(r"^\s*\|.+\|\s*$", lines[i]):
                table_lines.append(lines[i])
                i += 1
            html_lines.append(parse_table(table_lines))
            continue

        # blockquote
        if BLOCKQUOTE_RE.match(line):
            items = []
            while i < len(lines) and BLOCKQUOTE_RE.match(lines[i].rstrip()):
                m2 = BLOCKQUOTE_RE.match(lines[i].rstrip())
                items.append(inline_parse(escape_html(m2.group(1).strip())))
                i += 1
            block = " ".join(items)
            html_lines.append(f"<blockquote>{block}</blockquote>")
            continue

        # horizontal rule
        if HR_RE.match(line):
            html_lines.append("<hr>")
            i += 1
            continue
        
        
        # paragraph collect (preserve trailing spaces for double-space line breaks)
        para_lines = [raw]  # use raw so we keep trailing spaces
        i += 1
        while (
            i < len(lines)
            and lines[i].strip()
            and not HEADING_RE.match(lines[i])
            and not UL_ITEM_RE.match(lines[i])
            and not OL_ITEM_RE.match(lines[i])
            and not FENCE_RE.match(lines[i])
        ):
            para_lines.append(lines[i])
            i += 1

        # build paragraph: double-space at end -> <br>, otherwise join with single space
        parts = []
        for idx, pl in enumerate(para_lines):
            pl = pl.rstrip("\n")
            if pl.endswith("  "):  # two spaces at EOL => explicit line break
                content = pl[:-2]
                parts.append(inline_parse(escape_html(content)))
                parts.append("<br>")
            else:
                parts.append(inline_parse(escape_html(pl)))
                if idx != len(para_lines) - 1:
                    parts.append(" ")

        parsed = "".join(parts).strip()
        # first paragraph as meta description if no heading found
        if not meta_desc:
            clean = html.unescape(re.sub(r"<.*?>", "", parsed))
            meta_desc = (clean[:157] + "...") if len(clean) > 160 else clean
        html_lines.append(f"<p>{parsed}</p>")

    
    body = "\n".join(html_lines)
    if not title:
        title = "Lesson" # default title if no heading found
    return {"title": title, "meta": meta_desc or "", "body": body}

# Full HTML rendering with SEO tags
def render_full_html(parsed: dict, css_href: str = None, canonical: str = None) -> str:
    title = escape_html(parsed["title"])
    meta = escape_html(parsed["meta"]) if parsed.get("meta") else ""
    body = parsed["body"]
    css_link = (
        f'<link rel="stylesheet" href="{escape_html(css_href)}">' if css_href else ""
    )
    canonical_tag = (
        f'<link rel="canonical" href="{escape_html(canonical)}">' if canonical else ""
    )

    # Open Graph and Twitter card minimal set for better SEO sharing
    og_title = title
    og_desc = meta
    og_type = "article"
    # Full HTML document
    html_doc = f"""<!doctype html>
<html lang="en">

<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{meta}">
<meta name="robots" content="index, follow">
{canonical_tag}
{css_link}
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{og_desc}">
<meta property="og:type" content="{og_type}">
<meta name="twitter:card" content="summary">
</head>

<body>
<main>
{body}
</main>
</body>

</html>"""
    return html_doc

# File conversion helper
def convert_file(
    input_path: Path, output_path: Path, css_href: str = None, canonical: str = None
):
    md = input_path.read_text(encoding="utf-8")
    parsed = md_to_html(md)
    html_out = render_full_html(parsed, css_href=css_href, canonical=canonical)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_out, encoding="utf-8")
    print(f"Wrote {output_path}")


# CLI runner
if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Markdown to HTML converter with CLI and simple web form"
    )
    p.add_argument("-i", "--input", help="Input markdown file")
    p.add_argument("-o", "--output", help="Output html file")
    p.add_argument("-d", "--indir", help="Input directory (convert all .md)")
    p.add_argument("-D", "--outdir", help="Output directory")
    p.add_argument("--css", help="Optional CSS href to include in generated HTML")
    p.add_argument("--canonical", help="Optional canonical URL for SEO")
    p.add_argument(
        "--stdout",
        action="store_true",
        help="Write HTML to stdout (read stdin as markdown if no input file)",
    )
    p.add_argument(
        "--serve", action="store_true", help="Run lightweight web form (requires Flask)"
    )
    args = p.parse_args()
    
    # Web form mode
    if args.serve:
        if Flask is None:
            print("Flask not installed. Install with: pip install flask")
        else:
            app = Flask(__name__)

            FORM = """<!doctype html>
<html lang="en">

<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Markdown to HTML</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Alan+Sans:wght@300..900&family=Google+Sans+Code:ital,wght@0,300..800;1,300..800&display=swap');

        * {
            font-family: "Alan Sans", sans-serif;
            box-sizing: border-box;
            transition: all 0.2s ease;
            scrollbar-width: thin;
            scrollbar-color: #555 #1f1f1f;
        }

        *::-webkit-scrollbar {
            background: #555;
            width: 6px;
        }

        *::-webkit-scrollbar-track {
            background: #1f1f1f;
        }

        *::-webkit-scrollbar-thumb {
            border-radius: 6px;
        }

        body {
            font-size: 16px;
            background: url('https://images.unsplash.com/photo-1536859355448-76f92ebdc33d?q=80&w=1169&auto=format&fit=crop&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D') no-repeat center center fixed;
            background-size: cover;
            padding: 1.5rem 3rem;
            margin: 0 auto;
            color: #d4d4d4;
            max-width: 1280px;
        }

        a {
            color: #46a143;
            text-decoration: none;
        }

        a:hover {
            text-decoration: underline;
        }

        img {
            display: block;
            max-height: 400px;
            max-width: 300px;
            object-fit: contain;
        }

        .container {
            padding: 1rem 0;
            display: flex;
            justify-content: center;
            gap: 2rem;
        }
        
        .title {
            text-align: center;
            color: #46a143;
            margin-bottom: 0;
        }
        
        .description {
            text-align: center;
        }

        form,
        .result {
            min-height: 450px;
            max-height: 75vh;
            max-width: 900px;
            min-width: 340px;
            margin: 0 auto;
            font-size: 1rem;
            flex: 1;
            display: flex;
            flex-direction: column;
            border: 1px solid #525252;
            border-radius: 1rem;
        }

        form {
            overflow: auto;
        }

        textarea {
            flex: 1;
            color: #d4d4d4;
            background: #1f1f1f;
            font-size: .9rem;
            font-family: "Google Sans Code", monospace;
            padding: 1rem;
            resize: none;
            outline: none;
            border: none;
        }

        button {
            width: 100%;
            padding: .5rem;
            font-size: 1rem;
            background: #367d34;
            color: inherit;
            border: none;
            cursor: pointer;
            border-radius: 0 0 1rem 1rem;
        }

        button:hover {
            background: #46a143;
        }

        .result {
            background: #1f1f1f;
        }

        .tabs,
        label {
            display: flex;
            border-bottom: 1px solid #525252;
        }

        .tab,
        label {
            background: transparent;
            padding: .5rem 1rem;
            border-radius: 1rem 1rem 0 0;
        }

        .tab:hover {
            background: #111;
        }

        .tab.active,
        label {
            background: #333;
        }

        .tab-content {
            display: none;
            padding: 1rem;
            overflow: auto;
        }

        .tab-content.active {
            display: block;
        }

        hr {
            border: 2px solid #525252;
        }

        pre {
            margin: 0;
            font-family: "Google Sans Code", monospace;
            font-size: .9rem;
            text-wrap: wrap;
        }

        code {
            display: inline-block;
            font-family: "Google Sans Code", monospace;
            font-size: .9rem;
            background: #111;
            border-radius: 5px;
            padding: .1rem .5rem;
            text-wrap: wrap;
        }
        
        table {
            border-collapse: collapse;
            width: 100%;
        }
        th, td {
            border: 1px solid #999;
            padding: 6px 10px;
            text-align: left;
        }

        @media screen and (max-width: 768px) {

            .container {
                display: block;
            }

            .result {
                margin-top: 2rem;
            }
        }

        @media screen and (max-width: 540px) {
            body {
                padding: 1rem;
            }

            form,
            .result {
                height: 350px;
                min-width: auto;
            }
        }
    </style>
</head>

<body>
    <h1 class="title">Markdown to HTML</h1>
    <p class="description">Paste or type your Markdown text to view the HTML output.</p>
    <div class="container">
        <form method="post">
            <label for="md">Markdown input:</label>
            <textarea name="md" id="md" placeholder="Input markdown text">{{ md|e }}</textarea>
            <div><button type="submit">Convert</button></div>
        </form>

        {% if html_out %}
        <div class="result">
            <div class="tabs">
                <button class="tab active" onclick="openTab(event, 'preview')">Preview</button>
                <button class="tab" onclick="openTab(event, 'raw')">Raw HTML</button>
            </div>
            <div id="preview" class="tab-content active">
                <div class="preview">{{ html_out|safe }}</div>

            </div>
            <div id="raw" class="tab-content">
                <pre>{{ html_out|e }}</pre>
            </div>
        </div>
        {% endif %}
    </div>

    <script>
        function openTab(evt, tabName) {
            const tabs = document.querySelectorAll('.tab-content');
            tabs.forEach(tab => tab.classList.remove('active'));
            document.getElementById(tabName).classList.add('active');

            const btns = document.querySelectorAll('.tab');
            btns.forEach(btn => btn.classList.remove('active'));
            evt.currentTarget.classList.add('active');
        }
    </script>
</body>

</html>"""

            # store last HTML for /raw endpoint
            last_html = None
            
            # main route
            @app.route("/", methods=["GET", "POST"])
            def index():
                global last_html
                md = ""
                html_out = ""
                if request.method == "POST":
                    md = request.form.get("md", "")
                    parsed = md_to_html(md)
                    html_out = render_full_html(parsed)
                    last_html = html_out
                return render_template_string(FORM, md=md, html_out=html_out)
            
            # raw HTML route
            @app.route("/raw")
            def raw():
                return (
                    (last_html or ""),
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                )

            print("Starting web form at http://127.0.0.1:5000")
            app.run()
        raise SystemExit(0)

    # CLI modes
    if args.input and args.output:
        convert_file(
            Path(args.input),
            Path(args.output),
            css_href=args.css,
            canonical=args.canonical,
        )
    elif args.indir and args.outdir:
        in_dir = Path(args.indir)
        out_dir = Path(args.outdir)
        for mdfile in in_dir.glob("*.md"):
            outpath = out_dir / (mdfile.stem + ".html")
            convert_file(mdfile, outpath, css_href=args.css, canonical=args.canonical)
    else:
        # try stdin
        import sys

        if args.stdout:
            if args.input:
                md = Path(args.input).read_text(encoding="utf-8")
            else:
                md = sys.stdin.read()
            parsed = md_to_html(md)
            html_out = render_full_html(
                parsed, css_href=args.css, canonical=args.canonical
            )
            sys.stdout.write(html_out)
        else:
            p.print_help()
            
# DONE