import streamlit as st
import requests
import uuid
import time

# page config
st.set_page_config(
    page_title="Medical Health Assistant",
    page_icon="🏥",
    layout="centered"
)

API_BASE_URL = st.secrets["API_BASE_URL"]

# initialize session state
if 'chat_ready_at' not in st.session_state:
    st.session_state.chat_ready_at = 0
if 'patient_id' not in st.session_state:
    st.session_state.patient_id = None
if 'patient_name' not in st.session_state:
    st.session_state.patient_name = None
if 'patient_email' not in st.session_state:
    st.session_state.patient_email = None
if 'summary' not in st.session_state:
    st.session_state.summary = None
if 'critical_values' not in st.session_state:
    st.session_state.critical_values = None
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'report_uploaded' not in st.session_state:
    st.session_state.report_uploaded = False

# ── CSS ──
st.markdown("""
<style>
    .main { background-color: #f0f4f8; }
    .stButton>button {
        background-color: #1a73e8;
        color: white;
        border-radius: 8px;
        padding: 0.5rem 2rem;
        border: none;
    }
    .critical-high {
        background-color: #ff4444;
        color: white;
        padding: 4px 12px;
        border-radius: 12px;
        margin: 4px;
        display: inline-block;
    }
    .critical-low {
        background-color: #ff9800;
        color: white;
        padding: 4px 12px;
        border-radius: 12px;
        margin: 4px;
        display: inline-block;
    }
    .chat-message-user {
        background-color: #1a73e8;
        color: white;
        padding: 10px 15px;
        border-radius: 15px 15px 0px 15px;
        margin: 5px 0;
        text-align: right;
    }
    .chat-message-bot {
        background-color: #ffffff;
        color: #333;
        padding: 10px 15px;
        border-radius: 15px 15px 15px 0px;
        margin: 5px 0;
        border: 1px solid #e0e0e0;
    }
</style>
""", unsafe_allow_html=True)


# ── PAGE 1 — LOGIN ──
def show_login():
    st.title("🏥 Medical Health Assistant")
    st.subheader("Your personal medical report companion")
    st.markdown("---")

    with st.form("login_form"):
        name = st.text_input("Full Name", placeholder="Enter your full name")
        email = st.text_input("Email Address", placeholder="Enter your email")
        submitted = st.form_submit_button("Get Started →")

        if submitted:
            if not name or not email:
                st.error("Please fill in all fields.")
            else:
                # Step 1: check if this email already has a patient_id
                response = requests.get(f"{API_BASE_URL}/patient", params={"email": email})

                if response.status_code == 200:
                    patient_id = response.json()["patient_id"]
                else:
                    # Step 2: no existing patient — create one
                    create_response = requests.post(
                        f"{API_BASE_URL}/patient",
                        json={"email": email}
                    )
                    patient_id = create_response.json()["patient_id"]

                st.session_state.patient_id = patient_id
                st.session_state.patient_name = name
                st.session_state.patient_email = email
                st.rerun()

# ── PAGE 2 — UPLOAD ──
def show_upload():
    st.title(f"👋 Hello, {st.session_state.patient_name}!")
    st.subheader("Upload your medical report")
    st.markdown(f"**Your Patient ID:** `{st.session_state.patient_id}`")
    st.markdown("---")

    uploaded_file = st.file_uploader(
        "Choose your medical report (PDF only)",
        type=['pdf']
    )

    if uploaded_file:
        if st.button("Upload & Analyze Report 🔍"):
            with st.spinner("Uploading your report..."):
                # step 1 — get presigned URL
                try:
                    response = requests.post(
                        f"{API_BASE_URL}/upload",
                        json={
                            "patient_id": st.session_state.patient_id,
                            "file_name": uploaded_file.name
                        }
                    )
                    data = response.json()
                    presigned_url = data['upload_url']
                    s3_key = data['s3_key']

                except Exception as e:
                    st.error(f"Failed to get upload URL: {str(e)}")
                    return

            with st.spinner("Uploading PDF to secure storage..."):
                # step 2 — upload directly to S3
                try:
                    upload_response = requests.put(
                        presigned_url,
                        data=uploaded_file.getvalue(),
                        headers={'Content-Type': 'application/pdf'}
                    )
                    if upload_response.status_code != 200:
                        st.error("Upload failed. Please try again.")
                        return
                except Exception as e:
                    st.error(f"Upload error: {str(e)}")
                    return

            with st.spinner("Analyzing your report with AI... This may take 30-60 seconds..."):
                # step 3 — poll for summary
                report_date = s3_key.split('/')[2]
                filename = s3_key.split('/')[3]
                sort_key = f"{report_date}#{filename}"

                max_attempts = 20
                for attempt in range(max_attempts):
                    time.sleep(5)
                    try:
                        summary_response = requests.get(
                            f"{API_BASE_URL}/summary",
                            params={
                                "patient_id": st.session_state.patient_id,
                                "report_date": sort_key
                            }
                        )
                        

                        if summary_response.status_code == 200:
                            summary_data = summary_response.json()
                            if summary_data.get('summary'):
                                st.session_state.summary = summary_data['summary']
                                st.session_state.critical_values = summary_data.get('critical_values', [])
                                st.session_state.chat_ready_at = time.time() + 45
                                st.session_state.report_uploaded = True
                                st.rerun()
                    except Exception as e:
                        pass
                        #st.write(f"DEBUG attempt {attempt}: EXCEPTION: {str(e)}")

                st.warning("Analysis is taking longer than expected. Please refresh in a minute.")


