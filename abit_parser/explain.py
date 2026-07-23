import json
from typing import Optional


def generate_explanation(payload: dict) -> Optional[str]:
    try:
        import anthropic
    except ImportError:
        print("Пакет 'anthropic' не встановлено (pip install anthropic) — пропускаю --explain.")
        return None

    prompt = (
        "Ось структурований результат аналізу конкурентів абітурієнта (JSON). "
        "Перекажи його зрозумілою людською мовою українською, 3-5 речень: "
        "чи проходить, скільки реальних конкурентів, що з рештою (група ризику/невизначені).\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=500,
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        print("Anthropic API: немає дійсних облікових даних (встановіть ANTHROPIC_API_KEY або виконайте `ant auth login`).")
        return None
    except Exception as e:
        print(f"Помилка виклику Anthropic API: {e} — пропускаю --explain.")
        return None

    for block in response.content:
        if block.type == "text":
            return block.text
    return None
