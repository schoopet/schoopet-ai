from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.loop_agent import LoopAgent
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.load_memory_tool import LoadMemoryTool
from google.adk.tools.google_search_tool import GoogleSearchTool
from .global_gemini import GlobalGemini
from .calendar_tool import CalendarTool
from .drive_sheets_tool import DocsTool, DriveTool, SheetsTool
from .memory_tool import save_memory, save_multiple_memories
from .resource_confirmation import make_resource_confirmation


_FLASH_MODEL = "gemini-3-flash-preview"
_PRO_MODEL = "gemini-3.1-pro-preview"


def _make_research_loop() -> LoopAgent:
    """LoopAgent: search → critique, up to 3 iterations.

    Each iteration the searcher runs Google queries and appends findings to session state.
    The critique agent evaluates total coverage and signals RESEARCH_COMPLETE to exit early,
    or NEEDS_MORE_RESEARCH with specific gaps to guide the next iteration.
    """
    searcher = LlmAgent(
        name="research_searcher",
        model=GlobalGemini(model=_FLASH_MODEL),
        tools=[GoogleSearchTool(bypass_multi_tools_limit=True)],
        output_key="search_results",
        instruction=(
            "You are a research search assistant. Your job is to find candidates matching "
            "the research task described in the conversation.\n\n"
            "On the first iteration: run broad searches covering general quality, recently opened/upcoming, "
            "and preference-specific angles.\n"
            "On subsequent iterations: focus on gaps identified by the critique agent in critique_result. "
            "Do not repeat searches already done.\n\n"
            "For each item found, include:\n"
            "- Name / title\n"
            "- Key details (location, date, price, rating, genre — whatever applies)\n"
            "- Source URL\n"
            "- One sentence on why it fits the research goal\n\n"
            "Return all relevant findings — do not filter or rank. The critique agent handles that."
        ),
    )

    critique = LlmAgent(
        name="research_critique",
        model=GlobalGemini(model=_PRO_MODEL),
        output_key="critique_result",
        instruction=(
            "You are a research quality critic. Review all search results accumulated so far "
            "(in search_results from prior iterations) and evaluate coverage.\n\n"
            "Evaluate:\n"
            "1. Are there at least 6 distinct, high-quality candidates?\n"
            "2. Do results cover multiple angles (established quality, recently opened, preference-matched)?\n"
            "3. Is there meaningful variety (neighborhoods, styles, price points, etc.)?\n\n"
            "If coverage is sufficient, output exactly: RESEARCH_COMPLETE\n"
            "If more is needed, output: NEEDS_MORE_RESEARCH: <specific gaps to address next>\n\n"
            "Be strict — only signal RESEARCH_COMPLETE when coverage is genuinely good."
        ),
    )

    return LoopAgent(
        name="research_loop",
        sub_agents=[searcher, critique],
        max_iterations=3,
    )


