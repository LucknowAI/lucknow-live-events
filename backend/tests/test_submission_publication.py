from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from workers.tasks import submissions


class SubmissionPublicationTests(IsolatedAsyncioTestCase):
    async def process_submission(self, published):
        db = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock(side_effect=lambda source: setattr(source, "id", "source-1"))
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=db)
        session.__aexit__ = AsyncMock(return_value=False)
        counts = {
            "events_found": published,
            "events_new": published,
            "events_published": published,
            "events_queued": 0,
        }
        with (
            patch.object(submissions, "SessionLocal", return_value=session),
            patch.object(
                submissions,
                "_ai_validate_event_url",
                new=AsyncMock(return_value={"ok": True}),
            ),
            patch(
                "ingestion.pipeline.run_source_pipeline",
                new=AsyncMock(return_value=counts),
            ) as pipeline,
        ):
            result = await submissions._async_process(
                "submission-1", "https://example.org/event", None, None
            )
        pipeline.assert_awaited_once_with("source-1")
        return result, db.add.call_args.args[0]

    async def test_published_events_keep_source_enabled(self):
        for published in (1, 2):
            with self.subTest(published=published):
                result, source = await self.process_submission(published)
                self.assertEqual(result["status"], "published")
                self.assertEqual(result["published"], published)
                self.assertTrue(source.enabled)
                self.assertEqual(source.trust_score, 0.70)

    async def test_no_published_events_disable_source(self):
        result, source = await self.process_submission(0)
        self.assertEqual(result["status"], "no_events")
        self.assertFalse(source.enabled)
        self.assertEqual(source.trust_score, 0.60)
