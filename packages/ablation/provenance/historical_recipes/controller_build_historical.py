"""Freeze and compile the CPU-free online runtime; never run a simulation.

Run with WSL python3. Every invocation requires a new --name and refuses an
existing build directory. All three C++ translation units are compiled from
the new snapshot; only the pinned generated Gemmini model objects are reused.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


PAPER_ROOT = Path("/home/Gemguan/work/codex_gemmini_paper_20261103")
TOOLCHAIN = Path("/home/Gemguan/work/chipyard/.conda-env")
PINNED_REUSED = {
    "verilated.o": "45b79fd232f7a5eb35fa44817e8f38c43cc5d80dd35c84862a4416046c6ec5e7",
    "verilated_threads.o": "a6410fb4c9a6eb0e1858c115e75784bb159f94a75fe93fa30ccf5afefe6ed731",
    "VGemmini__ALL.a": "4fe4918a2d2f618116de5f2c537d64c912500aab06930db21f4943d72b2832d2",
    "VGemmini___024root.h": "4d896e4a526983423a986cc59c28d72cb2d2af558cb5bf86c445b51866dcdae0",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hashes(paths: list[Path], root: Path | None = None) -> dict[str, str]:
    return {
        str(path.relative_to(root) if root else path): sha256(path)
        for path in sorted(paths)
    }


def files_below(root: Path) -> list[Path]:
    return [path for path in sorted(root.rglob("*")) if path.is_file()]


def snapshot_tree(source: Path, destination: Path) -> dict:
    before = hashes(files_below(source), source)
    shutil.copytree(source, destination)
    copied = hashes(files_below(destination), destination)
    after = hashes(files_below(source), source)
    if before != copied or before != after:
        raise RuntimeError(f"source changed while freezing {source}")
    return {"origin": str(source), "snapshot": str(destination), "sha256": copied}


def snapshot_files(paths: list[Path], source: Path, destination: Path) -> dict:
    before = hashes(paths, source)
    for path in paths:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    copied = hashes(files_below(destination), destination)
    after = hashes(paths, source)
    if before != copied or before != after:
        raise RuntimeError(f"headers changed while freezing {source}")
    return {"origin": str(source), "snapshot": str(destination), "sha256": copied}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="new immutable build name")
    parser.add_argument(
        "--build-root", type=Path,
        default=PAPER_ROOT / "consumer_gate/online_runtime/builds",
    )
    parser.add_argument(
        "--reused", type=Path, default=PAPER_ROOT / "online_service/reused_v2",
    )
    parser.add_argument(
        "--mapper", type=Path,
        default=PAPER_ROOT / "online_portable_check_v1/platform/mapper",
    )
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("run this script with WSL python3")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.name):
        parser.error("--name must start with an alphanumeric and contain only . _ - or alphanumerics")
    if not args.build_root.is_absolute():
        parser.error("--build-root must be absolute")

    here = Path(__file__).resolve().parent
    source = here / "source"
    compiler = TOOLCHAIN / "bin/x86_64-conda-linux-gnu-c++"
    verilator_include = TOOLCHAIN / "share/verilator/include"
    reused = args.reused.resolve()
    mapper = args.mapper.resolve()
    build = args.build_root.resolve() / args.name
    # Exclusive creation protects earlier successes, failures, logs, and inputs.
    build.mkdir(parents=True, exist_ok=False)
    manifest_path = build / "manifest.json"
    record = {
        "schema_version": 1,
        "scope": "compile only; no simulator execution; no RTL generation or compilation",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "build": str(build),
        "status": "preparing",
        "exit_code": None,
        "rtl_modified": False,
        "cpu_instantiated": False,
        "controller_sources_recompiled": ["driver.cpp", "online_main.cpp", "engine/verilated_engine.cpp"],
        "commands": [],
        "snapshots": {},
        "runtime_environment": {"LD_LIBRARY_PATH_prefix": str(TOOLCHAIN / "lib")},
    }

    def save() -> None:
        manifest_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(TOOLCHAIN / "lib") + (
        ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
    )

    def run_step(label: str, argv: list[str]) -> subprocess.CompletedProcess:
        result = subprocess.run(argv, cwd=build, env=env, capture_output=True, text=True)
        stdout_path = build / f"{label}.stdout.log"
        stderr_path = build / f"{label}.stderr.log"
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        record["commands"].append({
            "label": label, "argv": argv, "cwd": str(build),
            "exit_code": result.returncode,
            "stdout": str(stdout_path), "stderr": str(stderr_path),
        })
        save()
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, argv, result.stdout, result.stderr)
        return result

    save()
    try:
        record["script"] = {"origin": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__))}
        shutil.copyfile(__file__, build / "build.py")
        record["reused_inputs"] = hashes([reused / name for name in PINNED_REUSED])
        for name, expected in PINNED_REUSED.items():
            if record["reused_inputs"][str(reused / name)] != expected:
                raise RuntimeError(f"pinned generated-model hash mismatch: {name}")

        record["snapshots"]["source"] = snapshot_tree(source, build / "source")
        mapper_headers = [mapper / "gemmini_host.h", mapper / "host_rocc.h"]
        mapper_headers += files_below(mapper / "vendor/include")
        record["snapshots"]["mapper"] = snapshot_files(mapper_headers, mapper, build / "mapper")
        generated_headers = [path for path in sorted(reused.iterdir()) if path.suffix in {".h", ".hpp"}]
        record["snapshots"]["generated_headers"] = snapshot_files(
            generated_headers, reused, build / "generated_headers",
        )
        verilator_headers = [
            path for path in files_below(verilator_include) if path.suffix in {".h", ".hpp"}
        ]
        record["verilator_headers"] = hashes(verilator_headers)
        record["compiler"] = {"path": str(compiler), "sha256": sha256(compiler)}
        version = run_step("compiler_version", [str(compiler), "--version"])
        record["compiler"]["version"] = version.stdout
        record["status"] = "compiling"
        save()

        include_directories = [
            build / "source", build / "source/engine", build / "mapper",
            build / "mapper/vendor", build / "generated_headers",
            verilator_include, verilator_include / "vltstd",
        ]
        common = [str(compiler), "-O1", "-std=gnu++17", "-pthread"]
        for directory in include_directories:
            common += ["-I", str(directory)]
        translations = [
            ("driver", "driver.cpp"),
            ("online_main", "online_main.cpp"),
            ("verilated_engine", "engine/verilated_engine.cpp"),
        ]
        objects = []
        for name, relative_source in translations:
            obj = build / f"{name}.o"
            command = common + [
                "-MMD", "-MF", str(build / f"{name}.d"),
                "-c", str(build / "source" / relative_source), "-o", str(obj),
            ]
            run_step(f"compile_{name}", command)
            objects.append(obj)

        binary = build / "online_service"
        link = [str(compiler), "-pthread"] + [str(path) for path in objects]
        link += [str(reused / name) for name in ("verilated.o", "verilated_threads.o", "VGemmini__ALL.a")]
        link += ["-o", str(binary)]
        run_step("link", link)
        if record["reused_inputs"] != hashes([reused / name for name in PINNED_REUSED]):
            raise RuntimeError("generated model inputs changed during compilation")
        if record["verilator_headers"] != hashes(verilator_headers):
            raise RuntimeError("Verilator headers changed during compilation")
        record["compiled_objects"] = hashes(objects)
        record["dependency_files"] = hashes([build / f"{name}.d" for name, _ in translations])
        dependencies = set()
        for name, _ in translations:
            depfile = (build / f"{name}.d").read_text().replace("\\\n", "")
            dependencies.update(Path(path) for path in shlex.split(depfile.split(":", 1)[1]))
        record["compiler_reported_project_dependencies"] = hashes(sorted(dependencies))
        record["binary"] = {"path": str(binary), "sha256": sha256(binary), "bytes": binary.stat().st_size}
        record["status"] = "built"
        record["exit_code"] = 0
    except Exception as error:
        record["status"] = "failed"
        record["exit_code"] = error.returncode if isinstance(error, subprocess.CalledProcessError) else 2
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        record["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save()

    print(json.dumps({
        "status": record["status"], "exit_code": record["exit_code"],
        "build": str(build), "manifest": str(manifest_path),
        "binary": record.get("binary"), "error": record.get("error"),
    }, indent=2))
    return record["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
