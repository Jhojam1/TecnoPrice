"""
TecnoPrice CO - Scraper Computer Working (Playwright)
------------------------------------------------------
Extrae productos del catálogo público de Computer Working.

Arquitectura del sitio:
    Computer Working usa una plantilla custom (no WooCommerce).
    URLs de listado:
        /categorias/<id>/true          -> página 1
        /categorias/<id>/true/2        -> página 2
        /categorias/<id>/true/3        -> página 3
    Selector de producto: .productBox
        - .productImage a       -> enlace al detalle del producto
        - .productImage img     -> imagen
        - .productCaption h5    -> nombre del producto
        - .productCaption h3    -> precio

Campos capturados por producto:
    - tienda, categoria, nombre, precio, precio_valor (entero COP),
      enlace, imagen, fecha_consulta
"""

import asyncio
import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

# --- Configuración general ---
TIENDA = "Computer Working"
BASE_URL = "https://www.computerworking.com.co"

# Categorías públicas (ID interno de la tienda)
CATEGORIAS = [
    {"nombre": "Memorias RAM",      "url": f"{BASE_URL}/categorias/304/true"},
    {"nombre": "Unidades SSD",      "url": f"{BASE_URL}/categorias/317/false"},
    {"nombre": "Tarjetas Gráficas", "url": f"{BASE_URL}/categorias/69/false"},
]

# --- Política de cortesía ---
DELAY_ENTRE_PAGINAS_SEG = 2.5
TIMEOUT_MS = 30000

# --- Configuración del navegador (controlable por SCRAPER_HEADLESS env var) ---
HEADLESS = os.getenv("SCRAPER_HEADLESS", "false").lower() in ("1", "true", "yes")
SLOW_MO_MS = 0 if HEADLESS else 400
USER_AGENT = (
    "Mozilla/5.0 (TecnoPriceCO/0.1 academic-project; contact: estudiante@universidad.edu) "
    "Chrome/120 Safari/537.36"
)

OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True)


def parsear_precio(texto: str):
    """Convierte un precio textual en entero COP. Ej: '$690,000' -> 690000."""
    if not texto:
        return None
    texto = texto.replace("\xa0", " ").strip()
    match = re.search(r"\$?\s*([\d\.\,]+)", texto)
    if not match:
        return None
    numero = match.group(1).replace(".", "").replace(",", "")
    try:
        return int(numero)
    except ValueError:
        return None


async def extraer_productos_de_pagina(page, categoria: str):
    """Extrae todos los .productBox visibles en la página actual."""
    await page.wait_for_selector(".productBox", timeout=TIMEOUT_MS)

    # Forzar carga de imágenes lazy mediante scroll
    try:
        altura = await page.evaluate("() => document.body.scrollHeight")
        for y in range(0, int(altura) + 600, 600):
            await page.evaluate(f"window.scrollTo(0, {y})")
            await asyncio.sleep(0.15)
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.3)
    except Exception:
        pass

    items = await page.evaluate(
        """
        () => {
            const productos = [];
            document.querySelectorAll('.productBox').forEach(box => {
                const linkEl   = box.querySelector('.productImage a');
                const imgEl    = box.querySelector('.productImage img');
                const tituloEl = box.querySelector('.productCaption h5');
                const precioEl = box.querySelector('.productCaption h3');

                let imagen = null;
                if (imgEl) {
                    imagen = imgEl.getAttribute('data-src')
                          || (imgEl.src && !imgEl.src.startsWith('data:') ? imgEl.src : null);
                }

                productos.push({
                    nombre: tituloEl ? tituloEl.innerText.trim() : null,
                    precio: precioEl ? precioEl.innerText.replace(/\\s+/g, ' ').trim() : null,
                    enlace: linkEl   ? linkEl.href : null,
                    imagen: imagen,
                });
            });
            return productos;
        }
        """
    )

    fecha = datetime.now().isoformat(timespec="seconds")
    limpios = []
    for p in items:
        if not p.get("nombre") or not p.get("enlace"):
            continue
        limpios.append({
            "tienda":         TIENDA,
            "categoria":      categoria,
            "nombre":         p["nombre"],
            "precio":         p["precio"],
            "precio_valor":   parsear_precio(p["precio"]),
            "enlace":         p["enlace"],
            "imagen":         p["imagen"],
            "fecha_consulta": fecha,
        })
    return limpios


