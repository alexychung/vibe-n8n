# Time-of-Day Greeting Webhook

A webhook that accepts a JSON body with a `name` (string) and `hour` (integer 0-23), and responds with a JSON greeting appropriate to the time of day:

- `hour` 0-11 → "Good morning, {name}"
- `hour` 12-17 → "Good afternoon, {name}"
- `hour` 18-23 → "Good evening, {name}"

## Validation

- If `name` is missing or empty: respond 400 with `{"error": "name is required"}`.
- If `hour` is missing, non-integer, or outside 0-23: respond 400 with `{"error": "hour must be an integer 0-23"}`.

## Non-functional

- Stakes: low (test workflow, no production data)
- Volume: <10 requests per day (manual testing only)
- Budget: no external API calls, no cost constraint
- Editors: developer only (single-operator project)
- Systems: none — pure synchronous webhook transform with no external dependencies
- Success: returns the correct greeting for every valid input, and a 400 with a clear error for every invalid input.
