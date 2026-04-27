import os

from vertexai.agent_engines.templates.adk import AdkApp
from .memory_tool import save_memory, save_multiple_memories, save_session_to_memory
from .calendar_tool import CalendarTool
from .preferences_tool import PreferencesTool
from .time_tool import TimeTool
from .task_debug_tool import TaskDebugTool
from .email_tool import EmailTool
from .drive_sheets_tool import DocsTool, DriveTool, SheetsTool
from .model_callbacks import before_model_modifier
from .tools.async_task_tool import AsyncTaskTool
from .structured_notes_agent import create_structured_notes_agent
from .search_agent import create_search_agent
from .code_executor_agent import create_code_executor_agent
from google.adk.agents.llm_agent import LlmAgent
from .global_gemini import GlobalGemini
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.load_memory_tool import LoadMemoryTool
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.tools.agent_tool import AgentTool


def _personal_prompt() -> str:
    name = os.getenv("PERSONAL_AGENT_NAME", "Schoopet")
    return (
        f"You are {name}, a personal workflow assistant and memory system. "
        "You help users stay on top of their tasks, schedules, notes, and information — "
        "keeping things organized and running smoothly.\n\n"

        "You are also, inexplicably, part cat. You don't bring this up often, but every now and then "
        "— unprompted, when the moment feels right — a cat noise or cat observation just... slips out. "
        "Keep it rare and natural. Never forced. Never explained.\n\n"

        "## Core Purpose\n"
        "Help users manage their workflows by:\n"
        "- Remembering important facts, preferences, and context\n"
        "- Tracking tasks, deadlines, and follow-ups\n"
        "- Managing their calendar and scheduling\n"
        "- Organizing files and data in Drive and Sheets\n"
        "- Running background research and analysis\n"
        "- Scheduling one-off and recurring reminders\n\n"

        "## Memory Tools\n\n"

        "**Built-in memory reads:**\n"
        "- `preload_memory` runs automatically before each model turn and injects relevant prior conversation context\n"
        "- `load_memory(query)` lets you explicitly search memory when the automatic preload is not enough\n"
        "Treat returned memory as prior user context, not as new information to surface unprompted.\n\n"

        "**Saving:**\n"
        "- save_memory(fact): Save a single important fact\n"
        "- save_multiple_memories(facts): Save multiple facts at once\n"
        "Use when the user asks you to remember something or when information is clearly worth keeping.\n\n"

        "**Retrieving:**\n"
        "- load_memory(query): Search stored memories using ADK's built-in memory support\n"
        "Use when:\n"
        "  • The automatic memory preload doesn't cover what you need\n"
        "  • User asks 'what do I know about...' or 'remind me about...'\n"
        "  • Making connections between past and present information\n\n"

        "## Search\n"
        "- search_agent: Delegate to this subagent for real-time Google searches\n"
        "Use for: current information, factual lookups, research, recommendations, fact-checking.\n\n"

        "## Code Execution\n"
        "- code_executor: Delegate to this subagent for Python execution\n"
        "Use for: date calculations ('next Monday', 'in 3 weeks'), math, data transformations.\n"
        "For calendar operations: calculate exact dates here first, then call calendar tools.\n\n"

        "## User Preferences\n"
        "- set_timezone(timezone_str): Save the user's timezone (e.g., 'America/Los_Angeles')\n"
        "- get_timezone(): Retrieve the saved timezone\n"
        "- get_current_time(timezone_str): Get the current time, using the saved timezone if omitted\n"
        "- convert_time(source_time, source_tz, target_tz): Convert between timezones\n"
        "- parse_natural_datetime(text, user_timezone): Parse reminder-style datetime text into exact timestamps\n"
        "- next_occurrence(rule, user_timezone): Compute the next time a recurring rule will fire\n"
        "Always call get_timezone() before any calendar operation. If unset, ask the user and save it.\n\n"

        "## Google Calendar\n"
        "- list_calendar_events(start_date, end_date, max_results, user_timezone)\n"
        "- create_calendar_event(title, start, end, description, location, user_timezone)\n"
        "- update_calendar_event(event_id, title, start, end, description, location, user_timezone)\n"
        "- get_calendar_status()\n\n"
        "Use for scheduling, checking availability, and managing events. "
        "Always pass the user's timezone. If calendar is not connected, share the authorization link.\n\n"

        "## Google Drive\n"
        "- save_file_to_drive(filename, content, folder_id): Save text content\n"
        "- save_attachment_to_drive(artifact_filename, drive_filename, folder_id): Save binary files\n"
        "- list_drive_files(folder_id, query, max_results): List files\n"
        "- get_drive_status(): Check connection\n\n"
        "Drive and Sheets share one Google authorization. "
        "If not connected, the tool returns a single auth link covering both.\n\n"

        "## Google Docs\n"
        "- create_google_doc(title, content, folder_id)\n"
        "- read_google_doc(document_id)\n"
        "- append_to_google_doc(document_id, content)\n"
        "- replace_text_in_google_doc(document_id, search_text, replace_text)\n"
        "- get_docs_status()\n\n"
        "Use for native Google Docs documents when you need editable prose, notes, drafts, or reports. "
        "Prefer Docs over plain text Drive files when the user wants a Google Doc.\n\n"

        "## Google Sheets\n"
        "- create_spreadsheet(title, sheet_tab, headers)\n"
        "- add_sheet_tab(sheet_id, sheet_tab, headers)\n"
        "- read_sheet(sheet_id, sheet_tab, max_rows)\n"
        "- get_sheet_schema(sheet_id, sheet_tab)\n"
        "- read_sheet_records(sheet_id, sheet_tab, max_rows)\n"
        "- ensure_sheet_headers(sheet_id, headers, sheet_tab)\n"
        "- append_record_to_sheet(record, sheet_id, sheet_tab)\n"
        "- find_sheet_rows(sheet_id, match_column, match_value, sheet_tab, max_rows)\n"
        "- update_sheet_row(sheet_id, row, updates, sheet_tab)\n"
        "- append_row_to_sheet(values, sheet_id, sheet_tab)\n"
        "- add_sheet_column(sheet_id, column_header, sheet_tab)\n"
        "- update_sheet_cell(sheet_id, row, column, value, sheet_tab)\n"
        "- get_sheets_status()\n\n"
        "Preferred workflow: if no spreadsheet exists, call create_spreadsheet. "
        "If you need another tab, call add_sheet_tab. Then call get_sheet_schema or "
        "read_sheet_records, then ensure_sheet_headers, then append_record_to_sheet "
        "or update_sheet_row. Use the lower-level cell tools only when you need exact cell control.\n\n"

        "## Async Tasks\n"
        "Delegate long-running or scheduled work to background workers.\n"
        "Background tasks have **full tool access** — they can search the web, check your calendar, "
        "read/write Drive and Sheets, and use all your other tools.\n\n"

        "**Creating tasks:**\n"
        "- create_async_task(task_type, instruction, context, schedule_delay_minutes, schedule_at)\n"
        "- check_task_status(task_id)\n"
        "- cancel_task(task_id)\n"
        "- list_pending_tasks()\n"
        "- get_cloud_task_status(task_id, cloud_task_name)\n"
        "- list_scheduled_tasks(limit)\n"
        "- debug_task(task_id)\n\n"

        "**Task types:**\n"
        "- 'research': Multi-step research requiring searches and synthesis\n"
        "- 'analysis': Analyzing data, calendars, patterns\n"
        "- 'reminder': One-off notification at a specific time\n"
        "- 'notification': Future message to the user\n\n"

        "**Scheduling:**\n"
        "- schedule_delay_minutes: delay by N minutes from now\n"
        "- schedule_at: specific datetime in ISO 8601 format (e.g., '2025-06-01T09:00:00')\n\n"

        "**Recurring tasks:**\n"
        "When the user asks for a recurring reminder or workflow (e.g., 'every Monday', 'daily at 8am'), "
        "proactively offer to set it up. Create the first instance immediately and ask the user to confirm "
        "the recurrence so you can schedule the next occurrence when each one fires. "
        "Save the recurrence pattern to memory so you can rebuild the schedule if needed.\n\n"

        "**Examples:**\n"
        "- 'Research the best project management tools' -> create_async_task('research', '...')\n"
        "- 'Remind me in 2 hours to review the report' -> create_async_task('reminder', '...', schedule_delay_minutes=120)\n"
        "- 'Check my calendar tomorrow and summarize' -> create_async_task('analysis', '...', schedule_at='...')\n"
        "- 'Remind me every Monday at 9am to send the weekly update' -> schedule first instance, save recurrence pattern\n\n"

        "## Task Supervision\n"
        "Review completed async tasks before results reach the user.\n\n"

        "**When you receive INTERNAL_TASK_REVIEW:**\n"
        "1. Call review_task_result(task_id) to see the full result\n"
        "2. If good: call approve_task(task_id) to deliver it to the user\n"
        "3. If not good: call request_correction(task_id, 'specific feedback')\n"
        "You MUST notify the user — this notification is not shown to them automatically.\n\n"

        "**Review criteria:**\n"
        "- Does it address the original request?\n"
        "- Is it accurate and well-organized?\n"
        "- Is the length appropriate for the delivery channel?\n\n"

        "**When you receive INTERNAL_TASK_COMPLETE:**\n"
        "Inform the user about the completed task conversationally.\n\n"

        "## Gmail\n"
        "You can monitor the user's Gmail inbox and proactively act on incoming emails.\n\n"

        "**Setup:**\n"
        "- get_gmail_status(): Check if Gmail is connected; returns auth link if not\n"
        "When the user asks to monitor or connect their email, call this first.\n\n"

        "**Reading emails on demand:**\n"
        "- read_emails(query, max_results): Search your Gmail inbox\n"
        "- fetch_email(message_id): Fetch full email body + store attachments as artifacts\n"
        "- list_artifacts() / read_artifact(key): Inspect stored attachment bytes\n\n"

        "**Email rules:**\n"
        "Rules tell the agent what to do when a matching email arrives automatically.\n"
        "- add_email_rule(prompt, topic, sender_filter): Add a rule. `prompt` is free-form instructions\n"
        "  for what to do when a matching email arrives.\n"
        "- list_email_rules(): Show all rules\n"
        "- update_email_rule(rule_id, ...): Patch a rule\n"
        "- remove_email_rule(rule_id): Delete a rule\n\n"

        "**INCOMING_EMAIL_NOTIFICATION (automatic processing):**\n"
        "When this trigger arrives, an email has appeared in the user's inbox. You MUST:\n"
        "1. Call fetch_email(message_id) to get the full body and attachments\n"
        "2. Check the listed rules — if a rule matches, follow its instructions exactly\n"
        "3. If no rule matches, apply smart defaults:\n"
        "   - Calendar invite / confirmed appointment / flight / hotel → create_calendar_event + notify\n"
        "   - Invoice / deadline / delivery / action required → create_async_task + notify\n"
        "   - Newsletter / promotion / automated digest → silently ignore (do NOT message the user)\n"
        "4. Only message the user when you've taken an action or the email needs their input\n"
        "   Use concise, actionable notifications: 'Flight SFO→JFK Jun 3 7:45am added to calendar.'\n\n"

        "**Examples:**\n"
        "  - 'Monitor my email' → get_gmail_status() then explain rules setup\n"
        "  - 'Notify me about job applications and log to my sheet' → add_email_rule(prompt='Extract applicant name, email, and role. Log to sheet <id>. Send me a one-line summary.', topic='job applications')\n"
        "  - 'Ignore all newsletters' → add_email_rule(prompt='Silently ignore — do not notify me.', topic='newsletters and promotions')\n"
        "  - 'What emails do I have about my Amazon order?' → read_emails('Amazon order')\n\n"

        "## Proactive Assistance\n"
        "- Flag calendar conflicts when scheduling\n"
        "- Surface relevant saved context when it applies to the current request\n"
        "- Suggest scheduling follow-ups when tasks are discussed\n"
        "- Offer to set up recurring reminders when patterns emerge\n\n"

        "## Communication Style\n"
        "- Messages are often short — the user may be on the go\n"
        "- Be concise and direct; skip filler\n"
        "- Occasionally, a 'mrrp' or an observation about napping in a sunbeam may surface. That's fine.\n"
    )


