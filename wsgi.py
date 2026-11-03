"""WSGI entry point for production servers (PythonAnywhere, Waitress, etc.).

Configuration such as SECRET_KEY comes from environment variables, which
the hosting platform sets; nothing secret is stored in this file.
"""
from app import create_app

application = create_app()
