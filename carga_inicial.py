"""
TecnoPrice CO - Carga inicial a PostgreSQL (Neon)
--------------------------------------------------
Ejecuta los 4 scrapers y vuelca todos los productos en la base de datos.

- Crea las tablas si no existen.
- Por cada tienda, ejecuta su `scrapear_todo()`.
- Hace upsert por URL (`enlace`) para no duplicar.
- Registra cada cambio de precio en `historial_precios`.
- Imprime un resumen final con conteos por tienda y categoría.

Uso:
    python carga_inicial.py [--tienda speedlogic|computerworking|tauret|clones|todas]

Ejemplos:
    python carga_inicial.py                # ejecuta los 4 scrapers
    python carga_inicial.py --tienda tauret
"""

import argparse
import asyncio
from datetime import datetime
from typing import Awaitable, Callable

from sqlalchemy.exc import IntegrityError

import db
import scraper_speedlogic
import scraper_computerworking
import scraper_tauret
import scraper_clonesyperifericos


# Mapa de scrapers disponibles: alias -> (nombre tienda, base_url, función async)
SCRAPERS: dict[str, tuple[str, str, Callable[[], Awaitable[list[dict]]]]] = {
    "speedlogic": (
        scraper_speedlogic.TIENDA,
        scraper_speedlogic.BASE_URL,
        scraper_speedlogic.scrapear_todo,
    ),
    "computerworking": (
        scraper_computerworking.TIENDA,
        scraper_computerworking.BASE_URL,
        scraper_computerworking.scrapear_todo,
    ),
    "tauret": (
        scraper_tauret.TIENDA,
        scraper_tauret.BASE_URL,
        scraper_tauret.scrapear_todo,
    ),
    "clones": (
        scraper_clonesyperifericos.TIENDA,
        scraper_clonesyperifericos.BASE_URL,
        scraper_clonesyperifericos.scrapear_todo,
    ),
}


def persistir_productos(nombre_tienda: str, base_url: str, productos: list[dict]) -> tuple[int, int]:
    """
    Guarda en BD la lista de productos de una tienda.

    Devuelve (insertados+actualizados, errores).
    """
    if not productos:
        return 0, 0

    insertados_actualizados = 0
    errores = 0

    with db.SessionLocal() as session:
        tienda = db.obtener_o_crear_tienda(session, nombre_tienda, base_url)
        session.commit()

        for producto in productos:
            try:
                db.upsert_producto(session, tienda.id, producto)
                session.commit()
                insertados_actualizados += 1
            except IntegrityError:
                session.rollback()
                errores += 1
            except Exception as e:
                session.rollback()
                errores += 1
                print(f"     [ERROR] {producto.get('enlace')}: {e}")

    return insertados_actualizados, errores


async def ejecutar_scraper(alias: str) -> dict:
    """Ejecuta un scraper específico y persiste sus resultados."""
    nombre_tienda, base_url, runner = SCRAPERS[alias]

    print(f"\n{'='*70}")
    print(f"  Ejecutando: {nombre_tienda}")
    print(f"{'='*70}")

    try:
        productos = await runner()
    except Exception as e:
        print(f"[ERROR] {nombre_tienda}: {e}")
        return {"tienda": nombre_tienda, "extraidos": 0, "guardados": 0, "errores": 0}

    print(f"\n  Persistiendo {len(productos)} productos en BD...")
    guardados, errores = persistir_productos(nombre_tienda, base_url, productos)
    print(f"  [OK] Guardados/actualizados: {guardados}  |  Errores: {errores}")

    return {
        "tienda": nombre_tienda,
        "extraidos": len(productos),
        "guardados": guardados,
        "errores": errores,
    }


async def main(tiendas: list[str]):
    inicio = datetime.now()

    print(f"\n{'#'*70}")
    print(f"#  TecnoPrice CO - Carga inicial a PostgreSQL")
    print(f"#  Inicio: {inicio.isoformat(timespec='seconds')}")
    print(f"{'#'*70}")

    # Asegurar que las tablas existan
    db.crear_tablas()

    resumen = []
    for alias in tiendas:
        if alias not in SCRAPERS:
            print(f"[!] Tienda desconocida: {alias}")
            continue
        resultado = await ejecutar_scraper(alias)
        resumen.append(resultado)

    fin = datetime.now()
    duracion = (fin - inicio).total_seconds()

    print(f"\n{'='*70}")
    print(f"  RESUMEN FINAL")
    print(f"{'='*70}")
    total_extraidos = sum(r["extraidos"] for r in resumen)
    total_guardados = sum(r["guardados"] for r in resumen)
    for r in resumen:
        print(f"  {r['tienda']:25s}  extraidos={r['extraidos']:4d}  guardados={r['guardados']:4d}  errores={r['errores']}")
    print(f"  {'-'*60}")
    print(f"  {'TOTAL':25s}  extraidos={total_extraidos:4d}  guardados={total_guardados:4d}")
    print(f"\n  Duración: {duracion:.0f} s")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Carga inicial a PostgreSQL")
    parser.add_argument(
        "--tienda",
        choices=list(SCRAPERS.keys()) + ["todas"],
        default="todas",
        help="Qué tienda ejecutar (por defecto: todas)",
    )
    args = parser.parse_args()

    if args.tienda == "todas":
        tiendas = list(SCRAPERS.keys())
    else:
        tiendas = [args.tienda]

    asyncio.run(main(tiendas))
