# gunicorn_config.py

# Binding
bind = "0.0.0.0:10000"

# Worker processes
workers = 3  # A good rule is 2-4 × number of CPU cores
worker_class = "sync"  # Can be 'sync', 'gevent', 'eventlet', etc.
timeout = 120  # Seconds

# Logging
loglevel = "info"
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stdout

# Django specific
django_settings = "rome_ai_project.settings"

# Process naming
proc_name = "rome_ai_gunicorn"

# Reload when code changes (for development only - remove in production)
reload = True
