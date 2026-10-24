from __future__ import annotations

import os
import time

import httpx
import streamlit as st

st.set_page_config(page_title="RepoAgent", page_icon="🤖", layout="wide")
st.title("RepoAgent")
st.caption("Understand → Plan → Code → Test → Correct → Review → Create PR")

api_url = st.sidebar.text_input(
    "API URL", os.getenv("REPOAGENT_API_URL", "http://localhost:8000")
).rstrip("/")

with st.form("new-run"):
    repository = st.text_input("Repository path or HTTPS URL")
    requirement = st.text_area("Requirement", height=140)
    base_branch = st.text_input("Base branch", "main")
    col1, col2 = st.columns(2)
    create_pr = col1.checkbox("Create pull request", value=True)
    draft_pr = col2.checkbox("Create as draft", value=True)
    submitted = st.form_submit_button("Start RepoAgent", type="primary")

if submitted:
    try:
        response = httpx.post(
            f"{api_url}/runs",
            json={
                "repository": repository,
                "requirement": requirement,
                "base_branch": base_branch,
                "create_pull_request": create_pr,
                "draft_pull_request": draft_pr,
            },
            timeout=30,
        )
        response.raise_for_status()
        st.session_state["run_id"] = response.json()["run_id"]
    except Exception as error:
        st.error(str(error))

run_id = st.text_input("Run ID", value=st.session_state.get("run_id", ""))
auto_refresh = st.checkbox("Auto-refresh active run", value=False)

if run_id:
    try:
        state = httpx.get(f"{api_url}/runs/{run_id}", timeout=10).json()
        events = httpx.get(f"{api_url}/runs/{run_id}/events", timeout=10).json()
        status = state["status"]
        st.subheader(f"Status: {status}")
        metrics = state.get("metrics", {})
        columns = st.columns(4)
        columns[0].metric("Corrections", state.get("correction_count", 0))
        columns[1].metric("Agent calls", metrics.get("agent_calls", 0))
        columns[2].metric("Tool calls", metrics.get("tool_calls", 0))
        columns[3].metric("Duration", f"{metrics.get('duration_seconds') or 0:.1f}s")

        if state.get("pull_request", {}).get("url"):
            st.link_button("Open pull request", state["pull_request"]["url"])
        if state.get("failure_reason"):
            st.warning(state["failure_reason"])

        tab1, tab2, tab3, tab4 = st.tabs(["Timeline", "Plan", "Validation", "State"])
        with tab1:
            for event in reversed(events):
                st.write(f"`{event['timestamp']}` **{event['component']}** — {event['message']}")
        with tab2:
            st.json(state.get("plan") or {})
        with tab3:
            st.json(state.get("validation_reports") or [])
        with tab4:
            st.json(state)

        if auto_refresh and status not in {
            "completed",
            "failed",
            "blocked",
            "needs_human",
            "cancelled",
        }:
            time.sleep(2)
            st.rerun()
    except Exception as error:
        st.error(f"Unable to load run: {error}")
