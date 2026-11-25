# Deploying Verdicto

## 1. Server setup
```bash
git clone https://github.com/TarunDavid/Verdicto.git && cd Verdicto
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt "psycopg[binary]"
cp .env.production.example .env        # fill in every value
```

## 2. Database, cache, translations, static files
```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createcachetable      # because CACHE_BACKEND=db
.venv/bin/python manage.py compilemo             # Kannada UI
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser       # your admin (strong password); don't run seed_demo
.venv/bin/python manage.py check --deploy
```
`static/css/app.css` is committed. Rebuild it only after changing Tailwind classes:
`python manage.py buildcss` (needs the Tailwind standalone CLI in `tools/`).

## 3. Processes
Two long-running processes (see `Procfile`; on a PaaS like Render/Railway/Heroku this is automatic):
```bash
.venv/bin/gunicorn factcheck.wsgi --config gunicorn.conf.py        # web
.venv/bin/python manage.py run_worker --pause 30                    # AI research worker
```
With systemd, create one service per command (`Restart=always`, `EnvironmentFile=/path/.env`).

## 4. nginx (TLS, static and uploaded files)
```nginx
server {
    listen 443 ssl http2;
    server_name verdicto.example.org;
    ssl_certificate     /etc/letsencrypt/live/verdicto.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/verdicto.example.org/privkey.pem;
    client_max_body_size 6m;                       # screenshots are capped at 5 MB

    location /static/ { alias /srv/verdicto/staticfiles/; expires 30d; }
    location /media/  { alias /srv/verdicto/media/; add_header X-Content-Type-Options nosniff; }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
server { listen 80; server_name verdicto.example.org; return 301 https://$host$request_uri; }
```

## 5. Checklist
- [ ] `DEBUG=False`, strong `SECRET_KEY`, real `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS`
- [ ] Postgres + `CACHE_BACKEND=db` (or redis) so rate limits work across gunicorn workers
- [ ] `RESEARCH_MODE=worker` and the worker process running
- [ ] SMTP configured (work-email codes); Twilio configured if `PHONE_OTP_ENABLED=True`
- [ ] `RESEARCH_CONTACT` points to a real page or email (Wikipedia requires it)
- [ ] Backups for the database and `media/`
- [ ] `python manage.py check --deploy` clean (HSTS preload is optional)
