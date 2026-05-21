"""
TecnoPrice CO - Scheduler (APScheduler)
----------------------------------------
Programa la ejecución automática de los scrapers cada N horas para mantener
los precios actualizados.

Uso:
    # Ejecutar en background (queda corriendo hasta Ctrl+C)
    python scheduler.py

    # Cambiar intervalo:
    python scheduler.py --horas 12

    # Ejecutar una vez de inmediato al arrancar:
    python scheduler.py --ejecutar-ya

Notas:
    - Por defecto, los scrapers corren en modo HEADLESS (sin ventana) para
      no requerir interacción gráfica en producción.
    - Se asegura de que dos ejecuciones del scheduler no se solapen
      (max_instances=1).
    - El log de cada corrida se imprime a stdout. Se puede redirigir a archivo:
        python scheduler.py >> data/scheduler.log 2>&1
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime

# Forzar headless para todas las corridas del scheduler
os.environ.setdefault("SCRAPER_HEADLESS", "true")

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: E402
from apscheduler.triggers.interval import IntervalTrigger  # noqa: E402

import carga_inicial  # reutiliza la lógica del script de carga inicial  # noqa: E402


# Bandera global para evitar solapamientos manualmente (además del flag de APS)
_ejecutando = False


async def ejecutar_scrapers_programado():
    """Job programado: corre los 4 scrapers y persiste en BD."""
    global _ejecutando
    if _ejecutando:
        print(f"[{datetime.now().isoformat(timespec='seconds')}] "
              "Ya hay una ejecución en curso; se salta.")
        return

    _ejecutando = True
    try:
        print(f"\n{'='*70}")
        print(f"[SCHEDULER] Inicio: {datetime.now().isoformat(timespec='seconds')}")
        print(f"{'='*70}")

        tiendas = list(carga_inicial.SCRAPERS.keys())
        await carga_inicial.main(tiendas)

        print(f"\n[SCHEDULER] Fin: {datetime.now().isoformat(timespec='seconds')}\n")
    except Exception as e:
        print(f"[SCHEDULER][ERROR] {e}")
    finally:
        _ejecutando = False


async def loop_principal(intervalo_horas: int, ejecutar_ya: bool):
    """Configura y arranca APScheduler."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        ejecutar_scrapers_programado,
        trigger=IntervalTrigger(hours=intervalo_horas),
        id="scrapers_todos",
        name=f"Scrapers cada {intervalo_horas}h",
        max_instances=1,
        coalesce=True,  # Si se acumulan corridas perdidas, ejecuta solo una
    )
    scheduler.start()

    proxima = scheduler.get_job("scrapers_todos").next_run_time
    print(f"[SCHEDULER] Programado: cada {intervalo_horas}h")
    print(f"[SCHEDULER] Próxima ejecución: {proxima.isoformat(timespec='seconds') if proxima else 'N/A'}")
    print("[SCHEDULER] Presiona Ctrl+C para detener.\n")

    if ejecutar_ya:
        print("[SCHEDULER] --ejecutar-ya: corriendo scrapers ahora...")
        await ejecutar_scrapers_programado()

    # Mantener el loop vivo
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scheduler de TecnoPrice CO")
    parser.add_argument("--horas", type=int, default=24,
                        help="Intervalo entre ejecuciones (horas). Default: 24")
    parser.add_argument("--ejecutar-ya", action="store_true",
                        help="Ejecutar los scrapers inmediatamente al arrancar")
    args = parser.parse_args()

    try:
        asyncio.run(loop_principal(args.horas, args.ejecutar_ya))
    except KeyboardInterrupt:
        print("\n[SCHEDULER] Detenido por el usuario.")
        sys.exit(0)
