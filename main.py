import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

import sympy as sp
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

app = FastAPI(title="MathSnap API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "10"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

PROMPT = r"""
You are the math-recognition and explanation engine for MathSnap.

Look carefully at the uploaded image. Identify the mathematical problem exactly.
Do not invent missing symbols. If the image is unclear, say so.

Solve it step by step. Prefer exact symbolic answers over decimal approximations.
Return ONLY valid JSON with this shape:

{
  "question": "clean transcription in plain text",
  "latex": "LaTeX transcription",
  "topic": "Algebra | Arithmetic | Calculus | Geometry | Trigonometry | Probability | Statistics | Matrices | Other",
  "steps": [
    {"title": "Step 1", "explanation": "short explanation", "latex": "equation if useful"}
  ],
  "answer": "final answer in plain text",
  "answer_latex": "final answer in LaTeX",
  "verification_expression": "optional expression/equation useful for independent checking",
  "verification_type": "equation | arithmetic | none"
}

For equations, verification_expression should be the original equation in a form useful for substitution,
such as "2*x^2 + 5*x - 3 = 0". For simple arithmetic, use the expression itself.
Do not claim an answer is verified merely because you solved it.
"""


def strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def sympy_verify(data: dict[str, Any]) -> dict[str, Any]:
    verification_type = data.get("verification_type", "none")
    expr = (data.get("verification_expression") or "").strip()
    answer = (data.get("answer") or "").strip()

    if not expr:
        return {
            "status": "not_checked",
            "message": "No independently checkable expression was supplied.",
        }

    try:
        if verification_type == "arithmetic":
            value = sp.sympify(expr, evaluate=True)
            return {
                "status": "verified",
                "message": f"SymPy evaluated the expression to {sp.sstr(value)}.",
            }

        if verification_type == "equation":
            candidates = re.findall(
                r"(?:x|y|z)\s*=\s*([\-+]?\d+(?:\.\d+)?(?:/\d+)?)",
                answer,
                re.I,
            )

            if "=" in expr:
                left, right = expr.split("=", 1)
                equation = sp.Eq(sp.sympify(left), sp.sympify(right))
            else:
                equation = sp.Eq(sp.sympify(expr), 0)

            variable = next(iter(equation.free_symbols), None)

            if variable is None:
                return {
                    "status": "verified",
                    "message": "The equation contains no free variable.",
                }

            if not candidates:
                return {
                    "status": "not_checked",
                    "message": "The solution could not be parsed into candidate values for substitution.",
                }

            checks = []
            all_ok = True

            for raw in candidates:
                try:
                    val = sp.sympify(raw)
                    ok = bool(equation.subs(variable, val))
                    checks.append(
                        f"{variable}={raw}: "
                        f"{'correct' if ok else 'does not satisfy the equation'}"
                    )
                    all_ok = all_ok and ok
                except Exception:
                    all_ok = False

            return {
                "status": "verified" if all_ok else "failed",
                "message": "; ".join(checks),
            }

        return {
            "status": "not_checked",
            "message": "This problem type needs a specialized verifier.",
        }

    except Exception as exc:
        return {
            "status": "not_checked",
            "message": f"Independent check unavailable: {exc}",
        }


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "model": MODEL,
        "ai_configured": GEMINI_API_KEY is not None,
    }


@app.post("/api/solve")
async def solve(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Please upload an image file.")

    raw = await file.read()

    if len(raw) > MAX_IMAGE_MB * 1024 * 1024:
        raise HTTPException(
            413,
            f"Image is too large. Maximum is {MAX_IMAGE_MB} MB.",
        )

    if not GEMINI_API_KEY:
        raise HTTPException(
            503,
            "GEMINI_API_KEY is not configured on the backend.",
        )

    mime = file.content_type
    image_base64 = base64.b64encode(raw).decode("utf-8")

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": image_base64,
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
        },
    }

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{MODEL}:generateContent?key={GEMINI_API_KEY}"
    )

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        candidates = response_data.get("candidates", [])

        if not candidates:
            raise HTTPException(
                502,
                "Gemini did not return a solution. Please try the image again.",
            )

        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = [part.get("text", "") for part in parts if part.get("text")]

        if not text_parts:
            raise HTTPException(
                502,
                "Gemini returned an empty solution. Please try the image again.",
            )

        text = strip_json_fences("\n".join(text_parts))
        result = json.loads(text)

    except HTTPException:
        raise

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(
            502,
            f"Gemini API error: {error_body}",
        )

    except urllib.error.URLError as exc:
        raise HTTPException(
            502,
            f"Could not connect to Gemini API: {exc.reason}",
        )

    except json.JSONDecodeError:
        raise HTTPException(
            502,
            "The AI returned an invalid solution format. Please try the image again.",
        )

    except Exception as exc:
        raise HTTPException(
            502,
            f"AI solving failed: {exc}",
        )

    required = ["question", "steps", "answer"]

    if any(key not in result for key in required):
        raise HTTPException(
            502,
            "The AI response was incomplete.",
        )

    result["verification"] = sympy_verify(result)

    return result
