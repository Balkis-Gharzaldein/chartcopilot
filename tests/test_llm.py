import os
import subprocess
import sys


def test_provider_models_are_configured_independently():
    env = os.environ.copy()
    env.update(
        {
            "ANTHROPIC_MODEL": "claude-test",
            "OPENAI_MODEL": "openai-test",
            "GEMINI_MODEL": "gemini-test",
            "CHARTCOPILOT_MODEL": "wrong-shared-model",
        }
    )
    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "import llm; print(llm.DEFAULT_MODEL_ANTHROPIC, llm.DEFAULT_MODEL_OPENAI, llm.DEFAULT_MODEL_GEMINI)",
        ],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        text=True,
    )
    assert output.strip() == "claude-test openai-test gemini-test"
