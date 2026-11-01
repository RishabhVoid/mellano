"""Phase-aware entry points. Never fabricate an unavailable dataset."""
import argparse


def main():
    parser = argparse.ArgumentParser(description="Otec terrain and simulation tools")
    parser.add_argument("command", choices=("data", "grid", "export", "calibrate", "replay", "score", "whatif", "record", "mp4", "export-metrics"))
    parser.add_argument('--run-id')
    args = parser.parse_args()
    try:
        if args.command == 'export-metrics':
            from sim.product import export_metrics
            if not args.run_id: parser.error('export-metrics requires --run-id')
            print(export_metrics(args.run_id))
        elif args.command == "data":
            from sim.data import download
            download()
        elif args.command == "grid":
            from sim.data import build_grid
            build_grid()
        elif args.command == "export":
            from sim.export_web import export_all
            export_all(force=True)
        elif args.command == "calibrate":
            from sim.replay import calibrate
            print(calibrate())
        elif args.command in ("replay", "score"):
            from sim.replay import compute_run
            from sim.export_web import export_all
            compute_run(); export_all()
        elif args.command == "whatif":
            import json
            from sim.dynamic import precompute
            from sim.export_web import export_all
            results = precompute(); export_all()
            print(json.dumps(results, indent=2))
        else:
            parser.exit(2, "Use ?scene=01_intro&record=1 for browser capture; smooth PNG/MP4 batch encoding remains in the recording phase.\n")
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__": main()
