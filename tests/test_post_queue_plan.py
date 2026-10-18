import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from app import post_queue
from app.gemini import OriginalDraft, QuoteDraft, _validate_original


class FakeClient:
    async def is_logged_in(self):
        return True


def _long_builder_post():
    prompt = (
        "Build an original AI research workspace for analysts who collect sources and turn verified notes into briefs. "
        "Use restrained neutrals, one purposeful accent, an 8-point spacing scale, deliberate typography, semantic color "
        "tokens, consistent borders, and clear hierarchy. Create a navigation rail, searchable source list, synthesis "
        "canvas, and evidence panel with reusable UI components and realistic data. Every control must work. Define "
        "responsive behavior for desktop, tablet, and mobile; collapse navigation, stack panels, preserve reading order, "
        "and prevent horizontal overflow. Implement loading, empty, error, success, disabled, hover, pressed, and "
        "keyboard-focus states. Meet accessibility requirements with semantic landmarks, labeled controls, keyboard "
        "navigation, visible focus, and sufficient text contrast. Handle missing evidence and slow AI responses. Review "
        "screenshots at 375px, 768px, and 1440px; fix spacing, typography, contrast, alignment, overflow, and dead "
        "interactions. Acceptance criteria: no placeholder copy, default purple gradient, random glass cards, fake "
        "buttons, or unresolved console errors."
    )
    return (
        "DID YOU KNOW?\nEvery research claim stays beside its source, so analysts can verify a brief without hunting through tabs.\n\nREPLY PROMPT:\n"
        + prompt
        + "\n\nFOLLOW FOR MORE PROMPTS."
    )


