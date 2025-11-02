# Daily Aggregator

A minimal Django + DRF + PostgreSQL service that ingests learning logs (word counts & study time) and returns period summaries at hourly/daily/monthly granularities. Designed to be **idempotent**, **parallel-friendly**, and **time-zone aware**.

## Quick Start

```bash
# 0) clone
git clone https://github.com/RekrapF/langaku-aggregator.git
cd langaku-aggregator

# 1) environment
cp .env.example .env
chmod +x entrypoint.sh

# 2) up
docker compose up --build

# 3) open
# API base: http://localhost:8000/api/
curl -X POST http://localhost:8000/api/records \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 11111111-1111-1111-1111-111111111113' \
  -d '{"user_id":"u3","word_count":20,"start_at":"2025-10-29T11:00:00Z","end_at":"2025-10-29T11:30:00Z"}'

curl "http://localhost:8000/api/users/u2/summary?from=2025-10-27T00:00:00Z&to=2025-11-02T00:00:00Z&granularity=hour&tz=Asia/Tokyo&include_empty=true"

```

---

## Overview

- **Ingestion**: `POST /api/records` stores a single learning session for a user.  
- **Aggregation**: `GET /api/users/{user_id}/summary` returns **totals** and **averages per bucket** for any period at `hour | day | month` granularity, in a specified time zone.
- **Time semantics**:
  - `word_count` is assigned to the **local bucket of `end_at`**.
  - `study_minutes` is counted **only if `start_at` and `end_at` fall on the same local day**; otherwise minutes = `0`.
  - For readability, when **word_count < 1** or **study_minutes < 1 minute**, the response shows
    - `"word count less than 1"`
    - `"study minutes less than a minute"`
- **Idempotency**: `user_id + idempotency_key` is unique; replay with identical payload returns `200`; different payload returns `409`.

---

## Endpoints

### 1) Create a learning record

`POST /api/records`

#### Request JSON
```json
{
  "user_id": "u-123",
  "idempotency_key": "uuid-or-client-key-1",
  "word_count": 120,
  "start_at": "2025-10-27T10:00:00Z",
  "end_at":   "2025-10-27T10:45:00Z"
}
```

- `idempotency_key` can also be provided via `Idempotency-Key` header.
- `start_at`/`end_at` optional; if both omitted, `end_at` defaults to server time (UTC).
- If both present, must satisfy `start_at <= end_at`.

#### Responses
- `201 Created` on first insert; `200 OK` on idempotent replay with same payload.
- `409 Conflict` when `Idempotency-Key` is reused **with different payload**.
- `400 Bad Request` on validation errors.

Example `201` body:
```json
{
  "id": 42,
  "user_id": "u-123",
  "idempotency_key": "uuid-or-client-key-1",
  "word_count": 120,
  "start_at": "2025-10-27T10:00:00Z",
  "end_at": "2025-10-27T10:45:00Z",
  "created_at": "2025-10-27T10:45:01Z",
  "study_minutes": 45
}
```

> Note: `study_minutes` here is a **plain echo** of `(end_at - start_at) // 60` if same day; the cross-day “minutes=0” rule only affects **aggregation** responses.

---

### 2) Summarize by period

`GET /api/users/{user_id}/summary`

#### Query params

| name           | type     | required | example                         | notes                                                                                          |
|----------------|----------|----------|----------------------------------|-------------------------------------------------------------------------------------------------|
| `from`         | ISO-8601 | yes      | `2025-10-27T00:00:00Z`          | inclusive; UTC ISO                                                                              |
| `to`           | ISO-8601 | yes      | `2025-10-29T00:00:00Z`          | exclusive; UTC ISO                                                                              |
| `granularity`  | string   | yes      | `hour` \\| `day` \\| `month`    | bucket step in target time zone                                                                 |
| `tz`           | string   | no       | `Asia/Tokyo`                    | IANA tz; default `UTC`                                                                          |
| `include_empty`| bool     | no       | `true`                          | controls **averages’ denominator**; see below                                                   |

#### Response (no `buckets` field; only totals and averages per bucket)
```json
{
  "user_id": "u-123",
  "from": "2025-10-27T00:00:00Z",
  "to": "2025-10-29T00:00:00Z",
  "granularity": "day",
  "tz": "Asia/Tokyo",
  "include_empty": true,
  "totals": {
    "word_count": 220,
    "study_minutes": 90
  },
  "averages_per_bucket": {
    "word_count": 110.0,
    "study_minutes": 45.0
  }
}
```

- **Totals** = sum across buckets.
- **Averages per bucket**:
  - if `include_empty=true`: denominator = **all buckets** in window.
  - if `include_empty=false`: denominator = **active buckets only** (where `word_count>0` or `study_minutes>0`).
