# Guía de Despliegue — TecnoPrice CO

Instrucciones detalladas para desplegar la aplicación en producción.

---

## 🌐 Opción 1: Render.com (Recomendado - Gratis)

### Ventajas
- ✅ Gratis (plan free)
- ✅ Despliegue automático desde GitHub
- ✅ SSL incluido
- ✅ Integración con PostgreSQL (Neon)

### Pasos

#### 1. Crear base de datos en Neon
1. Ve a [neon.tech](https://neon.tech)
2. Crea una cuenta gratis (con email o GitHub)
3. Crea un nuevo proyecto PostgreSQL
4. Copia la cadena de conexión completa (algo como: `postgresql://user:password@ep-xxx.neon.tech/tecno_price?sslmode=require`)

#### 2. Preparar GitHub
1. Sube tu repositorio a GitHub
2. Ve a tu repositorio → Settings → Secrets and variables → Actions
3. Crea un nuevo secret:
   ```
   Name: DATABASE_URL
   Value: postgresql://user:password@ep-xxx.neon.tech/tecno_price?sslmode=require
   ```
   (Reemplaza con tu cadena de conexión de Neon)

#### 3. Desplegar en Render
1. Ve a [render.com](https://render.com)
2. Crea una cuenta (conecta con GitHub)
3. Click en "New" → "Blueprint"
4. Selecciona tu repositorio
5. Render detectará automáticamente `render.yaml`
6. En "Environment Variables", agrega:
   ```
   DATABASE_URL = postgresql://user:password@ep-xxx.neon.tech/tecno_price?sslmode=require
   ```
7. Click en "Deploy Blueprint"
8. Espera a que termine (10-15 minutos)
   - Primero instala dependencias Python
   - Luego descarga Chromium (puede tardar)
   - Finalmente inicia la API

#### 4. Verificar despliegue
- Abre `https://tu-app.onrender.com`
- Verifica que funciona: `https://tu-app.onrender.com/api/health`
- Deberías ver: `{"status": "ok", "umbral_frescura_horas": 12}`

#### 5. Configurar scraping automático
El plan free de Render duerme tras 15 minutos sin actividad. Para scraping automático cada 24h:

GitHub Actions ejecutará automáticamente los scrapers (ver `.github/workflows/scraper-diario.yml`). 
Ya está configurado, solo necesita que `DATABASE_URL` esté en los secrets de GitHub.

**Verificar que funciona:**
1. Ve a tu repositorio en GitHub
2. Actions → "Scraping Automático Diario"
3. Debería ejecutarse automáticamente cada día a las 8:00 UTC (3:00 AM Colombia)

---

## 🚀 Opción 2: Heroku

### Ventajas
- ✅ Fácil de usar
- ✅ Integración con GitHub
- ❌ Plan free descontinuado (requiere tarjeta de crédito)

### Pasos

```bash
# 1. Instalar Heroku CLI
# Descargar desde https://devcenter.heroku.com/articles/heroku-cli

# 2. Autenticarse
heroku login

# 3. Crear aplicación
heroku create tu-app-name

# 4. Agregar PostgreSQL
heroku addons:create heroku-postgresql:standard-0

# 5. Configurar variables de entorno
heroku config:set SCRAPER_HEADLESS=true

# 6. Desplegar
git push heroku main

# 7. Ver logs
heroku logs --tail
```

---

## 🖥️ Opción 3: DigitalOcean / AWS / VPS

### Requisitos
- Servidor Linux (Ubuntu 22.04 recomendado)
- Python 3.10+
- PostgreSQL 14+
- Nginx (reverse proxy)

### Pasos

#### 1. Conectarse al servidor
```bash
ssh root@tu-servidor-ip
```

#### 2. Instalar dependencias
```bash
apt update && apt upgrade -y
apt install -y python3.12 python3.12-venv postgresql postgresql-contrib nginx git
```

#### 3. Clonar repositorio
```bash
cd /opt
git clone https://github.com/tu-usuario/tecno-price-co.git
cd tecno-price-co
```

#### 4. Crear entorno virtual
```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium --with-deps
```

#### 5. Configurar base de datos
```bash
sudo -u postgres psql
CREATE DATABASE tecno_price;
CREATE USER tecno_user WITH PASSWORD 'tu-contraseña-segura';
GRANT ALL PRIVILEGES ON DATABASE tecno_price TO tecno_user;
\q
```

#### 6. Configurar variables de entorno
```bash
cat > .env << EOF
DATABASE_URL=postgresql://tecno_user:tu-contraseña-segura@localhost:5432/tecno_price
SCRAPER_HEADLESS=true
EOF
```

#### 7. Crear servicio systemd
```bash
sudo cat > /etc/systemd/system/tecnoprice.service << EOF
[Unit]
Description=TecnoPrice CO API
After=network.target

[Service]
Type=notify
User=www-data
WorkingDirectory=/opt/tecno-price-co
Environment="PATH=/opt/tecno-price-co/venv/bin"
ExecStart=/opt/tecno-price-co/venv/bin/python start.py --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tecnoprice
sudo systemctl start tecnoprice
```

#### 8. Configurar Nginx como reverse proxy
```bash
sudo cat > /etc/nginx/sites-available/tecnoprice << EOF
server {
    listen 80;
    server_name tu-dominio.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/tecnoprice /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

#### 9. Configurar SSL (Let's Encrypt)
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d tu-dominio.com
```

#### 10. Configurar scraping automático (cron)
```bash
crontab -e
# Agregar línea:
0 8 * * * cd /opt/tecno-price-co && source venv/bin/activate && python carga_inicial.py
```

---

## 🔄 Actualizar en producción

### Render.com
```bash
git push origin main
# Render detecta automáticamente y redeploya
```

### Heroku
```bash
git push heroku main
```

### VPS
```bash
cd /opt/tecno-price-co
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart tecnoprice
```

---

## 📊 Monitoreo

### Verificar que la API funciona
```bash
curl https://tu-dominio.com/api/health
```

Respuesta esperada:
```json
{
  "status": "ok",
  "umbral_frescura_horas": 12
}
```

### Ver logs

**Render:**
```bash
# En el dashboard de Render
```

**Heroku:**
```bash
heroku logs --tail
```

**VPS:**
```bash
sudo journalctl -u tecnoprice -f
```

### Monitorear base de datos
```bash
psql postgresql://user:password@host/tecno_price
SELECT COUNT(*) FROM productos;
SELECT COUNT(*) FROM historial_precios;
```

---

## 🔐 Seguridad

### Checklist
- [ ] DATABASE_URL está en variables de entorno (no en código)
- [ ] SCRAPER_HEADLESS=true en producción
- [ ] SSL/HTTPS configurado
- [ ] Firewall solo permite puertos 80, 443, 22
- [ ] Backups automáticos de BD configurados
- [ ] Logs monitoreados regularmente

### Backups de base de datos

**Neon (automático):**
- Neon hace backups automáticos cada 24h

**PostgreSQL manual:**
```bash
pg_dump postgresql://user:password@host/tecno_price > backup.sql
```

---

## 🐛 Troubleshooting

### Error: "No se puede conectar a la BD"
```bash
# Verificar que DATABASE_URL es correcto
echo $DATABASE_URL

# Probar conexión
psql $DATABASE_URL -c "SELECT 1"
```

### Error: "Playwright executable not found"
```bash
python -m playwright install chromium --with-deps
```

### API lenta
- Aumentar recursos en Render/Heroku
- Optimizar índices en PostgreSQL
- Implementar caché Redis

### Scraping falla
- Verificar que los selectores CSS siguen siendo válidos
- Revisar logs: `heroku logs --tail` o `journalctl -u tecnoprice -f`
- Ejecutar manualmente: `python carga_inicial.py`

---

## 📞 Soporte

- Issues en GitHub: https://github.com/tu-usuario/tecno-price-co/issues
- Documentación: Ver README.md
- Contribuir: Ver CONTRIBUTING.md
