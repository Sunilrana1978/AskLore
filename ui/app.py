"""
Streamlit chat UI for AskLore. Calls the deployed POST /query endpoint
(RetrievalLambda: Bedrock Retrieve + Gemini generation) and renders a
chat-style conversation with cited sources.

Run: streamlit run ui/app.py
Requires ASKLORE_API_URL env var (or the "api_url" field in
.streamlit/secrets.toml) pointing at the stack's ApiUrl output.
"""

import html
import os
import re

import requests
import streamlit as st

SAMPLE_QUESTIONS = [
    "How do I trigger a manual failover for the primary RDS cluster?",
    "What's the escalation path when on-call can't resolve an incident?",
    "How do I roll back a bad deployment?",
    "What steps do I take when Lambda functions are timing out?",
    "How do I respond to a CloudWatch alarm firing?",
    "How do I safely flush the Redis cache in production?",
    "What's the process for rotating an SSL/TLS certificate?",
    "How do I change a security group without breaking traffic?",
    "Why would an application get access denied on an S3 bucket?",
    "How do I restart the payment service safely?",
    "What could cause an ALB health check to start failing?",
    "How do I investigate high CPU usage on an EC2 instance?",
    "What are common causes of DNS propagation delays?",
    "How do I troubleshoot an auto scaling group that won't scale?",
    "What's the first thing to check when RDS connections start failing?",
]

REQUEST_TIMEOUT_SECONDS = 30

CUSTOM_CSS = """
<style>
    #MainMenu, footer {visibility: hidden;}
    header[data-testid="stHeader"] {
        background: transparent;
    }
    [data-testid="stSidebarCollapsedControl"] {
        visibility: visible !important;
        display: block !important;
    }

    .stApp {
        background: #eef2f7;
    }
    .block-container {
        padding-top: 1.5rem;
        max-width: 880px;
    }

    /* ---- Top brand bar ---- */
    .app-topbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: linear-gradient(135deg, #5b9bd5, #4a72d6);
        border-radius: 14px;
        padding: 1rem 1.4rem;
        margin-bottom: 1.2rem;
        box-shadow: 0 4px 14px rgba(74, 114, 214, 0.28);
    }
    .app-topbar .brand {
        display: flex;
        align-items: center;
        gap: 0.6rem;
    }
    .app-topbar .brand .icon {
        font-size: 1.7rem;
    }
    .app-topbar .brand .title {
        color: #ffffff;
        font-size: 1.35rem;
        font-weight: 700;
        line-height: 1.1;
    }
    .app-topbar .brand .subtitle {
        color: #e4ecfb;
        font-size: 0.8rem;
    }
    .app-topbar .status {
        color: #eaf1ff;
        font-size: 0.82rem;
        display: flex;
        align-items: center;
        gap: 0.4rem;
    }
    .app-topbar .status .dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #4ade80;
        display: inline-block;
    }

    /* ---- Sidebar (light contact-list style) ---- */
    [data-testid="stSidebar"] {
        background-color: #ffffff;
        border-right: 1px solid #e6eaf2;
    }
    [data-testid="stSidebar"] .sidebar-title {
        font-size: 1.05rem;
        font-weight: 700;
        color: #26324a;
        padding-top: 0.3rem;
    }
    [data-testid="stSidebar"] .sidebar-caption {
        color: #8a93a6;
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin: 0.9rem 0 0.4rem 0;
    }
    [data-testid="stSidebar"] .stButton button {
        background-color: #ffffff;
        color: #26324a;
        border: 1px solid #e6eaf2;
        border-left: 3px solid transparent;
        border-radius: 10px;
        text-align: left;
        font-size: 0.83rem;
        padding: 0.55rem 0.75rem;
        white-space: normal;
        box-shadow: 0 1px 2px rgba(20, 30, 60, 0.04);
    }
    [data-testid="stSidebar"] .stButton button:hover {
        border-left: 3px solid #4a72d6;
        background-color: #f3f6fd;
        color: #1f2a44;
    }

    /* ---- Chat bubbles ---- */
    .msg-row {
        display: flex;
        align-items: flex-end;
        gap: 0.55rem;
        margin: 0.55rem 0;
    }
    .msg-row.user {
        justify-content: flex-end;
    }
    .msg-row.assistant {
        justify-content: flex-start;
    }
    .avatar {
        width: 34px;
        height: 34px;
        min-width: 34px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1rem;
    }
    .avatar.user-avatar {
        background: #4a72d6;
        color: white;
    }
    .avatar.assistant-avatar {
        background: #dfe7f9;
        color: #2b3245;
    }
    .bubble {
        max-width: 72%;
        padding: 0.65rem 0.95rem;
        border-radius: 16px;
        font-size: 0.92rem;
        line-height: 1.5;
        box-shadow: 0 1px 3px rgba(20, 30, 60, 0.08);
    }
    .bubble.user-bubble {
        background: #4a72d6;
        color: #ffffff;
        border-bottom-right-radius: 4px;
    }
    .bubble.assistant-bubble {
        background: #ffffff;
        color: #26324a;
        border-bottom-left-radius: 4px;
    }
    .bubble pre {
        background: rgba(20, 30, 60, 0.06);
        padding: 0.5rem 0.6rem;
        border-radius: 8px;
        overflow-x: auto;
        font-size: 0.78rem;
        white-space: pre-wrap;
    }
    .bubble.user-bubble pre {
        background: rgba(255, 255, 255, 0.18);
    }
    .bubble.user-bubble code {
        background: rgba(255, 255, 255, 0.18);
    }
    .bubble code {
        background: rgba(20, 30, 60, 0.06);
        padding: 0.1rem 0.3rem;
        border-radius: 4px;
        font-size: 0.85em;
    }

    .source-chips {
        margin-top: 0.5rem;
    }
    .source-chip {
        display: inline-block;
        background-color: #eef1f8;
        color: #4a72d6;
        border-radius: 999px;
        padding: 0.15rem 0.6rem;
        margin: 0.15rem 0.3rem 0 0;
        font-size: 0.72rem;
        font-family: monospace;
    }

    /* ---- Chat input pill ---- */
    [data-testid="stChatInput"] {
        border-radius: 999px;
    }
    [data-testid="stChatInput"] textarea {
        border-radius: 999px !important;
    }
    [data-testid="stChatInputSubmitButton"] {
        background-color: #4a72d6 !important;
        border-radius: 50% !important;
        color: white !important;
    }
</style>
"""


