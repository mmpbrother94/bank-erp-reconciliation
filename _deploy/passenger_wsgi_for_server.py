import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("RECO_PASSWORD", "Surya@Reco2026")
os.environ.setdefault("RECO_SECRET_KEY", "89a634dcb4bbe7e0603a49c96a0bd020c71f4607a7b72a65a81ce38a70fb9230")
os.environ.setdefault("RECO_HTTPS_ONLY", "1")

from webapp import app as application
