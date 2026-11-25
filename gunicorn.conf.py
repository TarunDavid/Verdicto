"""gunicorn settings for production: `gunicorn factcheck.wsgi --config gunicorn.conf.py`."""
import multiprocessing
import os

bind = os.environ.get("BIND", "0.0.0.0:" + os.environ.get("PORT", "8000"))
workers = int(os.environ.get("WEB_CONCURRENCY", min(4, multiprocessing.cpu_count() * 2 + 1)))
timeout = 60            # chat submissions call the AI briefly before research moves to the worker
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
