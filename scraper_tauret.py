"""
TecnoPrice CO - Scraper Tauret Computadores (Playwright)
---------------------------------------------------------
Extrae productos del catálogo público de Tauret.

Arquitectura del sitio:
    Tauret usa una SPA construida sobre Vue.js. Toda la categoría se carga
    en una sola URL y la paginación se realiza haciendo clic sobre botones
    `.page-link` (sin <a href>); el contenido de la página se reemplaza por
    JavaScript sin recarga.

    Estructura de un producto:
        <div class="name">
            <a href="/product/..."><h2>NOMBRE</h2></a>
        </div>
        <div class="card_detail">
            <span class="price1">$PRECIO</span>
        </div>

Estrategia:
    1) Cargar la URL de la categoría.
    2) Extraer productos con `.name a` (nombre + enlace) y `.price1` (precio).
    3) Iterar sobre los botones de paginación (.page-link con número),
       hacer clic y volver a extraer.
    4) Detener cuando no haya un número de página mayor al actual.
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
TIENDA = "Tauret Computadores"
BASE_URL = "https://tauretcomputadores.com"

# Categorías públicas (subcategory de Tauret)
CATEGORIAS = [
    # Memorias RAM
    {"nombre": "Memorias RAM",      "url": f"{BASE_URL}/products/subcategory?cat=memorias-ram-pc"},
    {"nombre": "Memorias RAM",      "url": f"{BASE_URL}/products/subcategory?cat=memorias-ram-portatil"},
    # Unidades SSD (varias subcategorías)
    {"nombre": "Unidades SSD",      "url": f"{BASE_URL}/products/subcategory?cat=unidad-solida-2-5"},
    {"nombre": "Unidades SSD",      "url": f"{BASE_URL}/products/subcategory?cat=unidad-solida-m-2"},
    {"nombre": "Unidades SSD",      "url": f"{BASE_URL}/products/subcategory?cat=unidad-solida-m-2-gen-4"},
    {"nombre": "Unidades SSD",      "url": f"{BASE_URL}/products/subcategory?cat=unidad-solida-m-2-gen5"},
    # Tarjetas Gráficas (NVIDIA + AMD)
    {"nombre": "Tarjetas Gráficas", "url": f"{BASE_URL}/products/subcategory?cat=tarjetas-de-video-nvidia"},
    {"nombre": "Tarjetas Gráficas", "url": f"{BASE_URL}/products/subcategory?cat=tarjetas-de-video-amd"},
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
    """Convierte un precio textual en entero COP. Ej: '$1.450.000' -> 1450000."""
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
    """
    Extrae todos los productos visibles en la página actual de Tauret.

    Si la categoría está vacía (Tauret muestra "0 Productos"), el selector
    nunca aparece. Para no quedarse esperando 30 s en categorías vacías,
    se usa un timeout corto y se retorna lista vacía si no hay productos.
    """
    try:
        await page.wait_for_selector("div.name a", timeout=8000)
    except Exception:
        # Probablemente la categoría está vacía
        return []

    # Forzar carga lazy mediante scroll
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
            // Cada producto tiene un .name (con enlace y título) y un .card_detail (con precio)
            // hermanos dentro del mismo contenedor padre
            document.querySelectorAll('div.name').forEach(nameEl => {
                const linkEl   = nameEl.querySelector('a');
                const tituloEl = nameEl.querySelector('h2, h3');

                // Buscar el hermano .card_detail dentro del mismo contenedor padre
                const padre = nameEl.parentElement;
                const detailEl = padre ? padre.querySelector('.card_detail') : null;
                const precioEl = detailEl ? detailEl.querySelector('.price1') : null;

                // Imagen: dentro del contenedor padre
                const imgEl = padre ? padre.querySelector('img.product, img[alt*="producto"], img') : null;
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


async def numeros_de_paginacion(page):
    """Devuelve la lista de números de página disponibles, en orden."""
    return await page.evaluate(
        """
        () => {
            const nums = [];
            document.querySelectorAll('.page-item .page-link, .pagination .page-link').forEach(el => {
                const t = el.innerText.trim();
                if (/^\\d+$/.test(t)) nums.push(parseInt(t));
            });
            return [...new Set(nums)].sort((a, b) => a - b);
        }
        """
    )


async def hacer_click_pagina(page, numero):
    """Hace clic en el botón de paginación con el número indicado."""
    selector = f"xpath=//*[contains(@class,'page-link') and normalize-space(text())='{numero}']"
    try:
        await page.click(selector, timeout=5000)
        return True
    except Exception:
        return False


async def scrapear_categoria(page, categoria: dict):
    """
    Recorre todas las páginas de una categoría en Tauret.

    Tauret tiene paginación SPA (clic en botón). Estrategia:
        1) Cargar URL inicial.
        2) Extraer productos de la página actual.
        3) Identificar el siguiente número de página y hacer clic.
        4) Esperar a que cambie el contenido (los enlaces no se solapan).
        5) Repetir hasta que no haya un número mayor.

    Se usa deduplicación por enlace para detectar fin natural si el sitio
    no cambia el contenido tras un clic.
    """
    resultados = []
    enlaces_vistos = set()
    url = categoria["url"]

    print(f"  -> URL: {url}")
    try:
        respuesta = await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
    except Exception as e:
        print(f"     [WARN] No se pudo cargar la página: {e}")
        return resultados

    if respuesta is None or respuesta.status >= 400:
        print(f"     [INFO] HTTP {respuesta.status if respuesta else 'sin respuesta'}.")
        return resultados

    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    await asyncio.sleep(2.0)  # SPAs necesitan tiempo extra

    pagina_actual = 1
    while True:
        print(f"     Página {pagina_actual}...")

        # Extraer con reintento
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
            print("     [INFO] Sin productos en esta página.")
            break

        nuevos = [p for p in productos if p["enlace"] not in enlaces_vistos]
        if not nuevos:
            print("     [INFO] La página solo trae productos repetidos; se detiene.")
            break

        for p in nuevos:
            enlaces_vistos.add(p["enlace"])
        repetidos = len(productos) - len(nuevos)
        if repetidos:
            print(f"     {len(nuevos)} productos nuevos ({repetidos} repetidos).")
        else:
            print(f"     {len(nuevos)} productos extraídos.")

        resultados.extend(nuevos)

        # Buscar siguiente número de página
        numeros = await numeros_de_paginacion(page)
        siguientes = [n for n in numeros if n > pagina_actual]
        if not siguientes:
            print("     [INFO] No hay más páginas.")
            break

        siguiente = siguientes[0]
        await asyncio.sleep(DELAY_ENTRE_PAGINAS_SEG)

        ok = await hacer_click_pagina(page, siguiente)
        if not ok:
            print(f"     [WARN] No se pudo hacer clic en página {siguiente}.")
            break

        # Esperar a que cambie el contenido (cambio en .page-item activo)
        try:
            await page.wait_for_function(
                f"() => document.querySelector('.page-item.active .page-link')?.innerText.trim() === '{siguiente}'",
                timeout=10000,
            )
        except Exception:
            pass
        await asyncio.sleep(1.5)

        pagina_actual = siguiente

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
    ruta_json = OUTPUT_DIR / f"tauret_{timestamp}.json"
    ruta_csv  = OUTPUT_DIR / f"tauret_{timestamp}.csv"

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
