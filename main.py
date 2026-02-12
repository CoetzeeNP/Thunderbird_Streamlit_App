import streamlit as st
from datetime import datetime
from ai_strategy import AIManager
from database import save_to_firebase, get_firebase_connection, load_selected_chat, update_previous_feedback
from streamlit_cookies_controller import CookieController

# --- 1. Setup & Configuration ---
st.set_page_config(layout="wide", page_title="Business Planning Assistant")
controller = CookieController()

# Custom CSS
st.markdown("""
    <style>
    div[data-testid="stColumn"]:nth-of-type(1) button { background-color: #28a745 !important; color: white !important; }
    div[data-testid="stColumn"]:nth-of-type(2) button { background-color: #dc3545 !important; color: white !important; }
    </style>
    """, unsafe_allow_html=True)

AI_CONFIG = {
    "active_model": "gemini-3-pro-preview",
    "system_instruction": "You are a helpful Business Planning Assistant. Provide clear, professional, and actionable advice."
}

# --- 2. State Initialization ---
if "session_id" not in st.session_state:
    st.session_state["session_id"] = datetime.now().strftime("%Y%m%d_%H%M%S")
if "messages" not in st.session_state: st.session_state["messages"] = []
if "feedback_pending" not in st.session_state: st.session_state["feedback_pending"] = False
if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
if "current_user" not in st.session_state: st.session_state["current_user"] = None

# --- 3. Persistence & Auth ---
AUTHORIZED_IDS = st.secrets["AUTHORIZED_STUDENT_LIST"]
cached_uid = controller.get('student_auth_id')

if cached_uid and not st.session_state["authenticated"]:
    if cached_uid in AUTHORIZED_IDS:
        st.session_state.update({"authenticated": True, "current_user": cached_uid})


# --- 4. Helper Functions ---
@st.cache_data(ttl=1800)
def get_cached_history_keys(user_id):
    db_ref = get_firebase_connection()
    return db_ref.child("logs").child(str(user_id).replace(".", "_")).get(shallow=True)

# --- Updated Helper for Previews ---
@st.cache_data(ttl=1800)
def get_cached_preview(user_id, session_key):
    try:
        db_ref = get_firebase_connection()
        clean_uid = str(user_id).replace(".", "_")
        # Fetches just the first message (index 0) to keep it lightweight
        return db_ref.child("logs").child(clean_uid).child(session_key).child("transcript").child("0").get()
    except Exception:
        return None


def generate_ai_response(interaction_type):
    """Unified function to get AI response and log to DB."""
    with st.chat_message("assistant"):
        with st.container(border=True):
            st.markdown("**Business Planning Assistant:**")
            ai_manager = AIManager(AI_CONFIG["active_model"])

            full_res = ""
            # Initialize with default, will be updated by the stream
            actual_model = AI_CONFIG["active_model"]
            placeholder = st.empty()

            # The generator yields (token, model_label)
            for chunk, model_label in ai_manager.get_response_stream(
                    st.session_state["messages"],
                    AI_CONFIG["system_instruction"]
            ):
                full_res += chunk
                actual_model = model_label  # This captures the failover to ChatGPT
                placeholder.markdown(full_res + "▌")

            placeholder.markdown(full_res)  # Remove cursor

    # 1. Store the response and the SPECIFIC model used
    st.session_state["messages"].append({"role": "assistant", "content": full_res})
    st.session_state["last_model_used"] = actual_model  # <--- ADD THIS
    st.session_state["feedback_pending"] = True

    # 2. LOGGING: Use actual_model
    save_to_firebase(
        st.session_state["current_user"],
        actual_model,
        st.session_state["messages"],
        interaction_type,
        st.session_state["session_id"]
    )

    # 3. Final Rerun to refresh UI
    st.rerun()

    st.session_state["messages"].append({"role": "assistant", "content": full_res})

    # --- ADD THIS LINE ---
    st.session_state["feedback_pending"] = True

    # Logs the AI's response
    save_to_firebase(
        st.session_state["current_user"], AI_CONFIG["active_model"],
        st.session_state["messages"], interaction_type, st.session_state["session_id"]
    )
    st.rerun()


def handle_feedback(understood: bool):
    user_id = st.session_state["current_user"]
    session_id = st.session_state["session_id"]
    # Get the model that actually sent the last message
    model_to_log = st.session_state.get("last_model_used", AI_CONFIG["active_model"])

    if understood:
        save_to_firebase(user_id, model_to_log, st.session_state["messages"],
                         "GENERATED_RESPONSE", session_id, feedback_value=True)
        st.session_state["feedback_pending"] = False
    else:
        clarification_text = "I don't understand the previous explanation. Please break it down further."
        st.session_state["messages"].append({"role": "user", "content": clarification_text})

        update_previous_feedback(user_id, session_id, st.session_state["messages"], False)

        save_to_firebase(
            user_id,
            model_to_log, # Use the correct model here too
            st.session_state["messages"],
            "CLARIFICATION_REQUEST",
            session_id,
            feedback_value=None
        )

        st.session_state["trigger_clarification"] = True
        st.session_state["feedback_pending"] = False


