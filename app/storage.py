from datetime import datetime
from pathlib import Path
from .models import Draft, Trend

def write_daily_markdown(root: Path, trends: list[Trend], drafts: list[Draft]):
    day = datetime.now().strftime('%Y-%m-%d')
    directory = root / 'posts'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{day}.md'
    lines = ['# X Trend Content — ' + day, '', '## X Trends collected', '']
    lines += [f'{t.rank}. {t.name}' for t in trends]
    lines += ['', '## Drafts', '']
    for i, d in enumerate(drafts, 1):
        lines += [f'### Draft {i}', '', f'**Trend:** {d.trend}', '', f'**Angle:** {d.angle}', '', d.text, '']
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path
