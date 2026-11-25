release: python manage.py migrate --noinput && python manage.py compilemo && python manage.py collectstatic --noinput
web: gunicorn factcheck.wsgi --config gunicorn.conf.py
worker: python manage.py run_worker --pause 30
