"""Vercel serverless function entry point."""
import os
import sys

# Add project root to Python path so we can import app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app  # noqa: E402
