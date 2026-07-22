"""
CareerOS Core Engine
--------------------
Serves the CareerOS frontend (frontend/index.html) and backs its two calls:
  POST /api/search   -> {"sources": {"LinkedIn": [...], "Unstop": [...], ...}}
  POST /api/analyze  -> {"analysis": {...}}
  POST /api/match     (bonus, wired up but optional to use from the UI)
                      -> {"match": {...}}

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000 in a browser - the frontend is served
directly by this same server, so there are no CORS issues.
"""

import os
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from agents.scout_agent import run_scout
from agents.strategist_agent import run_strategist_agent
from dissection.recruiter_match_agent import run_recruiter_match_agent

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)  # harmless here since same-origin, but keeps things flexible if you split hosts later

# Portals the frontend always renders a card for, even if a scan returns zero
# results for one of them (keeps the grid layout stable between searches).
KNOWN_PORTALS = ["LinkedIn", "Unstop", "RemoteOK", "Arbeitnow", "Devpost"]


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/api/search", methods=["POST"])
def search():
    body = request.get_json(force=True, silent=True) or {}
    role = (body.get("role") or "Python Developer").strip()
    location = (body.get("location") or "Remote").strip()
    sources = body.get("sources")  # optional list, e.g. ["linkedin", "remoteok"]

    try:
        result = run_scout(role, location, sources=sources)  # {"jobs": [...], "hackathons": [...]}
    except Exception as e:
        return jsonify({"error": f"Scout Agent failed: {e}"}), 500

    # The frontend expects a dict keyed by portal name -> list of jobs.
    sources = {portal: [] for portal in KNOWN_PORTALS}
    for item in result["jobs"] + result["hackathons"]:
        sources.setdefault(item["source"], []).append(item)

    return jsonify({"sources": sources})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    body = request.get_json(force=True, silent=True) or {}
    title = body.get("title", "")
    company = body.get("company", "")
    location = body.get("location", "Remote")
    link = body.get("link", "")

    if not title or not company:
        return jsonify({"error": "title and company are required"}), 400

    try:
        analysis = run_strategist_agent(title, company, location, link)
    except Exception as e:
        return jsonify({"error": f"Strategist Agent failed: {e}"}), 500

    return jsonify({"analysis": analysis})


@app.route("/api/match", methods=["POST"])
def match():
    body = request.get_json(force=True, silent=True) or {}
    job = body.get("job", {})
    strategist_analysis = body.get("strategist_analysis", {})
    student_profile = body.get("student_profile", {})

    try:
        result = run_recruiter_match_agent(job, strategist_analysis, student_profile)
    except Exception as e:
        return jsonify({"error": f"Recruiter-Match Agent failed: {e}"}), 500

    return jsonify({"match": result})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
