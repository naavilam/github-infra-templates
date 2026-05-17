#!/usr/bin/env python3
import argparse, os, shutil, json, html
from pathlib import Path
import subprocess
from datetime import datetime
import sys
import re, json, html
import yaml
import html


def _load_yaml_file(p: Path) -> dict:
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _merge_dicts(base: dict, extra: dict) -> dict:
    out = dict(base or {})
    for k, v in (extra or {}).items():
        out[k] = v
    return out

def load_config(cfg_path: Path) -> dict:
    """
    Se cfg_path for arquivo: lê.
    Se cfg_path for diretório: lê todos *.yml/*.yaml (recursivo) e mescla.
    Depois aplica a regra do HERO_URL (BASE+SUBDIR+FILE).
    """
    if not cfg_path or not cfg_path.exists():
        return {}

    data = {}
    if cfg_path.is_file():
        data = _load_yaml_file(cfg_path)
    else:
        files = []
        for pat in ("*.yml", "*.yaml"):
            files.extend(sorted(cfg_path.rglob(pat)))
        for f in files:
            data = _merge_dicts(data, _load_yaml_file(f))

    if not data.get("HERO_URL"):
        base = (data.get("ASSETS_BASE") or "").rstrip("/")
        sub  = (data.get("ASSETS_SUBDIR") or "").strip("/")
        fil  = (data.get("HERO_FILE") or "").lstrip("/")
        if base and fil:
            data["HERO_URL"] = "/".join(p for p in [base, sub, fil] if p)

    return data

# ============================= Helpers =============================

IGNORE_DIRS = {
    ".git", ".github", ".venv", "venv", "__pycache__",
    "node_modules", ".script", "site"
}

def copy_tree(src_dir: Path, dst_dir: Path):
    """
    Copia todo o conteúdo de src_dir para dst_dir (se existir).
    Mantém estrutura, sobrescreve arquivos.
    """
    if not src_dir.exists():
        return
    for root, dirs, files in os.walk(src_dir):
        rel = Path(root).relative_to(src_dir)
        out_root = dst_dir / rel
        out_root.mkdir(parents=True, exist_ok=True)
        for f in files:
            src_f = Path(root) / f
            dst_f = out_root / f
            shutil.copy2(src_f, dst_f)

def load_template_index(template_dir: Path) -> str:
    """
    Lê template/index.html. Lança erro claro se não existir.
    """
    index_path = template_dir / "index.html"
    if not index_path.exists():
        raise FileNotFoundError(f"Template missing: {index_path}")
    return index_path.read_text(encoding="utf-8")

def render_index(index_src: str, title: str, nb_count: int, tree: dict) -> str:
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    safe_json = json.dumps(tree, ensure_ascii=False).replace("</", "<\\/")  # evita fechar <script>

    rep = {
        r"\{\{\s*TITLE\s*\}\}": html.escape(title),
        r"\{\{\s*TIMESTAMP\s*\}\}": timestamp,
        r"\{\{\s*NBCOUNT\s*\}\}": str(nb_count),
        r"\{\{\s*TREE_JSON\s*\}\}": safe_json,
    }
    out = index_src
    for pattern, value in rep.items():
        out = re.sub(pattern, lambda m, v=value: v, out)  # <— literal
    return out


