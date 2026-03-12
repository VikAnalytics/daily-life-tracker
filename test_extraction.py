"""
Quick local test for the free-form natural language LLM extraction.
Run: python test_extraction.py
"""
from dotenv import load_dotenv
load_dotenv()

from llm_extractor import extract_daily_log

TEST_CASES = [
    # Typical conversational input
    "Spent 50 USD on lunch, went for a 30 min run, had a salad around 400 cal",

    # Multiple items, mixed currencies
    "Coffee 3.50 USD. Split dinner (80 EUR) with Alice and Bob. Did yoga for 45 minutes. Ate rice and dal.",

    # No calories given — LLM should estimate
    "Had pizza and a coke for dinner. Walked for 1 hour. Paid 20 GBP for groceries.",

    # Only fitness, nothing else
    "Cycled for 1 hour today",

    # Messy / incomplete
    "Bought some stuff, around 15 dollars. No workout today.",
]

for i, text in enumerate(TEST_CASES, 1):
    print(f"\n{'='*60}")
    print(f"TEST {i}: {text}")
    print("─" * 60)
    try:
        log = extract_daily_log(text)
        if log.expenses:
            print("💰 EXPENSES:")
            for e in log.expenses:
                split = f" (split: {', '.join(e.split_with)})" if e.split_with else ""
                print(f"   {e.amount} {e.currency} | {e.category} | {e.description}{split}")
        if log.fitness_activities:
            print("🏃 FITNESS:")
            for f in log.fitness_activities:
                print(f"   {f.activity_type} — {f.duration_minutes} min")
        if log.nutrition_items:
            print("🍽️  NUTRITION:")
            for n in log.nutrition_items:
                print(f"   {n.food_item} — {n.calories} kcal")
        if not log.expenses and not log.fitness_activities and not log.nutrition_items:
            print("   ⚠️  Nothing extracted")
    except Exception as e:
        print(f"   ❌ ERROR: {e}")
