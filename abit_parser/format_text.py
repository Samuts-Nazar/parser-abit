"""Спільні текстові шаблони подачі меж/шансів — використовуються і CLI, і GUI."""


def rank_strip(optimistic: int, expected: float, pessimistic: int, limit: int) -> str:
    """Смуга рангів — головне, що ми реально знаємо. Песимістичний веде, не оптимістичний."""
    return (
        f"Найгірший сценарій: {pessimistic}-й · Очікувано: {round(expected)}-й · "
        f"Найкращий: {optimistic}-й — місць {limit}"
    )


def chance_line(chance: float, pessimistic_chance: float, optimistic_chance: float) -> str:
    """Три % (не один), заголовком завжди песимістичний. Дрібна вторинна надбудова над rank_strip."""
    return (
        f"Шанс: песимістичний {pessimistic_chance * 100:.0f}% · очікуваний {chance * 100:.0f}% · "
        f"оптимістичний {optimistic_chance * 100:.0f}%  (евристика, не строга ймовірність)"
    )
