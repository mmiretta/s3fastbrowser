#!/usr/bin/env python3
"""Development server: python run.py (use the Docker image / gunicorn in production)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

from app import create_app  # noqa: E402
from config import Config  # noqa: E402

if __name__ == '__main__':
    create_app('development').run(host=Config.HOST, port=Config.PORT,
                                  debug=os.environ.get('FLASK_DEBUG', '').lower() == 'true')
