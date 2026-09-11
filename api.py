from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any
import pipeline

app = FastAPI()

class ScoreRequest(BaseModel):
    listings: List[Dict[str, Any]]

@app.post("/score")
def score(req: ScoreRequest):
    return pipeline.run_pipeline(req.listings)

@app.get("/health")
def health():
    return {"status": "ok"}
