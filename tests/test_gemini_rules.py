from app.gemini import _validate_candidate


def test_funny_ragebait_requires_17_words_and_emoji():
    text = "AI just stole your job again, and somehow you are still defending it 😂"
    assert len(text.split()) == 13
    assert not _validate_candidate(text, "funny_ragebait")

    valid = "AI just stole your weekend, and somehow developers are defending it again 😂🔥 like nothing happened today"
    assert len(valid.split()) == 17
    assert _validate_candidate(valid, "funny_ragebait")


def test_question_requires_exactly_17_words_and_question_mark():
    valid = "Will AI coding assistants make developers better, or simply make average code arrive faster every single day?"
    assert len(valid.split()) == 17
    assert _validate_candidate(valid, "question")

    invalid = "Will AI coding assistants make developers better every day?"
    assert not _validate_candidate(invalid, "question")


def test_breaking_news_requires_exactly_17_words():
    valid = "AI developers are watching this trend explode across X tonight as attention shifts rapidly toward the technology"
    assert len(valid.split()) == 17
    assert _validate_candidate(valid, "breaking_news")



def test_gemini_model_fallbacks_are_supported():
    from app.gemini import MODELS

    assert MODELS == ("gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.1-flash-lite")
    assert all("2.5" not in model for model in MODELS)
