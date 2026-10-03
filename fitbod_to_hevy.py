#!/usr/bin/env python3
"""Import a Fitbod CSV export into Hevy through the Hevy public API.

Stages (run in order; each is safe to re-run):

  inspect   Summarise the CSV. No network.
  map       Fetch every Hevy exercise template and write exercise_map.json
            (Fitbod name -> template id, with confidence). Review/edit it.
  import    Build workout payloads, skip ones already in Hevy, and POST.
            --dry-run writes payloads to ./out/ and posts nothing.
            --limit N posts at most N workouts.

The API key is read from $HEVY_API_KEY (or, failing that, the first line of
~/.config/hevy/api_key). It is never printed or written anywhere.
"""
import argparse
import difflib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE_URL = "https://api.hevyapp.com/v1"
KEY_FILE = Path.home() / ".config/hevy/api_key"
MAP_FILE = Path("exercise_map.json")
OUT_DIR = Path("out")
LOG_FILE = Path("import_log.jsonl")

SECONDS_PER_SET = 150  # Fitbod exports no end time; estimate 2.5 min per set
MAX_SPEED_MPS = 12.0  # distance/duration faster than this is treated as bad data
HIGH, REVIEW = 0.85, 0.60  # confidence thresholds

# Fitbod name -> Hevy title, for pairs the fuzzy matcher can't derive from the
# words alone. Only applied when the Hevy title really exists in the account.
ALIASES = {
    "Back Squat": "Squat (Barbell)",
    "Barbell Shoulder Press": "Overhead Press (Barbell)",
    "Seated Barbell Shoulder Press": "Seated Overhead Press (Barbell)",
    "Standing Dumbbell Shoulder Press": "Overhead Press (Dumbbell)",
    "Dumbbell Shoulder Press": "Shoulder Press (Dumbbell)",
    "Arnold Dumbbell Press": "Arnold Press (Dumbbell)",
    "Deadlift": "Deadlift (Barbell)",
    "Romanian Deadlift": "Romanian Deadlift (Barbell)",
    "Sumo Deadlift": "Sumo Deadlift",
    "Bent Over Barbell Row": "Bent Over Row (Barbell)",
    "Dumbbell Bent Over Row": "Bent Over Row (Dumbbell)",
    "Close-Grip Bench Press": "Bench Press - Close Grip (Barbell)",
    "Barbell Bench Press": "Bench Press (Barbell)",
    "Barbell Incline Bench Press": "Incline Bench Press (Barbell)",
    "Barbell Decline Bench Press": "Decline Bench Press (Barbell)",
    "Dumbbell Fly": "Chest Fly (Dumbbell)",
    "Dumbbell Incline Fly": "Incline Chest Fly (Dumbbell)",
    "Dumbbell Decline Fly": "Decline Chest Fly (Dumbbell)",
    "Cable Crossover Fly": "Cable Fly Crossovers",
    "Dumbbell Bicep Curl": "Bicep Curl (Dumbbell)",
    "Barbell Curl": "Bicep Curl (Barbell)",
    "Cable Bicep Curl": "Bicep Curl (Cable)",
    "Hammer Curls": "Hammer Curl (Dumbbell)",
    "Cross Body Hammer Curls": "Cross Body Hammer Curl",
    "EZ-Bar Curl": "EZ Bar Biceps Curl",
    "Preacher Curl": "Preacher Curl (Barbell)",
    "Dumbbell Preacher Curls": "Preacher Curl (Dumbbell)",
    "Seated Dumbbell Curl": "Seated Incline Curl (Dumbbell)",
    "Incline Dumbbell Curl": "Seated Incline Curl (Dumbbell)",
    "Reverse Barbell Curl": "Reverse Curl (Barbell)",
    "Reverse Dumbbell Curl": "Reverse Curl (Dumbbell)",
    "Barbell Bicep Drag Curl": "Drag Curl",
    "Skullcrusher": "Skullcrusher (Barbell)",
    "Dumbbell Skullcrusher": "Skullcrusher (Dumbbell)",
    "Cable Rope Tricep Extension": "Triceps Rope Pushdown",
    "Cable Tricep Pushdown": "Triceps Pushdown",
    "Cable Rope Overhead Triceps Extension": "Overhead Triceps Extension (Cable)",
    "Dumbbell Tricep Extension": "Triceps Extension (Dumbbell)",
    "Dumbbell Kickbacks": "Triceps Kickback (Dumbbell)",
    "Dip": "Triceps Dip",
    "Bench Dip": "Bench Dip",
    "Cable Row": "Seated Cable Row - V Grip (Cable)",
    "Machine Row": "Seated Row (Machine)",
    "Lat Pulldown": "Lat Pulldown (Cable)",
    "V-Bar Pulldown": "Lat Pulldown - Close Grip (Cable)",
    "Reverse Grip Pull Down": "Reverse Grip Lat Pulldown (Cable)",
    "Cable Face Pull": "Face Pull",
    "Dumbbell Rear Delt Raise": "Rear Delt Reverse Fly (Dumbbell)",
    "Dumbbell Back Fly": "Rear Delt Reverse Fly (Dumbbell)",
    "Dumbbell Bent Over Reverse Fly": "Rear Delt Reverse Fly (Dumbbell)",
    "Dumbbell Lateral Raise": "Lateral Raise (Dumbbell)",
    "Cable Lateral Raise": "Lateral Raise (Cable)",
    "Dumbbell Front Raise": "Front Raise (Dumbbell)",
    "Dumbbell Shrug": "Shrug (Dumbbell)",
    "Barbell Shrug": "Shrug (Barbell)",
    "Upright Row": "Upright Row (Barbell)",
    "Pull Up": "Pull Up",
    "Chin Up": "Chin Up",
    "Assisted Pull Up": "Pull Up (Assisted)",
    "Australian Chin Up": "Inverted Row",
    "Front Squat": "Front Squat",
    "Air Squats": "Squat (Bodyweight)",
    "Dumbbell Squat": "Squat (Dumbbell)",
    "Leg Press": "Leg Press (Machine)",
    "Leg Extension": "Leg Extension (Machine)",
    "Lying Hamstrings Curl": "Lying Leg Curl (Machine)",
    "Seated Leg Curl": "Seated Leg Curl (Machine)",
    "Lunge": "Lunge",
    "Dumbbell Lunge": "Lunge (Dumbbell)",
    "Barbell Lunge": "Lunge (Barbell)",
    "Dumbbell Bulgarian Split Squat": "Bulgarian Split Squat",
    "Barbell Hip Thrust": "Hip Thrust (Barbell)",
    "Hip Thrust": "Hip Thrust",
    "Good Morning": "Good Morning (Barbell)",
    "Stiff-Legged Barbell Good Morning": "Good Morning (Barbell)",
    "Dumbbell Romanian Deadlift": "Romanian Deadlift (Dumbbell)",
    "Single Leg Romanian Deadlift": "Single Leg Romanian Deadlift (Dumbbell)",
    "Standing Barbell Calf Raise": "Standing Calf Raise (Barbell)",
    "Kettlebell Swing American": "Kettlebell Swing",
    "Clean": "Clean",
    "Clean and Press": "Clean and Press",
    "Push Press": "Push Press",
    "Smith Machine Bench Press": "Bench Press (Smith Machine)",
    "Smith Machine Incline Bench Press": "Incline Bench Press (Smith Machine)",
    "Smith Machine Bent Over Row": "Bent Over Row (Smith Machine)",
    "Hammerstrength Chest Press": "Chest Press (Machine)",
    "Floor Press": "Floor Press (Barbell)",
    "Dumbbell Pullover": "Pullover (Dumbbell)",
    "Push Up on Knees": "Kneeling Push Up",
    "Tricep Push Up": "Diamond Push Up",
    "Crunches": "Crunch",
    "Ab Rollout": "Ab Wheel",
    "Vertical Knee Raise": "Knee Raise Parallel Bars",
    "Leg Raise": "Lying Leg Raise",
    "Back Extensions": "Back Extension (Hyperextension)",
    "Side Bridge": "Side Plank",
    "Jumping Jack": "Jumping Jack",
    "Running - Treadmill": "Treadmill",
    "Stair Stepper": "Stair Machine (Steps)",
    "Rowing": "Rowing Machine",
    "Elliptical": "Elliptical Trainer",
    "Farmer's Walk": "Farmers Walk",
}

