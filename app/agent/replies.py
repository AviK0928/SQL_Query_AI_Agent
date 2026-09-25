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
