CACHE_DIR = ".cache/search"
THROTTLE_MIN_SECONDS = 1.0
THROTTLE_MAX_SECONDS = 3.0
SEARCH_URL = "https://abit-poisk.org.ua/api/statements/"

# Ймовірність, що абітурієнт із вищим пріоритетом реально звільнить місце,
# якщо його позиція там <= БМmax, але > БМmin ("ймовірно піде").
# Довільна ручка — каліброється пізніше, коли зʼявляться фактичні результати.
P_LIKELY_DEFAULT = 0.6
