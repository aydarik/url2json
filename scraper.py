import hashlib
import json
import logging
import re
import urllib.parse
from typing import Dict, Any

import dateparser
from jsonpath_ng import parse
from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)


class Scraper:
    async def extract_data(self, context: BrowserContext, url: str, fields: Dict[str, Any],
                           method: str = "GET", cookie: bool = False) -> Dict[str, Any]:
        """
        Navigates to the given URL and extracts data based on XPath or JSONPath mappings.
        If cookie is True, it navigates to the base part of the URL first to collect cookies.
        """
        logger.info("URL: %s ", url)
        page = await context.new_page()

        try:
            if cookie:
                parsed_url = urllib.parse.urlparse(url)
                cookie_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                logger.info("Getting cookies")
                await page.goto(cookie_url, wait_until="commit")

                cookies = await context.cookies()
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                headers = {"Cookie": cookie_str} if cookie_str else {}

                xsrf_token = next((c['value'] for c in cookies if c['name'] == 'XSRF-TOKEN'), None)
                if xsrf_token:
                    headers["X-XSRF-TOKEN"] = urllib.parse.unquote(xsrf_token)
                    logger.info("Injecting X-XSRF-TOKEN")

            if method.upper() == "POST":
                response = await page.request.fetch(url, method="POST", headers=headers)
                content = await response.text()
                await page.set_content(content)
            else:
                response = await page.goto(url, wait_until="commit")
                logger.info("STATUS: %d", response.status if response else None)
                logger.info("TITLE: %s", await page.title())
                logger.info("HTML: %d", len(await page.content()))

            try:
                # Try to parse the inner text as JSON
                json_text = await page.evaluate("() => document.body.innerText")
                data = json.loads(json_text)
                return await self._process_fields(data, fields, is_json=True)
            except Exception:
                # Fallback to HTML/XPath
                return await self._process_fields(page, fields, is_json=False)
        finally:
            # Always close the page and context to free resources
            await context.close()

    async def _process_fields(self, root: Any, fields: Dict[str, Any], is_json: bool = False) -> Dict[str, Any]:
        """
        Recursively processes fields using the provided root.
        """
        results = {}
        for field_name, definition in fields.items():
            try:
                if isinstance(definition, str):
                    # Simple extraction
                    if is_json:
                        results[field_name] = self._extract_jpath(root, definition)
                    else:
                        results[field_name] = await self._extract_xpath(root, definition)
                elif isinstance(definition, dict):
                    selector = definition.get("selector")
                    nested_fields = definition.get("fields")

                    if selector and nested_fields:
                        # Collection
                        collection_results = []
                        if is_json:
                            items = self._extract_jpath(root, selector)
                            if isinstance(items, list):
                                for item in items:
                                    item_data = await self._process_fields(item, nested_fields, is_json=True)
                                    collection_results.append(item_data)
                        else:
                            locators = await root.locator(f"xpath={selector}").all()
                            for locator in locators:
                                item_data = await self._process_fields(locator, nested_fields, is_json=False)
                                collection_results.append(item_data)

                        results[field_name] = collection_results
                    elif "path" in definition:
                        # Single value with potential transform
                        path_def = definition.get("path")

                        if isinstance(path_def, list):
                            parts = []
                            for p in path_def:
                                val = self._extract_jpath(root, p) if is_json else await self._extract_xpath(root, p)
                                if val:
                                    if isinstance(val, list):
                                        parts.extend(val)
                                    else:
                                        parts.append(str(val))
                            value = " ".join(parts) if parts else None
                        else:
                            value = self._extract_jpath(root, path_def) if is_json \
                                else await self._extract_xpath(root, path_def)

                        # Apply transformation if specified
                        transform = definition.get("transform")
                        if transform and value:
                            results[field_name] = self._apply_transform(value, transform)
                        else:
                            results[field_name] = value
                    else:
                        results[field_name] = f"Error: Invalid field definition for {field_name}"
                else:
                    results[field_name] = f"Error: Invalid type for {field_name}"
            except Exception as e:
                results[field_name] = f"Error: {str(e)}"
        return results

    def _extract_jpath(self, data: Any, jpath: str) -> Any:
        """
        Extracts values using JSONPath.
        """
        try:
            jsonpath_expr = parse(jpath)
            matches = [match.value for match in jsonpath_expr.find(data)]
            if not matches:
                return None
            if len(matches) == 1:
                return self._clean_string(matches[0])
            return self._clean_string(matches)
        except Exception:
            return None

    async def _extract_xpath(self, root: Any, xpath: str) -> Any:
        """
        Extracts a single value or list of values from the given root using XPath.
        Applies cleaning (newline replacement and trimming) by default.
        """
        # Handle attribute extraction if specified (e.g., //a/@href)
        attr_name = None
        if "/@" in xpath:
            xpath, attr_name = xpath.rsplit("/@", 1)

        element = root.locator(f"xpath={xpath}")
        count = await element.count()

        if count == 0:
            return None

        if attr_name:
            if count == 1:
                val = await element.get_attribute(attr_name)
                return self._clean_single_string(val)
            else:
                vals = [await el.get_attribute(attr_name) for el in await element.all()]
                return self._clean_string(vals)
        else:
            if count == 1:
                val = await element.inner_text()
                return self._clean_single_string(val)
            else:
                vals = await element.all_inner_texts()
                return self._clean_string(vals)

    def _apply_transform(self, value: Any, transform: Any) -> Any:
        """
        Applies a transformation (string, object, or list) to the value.
        """
        if isinstance(transform, list):
            # Chain transformations
            current_value = value
            for t in transform:
                current_value = self._apply_transform(current_value, t)
            return current_value

        t_type = transform if isinstance(transform, str) else transform.get("type")

        if t_type == "date":
            return self._parse_date(value)
        elif t_type == "regex":
            if not isinstance(transform, dict):
                return value
            pattern = transform.get("pattern")
            replacement = transform.get("replacement", "")
            if pattern:
                return self._apply_regex(value, pattern, replacement)
        elif t_type == "map":
            if not isinstance(transform, dict):
                return value
            mapping = transform.get("mapping", {})
            return self._apply_map(value, mapping)
        elif t_type == "hash":
            return self._to_int(value)

        return value

    def _to_int(self, value: Any) -> int:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], byteorder="big")

    def _apply_map(self, value: Any, mapping: Dict[str, Any]) -> Any:
        """
        Maps the value or list of values using the provided mapping dictionary.
        Returns the original value if not found in mapping.
        """
        if isinstance(value, list):
            return [mapping.get(v, v) for v in value]
        return mapping.get(value, value)

    def _apply_regex(self, value: Any, pattern: str, replacement: str) -> Any:
        """
        Applies regex replacement to a string or list of strings.
        """
        if isinstance(value, list):
            return [self._apply_single_regex(v, pattern, replacement) for v in value]
        return self._apply_single_regex(value, pattern, replacement)

    def _apply_single_regex(self, val: str, pattern: str, replacement: str) -> str:
        if not isinstance(val, str):
            return val
        try:
            return re.sub(pattern, replacement, val).strip()
        except Exception:
            return val

    def _parse_date(self, value: Any) -> Any:
        """
        Parses a string or list of strings into ISO 8601 date format.
        """
        if isinstance(value, list):
            return [self._parse_single_date(v) for v in value]
        return self._parse_single_date(value)

    def _parse_single_date(self, date_str: str) -> str:
        """
        Parses a single date string into ISO 8601.
        """
        if not date_str:
            return None
        try:
            dt = dateparser.parse(date_str)
            if dt:
                return dt.isoformat()
            return date_str
        except Exception:
            return date_str

    def _clean_string(self, value: Any) -> Any:
        """
        Replaces newlines with spaces and trims the string.
        """
        if isinstance(value, list):
            return [self._clean_single_string(v) for v in value]
        return self._clean_single_string(value)

    def _clean_single_string(self, val: str) -> str:
        if not isinstance(val, str):
            return val
        return val.replace("\n", " ").strip()
