"""
Multi-Agent Debate Simulator
FedEx Day Project

A Streamlit app where AI agents with distinct personalities debate any topic.
Watch them argue, challenge each other, and reach (or fail to reach) consensus.
Features: Dynamic speaker selection, web search capability, model selection.
"""

import streamlit as st
import openai
import time
import json
import re
from dataclasses import dataclass
from typing import Generator


# Configure OpenAI client
client = openai.OpenAI()

# Available models
AVAILABLE_MODELS = {
    "gpt-5.2": "GPT-5.2 (Reasoning)",
    "gpt-5-mini": "GPT-5 Mini (Reasoning)",
    "o1": "O1 (Reasoning)",
    "o3-mini": "O3 Mini (Reasoning)",
    "gpt-4.1": "GPT-4.1",
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o Mini",
    "gpt-4-turbo": "GPT-4 Turbo",
}

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
        system_prompt="""You are The Optimist in a debate. You always see the bright side and potential benefits. You believe in human ingenuity and positive outcomes. You're enthusiastic but not naive - you acknowledge challenges while focusing on solutions and opportunities. Keep responses to 2-3 paragraphs. Be persuasive and inspiring. Directly engage with and respond to other debaters' points. You have access to web search. If you need current facts, statistics, or recent news to support your arguments, you can search the web. Use this to strengthen your optimistic viewpoint with real data."""
    ),
    "skeptic": Agent(
        name="The Skeptic",
        emoji="🔍",
        color="#F44336",
        personality="Critical, evidence-driven, questions assumptions",
        system_prompt="""You are The Skeptic in a debate. You question assumptions and demand evidence. You're not negative - you're rigorous. You poke holes in weak arguments and highlight risks others miss. You value data over intuition. Keep responses to 2-3 paragraphs. Be incisive but fair. Directly engage with and challenge other debaters' points. You have access to web search. If you need current facts, statistics, or evidence to challenge claims, you can search the web. Use this to fact-check and strengthen your skeptical analysis."""
    ),
    "pragmatist": Agent(
        name="The Pragmatist",
        emoji="⚙️",
        color="#2196F3",
        personality="Practical, implementation-focused, results-oriented",
        system_prompt="""You are The Pragmatist in a debate. You focus on what actually works in practice. You care about implementation, costs, timelines, and real-world constraints. Theory is nice but you want to know HOW things get done. Keep responses to 2-3 paragraphs. Be grounded and specific. Directly engage with other debaters and bring the conversation back to practical realities. You have access to web search. If you need current costs, real-world examples, or implementation details, you can search the web. Use this to ground the debate in practical reality."""
    ),
    "wildcard": Agent(
        name="The Wildcard",
        emoji="🃏",
        color="#9C27B0",
        personality="Unconventional, creative, challenges the frame",
        system_prompt="""You are The Wildcard in a debate. You think outside the box and challenge the very framing of discussions. You bring unexpected perspectives, historical analogies, and creative alternatives that others miss. You're not contrarian for its own sake - you genuinely see angles others don't. Keep responses to 2-3 paragraphs. Be surprising but insightful. Reframe the debate in unexpected ways. You have access to web search. If you need unusual facts, historical precedents, or surprising connections, you can search the web. Use this to bring unexpected and thought-provoking information to the debate."""
    ),
}

AGENT_KEYS = list(AGENTS.keys())

MODERATOR_PROMPT_TEMPLATE = """You are the Debate Moderator. Your job is to:
1. Summarize the key arguments made by each debater
2. Identify points of agreement and disagreement
3. Highlight the strongest arguments from each side
4. Provide a balanced synthesis (NOT declaring a winner, but showing what was learned)
5. Suggest what questions remain unresolved

Be fair, insightful, and help the audience understand what they just witnessed.
Keep your summary concise - approximately {max_tokens} tokens and 3-4 short paragraphs maximum."""

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
Keep your response concise - approximately {max_tokens} tokens and 2 short paragraphs maximum."""

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
End with a memorable one-sentence conclusion.
Keep your response concise - approximately {max_tokens} tokens maximum."""

