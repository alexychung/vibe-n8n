"""INTERVIEW phase — extract structured requirements from user description.

Interactive mode: infer what we can, ask remaining questions via stdin.
Non-interactive mode: infer everything from brief, fail if critical fields missing.
"""
import json
import sys

from llm import call_json, load_prompt

INTERVIEW_MODEL = 'claude-haiku-4-5-20251001'
CONSOLIDATE_MODEL = 'claude-haiku-4-5-20251001'

CRITICAL_FIELDS = ('outcome', 'trigger', 'stakes')


def interview_interactive(description: str) -> dict:
    """Run adaptive interview. Returns structured requirements dict.

    1. One LLM call to infer answers and identify remaining questions
    2. Ask remaining questions via stdin
    3. One LLM call to consolidate into final requirements
    """
    # Step 1: Infer + identify questions
    system_prompt = load_prompt('interview')
    result = call_json(INTERVIEW_MODEL, system_prompt, description)

    inferred = result.get('inferred', {})
    questions = result.get('questions_to_ask', [])

    # Show inferences
    print('\nFrom your description, I infer:')
    for key, value in inferred.items():
        if value:
            print(f'  - {key}: {value}')

    # Step 2: Ask remaining questions
    answers = {}
    if questions:
        print(f'\nLet me confirm {len(questions)} thing(s):\n')
        for i, question in enumerate(questions, 1):
            print(f'{i}. {question}')
            answer = input('   > ').strip()
            answers[f'q{i}'] = answer
            print()

    # Step 3: Consolidate
    consolidate_prompt = (
        f'Original description: {description}\n\n'
        f'Inferred answers: {json.dumps(inferred)}\n\n'
        f'User answers to follow-up questions: {json.dumps(answers)}\n\n'
        'Produce the final structured requirements as JSON with these fields:\n'
        'outcome, trigger, stakes, success_criteria, systems, volume, budget, editors\n'
        'All fields should be filled in. Use inferred values where the user confirmed or didn\'t contradict.'
    )
    requirements = call_json(CONSOLIDATE_MODEL, system_prompt, consolidate_prompt)

    return requirements


def interview_from_brief(brief_text: str) -> dict:
    """Non-interactive: infer all requirements from a brief.

    Returns requirements dict or raises SystemExit if critical fields missing.
    """
    system_prompt = load_prompt('interview')
    prompt = (
        f'{brief_text}\n\n'
        'This is a complete brief. Infer ALL answers — do not list any questions_to_ask.\n'
        'Return ONLY the "inferred" object as the top-level JSON (not wrapped in {inferred: ...}).\n'
        'All fields must be filled: outcome, trigger, stakes, success_criteria, systems, volume, budget, editors.\n'
        'For fields not mentioned in the brief, use sensible defaults:\n'
        '- stakes: "low" if not mentioned\n'
        '- volume: "1 per trigger" if not mentioned\n'
        '- budget: "no constraint" if not mentioned\n'
        '- editors: "developer only" if not mentioned'
    )

    requirements = call_json(INTERVIEW_MODEL, system_prompt, prompt)

    # Validate critical fields
    missing = []
    for field in CRITICAL_FIELDS:
        if not requirements.get(field):
            missing.append(field)

    if missing:
        print(f'Error: Brief is too vague. Cannot infer: {", ".join(missing)}')
        print('Add more detail to the brief or use interactive mode.')
        raise SystemExit(1)

    return requirements
