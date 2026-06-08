import csv
import json
import redis
import requests
import os
import re
import socket
import concurrent.futures
import io
import logging
from logging.handlers import RotatingFileHandler
from werkzeug.exceptions import HTTPException
from flask import Flask, render_template, request, redirect, url_for, flash, abort, make_response

app = Flask(__name__)

LOG_PATH = "/var/log/subcrawler.log"


def setup_logger():
    logger = logging.getLogger("subcrawler")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        try:
            handler = RotatingFileHandler(LOG_PATH, maxBytes=10_485_760, backupCount=3, encoding="utf-8")
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            logger.addHandler(handler)
        except Exception as exc:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            logger.addHandler(console_handler)
            logger.error("Unable to create log file %s; falling back to stdout. %s", LOG_PATH, exc)

    return logger


logger = setup_logger()

def _resolve_single(host: str) -> dict:
    try:
        ip = socket.gethostbyname(host)
    except Exception:
        ip = "unresolvable"
    return {"host": host, "ip": ip}

def resolve_hostnames(domains, max_workers: int = 20):
    """
    Resolve many hostnames concurrently.

    domains: iterable of hostnames
    returns: list[{"host": ..., "ip": ...}]
    """
    hosts = sorted(set(domains))  # still dedupe + sort

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all lookups at once
        future_to_host = {executor.submit(_resolve_single, h): h for h in hosts}

        # Collect as they finish (order doesn’t matter here)
        for future in concurrent.futures.as_completed(future_to_host):
            result = future.result()
            results.append(result)

    # Keep final list sorted by hostname for stable UI/CSV/Redis
    return sorted(results, key=lambda x: x["host"])

def load_key():
    secret_path = "/etc/app-seckey/.secret-key"
    if os.path.exists(secret_path):
        with open(secret_path, "r") as f:
            return f.read().strip()
    return os.urandom(32)
api_token = load_key()

app.secret_key = api_token
redis_host = os.getenv("REDIS_HOST", "redis")
csv_path = "/app/csv_db/"
r = redis.StrictRedis(host=redis_host, port=6379, decode_responses=True)

s = requests.Session()

def crawl(domain_to_crawl: str):
    params = {"q": domain_to_crawl}
    try:
        response = s.get("https://crt.sh/json", params=params, timeout=120)
        if response.status_code == 200:
            return response.text
        logger.error("crt.sh returned status %s for %s", response.status_code, domain_to_crawl)
        return None
    except requests.exceptions.RequestException as e:
        logger.exception("crt.sh request failed for %s", domain_to_crawl)
        return None

DOMAIN_RE = re.compile(
    r'^(?=.{1,253}$)(?!-)([A-Za-z0-9-]{1,63}(?<!-).)+[A-Za-z]{2,63}$'
)

def create_if_not_exist():
    try:
        with open(f"{csv_path}domain_results.csv", "x") as f:
            pass
    except FileExistsError:
        logger.info("CSV file already exists: %sdomain_results.csv", csv_path)

def is_valid_domain(name: str) -> bool:
    return DOMAIN_RE.match(name) is not None

def parse_results(json_results: str, base_domain: str):
    """
    Parse crt.sh JSON output and return a set of domains containing base_domain.
    Normalizes entries like '*.sub.example.com' to 'sub.example.com'.
    """
    domains = set()

    if not json_results:
        return domains

    try:
        data = json.loads(json_results)

        for v in data:
            name_value = v.get("name_value", "")

            # Normalize both real '\n' and literal '\n' sequences, then split
            for raw in str(name_value).replace("\\n", "\n").splitlines():
                item = raw.strip()
                if not item:
                    continue

                if item.startswith("*."):
                    item = item[2:]  # remove '*.' prefix

                if base_domain in item:
                    if is_valid_domain(item) and "@" not in item:
                        domains.add(item)

    except Exception:
        logger.exception("Failed to parse crt.sh JSON output for %s", base_domain)

    return domains

def write_to_redis(domain, crawl_results):
    """
    crawl_results: list of {"host": ..., "ip": ...}
    Store each as a JSON string in a Redis set keyed by the submitted domain.
    """
    if not crawl_results:
        return
    try:
        payloads = [json.dumps(entry, sort_keys=True) for entry in crawl_results]
        r.sadd(domain, *payloads)
    except Exception:
        logger.exception("Failed to write crawl results to Redis for %s", domain)

