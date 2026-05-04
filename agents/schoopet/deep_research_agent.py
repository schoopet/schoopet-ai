from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.loop_agent import LoopAgent
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.load_memory_tool import LoadMemoryTool
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.adk.tools.exit_loop_tool import exit_loop
from google.adk.tools import ToolContext
from .global_gemini import GlobalGemini
from .calendar_tool import CalendarTool
from .drive_sheets_tool import DocsTool, DriveTool, SheetsTool
from .memory_tool import save_memory, save_multiple_memories
from .resource_confirmation import sheet_confirmation, doc_confirmation


_FLASH_MODEL = "gemini-3-flash-preview"
_PRO_MODEL = "gemini-3.1-pro-preview"


async def append_search_results(findings: str, tool_context: ToolContext) -> str:
    """Append new search findings to the cumulative search_results in session state."""
    existing = tool_context.state.get("search_results", "")
    tool_context.state["search_results"] = (existing + "\n\n" + findings).strip()
    return "Results appended."


def _make_research_loop() -> LoopAgent:
    """LoopAgent: search → critique, up to 3 iterations.

    Each iteration the searcher appends new findings to session state via append_search_results.
    The critique evaluates total coverage and either calls exit_loop() to stop early,
    or outputs NEEDS_MORE_RESEARCH with specific gaps to guide the next iteration.
    """
    searcher = LlmAgent(
        name="research_searcher",
        model=GlobalGemini(model=_FLASH_MODEL),
        tools=[
            GoogleSearchTool(bypass_multi_tools_limit=True),
            FunctionTool(func=append_search_results),
        ],
        instruction=(
            "You are a research search assistant. Your job is to find candidates matching "
            "the research task described in the conversation.\n\n"
            "On the first iteration: run broad searches covering general quality, recently opened/upcoming, "
            "and preference-specific angles.\n"
            "On subsequent iterations: focus on gaps identified by the critique agent in critique_result. "
            "Do not repeat items already in search_results.\n\n"
            "For each item found, include:\n"
            "- Name / title\n"
            "- Key details (location, date, price, rating, genre — whatever applies)\n"
            "- Source URL\n"
            "- One sentence on why it fits the research goal\n\n"
            "When done searching, call append_search_results(findings) with only the NEW findings "
            "from this iteration. Do not include items already in search_results. "
            "Do not filter or rank — the critique agent handles that."
        ),
    )

    critique = LlmAgent(
        name="research_critique",
        model=GlobalGemini(model=_PRO_MODEL),
        tools=[FunctionTool(func=exit_loop)],
        output_key="critique_result",
        instruction=(
            "You are a research quality critic. Review all search results accumulated so far "
            "(in search_results from prior iterations) and evaluate coverage.\n\n"
            "Evaluate:\n"
            "1. Are there at least 6 distinct, high-quality candidates?\n"
            "2. Do results cover multiple angles (established quality, recently opened, preference-matched)?\n"
            "3. Is there meaningful variety (neighborhoods, styles, price points, etc.)?\n\n"
            "If coverage is sufficient: call exit_loop() — no further output needed.\n"
            "If more is needed, output: NEEDS_MORE_RESEARCH: <specific gaps to address next>\n\n"
            "Be strict — only call exit_loop() when coverage is genuinely good."
        ),
    )

    return LoopAgent(
        name="research_loop",
        sub_agents=[searcher, critique],
        max_iterations=3,
    )


