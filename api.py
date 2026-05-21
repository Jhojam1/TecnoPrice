"""
TecnoPrice CO - API REST (FastAPI)
-----------------------------------
Capa que expone los datos del marketplace al frontend.

Endpoints:

    GET  /api/health
        Estado del servicio.

    GET  /api/stats
        Conteos: total productos, por tienda, por categoría, etc.

    GET  /api/productos
        Listado paginado de productos con filtros opcionales:
            - q: texto a buscar en el nombre
            - categoria: filtrar por categoría exacta
            - tienda: filtrar por nombre de tienda
            - orden: precio_asc | precio_desc | nombre
            - pagina, por_pagina

    GET  /api/producto/{id}
        Detalle de un producto.
        Si el precio tiene > 12 horas, se dispara un re-scrape EN BACKGROUND.
        La respuesta incluye:
            - precio actual (de BD)
            - 'actualizando': True si se está re-scrapeando ahora
            - 'fresco': True si el precio se actualizó hace < 12h

    GET  /api/producto/{id}/historial
        Histórico de precios del producto.

    GET  /api/comparar?nombre=...
        Devuelve productos cuyos nombres coinciden parcialmente con el
        criterio dado, agrupados por tienda. Sirve para comparar el mismo
        ítem entre tiendas.

Lógica de cache 12h:
    - Cuando se consulta /api/producto/{id}, se compara `ultima_actualizacion`.
    - Si tiene < 12h, se sirve directo de BD (campo `fresco=true`).
    - Si tiene >= 12h y no está siendo actualizado, se marca `actualizando=true`
      en BD, se devuelve el precio antiguo, y se lanza una BackgroundTask
      que re-scrapea con `re_scrapear_producto(url)`. La siguiente consulta
      del usuario ya verá el precio fresco.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

import db
from scrapers_base import re_scrapear_producto

app = FastAPI(
    title="TecnoPrice CO API",
    description="Comparador de precios de componentes en tiendas colombianas",
    version="1.0.0",
)

# CORS abierto para que el frontend (cualquier origen) pueda consumir la API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def producto_a_dict(p: db.Producto, fresco: bool) -> dict:
    """Serializa un Producto al formato que espera el frontend."""
    tienda_nombre = p.tienda.nombre if p.tienda else None
    return {
        "id":                  p.id,
        "tienda":              tienda_nombre,
        "categoria":           p.categoria,
        "nombre":              p.nombre,
        "precio":              p.precio_actual_texto,
        "precio_valor":        p.precio_actual_cop,
        "enlace":              p.enlace,
        "imagen":              p.imagen,
        "ultima_actualizacion": p.ultima_actualizacion.isoformat() if p.ultima_actualizacion else None,
        "actualizando":        bool(p.actualizando),
        "fresco":              fresco,
    }


async def tarea_rescrape(producto_id: int, url: str, precio_anterior: Optional[int] = None):
    """
    Tarea de background: re-scrapea un producto y actualiza su precio en BD.
    Marcado-desmarcado de `actualizando` se hace dentro. Si `precio_anterior`
    se proporciona, se valida que el nuevo precio no sea anómalo.
    """
    print(f"[BG] Re-scrapeando producto {producto_id}: {url}")
    resultado = await re_scrapear_producto(url, precio_anterior=precio_anterior)

    with db.SessionLocal() as session:
        if "error" in resultado:
            # Aun en error, desmarcar y aplicar cooldown de ~1h para evitar
            # disparar re-scrapes constantes a un producto que falla
            # (ej. agotado, sin precio visible, página caída).
            print(f"[BG] Error: {resultado['error']}")
            producto = session.get(db.Producto, producto_id)
            if producto:
                from datetime import datetime, timedelta, timezone
                producto.actualizando = False
                # Marcar como "actualizado hace 11h" → reintentará en ~1h
                cooldown = datetime.now(timezone.utc) - timedelta(
                    hours=db.UMBRAL_FRESCURA_HORAS - 1
                )
                producto.ultima_actualizacion = cooldown
                session.commit()
            return

        db.aplicar_precio_actualizado(
            session,
            producto_id,
            resultado.get("precio_valor"),
            resultado.get("precio_texto"),
        )
        print(f"[BG] OK producto {producto_id}: {resultado.get('precio_texto')}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "umbral_frescura_horas": db.UMBRAL_FRESCURA_HORAS}


@app.get("/api/stats")
def stats():
    """Estadísticas globales del catálogo."""
    with db.SessionLocal() as session:
        total = session.execute(select(func.count(db.Producto.id))).scalar() or 0

        # Por tienda
        rows = session.execute(
            select(db.Tienda.nombre, func.count(db.Producto.id))
            .join(db.Producto, db.Producto.tienda_id == db.Tienda.id)
            .group_by(db.Tienda.nombre)
        ).all()
        por_tienda = {r[0]: r[1] for r in rows}

        # Por categoría
        rows = session.execute(
            select(db.Producto.categoria, func.count(db.Producto.id))
            .group_by(db.Producto.categoria)
        ).all()
        por_categoria = {r[0]: r[1] for r in rows}

        # Precio promedio por categoría (solo precios no nulos)
        rows = session.execute(
            select(
                db.Producto.categoria,
                func.avg(db.Producto.precio_actual_cop),
                func.min(db.Producto.precio_actual_cop),
                func.max(db.Producto.precio_actual_cop),
            )
            .where(db.Producto.precio_actual_cop.is_not(None))
            .group_by(db.Producto.categoria)
        ).all()
        precios_categoria = {
            r[0]: {
                "promedio": int(r[1]) if r[1] is not None else None,
                "minimo":   int(r[2]) if r[2] is not None else None,
                "maximo":   int(r[3]) if r[3] is not None else None,
            }
            for r in rows
        }

        ultima = session.execute(
            select(func.max(db.Producto.ultima_actualizacion))
        ).scalar()

    return {
        "total_productos":     total,
        "tiendas":             len(por_tienda),
        "categorias":          len(por_categoria),
        "por_tienda":          por_tienda,
        "por_categoria":       por_categoria,
        "precios_por_categoria": precios_categoria,
        "ultima_actualizacion": ultima.isoformat() if ultima else None,
    }


@app.get("/api/productos")
def listar_productos(
    q:         Optional[str] = Query(None, description="Búsqueda por nombre"),
    categoria: Optional[str] = Query(None),
    tienda:    Optional[str] = Query(None),
    orden:     str           = Query("precio_asc", pattern="^(precio_asc|precio_desc|nombre)$"),
    pagina:    int           = Query(1,  ge=1),
    por_pagina: int          = Query(24, ge=1, le=100),
):
    """Listado paginado de productos con filtros."""
    with db.SessionLocal() as session:
        consulta = (
            select(db.Producto)
            .options(joinedload(db.Producto.tienda))
            .where(db.Producto.activo == True)  # noqa: E712
        )

        if q:
            consulta = consulta.where(db.Producto.nombre.ilike(f"%{q}%"))
        if categoria:
            consulta = consulta.where(db.Producto.categoria == categoria)
        if tienda:
            consulta = consulta.join(db.Tienda).where(db.Tienda.nombre == tienda)

        # Conteo total para paginación (sin order_by ni limit)
        total = session.execute(
            select(func.count()).select_from(consulta.subquery())
        ).scalar() or 0

        # Ordenamiento
        if orden == "precio_asc":
            consulta = consulta.order_by(
                db.Producto.precio_actual_cop.asc().nulls_last()
            )
        elif orden == "precio_desc":
            consulta = consulta.order_by(
                db.Producto.precio_actual_cop.desc().nulls_last()
            )
        else:
            consulta = consulta.order_by(db.Producto.nombre.asc())

        # Paginación
        consulta = consulta.offset((pagina - 1) * por_pagina).limit(por_pagina)

        productos = session.execute(consulta).scalars().all()
        items = [producto_a_dict(p, db.es_fresco(p)) for p in productos]

    return {
        "total":     total,
        "pagina":    pagina,
        "por_pagina": por_pagina,
        "paginas":   (total + por_pagina - 1) // por_pagina,
        "items":     items,
    }


@app.get("/api/producto/{producto_id}")
def detalle_producto(producto_id: int, background: BackgroundTasks):
    """
    Detalle del producto. Si el precio tiene > 12 h, dispara re-scrape en
    background y devuelve el precio actual marcado como `fresco=false`.
    """
    with db.SessionLocal() as session:
        producto = session.execute(
            select(db.Producto)
            .options(joinedload(db.Producto.tienda))
            .where(db.Producto.id == producto_id)
        ).scalar_one_or_none()

        if producto is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        fresco = db.es_fresco(producto)
        url = producto.enlace

        # Si no está fresco y no se está actualizando ya, marcar y disparar BG
        if not fresco and not producto.actualizando:
            disparado = db.marcar_en_actualizacion(session, producto.id)
            if disparado:
                # Volver a leer para reflejar el nuevo flag actualizando=True
                session.refresh(producto)
                background.add_task(tarea_rescrape, producto.id, url, producto.precio_actual_cop)

        respuesta = producto_a_dict(producto, fresco)
    return respuesta


@app.get("/api/producto/{producto_id}/historial")
def historial_producto(producto_id: int, limite: int = Query(50, ge=1, le=500)):
    """Historial de precios del producto (orden cronológico desc)."""
    with db.SessionLocal() as session:
        existe = session.execute(
            select(db.Producto.id).where(db.Producto.id == producto_id)
        ).scalar_one_or_none()
        if existe is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        rows = session.execute(
            select(db.HistorialPrecio)
            .where(db.HistorialPrecio.producto_id == producto_id)
            .order_by(db.HistorialPrecio.fecha.desc())
            .limit(limite)
        ).scalars().all()

        items = [
            {
                "precio_cop":   r.precio_cop,
                "precio_texto": r.precio_texto,
                "fecha":        r.fecha.isoformat() if r.fecha else None,
            }
            for r in rows
        ]

    return {"producto_id": producto_id, "items": items}


@app.get("/api/grupos")
def listar_grupos(
    q:          Optional[str] = Query(None, description="Búsqueda por nombre representativo"),
    categoria:  Optional[str] = Query(None),
    solo_multi: bool          = Query(False, description="Solo grupos con 2+ tiendas"),
    orden:      str           = Query("ahorro", pattern="^(ahorro|precio_asc|precio_desc|tiendas)$"),
    pagina:     int           = Query(1,  ge=1),
    por_pagina: int           = Query(24, ge=1, le=100),
):
    """
    Lista productos AGRUPADOS: cada item agrupa el mismo producto entre
    varias tiendas y destaca el precio mínimo, máximo y el ahorro absoluto.

    Cada grupo devuelto contiene:
        - grupo_clave
        - categoria
        - nombre        : nombre representativo (el más largo de las ofertas)
        - imagen        : una imagen disponible entre las ofertas
        - n_tiendas     : cuántas tiendas distintas lo ofrecen
        - precio_min, tienda_min
        - precio_max, tienda_max
        - ahorro_cop, ahorro_pct
        - ofertas       : lista de productos con (tienda, precio, enlace, ...)
    """
    with db.SessionLocal() as session:
        # Subconsulta: por cada grupo_clave, calcular agregados
        consulta = (
            select(db.Producto)
            .options(joinedload(db.Producto.tienda))
            .where(
                db.Producto.activo == True,  # noqa: E712
                db.Producto.grupo_clave.is_not(None),
            )
        )
        if q:
            consulta = consulta.where(db.Producto.nombre.ilike(f"%{q}%"))
        if categoria:
            consulta = consulta.where(db.Producto.categoria == categoria)

        productos = session.execute(consulta).scalars().all()

        # Agrupar en Python
        agrupados: dict[str, list[db.Producto]] = {}
        for p in productos:
            agrupados.setdefault(p.grupo_clave, []).append(p)

        grupos = []
        for clave, items in agrupados.items():
            tiendas_distintas = {i.tienda_id for i in items}
            if solo_multi and len(tiendas_distintas) < 2:
                continue

            # Filtrar solo ofertas con precio para los cálculos
            con_precio = [i for i in items if i.precio_actual_cop]

            if con_precio:
                precio_min_item = min(con_precio, key=lambda x: x.precio_actual_cop)
                precio_max_item = max(con_precio, key=lambda x: x.precio_actual_cop)
                precio_min = precio_min_item.precio_actual_cop
                precio_max = precio_max_item.precio_actual_cop
                ahorro = precio_max - precio_min
                ahorro_pct = round(ahorro / precio_max * 100, 1) if precio_max else 0
            else:
                precio_min_item = precio_max_item = None
                precio_min = precio_max = ahorro = ahorro_pct = None

            # Nombre representativo: el más largo
            nombre_rep = max(items, key=lambda x: len(x.nombre or "")).nombre
            # Imagen: la primera disponible
            imagen_rep = next((i.imagen for i in items if i.imagen), None)
            categoria_rep = items[0].categoria

            ofertas = sorted(
                [producto_a_dict(i, db.es_fresco(i)) for i in items],
                key=lambda x: (x["precio_valor"] is None, x["precio_valor"] or 0),
            )

            grupos.append({
                "grupo_clave":  clave,
                "categoria":    categoria_rep,
                "nombre":       nombre_rep,
                "imagen":       imagen_rep,
                "n_tiendas":    len(tiendas_distintas),
                "n_ofertas":    len(items),
                "precio_min":   precio_min,
                "tienda_min":   precio_min_item.tienda.nombre if precio_min_item else None,
                "precio_max":   precio_max,
                "tienda_max":   precio_max_item.tienda.nombre if precio_max_item else None,
                "ahorro_cop":   ahorro,
                "ahorro_pct":   ahorro_pct,
                "ofertas":      ofertas,
            })

        # Ordenar
        if orden == "ahorro":
            grupos.sort(key=lambda g: (g["ahorro_cop"] or 0), reverse=True)
        elif orden == "precio_asc":
            grupos.sort(key=lambda g: (g["precio_min"] is None, g["precio_min"] or 0))
        elif orden == "precio_desc":
            grupos.sort(key=lambda g: (g["precio_min"] or 0), reverse=True)
        elif orden == "tiendas":
            grupos.sort(key=lambda g: g["n_tiendas"], reverse=True)

        total = len(grupos)
        inicio = (pagina - 1) * por_pagina
        items_pag = grupos[inicio:inicio + por_pagina]

    return {
        "total":      total,
        "pagina":     pagina,
        "por_pagina": por_pagina,
        "paginas":    (total + por_pagina - 1) // por_pagina,
        "items":      items_pag,
    }


@app.get("/api/comparar")
def comparar_producto(nombre: str = Query(..., min_length=3)):
    """
    Busca productos cuyo nombre contenga `nombre`, agrupados por tienda
    y ordenados por precio ascendente. Útil para mostrar comparación entre
    tiendas del mismo (o similar) producto.
    """
    with db.SessionLocal() as session:
        productos = session.execute(
            select(db.Producto)
            .options(joinedload(db.Producto.tienda))
            .where(db.Producto.nombre.ilike(f"%{nombre}%"))
            .where(db.Producto.activo == True)  # noqa: E712
            .order_by(db.Producto.precio_actual_cop.asc().nulls_last())
        ).scalars().all()

    return {
        "criterio": nombre,
        "total":    len(productos),
        "items":    [producto_a_dict(p, db.es_fresco(p)) for p in productos],
    }


# ---------------------------------------------------------------------------
# Servir el frontend estático en /
# ---------------------------------------------------------------------------

# Si existe la carpeta frontend, servirla en la raíz '/'
import os as _os  # alias para evitar conflicto con `os` ya importado al inicio
from pathlib import Path as _Path

_FRONTEND_DIR = _Path(__file__).parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