def get_from_redis(domain):
    """
    Return list[{"host": ..., "ip": ...}] for a submitted domain.
    Assumes each set member is a JSON object with 'host' and 'ip'.
    """
    members = r.smembers(domain)
    results = []
    for m in members:
        try:
            obj = json.loads(m)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "host" in obj and "ip" in obj:
            results.append(obj)
    return sorted(results, key=lambda x: x["host"])


def csv_to_redis_init():
    logger.info("Initializing Redis and appending crawl results...")
    try:
        with open(f"{csv_path}domain_results.csv", "r", newline="") as csv_read:
            reader = csv.reader(csv_read)
            for row in reader:
                if row:
                    domain = row[0]
                    try:
                        data = json.loads(row[1])
                    except json.JSONDecodeError:
                        continue
                    # data is expected to be list[{"host": ..., "ip": ...}]
                    write_to_redis(domain, data)
    except Exception:
        logger.exception("Failed to initialize Redis from CSV file %sdomain_results.csv", csv_path)

def append_to_csv(domain, crawl_results):
    """
    crawl_results: list[{"host": ..., "ip": ...}]
    """
    with open(f"{csv_path}domain_results.csv", "a", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow([domain, json.dumps(crawl_results)])


def domain_already_exists(value):
    """Return True if `value` appears anywhere in the first column of csv_path."""
    with open(f"{csv_path}domain_results.csv", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue  # skip empty lines
            if row[0].strip() == value:
                return True
    return False

@app.route("/", methods=["GET", "POST"])
def index():
    result_domains = []
    submitted_domain = None
    cached = "true"  # default when checkbox is NOT checked
    app_env = os.environ.get('APP_ENV')


    if request.method == "POST":
        submitted_domain = request.form.get("domain", "").strip()
        cached = request.form.get("cached", "true")

        if not submitted_domain:
            flash("Please enter a domain.", "danger")
            return redirect(url_for("index"))

        cached_results = get_from_redis(submitted_domain)

        if cached_results and cached == "true":
            # Use cached list[{"host": ..., "ip": ...}]
            result_domains = cached_results
        else:
            crawl_json_result = crawl(submitted_domain)
            if crawl_json_result is None:
                flash("Failed to crawl crt.sh. Please try again later.", "danger")
            else:
                domains = parse_results(crawl_json_result, submitted_domain)
                if not domains:
                    flash("No domains found for the given input.", "warning")
                else:
                    # Resolve hostnames to IP/unresolvable
                    resolved = resolve_hostnames(domains)
                    result_domains = resolved

                    if not domain_already_exists(submitted_domain):
                        append_to_csv(submitted_domain, result_domains)
                    write_to_redis(submitted_domain, result_domains)

    return render_template(
        "index.html",
        result_domains=result_domains,
        submitted_domain=submitted_domain,
        app_env=app_env,
        cached=cached,
    )

@app.route("/download", methods=["GET"])
def download_csv():
    domain = request.args.get("domain", "").strip()
    if not domain:
        abort(400, description="Missing domain parameter")

    # Reuse the existing Redis data structure: list[{"host": ..., "ip": ...}]
    rows = get_from_redis(domain)
    if not rows:
        abort(404, description="No results found for this domain")

    # Build CSV in-memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["host", "ip"])
    for entry in sorted(rows, key=lambda x: x.get("host", "")):
        writer.writerow([entry.get("host", ""), entry.get("ip", "")])

    csv_data = output.getvalue()
    output.close()

    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{domain}_results.csv"'
    return resp

@app.route("/health", methods=["GET"])
def health():
    health_status = {"redis": "Unhealthy", "app": "Healthy"}
    try:
        if r.ping():
            health_status["redis"] = "Healthy"
    except Exception:
        logger.exception("Redis health check failed")
    return health_status, 200


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if isinstance(error, HTTPException):
        return error

    safe_message = str(error) or "An unexpected error occurred."
    logger.exception("Unhandled exception during request: %s", safe_message)
    flash(safe_message, "danger")
    return redirect(url_for("index"))


@app.route("/shutdown", methods=["GET"])
def shutdown():
    os._exit(1)

if __name__ == "__main__":
    app_port = os.environ.get("APP_PORT")
    create_if_not_exist()
    csv_to_redis_init()
    app.run(host="0.0.0.0", port=app_port, debug=True)