# ── PAGE 3 — SUMMARY + CHAT ──
def show_summary_and_chat():
    st.title(f"🏥 {st.session_state.patient_name}'s Report")
    st.markdown(f"**Patient ID:** `{st.session_state.patient_id}`")
    st.markdown("---")

    # summary section
    st.subheader("📋 AI Summary")
    st.markdown(st.session_state.summary)

    # critical values section
    if st.session_state.critical_values:
        st.markdown("---")
        st.subheader("⚠️ Critical Values Detected")
        for cv in st.session_state.critical_values:
            status_class = "critical-high" if cv['status'] == 'HIGH' else "critical-low"
            st.markdown(
                f'<span class="{status_class}">'
                f'{cv["parameter"]}: {cv["value"]} {cv["unit"]} ({cv["status"]})'
                f'</span>',
                unsafe_allow_html=True
            )
    else:
        st.success("✅ No critical values detected in this report.")

    st.markdown("---")

    # upload another report
    if st.button("Upload Another Report"):
        st.session_state.summary = None
        st.session_state.critical_values = None
        st.session_state.report_uploaded = False
        st.session_state.chat_history = []
        st.rerun()

    # chat section
    st.markdown("---")
    st.subheader("💬 Ask About Your Report")

    seconds_left = st.session_state.chat_ready_at - time.time()

    if seconds_left > 0:
        st.info(f"🔄 Indexing your report for chat... ready in about {int(seconds_left)} seconds")
        time.sleep(2)
        st.rerun()
    else:
        st.caption("Ask me anything about your medical report in simple language.")

        # display chat history
        for message in st.session_state.chat_history:
            if message['role'] == 'user':
                st.markdown(
                    f'<div class="chat-message-user">{message["content"]}</div>',
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    f'<div class="chat-message-bot">{message["content"]}</div>',
                    unsafe_allow_html=True
                )
                
                sources = message.get('sources', [])
                if sources:
                    with st.expander("📄 View Source"):
                        for src in sources:
                            st.write(src)

        # chat input
        with st.form("chat_form", clear_on_submit=True):
            question = st.text_input(
                "Your question",
                placeholder="e.g. What does low hemoglobin mean?"
            )
            ask_button = st.form_submit_button("Ask →")

            if ask_button and question:
                st.session_state.chat_history.append({
                    'role': 'user',
                    'content': question
                })

                
                with st.spinner("Finding answer from your report..."):
                    try:
                        chat_response = requests.post(
                            f"{API_BASE_URL}/chat",
                            json={
                                "question": question,
                                "patient_id": st.session_state.patient_id
                            }
                        )
                        response_json = chat_response.json()
                        answer = response_json.get('answer', 'Sorry, I could not find an answer.')
                        sources = response_json.get('sources', [])  
                    except Exception as e:
                        answer = f"Error: {str(e)}"
                        sources = []

                st.session_state.chat_history.append({
                    'role': 'assistant',
                    'content': answer,
                    'sources': sources  
                })
                st.rerun()

# ── MAIN ROUTER ──
def main():
    # decide which page to show
    if st.session_state.patient_id is None:
        show_login()
    elif st.session_state.report_uploaded:
        show_summary_and_chat()
    else:
        show_upload()


if __name__ == "__main__":
    main()