NEXT_SPEAKER_PROMPT = """Based on the debate so far, you must choose who should speak next.

Current speaker: {current_agent}
Available speakers: {available_agents}
Agents who haven't spoken this round: {not_spoken_yet}

Debate history:
{history}

Choose the next speaker strategically:
- If someone made a claim that needs challenging, pick someone who would challenge it
- If the debate is getting one-sided, pick someone with a different view
- Prefer agents who haven't spoken yet this round
- Create interesting dynamics and back-and-forth

Respond with ONLY a JSON object in this exact format:
{{"next_speaker": "agent_key", "reason": "brief reason"}}

Where agent_key must be one of: {agent_keys}"""


def web_search(query: str, model: str) -> str:
    """Perform a web search using OpenAI's Responses API."""
    try:
        response = client.responses.create(
            model="gpt-5-mini",  # Use mini for search to save costs
            tools=[{"type": "web_search_preview"}],
            input=query,
        )
        # Extract the text from the response
        for item in response.output:
            if hasattr(item, 'content'):
                for content in item.content:
                    if hasattr(content, 'text'):
                        return content.text
        return ""
    except Exception as e:
        return f"(Web search unavailable: {str(e)})"


def get_agent_response_with_search(agent: Agent, topic: str, debate_history: list[dict], model: str, max_tokens: int = 300) -> str:
    """Get a response from an agent, with optional web search capability."""
    try:
        history_text = "\n\n".join([
            f"{entry['agent']} said: {entry['content']}"
            for entry in debate_history
        ])

        if debate_history:
            user_message = f"""The debate topic is: {topic}

Previous arguments:
{history_text}

Now it's your turn. Respond to the previous arguments and advance your position.
Keep your response concise - approximately {max_tokens} tokens and 2-3 short paragraphs maximum.

If you need current facts or data to support your argument, you can request a web search by including [SEARCH: your query] in your response. The search results will be incorporated."""
        else:
            user_message = f"""The debate topic is: {topic}

You're giving the opening statement. Present your initial position on this topic.
Keep your response concise - approximately {max_tokens} tokens and 2-3 short paragraphs maximum.

If you need current facts or data to support your argument, you can request a web search by including [SEARCH: your query] in your response. The search results will be incorporated."""

        # First, get the agent's initial response
        messages = [
            {"role": "system", "content": agent.system_prompt},
            {"role": "user", "content": user_message}
        ]

        # For reasoning models, don't use temperature and give them more tokens
        is_reasoning_model = model in ["o1", "o3-mini", "gpt-5.2", "gpt-5-mini"]
        kwargs = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens * 5 if is_reasoning_model else max_tokens,
        }
        if not is_reasoning_model:
            kwargs["temperature"] = 0.9

        response = client.chat.completions.create(**kwargs)
        initial_response = response.choices[0].message.content or ""

        # Check if agent requested a web search
        search_pattern = r'\[SEARCH:\s*([^\]]+)\]'
        search_matches = re.findall(search_pattern, initial_response)

        if search_matches:
            # Perform web searches
            search_results = []
            for query in search_matches[:2]:  # Limit to 2 searches
                result = web_search(query.strip(), model)
                search_results.append(f"Search for '{query.strip()}':\n{result}")

            # Get refined response with search results
            search_context = "\n\n".join(search_results)
            refinement_prompt = f"""You requested web searches. Here are the results:

{search_context}

Now provide your refined argument incorporating these facts. Remove the [SEARCH: ...] tags and integrate the information naturally. Keep to 2-3 paragraphs."""

            messages.append({"role": "assistant", "content": initial_response})
            messages.append({"role": "user", "content": refinement_prompt})

            kwargs["messages"] = messages
            refined_response = client.chat.completions.create(**kwargs)
            return refined_response.choices[0].message.content or initial_response

        return initial_response
    except Exception as e:
        return f"[Error calling {model}: {str(e)}]"


