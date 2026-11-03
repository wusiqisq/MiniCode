"""支持通过 python -m minicode 启动。"""

from minicode.cli import entrypoint


if __name__ == "__main__":
    raise SystemExit(entrypoint())
