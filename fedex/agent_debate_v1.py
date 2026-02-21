"""
Multi-Agent Debate Simulator
FedEx Day Project

A Streamlit app where AI agents with distinct personalities debate any topic.
Watch them argue, challenge each other, and reach (or fail to reach) consensus.
"""

import streamlit as st
import openai
import time
from dataclasses import dataclass
from typing import Generator

# Configure OpenAI client
client = openai.OpenAI()

@dataclass
class Agent:
    name: str
    emoji: str
    color: str
    personality: str
    system_prompt: str

# Define our debate agents with distinct personalities
AGENTS = {
    "optimist": Agent(
        name="The Optimist",
        emoji="🌟",
        color="#4CAF50",
        personality="Hopeful, enthusiastic, sees opportunity everywhere",
        system_prompt="""You are The Optimist in a debate. You always see the bright side and potential benefits.
You believe in human ingenuity and positive outcomes. You're enthusiastic but not naive - you acknowledge
challenges while focusing on solutions and opportunities. Keep responses to 2-3 paragraphs. Be persuasive
and inspiring. Directly engage with and respond to other debaters' points."""
    ),
    "skeptic": Agent(
        name="The Skeptic",
        emoji="🔍",
        color="#F44336",
        personality="Critical, evidence-driven, questions assumptions",
        system_prompt="""You are The Skeptic in a debate. You question assumptions and demand evidence.
You're not negative - you're rigorous. You poke holes in weak arguments and highlight risks others miss.
You value data over intuition. Keep responses to 2-3 paragraphs. Be incisive but fair. Directly engage
with and challenge other debaters' points."""
    ),
    "pragmatist": Agent(
        name="The Pragmatist",
        emoji="⚙️",
        color="#2196F3",
        personality="Practical, implementation-focused, results-oriented",
        system_prompt="""You are The Pragmatist in a debate. You focus on what actually works in practice.
You care about implementation, costs, timelines, and real-world constraints. Theory is nice but you want
to know HOW things get done. Keep responses to 2-3 paragraphs. Be grounded and specific. Directly engage
with other debaters and bring the conversation back to practical realities."""
    ),
    "wildcard": Agent(
        name="The Wildcard",
        emoji="🃏",
        color="#9C27B0",
        personality="Unconventional, creative, challenges the frame",
        system_prompt="""You are The Wildcard in a debate. You think outside the box and challenge the very
framing of discussions. You bring unexpected perspectives, historical analogies, and creative alternatives
that others miss. You're not contrarian for its own sake - you genuinely see angles others don't.
Keep responses to 2-3 paragraphs. Be surprising but insightful. Reframe the debate in unexpected ways."""
    ),
}

MODERATOR_PROMPT = """You are the Debate Moderator. Your job is to:
1. Summarize the key arguments made by each debater
2. Identify points of agreement and disagreement
3. Highlight the strongest arguments from each side
4. Provide a balanced synthesis (NOT declaring a winner, but showing what was learned)
5. Suggest what questions remain unresolved

Be fair, insightful, and help the audience understand what they just witnessed.
Keep your summary to 3-4 paragraphs."""

CONSENSUS_PROMPT_TEMPLATE = """You are {agent_name} in the consensus-building phase of a debate.

Your personality: {personality}

The debate topic was: {topic}

Here is what was discussed:
{history}

Now it's time to find common ground. You must:
1. Acknowledge 1-2 valid points from OTHER debaters (be specific about who and what)
2. State what you're willing to concede or compromise on
3. Identify the core principle you still hold that others might accept
4. Propose a synthesis position that incorporates multiple viewpoints

Stay in character but be genuinely collaborative. The goal is progress, not victory.
Keep your response to 2 paragraphs."""

FINAL_VERDICT_PROMPT = """You are the Debate Moderator delivering the final verdict after the consensus round.

The debate topic was: {topic}

Here is the full debate:
{debate_history}

Here are the consensus statements from each debater:
{consensus_history}

Your job is to:
1. Assess the level of consensus reached (Strong Consensus / Partial Consensus / Productive Disagreement)
2. Articulate the synthesized position that emerged (what do they mostly agree on?)
3. Note any remaining points of genuine disagreement
4. Deliver a final "verdict" - not who won, but what conclusion or actionable insight emerged
5. Rate the consensus level as a percentage (0-100%)

Format your response with clear sections. Be decisive and provide a clear takeaway.
End with a memorable one-sentence conclusion."""


