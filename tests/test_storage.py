from app.models import Draft, Trend
from app.storage import write_daily_markdown

def test_write_daily_markdown(tmp_path):
    p = write_daily_markdown(tmp_path, [Trend('OpenAI', 1)], [Draft('OpenAI', 'implication', 'A useful observation.', 'now')])
    assert p.exists()
    assert 'OpenAI' in p.read_text(encoding='utf-8')
