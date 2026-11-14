"""Render repository architecture SVGs without external tools.

Run normally to regenerate; --check reports stale SVGs or broken code anchors.
The JSON is the editable diagram source. Behavioral accuracy needs human review.
"""
import argparse
from html import escape
import json
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'docs' / 'diagrams'
SOURCE = DIRECTORY / 'card-chat-flows.json'
COLORS = {'browser': '#155e75', 'workflow': '#1d4ed8', 'agent': '#6d28d9',
          'claude': '#9a3412', 'mcp': '#047857', 'api': '#0f766e', 'storage': '#334155'}


def render(diagram):
    cards = []
    y = 170
    for number, step in enumerate(diagram['steps'], 1):
        lines = [line for paragraph in step['body'] for line in textwrap.wrap(paragraph, 94)]
        references = []
        for reference in step['references']:
            path, needle = reference['path'], reference['anchor']
            source_lines = (ROOT / path).read_text(encoding='utf-8').splitlines()
            matches = [index for index, line in enumerate(source_lines, 1) if needle in line]
            if not matches:
                raise ValueError(f'Missing code anchor: {path}: {needle}')
            references.append((path, matches[0]))
        height = 88 + len(lines) * 27 + len(references) * 25
        cards.append((number, step, lines, references, y, height))
        y += height + 48
    height = y + 65
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title description">',
             f'<title id="title">{escape(diagram["title"])}</title>',
             f'<desc id="description">{escape(diagram["subtitle"])}</desc>',
             '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0 0 L10 5 L0 10" fill="#64748b"/></marker></defs>',
             '<style>text{font-family:Consolas,"Liberation Mono",monospace}.heading{font-family:Arial,sans-serif;font-weight:700}</style>',
             f'<rect width="1200" height="{height}" fill="#f1f5f9"/>',
             f'<text x="60" y="65" class="heading" font-size="32" fill="#0f172a">{escape(diagram["title"])}</text>']
    for index, line in enumerate(textwrap.wrap(diagram['subtitle'], 94)):
        parts.append(f'<text x="60" y="{105 + index * 26}" font-size="18" fill="#475569">{escape(line)}</text>')
    for number, step, lines, references, top, card_height in cards:
        color = COLORS[step['owner']]
        if number > 1:
            parts.append(f'<path d="M600 {top - 43} V{top - 10}" stroke="#64748b" stroke-width="3" marker-end="url(#arrow)"/>')
        parts.extend([f'<rect x="60" y="{top}" width="1080" height="{card_height}" rx="12" fill="white" stroke="#cbd5e1"/>',
                      f'<rect x="60" y="{top}" width="9" height="{card_height}" rx="4" fill="{color}"/>',
                      f'<text x="88" y="{top + 35}" class="heading" font-size="23" fill="{color}">{number:02d} / {escape(step["title"])}</text>'])
        baseline = top + 69
        for line in lines:
            parts.append(f'<text x="88" y="{baseline}" font-size="18" fill="#1e293b">{escape(line)}</text>')
            baseline += 27
        baseline += 7
        for path, line in references:
            parts.append(f'<a href="../../{escape(path, quote=True)}"><text x="88" y="{baseline}" font-size="16" fill="#1d4ed8">{escape(path)}:{line}</text></a>')
            baseline += 25
    parts.append(f'<text x="60" y="{height - 28}" font-size="15" fill="#475569">Source: card-chat-flows.json | Regenerate: python scripts/render_chat_diagrams.py</text>')
    parts.append('</svg>')
    return '\n'.join(parts) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    diagrams = json.loads(SOURCE.read_text(encoding='utf-8'))['diagrams']
    stale = []
    for diagram in diagrams:
        path = DIRECTORY / diagram['file']
        svg = render(diagram)
        if args.check:
            if not path.exists() or path.read_text(encoding='utf-8') != svg:
                stale.append(path.name)
        else:
            path.write_text(svg, encoding='utf-8')
            print(f'Generated {path.relative_to(ROOT)}')
    if stale:
        parser.exit(1, 'Stale diagrams: ' + ', '.join(stale) + '\nRun python scripts/render_chat_diagrams.py\n')
    if args.check:
        print('Architecture SVGs are current; all referenced code anchors exist.')


if __name__ == '__main__':
    main()