# Closest-available stand-ins where Hevy has no true equivalent. Listed for review.
APPROX = {
    "Exercise Ball Crunch": "Crunch (Weighted)",
    "Russian Twist": "Russian Twist (Weighted)",
    "Gorilla Rows": "Gorilla Row (Kettlebell)",
    "Kettlebell Row": "Gorilla Row (Kettlebell)",
    "Kettlebell Alternating Row": "Gorilla Row (Kettlebell)",
    "Single Arm Kettlebell Swing": "Kettlebell Swing",
    "Alternating Single Arm Kettlebell Swing": "Kettlebell Swing",
    "EZ-Bar Overhead Tricep Extension": "Triceps Extension (Barbell)",
    "Step Up": "Dumbbell Step Up",
    "Cable Hip Extension": "Standing Cable Glute Kickbacks",
    "Single Leg Kickback": "Standing Cable Glute Kickbacks",
    "Bulgarian Split Squat": "Bulgarian Split Squat (Dumbbell)",
    "Seated Back Extension": "Back Extension (Machine)",
    "Single Arm Cable Press": "Bench Press (Cable)",
    "Cobra Triceps Extension": "Triceps Extension (Cable)",
    "Cable One Arm Tricep Side Extension": "Single Arm Triceps Pushdown (Cable)",
    "Single Arm High Cable Tricep Extension": "Single Arm Triceps Pushdown (Cable)",
    "Cable One Arm Underhand Tricep Extension": "Reverse Grip Triceps Pushdown",
    "Cable Underhand Tricep Pushdown": "Reverse Grip Triceps Pushdown",
    "Pause Back Squat": "Pause Squat (Barbell)",
    "Pause Barbell Incline Bench Press": "Incline Bench Press (Barbell)",
    "Barbell Back Squat with Deep Partial Squat": "Squat (Barbell)",
    "Barbell Pause Curl": "Bicep Curl (Barbell)",
    "Box Squat": "Box Squat (Barbell)",
    "Wide Grip Pull Up": "Wide Pull Up",
    "Incline Dumbbell Squeeze Press": "Dumbbell Squeeze Press",
    "Kettlebell Upright Row": "Kettlebell High Pull",
    "Single Arm Overhead Dumbbell Lunge": "Overhead Dumbbell Lunge",
    "Single-Leg Stability Ball Hip Thrust": "Single Leg Hip Thrust",
    "Dumbbell Wide Curls": "Bicep Curl (Dumbbell)",
    "Incline Dumbbell Row": "Chest Supported Incline Row (Dumbbell)",
    "Incline Hammer Curl": "Seated Incline Hammer Curl (Dumbbell)",
    "Single Dumbbell Sumo Squat": "Sumo Squat (Dumbbell)",
    "Waiter Curls": "Waiter Curl (Dumbbell)",
    "Overhead Cable Bicep Curl": "Overhead Curl (Cable)",
    "Leg Pull-In": "Lying Knee Raise",
    "Scissor Kick": "Ab Scissors",
    "Scissor Crossover Kick": "Ab Scissors",
    "Incline EZ-Bar Curl": "EZ Bar Biceps Curl",
    "Superman with Scaption": "Superman",
    "Alternating Superman": "Superman",
    "Medicine Ball Superman": "Superman",
    "Dumbbell Superman": "Superman",
    "Elevated Hip Bridge": "Glute Bridge",
    "Shotgun Row": "Single Arm Cable Row",
    "Hand Release Push Up": "Push Up",
    "Alternating Medicine Ball Push Up": "Push Up",
    "Balance Trainer Push Up": "Push Up",
    "Stability Ball Back Extension": "Back Extension (Hyperextension)",
    "Power Clean to Front Squat": "Power Clean",
    "Tuck Jump": "Jump Squat",
    "Alternating Lunge Jumps": "Jumping Lunge",
    "Lunge Jump": "Jumping Lunge",
    "Forward Lunge with Twist": "Lunge",
    "Kneeling Squat": "Squat (Bodyweight)",
    "Prone I's": "Chest Supported Y Raise (Dumbbell)",
    "Prone Y's": "Chest Supported Y Raise (Dumbbell)",
    "Single Arm Bottoms-up Kettlebell Press": "Kettlebell Shoulder Press",
    "Clean Deadlift": "Clean Pull",
}

