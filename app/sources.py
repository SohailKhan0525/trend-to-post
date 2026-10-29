"""Recent AI/technology conversations, not the X Trending Topics feed."""
import re
from .diversity import story_reason
from datetime import datetime, timedelta, timezone

QUERIES = ('(ChatGPT OR OpenAI)', '(Claude OR "Claude Code" OR Anthropic)',
           '(OpenCode OR "opencode ai")', '("AI agents" OR "coding assistant")',
           '("developer tools" OR "open source AI" OR NVIDIA)')
TOPIC = re.compile(r'\b(ai|artificial intelligence|chatgpt|openai|claude|anthropic|opencode|llm|nvidia|software|developer|coding|gpu|robotics)\b', re.I)
BLOCKED = re.compile(r'\b(giveaway|airdrop|follow back|follow for follow|election|war|politics)\b', re.I)


def candidate(tweet, now, used):
    ident = str(getattr(tweet, 'id', '') or '')
    text = str(getattr(tweet, 'text', '') or '')
    user = getattr(tweet, 'user', None)
    handle = str(getattr(user, 'screen_name', '') or '')
    created = getattr(tweet, 'created_at_datetime', None)
    if not ident.isdigit() or ident in used or not re.fullmatch(r'\w{1,15}', handle):
        return None
    if not isinstance(created, datetime) or created.tzinfo is None:
        return None
    if not timedelta(0) <= now-created <= timedelta(hours=24):
        return None
    if getattr(tweet, 'lang', '') != 'en' or not TOPIC.search(text) or BLOCKED.search(text):
        return None
    if any(getattr(tweet, key, None) for key in ('in_reply_to', 'retweeted_tweet', 'is_quote_status', 'possibly_sensitive')):
        return None
    likes = max(0, int(getattr(tweet, 'favorite_count', 0) or 0))
    replies = max(0, int(getattr(tweet, 'reply_count', 0) or 0))
    reposts = max(0, int(getattr(tweet, 'retweet_count', 0) or 0))
    # A recent post needs some observed conversation; popularity is not proof of accuracy.
    if likes+replies+reposts < 5:
        return None
    return {'id':ident, 'text':text[:6000], 'author':handle,
            'url':f'https://x.com/{handle}/status/{ident}', 'created_at':created.isoformat(),
            'score':(likes+2*reposts+3*replies)/(1+(now-created).total_seconds()/3600)}


async def discover(client, used, history, now):
    offset = (now.hour*60+now.minute)//144
    recent_authors = [p['author'].lower() for p in history[-3:]]
    found = {}
    for n in range(3):
        query = QUERIES[(offset+n)%len(QUERIES)]
        query += ' lang:en -filter:replies -filter:retweets since:'+(now-timedelta(days=1)).date().isoformat()
        # Stop on any service error; never work around rate limiting.
        results = await client.search_tweet(query, 'Top', count=20)
        for tweet in results or []:
            item = candidate(tweet, now, used)
            if item and item['author'].lower() not in recent_authors and not story_reason(item['text'], history, now):
                found[item['id']] = item
    return sorted(found.values(), key=lambda p:p['score'], reverse=True)