def make_proof_fold_copy(ipynb_path: Path, tmp_dir: Path) -> Path:
    nb = json.loads(ipynb_path.read_text(encoding="utf-8"))

    for cell in nb.get("cells", []):
        tags = cell.get("metadata", {}).get("tags", [])

        if cell.get("cell_type") == "markdown" and "proof" in tags:
            source = "".join(cell.get("source", []))

            # remove título "Proof" se já existir
            source = re.sub(r"^\s*#{1,6}\s*Proof\s*\n+", "", source)

            wrapped = f"""
<details class="proof-block">
<summary>Proof</summary>

{source}

</details>
"""

            cell["source"] = wrapped.splitlines(keepends=True)

    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / ipynb_path.name
    tmp_path.write_text(json.dumps(nb, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path

def fold_proof_blocks_in_html(html_path: Path):
    s = html_path.read_text(encoding="utf-8", errors="ignore")

    before_has_details = "<details class=\"proof-block\"" in s
    before_has_proof_h = re.search(r"<h[1-6][^>]*id=[\"']Proof[\"'][^>]*>", s) is not None

    print(f"[proof-fold] file={html_path}")
    print(f"[proof-fold] before: has_details={before_has_details} has_h_proof={before_has_proof_h}")

    pattern = re.compile(
        r'(<div class="text_cell_render[^"]* rendered_html">\s*)'
        r'<h[1-6][^>]*id=["\']Proof["\'][^>]*>.*?</h[1-6]>'
        r'(.*?)'
        r'(</div>\s*</div>\s*</div>)',
        flags=re.DOTALL | re.IGNORECASE,
    )

    def repl(m):
        before = m.group(1)
        body = m.group(2)
        after = m.group(3)
        return (
            before
            + '<details class="proof-block">\n'
            + '<summary>Proof</summary>\n'
            + body
            + '\n</details>\n'
            + after
        )

    s2, n = pattern.subn(repl, s)

    after_has_details = "<details class=\"proof-block\"" in s2
    after_has_proof_h = re.search(r"<h[1-6][^>]*id=[\"']Proof[\"'][^>]*>", s2) is not None

    print(f"[proof-fold] converted={n}")
    print(f"[proof-fold] after: has_details={after_has_details} has_h_proof={after_has_proof_h}")

    html_path.write_text(s2, encoding="utf-8")

# ================== FORMATACAO DAS PROVAS MATEMATICAS ==================
def _parse_attrs(attr_text: str) -> dict:
    attrs = {}
    pattern = re.compile(r'([a-zA-Z_][\w-]*)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s]+))')
    for m in pattern.finditer(attr_text or ""):
        key = m.group(1).strip().lower()
        val = m.group(3) or m.group(4) or m.group(5) or ""
        attrs[key] = val.strip()
    return attrs


def _slugify(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or "item"


def _extract_heading_text(block_html: str) -> str:
    m = re.search(
        r"<h[1-6][^>]*>(.*?)</h[1-6]>",
        block_html or "",
        flags=re.DOTALL | re.IGNORECASE,
    )

    if not m:
        return "Untitled"

    content = m.group(1)

    # remove anchor do nbconvert/jupyter
    content = re.sub(
        r'<a[^>]*class="anchor-link"[^>]*>.*?</a>',
        '',
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )

    txt = re.sub(r"<[^>]+>", "", content)

    txt = html.unescape(txt).strip()

    txt = re.sub(
        r"^(Theorem|Definition|Lemma|Corollary|Axiom|Postulate|Proposition|Result|Example|Remark)\s*:\s*",
        "",
        txt,
        flags=re.I,
    )

    return txt or "Untitled"


def fold_math_blocks_in_html(html_path: Path):
    s = html_path.read_text(encoding="utf-8", errors="ignore")

    allowed = {
        "theorem", "definition", "lemma", "corollary",
        "axiom", "postulate", "proposition", "result",
        "example", "remark",
    }

    type_names = {
        "theorem": "Theorem",
        "definition": "Definition",
        "lemma": "Lemma",
        "corollary": "Corollary",
        "axiom": "Axiom",
        "postulate": "Postulate",
        "proposition": "Proposition",
        "result": "Result",
        "example": "Example",
        "remark": "Remark",
    }

    chapter_n = 0
    section_n = 0
    subsection_n = 0
    block_counters = {k: 0 for k in allowed}
    dependent_counters = {
        "corollary": 0,
    }
    last_major_number = None

    current_chapter = None
    current_section = None
    current_subsection = None

    index_items = []

    token_pattern = re.compile(
        r'<!--\s*chapter:start\s*(.*?)\s*-->'
        r'|<!--\s*section:start\s*(.*?)\s*-->'
        r'|<!--\s*subsection:start\s*(.*?)\s*-->'
        r'|<!--\s*block:start\s+([a-zA-Z_-]+)(.*?)\s*-->'
        r'(.*?)'
        r'(?:<!--\s*proof:start\s*-->(.*?))?'
        r'<!--\s*block:end\s*-->',
        flags=re.DOTALL | re.IGNORECASE,
    )

    def repl(m):
        nonlocal chapter_n, section_n, subsection_n
        nonlocal block_counters, dependent_counters, last_major_number
        nonlocal current_chapter, current_section, current_subsection

        if m.group(1) is not None:
            attrs = _parse_attrs(m.group(1))
            chapter_n += 1
            section_n = 0
            subsection_n = 0
            block_counters = {k: 0 for k in allowed}
            dependent_counters = {
                "corollary": 0,
            }
            last_major_number = None

            title = attrs.get("title", f"Chapter {chapter_n}")
            cid = attrs.get("label") or f"chapter-{chapter_n}"
            current_chapter = {"number": str(chapter_n), "title": title, "id": cid}

            index_items.append({
                "kind": "chapter",
                "number": str(chapter_n),
                "title": title,
                "id": cid,
            })

            return f'<span id="{html.escape(cid, quote=True)}" class="math-anchor math-chapter-anchor"></span>'

        if m.group(2) is not None:
            attrs = _parse_attrs(m.group(2))
            if chapter_n == 0:
                chapter_n = 1
                current_chapter = {"number": "1", "title": "Chapter 1", "id": "chapter-1"}

            section_n += 1
            subsection_n = 0
            block_counters = {k: 0 for k in allowed}
            dependent_counters = {
                "corollary": 0,
            }
            last_major_number = None

            number = f"{chapter_n}.{section_n}"
            title = attrs.get("title", f"Section {number}")
            sid = attrs.get("label") or f"section-{chapter_n}-{section_n}"
            current_section = {"number": number, "title": title, "id": sid}

            index_items.append({
                "kind": "section",
                "number": number,
                "title": title,
                "id": sid,
            })

            return f'<span id="{html.escape(sid, quote=True)}" class="math-anchor math-section-anchor"></span>'

        if m.group(3) is not None:
            attrs = _parse_attrs(m.group(3))
            if chapter_n == 0:
                chapter_n = 1
                current_chapter = {"number": "1", "title": "Chapter 1", "id": "chapter-1"}
            if section_n == 0:
                section_n = 1
                current_section = {"number": f"{chapter_n}.1", "title": "Section 1", "id": f"section-{chapter_n}-1"}

            subsection_n += 1
            block_counters = {k: 0 for k in allowed}
            dependent_counters = {
                "corollary": 0,
            }
            last_major_number = None

            number = f"{chapter_n}.{section_n}.{subsection_n}"
            title = attrs.get("title", f"Subsection {number}")
            ssid = attrs.get("label") or f"subsection-{chapter_n}-{section_n}-{subsection_n}"
            current_subsection = {"number": number, "title": title, "id": ssid}

            index_items.append({
                "kind": "subsection",
                "number": number,
                "title": title,
                "id": ssid,
            })

            return f'<span id="{html.escape(ssid, quote=True)}" class="math-anchor math-subsection-anchor"></span>'

        block_type = (m.group(4) or "").strip().lower()
        attrs = _parse_attrs(m.group(5) or "")
        main_html = (m.group(6) or "").strip()
        proof_html = (m.group(7) or "").strip()

        if block_type not in allowed:
            block_type = "proposition"

        if subsection_n:
            prefix = f"{chapter_n}.{section_n}.{subsection_n}"
        elif section_n:
            prefix = f"{chapter_n}.{section_n}"
        elif chapter_n:
            prefix = f"{chapter_n}"
        else:
            prefix = "1"

        major_types = {"theorem", "proposition", "result"}

        if block_type == "corollary":
            dependent_counters["corollary"] += 1

            if last_major_number:
                number = f"{last_major_number}.{dependent_counters['corollary']}"
            else:
                number = f"{prefix}.{dependent_counters['corollary']}"

        else:
            block_counters[block_type] += 1
            number = f"{prefix}.{block_counters[block_type]}"

            if block_type in major_types:
                last_major_number = number
                dependent_counters = {
                    "corollary": 0,
                }

        title = _extract_heading_text(main_html)
        label = attrs.get("label") or f"{block_type}-{number.replace('.', '-')}-{_slugify(title)}"

        proof_html = re.sub(
            r'^\s*<h[1-6][^>]*>\s*Proof\s*.*?</h[1-6]>\s*',
            '',
            proof_html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        heading_replacement = (
            f'<span class="math-number">'
            f'{number} {type_names[block_type]}: {html.escape(title)}'
            f'</span>'
        )

        def replace_heading(match):
            level = match.group(1)
            attrs = match.group(2)
            return f"<h{level}{attrs}>{heading_replacement}</h{level}>"

        main_html = re.sub(
            r"<h([1-6])([^>]*)>.*?</h\1>",
            replace_heading,
            main_html,
            count=1,
            flags=re.DOTALL | re.IGNORECASE,
        )

        index_items.append({
            "kind": "block",
            "type": block_type,
            "type_name": type_names[block_type],
            "number": number,
            "title": title,
            "id": label,
        })

        links = ['<a href="#math-index">index</a>']
        if current_chapter:
            links.append(f'<a href="#{html.escape(current_chapter["id"], quote=True)}">chapter</a>')
        if current_section:
            links.append(f'<a href="#{html.escape(current_section["id"], quote=True)}">section</a>')
        if current_subsection:
            links.append(f'<a href="#{html.escape(current_subsection["id"], quote=True)}">subsection</a>')

        nav = '<div class="math-block-links">↑ ' + " · ".join(links) + '</div>'
        
        if proof_html:
            details_html = f"""
        <div class="proof-content">
        {proof_html}
        </div>
        """
        else:
            details_html = ""

        return f"""
        <details id="{html.escape(label, quote=True)}" class="math-block {block_type}">
        <summary class="math-block-summary">
        {main_html}
        </summary>

        {details_html}

        <div class="math-block-footer">
        {nav}
        </div>

        </details>
        """

    s2, n = token_pattern.subn(repl, s)

    if index_items:
        index_html = render_math_index_html(index_items)

        if re.search(r'<!--\s*math-index\s*-->', s2, flags=re.I):
            s2 = re.sub(r'<!--\s*math-index\s*-->', index_html, s2, count=1, flags=re.I)
        else:
            first = re.search(r'<(?:details|div) id="[^"]+" class="math-block', s2)
            if first:
                s2 = s2[:first.start()] + index_html + "\n" + s2[first.start():]

    print(f"[math-structure] converted_blocks={n} index_items={len(index_items)} file={html_path}")
    html_path.write_text(s2, encoding="utf-8")


def render_math_index_html(items: list[dict]) -> str:
    if not items:
        return ""

    out = ['<nav id="math-index" class="math-index">']
    out.append("<ul>")

    for item in items:
        kind = item.get("kind")
        number = html.escape(str(item.get("number", "")))
        title = html.escape(str(item.get("title", "")))
        item_id = html.escape(str(item.get("id", "")), quote=True)

        if kind == "chapter":
            out.append(f'<li class="math-index-chapter"><a href="#{item_id}">Chapter {number} — {title}</a></li>')
        elif kind == "section":
            out.append(f'<li class="math-index-section"><a href="#{item_id}">Section {number} — {title}</a></li>')
        elif kind == "subsection":
            out.append(f'<li class="math-index-subsection"><a href="#{item_id}">Subsection {number} — {title}</a></li>')
        elif kind == "block":
            type_name = html.escape(str(item.get("type_name", "")))
            out.append(f'<li class="math-index-block math-index-{item.get("type")}"><a href="#{item_id}"><span class="math-number">{number}</span> {type_name}: {title}</a></li>')

    out.append("</ul>")
    out.append("</nav>")
    return "\n".join(out)
# ====================== Núcleo de varredura/build ======================

#     return root, nb_count
import sys, subprocess
from pathlib import Path

def ensure_minimal_cell(ipynb_path: Path):
    """
    Garante que o notebook tenha pelo menos 1 célula renderizável.
    - Se cells == []  -> injeta 1 markdown.
    - Se não existir nenhuma célula do tipo markdown/code -> injeta 1 markdown no topo.
    """
    try:
        nb = json.loads(ipynb_path.read_text(encoding="utf-8"))
    except Exception:
        return

    cells = nb.get("cells", [])
    has_md_or_code = any(
        isinstance(c, dict) and c.get("cell_type") in ("markdown", "code")
        for c in cells
    )

    if (not cells) or (not has_md_or_code):
        nb["cells"] = [{
            "cell_type": "markdown",
            "metadata": {},
            "source": ["_Notebook criado — conteúdo em construção._\n"]
        }] + (cells if isinstance(cells, list) else [])

        ipynb_path.write_text(
            json.dumps(nb, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

def collect_tree(src: Path, out: Path, execute: bool):
    """
    Varre src; converte apenas .ipynb -> .html em out.
    - Arquivos que não sejam .ipynb são ignorados.
    - Diretórios sem nenhum notebook são removidos da árvore.
    """
    nb_count = 0
    root = {"type": "dir", "name": src.name, "path": "", "children": []}
    dir_map = {str(src.resolve()): root}

    for path in sorted(src.rglob("*")):
        if out in path.parents or path == out:
            continue
        rel_parts = path.relative_to(src).parts
        if not rel_parts:
            continue

        # Garante nós de diretório
        cur = src
        parent_node = root
        for i, p in enumerate(rel_parts[:-1] if not path.is_dir() else rel_parts):
            cur = cur / p
            key = str(cur.resolve())
            if key not in dir_map:
                node = {"type": "dir", "name": p, "path": str(Path(*rel_parts[: i + 1])), "children": []}
                parent_node["children"].append(node)
                dir_map[key] = node
            parent_node = dir_map[key]

        # Se for diretório, só garante hierarquia
        if path.is_dir():
            continue

        # Se não for .ipynb → ignora
        if path.suffix.lower() != ".ipynb":
            continue

        # Garante que notebooks vazios (ou sem md/code) não quebrem o nbconvert
        ensure_minimal_cell(path)

        # Converte notebook
        rel = path.relative_to(src)
        file_node = {"type": "file", "name": rel.name, "path": str(rel)}
        nb_count += 1

        out_html = (out / rel).with_suffix(".html")
        out_html.parent.mkdir(parents=True, exist_ok=True)


        tmp_nb_dir = out / ".tmp_nbconvert"
        nb_for_convert = make_proof_fold_copy(path, tmp_nb_dir)

        cmd = [
            sys.executable, "-m", "nbconvert",
            "--to", "html",
            "--template=classic",
            "--HTMLExporter.embed_images=True",
            "--TagRemovePreprocessor.enabled=True",
            "--TagRemovePreprocessor.remove_input_tags=hide-input",
            "--TagRemovePreprocessor.remove_all_outputs_tags={'remove-output','ro'}",
            "--output", out_html.name,
            "--output-dir", str(out_html.parent),
            str(nb_for_convert),
        ]
        

        def _widen_notebook_html(html_path: Path):
            try:
                s = html_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return

            css = """
        <style id="wide-notebook">
        /* make nbconvert classic use full width */
        #notebook-container {
        width: 100% !important;
        max-width: none !important;
        }
        .container, .container-fluid {
        width: 100% !important;
        max-width: none !important;
        }
        div#notebook {
        width: 100% !important;
        }
        body {
        margin: 0 !important;
        padding: 0 !important;
        }
        /* ====== Reduce classic nbconvert left gutter + top gap ====== */

        /* remove o "gutter" / margem esquerda que vira aquele bloco vazio */
        div.prompt.input_prompt {
        width: 42px !important;      /* era ~90-110px; ajuste fino aqui */
        min-width: 42px !important;
        }

        /* alguns themes usam .prompt */
        .prompt {
        width: 42px !important;
        min-width: 42px !important;
        }

        /* reduz padding horizontal do notebook (onde sobra ar demais) */
        #notebook {
        padding-left: 12px !important;
        padding-right: 12px !important;
        }

        /* reduz padding interno das células (ajuda a “encostar” mais no layout) */
        div.cell {
        padding-left: 0 !important;
        padding-right: 0 !important;
        }

        /* mata aquele gap no topo do primeiro conteúdo */
        #notebook-container {
        padding-top: 0 !important;
        margin-top: 0 !important;
        }

        /* se o classic estiver colocando sombra/borda feia no container, desliga */
        #notebook-container {
        box-shadow: none !important;
        border: 0 !important;
        }

        /* garante que o body não crie faixa branca extra */
        html, body {
        margin: 0 !important;
        padding: 0 !important;
        }
        div.prompt.input_prompt,
        div.prompt.output_prompt {
            display: none !important;
        }

        div.prompt {
            display: none !important;
        }

        div.output_area {
            width: 100% !important;
            margin-left: 0 !important;
        }

        div.output_area .output_subarea {
            display: flex !important;
            justify-content: center !important;
        }

        div.output_area .output_subarea > * {
            max-width: 100%;
        }

        div.output_area img,
        div.output_area svg {
            display: block;
            margin: 0 auto;
        }

        /* Indent só no conteúdo renderizado do Markdown (não mexe em código/output) */
        #notebook .text_cell_render p,
        #notebook .text_cell_render h1,
        #notebook .text_cell_render h2,
        #notebook .text_cell_render h3,
        #notebook .text_cell_render h4,
        #notebook .text_cell_render h5,
        #notebook .text_cell_render h6 {
            text-indent: 55px; /* ajuste aqui */
        }

        /* Remove indentação de p que esteja dentro de listas */
        #notebook .text_cell_render ul p,
        #notebook .text_cell_render ol p{
        text-indent: 0;
        }

        #notebook ul,
        #notebook ol {
            margin-left: 55px;
        }

        /* Justificar parágrafos no conteúdo do notebook */
        #notebook p,
        #notebook li {
        text-align: justify;
        /* text-justify: inter-word;   melhor em alguns browsers */
        hyphens: auto;              /* hifeniza quando suportado */
        -webkit-hyphens: auto;
        -ms-hyphens: auto;
        }
        .simulation-box,
        .deps-box {
            max-width: 1200px;
            margin: 24px auto;
        }

        /* Faz o output ocupar toda a largura */
        div.output_area {
            display: block !important;
        }

        /* Remove o limite interno */
        div.output_html.rendered_html {
            max-width: 100% !important;
            width: 100% !important;
            padding-left: 0 !important;
            padding-right: 0 !important;
        }

        /* Centraliza qualquer card interno */
        .fullwidth-center {
            width: 100%;
            display: flex;
            justify-content: center;
        }

        /* remove a faixa do output HTML */
        div.output_html.rendered_html.output_subarea {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }

        /* Esconde qualquer saída em stderr no notebook exportado */
        .output_subarea.output_stream.output_stderr {
            display: none !important;
        }

        .output_subarea.output_stream.output_stderr.output_text {
            display: none !important;
        }

        .math-block {
            margin: 2px;
            border-radius: 14px;
            background: var(--block-bg, #f7f7f7);
            overflow: hidden;
        }

        .math-block.theorem {
            --block-border: #2563eb;
            --block-bg: #f8fafc;
        }

        .math-block.definition {
            --block-border: #0f766e;
            --block-bg: #f8fafc;
        }

        .math-block.lemma {
            --block-border: #7c3aed;
            --block-bg: #f8fafc;
        }

        .math-block.corollary {
            --block-border: #d97706;
            --block-bg: #f8fafc;
        }

        .math-block.axiom {
            --block-border: #be123c;
            --block-bg: #f8fafc;
        }

        .math-block.proposition {
            --block-border: #475569;
            --block-bg: #f8fafc;
        }

        .math-block.example {
            --block-border: #0891b2;
            --block-bg: #f8fafc;
        }

        .math-block.remark {
            --block-border: #334155;
            --block-bg: #f8fafc;
        }

        .math-block.result {
            --block-border: #9333ea;
            --block-bg: #f8fafc;
        }

        .math-block.postulate {
            --block-border: #b45309;
            --block-bg: #f8fafc;
        }

        .math-block summary,
        .math-block-summary {
            cursor: pointer;
            padding: 22px 26px;
            list-style: none;
        }

        .math-block summary::-webkit-details-marker {
            display: none;
        }

        .math-block-summary h1,
        .math-block-summary h2,
        .math-block-summary h3,
        .math-block-summary h4,
        .math-block-summary p {
            text-indent: 0 !important;
        }

        .proof-content {
            margin: 0 10px 22px 10px;
            padding: 22px 26px;
            
            border-radius: 12px;
            background: white;
        }

        .proof-content p,
        .proof-content h1,
        .proof-content h2,
        .proof-content h3,
        .proof-content h4,
        .proof-content ul,
        .proof-content ol {
            text-indent: 0 !important;
        }

        .math-index {
            margin: 0px 2px 6px 2px;
            padding: 22px 26px;
            border-radius: 16px;
            background: #f8fafc;
            border: 1px solid rgba(15, 23, 42, 0.12);
        }

        .math-index h2 {
            margin-top: 0;
            text-indent: 0 !important;
        }

        .math-index ul {
            margin-left: 0 !important;
            padding-left: 0 !important;
            list-style: none;
        }

        .math-index li {
            text-align: left !important;
            margin: 6px 0;
        }

        .math-index a {
            text-decoration: none;
            color: #1e293b;
        }

        .math-index a:hover {
            text-decoration: underline;
        }

        .math-index-chapter {
            margin-top: 14px !important;
            font-weight: 800;
        }

        .math-index-section {
            margin-left: 18px !important;
            font-weight: 650;
        }

        .math-index-subsection {
            margin-left: 36px !important;
            font-weight: 550;
        }

        .math-index-block {
            margin-left: 54px !important;
            font-size: 0.95em;
        }

        .math-block-footer {
            padding: 5px 26px 10px 26px;
            background: rgba(255,255,255,0.55);
        }

        .math-block-links {
            padding-top: 0px;
            font-size: 0.85em;
            text-align: right;
            color: #cbd5e1;
        }

        .math-block-links a {
            color: #94a3b8;
            text-decoration: none;
        }

        .math-block-links a:hover {
            color: #64748b;
            text-decoration: underline;
        }

        .math-anchor {
            scroll-margin-top: 90px;
        }

        .math-block {
            scroll-margin-top: 90px;
        }
    
        /* remove espaço externo do markdown renderizado */
        div.text_cell_render.rendered_html {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
            margin-top: 0 !important;
            margin-bottom: 0 !important;
        }

        /* remove padding da célula markdown */
        div.text_cell {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }

        /* remove espaço da inner cell */
        div.inner_cell {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }

        /* remove margem automática dos blocos HTML renderizados */
        .rendered_html {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }

        .math-number {
            color: #94a3b8;
            font-size: 0.82em;
            font-weight: 500;
            letter-spacing: 0.02em;
        }

        .math-block-summary {
            padding: 18px 26px 24px 26px;
        }

        /* título do card vira metadado discreto */
        .math-block-summary h1,
        .math-block-summary h2,
        .math-block-summary h3,
        .math-block-summary h4 {
            margin: 0 0 14px 0 !important;
            padding: 0 !important;

            font-size: 0.95rem !important;
            line-height: 1.2 !important;
            font-weight: 600 !important;
            letter-spacing: 0.04em;

            color: var(--block-border, #64748b) !important;
            opacity: 0.72;

            text-transform: uppercase;
            text-indent: 0 !important;
        }

        .math-number {
            color: inherit;
            font-size: inherit;
            font-weight: inherit;
            letter-spacing: inherit;
        }

        /* equações ganham prioridade visual */
        .math-block-summary .MathJax_Display,
        .math-block-summary mjx-container[display="true"] {
            margin: 1.1rem 0 0.6rem 0 !important;
            font-size: 1.08em;
        }
        </style>
        """.strip()

            if "id=\"wide-notebook\"" in s:
                return

            if "</head>" in s:
                s = s.replace("</head>", css + "\n</head>", 1)
            elif "<body" in s:
                s = re.sub(r"(<body[^>]*>)", r"\1\n" + css + "\n", s, count=1)
            else:
                s = css + "\n" + s

            html_path.write_text(s, encoding="utf-8")


        if execute:
            cmd.append("--execute")
        subprocess.run(cmd, check=True)

        fold_math_blocks_in_html(out_html)
        fold_proof_blocks_in_html(out_html)
        _widen_notebook_html(out_html)

        file_node["nb_html"] = str(out_html.relative_to(out)).replace(os.sep, "/")

        parent_key = str(path.parent.resolve())
        dir_map[parent_key]["children"].append(file_node)

    # --- remove diretórios vazios ---
    def prune_empty_dirs(node):
        if node["type"] == "file":
            return node, True
        new_children = []
        has_ipynb = False
        for ch in node.get("children", []):
            pruned, child_has_ipynb = prune_empty_dirs(ch)
            if pruned:
                new_children.append(pruned)
            has_ipynb = has_ipynb or child_has_ipynb
        node["children"] = new_children
        return (node if has_ipynb else None), has_ipynb

    root, _ = prune_empty_dirs(root)
    if root is None:
        root = {"type": "dir", "name": src.name, "path": "", "children": []}

    return root, nb_count


def build_static_site(src: Path, out: Path, template_dir: Path, title: str, execute: bool, cfg: dict | None):
    # tree, nb_count = collect_tree(src, out, execute)

    # === NOVO: carregar refs do repo e colocar no cfg para render_tokens ===
    refs = load_references(src)  # assumes references.yml no root do repo (src)
    refs_html = render_references_html(refs)
    cfg = dict(cfg or {})
    cfg["REFERENCIAS"] = refs_html
    
    tree, nb_count = collect_tree(src, out, execute)

    out.mkdir(parents=True, exist_ok=True)

    # 1) Copia template primeiro (evita sobrescrever reports.json depois)
    copy_tree(template_dir / "css", out / "css")
    copy_tree(template_dir / "assets", out / "assets")
    copy_tree(template_dir / "js", out / "js")

    # 2) Copia PDFs para o root do site
    copy_reports_to_site_recursive(src_repo=src, out_site=out, pdf_name="report.pdf", debug=True)

    # 3) Gera reports.json (depois do copy_tree)
    reports_json = build_reports_json_recursive(src_repo=src, out_site=out, pdf_name="report.pdf", debug=True)

    if reports_json is None:
        # remove reports.json antigo se sobrou de build anterior
        stale = out / "assets" / "tree" / "reports.json"
        if stale.exists():
            stale.unlink()

    # 4) Geram as páginas
    pages = [
        ("index.html", False),     # Home
        ("studies.html", True),   # Studies (com árvore)
        ("publications.html", False),
        ("references.html", False),
    ]
    for fname, needs_tree in pages:
        page_path = template_dir / fname
        if not page_path.exists():
            continue
        src_html = page_path.read_text(encoding="utf-8")
        html_doc = render_tokens(src_html, title, nb_count, tree if needs_tree else None, cfg)
        (out / fname).write_text(html_doc, encoding="utf-8")

    return nb_count

# ================================ CLI ================================

def render_tokens(src: str, title: str, nb_count: int, tree: dict | None, cfg: dict | None):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # básicos
    rep = {
        r"\{\{\s*TITLE\s*\}\}": html.escape(title),
        r"\{\{\s*TIMESTAMP\s*\}\}": ts,
        r"\{\{\s*NBCOUNT\s*\}\}": str(nb_count),
    }

    # placeholders vindos do YAML (substituição literal, permitindo HTML)
    if cfg:
        for k, v in cfg.items():
            if v is None:
                continue
            pat = rf"\{{\{{\s*{re.escape(k)}\s*\}}\}}"
            rep[pat] = str(v)

    out = src
    for pat, val in rep.items():
        out = re.sub(pat, lambda m, v=val: v, out)

    if tree is not None:
        safe_json = json.dumps(tree, ensure_ascii=False).replace("</", "<\\/")
        out = re.sub(r"\{\{\s*TREE_JSON\s*\}\}", lambda m, v=safe_json: v, out)

    return out

def load_references(repo_path: Path) -> list[dict]:
    p = repo_path / "references.yml"
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        refs = data.get("references", [])
        return refs if isinstance(refs, list) else []
    except Exception as e:
        print(f"[warn] references.yml inválido em {repo_path}: {e}")
        return []

def render_references_html(refs: list[dict]) -> str:
    if not refs:
        return '<p class="muted"><em>No references provided yet.</em></p>'

    items = []
    for r in refs:
        title = html.escape(str(r.get("title") or "").strip() or "Untitled")
        author = html.escape(str(r.get("author") or "").strip())
        year = html.escape(str(r.get("year") or "").strip())
        note = html.escape(str(r.get("note") or "").strip())

        url = str(r.get("url") or "").strip()
        image_url = str(r.get("image_url") or "").strip()

        safe_url = html.escape(url, quote=True) if url else ""
        safe_img = html.escape(image_url, quote=True) if image_url else ""

        cover_html = ""
        if safe_img:
            cover_html = (
                "<div class='ref-cover-wrap'>"
                f"<img class='ref-cover' src='{safe_img}' alt='Cover of {title}' loading='lazy'>"
                "</div>"
            )

        if safe_url:
            title_html = (
                f"<a href='{safe_url}' target='_blank' rel='noopener noreferrer'>"
                f"<strong class='ref-title'>{title}</strong>"
                "</a>"
            )
        else:
            title_html = f"<strong class='ref-title'>{title}</strong>"

        parts = [title_html]

        meta = " — ".join([p for p in [author, year] if p])
        if meta:
            parts.append(f"<div class='ref-meta'>{meta}</div>")

        if note:
            parts.append(f"<div class='ref-note'><em>{note}</em></div>")

        body_html = "<div class='ref-body'>" + "\n".join(parts) + "</div>"

        items.append(
            "<li class='ref-item ref-card'>"
            + cover_html
            + body_html
            + "</li>"
        )

    return "<ul class='ref-list ref-grid'>\n" + "\n".join(items) + "\n</ul>"

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

import re, shutil
from pathlib import Path

def copy_reports_to_site_recursive(src_repo: Path, out_site: Path, pdf_name: str = "report.pdf", debug: bool = True):
    """
    Procura recursivamente por pastas YYYY-MM-DD que contenham report.pdf
    e copia para:
        out_site/YYYY-MM-DD/report.pdf
    """
    copied = 0
    if debug:
        print(f"[reports] src_repo={src_repo}")
        print(f"[reports] out_site={out_site}")
        print(f"[reports] pdf_name={pdf_name}")

    for p in sorted(src_repo.rglob("*")):
        if not p.is_dir():
            continue
        if not DATE_DIR_RE.match(p.name):
            continue

        pdf_path = p / pdf_name
        if not pdf_path.exists():
            continue

        dst_dir = out_site / p.name
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, dst_dir / pdf_name)
        copied += 1

        if debug:
            rel_found = str(p.relative_to(src_repo)).replace(os.sep, "/")
            print(f"[reports] found: {rel_found}/{pdf_name}")
            print(f"[reports]  -> copy: {p.name}/{pdf_name}")

    if debug:
        print(f"[reports] copied={copied}")
    return copied


def build_reports_json_recursive(src_repo: Path, out_site: Path, pdf_name: str = "report.pdf", debug: bool = True):
    """
    Gera um nó "Reports" com paths relativos ao root do site:
      { type:"pdf", title:"2026-01-23 — report", path:"2026-01-23/report.pdf" }

    Escreve em:
      out_site/assets/tree/reports.json
    """
    reports = {"type": "folder", "title": "Reports", "children": []}
    by_year = {}

    found = 0
    for p in sorted(src_repo.rglob("*")):
        if not p.is_dir():
            continue
        if not DATE_DIR_RE.match(p.name):
            continue

        pdf_path = p / pdf_name
        if not pdf_path.exists():
            continue

        date = p.name
        year = date[:4]
        month = date[:7]  # YYYY-MM
        rel_path = f"{date}/{pdf_name}"

        by_year.setdefault(year, {}).setdefault(month, []).append({
            "type": "pdf",
            "title": f"{date} — report",
            "path": rel_path
        })
        found += 1

    if found == 0:
        if debug:
            print("[reports] no reports found; skipping reports.json")
        return None

    for year in sorted(by_year.keys()):
        months = []
        for month in sorted(by_year[year].keys()):
            items = sorted(by_year[year][month], key=lambda x: x["title"])
            months.append({"type": "folder", "title": month, "children": items})
        reports["children"].append({"type": "folder", "title": year, "children": months})

    out_json = out_site / "assets" / "tree" / "reports.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if debug:
        print(f"[reports] reports.json -> {out_json}")
        print(f"[reports] entries={found}")
        # mostra 3 exemplos
        sample = []
        for y in reports["children"]:
            for m in y.get("children", []):
                for it in m.get("children", []):
                    sample.append(it.get("path"))
                    if len(sample) >= 3:
                        break
                if len(sample) >= 3:
                    break
            if len(sample) >= 3:
                break
        print(f"[reports] sample_paths={sample}")

    return out_json

def main():
    ap = argparse.ArgumentParser(
        description="Gera um site estático a partir de notebooks .ipynb usando nbconvert e um template externo."
    )
    ap.add_argument("--src", type=str, required=True)
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument("--template", type=str, required=True)
    ap.add_argument("--title", type=str, default=None)
    ap.add_argument("--execute", type=str, default="false")
    ap.add_argument("--cfg", type=str, default=None)  # <-- ADICIONE ISTO
    args = ap.parse_args()

    src = Path(args.src).resolve()
    out = Path(args.out).resolve()
    template_dir = Path(args.template).resolve()
    execute = args.execute.lower() == "true"
    title = args.title or f"Notebooks Tree — {src.name}"
    cfg = load_config(Path(args.cfg)) if args.cfg else {}   # <-- E ISTO

    nb_count = build_static_site(src, out, template_dir, title, execute, cfg)  # <-- E ISTO
    print(f"[OK] Gerado em {out} • notebooks convertidos: {nb_count}")

if __name__ == "__main__":
    main()