# Proposed custom exercises (nothing close in Hevy, or the data can't fit the
# stock template's type). Created only with --create-custom.
CREATE = {
    "Barbell Step Up": dict(title="Step Up (Barbell)", exercise_type="weight_reps", equipment_category="barbell", muscle_group="quadriceps", other_muscles=["glutes"]),
    "Dumbbell Squat To Shoulder Press": dict(title="Thruster (Dumbbell)", exercise_type="weight_reps", equipment_category="dumbbell", muscle_group="full_body", other_muscles=["quadriceps", "shoulders"]),
    "Pulse Back Squat": dict(title="Pulse Squat (Barbell)", exercise_type="weight_reps", equipment_category="barbell", muscle_group="quadriceps", other_muscles=["glutes"]),
    "Dual Kettlebell Front Squat": dict(title="Front Squat (Kettlebell)", exercise_type="weight_reps", equipment_category="kettlebell", muscle_group="quadriceps", other_muscles=["glutes"]),
    "Deadlift to Calf Raise": dict(title="Deadlift to Calf Raise", exercise_type="weight_reps", equipment_category="barbell", muscle_group="hamstrings", other_muscles=["calves", "glutes"]),
    # Grip variants stay separate from the standard lift: working weights differ by grip.
    "Smith Machine Close-Grip Bench Press": dict(title="Bench Press - Close Grip (Smith Machine)", exercise_type="weight_reps", equipment_category="machine", muscle_group="triceps", other_muscles=["chest", "shoulders"]),
    "Close-Grip EZ-Bar Curl": dict(title="EZ Bar Biceps Curl - Close Grip", exercise_type="weight_reps", equipment_category="barbell", muscle_group="biceps", other_muscles=["forearms"]),
    "Wide Grip Lat Pulldown": dict(title="Lat Pulldown - Wide Grip (Cable)", exercise_type="weight_reps", equipment_category="machine", muscle_group="lats", other_muscles=["biceps", "upper_back"]),
    "Reverse Grip Barbell Bent Over Row": dict(title="Bent Over Row - Reverse Grip (Barbell)", exercise_type="weight_reps", equipment_category="barbell", muscle_group="upper_back", other_muscles=["lats", "biceps"]),
    "Neutral Grip Pull Up": dict(title="Pull Up - Neutral Grip", exercise_type="reps_only", equipment_category="other", muscle_group="lats", other_muscles=["biceps", "upper_back"]),
    "V-Bar Pull Up": dict(title="Pull Up - Close Grip (V-Bar)", exercise_type="reps_only", equipment_category="other", muscle_group="lats", other_muscles=["biceps", "upper_back"]),
    "Dumbbell Clean": dict(title="Clean (Dumbbell)", exercise_type="weight_reps", equipment_category="dumbbell", muscle_group="full_body", other_muscles=[]),
    "Superman Hold": dict(title="Superman Hold", exercise_type="duration", equipment_category="none", muscle_group="lower_back", other_muscles=["glutes"]),
    "Dumbbell Glute Bridge": dict(title="Glute Bridge Hold (Dumbbell)", exercise_type="weight_duration", equipment_category="dumbbell", muscle_group="glutes", other_muscles=["hamstrings"]),
    "Barbell Glute Bridge": dict(title="Glute Bridge Hold (Barbell)", exercise_type="weight_duration", equipment_category="barbell", muscle_group="glutes", other_muscles=["hamstrings"]),
}