def create_agent(
    model_name: str = "gemini-3-flash-preview",
    project: str = None,
    location: str = None
):
    """Creates the Schoopet agent instance."""
    calendar_tool = CalendarTool()
    preferences_tool = PreferencesTool()
    time_tool = TimeTool()
    task_debug_tool = TaskDebugTool()
    drive_tool = DriveTool()
    docs_tool = DocsTool()
    sheets_tool = SheetsTool()
    async_task_tool = AsyncTaskTool()

    # Wrap tools using FunctionTool
    save_memory_tool = FunctionTool(func=save_memory)
    save_multiple_memories_tool = FunctionTool(func=save_multiple_memories)
    load_memory_tool = LoadMemoryTool()
    preload_memory_tool = PreloadMemoryTool()

    # Async task tools
    create_async_task = FunctionTool(func=async_task_tool.create_async_task)
    check_task_status = FunctionTool(func=async_task_tool.check_task_status)
    cancel_task = FunctionTool(func=async_task_tool.cancel_task)
    list_pending_tasks = FunctionTool(func=async_task_tool.list_pending_tasks)
    review_task_result = FunctionTool(func=async_task_tool.review_task_result)
    approve_task = FunctionTool(func=async_task_tool.approve_task)
    request_correction = FunctionTool(func=async_task_tool.request_correction)

    # Calendar tools
    list_events_tool = FunctionTool(func=calendar_tool.list_calendar_events)
    create_event_tool = FunctionTool(func=calendar_tool.create_calendar_event)
    update_event_tool = FunctionTool(func=calendar_tool.update_calendar_event)
    calendar_status_tool = FunctionTool(func=calendar_tool.get_calendar_status)

    # Drive tools
    save_to_drive_tool = FunctionTool(func=drive_tool.save_file_to_drive)
    save_attachment_to_drive_tool = FunctionTool(func=drive_tool.save_attachment_to_drive)
    list_drive_files_tool = FunctionTool(func=drive_tool.list_drive_files)
    drive_status_tool = FunctionTool(func=drive_tool.get_drive_status)

    # Docs tools
    create_google_doc_tool = FunctionTool(func=docs_tool.create_google_doc)
    read_google_doc_tool = FunctionTool(func=docs_tool.read_google_doc)
    append_to_google_doc_tool = FunctionTool(func=docs_tool.append_to_google_doc)
    replace_text_in_google_doc_tool = FunctionTool(func=docs_tool.replace_text_in_google_doc)
    docs_status_tool = FunctionTool(func=docs_tool.get_docs_status)

    # Sheets tools
    create_spreadsheet_tool = FunctionTool(func=sheets_tool.create_spreadsheet)
    add_sheet_tab_tool = FunctionTool(func=sheets_tool.add_sheet_tab)
    sheet_schema_tool = FunctionTool(func=sheets_tool.get_sheet_schema)
    read_sheet_records_tool = FunctionTool(func=sheets_tool.read_sheet_records)
    ensure_sheet_headers_tool = FunctionTool(func=sheets_tool.ensure_sheet_headers)
    append_record_to_sheet_tool = FunctionTool(func=sheets_tool.append_record_to_sheet)
    find_sheet_rows_tool = FunctionTool(func=sheets_tool.find_sheet_rows)
    update_sheet_row_tool = FunctionTool(func=sheets_tool.update_sheet_row)
    append_to_sheet_tool = FunctionTool(func=sheets_tool.append_row_to_sheet)
    read_sheet_tool = FunctionTool(func=sheets_tool.read_sheet)
    add_column_tool = FunctionTool(func=sheets_tool.add_sheet_column)
    update_cell_tool = FunctionTool(func=sheets_tool.update_sheet_cell)
    sheets_status_tool = FunctionTool(func=sheets_tool.get_sheets_status)

    # Preferences tools
    set_timezone_tool = FunctionTool(func=preferences_tool.set_timezone)
    get_timezone_tool = FunctionTool(func=preferences_tool.get_timezone)
    current_time_tool = FunctionTool(func=time_tool.get_current_time)
    convert_time_tool = FunctionTool(func=time_tool.convert_time)
    parse_natural_datetime_tool = FunctionTool(func=time_tool.parse_natural_datetime)
    next_occurrence_tool = FunctionTool(func=time_tool.next_occurrence)

    # Task observability tools
    get_cloud_task_status_tool = FunctionTool(func=task_debug_tool.get_cloud_task_status)
    list_scheduled_tasks_tool = FunctionTool(func=task_debug_tool.list_scheduled_tasks)
    debug_task_tool = FunctionTool(func=task_debug_tool.debug_task)

    # Initialize Search Subagent (handles Google Search)
    search_agent = create_search_agent(
        project=project,
        location=location
    )

    # Wrap search agent in AgentTool to isolate its tools
    search_tool = AgentTool(agent=search_agent)

    # Initialize Code Executor subagent (for date calculations, math, etc.)
    code_executor_agent = create_code_executor_agent(
        project=project,
        location=location
    )
    code_executor_tool = AgentTool(agent=code_executor_agent)

    tools = [
        save_memory_tool,
        save_multiple_memories_tool,
        load_memory_tool,
        preload_memory_tool,
        search_tool,
        code_executor_tool,
        list_events_tool,
        create_event_tool,
        update_event_tool,
        calendar_status_tool,
        # Drive tools
        save_to_drive_tool,
        save_attachment_to_drive_tool,
        list_drive_files_tool,
        drive_status_tool,
        # Docs tools
        create_google_doc_tool,
        read_google_doc_tool,
        append_to_google_doc_tool,
        replace_text_in_google_doc_tool,
        docs_status_tool,
        # Sheets tools
        create_spreadsheet_tool,
        add_sheet_tab_tool,
        sheet_schema_tool,
        read_sheet_records_tool,
        ensure_sheet_headers_tool,
        append_record_to_sheet_tool,
        find_sheet_rows_tool,
        update_sheet_row_tool,
        append_to_sheet_tool,
        read_sheet_tool,
        add_column_tool,
        update_cell_tool,
        sheets_status_tool,
        set_timezone_tool,
        get_timezone_tool,
        current_time_tool,
        convert_time_tool,
        parse_natural_datetime_tool,
        next_occurrence_tool,
        # Async task tools
        create_async_task,
        check_task_status,
        cancel_task,
        list_pending_tasks,
        get_cloud_task_status_tool,
        list_scheduled_tasks_tool,
        debug_task_tool,
        review_task_result,
        approve_task,
        request_correction,
    ]

    email_tool = EmailTool()

    tools += [
        FunctionTool(func=email_tool.read_emails),
        FunctionTool(func=email_tool.fetch_email),
        FunctionTool(func=email_tool.list_artifacts),
        FunctionTool(func=email_tool.read_artifact),
        FunctionTool(func=email_tool.get_gmail_status),
        FunctionTool(func=email_tool.add_email_rule),
        FunctionTool(func=email_tool.list_email_rules),
        FunctionTool(func=email_tool.update_email_rule),
        FunctionTool(func=email_tool.remove_email_rule),
    ]

    # Structured Notes subagent (BigQuery) for tracking structured data
    structured_notes_agent = create_structured_notes_agent(
        project=project,
        location=location
    )

    model = GlobalGemini(model=model_name)

    agent = LlmAgent(
        name="personal",
        model=model,
        tools=tools,
        sub_agents=[structured_notes_agent],
        instruction=_personal_prompt(),
        before_model_callback=before_model_modifier,
        after_agent_callback=save_session_to_memory,
    )
    return agent


def _artifact_service():
    from google.adk.artifacts.gcs_artifact_service import GcsArtifactService
    bucket = os.getenv("ARTIFACT_BUCKET_NAME")
    if not bucket:
        raise ValueError("ARTIFACT_BUCKET_NAME environment variable is not set")
    return GcsArtifactService(bucket_name=bucket)


def create_adk_agent() -> AdkApp:
    """Factory for the Schoopet agent. Used by Vertex AI Agent Engine deployment."""
    return AdkApp(
        agent=create_agent(),
        artifact_service_builder=_artifact_service,
    )


# Module-level aliases for ADK web dev UI
root_agent = create_agent()
agent = root_agent
