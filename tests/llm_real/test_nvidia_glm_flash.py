"""Live smoke test for NVIDIA NIM's OpenAI-compatible endpoint."""
import os

from openai import OpenAI


def test_nvidia_glm_53_flash_chat_completion() -> None:
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise AssertionError("NVIDIA_API_KEY is required for this live integration test")

    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key,
        timeout=60.0,
        max_retries=1,
    )
    response = client.chat.completions.create(
        model="z-ai/glm-5.3-flash",
        messages=[
            {"role": "system", "content": "Answer the decimal comparison correctly."},
            {"role": "user", "content": "Which number is larger, 9.11 or 9.8? State the larger number first."},
        ],
        temperature=0.0,
        top_p=1,
        max_tokens=128,
        stream=False,
    )

    assert response.choices, "NVIDIA endpoint returned no choices"
    answer = response.choices[0].message.content
    assert isinstance(answer, str) and answer.strip(), "NVIDIA endpoint returned empty content"
    first_line = answer.strip().splitlines()[0]
    assert "9.8" in first_line, f"Expected 9.8 to be identified first; got: {answer!r}"