# Too few sets, and no template that can hold the data, to be worth a custom.
SKIP = {"High Knee", "Jumping Jack", "Stability Ball Hip Bridge", "Biceps Curl To Shoulder Press"}

EQUIPMENT_WORDS = {
    "barbell": "barbell", "dumbbell": "dumbbell", "kettlebell": "kettlebell",
    "cable": "machine", "machine": "machine", "smith": "machine",
    "hammerstrength": "machine", "band": "resistance_band", "plate": "plate",
}
SYNONYMS = {
    "bicep": "biceps", "tricep": "triceps", "ez-bar": "ezbar", "ez": "ezbar",
    "hamstrings": "hamstring", "pulldown": "pulldown", "situp": "sit up",
    "pushup": "push up", "pullup": "pull up", "chinup": "chin up",
    "skullcrushers": "skullcrusher", "crunches": "crunch", "curls": "curl",
    "rows": "row", "squats": "squat", "extensions": "extension",
    "kickbacks": "kickback", "pulls": "pull", "kicks": "kick",
}
STOPWORDS = {"the", "a", "with", "to", "and", "bar"}


# ----------------------------------------------------------------- CSV side

def load_csv(path):
    df = pd.read_csv(path, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    df["row"] = range(len(df))
    df["Exercise"] = df["Exercise"].str.strip()
    df["ts"] = pd.to_datetime(df["Date"], utc=True)
    df["isWarmup"] = df["isWarmup"].astype(str).str.lower().eq("true")
    for c in ("Reps", "Weight(kg)", "Duration(s)", "Distance(m)"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    speed = df["Distance(m)"] / df["Duration(s)"].where(df["Duration(s)"] > 0)
    df["bad_distance"] = speed > MAX_SPEED_MPS
    return df


def weight_unit(df):
    """Fitbod labels the column itself; cross-check against plate increments."""
    col = next(c for c in df.columns if c.lower().startswith("weight"))
    w = df[col][df[col] > 0]
    as_lb = w * 2.2046226218
    round_lb = ((as_lb / 2.5 - (as_lb / 2.5).round()).abs() < 0.01).mean()
    round_kg = ((w / 1.25 - (w / 1.25).round()).abs() < 0.01).mean()
    return col, round_lb, round_kg


def shape(rows):
    """Which metrics an exercise actually uses across its Fitbod rows."""
    return {
        "reps": bool((rows["Reps"] > 0).any()),
        "weight": bool((rows["Weight(kg)"] > 0).any()),
        "duration": bool((rows["Duration(s)"] > 0).any()),
        "distance": bool((rows["Distance(m)"] > 0).any()),
    }


def cmd_inspect(df, args):
    col, round_lb, round_kg = weight_unit(df)
    per_workout = df.groupby("ts").size()
    print(f"file        : {args.csv}")
    print(f"headers     : {[c for c in df.columns if c not in ('row', 'ts', 'bad_distance')]}")
    print(f"rows (sets) : {len(df)}")
    print(f"date range  : {df.ts.min()}  ->  {df.ts.max()}  (UTC)")
    print(f"workouts    : {df.ts.nunique()} distinct timestamps over {df.ts.dt.date.nunique()} days")
    print(f"sets/workout: median {per_workout.median():.0f}, max {per_workout.max()}, "
          f"single-set sessions {int((per_workout == 1).sum())}")
    print(f"exercises   : {df.Exercise.nunique()} distinct")
    print(f"weight unit : column is '{col}' -> kg. {round_lb:.1%} of weights are round 2.5 lb steps, "
          f"{round_kg:.1%} round 1.25 kg steps (logged in lb, exported in kg; no conversion needed)")
    print(f"warm-up sets: {int(df.isWarmup.sum())}")
    print(f"timed rows  : {int((df['Duration(s)'] > 0).sum())}, with distance: {int((df['Distance(m)'] > 0).sum())}")
    bad = df[df.bad_distance]
    if len(bad):
        print(f"bad distance: {len(bad)} rows imply > {MAX_SPEED_MPS} m/s; distance will be dropped:")
        print(bad[["Date", "Exercise", "Duration(s)", "Distance(m)"]].to_string(index=False))
    print("\nsample:")
    print(df.drop(columns=["row", "ts", "bad_distance"]).head(8).to_string(index=False))


# ----------------------------------------------------------------- API side

def api_key():
    key = os.environ.get("HEVY_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().splitlines()[0].strip()
    if not key:
        sys.exit(f"No API key: set HEVY_API_KEY or put it in {KEY_FILE} (chmod 600).")
    return key


class Hevy:
    def __init__(self, throttle=1.0):
        self.s = requests.Session()
        self.s.headers.update({"api-key": api_key(), "accept": "application/json"})
        self.throttle = throttle
        self._last = 0.0

    def request(self, method, path, retries=5, **kw):
        for attempt in range(retries + 1):
            wait = self.throttle - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.s.request(method, BASE_URL + path, timeout=30, **kw)
            except requests.RequestException as e:
                # A POST that died mid-flight may have landed; don't blindly repeat it.
                if method != "GET" or attempt == retries:
                    raise RuntimeError(f"{method} {path}: {type(e).__name__}") from None
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or (r.status_code >= 500 and method == "GET"):
                if attempt == retries:
                    break
                delay = float(r.headers.get("Retry-After") or 2 ** (attempt + 1))
                print(f"  {r.status_code} on {method} {path}; retrying in {delay:.0f}s", file=sys.stderr)
                time.sleep(delay)
                continue
            return r
        return r

    def paged(self, path, key, page_size):
        items, page = [], 1
        while True:
            r = self.request("GET", path, params={"page": page, "pageSize": page_size})
            if r.status_code == 404 and page > 1:  # some endpoints 404 past the end
                break
            if r.status_code != 200:
                sys.exit(f"GET {path} page {page} -> {r.status_code}: {r.text[:300]}")
            body = r.json()
            items += body.get(key, [])
            if page >= body.get("page_count", 1):
                break
            page += 1
        return items


# ----------------------------------------------------------------- mapping

def tokens(name):
    name = name.lower().replace("&", " and ")
    name = re.sub(r"[()\-/,'’.]", " ", name)
    out = []
    for t in name.split():
        t = SYNONYMS.get(t, t)
        for u in t.split():
            if u not in STOPWORDS:
                out.append(u)
    return out


def similarity(a_tokens, b_tokens):
    a, b = set(a_tokens), set(b_tokens)
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / len(a | b)
    ratio = difflib.SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
    return 0.6 * jaccard + 0.4 * ratio


def guess_custom(name, sh):
    """Reasonable defaults for a custom template; edit in exercise_map.json."""
    toks = tokens(name)
    equipment = next((EQUIPMENT_WORDS[t] for t in toks if t in EQUIPMENT_WORDS), None)
    if equipment is None:
        equipment = "other" if sh["weight"] else "none"
    if sh["distance"]:
        etype = "distance_duration" if sh["duration"] else "short_distance_weight"
    elif sh["duration"] and not sh["reps"]:
        etype = "weight_duration" if sh["weight"] else "duration"
    else:
        etype = "weight_reps" if sh["weight"] else "reps_only"
    return {"title": name, "exercise_type": etype, "equipment_category": equipment,
            "muscle_group": "other", "other_muscles": []}


def fit(rows, hevy_type):
    """(sets that can't be represented, sets whose weight would be lost) for a template type."""
    built = [build_set(r, hevy_type) for _, r in rows.iterrows()]
    lost = sum(s is None for s in built)
    unweighted = sum(1 for s, (_, r) in zip(built, rows.iterrows())
                     if s is not None and r["Weight(kg)"] > 0 and s["weight_kg"] is None)
    return lost, unweighted


def build_map(df, templates):
    by_title = {t["title"].lower(): t for t in templates}
    tpl_tokens = [(t, tokens(t["title"])) for t in templates]
    result = {}
    for name, rows in sorted(df.groupby("Exercise"), key=lambda kv: -len(kv[1])):
        sh = shape(rows)
        ftoks = tokens(name)
        scored = sorted(((similarity(ftoks, tt), t) for t, tt in tpl_tokens),
                        key=lambda x: -x[0])[:4]
        best, method, conf, action = None, None, 0.0, "map"
        if name in SKIP:
            action = "skip"
        elif name in CREATE:
            action = "create"
        elif ALIASES.get(name, "").lower() in by_title:
            best, method, conf = by_title[ALIASES[name].lower()], "alias", 0.95
        elif APPROX.get(name, "").lower() in by_title:
            best, method, conf = by_title[APPROX[name].lower()], "approx", 0.8
        elif name.lower() in by_title:
            best, method, conf = by_title[name.lower()], "exact", 1.0
        elif scored:
            conf, best = scored[0]
            method = "normalized" if set(ftoks) == set(tokens(best["title"])) else "fuzzy"
            conf = 0.92 if method == "normalized" else round(min(conf, 0.84), 2)
            if conf < REVIEW:
                best, action = None, "unmatched"
        notes = []
        if best:
            lost, unweighted = fit(rows, best.get("type"))
            if lost:
                notes.append(f"{lost} of {len(rows)} sets don't fit a {best.get('type')} exercise and are dropped")
            if unweighted:
                notes.append(f"{unweighted} sets lose their weight")
        result[name] = {
            "sets": int(len(rows)),
            "fitbod_shape": "+".join(k for k, v in sh.items() if v) or "none",
            "confidence": conf,
            "method": method,
            "action": action,
            "hevy_id": best["id"] if best else None,
            "hevy_title": best["title"] if best else None,
            "hevy_type": best.get("type") if best else None,
            "note": "; ".join(notes),
            "alternatives": [{"id": t["id"], "title": t["title"], "type": t.get("type"),
                              "score": round(s, 2)} for s, t in scored],
            "custom": CREATE.get(name) or guess_custom(name, sh),
        }
    return result


def print_map(m):
    def line(name, e):
        return (f"  {e['confidence']:.2f} {e['method'] or '-':10} {e['sets']:4d}  {name[:38]:38} -> "
                f"{(e['hevy_title'] or '—')[:40]:40} [{e['hevy_type'] or ''}] {e['note']}")
    groups = [
        ("HIGH confidence", lambda e: e["action"] == "map" and e["confidence"] >= HIGH),
        ("REVIEW these", lambda e: e["action"] == "map" and e["confidence"] < HIGH),
        ("UNMATCHED (set action to map+hevy_id, create, or skip)", lambda e: e["action"] == "unmatched"),
        ("CREATE custom", lambda e: e["action"] == "create"),
        ("SKIP", lambda e: e["action"] == "skip"),
    ]
    for title, pred in groups:
        rows = [(n, e) for n, e in m.items() if pred(e)]
        if not rows:
            continue
        print(f"\n{title}: {len(rows)} exercises, {sum(e['sets'] for _, e in rows)} sets")
        for n, e in rows:
            if e["action"] == "create" and not e.get("hevy_id"):
                c = e["custom"]
                print(f"  new              {e['sets']:4d}  {n[:38]:38} -> {c['title']} [{c['exercise_type']}, {c['equipment_category']}, {c['muscle_group']}]")
                continue
            print(line(n, e))
            if e["action"] == "unmatched":
                print("        closest: " + "; ".join(f"{a['title']} ({a['score']})" for a in e["alternatives"][:3]))


def cmd_map(df, args):
    if MAP_FILE.exists() and not args.rebuild_map:
        print(f"{MAP_FILE} exists; showing it (use --rebuild-map to regenerate and discard edits).")
        print_map(json.loads(MAP_FILE.read_text()))
        return
    hevy = Hevy(throttle=0.25)
    templates = hevy.paged("/exercise_templates", "exercise_templates", 100)
    print(f"fetched {len(templates)} Hevy exercise templates "
          f"({sum(1 for t in templates if t.get('is_custom'))} custom)")
    m = build_map(df, templates)
    MAP_FILE.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    print_map(m)
    print(f"\nwrote {MAP_FILE}. Edit 'action' (map | create | skip) and 'hevy_id' as needed, then run --step import --dry-run.")


def create_customs(hevy, m):
    """POST a template for every map entry marked action=create. Not undoable via API."""
    for name, e in m.items():
        if e["action"] != "create" or e.get("hevy_id"):
            continue
        r = hevy.request("POST", "/exercise_templates", json={"exercise": e["custom"]})
        if r.status_code != 200:
            sys.exit(f"creating custom exercise '{name}' failed: {r.status_code} {r.text[:300]}")
        try:
            new_id = r.json().get("id")
        except ValueError:
            new_id = r.text.strip().strip('"')
        got = hevy.request("GET", f"/exercise_templates/{new_id}")
        actual = got.json().get("type") if got.status_code == 200 else None
        e.update(hevy_id=str(new_id), hevy_title=e["custom"]["title"],
                 hevy_type=actual or e["custom"]["exercise_type"], method="custom", confidence=1.0)
        MAP_FILE.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
        print(f"  created custom exercise '{name}' -> {new_id}")


# ----------------------------------------------------------------- payloads

def iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_set(row, hevy_type):
    """Return a Hevy set for one Fitbod row, or None if nothing in the row fits the template type."""
    reps = int(row["Reps"]) or None
    weight = round(float(row["Weight(kg)"]), 2) or None
    duration = int(round(row["Duration(s)"])) or None
    distance = None if row["bad_distance"] else (int(round(row["Distance(m)"])) or None)
    s = {"type": "warmup" if row["isWarmup"] else "normal", "weight_kg": None, "reps": None,
         "distance_meters": None, "duration_seconds": None}
    if hevy_type in ("weight_reps", "bodyweight_weighted", "bodyweight_assisted",
                     "bodyweight_reps", "bodyweight_assisted_reps"):
        if not reps:
            return None
        s.update(reps=reps, weight_kg=weight)
        if hevy_type == "weight_reps" and weight is None:
            s["weight_kg"] = 0
    elif hevy_type == "reps_only":
        if not reps:
            return None
        s.update(reps=reps)
    elif hevy_type in ("duration", "steps_duration", "floors_duration"):
        if not duration:
            return None
        s.update(duration_seconds=duration)
    elif hevy_type == "weight_duration":
        if not duration:
            return None
        s.update(duration_seconds=duration, weight_kg=weight or 0)
    elif hevy_type == "distance_duration":
        if not (duration or distance):
            return None
        s.update(duration_seconds=duration, distance_meters=distance)
    elif hevy_type == "short_distance_weight":
        if not (distance or weight):
            return None
        s.update(distance_meters=distance, weight_kg=weight)
    else:
        return None
    return s


def title_for(start, names, tz):
    if len(names) == 1:
        return names[0]
    hour = start.astimezone(tz).hour
    part = "Morning" if hour < 12 else "Afternoon" if hour < 17 else "Evening"
    return f"{part} Workout"


def build_workouts(df, m, args):
    tz = ZoneInfo(args.tz) if args.tz else None  # None -> system local time
    workouts, problems = [], []
    for ts, rows in df.sort_values("row").groupby("ts", sort=True):
        exercises, dropped, unresolved, seconds = [], 0, [], 0
        # consecutive rows of the same exercise form one exercise block
        block = (rows["Exercise"] != rows["Exercise"].shift()).cumsum()
        for _, erows in rows.groupby(block, sort=True):
            name = erows["Exercise"].iloc[0]
            e = m.get(name)
            if e is None or e["action"] == "unmatched" or (e["action"] != "skip" and not e.get("hevy_id")):
                unresolved.append(name)
                continue
            if e["action"] == "skip":
                dropped += len(erows)
                continue
            sets = [build_set(r, e["hevy_type"]) for _, r in erows.iterrows()]
            dropped += sum(s is None for s in sets)
            sets = [s for s in sets if s]
            if not sets:
                continue
            seconds += sum(s["duration_seconds"] or SECONDS_PER_SET for s in sets)
            exercises.append({"exercise_template_id": e["hevy_id"], "superset_id": None,
                              "notes": None, "sets": sets})
        start = ts.to_pydatetime()
        names = list(dict.fromkeys(rows["Exercise"]))
        if unresolved:
            problems.append((iso(start), f"unresolved exercises: {sorted(set(unresolved))}"))
            continue
        if not exercises:
            problems.append((iso(start), "no importable sets"))
            continue
        if args.skip_cardio_only and all(m[n]["hevy_type"] == "distance_duration" for n in names if m[n]["action"] == "map"):
            continue
        workouts.append({
            "dropped_sets": dropped,
            "payload": {"workout": {
                "title": title_for(start, names, tz),
                "description": "Imported from Fitbod",
                "start_time": iso(start),
                "end_time": iso(start + timedelta(seconds=max(seconds, 60))),
                "is_private": not args.public,
                "exercises": exercises,
            }},
        })
    return workouts, problems


def epoch(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def logged_starts():
    done = set()
    if LOG_FILE.exists():
        for line in LOG_FILE.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("ok"):
                done.add(epoch(rec["start_time"]))
    return done


def cmd_import(df, args):
    if not MAP_FILE.exists():
        sys.exit(f"{MAP_FILE} not found; run --step map first.")
    m = json.loads(MAP_FILE.read_text())
    hevy = Hevy(throttle=args.throttle)

    pending_custom = [n for n, e in m.items() if e["action"] == "create" and not e.get("hevy_id")]
    if pending_custom:
        if args.create_custom and not args.dry_run:
            create_customs(hevy, m)
        elif args.dry_run:
            print(f"dry run: {len(pending_custom)} custom exercises would be created; using placeholder ids")
            for n in pending_custom:
                m[n].update(hevy_id=f"CUSTOM:{n}", hevy_type=m[n]["custom"]["exercise_type"])
        else:
            sys.exit(f"{len(pending_custom)} exercises are marked 'create'. Custom templates cannot be "
                     f"deleted via the API; re-run with --create-custom to create them: {pending_custom}")

    workouts, problems = build_workouts(df, m, args)

    existing = {epoch(w["start_time"]) for w in hevy.paged("/workouts", "workouts", 10)}
    existing |= logged_starts()
    todo = [w for w in workouts if epoch(w["payload"]["workout"]["start_time"]) not in existing]

    n_sets = sum(len(x["sets"]) for w in todo for x in w["payload"]["workout"]["exercises"])
    print(f"workouts built      : {len(workouts)}")
    print(f"already in Hevy     : {len(workouts) - len(todo)} (skipped)")
    print(f"to import           : {len(todo)} workouts, {n_sets} sets")
    print(f"sets dropped        : {sum(w['dropped_sets'] for w in todo)} (skipped exercises or no data fitting the template type)")
    print(f"workouts held back  : {len(problems)}")
    for start, why in problems[:15]:
        print(f"    {start}  {why}")
    if len(problems) > 15:
        print(f"    ... {len(problems) - 15} more")

    if args.limit is not None:
        todo = todo[-args.limit:] if args.newest_first else todo[:args.limit]

    if args.dry_run:
        OUT_DIR.mkdir(exist_ok=True)
        for f in OUT_DIR.glob("*.json"):
            f.unlink()
        for w in todo:
            start = w["payload"]["workout"]["start_time"]
            (OUT_DIR / f"{start.replace(':', '')}.json").write_text(json.dumps(w["payload"], indent=2) + "\n")
        print(f"dry run: wrote {len(todo)} payloads to {OUT_DIR}/, nothing posted")
        return

    ok = failed = 0
    with LOG_FILE.open("a") as log:
        for i, w in enumerate(todo, 1):
            wk = w["payload"]["workout"]
            rec = {"at": iso(datetime.now(timezone.utc)), "start_time": wk["start_time"], "title": wk["title"]}
            try:
                r = hevy.request("POST", "/workouts", json=w["payload"])
                rec["status"] = r.status_code
                rec["ok"] = r.status_code in (200, 201)
                if rec["ok"]:
                    body = r.json()
                    created = body.get("workout", body)
                    if isinstance(created, list):
                        created = created[0] if created else {}
                    rec["hevy_id"] = created.get("id")
                else:
                    rec["error"] = r.text[:500]
            except (RuntimeError, ValueError) as e:
                rec.update(ok=False, error=str(e))
            log.write(json.dumps(rec) + "\n")
            log.flush()
            ok += rec["ok"]
            failed += not rec["ok"]
            print(f"[{i}/{len(todo)}] {wk['start_time']} {wk['title']}: "
                  f"{'ok ' + str(rec.get('hevy_id')) if rec['ok'] else 'FAILED ' + str(rec.get('status', '')) + ' ' + rec.get('error', '')[:120]}")
    print(f"done: {ok} imported, {failed} failed; log in {LOG_FILE}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", default=str(Path.home() / "Downloads/WorkoutExport.csv"))
    p.add_argument("--step", choices=["inspect", "map", "import"], default="inspect")
    p.add_argument("--dry-run", action="store_true", help="import: write payloads to ./out/, post nothing")
    p.add_argument("--limit", type=int, help="import: handle at most N workouts")
    p.add_argument("--newest-first", action="store_true", help="with --limit, take the most recent workouts")
    p.add_argument("--rebuild-map", action="store_true", help="map: regenerate exercise_map.json")
    p.add_argument("--create-custom", action="store_true", help="import: create templates marked action=create")
    p.add_argument("--skip-cardio-only", action="store_true", help="import: leave out sessions that are only cardio")
    p.add_argument("--public", action="store_true", help="import: post workouts as public (default private)")
    p.add_argument("--tz", help="IANA timezone used only for workout titles (default: system local)")
    p.add_argument("--throttle", type=float, default=1.0, help="minimum seconds between API requests")
    args = p.parse_args()

    df = load_csv(args.csv)
    {"inspect": cmd_inspect, "map": cmd_map, "import": cmd_import}[args.step](df, args)


if __name__ == "__main__":
    main()
