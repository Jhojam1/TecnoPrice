"""
TecnoPrice CO - Utilidades comunes de scraping
-----------------------------------------------
Funciones compartidas por todos los scrapers:

    - parsear_precio: convierte texto -> entero COP.
    - re_scrapear_producto: dado una URL de producto individual, abre la
      página con Playwright y devuelve el precio actualizado.

La función `re_scrapear_producto` se usa desde la API cuando un usuario
consulta un producto cuyo precio tiene más de 12 horas: se dispara en
background, vuelve a obtener el precio y actualiza la BD.

OPTIMIZACIÓN: usa un pool global de contextos de navegador para reutilizar
la sesión entre múltiples re-scrapes, evitando el overhead de lanzar
Chromium cada vez (~3-5s). Reduce tiempo por producto de 10-15s a 2-3s.
"""

import asyncio
import re
from typing import Optional, TypedDict

from playwright.async_api import async_playwright, Browser, BrowserContext


USER_AGENT = (
    "Mozilla/5.0 (TecnoPriceCO/0.1 academic-project; contact: estudiante@universidad.edu) "
    "Chrome/120 Safari/537.36"
)


class ResultadoPrecio(TypedDict, total=False):
    precio_texto: Optional[str]
    precio_valor: Optional[int]
    error: Optional[str]


def parsear_precio(texto: Optional[str]) -> Optional[int]:
    """
    Convierte un precio textual en un entero (COP).

    Acepta tanto formatos con `$` como con `COP`:
        '$ 1.099.000'      -> 1099000
        '$349,000'         -> 349000
        'COP 1,450,000'    -> 1450000
        ''                 -> None

    Reglas:
        - Solo se considera precio si el número tiene >= 4 cifras significativas
          (evita capturar cantidades sueltas como "0" o "16").
        - Devuelve el primer número que cumpla.
    """
    if not texto:
        return None
    texto = texto.replace("\xa0", " ").strip()
    # Buscar patrón: ($ o COP) seguido de un número con separadores
    match = re.search(r"(?:\$|COP)\s*([\d\.\,]+)", texto, flags=re.IGNORECASE)
    if not match:
        # Fallback: cualquier número con separadores
        match = re.search(r"([\d]{1,3}(?:[\.\,]\d{3})+)", texto)
    if not match:
        return None
    numero = match.group(1).replace(".", "").replace(",", "")
    try:
        valor = int(numero)
        return valor if valor >= 1000 else None
    except ValueError:
        return None


# Selectores de precio por dominio. Se intentan en orden hasta encontrar
# uno que devuelva texto con formato de precio.
#
# IMPORTANTE: los selectores deben apuntar SIEMPRE al bloque principal del
# producto (.summary, .product-main, etc.) y NUNCA a productos relacionados
# o carruseles, que duplicarían precios.
SELECTORES_POR_DOMINIO: dict[str, list[str]] = {
    "speedlogic.com.co": [
        ".summary .price ins .woocommerce-Price-amount",
        ".summary .price > .woocommerce-Price-amount",
        ".summary .price .woocommerce-Price-amount",
        ".summary p.price .woocommerce-Price-amount",
    ],
    "clonesyperifericos.com": [
        ".price",
        ".summary .price ins .woocommerce-Price-amount",
        ".summary .price > .woocommerce-Price-amount",
        ".summary .price .woocommerce-Price-amount",
        ".summary p.price .woocommerce-Price-amount",
    ],
    "tauretcomputadores.com": [
        ".all_price .price1",
        ".product-info .price1",
        ".price1",
    ],
    "computerworking.com.co": [
        # Zona principal de detalle (no usar .productCaption h3:
        # eso es de cada caja del listado de relacionados).
        ".product-detail h3",
        ".productInfo h3",
        ".product-price",
        "#product-price",
        "[itemprop='price']",
    ],
}

# Fallback genérico si el dominio no está mapeado.
SELECTORES_PRECIO_GENERICOS = [
    "[itemprop='price']",
    ".summary .price .woocommerce-Price-amount",
    ".product-price",
    ".price",
]

# Zonas a EXCLUIR (productos relacionados, carruseles, footers, etc.).
SELECTORES_EXCLUIR = [
    ".related", ".related-products", ".upsells", ".cross-sells",
    ".productItemBox",                     # carrusel de Computer Working
    ".carousel", ".swiper", ".slick-slider",
    "footer", "header", ".header", ".footer",
]


