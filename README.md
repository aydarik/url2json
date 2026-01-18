# url2json

`url2json` is a FastAPI-based web service that extracts data from a given URL and returns it as a JSON object. It supports both XPath for HTML and JSONPath for JSON data sources, and allows for complex data extraction and transformation.

## Features

-   Extracts data from any URL.
-   Supports both `GET` and `POST` request methods.
-   Handles both HTML and JSON data sources.
-   Uses XPath for HTML and JSONPath for JSON.
-   Allows for complex nested data extraction.
-   Provides data transformation functions (e.g., date parsing, regex, mapping).
-   Optional cookie injection for sites that require it.

## API

### Endpoint: `/extract`

-   **Method:** `POST`
-   **Payload:**

    ```json
    {
        "url": "string",
        "fields": "object",
        "method": "string (optional, default: GET)",
        "cookie": "boolean (optional, default: false)"
    }
    ```

-   **`fields` object:**

    The `fields` object defines the data to be extracted. It can contain simple key-value pairs or complex nested structures.

    -   **Simple extraction:**

        ```json
        {
            "field_name": "xpath_or_jsonpath_expression"
        }
        ```

    -   **Collection of items:**

        ```json
        {
            "collection_name": {
                "selector": "xpath_or_jsonpath_for_collection",
                "fields": {
                    "nested_field_1": "expression",
                    "nested_field_2": "expression"
                }
            }
        }
        ```

    -   **Transformation:**

        ```json
        {
            "field_name": {
                "path": "xpath_or_jsonpath_expression",
                "transform": {
                    "type": "date | regex | map",
                    "pattern": "regex_pattern (if type is regex)",
                    "replacement": "replacement_string (if type is regex)",
                    "mapping": { "key": "value" }
                }
            }
        }
        ```

## Installation

1.  Clone the repository:

    ```bash
    git clone https://github.com/your_username/url2json.git
    cd url2json
    ```

2.  Install the dependencies:

    ```bash
    pip install -r requirements.txt
    ```

3.  Install Playwright browsers:

    ```bash
    playwright install
    ```

## Usage

1.  Run the FastAPI server:

    ```bash
    uvicorn main:app --reload
    ```

2.  Send a `POST` request to the `/extract` endpoint with your desired configuration.

### Example: Extracting data from an HTML page

```json
{
    "url": "https://example.com",
    "fields": {
        "title": "//h1",
        "description": "//p"
    }
}
```

### Example: Extracting data from a JSON API

```json
{
    "url": "https://api.example.com/data",
    "fields": {
        "items": {
            "selector": "$.data.items",
            "fields": {
                "id": "$.id",
                "name": "$.name"
            }
        }
    }
}
```

## Dependencies

-   fastapi
-   uvicorn
-   playwright
-   dateparser
-   jsonpath-ng