# --- 5. Sidebar ---
with st.sidebar:
    st.image("icdf.png")
    if not st.session_state["authenticated"]:
        u_id = st.text_input("Enter Student ID", type="password")
        if st.button("Login", use_container_width=True) and u_id in AUTHORIZED_IDS:
            controller.set('student_auth_id', u_id)
            st.session_state.update({"authenticated": True, "current_user": u_id})
            st.rerun()
    else:
        st.write(f"**Logged in as:** {st.session_state['current_user']}")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Logout", use_container_width=True):
                st.cache_data.clear()
                st.session_state.clear()
                st.rerun()
        with col2:
            st.link_button("Feedback",
                           "https://forms.office.com/Pages/ResponsePage.aspx?id=...",
                           use_container_width=True)

        st.divider()
        st.subheader("Chat History")
        all_logs = get_cached_history_keys(st.session_state['current_user'])

        if all_logs:
            # Create a mapping of Pretty Date -> DB Key
            display_options = {}
            for k in sorted(all_logs.keys(), reverse=True):
                try:
                    dt_obj = datetime.strptime(k, "%Y%m%d_%H%M%S")
                    clean_date = dt_obj.strftime("%b %d, %Y - %I:%M %p")
                except:
                    clean_date = k
                display_options[clean_date] = k

            sel_display = st.selectbox("Select a previous session:", options=list(display_options.keys()))
            sel_key = display_options[sel_display]

            # Re-added Preview Logic
            preview_msg = get_cached_preview(st.session_state['current_user'], sel_key)

            with st.expander("🔍 Preview Session"):
                if preview_msg:
                    role = "User" if preview_msg.get("role") == "user" else "Assistant"
                    content = preview_msg.get("content", "No content available")
                    st.markdown(f"**{role}:** {content[:100]}...")
                else:
                    st.info("No preview available.")

            if st.button("🔄 Load & Continue", type="primary", use_container_width=True):
                load_selected_chat(st.session_state['current_user'], sel_key)
                st.rerun()

        if st.button("New Chat", use_container_width=True):
            st.session_state.update(
                {"messages": [], "session_id": datetime.now().strftime("%Y%m%d_%H%M%S"), "feedback_pending": False})
            st.rerun()

# --- 6. Main Chat UI ---
st.image("combined_logo.jpg")
st.title("Business Planning Assistant")

if not st.session_state["authenticated"]:
    st.warning("Please login via the sidebar.")
    st.stop()

# 1. Display Chat History
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        with st.container(border=True):
            label = st.session_state["current_user"] if msg["role"] == "user" else "Assistant"
            st.markdown(f"**{label}:**\n\n{msg['content']}")

if st.session_state.get("trigger_clarification"):
    st.session_state["trigger_clarification"] = False
    # This explicitly logs the NEXT AI message as a CLARIFICATION_RESPONSE
    generate_ai_response("CLARIFICATION_RESPONSE")

# 3. Chat Input
input_msg = "Please provide feedback..." if st.session_state["feedback_pending"] else "Ask about your business plan..."
if prompt := st.chat_input(input_msg, disabled=st.session_state["feedback_pending"]):
    st.session_state["messages"].append({"role": "user", "content": prompt})

    # Immediately log the user's manual input
    save_to_firebase(
        st.session_state["current_user"],
        AI_CONFIG["active_model"],
        st.session_state["messages"],
        "USER_PROMPT",
        st.session_state["session_id"]
    )
    st.rerun()

# 4. Feedback UI
if st.session_state["feedback_pending"]:
    st.divider()
    st.info("Did you understand the explanation?")
    c1, c2 = st.columns(2)
    c1.button("I understand!", on_click=handle_feedback, args=(True,), use_container_width=True)
    c2.button("I need more help!", on_click=handle_feedback, args=(False,), use_container_width=True)

# 5. Generate Standard Response
# This only fires if the last message is from a user and it wasn't a clarification trigger
if (
    st.session_state["messages"]
    and st.session_state["messages"][-1]["role"] == "user"
    and not st.session_state["feedback_pending"]
    and not st.session_state.get("trigger_clarification") # Add this check
):
    generate_ai_response("GENERATED_RESPONSE")