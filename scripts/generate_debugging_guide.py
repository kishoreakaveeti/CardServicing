"""Render journey diagrams and breakpoint links from named source symbols.

Standard library only; never imports or starts application services.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path("docs/debugging-journeys.json")
GUIDE = Path("docs/debugging-account-and-card.md")
START = "<!-- BEGIN GENERATED DEBUG JOURNEYS -->"
END = "<!-- END GENERATED DEBUG JOURNEYS -->"


@dataclass(frozen=True)
class Location:
    declaration: int
    executable: int
    statement: str


def python_symbols(source: str) -> dict[str, Location]:
    """Resolve lexical qualified names, including nested app-factory handlers."""
    result = {}
    lines = source.splitlines()

    def visit(node, parents):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [*parents, child.name]
                if not isinstance(child, ast.ClassDef):
                    body = child.body
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        body = body[1:]
                    if not body:
                        raise ValueError(f"{'.'.join(names)} has no executable body")
                    name = ".".join(names)
                    if name in result:
                        raise ValueError(f"Ambiguous symbol: {name}")
                    line = body[0].lineno
                    result[name] = Location(child.lineno, line, lines[line - 1].strip())
                visit(child, names)
            else:
                visit(child, parents)

    visit(ast.parse(source), [])
    return result


def typescript_symbol(source: str, symbol: str) -> Location:
    """Resolve this frontend's named App handlers; reject unsupported syntax.

    This deliberately supports named function declarations, not arbitrary TS/JS
    methods, arrows or overloads. Python resolution uses a full AST.
    """
    parts = symbol.split(".")
    if len(parts) != 2 or parts[0] != "App":
        raise ValueError(f"Unsupported TS symbol {symbol}; expected App.namedHandler")
    if len(re.findall(r"^function App\(\) \{\s*$", source, re.M)) != 1:
        raise ValueError("Expected one top-level function App() declaration")
    pattern = r"^  (?:async )?function " + re.escape(parts[1]) + r"\([^\n]*\) \{\s*$"
    hits = list(re.finditer(pattern, source, re.M))
    if len(hits) != 1:
        raise ValueError(f"Missing/ambiguous TS handler {symbol}; expected named App function")
    declaration = source[:hits[0].start()].count("\n") + 1
    lines = source.splitlines()
    line = declaration + 1
    in_comment = False
    while line <= len(lines):
        text = lines[line - 1].strip()
        if in_comment:
            in_comment = "*/" not in text
        elif text.startswith("/*"):
            in_comment = "*/" not in text
        elif text and not text.startswith("//"):
            if text.startswith("}"):
                raise ValueError(f"No executable body for {symbol}")
            return Location(declaration, line, text)
        line += 1
    raise ValueError(f"No executable body for {symbol}")


def resolve_symbols(root: Path, manifest: dict) -> dict[str, Location]:
    locations, cache = {}, {}
    for key, entry in manifest["symbols"].items():
        path = (root / entry["file"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError(f"Source path escapes repository: {entry['file']}")
        if path not in cache:
            source = path.read_text(encoding="utf-8-sig")
            cache[path] = (source, python_symbols(source) if path.suffix == ".py" else None)
        source, symbols = cache[path]
        try:
            if path.suffix == ".py":
                locations[key] = symbols[entry["symbol"]]
            elif path.suffix == ".tsx":
                locations[key] = typescript_symbol(source, entry["symbol"])
            else:
                raise ValueError(f"Unsupported source file {path}")
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Cannot resolve {key}: {entry['file']}::{entry['symbol']}: {exc}") from exc
    return locations


def expand_steps(steps, segments, stack=()):
    result = []
    for step in steps:
        if "segment" in step:
            name = step["segment"]
            if name in stack:
                raise ValueError(f"Recursive journey segment: {name}")
            result.extend(expand_steps(segments[name], segments, (*stack, name)))
        else:
            if not step.get("refs"):
                raise ValueError(f"Journey step needs source refs: {step}")
            result.append(step)
    return result


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def source_link(key, manifest, locations):
    entry = manifest["symbols"][key]
    # This guide lives immediately inside docs/; paths remain clone-portable.
    return f"[{entry['symbol']}](../{entry['file']}#L{locations[key].executable})"


def render(manifest, locations):
    symbols = manifest["symbols"]
    flows = manifest["journeys"]
    ids = [flow["id"] for flow in flows]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[a-z0-9-]+", i) for i in ids):
        raise ValueError("Journey IDs must be unique lowercase slugs")
    out = [START, "", "## Journey atlas", "",
           "Generated from [named journey definitions](debugging-journeys.json) by "
           "[generate_debugging_guide.py](../scripts/generate_debugging_guide.py). "
           "The manifest stores qualified method names, never source line numbers. "
           "Diagrams and ordered tables are curated walkthroughs, not automatically inferred call graphs.", "",
           "Each method link targets its first executable statement. Repeated methods represent "
           "different requests or phases; optional branches are identified in the step notes. "
           "Set a breakpoint with F9 after opening the link. GitHub honors `#L` anchors; "
           "if a local Markdown viewer opens only the file, use Ctrl+G and the displayed line.", "",
           "### Regenerate after changing code", "", "```powershell",
           ".\\.venv\\Scripts\\python.exe scripts/generate_debugging_guide.py",
           ".\\.venv\\Scripts\\python.exe scripts/generate_debugging_guide.py --check", "```", "",
           "The first command refreshes only this generated block. `--check` writes nothing and "
           "exits nonzero for stale output or an unresolved symbol, making it suitable for CI. "
           "No services, model credentials or third-party Python packages are needed. "
           "Python uses AST resolution, including nested handlers and multiline signatures; "
           "the frontend resolver supports the current named `App` function declarations and "
           "fails explicitly if that syntax changes. If a method is renamed/moved, update its "
           "`file`/`symbol` in the manifest. Review journey descriptions when behavior changes; "
           "regeneration updates positions, not architectural meaning.", "",
           "The original account walkthrough below is preserved. Its annotated source links also "
           "refresh from method names; hidden `debug-symbol` comments identify the manifest entry. "
           "This document does not install or export VS Code workspace breakpoints.", ""]
    out.extend(f"- [{flow['title']}](#journey-{flow['id']})" for flow in flows)
    out += ["- [Complete breakpoint index](#breakpoint-index)", ""]
    for flow in flows:
        steps = expand_steps(flow["steps"], manifest.get("segments", {}))
        out += [f"### Journey: {flow['id']}", "", f"**{flow['title']}**", "",
                f"**Trigger:** {flow['trigger']}", "", f"**Expected result:** {flow['outcome']}", ""]
        for note in flow.get("notes", []):
            out += [note, ""]
        out += ["```mermaid", "flowchart TD"]
        for i, step in enumerate(steps, 1):
            label = f"{i}. {step['layer']}: {step['title']}"
            out.append(f"    S{i}[{json.dumps(label)}]")
            if i > 1:
                out.append(f"    S{i-1} --> S{i}")
        out += ["```", "", "| Step | Layer / action | Methods / breakpoint links | Inspect / behavior |",
                "| ---: | --- | --- | --- |"]
        for i, step in enumerate(steps, 1):
            links = "<br>".join(source_link(key, manifest, locations) for key in step["refs"])
            out.append(f"| {i} | {cell(step['layer'])}: {cell(step['title'])} | {links} | {cell(step['inspect'])} |")
        out += ["", f"**Troubleshoot:** {flow['troubleshoot']}", "",
                "[Journey list](#journey-atlas) · [Complete index](#breakpoint-index)", ""]
    out += ["## Breakpoint index", "",
            f"{len(symbols)} named locations. The previous 65 account/card locations are retained; "
            "the additional entries cover the other journeys. These are recommended source locations, "
            "not a claim that additional IDE breakpoints have been installed.", ""]
    for group in dict.fromkeys(entry["group"] for entry in symbols.values()):
        out += [f"### Breakpoints: {group}", "",
                "| Method / breakpoint link | File | Line | First executable statement |",
                "| --- | --- | ---: | --- |"]
        for key, entry in symbols.items():
            if entry["group"] != group:
                continue
            loc = locations[key]
            statement = cell(html.escape(loc.statement))
            out.append(f"| {source_link(key, manifest, locations)} | `{entry['file']}` | "
                       f"{loc.executable} | <code>{statement}</code> |")
        out.append("")
    # Retained index-only entries are intentional: optional startup/adapters are useful too.
    out += [END]
    return "\n".join(out)


def update_document(document: str, generated: str) -> str:
    if document.count(START) != 1 or document.count(END) != 1:
        raise ValueError("Guide must contain exactly one generated block marker pair")
    before, rest = document.split(START, 1)
    _, after = rest.split(END, 1)
    return before + generated + after


def refresh_annotated_links(document, manifest, locations):
    """Keep source links in handwritten prose current without rewriting its text."""
    pattern = r"\[([^\]]+)\]\([^)]+\)<!-- debug-symbol: ([\w.-]+) -->"

    def replace(match):
        label, key = match.groups()
        entry = manifest["symbols"][key]
        return (f"[{label}](../{entry['file']}#L{locations[key].executable})"
                f"<!-- debug-symbol: {key} -->")

    return re.sub(pattern, replace, document)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if generated guide differs; do not write")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
        if manifest.get("version") != 1:
            raise ValueError("Unsupported journey manifest version")
        locations = resolve_symbols(ROOT, manifest)
        old = (ROOT / GUIDE).read_text(encoding="utf-8")
        new = refresh_annotated_links(update_document(old, render(manifest, locations)), manifest, locations)
        if args.check and new != old:
            print("Generated debugging guide is stale. Run scripts/generate_debugging_guide.py.", file=sys.stderr)
            return 1
        if not args.check and new != old:
            (ROOT / GUIDE).write_text(new, encoding="utf-8", newline="\n")
        print(f"{'Checked' if args.check else 'Generated'} {len(manifest['journeys'])} journeys, "
              f"{len(locations)} named breakpoint locations in {GUIDE.as_posix()}")
        return 0
    except (OSError, ValueError, KeyError, SyntaxError) as exc:
        print(f"Debug guide generation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
