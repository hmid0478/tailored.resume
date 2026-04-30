"""Vercel serverless function entry point.

Vercel's @vercel/python builder looks for `app` (a WSGI callable) in this file
and serves it. We import the Flask app from the project root.
"""
import os
import sys

# Make the project root importable so `import app` finds the top-level app.py.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app  # noqa: E402,F401
