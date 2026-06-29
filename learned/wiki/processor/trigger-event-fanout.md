# trigger_event fan-out

**Summary:** `trigger_event` is a high-volume processor op that re-broadcasts entity changes. Two distinct mechanisms produce `trigger_event` messages, and together they can amplify a bulk ingest into an indexing storm: an **interceptor re-seed** on every profile save (the dominant volume), and a **`write_back_sor` retry self-loop** (a bounded minority). Knowing both explains a recurring "[[../oncall/queue-backed-up|queue backed up]]" shape where `trigger_event` is the top *parent* of a flooded index queue.

## Mechanism 1 — interceptor re-seed (dominant)

Every profile-data / profile-applications **DB save** fires a post-save interceptor that publishes a fresh `trigger_event`:
- `post_save` at `www/interceptors/trigger_event_interceptor.py:44` →
- `www/data_propagation/publisher/trigger_event_publisher.py:31` `publish()` →
- `queue_utils.add_to_processing_queue(operations=['trigger_event'])`.

So during a bulk ATS ingest, each `batch_store_and_index` / `store` / `stage_advance` save broadcasts a `trigger_event`, which fans out downstream to the index queues. This path carries business `event_type`s like `application_update` / `candidate_update` / `profile_updated` (read from [[processor-event-log#the-data_json-payload|`data_json.$.event_type`]]) — **not** the `write_back` enum below. The interceptor lives under `www/interceptors/` (there is no `www/triggers/` dir, and the class is not under `ats`/`profile`/`processor` — a source grep by name misses it; the seeding path was recovered from a real message's `data_json._traceback`).

## Mechanism 2 — write_back_sor retry self-loop (bounded minority)

When a SOR write-back can't resolve an Eightfold-managed profile, it re-enqueues a `CANDIDATE_PROFILE_UPDATED` trigger_event with backoff, **capped at 6 retries**:
- backoff schedule `[2, 15, 60, 360, 1440, 2880]` minutes — `www/ats/write_back_sor.py:286` (`_get_back_off_time_for_update_candidate`; returns `-1` once `retry_count >= len(...)` → give up).
- `_replay_update_candidate_request` (`:291-304`) increments `update_spec[0]['retry_count']`, builds a `CANDIDATE_PROFILE_UPDATED` `TriggerEvent`, and publishes with `schedule_after_secs`.
- `update_candidate_via_sor` (`:324-334`): when `_get_ef_managed_profile(...)` is falsy → compute backoff; `-1` ⇒ `FAIL_IGNORE` (give up), else re-publish with delay and return `FAIL_IGNORE` ("retriggered with delay").

The per-chain cap of 6 holds in the data (no `event_context.update_spec[0].retry_count` > 6). High **per-profile** event counts (hundreds) come from **many overlapping short chains** repeatedly re-seeded, not one chain exceeding 6.

## Why the `schedule_after_secs` delay matters for attribution

Both mechanisms publish with a scheduled delay, so a retry/re-seed message's own `message_dispatched` row lands in a *later* bucket than the work it triggers. That is exactly why ranking the **parents** of a flooded queue must count `COUNT(DISTINCT processor_msg_id)` over **all** event types rather than filtering on `message_dispatched` — see [[../oncall/queue-backed-up#inflow-branch-what-flooded-the-queue|parent attribution]].

## Related

- [[../oncall/queue-backed-up|Queue backed up (oncall)]] — where this fan-out appears as the top parent op of a flooded index queue.
- [[processor-event-log#the-data_json-payload|processor_event_log — the data_json payload]] — the `_traceback` / inner `event_type` / `update_spec[0].retry_count` fields these mechanisms write.
- [[op-registry|op_registry]] — maps `trigger_event` / `batch_store_and_index` / `sync_ats` to their source files.

---
*Sources:* `www/interceptors/trigger_event_interceptor.py:44`, `www/data_propagation/publisher/trigger_event_publisher.py:31`, `www/ats/write_back_sor.py:286,291-304,324-334`. Witness: `inputs/2026-06-26-queue-backed-up-index-requests.md` (`[22:30]`, `[22:40]`, `[22:43]`, `[22:45]`).
