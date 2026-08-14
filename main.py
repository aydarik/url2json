from typing import Dict, Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from scraper import Scraper

app = FastAPI(title="url2json API")
scraper = Scraper()


class ExtractionRequest(BaseModel):
    url: str
    fields: Dict[str, Any]
    method: Optional[str] = "GET"
    cookie: Optional[bool] = False


@app.post("/extract")
async def extract_endpoint(request: ExtractionRequest):
    """
    Endpoint to extract data from a URL using provided XPath mappings.
    """
    try:
        data = await scraper.extract_data(
            url=request.url,
            fields=request.fields,
            method=request.method,
            cookie=request.cookie
        )
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
