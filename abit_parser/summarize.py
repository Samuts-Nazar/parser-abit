import json
from typing import Optional

from .engine import AnalysisResult

PROVIDER_GEMINI = "gemini"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OFFLINE = "offline"

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """Ти переказуєш готовий детермінований аналіз конкурсу на вступ людською мовою, українською, 3-6 речень. Ти НІЧОГО не вирішуєш і не рахуєш — усі числа вже обчислені, твоя робота лише пояснити їх зрозуміло людині, яка не розбирається в термінах вступної кампанії.

КРИТИЧНО — чесність на межі:
- Якщо "expected_count" (очікувана кількість конкурентів) в cross_check_v2 більша або дорівнює "m" — НЕ кажи "ти проходиш" впевнено. Формулюй як "ти прямо на межі, результат залежить від того, скільки людей з групи ризику реально підуть на вищий пріоритет".
- Загальне правило: твій підсумок ніколи не звучить впевненіше, ніж дозволяє розрив між очікуваною/песимістичною межами і M. Чим ближчі ці межі до M — тим обережніше формулювання.
- Для людей зі статусом "лишається" НЕ вигадуй, куди саме вона в підсумку потрапить — кажи лише "залишається реальним конкурентом тут".
- "chance" (оцінка шансу) — евристика, не строга ймовірність. Не подавай її як точний прогноз чи гарантію.
"""


class SummarizeError(Exception):
    """Сумаризація не вдалась (немає пакета/ключа, мережева помилка тощо)."""


def build_depersonalized_payload(result: AnalysisResult) -> dict:
    """Знеособлена структура для LLM: без ПІБ, лише бал/пріоритет/статус/куди метить."""
    payload = {
        "direction": result.stats.title,
        "m": result.verdict.m,
        "verdict_v1": result.verdict.verdict,
        "optimistic_bound_v1": result.verdict.optimistic_bound,
        "pessimistic_bound_v1": result.verdict.pessimistic_bound,
    }
    cc = result.cross_check
    if cc is not None:
        payload["cross_check_v2"] = {
            "hard_count": cc.hard_count,
            "stays_count": cc.stays_count,
            "likely_count": cc.likely_count,
            "definite_count": cc.definite_count,
            "unknown_count": cc.unknown_count,
            "optimistic_bound": cc.optimistic_bound,
            "expected_count": cc.expected_count,
            "pessimistic_bound": cc.pessimistic_bound,
            "chance": cc.chance,
            "risk_group": [
                {
                    "score": a.competitor.applicant.score,
                    "priority_here": a.competitor.applicant.priority,
                    "status": a.status,
                    "targets_instead": (
                        f"{a.best_choice.university} / {a.best_choice.specialty}" if a.best_choice else None
                    ),
                }
                for a in cc.assessments
            ],
        }
    return payload


def _summarize_offline(payload: dict) -> str:
    m = payload["m"]
    lines = [f"Вердикт v1: {payload['verdict_v1']}. Бюджетних місць (M) = {m}."]

    cc = payload.get("cross_check_v2")
    if cc is None:
        return " ".join(lines)

    expected = cc["expected_count"]
    opt = cc["optimistic_bound"]
    pess = cc["pessimistic_bound"]

    if pess <= m:
        lines.append(f"Навіть у найгіршому сценарії конкурентів ({pess}) менше за M ({m}) — проходиш.")
    elif opt > m:
        lines.append(f"Навіть у найкращому сценарії конкурентів ({opt}) більше за M ({m}) — не проходиш.")
    elif expected >= m:
        lines.append(
            f"Очікувана кількість конкурентів ({expected:.1f}) вже на рівні або вище M ({m}) — "
            "ти прямо на межі, результат залежить від того, скільки людей з групи ризику реально підуть "
            "на вищий пріоритет."
        )
    else:
        lines.append(
            f"Очікувана кількість конкурентів ({expected:.1f}) нижче M ({m}), але межі широкі "
            f"({opt}–{pess}) — оцінка шансу {cc['chance'] * 100:.0f}% (евристика, не гарантія)."
        )

    if cc["stays_count"]:
        lines.append(f"{cc['stays_count']} людей з групи ризику лишаються реальними конкурентами тут.")
    if cc["unknown_count"]:
        lines.append(f"{cc['unknown_count']} осіб не вдалось однозначно визначити.")

    return " ".join(lines)


def _summarize_gemini(payload: dict, api_key: Optional[str], model: str) -> str:
    if not api_key:
        raise SummarizeError("Не вказано ключ Gemini API.")
    try:
        from google import genai
    except ImportError as e:
        raise SummarizeError("Пакет 'google-genai' не встановлено (pip install google-genai).") from e

    try:
        client = genai.Client(api_key=api_key)
        interaction = client.interactions.create(
            model=model,
            system_instruction=SYSTEM_PROMPT,
            input=json.dumps(payload, ensure_ascii=False, indent=2),
        )
    except Exception as e:
        raise SummarizeError(f"Помилка виклику Gemini API: {e}") from e

    text = getattr(interaction, "output_text", None)
    if not text:
        raise SummarizeError("Gemini повернув порожню відповідь.")
    return text


def _summarize_anthropic(payload: dict, model: str) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise SummarizeError("Пакет 'anthropic' не встановлено (pip install anthropic).") from e

    prompt = (
        f"{SYSTEM_PROMPT}\n\nОсь структурований результат аналізу (JSON):\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=500,
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as e:
        raise SummarizeError(
            "Немає дійсних облікових даних (встановіть ANTHROPIC_API_KEY або виконайте `ant auth login`)."
        ) from e
    except Exception as e:
        raise SummarizeError(f"Помилка виклику Anthropic API: {e}") from e

    for block in response.content:
        if block.type == "text":
            return block.text
    raise SummarizeError("Anthropic повернув відповідь без тексту.")


def generate_summary(
    payload: dict,
    provider: str = PROVIDER_GEMINI,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Переказує готовий (детермінований) результат людською мовою. Кидає SummarizeError при невдачі."""
    if provider == PROVIDER_OFFLINE:
        return _summarize_offline(payload)
    if provider == PROVIDER_GEMINI:
        return _summarize_gemini(payload, api_key, model or DEFAULT_GEMINI_MODEL)
    if provider == PROVIDER_ANTHROPIC:
        return _summarize_anthropic(payload, model or DEFAULT_ANTHROPIC_MODEL)
    raise ValueError(f"Невідомий провайдер сумаризації: {provider!r}")