def get_api_url() -> str | None:
    return os.environ.get("ASKLORE_API_URL") or st.secrets.get("api_url")


def ask(api_url: str, query: str) -> dict:
    response = requests.post(api_url, json={"query": query}, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def render_markdown_lite(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(
        r"```(?:\w+)?\n?(.*?)```", lambda m: f"<pre><code>{m.group(1)}</code></pre>", escaped, flags=re.DOTALL
    )
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped.replace("\n", "<br>")


def source_chips_html(sources: list[dict]) -> str:
    if not sources:
        return ""
    chips = "".join(
        f'<span class="source-chip">{html.escape(s.get("doc_title", s.get("source_key", "unknown")))}</span>'
        for s in sources
    )
    return f'<div class="source-chips">{chips}</div>'


def render_message(role: str, content: str, sources: list[dict] | None = None) -> None:
    body = render_markdown_lite(content)
    if role == "user":
        st.markdown(
            f'<div class="msg-row user"><div class="bubble user-bubble">{body}</div>'
            f'<div class="avatar user-avatar">🧑</div></div>',
            unsafe_allow_html=True,
        )
    else:
        chips = source_chips_html(sources or [])
        st.markdown(
            f'<div class="msg-row assistant"><div class="avatar assistant-avatar">📚</div>'
            f'<div class="bubble assistant-bubble">{body}{chips}</div></div>',
            unsafe_allow_html=True,
        )


def render_history() -> None:
    for message in st.session_state.messages:
        render_message(message["role"], message["content"], message.get("sources"))


def submit_query(api_url: str, query: str) -> None:
    query = query.strip()
    if not query:
        return
    st.session_state.messages.append({"role": "user", "content": query})
    render_message("user", query)

    with st.spinner("AskLore is searching..."):
        try:
            result = ask(api_url, query)
            answer = result.get("answer", "")
            sources = result.get("sources", [])
        except requests.RequestException as exc:
            answer = f"Sorry, something went wrong reaching AskLore: {exc}"
            sources = []

    render_message("assistant", answer, sources)
    st.session_state.messages.append({"role": "assistant", "content": answer, "sources": sources})


def render_topbar() -> None:
    st.markdown(
        """
        <div class="app-topbar">
            <div class="brand">
                <span class="icon">📚</span>
                <div>
                    <div class="title">AskLore</div>
                    <div class="subtitle">Tribal-knowledge assistant</div>
                </div>
            </div>
            <div class="status"><span class="dot"></span>Connected</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar() -> str | None:
    with st.sidebar:
        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown('<div class="sidebar-title">💬 Chat</div>', unsafe_allow_html=True)
        with col2:
            if st.button("New", use_container_width=True):
                st.session_state.messages = []
                st.rerun()

        st.markdown('<div class="sidebar-caption">Sample questions</div>', unsafe_allow_html=True)
        clicked = None
        for question in SAMPLE_QUESTIONS:
            if st.button(question, use_container_width=True, key=f"sample-{question}"):
                clicked = question
        return clicked


def main() -> None:
    st.set_page_config(page_title="AskLore", page_icon="📚", layout="centered")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    api_url = get_api_url()
    render_topbar()

    if not api_url:
        st.error(
            "No API endpoint configured. Set the ASKLORE_API_URL environment variable "
            "to the stack's ApiUrl output (see `aws cloudformation describe-stacks "
            "--stack-name <stack> --query \"Stacks[0].Outputs\"`)."
        )
        st.stop()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    sample_clicked = render_sidebar()
    if sample_clicked:
        st.session_state.pending_query = sample_clicked

    if not st.session_state.messages:
        st.info("👋 Ask a question below, or pick a sample question from the sidebar to get started.")

    render_history()

    pending_query = st.session_state.pop("pending_query", None)
    typed_query = st.chat_input("Type your message...")
    query = pending_query or typed_query

    if query:
        submit_query(api_url, query)


if __name__ == "__main__":
    main()
