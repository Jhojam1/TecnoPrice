"""
TecnoPrice CO - Script de arranque local.

Lanza la API + frontend en http://localhost:8000 y abre el navegador.

Uso:
    python start.py
    python start.py --no-browser     # no abrir navegador automáticamente
    python start.py --port 8080      # puerto distinto

Para detener: Ctrl+C
"""

import argparse
import sys
import threading
import time
import webbrowser

import uvicorn


def abrir_navegador(url: str, delay: float = 2.0):
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reload", action="store_true",
                        help="Recarga automática al cambiar archivos (desarrollo)")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"

    print()
    print("=" * 60)
    print("  TecnoPrice CO")
    print("=" * 60)
    print(f"  Servidor: {url}")
    print(f"  Detener:  Ctrl+C")
    print("=" * 60)
    print()

    if not args.no_browser:
        threading.Thread(target=abrir_navegador, args=(url,), daemon=True).start()

    try:
        uvicorn.run(
            "api:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\n[OK] Servidor detenido.")
        sys.exit(0)


if __name__ == "__main__":
    main()