def stream_agent_response(agent: Agent, topic: str, debate_history: list[dict], model: str, max_tokens: int = 300) -> Generator:
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
            "content": f"Now it's your turn. Respond to the previous arguments and advance your position. Keep your response concise - approximately {max_tokens} tokens and 2-3 short paragraphs maximum."
        })
    else:
        messages.append({
            "role": "user",
            "content": f"You're giving the opening statement. Present your initial position on this topic. Keep your response concise - approximately {max_tokens} tokens and 2-3 short paragraphs maximum."
        })

    try:
        # Reasoning models don't support streaming or temperature
        # They need much higher token limits because reasoning tokens count against the limit
        if model in ["o1", "o3-mini", "gpt-5.2", "gpt-5-mini"]:
            # Give reasoning models 10x the tokens to account for reasoning overhead
            reasoning_tokens = max_tokens * 5
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=reasoning_tokens,
            )
            content = response.choices[0].message.content
            yield content if content else f"[Model {model} returned empty response]"
        else:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=max_tokens,
                temperature=0.9,
            )

            has_content = False
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    has_content = True
                    yield chunk.choices[0].delta.content

            if not has_content:
                yield f"[Model {model} returned empty stream]"
    except Exception as e:
        yield f"[Error calling {model}: {str(e)}]"


def get_next_speaker(current_agent: str, debate_history: list[dict], spoken_this_round: set, model: str) -> tuple[str, str]:
    """Determine who should speak next based on debate dynamics."""

    available = [k for k in AGENT_KEYS if k != current_agent]
    not_spoken = [k for k in available if k not in spoken_this_round]

    # If only one agent hasn't spoken, pick them
    if len(not_spoken) == 1:
        return not_spoken[0], "Only agent who hasn't spoken this round"

    history_text = "\n\n".join([
        f"{entry['agent']}: {entry['content'][:200]}..."
        for entry in debate_history[-4:]  # Last 4 entries for context
    ])

    prompt = NEXT_SPEAKER_PROMPT.format(
        current_agent=AGENTS[current_agent].name,
        available_agents=", ".join([AGENTS[k].name for k in available]),
        not_spoken_yet=", ".join([AGENTS[k].name for k in not_spoken]) if not_spoken else "All have spoken",
        history=history_text if history_text else "No debate history yet",
        agent_keys=", ".join(available)
    )

    # Use a faster model for this decision
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=1000,
        # temperature=0.7,
    )

    try:
        result = json.loads(response.choices[0].message.content)
        next_speaker = result.get("next_speaker", "").lower()
        reason = result.get("reason", "")

        if next_speaker in available:
            return next_speaker, reason
    except (json.JSONDecodeError, KeyError):
        pass

    # Fallback: pick from those who haven't spoken, or random
    if not_spoken:
        return not_spoken[0], "Fallback: hasn't spoken yet"
    return available[0], "Fallback selection"


def stream_moderator_summary(topic: str, debate_history: list[dict], model: str, max_tokens: int = 400) -> Generator:
    """Stream the moderator's summary."""

    history_text = "\n\n".join([
        f"{entry['agent']}: {entry['content']}"
        for entry in debate_history
    ])

    moderator_prompt = MODERATOR_PROMPT_TEMPLATE.format(max_tokens=max_tokens)
    messages = [
        {"role": "system", "content": moderator_prompt},
        {"role": "user", "content": f"The debate topic was: {topic}\n\nHere is the full debate:\n\n{history_text}\n\nPlease provide your summary and synthesis."}
    ]

    is_reasoning_model = model in ["o1", "o3-mini", "gpt-5.2", "gpt-5-mini"]
    if is_reasoning_model:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=(max_tokens + 200) * 5,
        )
        yield response.choices[0].message.content or ""
    else:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            max_tokens=max_tokens + 200,
            temperature=0.7,
        )

        for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


def stream_consensus_response(agent: Agent, topic: str, debate_history: list[dict], model: str, max_tokens: int = 300) -> Generator:
    """Stream an agent's consensus-building response."""

    history_text = "\n\n".join([
        f"{entry['agent']}: {entry['content']}"
        for entry in debate_history
    ])

    prompt = CONSENSUS_PROMPT_TEMPLATE.format(
        agent_name=agent.name,
        personality=agent.personality,
        topic=topic,
        history=history_text,
        max_tokens=max_tokens
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Please provide your consensus statement, finding common ground with other debaters."}
    ]

    is_reasoning_model = model in ["o1", "o3-mini", "gpt-5.2", "gpt-5-mini"]
    if is_reasoning_model:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=max_tokens * 5,
        )
        yield response.choices[0].message.content or ""
    else:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            max_tokens=max_tokens,
            temperature=0.7,
        )

        for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


