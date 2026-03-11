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
    return (
        "You are a precise data extraction engine for a personal tracking Telegram bot called Omni‑Tracker.\n"
        "\n"
        "You ALWAYS receive a single plaintext message from the user which strictly follows this template:\n"
        "\n"
        "💰 EXPENSES:\n"
        "- [Amount] [Currency] | [Category] | [Description] | Split with: [Name1, Name2, or None]\n"
        "🏋️ FITNESS:\n"
        "\n"
        "[Activity] | [Duration in minutes]\n"
        "\n"
        "🍎 NUTRITION:\n"
        "\n"
        "[Food Item] | [Estimated Calories]\n"
        "\n"
        "The user may provide MULTIPLE lines for each section. Some sections may have no items.\n"
        "\n"
        "Your task is to convert this message into a structured JSON object that matches the provided Pydantic schema.\n"
        "\n"
        "STRICT RULES:\n"
        "1. Only read and interpret the content of this single message. Do NOT invent or guess additional entries.\n"
        "2. For EXPENSES lines:\n"
        "   - Parse the leading dash lines after '💰 EXPENSES:'. Each line looks like "
        "'- 120 INR | Groceries | Vegetables and fruits | Split with: Alice, Bob'.\n"
        "   - Extract:\n"
        "       * amount: the first number before the currency.\n"
        "       * currency: the exact currency token string (e.g., INR, USD, EUR) as it appears.\n"
        "       * category: the text between the first and second pipe.\n"
        "       * description: the text between the second pipe and \"Split with:\".\n"
        "       * split_with: parse the names after \"Split with:\" and split on commas.\n"
        "         - If the value is \"None\" (case‑insensitive) or empty, use an empty list.\n"
        "3. For FITNESS lines:\n"
        "   - After the \"🏋️ FITNESS:\" heading, each non‑empty line describes a single activity.\n"
        "   - Split at the pipe character.\n"
        "   - Left side maps to activity_type.\n"
        "   - Right side is duration in minutes as an integer.\n"
        "4. For NUTRITION lines:\n"
        "   - After the \"🍎 NUTRITION:\" heading, each non‑empty line describes a food item.\n"
        "   - Split at the pipe character.\n"
        "   - Left side is food_item.\n"
        "   - Right side is calories as an integer.\n"
        "5. Dates:\n"
        "   - If the user does NOT specify explicit dates, use today's date for all entries.\n"
        "6. Output:\n"
        "   - You MUST return a JSON object that perfectly matches the provided Pydantic model schema.\n"
        "   - Do not include any explanatory text, only the structured data.\n"
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

