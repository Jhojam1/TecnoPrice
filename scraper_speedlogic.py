"""
Scraper Speedlogic
------------------------------------------------
Módulo encargado de extraer información de productos del sitio
speedlogic.com.co usando Playwright.

Campos capturados por producto:
    - nombre        : nombre del producto tal como aparece en la tienda
    - precio        : texto del precio tal cual lo publica la tienda
    - precio_valor  : precio convertido a entero en COP (si se pudo parsear)
    - enlace        : URL directa al producto
    - imagen        : URL de la imagen principal del producto
    - categoria     : categoría a la que pertenece el producto
    - tienda        : nombre de la tienda origen (siempre "Speedlogic")
    - fecha_consulta
"""

import asyncio          
import csv              
import json             
import os
import re               
from datetime import datetime  
from pathlib import Path        

from playwright.async_api import async_playwright  


TIENDA = "Speedlogic"             
BASE_URL = "https://speedlogic.com.co" 

# Categorías del catálogo que se van a rastrear.
# Cada entrada tiene un nombre legible y la URL de su página de listado.
CATEGORIAS = [
    {"nombre": "Memorias RAM",      "url": f"{BASE_URL}/categoria/memorias-ram/"},
    {"nombre": "Unidades SSD",      "url": f"{BASE_URL}/categoria/unidades-ssd/"},
    {"nombre": "Tarjetas Gráficas", "url": f"{BASE_URL}/categoria/tarjetas-graficas/"},
]

# --- Política de cortesía (evita sobrecargar el servidor) ---
DELAY_ENTRE_PAGINAS_SEG = 2.5   # Segundos de espera entre peticiones
TIMEOUT_MS = 30000              # Tiempo máximo de espera por carga de página (ms)

# --- Configuración visual del navegador ---
# HEADLESS controlable por variable de entorno SCRAPER_HEADLESS
# - 'true' / '1': el navegador no se muestra (modo rápido para producción / scheduler)
# - 'false' (por defecto): el navegador se abre y es visible (modo demo / desarrollo)
HEADLESS = os.getenv("SCRAPER_HEADLESS", "false").lower() in ("1", "true", "yes")
SLOW_MO_MS = 0 if HEADLESS else 400        # Retardo artificial entre acciones del navegador (ms)
                        # Útil para depuración visual y para no parecer un bot agresivo

# User-Agent descriptivo: identifica el scraper y su propósito académico
USER_AGENT = (
    "Mozilla/5.0 (TecnoPriceCO/0.1 academic-project; contact: estudiante@universidad.edu) "
    "Chrome/120 Safari/537.36"
)

# Carpeta de salida para los archivos generados; se crea si no existe
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# FUNCIÓN: parsear_precio
# ---------------------------------------------------------------------------

def parsear_precio(texto: str):
    """
    Convierte un precio en formato de texto a un número entero (pesos colombianos).

    Motivación: cada tienda publica sus precios con formatos distintos
    (puntos como separador de miles, espacios, símbolos de peso, rangos, etc.).
    Esta función normaliza esa variedad para poder comparar y ordenar precios
    entre tiendas sin depender de un formato específico.

    Proceso interno:
        1. Reemplaza el espacio no separable (\\xa0) por un espacio normal.
        2. Busca el primer número en el texto usando una expresión regular.
           Si hay un rango ('$100.000 – $200.000'), toma el primer valor.
        3. Elimina los separadores de miles (puntos y comas).
        4. Intenta convertir el resultado a entero.

    Parámetros:
        texto (str): cadena de texto con el precio, por ejemplo '$1.099.000'.

    Retorna:
        int: precio en pesos colombianos sin formato, por ejemplo 1099000.
        None: si el texto está vacío o no contiene un número reconocible.

    Ejemplos:
        '$ 1.099.000'          -> 1099000
        '$349.000'             -> 349000
        '$100.000 – $200.000'  -> 100000  (solo el primer valor del rango)
        ''                     -> None
    """
    if not texto:
        return None

    # Normalizar el texto: quitar espacios no separables y espacios extra
    texto = texto.replace("\xa0", " ").strip()

    # Extraer el primer número que aparezca, con o sin símbolo de peso
    match = re.search(r"\$?\s*([\d\.\,]+)", texto)
    if not match:
        return None

    # Eliminar separadores de miles para obtener solo dígitos
    numero = match.group(1).replace(".", "").replace(",", "")

    try:
        return int(numero)
    except ValueError:
        # Si por alguna razón no se puede convertir, devuelve None
        return None