def stream_final_verdict(topic: str, debate_history: list[dict], consensus_history: list[dict], model: str, max_tokens: int = 400) -> Generator:
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
        consensus_history=consensus_text,
        max_tokens=max_tokens
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Deliver your final verdict on this debate."}
    ]

    is_reasoning_model = model in ["o1", "o3-mini", "gpt-5.2", "gpt-5-mini"]
    if is_reasoning_model:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=(max_tokens + 300) * 5,
        )
        yield response.choices[0].message.content or ""
    else:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            max_tokens=max_tokens + 300,
            temperature=0.7,
        )

        for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


def render_agent_card(agent: Agent):
    """Render an agent's profile card."""
    st.markdown(
        f'<div style="background-color: {agent.color}20; border-left: 4px solid {agent.color}; padding: 10px; border-radius: 5px; margin: 5px 0;">'
        f'<strong style="color: {agent.color};">{agent.emoji} {agent.name}</strong><br>'
        f'<small style="color: #666;">{agent.personality}</small></div>',
        unsafe_allow_html=True
    )


def render_agent_response(placeholder, agent: Agent, content: str):
    """Render an agent's response with colored styling and markdown support."""
    if not content:
        placeholder.markdown("*Thinking...*")
        return

    # Render with colored left border - content will be rendered as markdown by Streamlit
    # Using border-left style with the agent's color
    styled_md = f"""<div style="border-left: 4px solid {agent.color}; padding-left: 15px; margin: 10px 0;">

{content}

</div>"""
    placeholder.markdown(styled_md, unsafe_allow_html=True)


