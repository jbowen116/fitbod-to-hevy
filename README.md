# fitbod-to-hevy

Import a [Fitbod](https://fitbod.me) workout history into [Hevy](https://www.hevyapp.com)
through the Hevy public API.

Hevy's in-app importer only accepts Strong exports and can be used once. This
script goes through the API instead, so it needs a Hevy Pro account (the API is
a Pro feature) and leaves the one-time in-app import untouched.

It was used to move 3.5 years of history (497 workouts, 6,019 sets, 221 distinct
exercises) into an empty Hevy account with no failed requests.

## Read this first

- **The Hevy API cannot delete workouts or custom exercises.** Anything imported
  can only be removed by hand in the app. Do the dry run, then import a single
  workout and look at it in the app before importing the rest.
- Workouts are posted as private unless you pass `--public`.
- Fitbod exports a start time only. Workout length is estimated at 2.5 minutes
  per set (timed sets use their real duration).

## Setup

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Export from Fitbod (Settings → Export Workout Data) to get `WorkoutExport.csv`.

Get an API key at <https://hevy.com/settings?developer> (web only; it is not in
the mobile app) and either `export HEVY_API_KEY=...` or put it on the first line
of `~/.config/hevy/api_key` (mode 600). The key is never printed or logged.

## Usage

```
# 1. Look at the export. No network.
.venv/bin/python fitbod_to_hevy.py --csv WorkoutExport.csv --step inspect

# 2. Build exercise_map.json (Fitbod name -> Hevy exercise) and review it.
.venv/bin/python fitbod_to_hevy.py --csv WorkoutExport.csv --step map

# 3. Dry run: payloads are written to ./out/, nothing is posted.
.venv/bin/python fitbod_to_hevy.py --csv WorkoutExport.csv --step import --dry-run

# 4. Create the custom exercises and post only the most recent workout.
.venv/bin/python fitbod_to_hevy.py --csv WorkoutExport.csv --step import --create-custom --limit 1 --newest-first

# 5. Check it in the app, then import everything else.
.venv/bin/python fitbod_to_hevy.py --csv WorkoutExport.csv --step import
```

Re-running is safe: any workout whose start time is already in Hevy (or in
`import_log.jsonl`) is skipped, so a later Fitbod export only adds new workouts.

Other flags: `--skip-cardio-only`, `--public`, `--tz`, `--throttle`, `--rebuild-map`.

## The exercise map

`--step map` matches each Fitbod exercise to a Hevy exercise template and
writes `exercise_map.json`. Each entry gets an `action`:

| action | meaning |
|---|---|
| `map` | use the Hevy exercise in `hevy_id` |
| `create` | create the custom exercise described in `custom` (needs `--create-custom`) |
| `skip` | leave these sets out |
| `unmatched` | nothing found; workouts containing it are held back until you decide |

Matching order: a curated alias table (`ALIASES`, direct equivalents such as
`Back Squat` → `Squat (Barbell)`), a table of closest stand-ins (`APPROX`,
shown for review), exact title, then normalised/fuzzy matching. The tables
cover about 160 Fitbod names and are checked against the templates in your
account, so an alias is only used if that Hevy title really exists.

For every mapping the script counts how many sets cannot be represented in the
target exercise's type (a timed hold mapped to a reps-only exercise, a weighted
set mapped to a bodyweight exercise) and prints it, so losses are visible
before anything is posted.

Edit the JSON freely; it is only regenerated with `--rebuild-map`. The
`CREATE` and `SKIP` tables in the script reflect one person's choices (for
example, grip variants are kept as separate exercises because working weights
differ by grip). Change them to suit.

## How Fitbod data is translated

- One workout per distinct Fitbod timestamp; consecutive rows of the same
  exercise become one exercise block.
- `Weight(kg)` is already kilograms even when you logged in pounds. Dumbbell
  weights are per dumbbell, which is also what Hevy expects.
- `isWarmup` becomes Hevy set type `warmup`.
- A distance that implies more than 12 m/s is treated as bad data and dropped
  (the duration is kept).
- Titles: the exercise name for single-exercise sessions (e.g. `Running`),
  otherwise Morning/Afternoon/Evening Workout by local time.
- POSTs are throttled to one per second. 429s are retried; a POST that fails
  with a 5xx or a network error is logged as failed and not retried, because
  it may have been saved. Re-run to pick up anything that was missed.

## Notes on the Hevy API

See [HEVY_API_NOTES.md](HEVY_API_NOTES.md) for places where the API behaved
differently from its published documentation.

## License

MIT
