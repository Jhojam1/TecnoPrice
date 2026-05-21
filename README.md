# TecnoPrice CO — Comparador de Precios de Componentes

Plataforma completa de comparación de precios de componentes de computador en tiendas colombianas. Incluye scrapers automáticos, base de datos PostgreSQL, API REST con caché inteligente, frontend interactivo y scheduler de actualización.

**Características principales:**
- 🔄 Scraping automático de 4 tiendas (Speedlogic, Computer Working, Tauret, Clones y Periféricos)
- 💾 Base de datos PostgreSQL con historial de precios
- ⚡ API REST con caché de 12 horas y re-scraping en background
- 📅 Scheduler automático de actualización cada 24 horas

---

## 🏪 Tiendas soportadas

| Tienda | Plataforma | Categorías |
|--------|-----------|-----------|
| **Speedlogic** | WooCommerce | RAM, SSD, Tarjetas Gráficas |
| **Computer Working** | Custom (PHP) | RAM, SSD, Tarjetas Gráficas |
| **Tauret Computadores** | SPA Vue.js | RAM, SSD (4 variantes), GPU (NVIDIA + AMD) |
| **Clones y Periféricos** | WooCommerce | RAM, SSD, Tarjetas Gráficas |

---

## 📦 Instalación

### Requisitos previos
- Python 3.10+
- PostgreSQL (o Neon para la nube)
- Git

### Pasos

1. **Clonar el repositorio**
```bash
git clone https://github.com/tu-usuario/tecno-price-co.git
cd tecno-price-co
```

2. **Crear entorno virtual**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

3. **Instalar dependencias**
```powershell
pip install -r requirements.txt
playwright install
```

4. **Configurar variables de entorno**
Crea un archivo `.env` en la raíz:
```
DATABASE_URL=postgresql://usuario:contraseña@host:5432/tecno_price
```

---

## 🚀 Uso

### Iniciar la aplicación completa
```powershell
python start.py
```
Abre automáticamente `http://localhost:8000` en tu navegador.



---

## 🏗️ Arquitectura

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (HTML+JS)                   │
│              Búsqueda, filtros, comparación             │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP/JSON
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   FastAPI (api.py)                      │
│  • /api/productos      - Listado paginado              │
│  • /api/producto/{id}  - Detalle + re-scrape bg       │
│  • /api/comparar       - Comparación entre tiendas     │
│  • /api/historial      - Evolución de precios          │
│  • /api/stats          - Estadísticas globales         │
└────────────────────────┬────────────────────────────────┘
                         │ 
                         ▼
┌─────────────────────────────────────────────────────────┐
│              PostgreSQL (Neon)                          │
│  ┌──────────┬──────────┬──────────────────┐            │
│  │ tiendas  │productos │historial_precios │            │
│  └──────────┴──────────┴──────────────────┘            │
└────────────────────────▲────────────────────────────────┘
                         │ 
                         │
┌────────────────────────┴────────────────────────────────┐
│              Scrapers (Playwright)                      │
│  • scraper_speedlogic.py                               │
│  • scraper_computerworking.py                          │
│  • scraper_tauret.py                                   │
│  • scraper_clonesyperifericos.py                       │
└────────────────────────▲────────────────────────────────┘
                         │
                         │ 
                         │
