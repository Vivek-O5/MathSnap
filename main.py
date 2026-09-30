import base64
import json
import os
import re
import time
from typing import Any

import sympy as sp
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types


load_dotenv()


# ============================================================
# APP CONFIGURATION
# ============================================================

app = FastAPI(
    title="MathSnap API",
    version="1.0.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
)

MAX_IMAGE_MB = int(
    os.getenv("MAX_IMAGE_MB", "10")
)


# ============================================================
# GEMINI CLIENT
# ============================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = (
    genai.Client(api_key=GEMINI_API_KEY)
    if GEMINI_API_KEY
    else None
)


# ============================================================
# AI PROMPT
# ============================================================

PROMPT = r"""
You are the math-recognition and explanation engine for MathSnap.

Look carefully at the uploaded image.

Identify the mathematical problem exactly.

Do not invent missing symbols.

If the image is unclear, say so.

Solve the problem step by step.

Prefer exact symbolic answers over decimal approximations.

Return ONLY valid JSON matching the requested structure.

Required JSON structure:

{
  "question": "clean transcription in plain text",
  "latex": "LaTeX transcription",
  "topic": "Algebra | Arithmetic | Calculus | Geometry | Trigonometry | Probability | Statistics | Matrices | Other",
  "steps": [
    {
      "title": "Step 1",
      "explanation": "short explanation",
      "latex": "equation if useful"
    }
  ],
  "answer": "final answer in plain text",
  "answer_latex": "final answer in LaTeX",
  "verification_expression": "optional expression/equation useful for independent checking",
  "verification_type": "equation | arithmetic | none"
}

For equations:

verification_expression should contain the original equation
in plain SymPy-style text.

Example:

2*x^2 + 5*x - 3 = 0

For simple arithmetic:

verification_expression should contain the arithmetic expression itself.

Do not claim an answer is verified merely because you solved it.

Formatting rules:

- "latex" must contain ONLY raw LaTeX.
- "answer_latex" must contain ONLY raw LaTeX.
- Do NOT put $, $$, \( \), or \[ \] around LaTeX fields.
- In explanation text, inline math may use single dollar signs.
- Keep verification_expression in plain SymPy-style text.
- Do not use Markdown code fences.
- Return valid JSON only.

If the image is unclear, explain the problem is unclear rather than guessing.
"""


# ============================================================
# HELPERS
# ============================================================

def strip_json_fences(text: str) -> str:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = re.sub(
            r"\s*```$",
            "",
            text
        )

    return text.strip()


# ============================================================
# GEMINI RETRY LOGIC
# ============================================================

def is_temporary_gemini_error(exc: Exception) -> bool:
    """
    Detect temporary Gemini availability/capacity errors.

    We retry errors such as:
    - 503 UNAVAILABLE
    - high demand
    - temporary service unavailable
    - 429 rate/resource limits
    - 500 internal server errors
    """

    error_text = str(exc).upper()

    temporary_errors = [
        "503",
        "UNAVAILABLE",
        "HIGH DEMAND",
        "SERVICE UNAVAILABLE",
        "429",
        "RESOURCE_EXHAUSTED",
        "RATE LIMIT",
        "500",
        "INTERNAL"
    ]

    return any(
        error in error_text
        for error in temporary_errors
    )


