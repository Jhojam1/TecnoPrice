"""
TecnoPrice CO - Capa de base de datos (PostgreSQL / Neon)
----------------------------------------------------------
Define el motor SQLAlchemy, los modelos ORM y funciones de acceso.

Modelo de datos:

    tiendas
        id (PK), nombre (UNIQUE), base_url, fecha_creacion
    productos
        id (PK), tienda_id (FK), categoria, nombre, enlace (UNIQUE),
        imagen, precio_actual_cop, precio_actual_texto,
        ultima_actualizacion, actualizando, fecha_creacion, activo
    historial_precios
        id (PK), producto_id (FK), precio_cop, precio_texto, fecha

Filosofía:
    - `productos.enlace` es la clave natural para hacer upserts.
    - `productos.ultima_actualizacion` se compara con UMBRAL_FRESCURA_HORAS
      en la API para decidir si se debe re-scrapear.
    - `productos.actualizando` es una bandera para evitar disparar varios
      re-scrapeos en paralelo del mismo producto.
    - Cada cambio de precio agrega una fila a `historial_precios`.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, create_engine, func, select, text,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from normalizador import normalizar_producto


# ---------------------------------------------------------------------------
# Configuración del motor
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "No se encontró DATABASE_URL en .env. Crea el archivo .env con la "
        "cadena de conexión a Neon/PostgreSQL."
    )

# Umbral de frescura: si un precio se actualizó hace menos de este tiempo,
# se sirve directo de BD; si no, se dispara re-scrape asíncrono.
UMBRAL_FRESCURA_HORAS = 12

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class Tienda(Base):
    __tablename__ = "tiendas"

    id              = Column(Integer, primary_key=True)
    nombre          = Column(String(100), unique=True, nullable=False)
    base_url        = Column(String(255), nullable=False)
    fecha_creacion  = Column(DateTime(timezone=True), server_default=func.now())

    productos = relationship("Producto", back_populates="tienda")


class Producto(Base):
    __tablename__ = "productos"

    id                    = Column(Integer, primary_key=True)
    tienda_id             = Column(Integer, ForeignKey("tiendas.id"), nullable=False, index=True)
    categoria             = Column(String(100), nullable=False, index=True)
    nombre                = Column(Text, nullable=False)
    enlace                = Column(Text, nullable=False, unique=True)
    imagen                = Column(Text, nullable=True)
    precio_actual_cop     = Column(Integer, nullable=True)
    precio_actual_texto   = Column(String(100), nullable=True)
    ultima_actualizacion  = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    actualizando          = Column(Boolean, default=False, nullable=False)
    fecha_creacion        = Column(DateTime(timezone=True), server_default=func.now())
    activo                = Column(Boolean, default=True, nullable=False)

    # Clave de agrupación: productos con la misma clave son "el mismo producto"
    # entre tiendas (ej: 'RAM|DDR4|16GB|3200|PC|CORSAIR|VENGEANCE').
    grupo_clave           = Column(String(255), nullable=True, index=True)

    tienda    = relationship("Tienda", back_populates="productos")
    historial = relationship("HistorialPrecio", back_populates="producto", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("enlace", name="uq_productos_enlace"),)


class HistorialPrecio(Base):
    __tablename__ = "historial_precios"

    id            = Column(Integer, primary_key=True)
    producto_id   = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False, index=True)
    precio_cop    = Column(Integer, nullable=True)
    precio_texto  = Column(String(100), nullable=True)
    fecha         = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    producto = relationship("Producto", back_populates="historial")


# ---------------------------------------------------------------------------
# Operaciones de alto nivel
# ---------------------------------------------------------------------------

def crear_tablas():
    """Crea todas las tablas si no existen."""
    Base.metadata.create_all(engine)


def obtener_o_crear_tienda(session: Session, nombre: str, base_url: str) -> Tienda:
    """Busca una tienda por nombre; la crea si no existe."""
    tienda = session.execute(
        select(Tienda).where(Tienda.nombre == nombre)
    ).scalar_one_or_none()
    if tienda is None:
        tienda = Tienda(nombre=nombre, base_url=base_url)
        session.add(tienda)
        session.flush()
    return tienda


def upsert_producto(session: Session, tienda_id: int, datos: dict) -> Producto:
    """
    Inserta o actualiza un producto identificado por su `enlace`.

    Si el precio cambió respecto al anterior (o no había precio),
    registra una nueva entrada en `historial_precios`.

    Devuelve el modelo `Producto` (creado o actualizado).
    """
    producto = session.execute(
        select(Producto).where(Producto.enlace == datos["enlace"])
    ).scalar_one_or_none()

    nuevo_precio = datos.get("precio_valor")
    nuevo_texto  = datos.get("precio")
    ahora = datetime.now(timezone.utc)

    # Calcular clave de agrupación a partir del nombre y categoría
    grupo = normalizar_producto(datos["nombre"], datos.get("categoria") or "")
    grupo_clave = grupo.get("grupo_clave")

    if producto is None:
        producto = Producto(
            tienda_id=tienda_id,
            categoria=datos.get("categoria") or "",
            nombre=datos["nombre"],
            enlace=datos["enlace"],
            imagen=datos.get("imagen"),
            precio_actual_cop=nuevo_precio,
            precio_actual_texto=nuevo_texto,
            ultima_actualizacion=ahora,
            actualizando=False,
            activo=True,
            grupo_clave=grupo_clave,
        )
        session.add(producto)
        session.flush()
        if nuevo_precio is not None:
            session.add(HistorialPrecio(
                producto_id=producto.id,
                precio_cop=nuevo_precio,
                precio_texto=nuevo_texto,
            ))
    else:
        # Actualizar campos descriptivos (por si cambió nombre/imagen/categoría)
        producto.nombre = datos["nombre"]
        producto.categoria = datos.get("categoria") or producto.categoria
        producto.grupo_clave = grupo_clave
        if datos.get("imagen"):
            producto.imagen = datos["imagen"]

        # Si el precio cambió, agregar al historial
        if nuevo_precio is not None and nuevo_precio != producto.precio_actual_cop:
            session.add(HistorialPrecio(
                producto_id=producto.id,
                precio_cop=nuevo_precio,
                precio_texto=nuevo_texto,
            ))
            producto.precio_actual_cop = nuevo_precio
            producto.precio_actual_texto = nuevo_texto

        producto.ultima_actualizacion = ahora
        producto.actualizando = False
        producto.activo = True

    return producto


def es_fresco(producto: Producto, umbral_horas: int = UMBRAL_FRESCURA_HORAS) -> bool:
    """True si el precio del producto fue actualizado en las últimas N horas."""
    if producto.ultima_actualizacion is None:
        return False
    ahora = datetime.now(timezone.utc)
    ultima = producto.ultima_actualizacion
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=timezone.utc)
    return (ahora - ultima) < timedelta(hours=umbral_horas)


def marcar_en_actualizacion(session: Session, producto_id: int) -> bool:
    """
    Marca el producto como 'actualizando' para evitar disparos paralelos.
    Devuelve True si lo logró marcar (no estaba ya en actualización).
    """
    producto = session.get(Producto, producto_id)
    if producto is None or producto.actualizando:
        return False
    producto.actualizando = True
    session.commit()
    return True


def aplicar_precio_actualizado(
    session: Session,
    producto_id: int,
    precio_valor: Optional[int],
    precio_texto: Optional[str],
):
    """Aplica un precio fresco recién obtenido por re-scrape al producto."""
    producto = session.get(Producto, producto_id)
    if producto is None:
        return

    if precio_valor is not None and precio_valor != producto.precio_actual_cop:
        session.add(HistorialPrecio(
            producto_id=producto.id,
            precio_cop=precio_valor,
            precio_texto=precio_texto,
        ))
        producto.precio_actual_cop = precio_valor
        producto.precio_actual_texto = precio_texto

    producto.ultima_actualizacion = datetime.now(timezone.utc)
    producto.actualizando = False
    session.commit()


# ---------------------------------------------------------------------------
# Inicialización al importar (idempotente)
# ---------------------------------------------------------------------------

def migrar_grupo_clave():
    """
    Migración idempotente:
        1. Agrega la columna `grupo_clave` si no existe.
        2. Recalcula la clave para todos los productos en BD.
    """
    print("Aplicando migración 'grupo_clave'...")
    with engine.begin() as conn:
        # Agregar columna si no existe
        conn.execute(text("""
            ALTER TABLE productos
            ADD COLUMN IF NOT EXISTS grupo_clave VARCHAR(255)
        """))
        # Asegurar índice
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_productos_grupo_clave
            ON productos(grupo_clave)
        """))

    # Recalcular claves
    with SessionLocal() as session:
        productos = session.execute(select(Producto)).scalars().all()
        cambios = 0
        for p in productos:
            grupo = normalizar_producto(p.nombre, p.categoria)
            nueva = grupo.get("grupo_clave")
            if p.grupo_clave != nueva:
                p.grupo_clave = nueva
                cambios += 1
        session.commit()
    print(f"[OK] Migración completa. Productos actualizados: {cambios}")


if __name__ == "__main__":
    print(f"Conectando a: {DATABASE_URL.split('@')[1].split('/')[0]}")
    print("Creando tablas...")
    crear_tablas()
    print("[OK] Tablas creadas/verificadas.")
    migrar_grupo_clave()