- **Small values**: any `<1` value (words or minutes) in **totals or averages** is replaced with the strings above.

---

## Data Model (API & Modeling)

Django model: `LearningRecord`

```python
user_id        # unique user id (indexed)
idempotency_key# unique per learning record (unique together with user_id)
word_count     # words learned in the session (>= 0)
start_at       # optional session start (tz-aware UTC), indexed
end_at         # optional session end (tz-aware UTC), indexed
created_at     # server-created timestamp (UTC)
```

- DB constraints:
  - Unique index on `(user_id, idempotency_key)`
  - B-tree indexes on `user_id`, `start_at`, `end_at`

---

## Time Semantics & Bucketing

- **Time zone**: all storage is UTC; **aggregation** is computed in the client-specified time zone (`tz`).
- **Bucketing**:
  - `hour`: floor to `HH:00:00` in local tz
  - `day`: floor to `00:00:00` local date
  - `month`: floor to `YYYY-MM-01 00:00:00` local date
- **Bucket assignment**:
  - A record contributes its **`word_count`** to the **bucket of `end_at` (local time)**.
  - **Study minutes** contribute to that bucket **only if** `start_at` and `end_at` are on the **same local day**; otherwise **0** (cross-day simplification).

---

## Aggregation Logic

Implemented in `logs/services.py` (pure Python + ORM):

1. Parse input window `[from, to)` to UTC and compute local bucket starts.
2. Query `LearningRecord` with `user_id` and `end_at ∈ [from,to)`.
3. For each record:
   - Find the target bucket by `end_at` in local tz.
   - `wc_sum[bucket] += word_count`
   - If same local day and both endpoints provided:
     - `mins_sum[bucket] += floor((end_at - start_at).seconds/60)`
   - Else `mins_sum[bucket] += 0`
4. Compute:
   - `totals = sum(wc_sum), sum(mins_sum)`
   - `averages_per_bucket` using denominator per `include_empty`
5. Apply **small value replacements** for totals & averages.

> We previously had a SQL-heavy version and a SMA window option. Current version is **simpler and safer** (no SMA fields; fewer DB-dialect concerns), while still efficient with indexes.

---

## Performance & Concurrency

- **Indexes**:
  - `user_id` (filter)
  - `end_at` (range scan on window)
  - `(user_id, idempotency_key)` unique (idempotency)
- **Concurrent writes**:
  - `get_or_create` wrapped in `transaction.atomic()` ensures **single-row upsert** semantics.
  - Duplicate `idempotency_key` with differing payload returns `409` immediately.
- **Parallel reads/writes**:
  - Reads filter by immutable `end_at` and `user_id`—no locking scans.
  - Aggregation uses **read-committed** scans; no table-level locks.
- **Latency**:
  - Windowed scans + Python aggregation; with proper indexes, costs scale with **records within the window**.
  - If needed, replace Python loop with SQL `GROUP BY` on generated buckets or materialized views (see “Trade-offs”).

---

## Validation & Error Handling

- `400 Bad Request`:
  - `word_count` missing/negative
  - `start_at > end_at`
  - `from/to` not ISO-8601 or `from >= to`
  - invalid `granularity` / `tz`
- `409 Conflict`:
  - `(user_id, idempotency_key)` exists **but payload differs**
- `200/201`:
  - 201 on first successful create; 200 on idempotent replay

Small value replacement (totals/averages):
- `word_count < 1` → `"word count less than 1"`
- `study_minutes < 1` → `"study minutes less than a minute"`

---

## Testing

We provide `pytest` cases under `logs/tests/test_api.py`, covering:

- Idempotent create & conflict on different payload
- Same-day duration vs. cross-day minute-zero rule
- `include_empty=true|false` denominator effect
- Small value replacement
- Hour/day/month windows, scattered months and random daily submissions

Run tests:

```bash
docker compose exec web pytest -q --reuse-db
```

---

## Design Trade-offs

- **Python aggregation vs. SQL windowing**  
  + Simpler to maintain; less DB vendor coupling; easier to enforce cross-day minute rules.  
  − For very large windows, pure SQL `GROUP BY` with generated series can be faster.
- **Assign `word_count` by `end_at`**  
  + Stable and deterministic; avoids splitting counts across buckets.  
  − If sessions are long, middle hours/days receive no allocation (acceptable per MVP rules).
- **Cross-day minutes = 0**  
  + Business simplification (as requested), avoids ambiguous allocation across days.  
  − Under-counts true duration for overnight sessions.

---

## Accuracy Improvements

