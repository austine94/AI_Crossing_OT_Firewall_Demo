#!/usr/bin/env python3
"""Create deterministic source and evidence release archives."""

import argparse
import hashlib
import json
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXED_TIME = (2026, 10, 24, 0, 0, 0)
SOURCE_DIRS = (
    "boundaryline",
    "demo_notes",
    "docs",
    "fixtures",
    "policy",
    "scripts",
    "services",
    "tests",
    "Build_Plan",
)
SOURCE_FILES = (
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "compose.yaml",
    "Dockerfile.firewall",
    "Dockerfile.service",
    "Makefile",
    "pyproject.toml",
    "README.md",
    "requirements.lock",
    "uv.lock",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_paths() -> list[Path]:
    paths = [ROOT / name for name in SOURCE_FILES]
    for directory in SOURCE_DIRS:
        paths.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    return sorted(set(paths), key=lambda path: path.relative_to(ROOT).as_posix())


def evidence_paths() -> list[Path]:
    return sorted(
        (
            path
            for path in (ROOT / "evidence").rglob("*")
            if path.is_file() and path.name != ".gitkeep"
        ),
        key=lambda path: path.relative_to(ROOT).as_posix(),
    )


def write_archive(output: Path, paths: list[Path], *, prefix: str) -> str:
    manifest = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            manifest[relative] = {"sha256": sha256(data), "size": len(data)}
            info = zipfile.ZipInfo(f"{prefix}/{relative}", FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
        manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        info = zipfile.ZipInfo(f"{prefix}/MANIFEST.sha256.json", FIXED_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, manifest_data)
    return sha256(output.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--allow-empty-evidence", action="store_true")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    evidence = evidence_paths()
    if not evidence and not args.allow_empty_evidence:
        parser.error("no evidence files found; run scripts/verify_live_demo.py first")

    source_name = f"boundaryline-source-{version}.zip"
    evidence_name = f"boundaryline-evidence-{version}.zip"
    source_hash = write_archive(
        args.output_dir / source_name, source_paths(), prefix=f"boundaryline-{version}"
    )
    evidence_hash = write_archive(
        args.output_dir / evidence_name, evidence, prefix=f"boundaryline-evidence-{version}"
    )
    checksums = f"{source_hash}  {source_name}\n{evidence_hash}  {evidence_name}\n"
    (args.output_dir / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    print(checksums, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
