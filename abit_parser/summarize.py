import json
from typing import Optional

from .contract_analysis import ContractResult
from .engine import AnalysisResult

PROVIDER_GEMINI = "gemini"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OFFLINE = "offline"

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """Ти переказуєш готовий детермінований аналіз конкурсу на вступ людською мовою, українською, 3-6 речень. Ти НІЧОГО не вирішуєш і не рахуєш — усі числа й вердикт вже обчислені, твоя робота лише пояснити їх зрозуміло людині, яка не розбирається в термінах вступної кампанії.

КРИТИЧНО — чесність на межі:
- У кожного блоку (cross_check_v2, contract_v4) вже є готове поле "verdict" — воно точно враховує і песимістичний, і очікуваний сценарій. ЗАВЖДИ бери формулювання звідти, НЕ вигадуй власне "проходиш"/"пройдеш" на основі самих чисел — легко переоцінити шанс, дивлячись лише на "chance".
- Якщо verdict містить "на межі" — це і є правильна відповідь, навіть якщо "chance" виглядає високим. Ніколи не пом'якшуй "на межі (радше пролітаєш)" до просто "на межі" чи тим паче "проходиш".
- Головні числа — це РАНГИ (optimistic_bound / expected_count / pessimistic_bound проти m або k), а не відсотки. Веди відповідь саме ними: "у найгіршому випадку Х-й, очікувано Y-й, у найкращому Z-й — місць N". Відсотки (chance, pessimistic_chance, optimistic_chance) — це вторинна, менш надійна оцінка; якщо згадуєш їх, веди з pessimistic_chance, не з optimistic_chance чи навіть chance.
- Для людей зі статусом "лишається" НЕ вигадуй, куди саме вона в підсумку потрапить — кажи лише "залишається реальним конкурентом тут".
- Якщо в payload є "contract_v4" — додай про це окремий короткий абзац після бюджетного висновку, тим самим принципом (спирайся на його "verdict", не вигадуй свій).
"""


class SummarizeError(Exception):
    """Сумаризація не вдалась (немає пакета/ключа, мережева помилка тощо)."""


def build_depersonalized_payload(
    result: AnalysisResult, contract_result: Optional[ContractResult] = None
) -> dict:
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
            "m": cc.m,
            "hard_count": cc.hard_count,
            "stays_count": cc.stays_count,
            "likely_count": cc.likely_count,
            "definite_count": cc.definite_count,
            "unknown_count": cc.unknown_count,
            "optimistic_bound": cc.optimistic_bound,
            "expected_count": cc.expected_count,
            "pessimistic_bound": cc.pessimistic_bound,
            "chance": cc.chance,
            "pessimistic_chance": cc.pessimistic_chance,
            "optimistic_chance": cc.optimistic_chance,
            "verdict": cc.verdict,
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
    if contract_result is not None:
        cr = contract_result
        payload["contract_v4"] = {
            "k": cr.k,
            "pool_size_total": cr.pool_size_total,
            "hard_count": cr.hard_count,
            "stays_count": cr.stays_count,
            "likely_count": cr.likely_count,
            "definite_count": cr.definite_count,
            "unknown_count": cr.unknown_count,
            "optimistic_bound": cr.optimistic_bound,
            "expected_count": cr.expected_count,
            "pessimistic_bound": cr.pessimistic_bound,
            "chance": cr.chance,
            "pessimistic_chance": cr.pessimistic_chance,
            "optimistic_chance": cr.optimistic_chance,
            "verdict": cr.verdict,
            "pool_above_user": [
                {
                    "score": a.member.applicant.score,
                    "origin": a.member.origin,
                    "status": a.status,
                    "targets_instead": (
                        f"{a.best_choice.university} / {a.best_choice.specialty}" if a.best_choice else None
                    ),
                }
                for a in cr.assessments
            ],
        }
    return payload


def _bound_sentence(label: str, block: dict, limit_key: str) -> str:
    limit = block[limit_key]
    opt = block["optimistic_bound"]
    expected = block["expected_count"]
    pess = block["pessimistic_bound"]
    return (
        f"{label}: у найгіршому випадку {pess}-й, очікувано {round(expected)}-й, "
        f"у найкращому {opt}-й — місць {limit}. Вердикт: {block['verdict']} "
        f"(шанс: песимістичний {block['pessimistic_chance'] * 100:.0f}%, "
        f"очікуваний {block['chance'] * 100:.0f}%, оптимістичний {block['optimistic_chance'] * 100:.0f}% — евристика)."
    )


def _summarize_offline(payload: dict) -> str:
    lines = [f"Вердикт v1 (широка вилка): {payload['verdict_v1']}. Бюджетних місць (M) = {payload['m']}."]

    cc = payload.get("cross_check_v2")
    if cc is not None:
        lines.append(_bound_sentence("Бюджет (уточнено v2)", cc, "m"))
        if cc["stays_count"]:
            lines.append(f"{cc['stays_count']} людей з групи ризику лишаються реальними конкурентами тут.")
        if cc["unknown_count"]:
            lines.append(f"{cc['unknown_count']} осіб не вдалось однозначно визначити.")

    cr = payload.get("contract_v4")
    if cr is not None:
        lines.append(_bound_sentence("Контракт (v4)", cr, "k"))

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
