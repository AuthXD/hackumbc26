"""Read-only runtime fingerprint. Run in Ubuntu before the benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def capture(command):
        return subprocess.check_output(command, text=True).strip()

    def digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    result = {"source": str(args.source), "revision": capture(["git", "-C", str(args.source), "rev-parse", "HEAD"]),
              "source_status": capture(["git", "-C", str(args.source), "status", "--porcelain"]),
              "model": str(args.model), "model_bytes": args.model.stat().st_size,
              "model_sha256": digest(args.model), "library_sha256": digest(args.library),
              "gpu": capture(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv"])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
