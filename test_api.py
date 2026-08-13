import requests
import json


def test_jsonpath():
    url = "http://localhost:8000/extract"
    payload = {
        "method": "POST",
        "cookie": True,
        "url": "https://ber.mzgb.net/api/load-data?page=main&locale=ru",
        "fields": {
            "games": {
                "selector": "$.upcomingGames",
                "fields": {
                    "id": "$.id",
                    "title": "$.game.name",
                    "description": "$.game.type",
                    "image": {
                        "path": "$.img",
                        "transform": {
                            "type": "regex",
                            "pattern": "(.+)",
                            "replacement": "https://ber.mzgb.net/\\1"
                        }
                    },
                    "datetime": {
                        "path": [
                            "$.calendar_date",
                            "$.calendar_time_start"
                        ],
                        "transform": [
                            {
                                "type": "regex",
                                "pattern": "(\\d{4})(\\d{2})(\\d{2}) (\\d{2})(\\d{2})",
                                "replacement": "\\1-\\2-\\3 \\4:\\5"
                            },
                            {
                                "type": "date"
                            }
                        ]
                    },
                    "price": {
                        "path": [
                            "$.price",
                            "$.currency"
                        ]
                    },
                    "venue": "$.venue.name",
                    "address": "$.venue.address"
                }
            }
        }
    }

    print("Testing JSONPath extraction...")
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))
    except Exception as e:
        print(f"Error: {e}")


def test_xpath():
    url = "http://localhost:8000/extract"
    payload = {
        "url": "https://berlin.quizplease.de/schedule",
        "fields": {
            "games": {
                "selector": "//div[contains(@class, 'schedule-column')]",
                "fields": {
                    "id": "./@id",
                    "title": ".//a[contains(@class, 'schedule-block-head')]",
                    "description": ".//div[contains(@class, 'techtext-mb30')]",
                    "language": {
                        "path": ".//div[div[text()='Language']]/div[contains(@class, 'techtext-halfwhite')]",
                        "transform": [
                            {
                                "type": "regex",
                                "pattern": ".*is in (\\w+)",
                                "replacement": "\\1"
                            },
                            {
                                "type": "map",
                                "mapping": {
                                    "English": "ENG",
                                    "Russian": "RUS"
                                }
                            }
                        ]
                    },
                    "datetime": {
                        "path": [
                            ".//div[contains(@class, 'h3')]",
                            ".//div[contains(@class, 'schedule-info')]//div[contains(@class, 'techtext') and contains(text(), 'at ')]"
                        ],
                        "transform": [
                            {
                                "type": "regex",
                                "pattern": "(.+), .+ at (\\d{2}:\\d{2})",
                                "replacement": "\\1, \\2"
                            },
                            {
                                "type": "date"
                            }
                        ]
                    },
                    "price": ".//div[contains(@class, 'schedule-info')]//div[contains(@class, 'text') and contains(text(), '€')]",
                    "venue": {
                        "path": ".//div[@class='schedule-block-info-bar']",
                        "transform": {
                            "type": "regex",
                            "pattern": " Bar Info$",
                            "replacement": ""
                        }
                    },
                    "address": {
                        "path": "(.//div[contains(@class, 'techtext-halfwhite')])[1]",
                        "transform": {
                            "type": "regex",
                            "pattern": " Where is it\\?$",
                            "replacement": ""
                        }
                    }
                }
            }
        }
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    test_xpath()
    test_jsonpath()
