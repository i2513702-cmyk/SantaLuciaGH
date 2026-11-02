import os
import sys

os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("SECRET_KEY", "clave-de-prueba-para-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
