#!/usr/bin/env python3
"""Side runner for the artifact lanes: the static shape of one built artifact.

The parity harness runs this as a tool (it is content-hashed into each side's
identity). It reads one artifact without executing it and writes
artifact-observation.json, which lanes/artifact.py compares:

  every file    relative path, size, sha256, container kind (Mach-O, ELF,
                WebAssembly or other)
  Mach-O        architectures (lipo), the build platform of each slice
                (LC_BUILD_VERSION), the load commands of each slice
  ELF           machine and class from the header
  main image    per architecture (per machine for ELF), symbol counts for each symbol family the
                scan fixture names (--scan), and the image's load commands
  bundle        Info.plist keys, the main executable, codesign verification of
                the whole bundle and of each slice of the main image alone

An artifact is a macOS/iOS bundle directory, an APK or a directory of build
outputs (web), or a single file.

Faults (calibration controls only) act on a copy under --work and never on
the artifact itself: --fault wrong-arch (drop the last slice of the main
image, or add a foreign one when it has a single slice; an ELF image gets a
foreign machine), --fault drop:<rel> (delete one bundle path),
--fault unsigned-edit (flip one byte in every slice of the main image,
keeping its old signature), --fault nested-wrong-arch:<rel> (the wrong-arch
rewrite applied to one bundled Mach-O).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

SCHEMA = "pulp.artifact-lane.observation.v1"
MACHO_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf",
                b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce", b"\xbf\xba\xfe\xca"}
ELF_MACHINES = {3: "x86", 40: "arm", 62: "x86_64", 183: "aarch64", 243: "riscv"}
FOREIGN = {"arm64": "x86_64", "x86_64": "arm64"}


def run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def kind_of(head: bytes) -> str:
    if head[:4] in MACHO_MAGICS:
        return "macho"
    if head[:4] == b"\x7fELF":
        return "elf"
    if head[:4] == b"\0asm":
        return "wasm"
    return "other"


def macho_archs(path: Path) -> list[str]:
    out = run(["lipo", "-archs", str(path)])
    return sorted(out.stdout.split()) if out.returncode == 0 else []


def build_platforms(path: Path, arch: str) -> list[int]:
    text = run(["otool", "-arch", arch, "-l", str(path)]).stdout
    platforms = [int(m.group(1)) for m in re.finditer(r"cmd LC_BUILD_VERSION\n.*?\n\s*platform (\d+)", text)]
    legacy = {"LC_VERSION_MIN_MACOSX": 1, "LC_VERSION_MIN_IPHONEOS": 2}
    platforms += [v for k, v in legacy.items() if k in text]
    return sorted(set(platforms))


def load_commands(path: Path, arch: str) -> list[str]:
    lines = run(["otool", "-arch", arch, "-L", str(path)]).stdout.splitlines()[1:]
    return [line.strip().split(" (")[0] for line in lines if line.strip()]


def elf_header(data: bytes) -> dict:
    cls = {1: 32, 2: 64}.get(data[4], 0)
    endian = "<" if data[5] == 1 else ">"
    machine = struct.unpack_from(endian + "H", data, 18)[0]
    return {"class": cls, "machine": ELF_MACHINES.get(machine, str(machine))}


def symbols(path: Path, arch: str) -> list[str]:
    return run(["nm", "-arch", arch, "-j", str(path)]).stdout.splitlines()


def family_counts(names: list[str], families: dict[str, str]) -> dict[str, int]:
    return {family: sum(1 for n in names if re.search(pattern, n)) for family, pattern in families.items()}


def main_image(root: Path, plist: dict | None) -> Path | None:
    if not root.is_dir():
        return root if root.is_file() else None
    for base in (root / "Contents" / "MacOS", root):
        name = (plist or {}).get("CFBundleExecutable")
        if name and (base / name).is_file():
            return base / name
    return None


def read_plist(root: Path) -> tuple[dict | None, str | None]:
    for rel in ("Contents/Info.plist", "Info.plist"):
        path = root / rel
        if path.is_file():
            try:
                return plistlib.loads(path.read_bytes()), rel
            except (plistlib.InvalidFileException, ValueError) as e:
                return {"__unreadable__": str(e)}, rel
    return None, None


def observe(artifact: Path, families: dict[str, str], unpacked: Path | None) -> dict:
    root = unpacked or artifact
    plist, plist_rel = read_plist(root) if root.is_dir() else (None, None)
    files = []
    paths = sorted(p for p in root.rglob("*")) if root.is_dir() else [root]
    for p in paths:
        rel = p.relative_to(root).as_posix() if root.is_dir() else p.name
        if p.is_symlink():
            files.append({"path": rel, "symlink": os.readlink(p)})
            continue
        if not p.is_file():
            continue
        data = p.read_bytes()
        entry = {"path": rel, "size": len(data), "sha256": sha256_bytes(data), "kind": kind_of(data[:4])}
        if entry["kind"] == "macho":
            archs = macho_archs(p)
            entry["archs"] = archs
            entry["platforms"] = {a: build_platforms(p, a) for a in archs}
        elif entry["kind"] == "elf":
            entry.update(elf_header(data))
        files.append(entry)
    image = main_image(root, plist)
    slices = {}
    image_kind = kind_of(image.read_bytes()[:4]) if image is not None else None
    if image_kind == "macho":
        for arch in macho_archs(image):
            slices[arch] = {"families": family_counts(symbols(image, arch), families),
                            "load_commands": load_commands(image, arch)}
    elif image_kind == "elf":
        # One machine per ELF image; nm (llvm-nm) reads its symbol table. The
        # dynamic section is not parsed, so no load commands are recorded.
        names = run(["nm", "-j", str(image)]).stdout.splitlines()
        slices[elf_header(image.read_bytes()[:64])["machine"]] = {
            "families": family_counts(names, families), "load_commands": []}
    codesign = None
    if root.is_dir() and plist is not None:
        cs = run(["codesign", "--verify", "--deep", "--strict", str(root)])
        codesign = {"exit": cs.returncode, "stderr_tail": cs.stderr[-400:]}
    codesign_image = None
    if image is not None and kind_of(image.read_bytes()[:4]) == "macho":
        # Inside its bundle codesign checks the bundle seal too, so the image
        # is verified as a lone file: only its own (linker) signature counts.
        with tempfile.TemporaryDirectory() as tmp:
            lone = Path(tmp) / image.name
            shutil.copy2(image, lone)
            codesign_image = {}
            for arch in macho_archs(lone):
                cs = run(["codesign", "--verify", "--strict", "--arch", arch, str(lone)])
                codesign_image[arch] = {"exit": cs.returncode,
                                        "stderr_tail": cs.stderr.replace(str(lone), image.name)[-400:]}
    keys = ("CFBundleExecutable", "CFBundleIdentifier", "CFBundlePackageType", "CFBundleSupportedPlatforms",
            "LSMinimumSystemVersion", "MinimumOSVersion")
    return {"schema": SCHEMA, "artifact": artifact.name, "plist_path": plist_rel,
            "plist": None if plist is None else {k: plist.get(k) for k in keys if k in plist} | (
                {"NSExtensionPointIdentifier": (plist.get("NSExtension") or {}).get("NSExtensionPointIdentifier")}
                if "NSExtension" in plist else {}) | (
                {"unreadable": plist["__unreadable__"]} if "__unreadable__" in plist else {}),
            "main_image": None if image is None else (image.relative_to(root).as_posix() if root.is_dir()
                                                      else image.name),
            "files": files, "slices": slices, "codesign": codesign,
            "codesign_image": codesign_image}


# ── faults ──────────────────────────────────────────────────────────────────


def copy_artifact(artifact: Path, work: Path) -> Path:
    dest = work / artifact.name
    if dest.exists():
        shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
    work.mkdir(parents=True, exist_ok=True)
    if artifact.is_dir():
        shutil.copytree(artifact, dest, symlinks=True)
    else:
        shutil.copy2(artifact, dest)
    return dest


def stub_slice(arch: str, work: Path) -> Path:
    src = work / "stub.c"
    src.write_text("int pulp_artifact_lane_stub(void) { return 0; }\n", encoding="utf-8")
    out = work / f"stub-{arch}.dylib"
    if run(["clang", "-arch", arch, "-dynamiclib", str(src), "-o", str(out)]).returncode != 0:
        raise SystemExit(f"could not build a {arch} stub slice")
    return out


def rewrite_slices(image: Path, work: Path) -> None:
    archs = macho_archs(image)
    if len(archs) > 1:
        res = run(["lipo", str(image), "-remove", archs[-1], "-output", str(image)])
    else:
        foreign = FOREIGN.get(archs[0], "x86_64") if archs else "x86_64"
        res = run(["lipo", "-create", str(image), str(stub_slice(foreign, work)), "-output", str(image)])
    if res.returncode != 0:
        raise SystemExit(f"lipo failed on {image}: {res.stderr}")


def slice_ranges(data: bytes) -> list[tuple[int, int]]:
    """(offset, size) of every architecture slice; a thin image is one slice."""
    magic = data[:4]
    if magic not in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        return [(0, len(data))]
    count = struct.unpack_from(">I", data, 4)[0]
    wide = magic == b"\xca\xfe\xba\xbf"
    entry, layout = (32, ">iiQQI") if wide else (20, ">iiIII")
    return [struct.unpack_from(layout, data, 8 + i * entry)[2:4] for i in range(count)]


def apply_fault(fault: str, artifact: Path, work: Path) -> Path:
    dest = copy_artifact(artifact, work)
    plist, _ = read_plist(dest) if dest.is_dir() else (None, None)
    image = main_image(dest, plist)
    if fault == "wrong-arch":
        if image is None:
            raise SystemExit("wrong-arch: no main image")
        data = bytearray(image.read_bytes())
        if kind_of(bytes(data[:4])) == "elf":
            endian = "<" if data[5] == 1 else ">"
            current = struct.unpack_from(endian + "H", data, 18)[0]
            struct.pack_into(endian + "H", data, 18, 62 if current != 62 else 183)
            image.write_bytes(bytes(data))
        elif kind_of(bytes(data[:4])) == "wasm":
            image.write_bytes(b"\0asn" + bytes(data[4:]))
        else:
            rewrite_slices(image, work)
    elif fault.startswith("drop:"):
        target = dest / fault[len("drop:"):]
        if not (target.exists() or target.is_symlink()):
            raise SystemExit(f"drop: {target} does not exist")
        shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink()
    elif fault == "unsigned-edit":
        data = bytearray(image.read_bytes())
        for offset, size in slice_ranges(bytes(data)):
            data[offset + size // 3] ^= 0x01
        image.write_bytes(bytes(data))
    elif fault.startswith("nested-wrong-arch:"):
        target = dest / fault[len("nested-wrong-arch:"):]
        if not target.is_file() or kind_of(target.read_bytes()[:4]) != "macho":
            raise SystemExit(f"nested-wrong-arch: {target} is not a Mach-O")
        rewrite_slices(target, work)
    else:
        raise SystemExit(f"unknown fault {fault}")
    return dest


def unpack(artifact: Path, work: Path) -> Path | None:
    if artifact.is_file() and artifact.suffix in (".apk", ".aar", ".zip"):
        dest = work / (artifact.name + ".unpacked")
        if dest.exists():
            shutil.rmtree(dest)
        with zipfile.ZipFile(artifact) as z:
            z.extractall(dest)
        return dest
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--artifact", required=True)
    p.add_argument("--scan", required=True, help="JSON fixture: {\"families\": {name: regex}}")
    p.add_argument("--out", required=True)
    p.add_argument("--fault", action="append", default=[])
    p.add_argument("--work")
    a = p.parse_args()
    artifact = Path(a.artifact)
    if not artifact.exists():
        print(f"artifact not found: {artifact}", file=sys.stderr)
        return 2
    families = json.loads(Path(a.scan).read_text(encoding="utf-8"))["families"]
    work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="artifact-lane-"))
    for fault in a.fault:
        artifact = apply_fault(fault, artifact, work / "fault")
    obs = observe(artifact, families, unpack(artifact, work))
    Path(a.out).write_text(json.dumps(obs, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