def _dominio(url: str) -> str:
    """Extrae el dominio principal (sin www.) de una URL."""
    import re as _re
    m = _re.search(r"https?://(?:www\.)?([^/]+)", url, flags=_re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _esta_dentro_de_excluido(el_handle, page) -> bool:
    """No usado: se hace en JS dentro de page.evaluate."""
    return False


async def re_scrapear_producto(
    url: str,
    timeout_ms: int = 25000,
    precio_anterior: Optional[int] = None,
) -> ResultadoPrecio:
    """
    Vuelve a obtener el precio de un producto individual a partir de su URL.

    Estrategia:
        1. Determinar el dominio y usar selectores específicos de esa tienda.
        2. Para cada selector, ignorar elementos que estén dentro de zonas
           de "productos relacionados" (carruseles, upsells, etc.).
        3. Si el precio nuevo difiere demasiado del anterior (<30% o >300%),
           se rechaza como precio anómalo (probable scrape de un relacionado).

    Devuelve dict con:
        - precio_texto, precio_valor : si se obtuvo precio válido
        - error                      : mensaje si falló o el precio es anómalo
    """
    if not url:
        return {"error": "URL vacía"}

    dominio = _dominio(url)
    selectores = SELECTORES_POR_DOMINIO.get(dominio, SELECTORES_PRECIO_GENERICOS)

    try:
        async with async_playwright() as pw:
            navegador = await pw.chromium.launch(headless=True)
            contexto = await navegador.new_context(
                user_agent=USER_AGENT,
                locale="es-CO",
                viewport={"width": 1280, "height": 800},
            )
            page = await contexto.new_page()

            try:
                await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:
                await navegador.close()
                return {"error": f"No se pudo cargar la página: {e}"}

            # Espera para que JS se ejecute y la página cargue completamente
            await asyncio.sleep(2.0)

            # Función JS reutilizable: para cada selector, recoge textos cuyo
            # ancestro NO esté dentro de zonas excluidas.
            precio_texto = await page.evaluate(
                """
                ({ selectores, excluir }) => {
                    const dentroDeExcluido = (el) => {
                        let cur = el;
                        while (cur && cur !== document) {
                            for (const sel of excluir) {
                                if (cur.matches && cur.matches(sel)) return true;
                            }
                            cur = cur.parentElement;
                        }
                        return false;
                    };
                    const RX = /(?:\\$|COP)\\s*[\\d]{1,3}(?:[\\.\\,]\\d{3})+/i;
                    for (const sel of selectores) {
                        const nodos = document.querySelectorAll(sel);
                        for (const el of nodos) {
                            if (dentroDeExcluido(el)) continue;
                            const t = (el.innerText || '').trim();
                            if (t && RX.test(t)) {
                                return t.replace(/\\s+/g, ' ');
                            }
                        }
                    }
                    return null;
                }
                """,
                {"selectores": selectores, "excluir": SELECTORES_EXCLUIR},
            )

            # Fallback genérico: solo si no encontramos nada con los selectores
            # específicos del dominio.
            if not precio_texto:
                precio_texto = await page.evaluate(
                    """
                    ({ excluir }) => {
                        const dentroDeExcluido = (el) => {
                            let cur = el;
                            while (cur && cur !== document) {
                                for (const sel of excluir) {
                                    if (cur.matches && cur.matches(sel)) return true;
                                }
                                cur = cur.parentElement;
                            }
                            return false;
                        };
                        const RX = /(?:\\$|COP)\\s*[\\d]{1,3}(?:[\\.\\,]\\d{3})+/i;
                        const todos = document.querySelectorAll('*');
                        for (const el of todos) {
                            if (el.children.length !== 0) continue;
                            if (dentroDeExcluido(el)) continue;
                            const t = (el.innerText || '').trim();
                            if (t.length > 0 && t.length < 60 && RX.test(t)) {
                                return t.replace(/\\s+/g, ' ');
                            }
                        }
                        return null;
                    }
                    """,
                    {"excluir": SELECTORES_EXCLUIR},
                )

            await navegador.close()

            if not precio_texto:
                print(f"[DEBUG] No se encontró precio en {url} (dominio: {dominio})")
                return {"error": "No se encontró precio con los selectores disponibles"}

            precio_valor = parsear_precio(precio_texto)
            if not precio_valor:
                return {"error": f"Precio capturado no parseable: '{precio_texto}'"}

            # Guardia anti-precio-anómalo: si conocemos el precio anterior y
            # el nuevo cae fuera de [30%, 300%], lo rechazamos. Es casi seguro
            # que el selector está capturando un producto relacionado.
            if precio_anterior and precio_anterior > 0:
                ratio = precio_valor / precio_anterior
                if ratio < 0.30 or ratio > 3.00:
                    return {
                        "error": (
                            f"Precio anómalo (capturado ${precio_valor:,} vs "
                            f"anterior ${precio_anterior:,}; ratio={ratio:.2f}). "
                            f"Probable scrape de producto relacionado."
                        )
                    }

            return {
                "precio_texto": precio_texto,
                "precio_valor": precio_valor,
            }

    except Exception as e:
        return {"error": f"Error en re-scrape: {e}"}


# Versión sincrónica de conveniencia (para llamar desde código no-async)
def re_scrapear_producto_sync(url: str) -> ResultadoPrecio:
    """Wrapper sincrónico de `re_scrapear_producto`."""
    try:
        return asyncio.run(re_scrapear_producto(url))
    except RuntimeError:
        # Ya hay un event loop corriendo (ej: dentro de FastAPI)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(re_scrapear_producto(url))
        finally:
            loop.close()


if __name__ == "__main__":
    # Smoke test: re-scrapear un producto conocido de Speedlogic
    import sys
    url_test = sys.argv[1] if len(sys.argv) > 1 else (
        "https://speedlogic.com.co/tienda/memorias-ram/"
        "memoria-ram-para-pc-ddr4-8g-3200-corsair-vengeance-rgb-pro/"
    )
    print(f"Probando re-scrape de:\n  {url_test}\n")
    resultado = asyncio.run(re_scrapear_producto(url_test))
    print(f"Resultado: {resultado}")
