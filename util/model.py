#!/usr/bin/env python3

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import signal

# ------------------- Globals -------------------
HOME = Path.home()
TOKENS_FILE = HOME / ".model-tokens.json"
INDEX_FILE = HOME / ".model-index.json"

CHUNK_SIZE = 1024 * 64
PRINT_LOCK = Lock()

# ------------------- SIGINT handling -------------------
def _sigint_handler(signum, frame):
    print("\nAborting downloads (Ctrl-C)", file=sys.stderr)
    os._exit(130)  # immediate exit, kills threads

signal.signal(signal.SIGINT, _sigint_handler)

# ------------------- Index handling -------------------
def load_index():
    if not INDEX_FILE.exists():
        return {"models": {}, "groups": {}}
    with INDEX_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_index(data):
    with INDEX_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def load_tokens():
    if not TOKENS_FILE.exists():
        return {}
    with TOKENS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)

# ------------------- Utilities -------------------
def ensure_parent_dir(path):
    path.mkdir(parents=True, exist_ok=True)

def get_filename_from_response(response, url):
    cd = response.headers.get("Content-Disposition")
    if cd:
        for part in cd.split(";"):
            part = part.strip()
            if part.lower().startswith("filename="):
                return part.split("=", 1)[1].strip('"')
    parsed = urllib.parse.urlparse(url)
    return os.path.basename(parsed.path) or "downloaded.file"

def format_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"

# ------------------- Group expansion -------------------
def expand_names(index, names):
    expanded = []
    seen = set()
    visiting = set()

    def expand(name):
        if name in visiting:
            sys.exit(f"Error: cyclic group reference detected at '{name}'")
        if name in index["groups"]:
            visiting.add(name)
            members = index["groups"][name]
            print(f"Group '{name}' expanded to: {' '.join(members)}")
            for m in members:
                expand(m)
            visiting.remove(name)
        else:
            expanded.append(name)

    for n in names:
        expand(n)

    # Deduplicate preserving order
    result = []
    for n in expanded:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result

# ------------------- Download -------------------
def download_file(name, url, subdirectory, force=False, unzip=False):
    tokens = load_tokens()
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname

    req = urllib.request.Request(url)
    if hostname and hostname in tokens:
        req.add_unredirected_header("Authorization", f"Bearer {tokens[hostname]}")

    ensure_parent_dir(subdirectory)

    with urllib.request.urlopen(req) as response:
        filename = get_filename_from_response(response, url)
        target = subdirectory / filename

        if target.exists() and not force:
            with PRINT_LOCK:
                print(f"[{name}] Skipping (exists): {target}")
            return target

        total = response.headers.get("Content-Length")
        total = int(total) if total else None
        downloaded = 0

        with open(target, "wb") as out:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                with PRINT_LOCK:
                    if total:
                        pct = downloaded / total * 100
                        print(f"\r[{name}] {pct:6.2f}% ({format_bytes(downloaded)} / {format_bytes(total)})", end="", flush=True)
                    else:
                        print(f"\r[{name}] {format_bytes(downloaded)}", end="", flush=True)

        with PRINT_LOCK:
            print(f"\n[{name}] Done → {target}")

    # ------------------- unzip if requested -------------------
    if unzip:
        if not zipfile.is_zipfile(target):
            print(f"[{name}] WARNING: file is not a valid zip: {target}", file=sys.stderr)
        else:
            with PRINT_LOCK:
                print(f"[{name}] Unzipping {target} ...")
            with zipfile.ZipFile(target, 'r') as zip_ref:
                zip_ref.extractall(subdirectory)
            target.unlink()  # remove the original zip
            with PRINT_LOCK:
                print(f"[{name}] Unzipped and removed {target}")

    return target

# ------------------- Commands -------------------
def cmd_token(args):
    tokens = load_tokens()
    tokens[args.hostname] = args.token
    with TOKENS_FILE.open("w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)
    print(f"Stored token for {args.hostname}")

def cmd_group(args):
    index = load_index()
    if not args.models:
        if args.group not in index["groups"]:
            sys.exit(f"Error: group '{args.group}' does not exist")
        del index["groups"][args.group]
        save_index(index)
        print(f"Deleted group '{args.group}'")
        return
    for m in args.models:
        if m not in index["models"] and m not in index["groups"]:
            sys.exit(f"Error: unknown model or group '{m}'")
    index["groups"][args.group] = args.models
    save_index(index)
    print(f"Saved group '{args.group}': {' '.join(args.models)}")

def cmd_dl(args):
    index = load_index()
    seen = set()
    names = [n for n in args.names if not (n in seen or seen.add(n))]
    names = expand_names(index, names)

    # Single download with -u/-d
    if args.url or args.subdirectory:
        if len(names) != 1:
            sys.exit("Error: -u / -d require exactly one model name")
        if not args.url or not args.subdirectory:
            sys.exit("Error: -u and -d must be used together")
        if args.jobs is not None:
            sys.exit("Error: -j cannot be used with -u / -d")

        # Save model info including unzip flag
        index["models"][names[0]] = {
            "url": args.url,
            "subdirectory": args.subdirectory,
            "unzip": args.unzip
        }
        save_index(index)

        download_file(
            names[0],
            args.url,
            Path(args.subdirectory),
            force=args.force,
            unzip=args.unzip
        )
        return

    # Download models from index (possibly multiple)
    tasks = []
    for name in names:
        if name not in index["models"]:
            sys.exit(f"Error: no entry for model '{name}'")
        m = index["models"][name]
        tasks.append((
            name,
            m["url"],
            Path(m["subdirectory"]),
            args.force,
            m.get("unzip", False)  # automatically apply stored unzip flag
        ))

    jobs = args.jobs if args.jobs is not None else (4 if len(tasks) > 1 else 1)

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = [executor.submit(download_file, *t) for t in tasks]
        for f in as_completed(futures):
            f.result()

def cmd_list(args):
    index = load_index()
    if args.kind == "token":
        for h in sorted(load_tokens()):
            print(h)
    elif args.kind == "dl":
        for name, m in sorted(index["models"].items()):
            print(f"{name}\t{m['subdirectory']}\tunzip={m.get('unzip', False)}")
    elif args.kind == "group":
        for g, members in sorted(index["groups"].items()):
            print(f"{g}\t{' '.join(members)}")

# ------------------- Main -------------------
def main():
    parser = argparse.ArgumentParser(prog="model")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("token")
    p.add_argument("hostname")
    p.add_argument("token")
    p.set_defaults(func=cmd_token)

    p = sub.add_parser("group")
    p.add_argument("-g", "--group", required=True)
    p.add_argument("models", nargs="*")
    p.set_defaults(func=cmd_group)

    p = sub.add_parser("dl")
    p.add_argument("names", nargs="+")
    p.add_argument("-u", "--url")
    p.add_argument("-d", "--subdirectory")
    p.add_argument("-j", "--jobs", type=int)
    p.add_argument("-f", "--force", action="store_true")
    p.add_argument("-z", "--unzip", action="store_true", help="Unzip downloaded zip file in place")
    p.set_defaults(func=cmd_dl)

    p = sub.add_parser("list")
    p.add_argument("kind", choices=["token", "dl", "group"])
    p.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()