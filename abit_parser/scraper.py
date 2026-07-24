import requests
import certifi
import urllib3

from .throttle import throttled_request

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
}


def fetch_page(url: str, timeout: int = 15) -> str:
    with throttled_request():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout, verify=certifi.where())
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.SSLError:
            # Типово через антивірус з HTTPS-сканером або конфлікт версій сертифікатів на Windows.
            # Дані публічні, некритично зробити один запит без перевірки сертифіката.
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)
            resp.raise_for_status()
            return resp.text
