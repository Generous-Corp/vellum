#!/usr/bin/env python3
"""Side runner for the platform lanes: built artifacts of platforms this Mac
cannot host as a plug-in, read statically and, where a host exists, run.

The parity harness runs this as a tool (it is content-hashed into each side's
identity) and writes platform-observation.json, which lanes/platforms.py
compares. Subcommands:

  static    read every ELF, PE or WebAssembly image under --artifact without
            executing it: ELF machine, type, DT_NEEDED, DT_SONAME, defined
            dynamic exports and symbol-family counts (.symtab and .dynsym);
            PE machine, subsystem, characteristics, imported DLLs (including
            delay-loaded), exported names, Authenticode presence; WebAssembly
            imports and exports
  node      the static read of each WebAssembly module, plus Pulp's own Node
            host checks run against it (--check NAME=ARGV, {wasm} expands to
            the module): exit code and the PASS marker the check prints
  ios-sim   install a host app with its embedded AUv3 extension on a booted
            iOS simulator, record codesign verification of the app and every
            extension, the install result, the installed app's bundle record,
            the extension-point registration pluginkit reports, then uninstall
  android   install an APK on a running emulator or device, launch its
            activity, record the log lines of the app's tag and any fatal
            exception, whether the process is still alive after --settle
            seconds, then stop and uninstall; the JNI symbols each native
            library of the APK exports are read statically

Faults (calibration controls only) act on a copy under --work and never on
the artifact itself:

  elf-machine                 swap the ELF e_machine (x86_64 <-> aarch64)
  elf-rename-export:A=B       rename a dynamic symbol in .dynstr (same length)
  elf-rename-needed:A=B       rename a DT_NEEDED entry in .dynstr (same length)
  pe-machine                  swap the PE machine (x86_64 <-> arm64)
  pe-rename-export:A=B        rename an exported name (same length)
  pe-rename-import:A=B        rename an imported DLL name (same length)
  wasm-rename-export:A=B      rename a WebAssembly export (same length)
  drop:REL                    delete one path of the artifact
  unsigned-edit:REL           flip one byte of the Mach-O at REL, keeping its
                              old signature
  plist-set:REL:KEY.PATH=VAL  rewrite one Info.plist key (dotted path), then
                              keep the old signature (the seal now fails)
  resign-ad-hoc:REL           ad-hoc re-sign the bundle at REL inside out
                              (restores a seal after a plist-set)
  apk-drop:ENTRY              remove one entry of an APK and re-sign it with
                              the local debug key, so it still installs
  apk-rename-export:ENTRY:A=B rename a dynamic symbol of one native library
                              inside an APK (same length), then re-sign it

A host step that cannot reach its host (no booted simulator, no device) exits
77, which the harness records as blocked, never as a pass.
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
import time
import zipfile

SCHEMA = "pulp.platform-lane.observation.v1"
SKIP = 77
ELF_MACHINES = {3: "x86", 40: "arm", 62: "x86_64", 183: "aarch64", 243: "riscv"}
ELF_TYPES = {1: "rel", 2: "exec", 3: "dyn", 4: "core"}
PE_MACHINES = {0x14C: "x86", 0x8664: "x86_64", 0xAA64: "arm64", 0x1C4: "armnt", 0xA641: "arm64ec"}
PE_SUBSYSTEMS = {1: "native", 2: "windows-gui", 3: "windows-cui", 10: "efi-application"}
WASM_KINDS = {0: "func", 1: "table", 2: "memory", 3: "global", 4: "tag"}


def run(argv: list[str], timeout: int = 300, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, **kw)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cstr(data: bytes, offset: int) -> str:
    end = data.find(b"\0", offset)
    return data[offset:end if end >= 0 else len(data)].decode("utf-8", "replace")


# ── ELF ─────────────────────────────────────────────────────────────────────


def elf_sections(data: bytes) -> tuple[str, list[dict]]:
    wide, e = data[4] == 2, "<" if data[5] == 1 else ">"
    if wide:
        shoff, = struct.unpack_from(e + "Q", data, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from(e + "HHH", data, 0x3A)
        layout = e + "IIQQQQIIQQ"
    else:
        shoff, = struct.unpack_from(e + "I", data, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from(e + "HHH", data, 0x2E)
        layout = e + "IIIIIIIIII"
    sections = []
    for i in range(shnum):
        name, typ, flags, addr, off, size, link, info, align, entsize = struct.unpack_from(
            layout, data, shoff + i * shentsize)
        sections.append({"name_off": name, "type": typ, "offset": off, "size": size, "link": link,
                         "entsize": entsize})
    if 0 <= shstrndx < len(sections):
        base = sections[shstrndx]["offset"]
        for s in sections:
            s["name"] = cstr(data, base + s["name_off"])
    return e, sections


def elf_symbols(data: bytes, e: str, sections: list[dict], kind: int) -> list[dict]:
    wide = data[4] == 2
    out = []
    for s in sections:
        if s["type"] != kind or not s["entsize"]:
            continue
        strtab = sections[s["link"]]["offset"]
        for i in range(s["size"] // s["entsize"]):
            at = s["offset"] + i * s["entsize"]
            if wide:
                name, info, other, shndx = struct.unpack_from(e + "IBBH", data, at)
            else:
                name, = struct.unpack_from(e + "I", data, at)
                info, other, shndx = struct.unpack_from(e + "BBH", data, at + 12)
            out.append({"name": cstr(data, strtab + name), "bind": info >> 4, "vis": other & 3,
                        "defined": shndx != 0, "name_at": strtab + name})
    return out


def elf_dynamic(data: bytes, e: str, sections: list[dict]) -> tuple[list[dict], str | None]:
    wide = data[4] == 2
    needed, soname = [], None
    for s in sections:
        if s["type"] != 6:  # SHT_DYNAMIC
            continue
        strtab = sections[s["link"]]["offset"]
        step = 16 if wide else 8
        for i in range(s["size"] // step):
            tag, val = struct.unpack_from(e + ("qQ" if wide else "iI"), data, s["offset"] + i * step)
            if tag == 0:
                break
            if tag == 1:
                needed.append({"name": cstr(data, strtab + val), "name_at": strtab + val})
            elif tag == 14:
                soname = cstr(data, strtab + val)
    return needed, soname


def read_elf(data: bytes, families: dict[str, str]) -> dict:
    e = "<" if data[5] == 1 else ">"
    typ, machine = struct.unpack_from(e + "HH", data, 16)
    info = {"kind": "elf", "class": {1: 32, 2: 64}.get(data[4], 0),
            "machine": ELF_MACHINES.get(machine, str(machine)), "type": ELF_TYPES.get(typ, str(typ))}
    e, sections = elf_sections(data)
    dynsym = elf_symbols(data, e, sections, 11)
    symtab = elf_symbols(data, e, sections, 2)
    needed, soname = elf_dynamic(data, e, sections)
    info["needed"] = [n["name"] for n in needed]
    info["soname"] = soname
    info["exports"] = sorted({s["name"] for s in dynsym
                              if s["defined"] and s["bind"] in (1, 2) and s["vis"] in (0, 3) and s["name"]})
    names = {s["name"] for s in dynsym + symtab if s["name"]}
    info["symbol_table"] = "symtab" if symtab else "dynsym"
    info["families"] = {f: sum(1 for n in names if re.search(p, n)) for f, p in families.items()}
    return info


# ── PE ──────────────────────────────────────────────────────────────────────


def pe_layout(data: bytes) -> dict:
    lfanew, = struct.unpack_from("<I", data, 0x3C)
    if data[lfanew:lfanew + 4] != b"PE\0\0":
        raise ValueError("no PE signature")
    coff = lfanew + 4
    machine, nsec, _, _, _, optsize, chars = struct.unpack_from("<HHIIIHH", data, coff)
    opt = coff + 20
    magic, = struct.unpack_from("<H", data, opt)
    wide = magic == 0x20B
    subsystem, dllchars = struct.unpack_from("<HH", data, opt + 68)
    dd = opt + (112 if wide else 96)
    ndirs, = struct.unpack_from("<I", data, dd - 4)
    dirs = [struct.unpack_from("<II", data, dd + 8 * i) for i in range(min(ndirs, 16))]
    sections = []
    for i in range(nsec):
        at = opt + optsize + 40 * i
        vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", data, at + 8)
        sections.append((va, max(vsize, rawsize), rawptr))
    return {"machine_at": coff, "machine": machine, "chars": chars, "wide": wide, "subsystem": subsystem,
            "dllchars": dllchars, "dirs": dirs, "sections": sections}


def rva(layout: dict, addr: int) -> int:
    for va, size, raw in layout["sections"]:
        if va <= addr < va + size:
            return raw + addr - va
    raise ValueError(f"RVA {addr:#x} is in no section")


def pe_exports(data: bytes, layout: dict) -> list[dict]:
    if len(layout["dirs"]) < 1 or not layout["dirs"][0][0]:
        return []
    at = rva(layout, layout["dirs"][0][0])
    nnames, = struct.unpack_from("<I", data, at + 24)
    names_rva, = struct.unpack_from("<I", data, at + 32)
    table = rva(layout, names_rva) if nnames else 0
    out = []
    for i in range(nnames):
        name_at = rva(layout, struct.unpack_from("<I", data, table + 4 * i)[0])
        out.append({"name": cstr(data, name_at), "name_at": name_at})
    return out


def pe_imports(data: bytes, layout: dict) -> list[dict]:
    out = []
    if len(layout["dirs"]) > 1 and layout["dirs"][1][0]:
        at = rva(layout, layout["dirs"][1][0])
        while True:
            desc = struct.unpack_from("<IIIII", data, at)
            if not any(desc):
                break
            name_at = rva(layout, desc[3])
            out.append({"name": cstr(data, name_at), "name_at": name_at, "delay": False})
            at += 20
    if len(layout["dirs"]) > 13 and layout["dirs"][13][0]:
        at = rva(layout, layout["dirs"][13][0])
        while True:
            desc = struct.unpack_from("<8I", data, at)
            if not any(desc):
                break
            name_at = rva(layout, desc[1])
            out.append({"name": cstr(data, name_at), "name_at": name_at, "delay": True})
            at += 32
    return out


def read_pe(data: bytes, families: dict[str, str]) -> dict:
    layout = pe_layout(data)
    exports = pe_exports(data, layout)
    imports = pe_imports(data, layout)
    names = [x["name"] for x in exports]
    security = layout["dirs"][4] if len(layout["dirs"]) > 4 else (0, 0)
    return {"kind": "pe", "machine": PE_MACHINES.get(layout["machine"], hex(layout["machine"])),
            "dll": bool(layout["chars"] & 0x2000), "pe32plus": layout["wide"],
            "subsystem": PE_SUBSYSTEMS.get(layout["subsystem"], str(layout["subsystem"])),
            "dynamic_base": bool(layout["dllchars"] & 0x40), "nx_compat": bool(layout["dllchars"] & 0x100),
            "imports": [x["name"] for x in imports if not x["delay"]],
            "delay_imports": [x["name"] for x in imports if x["delay"]],
            "exports": sorted(names), "authenticode_present": security[1] > 0,
            "families": {f: sum(1 for n in names if re.search(p, n)) for f, p in families.items()}}


# ── WebAssembly ─────────────────────────────────────────────────────────────


def leb(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, at


def wasm_name(data: bytes, at: int) -> tuple[str, int, int]:
    n, at = leb(data, at)
    return data[at:at + n].decode("utf-8", "replace"), at, at + n


def wasm_limits(data: bytes, at: int) -> int:
    flags, at = leb(data, at)
    _, at = leb(data, at)
    if flags & 1:
        _, at = leb(data, at)
    return at


def wasm_sections(data: bytes):
    at = 8
    while at < len(data):
        sid = data[at]
        size, body = leb(data, at + 1)
        yield sid, body, body + size
        at = body + size


def read_wasm(data: bytes, families: dict[str, str]) -> dict:
    version, = struct.unpack_from("<I", data, 4)
    imports, exports, customs = [], [], []
    for sid, at, end in wasm_sections(data):
        if sid == 0:
            customs.append(wasm_name(data, at)[0])
        elif sid == 2:
            count, at = leb(data, at)
            for _ in range(count):
                module, _, at = wasm_name(data, at)
                field, _, at = wasm_name(data, at)
                kind = data[at]
                at += 1
                if kind == 0:
                    _, at = leb(data, at)
                elif kind == 1:
                    at = wasm_limits(data, at + 1)
                elif kind == 2:
                    at = wasm_limits(data, at)
                elif kind == 3:
                    at += 2
                elif kind == 4:
                    _, at = leb(data, at + 1)
                imports.append({"module": module, "name": field, "kind": WASM_KINDS.get(kind, str(kind))})
        elif sid == 7:
            count, at = leb(data, at)
            for _ in range(count):
                name, name_at, at = wasm_name(data, at)
                kind = data[at]
                _, at = leb(data, at + 1)
                exports.append({"name": name, "kind": WASM_KINDS.get(kind, str(kind))})
    names = [x["name"] for x in exports]
    return {"kind": "wasm", "version": version, "import_modules": sorted({i["module"] for i in imports}),
            "imports": len(imports), "exports": sorted(f"{x['kind']}:{x['name']}" for x in exports),
            "custom_sections": sorted(set(customs)),
            "families": {f: sum(1 for n in names if re.search(p, n)) for f, p in families.items()}}


def wasm_export_name_at(data: bytes, name: str) -> int:
    for sid, at, end in wasm_sections(data):
        if sid != 7:
            continue
        count, at = leb(data, at)
        for _ in range(count):
            n, name_at, at = wasm_name(data, at)
            _, at = leb(data, at + 1)
            if n == name:
                return name_at
    raise SystemExit(f"wasm export {name!r} not found")


# ── static observation ──────────────────────────────────────────────────────


def classify(head: bytes) -> str:
    if head[:4] == b"\x7fELF":
        return "elf"
    if head[:2] == b"MZ":
        return "pe"
    if head[:4] == b"\0asm":
        return "wasm"
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        return "macho"
    return "other"


def observe_static(root: Path, families: dict[str, str]) -> dict:
    files, images = [], {}
    paths = sorted(root.rglob("*")) if root.is_dir() else [root]
    for p in paths:
        rel = p.relative_to(root).as_posix() if root.is_dir() else p.name
        if p.is_symlink():
            files.append({"path": rel, "symlink": os.readlink(p)})
            continue
        if not p.is_file():
            continue
        data = p.read_bytes()
        kind = classify(data[:4])
        files.append({"path": rel, "size": len(data), "sha256": sha256_bytes(data), "kind": kind})
        try:
            if kind == "elf":
                images[rel] = read_elf(data, families)
            elif kind == "pe":
                images[rel] = read_pe(data, families)
            elif kind == "wasm":
                images[rel] = read_wasm(data, families)
        except (ValueError, struct.error, IndexError) as error:
            images[rel] = {"kind": kind, "unreadable": str(error)}
    return {"files": files, "images": images}


# ── faults ──────────────────────────────────────────────────────────────────


def copy_artifact(artifact: Path, work: Path) -> Path:
    dest = work / artifact.name
    if dest.exists() or dest.is_symlink():
        shutil.rmtree(dest) if dest.is_dir() and not dest.is_symlink() else dest.unlink()
    work.mkdir(parents=True, exist_ok=True)
    if artifact.is_dir():
        shutil.copytree(artifact, dest, symlinks=True)
    else:
        shutil.copy2(artifact, dest)
    return dest


def images_of(root: Path, kind: str) -> list[Path]:
    paths = sorted(root.rglob("*")) if root.is_dir() else [root]
    return [p for p in paths if p.is_file() and not p.is_symlink() and classify(p.read_bytes()[:4]) == kind]


def split_pair(spec: str) -> tuple[bytes, bytes]:
    old, new = spec.split("=", 1)
    if len(old) != len(new):
        raise SystemExit(f"rename {old!r} -> {new!r}: names must have the same length")
    return old.encode(), new.encode()


def patch_names(root: Path, kind: str, spec: str, locate) -> None:
    old, new = split_pair(spec)
    hits = 0
    for image in images_of(root, kind):
        data = bytearray(image.read_bytes())
        for at in locate(bytes(data), old.decode()):
            data[at:at + len(old)] = new
            hits += 1
        image.write_bytes(bytes(data))
    if not hits:
        raise SystemExit(f"{kind} rename: {old.decode()!r} not found in any image")


def elf_export_sites(data: bytes, name: str) -> list[int]:
    e, sections = elf_sections(data)
    return sorted({s["name_at"] for s in elf_symbols(data, e, sections, 11) if s["name"] == name})


def elf_needed_sites(data: bytes, name: str) -> list[int]:
    e, sections = elf_sections(data)
    return sorted({n["name_at"] for n in elf_dynamic(data, e, sections)[0] if n["name"] == name})


def pe_export_sites(data: bytes, name: str) -> list[int]:
    return [x["name_at"] for x in pe_exports(data, pe_layout(data)) if x["name"] == name]


def pe_import_sites(data: bytes, name: str) -> list[int]:
    return sorted({x["name_at"] for x in pe_imports(data, pe_layout(data)) if x["name"].lower() == name.lower()})


def wasm_export_sites(data: bytes, name: str) -> list[int]:
    try:
        return [wasm_export_name_at(data, name)]
    except SystemExit:
        return []


def swap_elf_machine(root: Path) -> None:
    images = images_of(root, "elf")
    if not images:
        raise SystemExit("elf-machine: no ELF image")
    for image in images:
        data = bytearray(image.read_bytes())
        e = "<" if data[5] == 1 else ">"
        current, = struct.unpack_from(e + "H", data, 18)
        struct.pack_into(e + "H", data, 18, 62 if current != 62 else 183)
        image.write_bytes(bytes(data))


def swap_pe_machine(root: Path) -> None:
    images = images_of(root, "pe")
    if not images:
        raise SystemExit("pe-machine: no PE image")
    for image in images:
        data = bytearray(image.read_bytes())
        at = pe_layout(bytes(data))["machine_at"]
        current, = struct.unpack_from("<H", data, at)
        struct.pack_into("<H", data, at, 0x8664 if current != 0x8664 else 0xAA64)
        image.write_bytes(bytes(data))


def set_plist_key(plist: Path, dotted: str, value: str) -> None:
    doc = plistlib.loads(plist.read_bytes())
    node, keys = doc, dotted.split(".")
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    node[keys[-1]] = value
    plist.write_bytes(plistlib.dumps(doc, fmt=plistlib.FMT_BINARY))


def resign_inside_out(bundle: Path) -> None:
    nested = sorted((p for p in bundle.rglob("*") if p.suffix in (".appex", ".framework")),
                    key=lambda p: -len(p.parts))
    for target in [*nested, bundle]:
        res = run(["codesign", "--force", "--sign", "-", "--timestamp=none",
                   "--preserve-metadata=identifier,entitlements", str(target)])
        if res.returncode != 0:
            raise SystemExit(f"codesign {target}: {res.stderr}")


def debug_keystore() -> Path:
    return Path(os.environ.get("ANDROID_DEBUG_KEYSTORE", Path.home() / ".android" / "debug.keystore"))


def build_tool(name: str) -> str:
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
               or Path.home() / "Library" / "Android" / "sdk")
    found = sorted((sdk / "build-tools").glob(f"*/{name}"))
    if not found:
        raise SystemExit(f"{name} not found under {sdk}/build-tools")
    return str(found[-1])


def apk_drop(apk: Path, entry: str, work: Path) -> None:
    stripped = work / "stripped.apk"
    with zipfile.ZipFile(apk) as src, zipfile.ZipFile(stripped, "w") as dst:
        names = src.namelist()
        if entry not in names:
            raise SystemExit(f"apk-drop: {entry} is not in {apk.name}")
        for info in src.infolist():
            if info.filename == entry or info.filename.startswith("META-INF/"):
                continue
            dst.writestr(info, src.read(info.filename), compress_type=info.compress_type)
    sign_apk(stripped, apk, work)


def sign_apk(unsigned: Path, apk: Path, work: Path) -> None:
    aligned = work / "aligned.apk"
    for argv in ([build_tool("zipalign"), "-f", "-p", "4", str(unsigned), str(aligned)],
                 [build_tool("apksigner"), "sign", "--ks", str(debug_keystore()), "--ks-pass", "pass:android",
                  "--key-pass", "pass:android", "--ks-key-alias", "androiddebugkey", "--out", str(apk),
                  str(aligned)]):
        res = run(argv)
        if res.returncode != 0:
            raise SystemExit(f"{Path(argv[0]).name} failed: {res.stderr[-400:]}")


def apk_rename_export(apk: Path, spec: str, work: Path) -> None:
    entry, pair = spec.split(":", 1)
    old, new = split_pair(pair)
    with zipfile.ZipFile(apk) as z:
        data = bytearray(z.read(entry))
    sites = elf_export_sites(bytes(data), old.decode())
    if not sites:
        raise SystemExit(f"apk-rename-export: {old.decode()} is not exported by {entry}")
    for at in sites:
        data[at:at + len(old)] = new
    rewritten = work / "rewritten.apk"
    with zipfile.ZipFile(apk) as src, zipfile.ZipFile(rewritten, "w") as dst:
        for info in src.infolist():
            if info.filename.startswith("META-INF/"):
                continue
            body = bytes(data) if info.filename == entry else src.read(info.filename)
            dst.writestr(info, body, compress_type=info.compress_type)
    sign_apk(rewritten, apk, work)


def apply_fault(fault: str, artifact: Path, work: Path) -> Path:
    dest = copy_artifact(artifact, work)
    if fault == "elf-machine":
        swap_elf_machine(dest)
    elif fault == "pe-machine":
        swap_pe_machine(dest)
    elif fault.startswith("elf-rename-export:"):
        patch_names(dest, "elf", fault.split(":", 1)[1], elf_export_sites)
    elif fault.startswith("elf-rename-needed:"):
        patch_names(dest, "elf", fault.split(":", 1)[1], elf_needed_sites)
    elif fault.startswith("pe-rename-export:"):
        patch_names(dest, "pe", fault.split(":", 1)[1], pe_export_sites)
    elif fault.startswith("pe-rename-import:"):
        patch_names(dest, "pe", fault.split(":", 1)[1], pe_import_sites)
    elif fault.startswith("wasm-rename-export:"):
        patch_names(dest, "wasm", fault.split(":", 1)[1], wasm_export_sites)
    elif fault.startswith("drop:"):
        target = dest / fault[len("drop:"):]
        if not (target.exists() or target.is_symlink()):
            raise SystemExit(f"drop: {target} does not exist")
        shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink()
    elif fault.startswith("unsigned-edit:"):
        image = dest / fault.split(":", 1)[1]
        data = bytearray(image.read_bytes())
        data[len(data) // 3] ^= 0x01
        image.write_bytes(bytes(data))
    elif fault.startswith("plist-set:"):
        rel, assignment = fault[len("plist-set:"):].split(":", 1)
        key, value = assignment.split("=", 1)
        set_plist_key(dest / rel, key, value)
    elif fault.startswith("resign-ad-hoc:"):
        resign_inside_out(dest / fault.split(":", 1)[1] if fault.split(":", 1)[1] else dest)
    elif fault.startswith("apk-rename-export:"):
        apk_rename_export(dest, fault.split(":", 1)[1], work)
    elif fault.startswith("apk-drop:"):
        apk_drop(dest, fault.split(":", 1)[1], work)
    else:
        raise SystemExit(f"unknown fault {fault}")
    return dest


def faulted(artifact: Path, faults: list[str], work: Path) -> Path:
    # Faults compose: each acts on the previous fault's copy.
    for i, fault in enumerate(faults):
        artifact = apply_fault(fault, artifact, work / f"fault-{i}")
    return artifact


# ── host checks ─────────────────────────────────────────────────────────────


def cmd_node(a: argparse.Namespace, artifact: Path, families: dict[str, str]) -> dict:
    obs = observe_static(artifact, families)
    node = shutil.which("node")
    if node is None:
        print("node is not on PATH", file=sys.stderr)
        sys.exit(SKIP)
    checks = {}
    for spec in a.check:
        name, argv = spec.split("=", 1)
        target, _, script = name.partition("@")
        wasm = artifact / target if artifact.is_dir() else artifact
        cmd = [node, *[x.replace("@WASM@", str(wasm)) for x in argv.split("\x1f")]]
        res = run(cmd, timeout=a.timeout, cwd=a.cwd or None)
        lines = (res.stdout + res.stderr).splitlines()
        checks[name] = {"exit": res.returncode,
                        "pass_lines": [ln.strip() for ln in lines if re.match(r"\s*(PASS|ALL CHECKS PASSED)", ln)],
                        "fail_lines": [ln.strip()[:200] for ln in lines if re.search(r"\b(FAIL|Error)\b", ln)][:5]}
    obs["node"] = {"version": run([node, "--version"]).stdout.strip(), "checks": checks}
    return obs


def simctl(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return run(["xcrun", "simctl", *args], timeout=timeout)


def codesign_verify(path: Path) -> dict:
    res = run(["codesign", "--verify", "--deep", "--strict", str(path)])
    return {"exit": res.returncode, "stderr_tail": res.stderr.replace(str(path.parent) + "/", "")[-300:]}


def cmd_ios(a: argparse.Namespace, artifact: Path) -> dict:
    listing = json.loads(simctl("list", "devices", "-j").stdout or "{}").get("devices", {})
    devices = [dict(d, runtime=rt) for rt, ds in listing.items() for d in ds]
    if a.device == "booted":
        # Exactly one booted simulator, or the run cannot say which one it used.
        booted = [d["udid"] for d in devices if d.get("state") == "Booted"]
        if len(booted) != 1:
            print(f"--device booted needs exactly one booted simulator, found {len(booted)}", file=sys.stderr)
            sys.exit(SKIP)
        a.device = booted[0]
    state = next((d.get("state") for d in devices if d.get("udid") == a.device), None)
    runtime = next((d["runtime"] for d in devices if d.get("udid") == a.device), None)
    if state != "Booted":
        print(f"simulator {a.device} is not booted ({state})", file=sys.stderr)
        sys.exit(SKIP)
    info = plistlib.loads((artifact / "Info.plist").read_bytes())
    app_id = info.get("CFBundleIdentifier")
    obs: dict = {"app": {"bundle_id": app_id, "codesign": codesign_verify(artifact)}, "extensions": {},
                 "simulator": {"udid": a.device, "runtime": runtime}}
    for appex in sorted((artifact / "PlugIns").glob("*.appex")):
        plist = plistlib.loads((appex / "Info.plist").read_bytes())
        ext = plist.get("NSExtension") or {}
        obs["extensions"][appex.name] = {
            "bundle_id": plist.get("CFBundleIdentifier"),
            "extension_point": ext.get("NSExtensionPointIdentifier"),
            "audio_components": [{k: c.get(k) for k in ("type", "subtype", "manufacturer", "name")}
                                 for c in (ext.get("NSExtensionAttributes") or {}).get("AudioComponents", [])],
            "codesign": codesign_verify(appex)}
    simctl("uninstall", a.device, app_id)
    res = simctl("install", a.device, str(artifact))
    obs["install"] = {"exit": res.returncode, "stderr_tail": res.stderr[-400:]}
    if res.returncode == 0:
        container = simctl("get_app_container", a.device, app_id).stdout.strip()
        obs["installed_app_plugins"] = sorted(p.name for p in (Path(container) / "PlugIns").glob("*.appex")) \
            if container else []
        registered = {}
        for name, ext in obs["extensions"].items():
            listing = run(["xcrun", "simctl", "spawn", a.device, "pluginkit", "-m", "-A", "-D", "-v",
                           "-i", ext["bundle_id"] or "-"])
            lines = [ln.strip() for ln in listing.stdout.splitlines() if ext["bundle_id"] and ext["bundle_id"] in ln]
            registered[name] = {"exit": listing.returncode, "lines": len(lines),
                                "listed": bool(lines)}
        obs["pluginkit"] = registered
    else:
        obs["installed_app_plugins"], obs["pluginkit"] = [], {}
    simctl("uninstall", a.device, app_id)
    return obs


def cmd_android(a: argparse.Namespace, artifact: Path) -> dict:
    adb = a.adb or shutil.which("adb") or str(Path.home() / "Library/Android/sdk/platform-tools/adb")

    def shell(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return run([adb, "-s", a.device, *args], timeout=timeout)

    if shell("get-state").stdout.strip() != "device":
        print(f"android device {a.device} is not attached", file=sys.stderr)
        sys.exit(SKIP)
    package, activity = a.activity.split("/", 1)
    with zipfile.ZipFile(artifact) as z:
        libs = sorted(n for n in z.namelist() if n.startswith("lib/") and n.endswith(".so"))
        # The JNI symbols each native library exports: a native method the app
        # declares resolves only against these (unless registered at run time).
        jni = {n: [x for x in read_elf(z.read(n), {})["exports"] if x.startswith(("Java_", "JNI_"))]
               for n in libs}
    obs: dict = {"package": package, "apk_native_libs": libs, "jni_exports": jni,
                 "abi": shell("shell", "getprop", "ro.product.cpu.abi").stdout.strip(),
                 "sdk": shell("shell", "getprop", "ro.build.version.sdk").stdout.strip()}
    shell("uninstall", package)
    shell("logcat", "-c")
    res = shell("install", "-r", str(artifact), timeout=300)
    obs["install"] = {"exit": res.returncode, "output_tail": (res.stdout + res.stderr).strip()[-300:]}
    if res.returncode == 0:
        start = shell("shell", "am", "start", "-W", "-n", a.activity)
        obs["start"] = {"exit": start.returncode, "ok": "Status: ok" in start.stdout or "Complete" in start.stdout}
        time.sleep(a.settle)
        obs["alive_after_settle"] = bool(shell("shell", "pidof", package).stdout.strip())
        log = shell("logcat", "-d", "-v", "brief", f"{a.tag}:V", "AndroidRuntime:E", "*:S").stdout
        lines = [re.sub(r"\(\s*\d+\)", "", ln).strip() for ln in log.splitlines() if ln and not ln.startswith("-")]
        obs["tag_lines"] = [ln for ln in lines if ln.startswith(("I/" + a.tag, "W/" + a.tag, "E/" + a.tag))]
        fatal = [ln for ln in lines if ln.startswith("E/AndroidRuntime")]
        obs["fatal"] = fatal[:12]
        shell("shell", "am", "force-stop", package)
    shell("uninstall", package)
    return obs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("mode", choices=("static", "node", "ios-sim", "android"))
    p.add_argument("--artifact", required=True)
    p.add_argument("--scan", required=True, help="JSON fixture: {\"families\": {name: regex}}")
    p.add_argument("--out", required=True)
    p.add_argument("--fault", action="append", default=[])
    p.add_argument("--work")
    p.add_argument("--check", action="append", default=[],
                   help="node mode: NAME=ARGV, ARGV separated by \\x1f; NAME is <wasm>@<check>")
    p.add_argument("--cwd")
    p.add_argument("--timeout", type=int, default=240)
    p.add_argument("--device")
    p.add_argument("--adb")
    p.add_argument("--activity")
    p.add_argument("--tag", default="Pulp")
    p.add_argument("--settle", type=float, default=6.0)
    a = p.parse_args()
    artifact = Path(a.artifact)
    if not artifact.exists():
        print(f"artifact not found: {artifact}", file=sys.stderr)
        return 2
    families = json.loads(Path(a.scan).read_text(encoding="utf-8"))["families"]
    work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="platform-lane-"))
    if a.fault:
        artifact = faulted(artifact, a.fault, work / "fault")
    if a.mode == "static":
        obs = observe_static(artifact, families)
    elif a.mode == "node":
        obs = cmd_node(a, artifact, families)
    elif a.mode == "ios-sim":
        obs = cmd_ios(a, artifact)
    else:
        obs = cmd_android(a, artifact)
    obs = {"schema": SCHEMA, "mode": a.mode, "artifact": artifact.name, **obs}
    Path(a.out).write_text(json.dumps(obs, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
