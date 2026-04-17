"""Thin LLM wrapper over the Anthropic SDK.

Provides call() for text responses and call_json() for structured JSON responses.
"""
import json
import os
import re

import anthropic


def _get_client() -> anthropic.Anthropic:
    """Create an Anthropic client. Reads API key from env."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY not set. Add it to .env or set the environment variable.')
    return anthropic.Anthropic(api_key=api_key)


def call(model: str, system_prompt: str, user_message: str, max_tokens: int = 4096) -> str:
    """Call Claude and return the text response."""
    client = _get_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_message}],
    )
    return response.content[0].text


def call_json(model: str, system_prompt: str, user_message: str, max_tokens: int = 4096):
    """Call Claude and parse the response as JSON.

    Extracts JSON from the response (handles markdown code blocks).
    Retries once on parse failure with an error prompt.
    """
    client = _get_client()
    last_response = None
    last_error = None

    for attempt in range(2):
        messages = [{'role': 'user', 'content': user_message}]
        if attempt > 0 and last_response is not None:
            messages.append({'role': 'assistant', 'content': last_response})
            messages.append({'role': 'user', 'content': f'That response was not valid JSON. Error: {last_error}\n\nPlease respond with ONLY valid JSON, no markdown fences or extra text.'})

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )
        text = response.content[0].text

        try:
            return _extract_json(text)
        except json.JSONDecodeError as e:
            last_response = text
            last_error = str(e)

    raise ValueError(f'Failed to get valid JSON after 2 attempts. Last response:\n{text[:500]}')


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas before ] or } — common LLM JSON mistake."""
    return re.sub(r',\s*([}\]])', r'\1', text)


def _try_parse(text: str):
    """Try json.loads, then retry after stripping trailing commas."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = _strip_trailing_commas(text)
        return json.loads(cleaned)


def _extract_json(text: str):
    """Extract JSON from a response that might contain markdown code blocks.

    Handles trailing commas (common LLM mistake) and various wrapping formats.
    Returns a dict or list depending on the JSON content.
    """
    # Try direct parse first
    stripped = text.strip()
    if stripped.startswith('{') or stripped.startswith('['):
        try:
            return _try_parse(stripped)
        except json.JSONDecodeError:
            pass  # Fall through to other extraction methods

    # Try extracting from ```json ... ``` blocks
    if '```' in text:
        parts = text.split('```')
        for part in parts:
            clean = part.strip()
            if clean.startswith('json'):
                clean = clean[4:].strip()
            if clean.startswith('{') or clean.startswith('['):
                try:
                    return _try_parse(clean)
                except json.JSONDecodeError:
                    continue

    # Last resort: find first { to last } or [ to ]
    for open_char, close_char in [('{', '}'), ('[', ']')]:
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start != -1 and end != -1 and end > start:
            try:
                return _try_parse(text[start:end + 1])
            except json.JSONDecodeError:
                continue

    raise json.JSONDecodeError('No JSON found in response', text, 0)


def load_prompt(name: str, **kwargs) -> str:
    """Load a prompt template from the prompts/ directory and format it."""
    prompt_dir = os.path.join(os.path.dirname(__file__), 'prompts')
    path = os.path.join(prompt_dir, f'{name}.md')
    with open(path) as f:
        template = f.read()
    # Simple string formatting for {variable} placeholders
    for key, value in kwargs.items():
        template = template.replace('{' + key + '}', str(value))
    return template
