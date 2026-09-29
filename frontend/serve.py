"""Static server for the frontend, with caching turned off.

`python -m http.server` answers with Last-Modified and no Cache-Control, so a
browser happily reuses a stale app.js or styles.css after a rebuild. That wastes
real time: the page looks unchanged, or worse, runs old JavaScript against new
markup and throws. Every response here is marked no-store.

    python frontend/serve.py            # http://127.0.0.1:8080
    python frontend/serve.py --port 9000
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # One quiet line per request; the default logs to stderr very noisily.
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    handler = partial(NoCacheHandler, directory=str(root))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"frontend on http://{args.host}:{args.port}/index.html  (no-store)")
    print(f"serving {root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