async def scrapear_categoria(page, categoria: dict):
    """
    Recorre dinámicamente todas las páginas de una categoría.

    Computer Working no devuelve 404 cuando la página no existe: simplemente
    repite el contenido de la primera. Para evitar un bucle infinito, se lleva
    un conjunto de enlaces ya vistos y se detiene cuando la página actual no
    aporta productos nuevos.
    """
    resultados = []
    enlaces_vistos = set()  # URLs únicos de productos ya capturados
    url = categoria["url"]
    num_pagina = 1

    while True:
        # Página 1 = URL base; siguientes = URL/<n>
        url_pagina = url if num_pagina == 1 else f"{url}/{num_pagina}"
        print(f"  -> Página {num_pagina}: {url_pagina}")

        try:
            respuesta = await page.goto(url_pagina, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        except Exception as e:
            print(f"     [WARN] No se pudo cargar la página: {e}")
            break

        if respuesta is None or respuesta.status >= 400:
            status = respuesta.status if respuesta else "sin respuesta"
            print(f"     [INFO] Fin de paginación (HTTP {status}).")
            break

        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # Extraer con un reintento
        productos = []
        for _ in range(2):
            try:
                productos = await extraer_productos_de_pagina(page, categoria["nombre"])
            except Exception:
                productos = []
            if productos:
                break
            await asyncio.sleep(2.0)

        if not productos:
            print("     [INFO] Página sin productos, se detiene paginación.")
            break

        # Filtrar solo productos cuyo enlace no se haya visto antes
        nuevos = [p for p in productos if p["enlace"] not in enlaces_vistos]

        if not nuevos:
            print("     [INFO] La página solo contiene productos repetidos; se detiene paginación.")
            break

        for p in nuevos:
            enlaces_vistos.add(p["enlace"])

        repetidos = len(productos) - len(nuevos)
        if repetidos:
            print(f"     {len(nuevos)} productos nuevos extraídos ({repetidos} ya vistos, ignorados).")
        else:
            print(f"     {len(nuevos)} productos extraídos.")

        resultados.extend(nuevos)

        await asyncio.sleep(DELAY_ENTRE_PAGINAS_SEG)
        num_pagina += 1

    return resultados


async def scrapear_todo() -> list[dict]:
    """Ejecuta el scraping de todas las categorías y devuelve la lista en memoria."""
    productos_totales: list[dict] = []
    async with async_playwright() as pw:
        navegador = await pw.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO_MS)
        contexto = await navegador.new_context(
            user_agent=USER_AGENT,
            locale="es-CO",
            viewport={"width": 1366, "height": 820},
        )
        page = await contexto.new_page()
        for cat in CATEGORIAS:
            print(f"[Categoría] {cat['nombre']}")
            productos = await scrapear_categoria(page, cat)
            productos_totales.extend(productos)
            print(f"  Total categoría: {len(productos)}\n")
        await contexto.close()
        await navegador.close()
    return productos_totales


async def main():
    print(f"=== TecnoPrice CO | Scraper {TIENDA} ===")
    print(f"Inicio: {datetime.now().isoformat(timespec='seconds')}\n")

    todos = await scrapear_todo()

    if not todos:
        print("No se obtuvieron productos.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta_json = OUTPUT_DIR / f"computerworking_{timestamp}.json"
    ruta_csv  = OUTPUT_DIR / f"computerworking_{timestamp}.csv"

    with ruta_json.open("w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)

    columnas = ["tienda", "categoria", "nombre", "precio", "precio_valor",
                "enlace", "imagen", "fecha_consulta"]
    with ruta_csv.open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=columnas)
        escritor.writeheader()
        escritor.writerows(todos)

    print("=== Resumen ===")
    print(f"Productos totales: {len(todos)}")
    por_cat = {}
    for p in todos:
        por_cat[p["categoria"]] = por_cat.get(p["categoria"], 0) + 1
    for c, n in por_cat.items():
        print(f"  - {c}: {n}")
    print(f"\nJSON: {ruta_json}")
    print(f"CSV : {ruta_csv}")


if __name__ == "__main__":
    asyncio.run(main())
