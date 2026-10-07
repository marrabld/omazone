"""Write platform build identity beside a release archive."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("platform", "version", "commit", "ref", "event", "run-url", "output"):
        parser.add_argument(f"--{field}", required=True)
    args = parser.parse_args()
    data = {
        "platform": args.platform,
        "version": args.version,
        "commit": args.commit,
        "source": f"https://github.com/marrabld/omazone/tree/{args.commit}",
        "workflow_run": args.run_url,
        "ref": args.ref,
        "event": args.event,
    }
    Path(args.output).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