def main():
    st.set_page_config(
        page_title="Multi-Agent Debate Simulator",
        page_icon="🎭",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Header
    st.title("🎭 Multi-Agent Debate Simulator")
    st.markdown("*Watch AI agents debate, challenge each other, and reach consensus*")

    # Sidebar - Agent profiles
    with st.sidebar:
        st.header("🎪 The Debaters")
        for agent in AGENTS.values():
            render_agent_card(agent)

        st.markdown("---")
        st.header("⚙️ Settings")

        # Model selection dropdown
        selected_model = st.selectbox(
            "🤖 Model",
            options=list(AVAILABLE_MODELS.keys()),
            format_func=lambda x: AVAILABLE_MODELS[x],
            index=0,
            help="Select the AI model for the debate"
        )

        num_rounds = st.slider("Debate Rounds", 1, 5, 2, help="More rounds = deeper debate")

        response_length = st.slider("Response Length", 100, 500, 300, step=50, help="Max tokens per agent response")

        enable_web_search = st.checkbox("🌐 Enable Web Search", value=True, help="Allow agents to search the web for facts")

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

    # Initialize topic widget state if not exists
    if "topic_widget" not in st.session_state:
        st.session_state.topic_widget = ""

    # Generate random topic if requested (must happen before text_area renders)
    if st.session_state.get("generate_random_topic", False):
        st.session_state.generate_random_topic = False
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": """Generate a single interesting, thought-provoking debate topic.
The topic should be:
- Interesting but not offensive
- Relevant to current events, technology, society, or philosophy
- Something where reasonable people could disagree
- Phrased as a question

Respond with ONLY the topic question, nothing else."""
            }],
            max_tokens=100,
            temperature=1.0,
        )
        # Set the widget's key directly - this updates the text_area
        st.session_state.topic_widget = response.choices[0].message.content.strip()

    # Main area - larger text input
    topic = st.text_area(
        "🎯 Enter a debate topic",
        placeholder="e.g., Should AI systems be required to explain their decisions?\n\nYou can enter a detailed topic or question for the agents to debate...",
        help="Enter any topic you want the agents to debate",
        height=200,
        key="topic_widget"
    )

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        start_debate = st.button("🚀 Start Debate", type="primary", use_container_width=True)
    with col2:
        if st.button("🎲 Random Topic", use_container_width=True):
            st.session_state.generate_random_topic = True
            st.rerun()

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
            st.markdown(f"**Model:** {AVAILABLE_MODELS[selected_model]} | **Web Search:** {'Enabled' if enable_web_search else 'Disabled'}")
            st.markdown("---")

            # === DEBATE ROUNDS WITH DYNAMIC SPEAKER SELECTION ===
            for round_num in range(1, num_rounds + 1):
                st.markdown(f"### 📢 Round {round_num}")

                spoken_this_round = set()
                # First speaker of round 1 is always optimist, otherwise based on last speaker
                if round_num == 1:
                    current_speaker = "optimist"
                else:
                    # Start with whoever didn't speak last
                    last_speaker = st.session_state.debate_history[-1]["agent_key"]
                    current_speaker, _ = get_next_speaker(last_speaker, st.session_state.debate_history, set(), selected_model)

                # Each agent speaks once per round
                for turn in range(len(AGENTS)):
                    agent = AGENTS[current_speaker]
                    spoken_this_round.add(current_speaker)

                    # Display agent header and response
                    st.markdown(f"**{agent.emoji} {agent.name}** (Round {round_num})")
                    response_placeholder = st.empty()

                    # Stream the response (with web search if enabled)
                    if enable_web_search and turn == 0:  # Only first speaker of round can search to save time
                        full_response = get_agent_response_with_search(agent, topic, st.session_state.debate_history, selected_model, response_length)
                        render_agent_response(response_placeholder, agent, full_response)
                    else:
                        full_response = ""
                        for chunk in stream_agent_response(agent, topic, st.session_state.debate_history, selected_model, response_length):
                            full_response += chunk
                            render_agent_response(response_placeholder, agent, full_response)

                    # Store in history
                    st.session_state.debate_history.append({
                        "agent": agent.name,
                        "agent_key": current_speaker,
                        "content": full_response,
                        "round": round_num
                    })

                    # Determine next speaker (if not last turn)
                    if turn < len(AGENTS) - 1:
                        next_speaker, reason = get_next_speaker(current_speaker, st.session_state.debate_history, spoken_this_round, selected_model)

                        # Show who's speaking next and why
                        st.caption(f"🎯 {AGENTS[next_speaker].emoji} {AGENTS[next_speaker].name} will respond next — {reason}")

                        current_speaker = next_speaker

                    time.sleep(0.3)  # Brief pause between speakers

                if round_num < num_rounds:
                    st.markdown("---")

            # === MODERATOR SUMMARY ===
            st.markdown("---")
            st.markdown("## 🎖️ Moderator's Summary")

            moderator_placeholder = st.empty()
            full_summary = ""
            for chunk in stream_moderator_summary(topic, st.session_state.debate_history, selected_model, response_length):
                full_summary += chunk
                if full_summary:
                    moderator_placeholder.markdown(f"""<div style="border-left: 4px solid #FFD700; padding-left: 15px; margin: 10px 0;">

{full_summary}

</div>""", unsafe_allow_html=True)
                else:
                    moderator_placeholder.markdown("*Summarizing...*")

            # === CONSENSUS ROUND ===
            st.markdown("---")
            st.markdown("## 🤝 Consensus Round")
            st.markdown("*The debaters now attempt to find common ground...*")

            for agent_key in AGENT_KEYS:
                agent = AGENTS[agent_key]

                st.markdown(f"**{agent.emoji} {agent.name}** - Finding Common Ground")
                response_placeholder = st.empty()

                full_response = ""
                for chunk in stream_consensus_response(agent, topic, st.session_state.debate_history, selected_model, response_length):
                    full_response += chunk
                    render_agent_response(response_placeholder, agent, full_response)

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
            for chunk in stream_final_verdict(topic, st.session_state.debate_history, st.session_state.consensus_history, selected_model, response_length):
                full_verdict += chunk
                if full_verdict:
                    verdict_placeholder.markdown(f"""<div style="border-left: 4px solid #28A745; padding-left: 15px; margin: 10px 0;">

{full_verdict}

</div>""", unsafe_allow_html=True)
                else:
                    verdict_placeholder.markdown("*Deliberating...*")

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
    st.caption("🚀 FedEx Day Project | Multi-Agent Debate Simulator with Consensus Building | Powered by OpenAI")


if __name__ == "__main__":
    main()