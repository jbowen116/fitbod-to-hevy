# Hevy API: observed behaviour vs. documentation

Observed on 2026-10-03 against `https://api.hevyapp.com/v1`, compared with the
OpenAPI document served at <https://api.hevyapp.com/docs/>. Each item was seen
in real responses while importing 497 workouts and creating 15 custom exercises.

## 1. Exercise template `type` values are not the documented ones

`GET /v1/exercise_templates` returned these `type` values across the 451 stock
templates:

| type | templates |
|---|---|
| `weight_reps` | 287 |
| `reps_only` | 104 |
| `duration` | 24 |
| `distance_duration` | 14 |
| `bodyweight_weighted` | 10 |
| `bodyweight_assisted` | 5 |
| `short_distance_weight` | 5 |
| `steps_duration` | 1 |
| `floors_duration` | 1 |

The only enumeration in the docs is `CustomExerciseType`, used for creating
exercises: `weight_reps`, `reps_only`, `bodyweight_reps`,
`bodyweight_assisted_reps`, `duration`, `weight_duration`, `distance_duration`,
`short_distance_weight`.

So `bodyweight_weighted`, `bodyweight_assisted`, `steps_duration` and
`floors_duration` are returned but undocumented, and the names used when
creating (`bodyweight_reps`, `bodyweight_assisted_reps`) differ from the names
returned when reading. `ExerciseTemplate.type` is documented only as a string
with the example `weight_reps`.

Suggested fix: document the full set of values `ExerciseTemplate.type` can
return, and say how the create-time names map to them.

## 2. `POST /v1/exercise_templates` returns a UUID string, documented as integer

The documented 200 response is `{"id": <integer>}` with the example `123`.
Actual responses carry a UUID string of the form
`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`. Stock templates use 8-character ids
such as `D04AC939`, so ids come in two formats and neither is an integer.

## 3. `is_private` is accepted on create but not returned on read

`POST /v1/workouts` accepts `is_private`. `GET /v1/workouts/{id}` for the same
workout did not include an `is_private` field, so a client cannot confirm the
visibility it set.

## 4. No delete endpoints

There is no `DELETE` for workouts or for custom exercise templates. A client
that imports the wrong data cannot undo it, and the in-app Strong import, which
can be reverted, is not available through the API. Even a delete limited to
workouts created by the same API key would make bulk imports much safer.

## 5. Page size limits are not in the docs

The spec lists `pageSize` with a default of 5 and no maximum. `pageSize=100`
worked for `/exercise_templates` and `pageSize=10` for `/workouts`; the upper
bounds are not stated.

## Things that worked as documented

- `POST /v1/workouts` returned 201 for all 497 requests at one request per
  second; no 429 or 5xx was seen.
- Set types `warmup` and `normal`, and `null` for unused metrics.
- Backdated `start_time`/`end_time` (2023 onwards) were accepted as given.
- `GET /v1/workouts/count` matched the number of workouts created.
