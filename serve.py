from __future__ import annotations

import argparse
import sys
import uvicorn

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve ALBRA Agent Bot")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=9000, help="Port to listen on")
    args = parser.parse_args()

    print(f"============================================================")
    print(f"  ALBRA AGENT BOT DESPLEGADO")
    print(f"  URL Base:   http://{args.host}:{args.port}")
    print(f"  Swagger UI: http://{args.host}:{args.port}/docs")
    print(f"  Health:     http://{args.host}:{args.port}/api/v1/health")
    print(f"============================================================")

    uvicorn.run(
        "render_app:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