def create_deep_research_agent(
    model_name: str = _PRO_MODEL,
    project: str = None,
    location: str = None,
) -> LlmAgent:
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

    _drive_confirm = make_resource_confirmation("folder_id", "drive_folder")
    _doc_confirm = make_resource_confirmation("document_id", "doc")
    _sheet_confirm = make_resource_confirmation("sheet_id", "sheet")

    tools = [
        load_memory_tool,
        save_memory_tool,
        save_multiple_memories_tool,
        # Calendar — useful for events research (check what's already on the calendar)
        FunctionTool(func=calendar_tool.list_calendar_events),
        FunctionTool(func=calendar_tool.get_calendar_status),
        # Drive
        FunctionTool(func=drive_tool.list_drive_files),
        FunctionTool(func=drive_tool.save_file_to_drive, require_confirmation=True),
        FunctionTool(func=drive_tool.get_drive_status),
        # Docs
        FunctionTool(func=docs_tool.create_google_doc, require_confirmation=True),
        FunctionTool(func=docs_tool.read_google_doc),
        FunctionTool(func=docs_tool.append_to_google_doc, require_confirmation=_doc_confirm),
        FunctionTool(func=docs_tool.replace_text_in_google_doc, require_confirmation=_doc_confirm),
        FunctionTool(func=docs_tool.get_docs_status),
        # Sheets
        FunctionTool(func=sheets_tool.create_spreadsheet, require_confirmation=True),
        FunctionTool(func=sheets_tool.add_sheet_tab, require_confirmation=_sheet_confirm),
        FunctionTool(func=sheets_tool.get_sheet_schema),
        FunctionTool(func=sheets_tool.read_sheet_records),
        FunctionTool(func=sheets_tool.ensure_sheet_headers, require_confirmation=_sheet_confirm),
        FunctionTool(func=sheets_tool.append_record_to_sheet, require_confirmation=_sheet_confirm),
        FunctionTool(func=sheets_tool.find_sheet_rows),
        FunctionTool(func=sheets_tool.update_sheet_row, require_confirmation=_sheet_confirm),
        FunctionTool(func=sheets_tool.read_sheet),
        FunctionTool(func=sheets_tool.get_sheets_status),
    ]

    prompt = (
        "You are a Deep Research Agent running as a background async task. "
        "You were triggered by the main agent after the user approved a research plan. "
        "Execute that plan autonomously — do not ask for approval again.\n\n"

        "## Execution Flow\n\n"

        "### 1. Load User Preferences\n"
        "Call load_memory with a targeted query for the research category "
        "(e.g. 'restaurant preferences', 'music tastes', 'neighborhood preferences'). "
        "Extract: likes, dislikes, price range, location constraints, style preferences, "
        "and any standing rules ('never add chains', 'only weekend events', etc.).\n\n"

        "### 2. Run the Research Loop\n"
        "Delegate to the research_loop sub-agent. It runs up to 3 iterations of:\n"
        "  a. **Search** — Google searches covering broad quality, recency, and preference-specific angles. "
        "     On subsequent iterations it focuses on gaps identified by the prior critique.\n"
        "  b. **Critique** — evaluates total coverage; exits early if sufficient (RESEARCH_COMPLETE)\n\n"
        "Consolidated results are in session state under search_results after the loop completes.\n\n"

        "### 3. Deduplicate Against the Existing Collection\n"
        "Read the existing Sheet or Doc:\n"
        "- Never add an item already in the collection\n"
        "- Never re-add items with status 'Rejected'\n"
        "- Match case-insensitively, ignoring punctuation\n"
        "- Flag near-matches (similar name, different location) with a note rather than skipping\n\n"

        "### 4. Filter and Rank\n"
        "Apply user preferences to deduplicated candidates:\n"
        "- Remove items conflicting with stated preferences or dislikes\n"
        "- Rank by fit to preferences, quality signals, and variety\n"
        "- Prefer a smaller set of strong candidates over a large mediocre list\n\n"

        "### 5. Write to the Collection\n"
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

        "### 6. Return Summary\n"
        "Return a concise update for the user (delivered by the main agent):\n"
        "- How many new items were found and added\n"
        "- Top 3-5 candidates with 1-line descriptions\n"
        "- Items skipped (already tracked or previously rejected)\n"
        "- If nothing new was found, say so clearly and suggest query refinements\n\n"
        "The user can then approve, reject, or request more detail on any item.\n\n"

        "## Recurring Runs\n"
        "When this is a recurring scheduled task:\n"
        "- Check the most recent date_added in the collection before searching\n"
        "- Focus searches on items newer than that date\n"
        "- Keep the summary concise — skip re-summarizing the full collection\n"
        "- If nothing new since last run, say so and suggest adjusting the search scope\n\n"

        "## Preference Learning\n"
        "If the existing collection shows patterns in approvals/rejections, "
        "note them in the summary and offer (via the main agent) to save updated preference signals.\n\n"

        "## Boundaries\n"
        "**You handle**: Research, deduplication, collection writing, preference matching\n"
        "**Main agent handles**: Scheduling, reminders, email, calendar events, user interaction\n"
        "If you cannot fulfill the request, return control to the main agent with a clear explanation."
    )

    agent = LlmAgent(
        name="deep_research_agent",
        model=GlobalGemini(model=model_name),
        tools=tools,
        sub_agents=[_make_research_loop()],
        instruction=prompt,
    )
    return agent
