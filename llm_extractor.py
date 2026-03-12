from datetime import date as Date
import logging
from typing import List, Sequence

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableSerializable
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field, field_validator


logger = logging.getLogger(__name__)


class Expense(BaseModel):
    date: Date = Field(
        default_factory=Date.today,
        description="The calendar date the expense applies to. Use today's date if not explicitly provided.",
    )
    amount: float = Field(..., description="Total expense amount as a positive number.")
    currency: str = Field(
        ...,
        description='Exact 3–10 character currency string appearing in the user text, e.g. "USD", "EUR", "JPY", "INR".',
    )
    category: str = Field(..., description="Short category label such as Groceries, Rent, Cafe, Transport, etc.")
    description: str = Field(
        ...,
        description="Free‑form text description copied or summarized from the user entry.",
    )
    split_with: List[str] = Field(
        default_factory=list,
        description=(
            'List of names that the expense is split with. '
            "If the user specifies 'None' or leaves it empty, use an empty list."
        ),
    )

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        if v < 0:
            raise ValueError("amount must be non‑negative")
        return v

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("currency must not be empty")
        return v.upper()

    @field_validator("split_with", mode="before")
    @classmethod
    def normalize_split_with(cls, v: object) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str) and v.strip().lower() == "none":
            return []
        if isinstance(v, str):
            # Split on commas and strip
            items = [name.strip() for name in v.split(",") if name.strip()]
            return items
        if isinstance(v, list):
            return [str(name).strip() for name in v if str(name).strip()]
        return []


class FitnessActivity(BaseModel):
    date: Date = Field(
        default_factory=Date.today,
        description="The calendar date the fitness activity applies to. Use today's date if not explicitly provided.",
    )
    activity_type: str = Field(..., description="Type of fitness activity, e.g. Running, Walking, Yoga.")
    duration_minutes: int = Field(..., description="Duration of the activity in whole minutes.")

    @field_validator("duration_minutes")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v < 0:
            raise ValueError("duration_minutes must be non‑negative")
        return v


class NutritionItem(BaseModel):
    date: Date = Field(
        default_factory=Date.today,
        description="The calendar date the nutrition item applies to. Use today's date if not explicitly provided.",
    )
    food_item: str = Field(..., description="Name of the food item.")
    calories: int = Field(..., description="Estimated calories for this food item.")

    @field_validator("calories")
    @classmethod
    def validate_calories(cls, v: int) -> int:
        if v < 0:
            raise ValueError("calories must be non‑negative")
        return v


class DailyLog(BaseModel):
    expenses: List[Expense] = Field(default_factory=list)
    fitness_activities: List[FitnessActivity] = Field(default_factory=list)
    nutrition_items: List[NutritionItem] = Field(default_factory=list)


def _build_system_prompt() -> str:
    today = Date.today().isoformat()  # e.g. "2026-03-12" — real date injected at call time
    return (
        f"Today's date is {today}. Use this exact date for all entries unless the user says otherwise.\n"
        "\n"
        "You are a smart data extraction engine for a personal tracking Telegram bot called Omni-Tracker.\n"
        "\n"
        "The user will send you a FREE-FORM message in natural language describing their day.\n"
        "It may mention any combination of expenses, fitness activities, and food/nutrition.\n"
        "There is NO required format — the user can write however feels natural to them.\n"
        "\n"
        "Examples of valid inputs:\n"
        "  - 'spent 50 bucks on lunch, went for a 30 min run, had a salad around 400 cal'\n"
        "  - 'Bought groceries for 120 INR. Did yoga for 45 minutes. Ate rice and dal.'\n"
        "  - 'Coffee 3.50 USD, split the dinner (40 EUR) with Alice and Bob. Cycled 1 hour.'\n"
        "  - 'No workouts today. Had pizza (800 cal) and pasta (600 cal). Spent 25 GBP on dinner.'\n"
        "\n"
        "Your task is to extract ALL mentioned expenses, fitness activities, and food items "
        "and return them as a structured JSON object matching the provided schema.\n"
        "\n"
        "RULES:\n"
        "1. EXPENSES — extract every money amount mentioned:\n"
        "   - amount: the numeric value\n"
        "   - currency: the currency mentioned (e.g. USD, EUR, INR, GBP). "
        "If no currency is mentioned, default to USD.\n"
        "   - description: what the money was spent on\n"
        "   - category: infer a short category label (Food, Transport, Shopping, Health, Entertainment, Utilities, General)\n"
        "   - split_with: list of names if the user says they split it with someone. Empty list if not mentioned.\n"
        "2. FITNESS — extract every physical activity mentioned:\n"
        "   - activity_type: name of the activity (e.g. Running, Yoga, Cycling)\n"
        "   - duration_minutes: duration as an integer. "
        "Convert hours to minutes (e.g. '1 hour' = 60). If not mentioned, make a reasonable estimate.\n"
        "3. NUTRITION — extract every food or drink item mentioned:\n"
        "   - food_item: name of the food\n"
        "   - calories: integer calorie estimate. "
        "If the user provides a number use it; otherwise estimate based on common knowledge.\n"
        f"4. DATES — use {today} for all entries unless the user explicitly mentions a different date. Never guess or invent a date.\n"
        "5. DO NOT invent entries that are not mentioned. If nothing fits a category, return an empty list.\n"
        "6. Return ONLY the structured JSON — no explanatory text.\n"
    )


def build_extraction_chain(model_name: str = "gemini-2.5-flash") -> RunnableSerializable:
    """
    Build a LangChain pipeline that maps raw user text into a DailyLog object.

    The resulting chain accepts a dict with a single key \"user_input\" (the raw message text)
    and returns a DailyLog instance.
    """
    parser: PydanticOutputParser[DailyLog] = PydanticOutputParser(pydantic_object=DailyLog)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _build_system_prompt()),
            (
                "human",
                "User daily log message:\n\n{user_input}\n\n"
                "Carefully parse this text and output ONLY the structured data in the required format.\n"
                "{format_instructions}",
            ),
        ]
    ).partial(format_instructions=parser.get_format_instructions())

    llm = ChatGoogleGenerativeAI(model=model_name, temperature=0)

    chain: RunnableSerializable = prompt | llm | parser
    return chain


def extract_daily_log(raw_text: str, model_name: str = "gemini-2.5-flash") -> DailyLog:
    """
    Run the extraction chain on the given raw message text and return a DailyLog.

    Raises RuntimeError on model or parsing issues.
    """
    try:
        chain = build_extraction_chain(model_name=model_name)
        result: DailyLog = chain.invoke({"user_input": raw_text})
        if not isinstance(result, DailyLog):
            raise RuntimeError("LLM did not return a DailyLog instance.")
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to extract DailyLog from text.")
        raise RuntimeError("LLM extraction failed.") from exc


__all__: Sequence[str] = [
    "Expense",
    "FitnessActivity",
    "NutritionItem",
    "DailyLog",
    "build_extraction_chain",
    "extract_daily_log",
]