def generate_with_retry(
    image_part: types.Part,
    max_attempts: int = 4
):
    """
    Send the MathSnap request to Gemini.

    If Gemini temporarily returns a 503/high-demand error,
    automatically retry with increasing delays.

    Attempts:
        1 -> immediately
        2 -> wait 2 seconds
        3 -> wait 4 seconds
        4 -> wait 8 seconds
    """

    last_error = None

    for attempt in range(1, max_attempts + 1):

        try:

            response = client.models.generate_content(
                model=MODEL,
                contents=[
                    PROMPT,
                    image_part
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )

            return response

        except Exception as exc:

            last_error = exc

            # If this is not a temporary error,
            # don't waste time retrying it.
            if not is_temporary_gemini_error(exc):
                raise

            # If this was the final attempt,
            # return the original error.
            if attempt == max_attempts:
                raise last_error

            # Exponential backoff:
            # 2 sec -> 4 sec -> 8 sec
            wait_time = 2 ** attempt

            time.sleep(wait_time)

    raise last_error


# ============================================================
# SYMPY VERIFICATION
# ============================================================

def sympy_verify(data: dict[str, Any]) -> dict[str, Any]:

    verification_type = data.get(
        "verification_type",
        "none"
    )

    expr = (
        data.get("verification_expression")
        or ""
    ).strip()

    answer = (
        data.get("answer")
        or ""
    ).strip()

    if not expr:
        return {
            "status": "not_checked",
            "message": "No independently checkable expression was supplied."
        }

    try:

        # ----------------------------------------------------
        # Arithmetic
        # ----------------------------------------------------

        if verification_type == "arithmetic":

            value = sp.sympify(
                expr,
                evaluate=True
            )

            return {
                "status": "verified",
                "message": (
                    f"SymPy evaluated the expression to "
                    f"{sp.sstr(value)}."
                )
            }

        # ----------------------------------------------------
        # Equation
        # ----------------------------------------------------

        if verification_type == "equation":

            candidates = re.findall(
                r"(?:x|y|z)\s*=\s*"
                r"([\-+]?\d+(?:\.\d+)?(?:/\d+)?)",
                answer,
                re.I
            )

            if "=" in expr:

                left, right = expr.split(
                    "=",
                    1
                )

                equation = sp.Eq(
                    sp.sympify(left),
                    sp.sympify(right)
                )

            else:

                equation = sp.Eq(
                    sp.sympify(expr),
                    0
                )

            variable = next(
                iter(equation.free_symbols),
                None
            )

            if variable is None:

                return {
                    "status": "verified",
                    "message": (
                        "The equation contains no free variable."
                    )
                }

            if not candidates:

                return {
                    "status": "not_checked",
                    "message": (
                        "The solution could not be parsed into "
                        "candidate values for substitution."
                    )
                }

            checks = []
            all_ok = True

            for raw_value in candidates:

                try:

                    val = sp.sympify(raw_value)

                    ok = bool(
                        equation.subs(
                            variable,
                            val
                        )
                    )

                    checks.append(
                        f"{variable}={raw_value}: "
                        f"{'correct' if ok else 'does not satisfy the equation'}"
                    )

                    all_ok = all_ok and ok

                except Exception:

                    all_ok = False

            return {
                "status": (
                    "verified"
                    if all_ok
                    else "failed"
                ),
                "message": "; ".join(checks)
            }

        # ----------------------------------------------------
        # No verifier
        # ----------------------------------------------------

        return {
            "status": "not_checked",
            "message": (
                "This problem type needs a specialized verifier."
            )
        }

    except Exception as exc:

        return {
            "status": "not_checked",
            "message": (
                f"Independent check unavailable: {exc}"
            )
        }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/api/health")
def health():

    return {
        "ok": True,
        "model": MODEL,
        "ai_configured": client is not None
    }


# ============================================================
# SOLVE ENDPOINT
# ============================================================

@app.post("/api/solve")
async def solve(
    file: UploadFile = File(...)
):

    # --------------------------------------------------------
    # Validate image
    # --------------------------------------------------------

    if (
        not file.content_type
        or not file.content_type.startswith("image/")
    ):

        raise HTTPException(
            status_code=400,
            detail="Please upload an image file."
        )

    # --------------------------------------------------------
    # Read image
    # --------------------------------------------------------

    raw = await file.read()

    if len(raw) > MAX_IMAGE_MB * 1024 * 1024:

        raise HTTPException(
            status_code=413,
            detail=(
                f"Image is too large. "
                f"Maximum is {MAX_IMAGE_MB} MB."
            )
        )

    # --------------------------------------------------------
    # Check Gemini API key
    # --------------------------------------------------------

    if client is None:

        raise HTTPException(
            status_code=503,
            detail=(
                "GEMINI_API_KEY is not configured "
                "on the backend."
            )
        )

    mime = file.content_type

    # --------------------------------------------------------
    # Send image + prompt to Gemini
    # WITH AUTOMATIC RETRY
    # --------------------------------------------------------

    try:

        image_part = types.Part.from_bytes(
            data=raw,
            mime_type=mime
        )

        response = generate_with_retry(
            image_part=image_part,
            max_attempts=4
        )

        text = response.text or ""

        text = strip_json_fences(text)

        result = json.loads(text)

    except json.JSONDecodeError:

        raise HTTPException(
            status_code=502,
            detail=(
                "Gemini returned an invalid solution format. "
                "Please try the image again."
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail=f"Gemini solving failed: {exc}"
        )

    # --------------------------------------------------------
    # Validate response
    # --------------------------------------------------------

    required = [
        "question",
        "steps",
        "answer"
    ]

    if any(
        key not in result
        for key in required
    ):

        raise HTTPException(
            status_code=502,
            detail=(
                "The AI response was incomplete."
            )
        )

    # --------------------------------------------------------
    # Independent verification
    # --------------------------------------------------------

    result["verification"] = sympy_verify(
        result
    )

    return result
