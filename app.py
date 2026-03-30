import csv
import json
import traceback
import redis
import requests
import os
import re
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "change-this-secret-key"
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
        else:
            print(f"crt.sh returned status {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        print(str(e))
        return None

DOMAIN_RE = re.compile(
    r'^(?=.{1,253}$)(?!-)([A-Za-z0-9-]{1,63}(?<!-).)+[A-Za-z]{2,63}$'
)

def create_if_not_exist():
    try:
        with open(f"{csv_path}domain_results.csv", "x") as f:
            pass
    except FileExistsError:
        print("The file already exists.")

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

    except Exception as e:
        error_msg = f"Exception: {str(e)}\nError stack: {traceback.format_exc()}."
        print(error_msg)

    print(domains)
    return domains

def write_to_redis(domain, crawl_results):
    r.sadd(domain, *crawl_results)

def get_from_redis(domain):
    return r.smembers(domain)

def csv_to_redis_init():
    print("Initializing Redis and appending crawl results...")
    with open(f"{csv_path}domain_results.csv", "r", newline="") as csv_read:
        reader = csv.reader(csv_read)
        for row in reader:
            if row:
                write_to_redis(row[0], json.loads(row[1]))

def append_to_csv(domain, crawl_results):
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

    if request.method == "POST":
        submitted_domain = request.form.get("domain", "").strip()

        # If checkbox is checked → cached = "false"
        # If unchecked (no field sent) → cached = "true"
        cached = request.form.get("cached", "true")

        if not submitted_domain:
            flash("Please enter a domain.", "danger")
            return redirect(url_for("index"))

        if get_from_redis(submitted_domain) and cached == "true":
            result_domains = get_from_redis(submitted_domain)
        else:
            crawl_json_result = crawl(submitted_domain)
            if crawl_json_result is None:
                flash("Failed to crawl crt.sh. Please try again later.", "danger")
            else:
                domains = parse_results(crawl_json_result, submitted_domain)
                if not domains:
                    flash("No domains found for the given input.", "warning")
                else:
                    result_domains = sorted(domains)
                    if not domain_already_exists(submitted_domain):
                        append_to_csv(submitted_domain, result_domains)
                    write_to_redis(submitted_domain, result_domains)

    return render_template(
        "index.html",
        result_domains=result_domains,
        submitted_domain=submitted_domain,
        cached=cached,
    )

if __name__ == "__main__":
    create_if_not_exist()
    csv_to_redis_init()
    app.run("0.0.0.0", port=5000, debug=True)