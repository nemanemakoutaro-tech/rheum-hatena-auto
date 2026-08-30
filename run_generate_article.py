"""Lean/safe entry point for the daily article generator.

This wrapper keeps generate_article.py unchanged while:
- shrinking the history catalog sent to the theme-selector prompt,
- capping output size/search context to reduce TPM pressure,
- retrying only short-lived 429s,
- failing fast when the API asks us to wait for a long time so a later
  GitHub Actions schedule can retry without keeping a runner asleep.
"""

import os
import time

import generate_article as ga


LONG_RATE_LIMIT_SECONDS = 120.0
MAX_SHORT_WAIT_SECONDS = 90.0
MAX_OUTPUT_TOKENS = 3400
HISTORY_PER_ITEM_CHARS = 160
HISTORY_MAX_CHARS = 10000


_original_compact_history_for_prompt = ga.compact_history_for_prompt


def compact_history_for_prompt(history_catalog, per_item_chars=HISTORY_PER_ITEM_CHARS, max_chars=HISTORY_MAX_CHARS):
    """Use a smaller history excerpt for LLM theme selection.

    Full-history duplicate detection remains local in generate_article.py,
    so reducing this prompt does not disable the duplicate guard.
    """
    return _original_compact_history_for_prompt(
        history_catalog,
        per_item_chars=min(per_item_chars, HISTORY_PER_ITEM_CHARS),
        max_chars=min(max_chars, HISTORY_MAX_CHARS),
    )


def create_response_with_rate_limit_retry(client, operation_name, max_attempts=3, **kwargs):
    """Retry short 429s, but immediately hand long limits to later schedules."""
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return client.responses.create(**kwargs)
        except ga.RateLimitError as exc:
            last_error = exc
            requested_wait = ga.parse_rate_limit_wait_seconds(exc)

            if requested_wait is not None and requested_wait > LONG_RATE_LIMIT_SECONDS:
                raise RuntimeError(
                    f"{operation_name} hit a long OpenAI rate limit "
                    f"({requested_wait:.1f}s). Failing fast; a later scheduled "
                    "GitHub Actions run will retry."
                ) from exc

            if attempt >= max_attempts:
                break

            fallback_wait = 20.0 * attempt
            wait_seconds = fallback_wait if requested_wait is None else max(requested_wait, 5.0)
            wait_seconds = min(wait_seconds, MAX_SHORT_WAIT_SECONDS)

            print(
                f"Short rate limit during {operation_name} "
                f"(attempt {attempt}/{max_attempts}); retrying in "
                f"{wait_seconds:.1f}s."
            )
            time.sleep(wait_seconds)

    raise RuntimeError(
        f"{operation_name} failed after {max_attempts} attempts because of "
        "OpenAI rate limits."
    ) from last_error


def cap_generation_settings():
    try:
        configured_output = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", str(MAX_OUTPUT_TOKENS)))
    except ValueError:
        configured_output = MAX_OUTPUT_TOKENS

    os.environ["OPENAI_MAX_OUTPUT_TOKENS"] = str(min(configured_output, MAX_OUTPUT_TOKENS))
    os.environ["OPENAI_SEARCH_CONTEXT"] = "low"

    print(
        "Lean generation settings: "
        f"max_output_tokens={os.environ['OPENAI_MAX_OUTPUT_TOKENS']}, "
        "search_context=low, "
        f"history_prompt_max_chars={HISTORY_MAX_CHARS}."
    )


def main():
    cap_generation_settings()
    ga.compact_history_for_prompt = compact_history_for_prompt
    ga.create_response_with_rate_limit_retry = create_response_with_rate_limit_retry
    ga.main()


if __name__ == "__main__":
    main()
