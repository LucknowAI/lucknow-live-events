# Luma fixtures

Captured from the public `https://api.lu.ma/event/get?event_api_id=...` endpoint on 2026-11-06:

- `tech.json`: [Data Engineers Meetup](https://luma.com/ne5hyjwj), `evt-0zi7zx9aezlPUDF`, category `Tech`.
- `non_tech.json`: [Spark run](https://luma.com/x98vtg5q), `evt-9vZagGnMk29gCBe`, categories `Fitness` and `Running`.

Responses retain only adapter fields and the first description paragraph; guest profiles and unused metadata are omitted. Tests build synthetic pagination envelopes around these real event records. `allowed_hosts` contains stable host `api_id` values, not display names.
