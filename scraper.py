import asyncio
import hashlib
import json
import logging
import re
import urllib.parse
from typing import Dict, Any, Optional

from fastapi import HTTPException

import dateparser
from jsonpath_ng import parse
from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)


class Scraper:
    async def extract_data(self, context: BrowserContext, url: str, fields: Dict[str, Any],
                           method: str = "GET", cookie: bool = False,
                           headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Navigates to the given URL and extracts data based on XPath, JSONPath, or regex mappings.
        Content type is determined from the response Content-Type header.
        If cookie is True, it navigates to the base part of the URL first to collect cookies.
        Raises an exception if the server responds with an HTTP error status code.
        """
        logger.info("URL: %s ", url)
        page = await context.new_page()

        try:
            req_headers = dict(headers) if headers else {}
            if cookie:
                parsed_url = urllib.parse.urlparse(url)
                cookie_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                logger.info("Getting cookies")
                await page.goto(cookie_url, wait_until="commit")

                cookies = await context.cookies()
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                if cookie_str:
                    req_headers["Cookie"] = cookie_str

                xsrf_token = next((c['value'] for c in cookies if c['name'] == 'XSRF-TOKEN'), None)
                if xsrf_token:
                    req_headers["X-XSRF-TOKEN"] = urllib.parse.unquote(xsrf_token)
                    logger.info("Injecting X-XSRF-TOKEN")

            post_body: Optional[str] = None
            raw_text: Optional[str] = None
            if method.upper() == "POST":
                response = await page.request.fetch(url, method="POST", headers=req_headers)
                post_body = await response.text()
                await page.set_content(post_body)
                raw_text = post_body
            else:
                response = await page.goto(url, wait_until="load")
                if response:
                    try:
                        raw_text = await response.text()
                    except Exception:
                        raw_text = None

            status = response.status if response else None
            logger.info("STATUS: %d", status)

            if status is None or status >= 400:
                raise HTTPException(
                    status_code=status or 502,
                    detail=f"Upstream HTTP error {status} fetching {url}",
                )

            content_type = response.headers.get("content-type", "") if response else ""
            logger.info("CONTENT TYPE: %s", content_type)
            is_json = "application/json" in content_type

            if is_json:
                # For POST responses the body is already in post_body; otherwise read from DOM
                json_text = post_body if post_body is not None else await page.evaluate(
                    "() => document.body.innerText"
                )
                raw_text = json_text
                data = json.loads(json_text)
                return await self._process_fields(
                    data, fields, is_json=True, raw_text=raw_text, context=context, headers=req_headers
                )
            return await self._process_fields(
                page, fields, is_json=False, raw_text=raw_text, context=context, headers=req_headers
            )
        finally:
            # Always close the page and context to free resources
            await context.close()

    async def _process_fields(
        self,
        root: Any,
        fields: Dict[str, Any],
        is_json: bool = False,
        raw_text: Optional[str] = None,
        context: Optional[BrowserContext] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        logger.info("JSON: %s", is_json)

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
                    regex = definition.get("regex")
                    item_regex = definition.get("item_regex")
                    url_template = definition.get("url_template")
                    transform = definition.get("transform")
                    path_def = definition.get("path")
                    sub_method = definition.get("method", "GET")

                    if url_template:
                        # Collection of sub-requests generated from regex, selector, or path
                        items = []
                        if regex:
                            items = self._extract_regex_items(raw_text or "", regex, item_regex)
                        elif selector:
                            if is_json:
                                val = self._extract_jpath(root, selector)
                            else:
                                val = await self._extract_xpath(root, selector)
                            items = val if isinstance(val, list) else ([val] if val is not None else [])
                        elif path_def:
                            val = self._extract_jpath(root, path_def) if is_json else await self._extract_xpath(root, path_def)
                            items = val if isinstance(val, list) else ([val] if val is not None else [])

                        async def _fetch_sub_item(item_val: Any) -> Any:
                            sub_url = self._format_url_template(url_template, item_val)
                            try:
                                sub_res = await context.request.fetch(
                                    sub_url, method=sub_method.upper(), headers=headers or {}
                                )
                                if sub_res.status >= 400:
                                    logger.warning("Sub-request %s returned status %d", sub_url, sub_res.status)
                                    return None
                                sub_ct = sub_res.headers.get("content-type", "")
                                if "application/json" in sub_ct:
                                    sub_data = await sub_res.json()
                                    if nested_fields:
                                        return await self._process_fields(
                                            sub_data, nested_fields, is_json=True,
                                            raw_text=json.dumps(sub_data), context=context, headers=headers
                                        )
                                    return sub_data
                                else:
                                    sub_text = await sub_res.text()
                                    if nested_fields:
                                        sub_page = await context.new_page()
                                        try:
                                            await sub_page.set_content(sub_text)
                                            return await self._process_fields(
                                                sub_page, nested_fields, is_json=False,
                                                raw_text=sub_text, context=context, headers=headers
                                            )
                                        finally:
                                            await sub_page.close()
                                    return sub_text
                            except Exception as e:
                                logger.warning("Error fetching sub-request %s: %s", sub_url, e)
                                return None

                        if context and items:
                            fetched = await asyncio.gather(*[_fetch_sub_item(item) for item in items])
                            results[field_name] = [item for item in fetched if item is not None]
                        else:
                            results[field_name] = []

                    elif regex and (item_regex or nested_fields):
                        # Regex-based collection without sub-requests
                        items = self._extract_regex_items(raw_text or "", regex, item_regex)
                        if nested_fields:
                            collection_results = []
                            for it in items:
                                it_data = await self._process_fields(
                                    it, nested_fields, is_json=isinstance(it, (dict, list)),
                                    raw_text=str(it), context=context, headers=headers
                                )
                                collection_results.append(it_data)
                            results[field_name] = collection_results
                        else:
                            results[field_name] = items

                    elif regex and not selector and not path_def:
                        # Single value regex extraction from raw_text
                        val = self._extract_single_regex(raw_text or "", regex)
                        if transform and val:
                            results[field_name] = self._apply_transform(val, transform)
                        else:
                            results[field_name] = val

                    elif selector and nested_fields:
                        # Collection
                        collection_results = []
                        if is_json:
                            items = self._extract_jpath(root, selector)
                            if isinstance(items, list):
                                for item in items:
                                    item_data = await self._process_fields(
                                        item, nested_fields, is_json=True,
                                        raw_text=raw_text, context=context, headers=headers
                                    )
                                    collection_results.append(item_data)
                        else:
                            locators = await root.locator(f"xpath={selector}").all()
                            for locator in locators:
                                item_data = await self._process_fields(
                                    locator, nested_fields, is_json=False,
                                    raw_text=raw_text, context=context, headers=headers
                                )
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

    def _extract_regex_items(self, text: str, regex: str, item_regex: Optional[str] = None) -> list:
        """
        Extracts a list of items using regex.
        If item_regex is provided, first matches regex to find a section, then finds all item_regex in that section.
        """
        if not text:
            return []
        try:
            if item_regex:
                m = re.search(regex, text, re.DOTALL)
                if not m:
                    return []
                block = m.group(1) if m.groups() else m.group(0)
                items = re.findall(item_regex, block, re.DOTALL)
            else:
                items = re.findall(regex, text, re.DOTALL)

            cleaned = []
            for item in items:
                if isinstance(item, tuple):
                    cleaned.append(item[0] if len(item) == 1 else item)
                else:
                    cleaned.append(item)
            return cleaned
        except Exception as e:
            logger.warning("Regex extraction error: %s", e)
            return []

    def _extract_single_regex(self, text: str, regex: str) -> Optional[str]:
        """
        Extracts a single value from text using regex.
        """
        if not text:
            return None
        try:
            m = re.search(regex, text, re.DOTALL)
            if not m:
                return None
            return m.group(1) if m.groups() else m.group(0)
        except Exception as e:
            logger.warning("Single regex extraction error: %s", e)
            return None

    def _format_url_template(self, template: str, item: Any) -> str:
        """
        Formats a URL template with the extracted item.
        Supports <id>, <item>, {id}, {}, etc.
        """
        if isinstance(item, dict):
            res = template
            for k, v in item.items():
                s_val = str(v).strip("\"' \t\r\n")
                res = res.replace(f"<{k}>", s_val).replace(f"{{{k}}}", s_val)
            return res

        s_item = str(item).strip("\"' \t\r\n")
        if re.search(r"<[a-zA-Z0-9_]+>", template):
            return re.sub(r"<[a-zA-Z0-9_]+>", s_item, template)
        if "{}" in template:
            return template.replace("{}", s_item)
        if re.search(r"\{[a-zA-Z0-9_]+\}", template):
            return re.sub(r"\{[a-zA-Z0-9_]+\}", s_item, template)
        if template.endswith("/"):
            return f"{template}{s_item}"
        return f"{template}/{s_item}"


    def _extract_jpath(self, data: Any, jpath: str) -> Any:
        """
        Extracts values using JSONPath.
        """
        if jpath.startswith("'"):
            return jpath.strip("'")

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

        if xpath.startswith("'"):
            return xpath.strip("'")

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
            vals = [await el.get_attribute(attr_name) for el in await element.all()]
            return self._clean_string(vals)
        if count == 1:
            val = await element.inner_text()
            return self._clean_single_string(val)
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

    def _parse_single_date(self, date_str: Any) -> str:
        """
        Parses a single date string into ISO 8601.
        """
        if date_str is None:
            return None
        if isinstance(date_str, (int, float)):
            date_str = str(int(date_str))
        try:
            dt = dateparser.parse(str(date_str))
            if dt:
                return dt.isoformat()
            return str(date_str)
        except Exception:
            return str(date_str)

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