# ---------------------------------------------------------------------------
# FUNCIÓN: extraer_productos_de_pagina
# ---------------------------------------------------------------------------

async def extraer_productos_de_pagina(page, categoria: str):
    """
    Extrae todos los productos visibles en la página actual del catálogo.

    Esta función encapsula la lógica de scraping para una sola página de
    resultados. Usa Playwright para interactuar con el DOM del navegador real,
    lo que permite manejar contenido cargado dinámicamente con JavaScript.

    Parámetros:
        page     : objeto Page de Playwright con la página ya cargada.
        categoria: nombre de la categoría actual (se agrega a cada registro).

    Retorna:
        list[dict]: lista de productos limpios, cada uno con los campos
                    definidos al inicio del módulo.

    Proceso interno:
        1. ESPERA AL DOM: Aguarda a que el selector de productos WooCommerce
           ('ul.products li.product') esté presente. Esto garantiza que no
           se intente extraer antes de que el catálogo esté renderizado.

        2. SCROLL PAULATINO: Recorre la página verticalmente en pasos de
           600 px para forzar la carga de imágenes con 'lazy loading'
           (las imágenes solo se cargan cuando entran al viewport).
           Esta es la razón principal para usar Playwright en lugar de
           una librería HTTP simple como requests.

        3. EXTRACCIÓN VÍA JS: Ejecuta un bloque de JavaScript dentro del
           navegador (page.evaluate) que recorre todos los elementos
           'li.product' y extrae: nombre, precio actual (priorizando
           el precio de oferta si existe), enlace y URL de imagen.
           La imagen se busca en múltiples atributos (data-lazy-src,
           data-src, src) para cubrir distintas estrategias de lazy loading.

        4. LIMPIEZA Y ENRIQUECIMIENTO: Filtra registros sin nombre o sin
           enlace (que no son productos válidos), agrega los metadatos
           de tienda, categoría y fecha de consulta, y normaliza el precio
           con parsear_precio().
    """
    # Paso 1: esperar a que el grid de productos esté en el DOM
    await page.wait_for_selector("ul.products li.product", timeout=TIMEOUT_MS)

    # Paso 2: scroll para forzar carga de imágenes lazy-loaded
    try:
        altura = await page.evaluate("() => document.body.scrollHeight")
        paso = 600  # píxeles por cada desplazamiento
        for y in range(0, int(altura) + paso, paso):
            await page.evaluate(f"window.scrollTo(0, {y})")
            await asyncio.sleep(0.15)   # pausa breve para que el navegador procese
        await page.evaluate("window.scrollTo(0, 0)")  # volver al inicio
        await asyncio.sleep(0.3)
    except Exception:
        pass  # Si el scroll falla, se continúa igual (las imágenes pueden estar ya cargadas)

    # Paso 3: extraer datos de cada producto mediante JavaScript en el navegador
    items = await page.evaluate(
        """
        () => {
            const productos = [];
            document.querySelectorAll('ul.products li.product').forEach(li => {

                // Enlace principal del producto (selector estándar de WooCommerce)
                const linkEl  = li.querySelector('a.woocommerce-LoopProduct-link, a.woocommerce-loop-product__link');

                // Título del producto
                const tituloEl = li.querySelector('.woocommerce-loop-product__title, h2, h3');

                // Contenedor de precio (puede incluir precio tachado + precio actual)
                const precioEl = li.querySelector('.price');

                // Precio actual: si hay <ins> (elemento de oferta), se prioriza;
                // si no, se toma el primer .woocommerce-Price-amount disponible
                const precioActualEl = li.querySelector('.price ins .woocommerce-Price-amount')
                                    || li.querySelector('.price > .woocommerce-Price-amount')
                                    || li.querySelector('.price .woocommerce-Price-amount');

                // Imagen: se prueban múltiples atributos de lazy loading
                const imgEl = li.querySelector('img');
                let imagen = null;
                if (imgEl) {
                    imagen = imgEl.getAttribute('data-lazy-src')    // Lazy Load plugin
                          || imgEl.getAttribute('data-src')         // otro esquema lazy
                          || (imgEl.src && !imgEl.src.startsWith('data:') ? imgEl.src : null);
                    // Si aún no se encontró, buscar en el srcset
                    if (!imagen) {
                        const srcset = imgEl.getAttribute('data-lazy-srcset') || imgEl.getAttribute('srcset');
                        if (srcset) imagen = srcset.split(',')[0].trim().split(' ')[0];
                    }
                }

                productos.push({
                    nombre:  tituloEl ? tituloEl.innerText.trim() : null,
                    // Precio: se prefiere el precio actual; si no hay, el texto completo del bloque
                    precio:  precioActualEl ? precioActualEl.innerText.replace(/\\s+/g, ' ').trim()
                                            : (precioEl ? precioEl.innerText.replace(/\\s+/g, ' ').trim() : null),
                    enlace:  linkEl   ? linkEl.href : null,
                    imagen:  imagen,
                });
            });
            return productos;
        }
        """
    )

    # Paso 4: limpiar y enriquecer cada registro extraído
    fecha = datetime.now().isoformat(timespec="seconds")  # marca de tiempo actual
    limpios = []
    for p in items:
        # Descartar productos sin nombre o sin enlace: no son útiles para el análisis
        if not p.get("nombre") or not p.get("enlace"):
            continue
        limpios.append({
            "tienda":         TIENDA,
            "categoria":      categoria,
            "nombre":         p["nombre"],
            "precio":         p["precio"],
            "precio_valor":   parsear_precio(p["precio"]),  # precio normalizado a entero
            "enlace":         p["enlace"],
            "imagen":         p["imagen"],
            "fecha_consulta": fecha,
        })
    return limpios


