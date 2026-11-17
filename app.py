import hmac
import os
import time

import openai
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

LANGUAGES = {
    "영어": "English",
    "일본어": "Japanese",
    "베트남어": "Vietnamese",
}

MAX_CHARS = 2000
TIMEOUT_SECONDS = 20  # 시도당 대기 시간. 재시도 1회 포함 최대 약 40초
RATE_LIMIT = 20  # 세션당 허용 호출 수
RATE_WINDOW_SECONDS = 600  # 위 호출 수를 세는 구간(10분)

SYSTEM_PROMPT = (
    "You are a professional translator. Translate the user's text into {target_language}. "
    "Output only the translation, with no explanations."
)


class TranslationError(Exception):
    """사용자에게 그대로 보여줘도 되는 메시지를 담는 예외."""


def get_config(name: str) -> str | None:
    """환경변수(.env 포함)를 먼저 보고, 없으면 st.secrets에서 찾는다."""
    value = os.getenv(name)
    if value:
        return value
    try:
        return st.secrets.get(name)
    except Exception:  # secrets 파일이 없는 경우 등
        return None


def translate(text: str, target_language: str) -> str:
    api_key = get_config("OPENAI_API_KEY")
    model = get_config("MODEL_NAME")
    if not api_key or not model:
        raise TranslationError(
            "API 설정이 없습니다. `.env` 파일에 `OPENAI_API_KEY`와 `MODEL_NAME`을 설정해주세요. "
            "(배포 환경에서는 Streamlit Secrets에 등록)"
        )

    client = OpenAI(
        api_key=api_key,
        base_url=get_config("OPENAI_BASE_URL") or None,
        timeout=TIMEOUT_SECONDS,
        max_retries=1,
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(target_language=target_language)},
                {"role": "user", "content": text},
            ],
        )
    except openai.AuthenticationError:
        raise TranslationError("API 키가 올바르지 않습니다. 설정을 확인해주세요.")
    except openai.NotFoundError:
        raise TranslationError("모델 또는 API 주소를 찾을 수 없습니다. `MODEL_NAME`과 `OPENAI_BASE_URL`을 확인해주세요.")
    except openai.RateLimitError:
        raise TranslationError("요청이 너무 많거나 사용 한도를 초과했습니다. 잠시 후 다시 시도해주세요.")
    except openai.APITimeoutError:
        raise TranslationError(f"응답 시간이 {TIMEOUT_SECONDS}초를 초과했습니다. 잠시 후 다시 시도해주세요.")
    except openai.APIConnectionError:
        raise TranslationError("서버에 연결할 수 없습니다. 네트워크 상태를 확인해주세요.")
    except openai.APIError:
        raise TranslationError("번역 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise TranslationError("번역 결과가 비어 있습니다. 다시 시도해주세요.")
    return content.strip()


def require_access_code() -> None:
    """ACCESS_CODE가 설정된 경우에만 접근 코드를 요구한다. (로컬 개발에서는 설정하지 않으면 통과)"""
    expected = get_config("ACCESS_CODE")
    if not expected or st.session_state.get("authed"):
        return
    st.caption("접근 코드를 입력해야 사용할 수 있습니다.")
    code = st.text_input("접근 코드", type="password")
    if code:
        if hmac.compare_digest(code.encode(), str(expected).encode()):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("접근 코드가 올바르지 않습니다.")
    st.stop()


def allow_call() -> bool:
    """세션 단위로 최근 RATE_WINDOW_SECONDS 동안의 호출 수를 제한한다."""
    now = time.time()
    calls = [t for t in st.session_state.get("calls", []) if now - t < RATE_WINDOW_SECONDS]
    allowed = len(calls) < RATE_LIMIT
    if allowed:
        calls.append(now)
    st.session_state["calls"] = calls
    return allowed


st.set_page_config(page_title="번역기", page_icon="🌐", layout="centered")

# 하드코딩 색상 없이 여백/모서리만 다듬어 라이트·다크 테마 모두에서 읽히게 한다.
st.markdown(
    """
    <style>
    .block-container { padding-top: 2.5rem; max-width: 720px; }
    .stButton > button { height: 3rem; font-weight: 600; border-radius: 0.75rem; }
    .result-label { font-size: 0.9rem; opacity: 0.7; margin-bottom: 0.25rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🌐 번역기")
st.caption("텍스트를 입력하고 번역할 언어 버튼을 눌러보세요.")

require_access_code()

text = st.text_area(
    "번역할 텍스트",
    height=200,
    placeholder="여기에 번역할 내용을 입력하세요",
)
length = len(text)
too_long = length > MAX_CHARS
st.caption(f"{length:,} / {MAX_CHARS:,}자")
if too_long:
    st.error(f"입력은 최대 {MAX_CHARS:,}자까지 가능합니다. {length - MAX_CHARS:,}자를 줄여주세요.")

clicked = None
cols = st.columns(len(LANGUAGES))
for col, label in zip(cols, LANGUAGES):
    if col.button(label, icon=":material/translate:", type="primary", use_container_width=True):
        clicked = label

if clicked:
    st.session_state.pop("result", None)
    st.session_state.pop("error", None)
    if not text.strip():
        st.warning("텍스트를 입력해주세요.")
    elif too_long:
        pass  # 위에서 이미 안내함
    elif not allow_call():
        st.session_state["error"] = (
            f"짧은 시간에 너무 많이 요청했습니다. {RATE_WINDOW_SECONDS // 60}분 뒤에 다시 시도해주세요."
        )
    else:
        try:
            with st.spinner(f"{clicked}로 번역하는 중..."):
                result = translate(text, LANGUAGES[clicked])
            st.session_state["result"] = {"language": clicked, "text": result, "source": text}
        except TranslationError as e:
            st.session_state["error"] = str(e)
        except Exception:
            st.session_state["error"] = "알 수 없는 오류가 발생했습니다. 잠시 후 다시 시도해주세요."

if "error" in st.session_state:
    st.error(st.session_state["error"])

if "result" in st.session_state:
    res = st.session_state["result"]
    if text != res["source"]:
        st.info("입력이 바뀌었습니다. 아래는 이전 입력의 번역 결과이니, 다시 번역하려면 버튼을 눌러주세요.")
    with st.container(border=True):
        st.markdown(
            f"<div class='result-label'>{res['language']} 번역 결과</div>",
            unsafe_allow_html=True,
        )
        st.code(res["text"], language=None, wrap_lines=True)
