import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright
from pydantic import BaseModel

from scraper import Scraper

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)-7s %(asctime)s %(message)s')
logger = logging.getLogger(__name__)

# Global playwright and browser objects
playwright_instance = None
browser_instance = None
browser_lock = asyncio.Lock()


async def start_browser():
    global playwright_instance, browser_instance
    async with browser_lock:
        # Check if browser is already running and connected
        if browser_instance and browser_instance.is_connected():
            return

        logger.info("Starting browser...")
        # Clean up existing instances if they exist
        if browser_instance:
            try:
                await browser_instance.close()
            except Exception:
                pass
        if playwright_instance:
            try:
                await playwright_instance.stop()
            except Exception:
                pass

        playwright_instance = await async_playwright().start()
        browser_instance = await playwright_instance.chromium.launch()


async def stop_browser():
    global playwright_instance, browser_instance
    logger.info("Stopping browser...")
    if browser_instance:
        try:
            await browser_instance.close()
        except Exception:
            pass
    if playwright_instance:
        try:
            await playwright_instance.stop()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_browser()
    yield
    await stop_browser()


app = FastAPI(title="URL to JSON API",
              description="An API to get the JSON version of HTML content using Playwright.",
              version="0.2.0",
              lifespan=lifespan)
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
        for attempt in range(2):
            try:
                if not browser_instance or not browser_instance.is_connected():
                    logger.warning("Browser not initialized or disconnected. Starting...")
                    await start_browser()

                # Create a new context and page for each request to ensure thread safety (isolation)
                context = await browser_instance.new_context()

                data = await scraper.extract_data(
                    context=context,
                    url=request.url,
                    fields=request.fields,
                    method=request.method,
                    cookie=request.cookie
                )
                return data
            except Exception as e:
                error_msg = str(e)
                if ("Target page, context or browser has been closed" in error_msg or
                    "Browser closed" in error_msg) and attempt == 0:
                    logger.warning(
                        f"Browser error detected: {error_msg}. Restarting browser and retrying (attempt {attempt + 1})...")
                    await start_browser()
                    continue
                else:
                    raise e
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error processing URL: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