┌────────────────────────┴────────────────────────────────┐
│           Scheduler (APScheduler)                       │
└─────────────────────────────────────────────────────────┘
```

---

## ⏱️ Lógica de caché (12 horas)

El sistema implementa un caché inteligente para optimizar consultas:

1. **Usuario solicita detalle de producto** → `/api/producto/{id}`
2. **API verifica antigüedad del precio:**
   - ✅ **< 12 horas**: Devuelve precio de BD
   - ⏳ **≥ 12 horas**: Marca como `actualizando=true` y dispara re-scrape en background
3. **Re-scrape en background:**
   - Abre la página con Playwright
   - Extrae el precio actual usando selectores CSS específicos por tienda
   - Valida que no sea anómalo (entre 30% y 300% del anterior)
   - Actualiza BD e inserta en historial
4. **Siguiente consulta**: Ya ve el precio actualizado


## 📁 Estructura del proyecto

```
tecno-price-co/
├── start.py                      # Punto de entrada (API + frontend)
├── api.py                        # FastAPI con endpoints REST
├── db.py                         # Modelos SQLAlchemy + conexión
├── scheduler.py                  # APScheduler para actualizaciones automáticas
├── carga_inicial.py              # Carga inicial de datos desde scrapers
│
├── scrapers_base.py              # Utilidades comunes (parsear precio, re-scrape)
├── scraper_speedlogic.py         # Scraper Speedlogic (WooCommerce)
├── scraper_computerworking.py    # Scraper Computer Working
├── scraper_tauret.py             # Scraper Tauret (SPA Vue.js)
├── scraper_clonesyperifericos.py # Scraper Clones y Periféricos
│
├── frontend/
│   └── index.html                # UI (HTML + CSS + JS vanilla)
│
├── requirements.txt              # Dependencias Python
├── runtime.txt                   # Versión de Python
├── render.yaml                   # Configuración para despliegue Render
├── .env                          # Variables de entorno (gitignored)
├── .gitignore
├── DEPLOYMENT.md                 # Guía de despliegue
└── README.md                     # Este archivo
```

---

## 🔌 Endpoints de la API

### Información general
```
GET /api/health
```
Estado del servicio y umbral de frescura.

```
GET /api/stats
```
Estadísticas: total productos, por tienda, por categoría, precios promedio.

### Productos
```
GET /api/productos?q=memoria&categoria=RAM&tienda=Speedlogic&orden=precio_asc&pagina=1&por_pagina=24
```
Listado paginado con filtros. Parámetros:
- `q`: Búsqueda por nombre
- `categoria`: Filtrar por categoría
- `tienda`: Filtrar por tienda
- `orden`: `precio_asc`, `precio_desc`, `nombre`
- `pagina`, `por_pagina`: Paginación

```
GET /api/producto/{id}
```
Detalle de un producto. Dispara re-scrape automático si el precio tiene > 12h.

Respuesta incluye:
- `fresco`: `true` si el precio se actualizó hace < 12h
- `actualizando`: `true` si se está re-scrapeando ahora

```
GET /api/producto/{id}/historial?limite=50
```
Historial de precios del producto (últimas 50 por defecto).

### Comparación
```
GET /api/comparar?nombre=RTX%205070
```
Busca productos con nombre similar y los agrupa por tienda para comparación.

### Agrupación
```
GET /api/grupos?q=memoria&categoria=RAM&solo_multi=true&orden=ahorro&pagina=1&por_pagina=24
```
Agrupa el mismo producto entre varias tiendas, mostrando:
- Precio mínimo y máximo
- Ahorro absoluto y porcentual
- Todas las ofertas disponibles

---

## 🛠️ Variables de entorno

| Variable | Requerida | Default | Descripción |
|----------|-----------|---------|-------------|
| `DATABASE_URL` | ✅ Sí | - | Cadena de conexión PostgreSQL (ej: `postgresql://user:pass@host/db`) |

**Ejemplo `.env`:**
```
DATABASE_URL=postgresql://neon_user:password@ep-cool-name.neon.tech/tecno_price?sslmode=require
SCRAPER_HEADLESS=true
```

---

## 🎯 Características técnicas

### Scraping robusto
- **Playwright** para JavaScript rendering
- Selectores CSS específicos por tienda
- Validación de precios anómalos (detección de productos relacionados)

### Base de datos
- **PostgreSQL** con relaciones normalizadas
- Historial de precios 
- Índices en URLs para búsquedas rápidas
- Timestamps automáticos

### API
- **FastAPI** con validación Pydantic
- CORS habilitado para cualquier origen
- Paginación eficiente
- Caché inteligente de 12 horas
- Re-scraping en background sin bloquear

### Frontend
- HTML5 + CSS3 + JavaScript vanilla (sin dependencias)
- Búsqueda en tiempo real
- Filtros por categoría y tienda
- Comparación de precios entre tiendas
- Historial visual de precios
- Responsive design

---

## 📊 Datos extraídos por producto

```json
{
  "id": 1,
  "tienda": "Speedlogic",
  "categoria": "Tarjetas Gráficas",
  "nombre": "NVIDIA RTX 5070 12GB GDDR7",
  "precio": "$ 4.063.000",
  "precio_valor": 4063000,
  "enlace": "https://speedlogic.com.co/...",
  "imagen": "https://...",
  "ultima_actualizacion": "2026-05-21T14:30:00",
  "fresco": true,
  "actualizando": false
}
```