def stream_agent_response(agent: Agent, topic: str, debate_history: list[dict]) -> Generator:
    """Stream a response from an agent."""

    messages = [
        {"role": "system", "content": agent.system_prompt},
        {"role": "user", "content": f"The debate topic is: {topic}"},
    ]

    # Add debate history for context
    for entry in debate_history:
        messages.append({
            "role": "user",
            "content": f"{entry['agent']} said: {entry['content']}"
        })

    if debate_history:
        messages.append({
            "role": "user",
            "content": "Now it's your turn. Respond to the previous arguments and advance your position."
        })
    else:
        messages.append({
            "role": "user",
            "content": "You're giving the opening statement. Present your initial position on this topic."
        })

    stream = client.chat.completions.create(
        model="gpt-5.2",
        messages=messages,
        stream=True,
        max_tokens=500,
        temperature=0.9,
    )

    for chunk in stream:
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def stream_moderator_summary(topic: str, debate_history: list[dict]) -> Generator:
    """Stream the moderator's summary."""

    history_text = "\n\n".join([
        f"{entry['agent']}: {entry['content']}"
        for entry in debate_history
    ])

    messages = [
        {"role": "system", "content": MODERATOR_PROMPT},
        {"role": "user", "content": f"The debate topic was: {topic}\n\nHere is the full debate:\n\n{history_text}\n\nPlease provide your summary and synthesis."}
    ]

    stream = client.chat.completions.create(
        model="gpt-5.2",
        messages=messages,
        stream=True,
        max_tokens=600,
        temperature=0.7,
    )

    for chunk in stream:
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def stream_consensus_response(agent: Agent, topic: str, debate_history: list[dict]) -> Generator:
    """Stream an agent's consensus-building response."""

    history_text = "\n\n".join([
        f"{entry['agent']}: {entry['content']}"
        for entry in debate_history
    ])

    prompt = CONSENSUS_PROMPT_TEMPLATE.format(
        agent_name=agent.name,
        personality=agent.personality,
        topic=topic,
        history=history_text
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Please provide your consensus statement, finding common ground with other debaters."}
    ]

    stream = client.chat.completions.create(
        model="gpt-5.2",
        messages=messages,
        stream=True,
        max_tokens=400,
        temperature=0.7,
    )

    for chunk in stream:
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def stream_final_verdict(topic: str, debate_history: list[dict], consensus_history: list[dict]) -> Generator:
    """Stream the final verdict after consensus round."""

    debate_text = "\n\n".join([
        f"{entry['agent']}: {entry['content']}"
        for entry in debate_history
    ])

    consensus_text = "\n\n".join([
        f"{entry['agent']}: {entry['content']}"
        for entry in consensus_history
    ])

    prompt = FINAL_VERDICT_PROMPT.format(
        topic=topic,
        debate_history=debate_text,
        consensus_history=consensus_text
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Deliver your final verdict on this debate."}
    ]

    stream = client.chat.completions.create(
        model="gpt-5.2",
        messages=messages,
        stream=True,
        max_tokens=700,
        temperature=0.7,
    )

    for chunk in stream:
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def render_agent_card(agent: Agent):
    """Render an agent's profile card."""
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, {agent.color}22, {agent.color}11);
                border-left: 4px solid {agent.color};
                padding: 10px 15px;
                border-radius: 8px;
                margin: 5px 0;">
        <span style="font-size: 1.5em;">{agent.emoji}</span>
        <strong style="color: {agent.color};">{agent.name}</strong><br>
        <small style="color: #666;">{agent.personality}</small>
    </div>
    """, unsafe_allow_html=True)


def render_debate_entry(agent: Agent, content: str, round_num: int = None):
    """Render a debate entry with styling."""
    round_badge = f"<small style='background: {agent.color}33; color: {agent.color}; padding: 2px 8px; border-radius: 10px; margin-left: 10px;'>Round {round_num}</small>" if round_num else ""

    st.markdown(f"""
    <div style="background: linear-gradient(135deg, {agent.color}20, {agent.color}10);
                border-left: 4px solid {agent.color};
                padding: 15px 20px;
                border-radius: 8px;
                margin: 15px 0;">
        <div style="margin-bottom: 10px;">
            <span style="font-size: 1.3em;">{agent.emoji}</span>
            <strong style="color: {agent.color}; font-size: 1.1em;">{agent.name}</strong>
            {round_badge}
        </div>
        <div style="color: #333; line-height: 1.6;">
            {content}
        </div>
    </div>
    """, unsafe_allow_html=True)


def main():
    st.set_page_config(
        page_title="Multi-Agent Debate Simulator",
        page_icon="🎭",
        layout="wide",
    )

    # Custom CSS
    st.markdown("""
    <style>
    .stApp {
        background-color: #FFFFFF;
    }
    .main-header {
        text-align: center;
        padding: 20px 0;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 3em;
        font-weight: bold;
    }
    .subtitle {
        text-align: center;
        color: #555;
        font-size: 1.2em;
        margin-bottom: 30px;
    }
    .moderator-box {
        background: linear-gradient(135deg, #FFD70033, #FFD70022);
        border-left: 4px solid #FFD700;
        padding: 20px;
        border-radius: 8px;
        margin: 20px 0;
    }
    </style>
    """, unsafe_allow_html=True)

    # Header
    st.markdown('<div class="main-header">🎭 Multi-Agent Debate Simulator</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Watch AI agents debate, challenge each other, and reach consensus</div>', unsafe_allow_html=True)

    # Sidebar - Agent profiles
    with st.sidebar:
        st.header("🎪 The Debaters")
        for agent in AGENTS.values():
            render_agent_card(agent)

        st.markdown("---")
        st.header("⚙️ Settings")
        num_rounds = st.slider("Debate Rounds", 1, 5, 2, help="More rounds = deeper debate")

        st.markdown("---")
        st.markdown("### 📋 Debate Phases")
        st.markdown("""
        1. **Opening Rounds** - Agents present positions
        2. **Moderator Summary** - Key points identified
        3. **Consensus Round** - Finding common ground
        4. **Final Verdict** - Conclusion reached
        """)

        st.markdown("---")
        st.markdown("### 💡 Topic Ideas")
        st.markdown("""
        - Should AI be open-sourced?
        - Is remote work better than office?
        - Should we colonize Mars?
        - Is social media net positive?
        - Should coding be taught in schools?
        - Are LLMs actually intelligent?
        """)

    # Main area
    col1, col2 = st.columns([3, 1])

    with col1:
        topic = st.text_input(
            "🎯 Enter a debate topic",
            placeholder="e.g., Should AI systems be required to explain their decisions?",
            help="Enter any topic you want the agents to debate"
        )

    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        start_debate = st.button("🚀 Start Debate", type="primary", use_container_width=True)

    # Initialize session state
    if "debate_history" not in st.session_state:
        st.session_state.debate_history = []
    if "consensus_history" not in st.session_state:
        st.session_state.consensus_history = []
    if "debate_complete" not in st.session_state:
        st.session_state.debate_complete = False

    # Run the debate
    if start_debate and topic:
        st.session_state.debate_history = []
        st.session_state.consensus_history = []
        st.session_state.debate_complete = False

        debate_container = st.container()

        with debate_container:
            st.markdown("---")
            st.markdown("## 🎬 The Debate Begins!")
            st.markdown(f"**Topic:** *{topic}*")
            st.markdown("---")

            agent_order = ["optimist", "skeptic", "pragmatist", "wildcard"]

            # === DEBATE ROUNDS ===
            for round_num in range(1, num_rounds + 1):
                st.markdown(f"### 📢 Round {round_num}")

                for agent_key in agent_order:
                    agent = AGENTS[agent_key]

                    # Create placeholder for streaming
                    with st.container():
                        header_placeholder = st.empty()
                        header_placeholder.markdown(f"""
                        <div style="margin: 15px 0 5px 0;">
                            <span style="font-size: 1.3em;">{agent.emoji}</span>
                            <strong style="color: {agent.color}; font-size: 1.1em;">{agent.name}</strong>
                            <small style='background: {agent.color}33; color: {agent.color}; padding: 2px 8px; border-radius: 10px; margin-left: 10px;'>Round {round_num}</small>
                        </div>
                        """, unsafe_allow_html=True)

                        response_placeholder = st.empty()

                        # Stream the response
                        full_response = ""
                        for chunk in stream_agent_response(agent, topic, st.session_state.debate_history):
                            full_response += chunk
                            response_placeholder.markdown(f"""
                            <div style="background: linear-gradient(135deg, {agent.color}20, {agent.color}10);
                                        border-left: 4px solid {agent.color};
                                        padding: 15px 20px;
                                        border-radius: 8px;">
                                <div style="color: #333; line-height: 1.6;">
                                    {full_response}
                                </div>
                            </div>
                            """, unsafe_allow_html=True)

                        # Store in history
                        st.session_state.debate_history.append({
                            "agent": agent.name,
                            "agent_key": agent_key,
                            "content": full_response,
                            "round": round_num
                        })

                        time.sleep(0.3)  # Brief pause between speakers

                if round_num < num_rounds:
                    st.markdown("---")

            # === MODERATOR SUMMARY ===
            st.markdown("---")
            st.markdown("## 🎖️ Moderator's Summary")

            moderator_placeholder = st.empty()
            full_summary = ""
            for chunk in stream_moderator_summary(topic, st.session_state.debate_history):
                full_summary += chunk
                moderator_placeholder.markdown(f"""
                <div class="moderator-box">
                    <div style="margin-bottom: 10px;">
                        <span style="font-size: 1.3em;">⚖️</span>
                        <strong style="color: #B8860B; font-size: 1.1em;">The Moderator</strong>
                    </div>
                    <div style="color: #333; line-height: 1.6;">
                        {full_summary}
                    </div>
                </div>
                """, unsafe_allow_html=True)

            # === CONSENSUS ROUND ===
            st.markdown("---")
            st.markdown("## 🤝 Consensus Round")
            st.markdown("*The debaters now attempt to find common ground...*")

            for agent_key in agent_order:
                agent = AGENTS[agent_key]

                with st.container():
                    header_placeholder = st.empty()
                    header_placeholder.markdown(f"""
                    <div style="margin: 15px 0 5px 0;">
                        <span style="font-size: 1.3em;">{agent.emoji}</span>
                        <strong style="color: {agent.color}; font-size: 1.1em;">{agent.name}</strong>
                        <small style='background: #17a2b833; color: #17a2b8; padding: 2px 8px; border-radius: 10px; margin-left: 10px;'>Finding Common Ground</small>
                    </div>
                    """, unsafe_allow_html=True)

                    response_placeholder = st.empty()

                    full_response = ""
                    for chunk in stream_consensus_response(agent, topic, st.session_state.debate_history):
                        full_response += chunk
                        response_placeholder.markdown(f"""
                        <div style="background: linear-gradient(135deg, {agent.color}20, {agent.color}10);
                                    border-left: 4px solid {agent.color};
                                    border-right: 4px solid #17a2b8;
                                    padding: 15px 20px;
                                    border-radius: 8px;">
                            <div style="color: #333; line-height: 1.6;">
                                {full_response}
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                    st.session_state.consensus_history.append({
                        "agent": agent.name,
                        "agent_key": agent_key,
                        "content": full_response
                    })

                    time.sleep(0.3)

            # === FINAL VERDICT ===
            st.markdown("---")
            st.markdown("## ⚖️ Final Verdict")

            verdict_placeholder = st.empty()
            full_verdict = ""
            for chunk in stream_final_verdict(topic, st.session_state.debate_history, st.session_state.consensus_history):
                full_verdict += chunk
                verdict_placeholder.markdown(f"""
                <div style="background: linear-gradient(135deg, #FFD70033, #FFA50022);
                            border: 2px solid #DAA520;
                            padding: 25px;
                            border-radius: 12px;
                            margin: 20px 0;">
                    <div style="margin-bottom: 15px;">
                        <span style="font-size: 1.5em;">🏛️</span>
                        <strong style="color: #B8860B; font-size: 1.3em;">Final Verdict</strong>
                    </div>
                    <div style="color: #333; line-height: 1.8;">
                        {full_verdict}
                    </div>
                </div>
                """, unsafe_allow_html=True)

            st.session_state.debate_complete = True

            st.markdown("---")
            st.success("🎬 Debate Complete! The agents have reached their conclusion.")

            # Fun metrics
            col1, col2, col3, col4 = st.columns(4)
            total_words = sum(len(entry["content"].split()) for entry in st.session_state.debate_history)
            consensus_words = sum(len(entry["content"].split()) for entry in st.session_state.consensus_history)

            with col1:
                st.metric("Debate Exchanges", len(st.session_state.debate_history))
            with col2:
                st.metric("Words Debated", total_words)
            with col3:
                st.metric("Consensus Statements", len(st.session_state.consensus_history))
            with col4:
                st.metric("Total Words", total_words + consensus_words)

    elif not topic and start_debate:
        st.warning("Please enter a debate topic first!")

    # Footer
    st.markdown("---")
    st.markdown("""
    <div style="text-align: center; color: #888; padding: 20px;">
        <small>🚀 FedEx Day Project | Multi-Agent Debate Simulator with Consensus Building | Powered by OpenAI gpt-5.2</small>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