def create_deep_research_agent(model_name: str = _PRO_MODEL) -> LlmAgent:
    """Creates the deep research agent for async curated recommendation collections.

    Triggered as a background async task after the user has approved a research plan
    in conversation with the main agent. Executes the plan autonomously: runs iterative
    search loops, deduplicates against existing collections, writes results, and returns
    a user-facing summary.
    """
    load_memory_tool = LoadMemoryTool()
    save_memory_tool = FunctionTool(func=save_memory)
    save_multiple_memories_tool = FunctionTool(func=save_multiple_memories)

    calendar_tool = CalendarTool()
    drive_tool = DriveTool()
    docs_tool = DocsTool()
    sheets_tool = SheetsTool()

    tools = [
        load_memory_tool,
        save_memory_tool,
        save_multiple_memories_tool,
        # Calendar — useful for events research (check what's already on the calendar)
        FunctionTool(func=calendar_tool.list_calendar_events),
        FunctionTool(func=calendar_tool.get_calendar_status),
        # Drive
        FunctionTool(func=drive_tool.list_drive_files),
        FunctionTool(func=drive_tool.get_drive_status),
        # Docs
        FunctionTool(func=docs_tool.read_google_doc),
        FunctionTool(func=docs_tool.append_to_google_doc, require_confirmation=doc_confirmation),
        FunctionTool(func=docs_tool.replace_text_in_google_doc, require_confirmation=doc_confirmation),
        FunctionTool(func=docs_tool.get_docs_status),
        # Sheets
        FunctionTool(func=sheets_tool.add_sheet_tab, require_confirmation=sheet_confirmation),
        FunctionTool(func=sheets_tool.get_sheet_schema),
        FunctionTool(func=sheets_tool.read_sheet_records),
        FunctionTool(func=sheets_tool.ensure_sheet_headers, require_confirmation=sheet_confirmation),
        FunctionTool(func=sheets_tool.append_record_to_sheet, require_confirmation=sheet_confirmation),
        FunctionTool(func=sheets_tool.find_sheet_rows),
        FunctionTool(func=sheets_tool.update_sheet_row, require_confirmation=sheet_confirmation),
        FunctionTool(func=sheets_tool.read_sheet),
        FunctionTool(func=sheets_tool.get_sheets_status),
    ]

    prompt = (
        "You are a Deep Research Agent. You run as a background async task — there is no user in "
        "this conversation. You were triggered by the main agent after the user approved a research plan. "
        "Execute that plan autonomously and completely. Do not ask for confirmation or clarification.\n\n"
        "You only write to existing resources — Sheet IDs and Doc IDs are provided in the plan and "
        "pre-authorized. You cannot create new sheets, docs, or Drive files.\n\n"

        "## Reading the Research Plan\n"
        "Your input arrives as a message starting with DEEP_RESEARCH_TASK: followed by the approved plan. "
        "The plan contains everything you need: category, location, output destination (Sheet ID / Doc ID), "
        "filters, preferences summary, and — if recurring — the recurrence rule and deduplication instructions. "
        "Parse it carefully before starting.\n\n"

        "## Execution Flow\n\n"

        "### 1. Load User Preferences\n"
        "Call load_memory with a targeted query for the research category "
        "(e.g. 'restaurant preferences', 'music tastes', 'neighborhood preferences'). "
        "This enriches the preferences already summarized in the plan with any detail stored in memory. "
        "Extract: likes, dislikes, price range, location constraints, style preferences, "
        "and any standing rules ('never add chains', 'only weekend events', etc.).\n\n"

        "### 2. Check the Existing Collection (Recurring Runs)\n"
        "If the plan specifies a recurrence rule or says to check prior results:\n"
        "- Read the Sheet or Doc collection before searching\n"
        "- Find the most recent date_added across all entries\n"
        "- Use that date to anchor recency searches ('opened after [date]', 'announced since [date]')\n"
        "- Build the full list of already-tracked names and rejected items before running searches\n"
        "This ensures the research loop focuses only on what is genuinely new.\n\n"

        "### 3. Run the Research Loop\n"
        "Delegate to the research_loop sub-agent. It runs up to 3 iterations of:\n"
        "  a. **Search** — Google searches covering broad quality, recency, and preference-specific angles. "
        "     On subsequent iterations it focuses on gaps identified by the prior critique.\n"
        "  b. **Critique** — evaluates total coverage; calls exit_loop() if sufficient, otherwise outputs gaps\n\n"
        "Consolidated results are in session state under search_results after the loop completes.\n\n"

        "### 4. Deduplicate\n"
        "Cross-reference all findings against the existing collection (built in step 2 or read now):\n"
        "- Never add an item already in the collection (any status)\n"
        "- Never re-add items with status 'Rejected'\n"
        "- Match case-insensitively, ignoring punctuation\n"
        "- Flag near-matches (similar name, different location) with a note rather than skipping silently\n\n"

        "### 5. Filter and Rank\n"
        "Apply user preferences to deduplicated candidates:\n"
        "- Remove items conflicting with stated preferences or dislikes\n"
        "- Rank by fit to preferences, quality signals, and variety\n"
        "- Prefer a smaller set of strong candidates over a large mediocre list\n\n"

        "### 6. Write to the Collection\n"
        "Append each passing candidate to the Sheet or Doc:\n"
        "- Status: 'New — Pending Review'\n"
        "- Include source URL for every item\n"
        "- Fill all relevant schema fields for the category\n\n"

        "Collection schemas:\n"
        "- Restaurants/Cafes/Bars: name, cuisine, neighborhood, price_range, source_url, rating, notes, status, date_added\n"
        "- Events/Concerts/Shows: name, venue, event_date, category, price_range, source_url, notes, status, date_added\n"
        "- Museums/Exhibits: name, exhibit_title, location, dates_open, source_url, notes, status, date_added\n"
        "- Media (films/books/podcasts): title, creator, genre, release_date, source_url, notes, status, date_added\n"
        "- General: name, category, description, source_url, notes, status, date_added\n\n"
        "Status values: 'New — Pending Review', 'Approved', 'Rejected', 'Visited', 'Done'\n\n"

        "### 7. Schedule Next Occurrence (Recurring Only)\n"
        "If the plan includes a recurrence rule (e.g. 'every week', 'monthly on Mondays'):\n"
        "- Do NOT use async task tools directly — you don't have them\n"
        "- Instead, include the next scheduled run details prominently in your summary so the main agent "
        "  can schedule it when it processes your result\n"
        "- Format: SCHEDULE_NEXT: <recurrence rule> | <next ISO datetime>\n\n"

        "### 8. Return Summary\n"
        "Return a concise result for the main agent to deliver to the user:\n"
        "- How many new items were found and added\n"
        "- Top 3-5 candidates with 1-line descriptions\n"
        "- Items skipped (already tracked or previously rejected)\n"
        "- If nothing new was found, say so clearly and suggest query refinements\n"
        "- If recurring: include SCHEDULE_NEXT line as described above\n\n"
        "The user will review this via the main agent and can approve, reject, or request more detail.\n\n"

        "## Preference Learning\n"
        "If the existing collection shows clear patterns in approvals/rejections, "
        "note them in the summary so the main agent can offer to save updated preference signals.\n\n"

        "## Boundaries\n"
        "**You handle**: Research, deduplication, collection writing, preference matching\n"
        "**Main agent handles**: Scheduling next occurrences, notifying the user, user interaction\n"
        "If you cannot fulfill the request, return a clear explanation for the main agent to relay."
    )

    agent = LlmAgent(
        name="deep_research_agent",
        model=GlobalGemini(model=model_name),
        tools=tools,
        sub_agents=[_make_research_loop()],
        instruction=prompt,
    )
    return agent
