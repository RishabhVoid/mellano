"""Audited July 2023 gauge observations and gated background replay orchestration.

These are published observations, not a synthetic hydrograph. The linked pages
were inspected on 2026-11-01; rerunning this writer does not claim a new source
verification. Only replay.hourly_stages creates labelled intermediate values.
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time

from sim.config import DERIVED, ROOT, RUNS
from sim.data import write_json
from sim.export_web import read_json

IST = timezone(timedelta(hours=5, minutes=30))
DIARY = "https://www.noidadiary.in/2023/07/yamuna-water-level-breaches-danger-mark-amid-rains-timeline-of-floods-in-noida-delhi-in-july-2023/"
ANI_ORIGINAL = "http://aninews.in/news/national/politics/yamuna-water-level-starts-receding-at-9-pm-it-was-20807-metres20230715002654/"
ANI_SYNDICATION = "https://timesofindia.indiatimes.com/videos/news/delhi-army-personnel-conducts-desilting-work-at-itos-yamuna-barrage/videoshow/101787251.cms"
VERIFIED_ON = "2026-11-01 Asia/Kolkata"
READINGS = [
    ("2023-07-09T11:00:00+05:30", 203.14, DIARY, "July 12 entry retrospectively reports Sunday 11 am; Sunday is July 9 in the dated timeline."),
    ("2023-07-09T13:00:00+05:30", 203.18, DIARY, "July 9 entry: Old Railway Bridge, 1 pm."),
    ("2023-07-10T17:00:00+05:30", 205.40, DIARY, "July 10 entry: Old Railway Bridge, 5 pm."),
    ("2023-07-12T13:00:00+05:30", 207.55, DIARY, "July 12 entry: Old Railway Bridge, 1 pm."),
    ("2023-07-13T08:00:00+05:30", 208.48, DIARY, "July 13 entry: Old Railway Bridge, 8 am."),
    ("2023-07-13T19:00:00+05:30", 208.66, DIARY, "Peak timestamp uncertain: July 14 entry says previous Thursday 7 pm; a later recap says Thursday 8 pm. Use 19:00 as the explicit main-timeline anchor, retain 20:00 alternative; do not invent a second observation."),
    ("2023-07-14T05:00:00+05:30", 208.48, DIARY, "July 14 entry: 5 am."),
    ("2023-07-14T21:00:00+05:30", 208.07, ANI_SYNDICATION, "ANI-attributed July 15 video text explicitly pairs July 14, 9 pm and 208.07 m at Old Railway Bridge. Original ANI URL was unavailable; verification uses this accessible syndication."),
    ("2023-07-19T08:00:00+05:30", 205.48, DIARY, "July 19 entry: 8 am observed level. Exclude its separate 6 pm forecast."),
    ("2023-07-19T18:00:00+05:30", 205.80, DIARY, "July 19 entry: 6 pm observed level, not the earlier 205.72 m forecast."),
]


def audited_rows():
    rows = []
    for stamp, value, url, note in READINGS:
        uncertain = stamp.startswith("2023-07-13T19:")
        alternatives = [stamp, "2023-07-13T20:00:00+05:30"] if uncertain else [stamp]
        rows.append({"time_ist": stamp, "level_m": f"{value:.2f}", "source_url": url, "verified": "true",
                     "station": "Yamuna at Old Railway Bridge, Delhi", "source_name": "ANI via Times of India" if url == ANI_SYNDICATION else "Noida Diary (PTI/CWC reporting)",
                     "time_uncertainty_minutes": 60 if uncertain else 0, "time_alternatives_ist": json.dumps(alternatives),
                     "time_min_ist": alternatives[0], "time_max_ist": alternatives[-1], "notes": note})
    return rows


def build():
    from sim.jobs import update
    update("job_b", "Writing 10 source-verified stage anchors; preserving peak timestamp uncertainty", status="running")
    rows = audited_rows()
    path = DERIVED / "stage_2023.csv"
    DERIVED.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)
    from sim.replay import hourly_stages
    frames = hourly_stages(path)
    audit = {"verified_on": VERIFIED_ON, "generated_at": datetime.now(IST).isoformat(), "observed_rows": len(rows),
             "verified_rows": len(rows), "hourly_frames": len(frames), "interpolated_frames": sum(frame["interpolated"] for frame in frames),
             "uncertain_timestamp_observations": 1, "rows": rows, "source_urls": [DIARY, ANI_SYNDICATION],
             "original_ani_url": ANI_ORIGINAL, "original_ani_status": "Unavailable during source audit; actual verified syndication URL is stored on the row.",
             "verification_method": "Manual check of explicit value/time and dated article context on the linked pages; verified means publication-matched, not independent gauge certification.",
             "timezone_basis": "Indian local reporting time, Asia/Kolkata (UTC+05:30), explicit in every stored timestamp.",
             "peak_time_policy": "19:00 anchor, 20:00 alternative; one-hour timestamp uncertainty propagates to interpolated intervals touching this anchor.",
             "stage_csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    write_json(DERIVED / "stage_2023_sources.json", audit)
    update("job_b", f"10 verified anchors; {len(frames)} hourly frames ({audit['interpolated_frames']} interpolated); 1 peak timestamp ambiguity", outputs=[str(path), str(DERIVED / "stage_2023_sources.json")])
    print(json.dumps({key: audit[key] for key in ("observed_rows", "verified_rows", "hourly_frames", "interpolated_frames", "uncertain_timestamp_observations")}), flush=True)
    return audit


def nearest_preflood_stage(meta, rows):
    """Select a cited observation nearest to a real acquisition; retain its gap."""
    acquisitions = []
    for value in meta.get("before_dates", []):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Pre-flood acquisition dates need full timezone-aware timestamps; do not invent a time")
        acquisitions.append(stamp)
    verified = [row for row in rows if str(row.get("verified", "")).lower() == "true"]
    if not acquisitions or not verified:
        raise ValueError("Actual before acquisition timestamps and verified stage rows are required")
    pairs = [(abs((datetime.fromisoformat(row["time_ist"])-scene).total_seconds()), datetime.fromisoformat(row["time_ist"]), scene, row) for row in verified for scene in acquisitions]
    gap, reading_time, scene, selected = min(pairs, key=lambda item: (item[0], item[1], item[2]))
    return {"pre_flood_stage_m": float(selected["level_m"]), "pre_flood_stage_source_url": selected["source_url"],
            "pre_flood_stage_time_ist": reading_time.astimezone(IST).isoformat(), "pre_flood_matched_acquisition": scene.isoformat(),
            "pre_flood_stage_gap_hours": round(gap/3600, 6), "pre_flood_stage_gap_authorized": True,
            "pre_flood_stage_gap_authorization": "User instruction: use the closest cited reading to the actual pre-flood acquisition and explicitly flag the temporal gap.",
            "pre_flood_stage_kind": "closest sourced gauge proxy; not a time-matched observation" if gap else "time-matched sourced gauge observation",
            "pre_flood_stage_notes": selected.get("notes", ""),
            "pre_flood_acquisition_gaps_hours": [{"acquisition": item.isoformat(), "signed_stage_minus_scene_hours": round((reading_time-item).total_seconds()/3600,6)} for item in acquisitions]}


def replay_after_jobs(poll_seconds=30):
    from sim.jobs import state, update
    previous = None
    while True:
        jobs = {name: (state(name) or {}).get("status", "not started") for name in ("job_a", "job_b")}
        needed = [name for name in ("s1_water_before.tif", "s1_water_peak.tif", "s1_meta.json", "stage_2023.csv", "stage_2023_sources.json") if not (DERIVED / name).is_file()]
        if all(value == "complete" for value in jobs.values()) and not needed:
            break
        detail = f"Waiting for A/B: {jobs}; missing outputs: {', '.join(needed) or 'none'}"
        update("replay_2023", detail, status="running")
        if detail != previous: print(detail, flush=True); previous = detail
        time.sleep(poll_seconds)
    # Import only after dependencies finish, so a waiting job uses current code.
    from sim.replay import calibrate, compute_run
    from sim.export_web import export_all
    meta_path = DERIVED / "s1_meta.json"
    meta = read_json(meta_path)
    with (DERIVED / "stage_2023.csv").open() as stream: rows = list(csv.DictReader(stream))
    chosen = nearest_preflood_stage(meta, rows)
    if any(meta.get(key) != value for key, value in chosen.items()):
        meta.update(chosen); write_json(meta_path, meta)
    print(f"Pre-flood stage proxy {chosen['pre_flood_stage_m']:.2f} m at {chosen['pre_flood_stage_time_ist']}; acquisition gap {chosen['pre_flood_stage_gap_hours']:.3f} hours.", flush=True)
    update("replay_2023", "A/B complete; calibrating datum on real FABDEM and pre-flood satellite mask", status="running")
    calibration = calibrate()
    print(f"Datum offset {calibration['offset_m']:+.2f} m; pre-flood spatial IoU {calibration['before_iou']:.6f}; temporally mismatched stage proxy remains flagged.", flush=True)
    update("replay_2023", "Computing static July2023 replay from sourced/interpolated stages", status="running")
    compute_run()
    export_all()
    metrics = read_json(RUNS / "replay2023" / "metrics.json")
    update("replay_2023", f"Static replay complete; IoU={metrics['iou']}, precision={metrics['precision']}, recall={metrics['recall']}", outputs=[str(RUNS / "replay2023" / "manifest.json"), str(RUNS / "replay2023" / "metrics.json")])
    print(json.dumps(metrics), flush=True)
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verified published stages and automatic real-input replay")
    parser.add_argument("command", choices=("build", "replay-after-jobs"))
    args = parser.parse_args()
    try:
        build() if args.command == "build" else replay_after_jobs()
    except (ValueError, OSError, KeyError) as exc:
        print(f"Stage/replay job stopped: {exc}", flush=True)
        raise SystemExit(2)
