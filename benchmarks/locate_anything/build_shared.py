"""Link the existing PIC CUDA archives into an isolated C API library. Run in Ubuntu."""
import argparse
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    link_dir = args.source / "build/examples/cli"
    link_file = link_dir / "CMakeFiles/locate-anything-cli.dir/link.txt"
    tokens = shlex.split(link_file.read_text())
    archive = tokens.index("../../liblocate_anything.a")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Keep the verified build's dependency ordering and CUDA paths. Export C API objects
    # even though a shared library has no main() to reference them.
    command = [tokens[0], "-shared", "-o", str(args.output.resolve()),
               "-Wl,--whole-archive", tokens[archive], "-Wl,--no-whole-archive"]
    command += [t for t in tokens[1:archive] if t.startswith("-Wl,-rpath,")]
    command += tokens[archive + 1:]
    subprocess.run(command, cwd=link_dir, check=True)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
