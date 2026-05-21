"""
TecnoPrice CO - Scraper Clones y Periféricos (Playwright)
----------------------------------------------------------
Extrae productos del catálogo público de Clones y Periféricos.

Arquitectura del sitio:
    Clones y Periféricos usa WooCommerce con un theme custom (Visual Composer).
    URLs de listado:
        /tienda/<slug>/                -> página 1
        /tienda/<slug>/page/2/         -> página 2
        /tienda/<slug>/page/3/         -> página 3

    Estructura de un producto:
        <div class="product type-product ...">
            <a class="product-content-image" href="...">
                <img class="attachment-woocommerce_single ..."/>
            </a>
            <h2 class="product-title">NOMBRE</h2>
            <div class="price">
                <span class="woocommerce-Price-amount">$PRECIO</span>
            </div>
        </div>
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
TIENDA = "Clones y Periféricos"
BASE_URL = "https://clonesyperifericos.com"

# Categorías públicas de WooCommerce
CATEGORIAS = [
    {"nombre": "Memorias RAM",      "url": f"{BASE_URL}/tienda/memorias-ram-para-pc/"},
    {"nombre": "Unidades SSD",      "url": f"{BASE_URL}/tienda/almacenamiento-discos-duros-para-pc/ssd-discos-solidos/"},
    {"nombre": "Tarjetas Gráficas", "url": f"{BASE_URL}/tienda/tarjetas-de-video-para-pc-gamer/"},
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
    """Convierte un precio textual en entero COP. Ej: '$569,000' -> 569000."""
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
    """Extrae todos los productos visibles en la página actual."""
    try:
        await page.wait_for_selector(".product.type-product", timeout=10000)
    except Exception:
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
            document.querySelectorAll('.product.type-product').forEach(prod => {
                const linkEl   = prod.querySelector('a.product-content-image, a.product-content-image-only');
                const tituloEl = prod.querySelector('h2.product-title, .product-title, h2');

                // Precio: priorizar precio actual en oferta (<ins>) si existe
                const precioActualEl = prod.querySelector('.price ins .woocommerce-Price-amount')
                                    || prod.querySelector('.price > .woocommerce-Price-amount')
                                    || prod.querySelector('.price .woocommerce-Price-amount');
                const precioEl = prod.querySelector('.price');

                // Imagen: preferir imagen del producto, no el logo de la marca
                const imgEl = prod.querySelector('a.product-content-image img, .product-image-wrapper img');
                let imagen = null;
                if (imgEl) {
                    imagen = imgEl.getAttribute('data-src')
                          || imgEl.getAttribute('data-lazy-src')
                          || (imgEl.src && !imgEl.src.startsWith('data:') ? imgEl.src : null);
                    if (!imagen) {
                        const srcset = imgEl.getAttribute('data-lazy-srcset') || imgEl.getAttribute('srcset');
                        if (srcset) imagen = srcset.split(',')[0].trim().split(' ')[0];
                    }
                }

                productos.push({
                    nombre: tituloEl ? tituloEl.innerText.trim() : null,
                    precio: precioActualEl ? precioActualEl.innerText.replace(/\\s+/g, ' ').trim()
                                           : (precioEl ? precioEl.innerText.replace(/\\s+/g, ' ').trim() : null),
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
    Recorre dinámicamente todas las páginas de una categoría WooCommerce.

    WooCommerce devuelve 404 cuando se solicita una página inexistente, lo
    que sirve como condición natural de parada. Aun así se aplica un set de
    enlaces ya vistos como salvaguarda contra contenido duplicado.
    """
    resultados = []
    enlaces_vistos = set()
    url = categoria["url"]
    num_pagina = 1

    while True:
        url_pagina = url if num_pagina == 1 else f"{url}page/{num_pagina}/"
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
    ruta_json = OUTPUT_DIR / f"clonesyperifericos_{timestamp}.json"
    ruta_csv  = OUTPUT_DIR / f"clonesyperifericos_{timestamp}.csv"

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
