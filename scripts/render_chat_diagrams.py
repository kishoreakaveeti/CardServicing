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
    participants = diagram['participants']
    width = max(1400, len(participants) * 260 + 140)
    gap = (width - 260) / (len(participants) - 1)
    positions = {p['id']: 130 + index * gap for index, p in enumerate(participants)}
    rows, y = [], 245
    for number, message in enumerate(diagram['messages'], 1):
        kind = message['kind']
        if kind in ('note', 'section'):
            lines = textwrap.wrap(message['label'], (width - 160) // 10)
            row_height = 36 + len(lines) * 25
        else:
            x1, x2 = positions[message['from']], positions[message['to']]
            available = abs(x2 - x1) - 35 if x1 != x2 else gap - 65
            lines = textwrap.wrap(message['label'], max(17, int(available // 9)))
            row_height = 47 + len(lines) * 24 + (25 if x1 == x2 else 0)
        rows.append((number, message, lines, y, row_height))
        y += row_height
    bottom = y + 20
    references = []
    for step in diagram['steps']:
        for reference in step['references']:
            path, needle = reference['path'], reference['anchor']
            matches = [i for i, line in enumerate((ROOT / path).read_text(encoding='utf-8').splitlines(), 1) if needle in line]
            if not matches:
                raise ValueError(f'Missing code anchor: {path}: {needle}')
            item = (path, matches[0])
            if item not in references:
                references.append(item)
    height = bottom + 135 + len(references) * 25
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
             f'<title id="title">{escape(diagram["title"])} - sequence diagram</title>',
             f'<desc id="description">{escape(diagram["subtitle"])} Read interactions from top to bottom. Dashed arrows are replies.</desc>',
             '<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0 0 L9 4.5 L0 9" fill="#334155"/></marker></defs>',
             '<style>text{font-family:Consolas,"Liberation Mono",monospace}.heading{font-family:Arial,sans-serif;font-weight:700}</style>',
             f'<rect width="{width}" height="{height}" fill="#fff"/>',
             f'<text x="40" y="55" class="heading" font-size="32" fill="#0f172a">{escape(diagram["title"])}</text>',
             f'<text x="40" y="90" font-size="18" fill="#475569">{escape(diagram["subtitle"])}</text>',
             '<text x="40" y="120" font-size="16" fill="#475569">Time flows downward | Solid arrow: call | Dashed arrow: reply | Yellow: explanation</text>']
    for participant in participants:
        x = positions[participant['id']]
        color = COLORS[participant['id']]
        parts.extend([f'<path d="M{x} 207 V{bottom}" stroke="#94a3b8" stroke-width="2" stroke-dasharray="6 6"/>',
                      f'<rect x="{x-111}" y="155" width="222" height="52" rx="7" fill="{color}"/>',
                      f'<text x="{x}" y="187" text-anchor="middle" class="heading" font-size="19" fill="white">{escape(participant["label"])}</text>'])
    for number, message, lines, top, row_height in rows:
        kind = message['kind']
        if kind in ('note', 'section'):
            fill = '#fef3c7' if kind == 'note' else '#e0e7ff'
            parts.append(f'<rect x="35" y="{top}" width="{width-70}" height="{row_height-12}" rx="5" fill="{fill}" stroke="#cbd5e1"/>')
            for index, line in enumerate(lines):
                parts.append(f'<text x="55" y="{top+25+index*25}" font-size="17" fill="#1e293b">{escape(line)}</text>')
            continue
        x1, x2 = positions[message['from']], positions[message['to']]
        arrow_y = top + len(lines) * 24 + 9
        if x1 == x2:
            direction = -1 if x1 > width - gap else 1
            text_x = x1 + direction * (gap - 40) / 2
            loop_x = x1 + direction * 65
            path = f'M{x1} {arrow_y} H{loop_x} V{arrow_y+22} H{x1}'
        else:
            text_x = (x1+x2)/2
            path = f'M{x1} {arrow_y} H{x2}'
        label_width = max(len(line) for line in lines) * 9.5 + 16
        parts.append(f'<rect x="{text_x-label_width/2}" y="{top-5}" width="{label_width}" height="{len(lines)*24+7}" fill="white"/>')
        for index, line in enumerate(lines):
            parts.append(f'<text x="{text_x}" y="{top+15+index*24}" text-anchor="middle" font-size="16" fill="#0f172a">{escape(line)}</text>')
        dash = ' stroke-dasharray="7 5"' if kind == 'return' else ''
        parts.append(f'<path d="{path}" fill="none" stroke="#334155" stroke-width="2"{dash} marker-end="url(#arrow)"/>')
    baseline = bottom + 42
    parts.append(f'<text x="40" y="{baseline}" class="heading" font-size="20" fill="#0f172a">Traceable code references</text>')
    for path, line in references:
        baseline += 25
        parts.append(f'<a href="../../{escape(path, quote=True)}"><text x="40" y="{baseline}" font-size="16" fill="#1d4ed8">{escape(path)}:{line}</text></a>')
    parts.append(f'<text x="40" y="{height-25}" font-size="15" fill="#475569">Source: card-chat-flows.json | Regenerate: python scripts/render_chat_diagrams.py</text>')
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
