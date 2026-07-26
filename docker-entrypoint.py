#!/usr/local/bin/python
"""Prepare the data directory, then drop privileges before starting the app.

Hosted platforms mount volumes owned by root and offer no way to change that, so
an image that simply declares `USER` can never write to its own volume. Starting
as root, fixing ownership and then dropping to an unprivileged uid is the only
approach that works both there and on a plain bind mount — and the application
itself still never runs as root.

If the container is already started as a non-root user, everything here is
skipped and the command runs as-is.
"""

from __future__ import annotations

import contextlib
import os
import sys

UID = 10001
GID = 10001


def prepare(data_dir: str) -> None:
    os.makedirs(data_dir, exist_ok=True)
    os.chown(data_dir, UID, GID)
    for root, dirs, files in os.walk(data_dir):
        for name in dirs + files:
            # A single unreadable leftover must not stop the app from starting.
            with contextlib.suppress(OSError):
                os.chown(os.path.join(root, name), UID, GID)


def drop_privileges() -> None:
    os.setgroups([])
    os.setgid(GID)
    os.setuid(UID)
    os.environ.setdefault("HOME", "/tmp")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: docker-entrypoint.py <command> [args...]")

    if os.geteuid() == 0:
        try:
            prepare(os.environ.get("DATA_DIR", "/data"))
        except OSError as exc:
            print(f"entrypoint: не удалось подготовить каталог данных: {exc}", file=sys.stderr)
        drop_privileges()

    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
