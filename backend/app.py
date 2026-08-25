"""5037 STRC→BTC 領先指標研究：Flask 後端（單一 port 5037，同時 serve 前端）。"""
import os
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory

import data
import analysis

TEMPLATES = os.path.join(os.path.dirname(__file__), "..", "templates")
CACHE_TTL = 1800  # 30 分鐘

app = Flask(__name__, static_folder=None)
_cache = {"ts": 0, "data": None}


@app.route("/")
def index():
    return send_from_directory(TEMPLATES, "index.html")


@app.route("/api/analysis")
def api_analysis():
    refresh = request.args.get("refresh") == "1"
    now = time.time()
    if refresh or not _cache["data"] or now - _cache["ts"] > CACHE_TTL:
        try:
            strc = data.build_dataset()
            _cache["data"] = analysis.analyze(strc)
            _cache["ts"] = now
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 502
    return jsonify({**_cache["data"], "cached_age_sec": int(now - _cache["ts"])})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5037, debug=False)
