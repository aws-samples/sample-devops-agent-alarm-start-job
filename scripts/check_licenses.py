#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check that every runtime dependency (including transitive ones) uses an allowed licence.

Run inside an environment where scripts/requirements.txt is installed:

    python3 -m venv .venv && . .venv/bin/activate
    pip install -r scripts/requirements.txt
    python3 scripts/check_licenses.py

Exits non-zero if a dependency is missing, has no licence metadata, or uses a licence
outside ALLOWED.
"""
from __future__ import annotations

import pathlib
import re
import sys
from importlib import metadata

# Permissive licences only. Keep this set in sync with the Amazon pre-approved licence list for
# the engagement; anything not listed here is reported as REVIEW and fails the check.
ALLOWED = {
    "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "PSF-2.0", "Python-2.0",
}
# Map common free-text / classifier spellings to SPDX identifiers.
ALIASES = {
    "apache software license": "Apache-2.0", "apache 2.0": "Apache-2.0", "apache license 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0", "apache license, version 2.0": "Apache-2.0",
    "mit license": "MIT", "mit": "MIT",
    "bsd license": "BSD-3-Clause", "bsd": "BSD-3-Clause", "bsd-3-clause": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause", "isc license (iscl)": "ISC", "isc": "ISC",
    "python software foundation license": "PSF-2.0", "psf": "PSF-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0", "mpl-2.0": "MPL-2.0",
}
REQ = pathlib.Path(__file__).with_name("requirements.txt")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def licences(dist: metadata.Distribution) -> set[str]:
    meta = dist.metadata
    found = set()
    expr = meta.get("License-Expression")
    if expr:
        found.update(t for t in re.split(r"\s+(?:OR|AND)\s+|[()]", expr) if t.strip())
    for c in meta.get_all("Classifier") or []:
        if c.startswith("License :: OSI Approved :: "):
            found.add(c.rsplit("::", 1)[1].strip())
    # The free-text License field is only a fallback: it often holds prose such as "Dual License".
    lic = (meta.get("License") or "").strip()
    if not found and lic and len(lic) < 80 and "\n" not in lic:
        found.add(lic)
    return {ALIASES.get(x.strip().lower(), x.strip()) for x in found}


def requires(dist: metadata.Distribution) -> list[str]:
    out = []
    for r in dist.requires or []:
        if "extra ==" in r:
            continue
        out.append(re.split(r"[\s;<>=!~\[(]", r, maxsplit=1)[0])
    return out


def main() -> int:
    roots = [re.split(r"[\s;<>=!~\[]", line, maxsplit=1)[0]
             for line in REQ.read_text().splitlines() if line.strip() and not line.startswith("#")]
    seen, queue, failed = {}, list(roots), False
    while queue:
        name = queue.pop()
        if norm(name) in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            print(f"MISSING  {name} (install scripts/requirements.txt first)")
            seen[norm(name)] = None
            failed = True
            continue
        lic = licences(dist)
        seen[norm(name)] = lic
        ok = bool(lic) and lic <= ALLOWED
        failed |= not ok
        print(f"{'OK      ' if ok else 'REVIEW  '}{dist.metadata['Name']} {dist.version}: {', '.join(sorted(lic)) or 'no licence metadata'}")
        queue.extend(requires(dist))
    print(f"{len(seen)} packages checked; allowed: {', '.join(sorted(ALLOWED))}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
