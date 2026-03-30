![](./static/example.png)
# SubCrawler
SubCrawler is a simple application that allows you to collect all available subdomains of the provided domain.

## Features
- **Simple usage:** Gathers the subdomains based on the requested domain.
- **Wellknown source of data:** Utilizing the crt.sh API and collects the subdomains based on the past certificates and SANs.
- **Local storage:** Stores the collected subdomains in a file to prevent recrawling already existing data.
- **Recrawl:** Has option to recrawl already existing domain.
- **Fast response:** The collected results are also stored in Redis for cached responses.
- **Dockerized:** Easily containerized for streamlined deployment.

## Structure
```
subcrawler
    ├── Dockerfile
    ├── README.md
    ├── app.py
    ├── csv_db
    │   └── domain_results.csv
    ├── docker-compose.yml
    ├── requirements.txt
    ├── static
    │   └── example.png
    └── templates
        └── index.html
```
## Prerequisites

[Docker](https://docs.docker.com/get-docker/) <br>
[Docker Compose](https://docs.docker.com/compose/install/) 

## Installation
1. Clone the Repository:
```aiignore
git clone https://github.com/gh0stik/subcrawler.git
cd /subcrawler
```
2. Spin up the environment:
```aiignore
docker-compose up --build
```
3. Once the containers are running, the app is available at:
```aiignore
http://127.0.0.1:5000
```