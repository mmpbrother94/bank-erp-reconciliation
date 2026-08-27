"""cPanel / WHM entry point (Phusion Passenger).

Setup in cPanel -> "Setup Python App":

    Application root        : reco            (folder you upload this project to)
    Application URL         : yourdomain.com/reco
    Application startup file: passenger_wsgi.py
    Application Entry point : application

Then "Run Pip Install" against requirements.txt and restart the app.
No new domain is needed - the portal lives on a route of the existing one.

The mount point is taken from Passenger automatically; set RECO_URL_PREFIX in
the app's environment variables only if links come out wrong.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webapp import app as application  # noqa: E402

# Where uploads and generated files live. Keep it outside public_html.
os.makedirs(os.environ.get("RECO_JOBS", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "jobs")), exist_ok=True)
