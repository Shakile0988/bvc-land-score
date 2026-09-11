import os, json, requests
import pipeline

listings = json.loads(os.environ["LISTINGS_JSON"])
webhook_url = os.environ["WEBHOOK_URL"]

result = pipeline.run_pipeline(listings)

requests.post(webhook_url, json=result, timeout=30)
