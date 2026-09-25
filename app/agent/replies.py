"""Fixed replies and constants shared by the agent's nodes."""

from app.llm.client import LlmErrorCode

TEMPERATURE = 0

READ_ONLY_REPLY = (
    "I can only read from this database, not change it. "
    "Try asking a question about the existing data instead."
)

OUT_OF_SCOPE_REPLY = (
    "I can only answer questions about the e-commerce database "
    "(customers, products, orders and order items). Try asking about "
    "customers, sales or products."
)

LLM_ERROR_REPLIES = {
    LlmErrorCode.RATE_LIMITED: "The AI service is busy right now. Please try again in a minute.",
    LlmErrorCode.TIMEOUT: "The AI service took too long to respond. Please try again.",
    LlmErrorCode.UNAVAILABLE: "The AI service is unavailable right now. Please try again shortly.",
    LlmErrorCode.MODEL_UNAVAILABLE: "The AI model is unavailable. Please try again later.",
    LlmErrorCode.BAD_REQUEST: "That question could not be processed. Try a shorter or simpler one.",
}

SUMMARY_UNAVAILABLE_REPLY = "Here are the results; a summary couldn't be generated right now."

NO_USAGE = {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0}

# guard_input (Phase 5): questions rejected before any model call.
EMPTY_QUESTION_REPLY = "Please type a question about the customers, products or orders data."
NO_TEXT_REPLY = "That doesn't look like a question. Try asking about customers, products or orders."
TOO_LONG_REPLY = "That question is too long. Please shorten it to under 500 characters."

# classify_intent (Phase 5): used when the model asks to clarify but gives no question.
CLARIFY_FALLBACK = "Could you say a bit more about what you mean, so I pick the right data?"
