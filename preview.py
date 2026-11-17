import datetime
import http.server
import os
import re
import socketserver
import webbrowser
import markdown
import yaml

PORT = 8000
ROOT = os.path.dirname(os.path.abspath(__file__))


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_front_matter(text):
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]) or {}, parts[2].strip()
    return {}, text


def resolve_var(expr, ctx):
    expr = expr.strip()
    if "|" in expr:
        val_part, filter_part = [p.strip() for p in expr.split("|", 1)]
        if val_part in ('"now"', "'now'"):
            return str(datetime.datetime.now().year)
        val = resolve_var(val_part, ctx)
        if filter_part == "relative_url":
            if not val:
                return ""
            return val if val.startswith(("http", "#", "/")) else "/" + val
        return str(val or "")

    if (expr.startswith('"') and expr.endswith('"')) or (
        expr.startswith("'") and expr.endswith("'")
    ):
        return expr[1:-1]

    cur = ctx
    for part in expr.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return ""
    return cur if cur is not None else ""


def render_liquid(template, ctx):
    def do_assign(match):
        var_name = match.group(1).strip()
        expr = match.group(2).strip()
        ctx[var_name] = resolve_var(expr, ctx)
        return ""

    template = re.sub(
        r"\{%\s*assign\s+([a-zA-Z0-9_]+)\s*=\s*(.+?)\s*%\}", do_assign, template
    )

    def do_include(match):
        inc_path = os.path.join(ROOT, "_includes", match.group(1).strip())
        if os.path.exists(inc_path):
            with open(inc_path, "r", encoding="utf-8") as f:
                return render_liquid(f.read(), dict(ctx))
        return ""

    template = re.sub(r"\{%\s*include\s+([^\s%]+)\s*%\}", do_include, template)

    for_pattern = re.compile(
        r"\{%\s*for\s+([a-zA-Z0-9_]+)\s+in\s+([a-zA-Z0-9_.]+)\s*%\}(.*?)\{%\s*endfor\s*%\}",
        re.DOTALL,
    )
    while for_pattern.search(template):
        def do_for(match):
            var_name, list_expr, body = match.group(1), match.group(2), match.group(3)
            items = resolve_var(list_expr, ctx)
            if not isinstance(items, list):
                return ""
            out = []
            for item in items:
                local_ctx = dict(ctx)
                local_ctx[var_name] = item
                out.append(render_liquid(body, local_ctx))
            return "".join(out)

        template = for_pattern.sub(do_for, template, count=1)

    if_pattern = re.compile(
        r"\{%\s*if\s+(.+?)\s*%\}(.*?)\{%\s*endif\s*%\}", re.DOTALL
    )
    while if_pattern.search(template):
        def do_if(match):
            cond_expr, body = match.group(1).strip(), match.group(2)
            parts = re.split(r"\{%\s*else\s*%\}", body, maxsplit=1)
            true_branch = parts[0]
            false_branch = parts[1] if len(parts) > 1 else ""

            if "!=" in cond_expr:
                left, right = [p.strip() for p in cond_expr.split("!=", 1)]
                passed = str(resolve_var(left, ctx)) != str(resolve_var(right, ctx))
            elif "==" in cond_expr:
                left, right = [p.strip() for p in cond_expr.split("==", 1)]
                passed = str(resolve_var(left, ctx)) == str(resolve_var(right, ctx))
            else:
                val = resolve_var(cond_expr, ctx)
                passed = bool(val) and str(val).strip() != ""

            return true_branch if passed else false_branch

        template = if_pattern.sub(do_if, template, count=1)

    def do_var(match):
        val = resolve_var(match.group(1), ctx)
        return str(val) if not isinstance(val, (dict, list)) else ""

    return re.sub(r"\{\{\s*(.+?)\s*\}\}", do_var, template)


def render_markdown_file(md_path):
    site = load_yaml(os.path.join(ROOT, "_config.yml"))
    site["data"] = {}
    data_dir = os.path.join(ROOT, "_data")
    if os.path.isdir(data_dir):
        for fname in os.listdir(data_dir):
            if fname.endswith((".yml", ".yaml")):
                key = os.path.splitext(fname)[0]
                site["data"][key] = load_yaml(os.path.join(data_dir, fname))

    with open(md_path, "r", encoding="utf-8") as f:
        page_meta, raw_body = parse_front_matter(f.read())

    ctx = {"site": site, "page": page_meta}
    rendered_md = render_liquid(raw_body, dict(ctx))
    html_body = markdown.markdown(rendered_md, extensions=["extra"])

    layout_name = page_meta.get("layout", "page")
    layout_path = os.path.join(ROOT, "_layouts", f"{layout_name}.html")
    if os.path.exists(layout_path):
        with open(layout_path, "r", encoding="utf-8") as f:
            layout_html = f.read()
        ctx["content"] = html_body
        return render_liquid(layout_html, ctx)
    return html_body


class LiveJekyllHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        clean_path = self.path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        candidates = []
        if clean_path == "":
            candidates.append(os.path.join(ROOT, "index.md"))
        else:
            candidates.extend(
                [
                    os.path.join(ROOT, clean_path + ".md"),
                    os.path.join(ROOT, clean_path, "index.md"),
                ]
            )

        for md_file in candidates:
            if os.path.isfile(md_file):
                html = render_markdown_file(md_file).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return

        super().do_GET()


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), LiveJekyllHandler) as httpd:
        url = f"http://localhost:{PORT}"
        print(f"Serving Voice with Beth preview at {url} (Press Ctrl+C to stop)")
        webbrowser.open(url)
        httpd.serve_forever()