1. **Optional Duration Splitting Across Day Boundaries**  
   When `start_at`/`end_at` cross local midnight, proportionally allocate minutes to each touched day/hour bucket instead of zeroing; keep `word_count` pinned to `end_at` to preserve current semantics.

2. **Outlier & Overlap Guardrails**  
   - Cap a single session’s duration to a sane ceiling (e.g., 12h) to prevent stale timers inflating minutes.  
   - If multiple sessions overlap, either allow overlap or introduce a merge policy (dedupe by close timestamps per user).

3. **Clock Skew Correction & Server-Side Timestamps**  
   - If client timestamps are missing or skewed, allow server to set `end_at` and optional `start_at = end_at - reported_duration` with guardrails.  
   - Persist client offset estimates per user and auto-correct.

---

## OpenAPI (YAML)

```yaml
openapi: 3.0.3
info:
  title: Daily Aggregator API
  version: 1.0.0
servers:
  - url: http://localhost:8000
paths:
  /api/records:
    post:
      summary: Create a learning record (idempotent)
      operationId: createRecord
      parameters:
        - in: header
          name: Idempotency-Key
          schema: { type: string, maxLength: 64 }
          required: false
          description: Optional; same effect as body.idempotency_key.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/LearningRecordCreate'
      responses:
        '201':
          description: Created
          content:
            application/json:
              schema: { $ref: '#/components/schemas/LearningRecord' }
        '200':
          description: Replayed idempotent request (same payload)
          content:
            application/json:
              schema: { $ref: '#/components/schemas/LearningRecord' }
        '400':
          description: Validation error
        '409':
          description: Idempotency key reused with different payload
  /api/users/{user_id}/summary:
    get:
      summary: Summarize totals and averages by bucket
      operationId: getUserSummary
      parameters:
        - in: path
          name: user_id
          required: true
          schema: { type: string, maxLength: 64 }
        - in: query
          name: from
          required: true
          schema: { type: string, format: date-time }
          description: Inclusive UTC ISO-8601
        - in: query
          name: to
          required: true
          schema: { type: string, format: date-time }
          description: Exclusive UTC ISO-8601
        - in: query
          name: granularity
          required: true
          schema:
            type: string
            enum: [hour, day, month]
        - in: query
          name: tz
          required: false
          schema: { type: string, default: UTC }
          description: IANA time zone, e.g., Asia/Tokyo
        - in: query
          name: include_empty
          required: false
          schema: { type: boolean, default: true }
          description: If false, averages divide by active buckets only.
      responses:
        '200':
          description: Summary
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/SummaryResponse'
        '400':
          description: Validation error
components:
  schemas:
    LearningRecordCreate:
      type: object
      required: [user_id, word_count]
      properties:
        user_id:
          type: string
          maxLength: 64
        idempotency_key:
          type: string
          maxLength: 64
          description: Optional here; can be provided via header.
        word_count:
          type: integer
          minimum: 0
        start_at:
          type: string
          format: date-time
          nullable: true
        end_at:
          type: string
          format: date-time
          nullable: true
    LearningRecord:
      allOf:
        - $ref: '#/components/schemas/LearningRecordCreate'
        - type: object
          required: [id, created_at]
          properties:
            id:
              type: integer
            created_at:
              type: string
              format: date-time
            study_minutes:
              type: integer
              description: Derived from endpoints; echo only.
    SummaryResponse:
      type: object
      required:
        [user_id, from, to, granularity, tz, include_empty, totals, averages_per_bucket]
      properties:
        user_id:
          type: string
        from:
          type: string
          format: date-time
        to:
          type: string
          format: date-time
        granularity:
          type: string
          enum: [hour, day, month]
        tz:
          type: string
        include_empty:
          type: boolean
        totals:
          type: object
          required: [word_count, study_minutes]
          properties:
            word_count:
              oneOf:
                - type: number
                - type: string
                  enum: ["word count less than 1"]
            study_minutes:
              oneOf:
                - type: number
                - type: string
                  enum: ["study minutes less than a minute"]
        averages_per_bucket:
          type: object
          required: [word_count, study_minutes]
          properties:
            word_count:
              oneOf:
                - type: number
                - type: string
                  enum: ["word count less than 1"]
            study_minutes:
              oneOf:
                - type: number
                - type: string
                  enum: ["study minutes less than a minute"]
```
---

## Notes for Contributors

- Ensure shell scripts are LF line endings (`.gitattributes: *.sh text eol=lf`).
- Keep `(user_id, idempotency_key)` unique constraint intact.
- Extend tests when altering aggregation semantics (e.g., if you implement duration splitting across days/hours).
