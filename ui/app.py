"""
Streamlit chat UI for AskLore. Calls the deployed POST /query endpoint
(RetrievalLambda: Bedrock Retrieve + Gemini generation) and renders a
chat-style conversation with cited sources.

Run: streamlit run ui/app.py
Requires ASKLORE_API_URL env var (or the "api_url" field in
.streamlit/secrets.toml) pointing at the stack's ApiUrl output.
"""

import os

import requests
import streamlit as st

SAMPLE_QUESTIONS = [
    "How do I trigger a manual failover for the primary RDS cluster?",
    "What's the escalation path when on-call can't resolve an incident?",
    "How do I roll back a bad deployment?",
    "What steps do I take when Lambda functions are timing out?",
    "How do I respond to a CloudWatch alarm firing?",
]

REQUEST_TIMEOUT_SECONDS = 30


def get_api_url() -> str | None:
    return os.environ.get("ASKLORE_API_URL") or st.secrets.get("api_url")


def ask(api_url: str, query: str) -> dict:
    response = requests.post(api_url, json={"query": query}, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def render_sources(sources: list[dict]) -> None:
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})"):
        for source in sources:
            st.markdown(f"- `{source.get('doc_title', source.get('source_key', 'unknown'))}`")


def render_history() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                render_sources(message.get("sources", []))


def submit_query(api_url: str, query: str) -> None:
    query = query.strip()
    if not query:
        return
    st.session_state.messages.append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        with st.spinner("Searching AskLore..."):
            try:
                result = ask(api_url, query)
                answer = result.get("answer", "")
                sources = result.get("sources", [])
            except requests.RequestException as exc:
                answer = f"Sorry, something went wrong reaching AskLore: {exc}"
                sources = []
        st.markdown(answer)
        render_sources(sources)

    st.session_state.messages.append({"role": "assistant", "content": answer, "sources": sources})


def main() -> None:
    st.set_page_config(page_title="AskLore", page_icon="📚", layout="centered")
    st.title("📚 AskLore")
    st.caption("Ask questions grounded in your team's tribal knowledge.")

    api_url = get_api_url()
    if not api_url:
        st.error(
            "No API endpoint configured. Set the ASKLORE_API_URL environment variable "
            "to the stack's ApiUrl output (see `aws cloudformation describe-stacks "
            "--stack-name <stack> --query \"Stacks[0].Outputs\"`)."
        )
        st.stop()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    with st.sidebar:
        st.subheader("Sample questions")
        for question in SAMPLE_QUESTIONS:
            if st.button(question, use_container_width=True):
                st.session_state.pending_query = question
        st.divider()
        if st.button("Clear conversation", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

    render_history()

    pending_query = st.session_state.pop("pending_query", None)
    typed_query = st.chat_input("Ask AskLore a question...")
    query = pending_query or typed_query

    if query:
        submit_query(api_url, query)


if __name__ == "__main__":
    main()
