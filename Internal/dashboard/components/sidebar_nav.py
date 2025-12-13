# dashboard/components/sidebar_nav.py
import streamlit as st

def render_sidebar_nav():
    # Hide Streamlit default multipage nav
    st.markdown(
        """
        <style>
        section[data-testid="stSidebarNav"] {display: none !important;}
        div[data-testid="stSidebarNav"] {display: none !important;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Custom sidebar
    with st.sidebar:
        st.markdown(
            """
            <style>
            .yt-sidebar-title {
                font-size: 1.15rem;
                font-weight: 700;
                margin-bottom: -2px;
            }
            .yt-sidebar-sub {
                font-size: 0.78rem;
                color: #6b7280;
                margin-bottom: 0.8rem;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            '<div class="yt-sidebar-title">📺 YT AutoScanner</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="yt-sidebar-sub">24h tracking · ML virality · Ad-friendly</div>',
            unsafe_allow_html=True,
        )

        st.markdown("---")

        # Main navigation
        st.page_link("app.py", label="Overview (Home)", icon="🏠")
        st.page_link("pages/01_Overview.py", label="System KPIs", icon="📊")
        st.page_link("pages/03_filter.py", label="Viral Filter", icon="🚀")
        st.page_link("pages/99_Settings.py", label="Settings & Workers", icon="⚙️")
