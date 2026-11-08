"""Read-only, bounded inspection of public source evidence and editorial decisions."""
import json

from . import queue, sources, writer
from .cloudflare import DraftError


async def diagnose():
    state = queue.load()
    queue.require_open(state)
    connection = queue.client()
    candidates = await sources.discover(connection, state, queue.utcnow())
    original_complete = writer.complete

    def traced_complete(system, data, **kwargs):
        result = original_complete(system, data, **kwargs)
        stage = "classify" if system == writer.SOURCE_GATE else "write" if system == writer.VOICE else "review"
        keys = {"subject", "event_key", "facts", "headline", "body", "text",
                *writer.GATE_FLAGS, *writer.REVIEW_FLAGS, *writer.REVIEW_EVIDENCE}
        selected = ({key: value for key, value in result.items() if key in keys}
                    if isinstance(result, dict) else {"result_type": type(result).__name__})
        # JSON encodes newlines/control characters; no headers, cookies, or environment are logged.
        print("Editorial diagnostic " + json.dumps({"stage": stage, "result": selected}, ensure_ascii=True)[:9000], flush=True)
        return result

    writer.complete = traced_complete
    try:
        for _, candidate in queue.candidate_batch(state, candidates):
            evidence = {key: candidate[key] for key in ("id", "author", "text", "views", "created_at")}
            print("Source diagnostic " + json.dumps(evidence, ensure_ascii=True)[:18000], flush=True)
            try:
                draft = writer.generate(candidate, state, queue.utcnow())
            except DraftError as exc:
                queue.summary("Diagnostic rejection: " + str(exc)[:600])
            else:
                queue.summary("Diagnostic approved draft\n\n" + draft["text"])
    finally:
        writer.complete = original_complete
    queue.summary("Read-only diagnostic finished; no post sent and no publication state changed.")
