# 🏛️ Rome AI Initiative

## About the Project

The Rome AI Initiative website is built with Django, showcasing our research and applications of artificial intelligence in the study and preservation of Roman history and culture.

## 🚀 Getting Started

### Prerequisites

- Python 3.8+
- pip

### Installation

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Prepare static assets:
   ```bash
   python manage.py collectstatic
   ```

## 🖥️ Running the Application

### Development Server

For local development:
```bash
python manage.py runserver 0.0.0.0:10000
```

### Production Server (Gunicorn)

For production deployment:
```bash
gunicorn -c gunicorn_config.py rome_ai_project.wsgi:application
```

## 📝 Configuration

### Gunicorn Configuration

A sample `gunicorn_config.py` is included with recommended production settings:
```python
# Binding
bind = "0.0.0.0:10000"

# Worker processes
workers = 3
worker_class = "sync"
timeout = 120

# Logging
loglevel = "info"
accesslog = "-"
errorlog = "-"
```

## 🔧 Additional Settings

For production environments:
- Set `DEBUG = False` in settings.py
- Configure proper `ALLOWED_HOSTS`
- Use a proper database (PostgreSQL recommended)
- Set up proper static file serving (WhiteNoise or Nginx)

## 🛡️ License

This project is licensed under the MIT License.

---

Built with ❤️ by the Rome AI Initiative Team