class PostQueuePlanTests(unittest.IsolatedAsyncioTestCase):
    def test_daily_plan_fills_all_45_slots_with_curated_repo_lane(self):
        from collections import Counter

        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.BUILDER_TIP_POSTS_PER_DAY, 19)
        self.assertEqual(post_queue.GITHUB_REPO_POSTS_PER_DAY, 4)
        self.assertEqual(post_queue.PERSON_PHOTO_POSTS_PER_DAY, 22)
        self.assertEqual(post_queue.AI_TECH_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(
            post_queue.BUILDER_TIP_POSTS_PER_DAY
            + post_queue.GITHUB_REPO_POSTS_PER_DAY
            + post_queue.PERSON_PHOTO_POSTS_PER_DAY,
            post_queue.POSTS_PER_DAY,
        )
        state = post_queue._new_state()
        published_lanes = []
        while state["daily_count"] < post_queue.POSTS_PER_DAY:
            lane = post_queue._select_next_lane(state)
            self.assertIsNotNone(lane)
            lane_name, count_key, limit, _publisher = lane
            self.assertLess(state[count_key], limit)
            state[count_key] += 1
            state["daily_count"] += 1
            published_lanes.append(lane_name)
        self.assertEqual(Counter(published_lanes), {
            "builder_tip": 19,
            "github_repo_calendar": 4,
            "comparison_meme": 22,
        })
        self.assertEqual(post_queue.POSTS_PER_DAY, 45)

    def test_curated_github_calendar_contains_100_unique_under_limit_posts(self):
        repositories = post_queue._load_github_repo_calendar()
        self.assertEqual(len(repositories), 100)
        self.assertEqual(len({item["repo"].casefold() for item in repositories}), 100)
        self.assertTrue(all(len(item["x_post"]) <= 280 for item in repositories))
        self.assertTrue(all(item["url"] in item["x_post"] for item in repositories))

    def test_mention_posts_are_limited_to_two_in_rolling_48_hours(self):
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        state = {"mention_post_timestamps": [
            {"timestamp": (now - timedelta(hours=49)).isoformat(), "handle": "@old"},
            {"timestamp": (now - timedelta(hours=24)).isoformat(), "handle": "@one"},
            {"timestamp": (now - timedelta(hours=1)).isoformat(), "handle": "@two"},
        ]}
        active = post_queue._active_mention_posts(state, now)
        self.assertEqual([item["handle"] for item in active], ["@one", "@two"])
        self.assertEqual(len(active), 2)

    def test_builder_prompt_validation_rejects_generic_did_you_know_hook(self):
        from app.gemini import GeminiError
        bad = _long_builder_post().replace(
            "Every research claim stays beside its source, so analysts can verify a brief without hunting through tabs.",
            "This prompt helps you build a polished, responsive AI research workspace.",
        )
        draft = OriginalDraft(
            True, bad, "premium builder prompt", "", "builder prompt",
            "AI research workspace", "responsive accessibility visual QA",
            "hook_prompt_marker_long_implementation", "builder_tip",
        )
        with self.assertRaisesRegex(GeminiError, "generic prompt-advertising"):
            _validate_original(
                draft, {}, include_handle=False, minimum_emoji_count=0,
                maximum_emoji_count=0, content_mode="builder_tip",
            )

    def test_builder_prompt_validation_accepts_long_accessible_ai_ui_prompt(self):
        previous = {
            "format_name": "premium builder prompt",
            "structure_signature": "hook_prompt_marker_long_implementation",
            "post_text": "A previously used brief with a different UI scenario.",
            "content_type": "builder_tip",
        }
        draft = OriginalDraft(
            True,
            _long_builder_post(),
            "premium builder prompt",
            "",
            "builder prompt",
            "AI research workspace",
            "responsive accessibility visual QA",
            "hook_prompt_marker_long_implementation",
            "builder_tip",
        )
        validated = _validate_original(
            draft,
            {},
            include_handle=False,
            minimum_emoji_count=0,
            maximum_emoji_count=0,
            format_memory=[previous],
            content_mode="builder_tip",
        )
        self.assertIs(validated, draft)


    def test_content_deduplication_catches_exact_and_near_duplicates(self):
        original = (
            "Build an AI dashboard with a responsive component layout, accessible keyboard focus, "
            "loading and error states, a clear typography hierarchy, and screenshot-based visual QA."
        )
        fingerprint = post_queue._content_fingerprint(original)
        self.assertTrue(post_queue._is_duplicate_content(original, [fingerprint], []))
        self.assertEqual(
            post_queue._comparison_pair_fingerprint("Grok Bot or ChatGPT Dots?"),
            post_queue._comparison_pair_fingerprint("ChatGPT Dots vs Grok Bot?"),
        )
        self.assertTrue(
            post_queue._is_duplicate_content(
                original,
                [],
                [original.replace("Build an AI dashboard", "Build an AI workspace")],
            )
        )
        self.assertFalse(
            post_queue._is_duplicate_content(
                "Design a music discovery app with playlist controls and audio waveform previews.",
                [],
                [original],
            )
        )
    def test_generation_budget_allows_retries_beyond_daily_post_cap(self):
        self.assertGreaterEqual(post_queue.AI_GENERATIONS_PER_DAY, post_queue.POSTS_PER_DAY + 30)

    def test_post_interval_supports_daily_target_with_30_minute_schedule(self):
        self.assertLessEqual(post_queue.MIN_POST_INTERVAL_MINUTES, 30)
        self.assertEqual(post_queue.MIN_POST_INTERVAL_MINUTES, 20)

    def test_workflow_has_a_scheduled_fallback(self):
        from pathlib import Path

        workflow = Path(".github/workflows/post-queue.yml").read_text(encoding="utf-8")
        self.assertIn('schedule:', workflow)
        self.assertIn('cron: "*/30 * * * *"', workflow)
        self.assertIn('Report daily queue progress', workflow)
        self.assertIn('Fail visibly when no post was published', workflow)
        self.assertIn("steps.post_bot.outputs.skip_reason != 'interval_guard'", workflow)
        self.assertIn("steps.post_bot.outputs.skip_reason != 'no_eligible_source'", workflow)
        self.assertIn("POST_SKIP_REASON", workflow)
        self.assertIn("DID YOU KNOW builder prompts", workflow)
        self.assertIn("Curated GitHub repository posts", workflow)
        self.assertIn("data/github_repos_x_calendar_100_no_day_numbers.json", post_queue.GITHUB_REPO_CALENDAR_PATH.as_posix())
        self.assertIn("Comparison memes", workflow)
        self.assertIn("text-only comparison meme", workflow.lower())
        self.assertIn("Two additional fixed community posts", workflow)
        self.assertIn("X Trends quote posts (retired)", workflow)
        self.assertIn('test -n "$X_AUTH_TOKEN"', workflow)
        self.assertIn('test -n "$X_CT0"', workflow)
        self.assertNotIn('test -n "$CLOUDFLARE_ACCOUNT_ID"', workflow)
        self.assertNotIn("generate_comparison_image", post_queue._post_comparison_meme.__code__.co_names)
        community_workflow = Path(".github/workflows/daily-community-posts.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "30 12 * * *"', community_workflow)
        self.assertIn('cron: "30 18 * * *"', community_workflow)
        self.assertIn("community-connect", community_workflow)
        self.assertIn("community-building", community_workflow)

    async def test_person_photo_lane_only_uses_a_matching_active_x_profile(self):
        person = ("Thibault Sottiaux", "Tibo", "Codex / AI engineering", "thsottiaux", ("Tibo",))

        class PersonSearchClient:
            async def search_tweet(self, query, search_type, count=5):
                self.query = query
                user = SimpleNamespace(screen_name="thsottiaux", name="Tibo")
                return [
                    SimpleNamespace(
                        user=user,
                        in_reply_to=None,
                        retweeted_tweet=None,
                        text="Codex is shipping.",
                    )
                ]

        with patch.object(post_queue, "PERSON_SUBJECTS", (person,)):
            result = await post_queue._find_active_x_person_handles(PersonSearchClient(), [])

        self.assertEqual(result, ["thsottiaux"])

    async def test_person_photo_lane_rejects_handle_with_wrong_profile_name(self):
        person = ("Thibault Sottiaux", "Tibo", "Codex / AI engineering", "thsottiaux", ("Tibo",))

        class WrongProfileClient:
            async def search_tweet(self, query, search_type, count=5):
                user = SimpleNamespace(screen_name="thsottiaux", name="Someone Else")
                return [
                    SimpleNamespace(
                        user=user,
                        in_reply_to=None,
                        retweeted_tweet=None,
                        text="Unrelated account content.",
                    )
                ]

        with patch.object(post_queue, "PERSON_SUBJECTS", (person,)):
            result = await post_queue._find_active_x_person_handles(WrongProfileClient(), [])

        self.assertEqual(result, [])

    async def test_x_trends_quote_lane_is_retired(self):
        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.BUILDER_TIP_POSTS_PER_DAY, 23)

    async def test_builder_tip_publishes_did_you_know_parent_and_prompt_reply_thread(self):
        state = post_queue._new_state()
        draft = OriginalDraft(
            True,
            _long_builder_post(),
            "premium builder prompt",
            "",
            "builder prompt",
            "AI research workspace",
            "responsive accessibility visual QA",
            "hook_prompt_marker_long_implementation",
            "builder_tip",
        )
        ids = iter(SimpleNamespace(id=f"tweet-{index}") for index in range(20))
        client = SimpleNamespace(create_tweet=AsyncMock(side_effect=lambda **kwargs: next(ids)))
        with (
            patch.object(post_queue, "generate_original_text", return_value=draft),
            patch.object(post_queue, "_save_state"),
        ):
            result = await post_queue._post_builder_tip(state, client)

        self.assertTrue(result)
        self.assertGreater(client.create_tweet.await_count, 1)
        calls = client.create_tweet.await_args_list
        self.assertTrue(calls[0].kwargs["text"].startswith("DID YOU KNOW?"))
        self.assertLessEqual(len(calls[0].kwargs["text"]), 220)
        self.assertNotIn("Build an original AI research workspace", calls[0].kwargs["text"])
        self.assertTrue(all(call.kwargs.get("reply_to") for call in calls[1:]))
        self.assertTrue(calls[-1].kwargs["text"].endswith("FOLLOW FOR MORE PROMPTS."))
        self.assertEqual(state["builder_tip_post_count"], 1)
        self.assertEqual(state["daily_count"], 1)
        self.assertEqual(state["last_post_type"], "builder_tip")
        self.assertEqual(state["builder_tip_posts"][-1]["reply_status"], "published")
        self.assertEqual(state["builder_tip_posts"][-1]["thread_tweet_ids"][0], "tweet-0")

    async def test_curated_github_repo_lane_uses_json_post_and_self_reply(self):
        state = post_queue._new_state()
        state["day_key"] = post_queue._today()
        state["daily_count"] = 0
        repo = {
            "repo": "example/awesome-dev-tool",
            "url": "https://github.com/example/awesome-dev-tool",
            "x_post": (
                "A specific hook for developers.\n\n"
                "awesome-dev-tool (~1.0k★): A toolkit. "
                "https://github.com/example/awesome-dev-tool\n\n"
                "What would you build with it?"
            ),
            "self_reply": "Try one small task first and inspect the changes before trusting it.",
        }
        metadata = {
            "full_name": "example/awesome-dev-tool",
            "html_url": "https://github.com/example/awesome-dev-tool",
            "stargazers_count": 12345,
            "archived": False,
            "disabled": False,
        }
        ids = iter((SimpleNamespace(id="repo-main-id"), SimpleNamespace(id="repo-reply-id")))
        client = SimpleNamespace(create_tweet=AsyncMock(side_effect=lambda **kwargs: next(ids)))
        with (
            patch.object(post_queue, "_load_github_repo_calendar", return_value=[repo]),
            patch.object(post_queue, "_get_live_github_repo_metadata", return_value=metadata),
            patch.object(post_queue, "_save_state"),
        ):
            result = await post_queue._post_curated_github_repo(state, client)

        self.assertTrue(result)
        main_call, reply_call = client.create_tweet.await_args_list
        self.assertIn("~12.3k★", main_call.kwargs["text"])
        self.assertIn("https://github.com/example/awesome-dev-tool", main_call.kwargs["text"])
        self.assertEqual(reply_call.kwargs["text"], repo["self_reply"])
        self.assertEqual(reply_call.kwargs["reply_to"], "repo-main-id")
        self.assertEqual(state["github_repo_post_count"], 1)
        self.assertEqual(state["builder_tip_post_count"], 0)
        self.assertEqual(state["daily_count"], 1)
        self.assertEqual(state["github_repo_next_index"], 0)
        self.assertEqual(state["last_post_type"], "github_repo_calendar")
        self.assertEqual(state["github_repo_posts"][-1]["reply_status"], "published")

    async def test_post_queue_enables_all_three_content_lanes(self):
        self.assertEqual(post_queue.PERSON_PHOTO_POSTS_PER_DAY, 22)
        self.assertEqual(post_queue.BUILDER_TIP_POSTS_PER_DAY, 19)
        self.assertEqual(post_queue.GITHUB_REPO_POSTS_PER_DAY, 4)
        self.assertEqual(post_queue.AI_TECH_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.TREND_QUOTE_POSTS_PER_DAY, 0)
        self.assertEqual(post_queue.POSTS_PER_DAY, 45)

    async def test_queue_stops_when_forty_five_main_posts_are_complete(self):
        state = post_queue._new_state()
        state["daily_count"] = post_queue.POSTS_PER_DAY
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(post_queue, "_load_state", return_value=state),
            patch.object(post_queue, "_client") as client,
        ):
            result = await post_queue.post_next("auth", "ct0")
        self.assertFalse(result)
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