# ---------------------------------------------------------------------------
# FUNCIÓN: scrapear_categoria
# ---------------------------------------------------------------------------

async def scrapear_categoria(page, categoria: dict):
    """
    Recorre todas las páginas de una categoría y acumula los productos encontrados.

    WooCommerce pagina los catálogos con URLs del tipo:
        /categoria/memorias-ram/          <- página 1
        /categoria/memorias-ram/page/2/   <- página 2
        /categoria/memorias-ram/page/3/   <- página 3

    Esta función maneja esa paginación automáticamente, respetando el límite
    definido en MAX_PAGINAS_POR_CATEGORIA y la política de cortesía.

    Parámetros:
        page     : objeto Page de Playwright (navegador abierto y listo).
        categoria: dict con 'nombre' y 'url' de la categoría a recorrer.

    Retorna:
        list[dict]: todos los productos acumulados de todas las páginas.

    Lógica de paginación:
        - Página 1 usa la URL base; páginas siguientes agregan '/page/N/'.
        - Si el servidor responde con código HTTP >= 400 (ej. 404 Not Found),
          se interpreta como que no hay más páginas y se detiene el bucle.
        - Si una página no contiene productos, también se detiene.
        - Después de cada página se aplica una pausa (DELAY_ENTRE_PAGINAS_SEG)
          como cortesía hacia el servidor.
        - En caso de error al extraer, se reintenta una vez antes de abandonar.
    """
    resultados = []
    url = categoria["url"]
    num_pagina = 1

    while True:
        # Construir la URL de la página actual
        url_pagina = url if num_pagina == 1 else f"{url}page/{num_pagina}/"
        print(f"  -> Página {num_pagina}: {url_pagina}")

        # Navegar a la página; si falla la conexión, se abandona esta categoría
        try:
            respuesta = await page.goto(url_pagina, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        except Exception as e:
            print(f"     [WARN] No se pudo cargar la página: {e}")
            break

        # Verificar que el servidor respondió correctamente (HTTP 200-399)
        if respuesta is None or respuesta.status >= 400:
            status = respuesta.status if respuesta else "sin respuesta"
            print(f"     [INFO] Fin de paginación (HTTP {status}).")
            break

        # Esperar a que terminen las peticiones de red pendientes (hasta 10 s)
        # Esto asegura que el contenido dinámico (JS, AJAX) haya terminado de cargar
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass  # Si no se alcanza el estado idle, se continúa con lo que haya

        # Intentar extraer productos; si falla, reintentar una vez más
        productos = []
        for intento in range(2):
            try:
                productos = await extraer_productos_de_pagina(page, categoria["nombre"])
            except Exception as e:
                print(f"     [WARN] Error extrayendo (intento {intento+1}): {e}")
                productos = []
            if productos:
                break               # Extracción exitosa, no es necesario reintentar
            await asyncio.sleep(2.0)  # Breve pausa antes del reintento

        # Si la página no tiene productos, se asume que ya no hay más páginas
        if not productos:
            print("     [INFO] Página sin productos, se detiene paginación.")
            break

        print(f"     {len(productos)} productos extraídos.")
        resultados.extend(productos)

        # Pausa de cortesía entre peticiones al servidor
        await asyncio.sleep(DELAY_ENTRE_PAGINAS_SEG)

        # Incrementar número de página para la siguiente iteración
        num_pagina += 1

    return resultados


# ---------------------------------------------------------------------------
# FUNCIÓN: main
# ---------------------------------------------------------------------------

async def scrapear_todo() -> list[dict]:
    """
    Ejecuta los scrapers de todas las categorías y devuelve la lista de
    productos en memoria, sin escribir archivos. Pensada para ser usada
    desde la carga inicial a BD y desde el scheduler.
    """
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
    """
    Punto de entrada del scraper. Orquesta todo el flujo de extracción y
    persistencia de datos.

    Flujo completo:
        1. INICIO DEL NAVEGADOR: Lanza Chromium con Playwright en el modo
           configurado (visible o headless) con el User-Agent identificable.
           Se crea un contexto con locale 'es-CO' para que los precios
           aparezcan en formato colombiano.

        2. ITERACIÓN POR CATEGORÍAS: Para cada categoría en CATEGORIAS,
           delega la extracción a `scrapear_categoria` y acumula los
           resultados en la lista `todos`.

        3. CIERRE DEL NAVEGADOR: Se cierra el contexto y el navegador
           correctamente, liberando recursos.

        4. PERSISTENCIA EN DOS FORMATOS:
           - JSON: datos crudos completos, útil para importar a MongoDB
             o inspeccionar manualmente la estructura.
           - CSV: vista tabular con columnas fijas, lista para abrir en
             Excel o cargar en PostgreSQL con COPY.
           Ambos archivos llevan un timestamp en el nombre para no sobreescribir
           ejecuciones anteriores.

        5. RESUMEN EN CONSOLA: Imprime el total de productos obtenidos y
           cuántos corresponden a cada categoría.

    No recibe parámetros ni retorna valores; escribe directamente en disco.
    """
    print(f"=== TecnoPrice CO | Scraper {TIENDA} ===")
    print(f"Inicio: {datetime.now().isoformat(timespec='seconds')}\n")

    # Delegar el scraping a la función reutilizable
    todos = await scrapear_todo()

    # Si no se obtuvo nada, informar y salir sin generar archivos vacíos
    if not todos:
        print("No se obtuvieron productos. Revisa conectividad o selectores.")
        return

    # --- Persistencia de resultados ---
    # Timestamp para nombrar los archivos y evitar sobreescrituras
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta_json = OUTPUT_DIR / f"speedlogic_{timestamp}.json"
    ruta_csv  = OUTPUT_DIR / f"speedlogic_{timestamp}.csv"

    # Guardar en JSON con indentación para lectura humana
    with ruta_json.open("w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)

    # Guardar en CSV con columnas en orden definido
    columnas = ["tienda", "categoria", "nombre", "precio", "precio_valor",
                "enlace", "imagen", "fecha_consulta"]
    with ruta_csv.open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=columnas)
        escritor.writeheader()
        escritor.writerows(todos)

    # --- Resumen final en consola ---
    print("=== Resumen ===")
    print(f"Productos totales : {len(todos)}")

    # Contar cuántos productos se obtuvieron por categoría
    por_cat = {}
    for p in todos:
        por_cat[p["categoria"]] = por_cat.get(p["categoria"], 0) + 1
    for c, n in por_cat.items():
        print(f"  - {c}: {n}")

    print(f"\nJSON: {ruta_json}")
    print(f"CSV : {ruta_csv}")


# ---------------------------------------------------------------------------
# PUNTO DE ENTRADA
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Ejecutar la función principal dentro del event loop de asyncio.
    # Se usa asyncio.run() en lugar de llamar main() directamente porque
    # Playwright requiere un entorno asíncrono para funcionar correctamente.
    asyncio.run(main())